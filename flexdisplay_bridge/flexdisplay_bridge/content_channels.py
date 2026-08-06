from __future__ import annotations

import html
import json
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
ITEM_TYPES = {"dashboard", "message", "quote", "rss"}
NEWS_LAYOUTS = {"headline", "digest", "summary"}
PRIORITIES = {"normal", "important", "critical"}
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_CHANNEL_ITEMS = 24

DEFAULT_QUOTES: tuple[tuple[str, str], ...] = (
    ("The secret of getting ahead is getting started.", "Mark Twain"),
    ("Great things are done by a series of small things brought together.", "Vincent van Gogh"),
    ("It always seems impossible until it is done.", "Nelson Mandela"),
    ("Simplicity is the ultimate sophistication.", "Leonardo da Vinci"),
    ("Well done is better than well said.", "Benjamin Franklin"),
    ("The future depends on what you do today.", "Mahatma Gandhi"),
    ("Make each day your masterpiece.", "John Wooden"),
    ("Quality means doing it right when no one is looking.", "Henry Ford"),
)

FEED_PRESETS: dict[str, dict[str, str]] = {
    "sbs_australia": {
        "name": "SBS Australia",
        "url": "https://www.sbs.com.au/news/topic/australia/feed",
        "attribution": "SBS News",
    },
    "sbs_top_stories": {
        "name": "SBS Top Stories",
        "url": "https://www.sbs.com.au/news/feed",
        "attribution": "SBS News",
    },
    "bom_victoria_warnings": {
        "name": "BOM Victoria Warnings",
        "url": "https://www.bom.gov.au/fwo/IDZ00059.warnings_vic.xml",
        "attribution": "Bureau of Meteorology",
    },
    "rba_media": {
        "name": "RBA Media Releases",
        "url": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
        "attribution": "Reserve Bank of Australia",
    },
}


class ContentChannelValidationError(ValueError):
    """Raised when mixed-content configuration is unsafe or malformed."""


@dataclass(frozen=True)
class ContentPage:
    kind: str
    title: str
    body: str = ""
    footer: str = ""
    source: str = ""
    link: str = ""
    priority: str = "normal"
    dashboard_index: int = -1


def _bounded(value: Any, fallback: str, maximum: int, *, multiline: bool = False) -> str:
    selected = str(value if value is not None else fallback)
    selected = selected.replace("\r\n", "\n").replace("\r", "\n")
    if not multiline:
        selected = selected.replace("\n", " ")
    return selected.strip()[:maximum]


