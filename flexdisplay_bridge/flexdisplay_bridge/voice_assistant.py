from __future__ import annotations

import json
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from websockets.sync.client import connect

from .config import HomeAssistantConfig

SAMPLE_RATE = 16_000
MIN_AUDIO_BYTES = SAMPLE_RATE * 2 * 4 // 10
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * 15
MAX_TTS_BYTES = 8 * 1024 * 1024
CONVERSATION_TTL_SECONDS = 5 * 60
RESPONSE_V1_HEADER = struct.Struct("<4sIII")
RESPONSE_V2_HEADER = struct.Struct("<4sIIII")
RESPONSE_V1_MAGIC = b"FVA1"
RESPONSE_V2_MAGIC = b"FVA2"


class VoiceAssistantError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceAssistantResult:
    transcript: str
    response_text: str
    audio_pcm: bytes
    sample_rate: int = SAMPLE_RATE
    conversation_id: str = ""
    continue_conversation: bool = False


@dataclass(frozen=True)
class _ConversationSession:
    conversation_id: str
    updated_at: float


def display_text(value: str, limit: int = 220) -> str:
    replacements = {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
    }
    normalized = "".join(replacements.get(char, char) for char in value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_text.split())[:limit]


def encode_voice_response(result: VoiceAssistantResult) -> bytes:
    transcript = display_text(result.transcript).encode("utf-8")
    response = display_text(result.response_text or result.transcript).encode("utf-8")
    audio = bytes(result.audio_pcm)
    return RESPONSE_V2_HEADER.pack(
        RESPONSE_V2_MAGIC,
        int(result.sample_rate),
        len(transcript),
        len(response),
        len(audio),
    ) + transcript + response + audio


def decode_voice_response(payload: bytes) -> VoiceAssistantResult:
    if len(payload) < RESPONSE_V1_HEADER.size:
        raise VoiceAssistantError("voice response is truncated")
    magic = payload[:4]
    if magic == RESPONSE_V2_MAGIC:
        if len(payload) < RESPONSE_V2_HEADER.size:
            raise VoiceAssistantError("voice response is truncated")
        _, sample_rate, transcript_length, response_length, audio_length = (
            RESPONSE_V2_HEADER.unpack_from(payload)
        )
        expected = (
            RESPONSE_V2_HEADER.size
            + transcript_length
            + response_length
            + audio_length
        )
        if expected != len(payload):
            raise VoiceAssistantError("voice response length does not match its header")
        transcript_start = RESPONSE_V2_HEADER.size
        transcript_end = transcript_start + transcript_length
        response_end = transcript_end + response_length
        return VoiceAssistantResult(
            transcript=payload[transcript_start:transcript_end].decode("utf-8"),
            response_text=payload[transcript_end:response_end].decode("utf-8"),
            audio_pcm=payload[response_end:],
            sample_rate=sample_rate,
        )
    if magic == RESPONSE_V1_MAGIC:
        _, sample_rate, text_length, audio_length = RESPONSE_V1_HEADER.unpack_from(payload)
        expected = RESPONSE_V1_HEADER.size + text_length + audio_length
        if expected != len(payload):
            raise VoiceAssistantError("voice response length does not match its header")
        text_start = RESPONSE_V1_HEADER.size
        text_end = text_start + text_length
        return VoiceAssistantResult(
            transcript="",
            response_text=payload[text_start:text_end].decode("utf-8"),
            audio_pcm=payload[text_end:],
            sample_rate=sample_rate,
        )
    raise VoiceAssistantError("voice response has an invalid signature")