def _integer(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def _url(value: Any) -> str:
    selected = _bounded(value, "", 2048)
    if not selected:
        return ""
    parsed = urlparse(selected)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContentChannelValidationError("Feed URLs must use http:// or https://")
    if parsed.username or parsed.password:
        raise ContentChannelValidationError("Feed URLs must not contain credentials")
    return selected


def _clean_markup(value: str, maximum: int = 900) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return _bounded(html.unescape(re.sub(r"\s+", " ", without_tags)), "", maximum)


def _item(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContentChannelValidationError(f"Item {index + 1} must be an object")
    kind = str(raw.get("type") or "message").lower()
    if kind not in ITEM_TYPES:
        raise ContentChannelValidationError(f"Item {index + 1} has an unsupported type")
    item: dict[str, Any] = {
        "id": _bounded(raw.get("id"), f"item-{index + 1}", 48),
        "type": kind,
        "enabled": bool(raw.get("enabled", True)),
        "title": _bounded(raw.get("title"), kind.title(), 80),
    }
    if kind == "dashboard":
        # Dashboard items intentionally use the profile already assigned to each
        # device. This keeps one device assignment authoritative and avoids a
        # mixed channel silently overriding fleet policy.
        pass
    elif kind == "message":
        item.update(
            {
                "body": _bounded(raw.get("body"), "Your message", 1400, multiline=True),
                "footer": _bounded(raw.get("footer"), "", 160),
                "priority": str(raw.get("priority") or "normal").lower(),
                "link": _url(raw.get("link")),
            }
        )
        if item["priority"] not in PRIORITIES:
            raise ContentChannelValidationError("Message priority is not supported")
    elif kind == "quote":
        raw_quotes = raw.get("quotes") or []
        quotes: list[dict[str, str]] = []
        if not isinstance(raw_quotes, list):
            raise ContentChannelValidationError("Quote collections must be a list")
        for quote in raw_quotes[:100]:
            if isinstance(quote, str):
                text, author = quote, ""
            elif isinstance(quote, dict):
                text, author = quote.get("text"), quote.get("author")
            else:
                continue
            text = _bounded(text, "", 600, multiline=True)
            if text:
                quotes.append({"text": text, "author": _bounded(author, "", 100)})
        item.update(
            {
                "quotes": quotes,
                "selection": "random" if raw.get("selection") == "random" else "daily",
                "footer": _bounded(raw.get("footer"), "Quote of the day", 120),
            }
        )
    else:
        preset = FEED_PRESETS.get(str(raw.get("preset") or ""), {})
        layout = str(raw.get("layout") or "digest").lower()
        if layout not in NEWS_LAYOUTS:
            raise ContentChannelValidationError("News layout is not supported")
        item.update(
            {
                "preset": _bounded(raw.get("preset"), "", 48),
                "url": _url(raw.get("url") or preset.get("url")),
                "source": _bounded(raw.get("source"), preset.get("attribution", "News"), 100),
                "layout": layout,
                "limit": _integer(raw.get("limit"), 4, 1, 8),
                "cache_seconds": _integer(raw.get("cache_seconds"), 900, 60, 86400),
            }
        )
        if not item["url"]:
            raise ContentChannelValidationError("News items need a feed URL or preset")
    return item


def parse_channel(channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not CHANNEL_PATTERN.fullmatch(channel_id):
        raise ContentChannelValidationError(
            "Channel IDs may contain only letters, numbers, underscores, and hyphens"
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_CHANNEL_ITEMS:
        raise ContentChannelValidationError(
            f"A channel must contain between 1 and {MAX_CHANNEL_ITEMS} items"
        )
    items = [_item(value, index) for index, value in enumerate(raw_items)]
    used_ids: set[str] = set()
    for index, item in enumerate(items):
        base = str(item.get("id") or f"item-{index + 1}")
        candidate = base
        suffix = 2
        while candidate in used_ids:
            marker = f"-{suffix}"
            candidate = f"{base[: 48 - len(marker)]}{marker}"
            suffix += 1
        item["id"] = candidate
        used_ids.add(candidate)
    return {
        "id": channel_id,
        "name": _bounded(payload.get("name"), channel_id, 64),
        "items": items,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


class FeedReader:
    """Bounded RSS/Atom fetcher with a small in-memory stale-on-error cache."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[dict[str, str]], str]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _text(node: ET.Element | None, names: tuple[str, ...]) -> str:
        if node is None:
            return ""
        for child in list(node):
            local = child.tag.rsplit("}", 1)[-1].lower()
            if local in names and child.text:
                return child.text
        return ""

    @classmethod
    def _parse(cls, content: bytes) -> tuple[list[dict[str, str]], str]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as err:
            raise ContentChannelValidationError("The news source did not return valid RSS or Atom") from err
        channel = next(
            (node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "channel"),
            root,
        )
        feed_title = _clean_markup(cls._text(channel, ("title",)), 100)
        entries = [
            node
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
        result: list[dict[str, str]] = []
        for entry in entries[:24]:
            title = _clean_markup(cls._text(entry, ("title",)), 240)
            summary = _clean_markup(
                cls._text(entry, ("description", "summary", "content")), 900
            )
            link = cls._text(entry, ("link",))
            if not link:
                link_node = next(
                    (
                        child
                        for child in list(entry)
                        if child.tag.rsplit("}", 1)[-1].lower() == "link"
                    ),
                    None,
                )
                link = str(link_node.attrib.get("href") or "") if link_node is not None else ""
            if title:
                result.append(
                    {
                        "title": title,
                        "summary": summary,
                        "link": _bounded(link, "", 2048),
                    }
                )
        if not result:
            raise ContentChannelValidationError("The news feed contains no readable headlines")
        return result, feed_title

    def fetch(self, url: str, cache_seconds: int) -> tuple[list[dict[str, str]], str, bool]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(url)
            if cached and now - cached[0] < cache_seconds:
                return list(cached[1]), cached[2], True
        try:
            response = requests.get(
                url,
                timeout=(3.05, 8),
                headers={"User-Agent": "FlexDisplay-Bridge/0.34 (+RSS reader)"},
                stream=True,
            )
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(32 * 1024):
                size += len(chunk)
                if size > MAX_FEED_BYTES:
                    raise ContentChannelValidationError("The news feed exceeds the 2 MB limit")
                chunks.append(chunk)
            entries, title = self._parse(b"".join(chunks))
        except (requests.RequestException, ContentChannelValidationError) as err:
            with self._lock:
                cached = self._cache.get(url)
                if cached:
                    return list(cached[1]), cached[2], True
            if isinstance(err, ContentChannelValidationError):
                raise
            raise ContentChannelValidationError(f"News feed request failed: {err}") from err
        with self._lock:
            self._cache[url] = (now, entries, title)
        return list(entries), title, False


class ContentChannelStore:
    """Persist mixed-content definitions and per-device assignments."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()
        self.feeds = FeedReader()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    assignments = dict(payload.get("assignments") or {})
                    return {
                        "version": 1,
                        "channels": dict(payload.get("channels") or {}),
                        "assignments": {
                            device_id: channel_id
                            for device_id, channel_id in assignments.items()
                            if device_id and device_id.upper() != "UNKNOWN"
                        },
                    }
            except (OSError, ValueError):
                pass
        return {"version": 1, "channels": {}, "assignments": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                **json.loads(json.dumps(self._data)),
                "capabilities": {
                    "item_types": sorted(ITEM_TYPES),
                    "news_layouts": sorted(NEWS_LAYOUTS),
                    "priorities": sorted(PRIORITIES),
                    "feed_presets": FEED_PRESETS,
                    "default_quotes": len(DEFAULT_QUOTES),
                },
            }

    def put(self, channel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        parsed = parse_channel(channel_id, payload)
        with self._lock:
            self._data["channels"][channel_id] = parsed
            self._save()
            return json.loads(json.dumps(parsed))

    def delete(self, channel_id: str) -> None:
        with self._lock:
            if channel_id not in self._data["channels"]:
                raise KeyError(channel_id)
            del self._data["channels"][channel_id]
            self._data["assignments"] = {
                device_id: selected
                for device_id, selected in self._data["assignments"].items()
                if selected != channel_id
            }
            self._save()

    def assign(self, device_id: str, channel_id: str) -> None:
        with self._lock:
            if channel_id and channel_id not in self._data["channels"]:
                raise ContentChannelValidationError("Unknown content channel")
            if channel_id:
                self._data["assignments"][device_id] = channel_id
            else:
                self._data["assignments"].pop(device_id, None)
            self._save()

    def assigned(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            channel_id = str(self._data["assignments"].get(device_id) or "")
            channel = self._data["channels"].get(channel_id)
            return json.loads(json.dumps(channel)) if channel else None

    @staticmethod
    def _quote_page(item: dict[str, Any], device_id: str) -> ContentPage:
        configured = item.get("quotes") or [
            {"text": text, "author": author} for text, author in DEFAULT_QUOTES
        ]
        if item.get("selection") == "random":
            selected = random.SystemRandom().choice(configured)
        else:
            day = datetime.now().astimezone().date().isoformat()
            seed = f"{day}:{item.get('id')}:{device_id}"
            selected = configured[random.Random(seed).randrange(len(configured))]
        return ContentPage(
            kind="quote",
            title=str(item.get("title") or "QUOTE OF THE DAY"),
            body=str(selected.get("text") or ""),
            footer=str(selected.get("author") or item.get("footer") or ""),
            source="Quote collection",
        )

    def pages_for_channel(
        self,
        channel: dict[str, Any],
        device_id: str,
        dashboard_titles: list[str],
    ) -> list[ContentPage]:
        pages: list[ContentPage] = []
        for item in channel.get("items") or []:
            if not item.get("enabled", True):
                continue
            kind = item.get("type")
            if kind == "dashboard":
                pages.extend(
                    ContentPage(
                        kind="dashboard",
                        title=title,
                        dashboard_index=index,
                        source="Assigned Home Assistant profile",
                    )
                    for index, title in enumerate(dashboard_titles)
                )
            elif kind == "message":
                pages.append(
                    ContentPage(
                        kind="message",
                        title=str(item.get("title") or "MESSAGE"),
                        body=str(item.get("body") or ""),
                        footer=str(item.get("footer") or ""),
                        link=str(item.get("link") or ""),
                        priority=str(item.get("priority") or "normal"),
                        source="Message Center",
                    )
                )
            elif kind == "quote":
                pages.append(self._quote_page(item, device_id))
            elif kind == "rss":
                try:
                    entries, feed_title, cached = self.feeds.fetch(
                        str(item["url"]), int(item.get("cache_seconds") or 900)
                    )
                except ContentChannelValidationError as err:
                    pages.append(
                        ContentPage(
                            kind="news",
                            title=str(item.get("title") or "NEWS"),
                            body="News is temporarily unavailable.",
                            footer=str(err)[:160],
                            source=str(item.get("source") or "News"),
                            priority="important",
                        )
                    )
                    continue
                limit = int(item.get("limit") or 4)
                source = str(item.get("source") or feed_title or "News")
                layout = str(item.get("layout") or "digest")
                if layout == "digest":
                    pages.append(
                        ContentPage(
                            kind="news",
                            title=str(item.get("title") or feed_title or "NEWS"),
                            body="\n".join(
                                f"{index + 1}. {entry['title']}"
                                for index, entry in enumerate(entries[:limit])
                            ),
                            footer=f"{source}{' · cached' if cached else ''}",
                            source=source,
                            link=entries[0].get("link", "") if entries else "",
                        )
                    )
                else:
                    selected_entries = entries[:1] if layout == "headline" else entries[:limit]
                    for entry in selected_entries:
                        pages.append(
                            ContentPage(
                                kind="news",
                                title=str(item.get("title") or "NEWS"),
                                body=(
                                    entry["title"]
                                    if layout == "headline"
                                    else f"{entry['title']}\n\n{entry.get('summary', '')}"
                                ),
                                footer=source,
                                source=source,
                                link=entry.get("link", ""),
                            )
                        )
        return pages

    def pages(self, device_id: str, dashboard_titles: list[str]) -> list[ContentPage]:
        channel = self.assigned(device_id)
        return self.pages_for_channel(channel, device_id, dashboard_titles) if channel else []