class HomeAssistantVoiceClient:
    def __init__(
        self,
        config: HomeAssistantConfig,
        *,
        websocket_connect: Callable[..., Any] = connect,
    ):
        self.config = config
        self.websocket_connect = websocket_connect
        self._conversation_sessions: dict[str, _ConversationSession] = {}
        self._conversation_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.config.token)

    def reset_conversation(self, device_id: str) -> None:
        if not device_id:
            return
        with self._conversation_lock:
            self._conversation_sessions.pop(device_id, None)

    def _conversation_for_device(self, device_id: str) -> str:
        if not device_id:
            return ""
        now = time.monotonic()
        with self._conversation_lock:
            session = self._conversation_sessions.get(device_id)
            if session is None:
                return ""
            if now - session.updated_at > CONVERSATION_TTL_SECONDS:
                self._conversation_sessions.pop(device_id, None)
                return ""
            return session.conversation_id

    def _remember_conversation(self, device_id: str, conversation_id: str) -> None:
        if not device_id or not conversation_id:
            return
        with self._conversation_lock:
            self._conversation_sessions[device_id] = _ConversationSession(
                conversation_id=conversation_id,
                updated_at=time.monotonic(),
            )

    def run(self, audio_pcm: bytes, device_id: str = "") -> VoiceAssistantResult:
        if not self.config.token:
            raise VoiceAssistantError("Home Assistant token is not configured")
        if len(audio_pcm) < MIN_AUDIO_BYTES:
            raise VoiceAssistantError("Hold the button a little longer")
        if len(audio_pcm) > MAX_AUDIO_BYTES:
            raise VoiceAssistantError("Voice command is longer than 15 seconds")
        if len(audio_pcm) % 2:
            raise VoiceAssistantError("PCM audio must contain complete 16-bit samples")

        transcript = ""
        response_text = ""
        tts_url = ""
        tts_mime = ""
        handler_id: int | None = None
        command_id = 1
        conversation_id = self._conversation_for_device(device_id)
        returned_conversation_id = ""
        continue_conversation = False

        try:
            with self.websocket_connect(
                self._websocket_url(),
                open_timeout=self.config.timeout_seconds,
                close_timeout=2,
                max_size=2 * 1024 * 1024,
            ) as socket:
                auth_required = self._receive_json(socket)
                if auth_required.get("type") != "auth_required":
                    raise VoiceAssistantError("Home Assistant did not request authentication")
                socket.send(json.dumps({"type": "auth", "access_token": self.config.token}))
                authenticated = self._receive_json(socket)
                if authenticated.get("type") != "auth_ok":
                    raise VoiceAssistantError("Home Assistant rejected Bridge authentication")

                command: dict[str, Any] = {
                    "id": command_id,
                    "type": "assist_pipeline/run",
                    "start_stage": "stt",
                    "end_stage": "tts",
                    "input": {"sample_rate": SAMPLE_RATE},
                }
                if conversation_id:
                    command["conversation_id"] = conversation_id
                socket.send(json.dumps(command))

                while handler_id is None:
                    message = self._receive_json(socket)
                    self._raise_event_error(message)
                    handler_id = self._find_int(message, "stt_binary_handler_id")

                prefix = bytes((handler_id,))
                for offset in range(0, len(audio_pcm), 1024):
                    socket.send(prefix + audio_pcm[offset : offset + 1024])
                socket.send(prefix)

                while True:
                    message = self._receive_json(socket)
                    self._raise_event_error(message)
                    event_type, data = self._event(message)
                    if event_type == "stt-end":
                        transcript = str((data.get("stt_output") or {}).get("text") or "")
                    elif event_type == "intent-end":
                        response_text = self._intent_speech(data)
                        intent_output = data.get("intent_output") or {}
                        returned_conversation_id = str(
                            intent_output.get("conversation_id") or ""
                        )
                        continue_conversation = bool(
                            intent_output.get("continue_conversation", False)
                        )
                    elif event_type == "tts-end":
                        output = data.get("tts_output") or data
                        tts_url = str(output.get("url") or "")
                        tts_mime = str(output.get("mime_type") or "")
                    elif event_type == "run-end":
                        break
        except VoiceAssistantError:
            raise
        except Exception as exc:
            raise VoiceAssistantError(f"Home Assistant Assist connection failed: {exc}") from exc

        if not transcript:
            raise VoiceAssistantError("Home Assistant did not recognize speech")
        self._remember_conversation(device_id, returned_conversation_id)
        audio = self._download_and_convert_tts(tts_url, tts_mime) if tts_url else b""
        return VoiceAssistantResult(
            transcript=transcript,
            response_text=response_text or transcript,
            audio_pcm=audio,
            conversation_id=returned_conversation_id,
            continue_conversation=continue_conversation,
        )

    def _websocket_url(self) -> str:
        parsed = urlparse(self.config.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"{parsed.path.rstrip('/')}/api/websocket"
        return parsed._replace(scheme=scheme, path=path, params="", query="", fragment="").geturl()

    def _media_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return url
        base = urlparse(self.config.base_url)
        base_path = base.path.rstrip("/")
        path = parsed.path
        if base_path and path.startswith("/api/"):
            path = f"{base_path}{path}"
        elif not path.startswith("/"):
            path = f"{base_path}/{path}"
        return base._replace(path=path, params="", query=parsed.query, fragment="").geturl()

    def _download_and_convert_tts(self, url: str, mime_type: str) -> bytes:
        media_url = self._media_url(url)
        parsed_media = urlparse(media_url)
        parsed_base = urlparse(self.config.base_url)
        same_origin = (parsed_media.scheme, parsed_media.netloc) == (
            parsed_base.scheme,
            parsed_base.netloc,
        )
        headers = {"Accept": mime_type or "audio/*"}
        if same_origin:
            headers["Authorization"] = f"Bearer {self.config.token}"
        try:
            response = requests.get(
                media_url,
                headers=headers,
                timeout=max(20.0, self.config.timeout_seconds),
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise VoiceAssistantError(f"Could not download Home Assistant speech: {exc}") from exc
        if not response.content or len(response.content) > MAX_TTS_BYTES:
            raise VoiceAssistantError("Home Assistant speech response is empty or too large")
        try:
            converted = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    "pipe:0",
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-ac",
                    "1",
                    "-ar",
                    str(SAMPLE_RATE),
                    "pipe:1",
                ],
                input=response.content,
                capture_output=True,
                check=True,
                timeout=20,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise VoiceAssistantError(f"Could not convert Home Assistant speech: {exc}") from exc
        if len(converted) > MAX_TTS_BYTES:
            raise VoiceAssistantError("Converted speech response is too large")
        return converted

    def _receive_json(self, socket: Any) -> dict[str, Any]:
        message = socket.recv(timeout=max(30.0, self.config.timeout_seconds))
        if not isinstance(message, str):
            raise VoiceAssistantError("Home Assistant returned unexpected binary data")
        payload = json.loads(message)
        if not isinstance(payload, dict):
            raise VoiceAssistantError("Home Assistant returned an invalid event")
        return payload

    @staticmethod
    def _event(message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        event = message.get("event")
        if isinstance(event, dict):
            data = event.get("data")
            return str(event.get("type") or ""), data if isinstance(data, dict) else {}
        return "", {}

    @classmethod
    def _raise_event_error(cls, message: dict[str, Any]) -> None:
        event_type, data = cls._event(message)
        if event_type == "error":
            raise VoiceAssistantError(str(data.get("message") or data.get("code") or "Assist failed"))
        if message.get("type") == "result" and message.get("success") is False:
            error = message.get("error") or {}
            raise VoiceAssistantError(str(error.get("message") or "Assist command failed"))

    @classmethod
    def _find_int(cls, value: Any, key: str) -> int | None:
        if isinstance(value, dict):
            found = value.get(key)
            if isinstance(found, int) and 0 <= found <= 255:
                return found
            for child in value.values():
                nested = cls._find_int(child, key)
                if nested is not None:
                    return nested
        elif isinstance(value, list):
            for child in value:
                nested = cls._find_int(child, key)
                if nested is not None:
                    return nested
        return None

    @staticmethod
    def _intent_speech(data: dict[str, Any]) -> str:
        intent_output = data.get("intent_output") or {}
        response = intent_output.get("response") or {}
        speech = response.get("speech") or {}
        plain = speech.get("plain") or {}
        return str(plain.get("speech") or "")
