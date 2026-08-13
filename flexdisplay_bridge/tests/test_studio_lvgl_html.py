from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


STUDIO_HTML = (
    Path(__file__).parents[1]
    / "flexdisplay_bridge"
    / "static"
    / "dashboard-studio.html"
)


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        self.ids.extend(value for key, value in attrs if key == "id" and value)


def _studio() -> str:
    return STUDIO_HTML.read_text(encoding="utf-8")


def test_studio_exposes_round_jc3636_preview_model() -> None:
    studio = _studio()

    assert 'data-model="JC3636"' in studio
    assert (
        'JC3636: {id:"JC3636", display_name:"JC3636W518EN", '
        'width:360, height:360'
    ) in studio
    assert 'classList.toggle("round", spec.shape === "round")' in studio
    assert 'style.setProperty("--ratio", `${spec.width}/${spec.height}`)' in studio
    assert '"Live LVGL v1 preview · value, control, and gauge cards"' in studio
    assert "width: spec.width" in studio
    assert "height: spec.height" in studio
    assert studio.count(".device-frame.round {") == 1


def test_studio_serializes_only_bounded_lvgl_screen_controls() -> None:
    studio = _studio()

    assert 'id="colorTheme"' in studio
    assert 'class="tile-color-role"' in studio
    assert 'class="tile-control-style"' in studio
    assert 'class="tile-action-type"' in studio
    assert 'color_role: "auto"' in studio
    assert 'control_style: "auto"' in studio
    assert 'tap_action: {type: "none"}' in studio
    assert 'return {type: "navigation", command:' in studio
    assert 'type: "home_assistant"' in studio
    assert "function syncVisibleTileActions()" in studio
    assert "if (!syncVisibleTileActions()) return false;" in studio
    assert 'const showLvgl = isLvglModel();' in studio
    assert 'tile.control_style === "read_only"' in studio
    assert 'tile.tap_action = {type: "none"};' in studio
    assert 'state.profile.pages.length >= 12' in studio
    assert 'currentPage().entities.length >= 4' in studio


def test_studio_includes_receiver_supported_round_status_and_controls_starter() -> None:
    studio = _studio()

    assert 'value="round_controls"' in studio
    assert 'value="jc3636"' in studio
    assert "function roundStatusPage()" in studio
    assert "function roundControlsPage()" in studio
    assert "function roundStarterProfile(name)" in studio
    assert 'color_theme: "midnight"' in studio
    assert 'service: "homeassistant.toggle"' in studio
    starter = studio.split("function roundStatusPage()", 1)[1].split(
        "function applyPageTemplate()", 1
    )[0]
    assert 'style: "gauge"' in starter
    assert 'style: "progress"' in starter
    assert 'style: "name_card"' not in starter
    assert 'style: "qr"' not in starter
    assert 'style: "image"' not in starter
    assert 'style: "history"' not in starter


def test_studio_limits_lvgl_v1_to_receiver_supported_visuals() -> None:
    studio = _studio()

    assert 'lvgl_styles: ["gauge", "progress", "value"]' in studio
    assert 'const tileStyles = showLvgl ? lvglStyles : DASHBOARD_TILE_STYLES;' in studio
    assert 'option.hidden = showLvgl;' in studio
    assert '$("#addQrTile").hidden = showLvgl;' in studio
    assert '<div class="field-grid tile-sizing" ${showLvgl ? "hidden" : ""}>' in studio
    assert 'lvgl_layouts: ["auto", "single", "rows", "columns", "grid"]' in studio
    assert "!lvglLayouts.has(option.value)" in studio
    assert '"energy", "house_pulse", "name_card", "qr_code"' in studio
    assert 'class="tile-icon-field" ${isImage || showLvgl ? "hidden" : ""}' in studio
    assert "does not consume dashboard icons" in studio
    assert "Not supported by LVGL receiver v1:" in studio
    assert "if (isLvglModel() && LVGL_UNSUPPORTED_PAGE_TEMPLATES.has(template))" in studio
    assert "!supportedLayouts.includes(currentPage().layout || \"auto\")" in studio

    # The device-neutral/e-paper editor keeps its richer, renderer-backed set.
    assert (
        'const DASHBOARD_TILE_STYLES = ["value", "gauge", "progress", '
        '"history", "qr", "name_card", "image"]'
    ) in studio
    assert '<option value="house_pulse">House Pulse</option>' in studio
    assert '${options(["auto","home","temperature","humidity","battery","power","solar","wifi","storage","clock","weather","rain","light","lock","alert"], tile.icon || "auto", titleCase)}' in studio
    assert "full colour for LVGL" not in studio
    assert "keeps colour for LVGL" not in studio


def test_studio_exposes_safe_display_hardware_profile_builder() -> None:
    studio = _studio()

    assert 'id="openDisplayProfiles"' in studio
    assert 'id="displayProfileDialog"' in studio
    assert 'id="displayProfileList"' in studio
    assert "Built-in profiles are immutable" in studio
    assert 'api("display-profiles")' in studio
    assert 'method: "PUT"' in studio
    assert 'method: "DELETE"' in studio
    assert "profile.builtin" in studio

    for identifier in (
        "displayProfileId",
        "displayProfileName",
        "displayProfileModel",
        "displayWidth",
        "displayHeight",
        "displayShape",
        "displayPixelFormat",
        "displayTouch",
        "displayTouchController",
        "displayController",
        "displayMcu",
        "displayFlashMib",
        "displayPsramMib",
        "displayAliases",
    ):
        assert f'id="{identifier}"' in studio

    assert '<option value="RGB565">' in studio
    assert '<option value="RGB888">' in studio
    assert 'technology: "color"' in studio
    assert "lvgl: true" in studio
    assert 'shape === "round" && width !== height' in studio
    assert "touch && !touchController" in studio
    assert "aliases.length > 12" in studio
    assert "const BUILTIN_MODEL_ALIASES = new Map([" in studio
    assert "const builtinModelId = (value) => BUILTIN_MODEL_ALIASES.get(" in studio
    assert "reserved by an existing receiver family" in studio
    assert "state.models = normalizeModels(state.displayProfiles.profiles)" in studio
    assert 'return match?.id || supplied || "X4";' in studio

    builder = studio.split('<dialog id="displayProfileDialog"', 1)[1].split(
        '<dialog id="contentChannelDialog"', 1
    )[0]
    assert 'id="displayPins"' not in builder
    assert 'id="displayCode"' not in builder
    assert 'id="displayX"' not in builder
    assert 'id="displayY"' not in builder


def test_studio_builtin_model_matching_is_exact_and_independent_of_custom_profiles() -> None:
    studio = _studio()
    alias_table = studio.split("const BUILTIN_MODEL_ALIASES = new Map([", 1)[1].split(
        "]);", 1
    )[0]
    aliases = dict(re.findall(r'\["([A-Z0-9]+)", "([A-Z0-9]+)"\]', alias_table))

    def builtin_model_id(value: str) -> str:
        normalized = re.sub(r"[^A-Z0-9]", "", value.strip().upper())
        return aliases.get(normalized, "")

    assert builtin_model_id("JC3636W518EN") == "JC3636"
    assert builtin_model_id("Amazon Echo Show 5") == "CHECKERS"
    assert builtin_model_id("JC3636 control room") == ""
    assert builtin_model_id("not-jc3636-panel") == ""

    canonical = studio.split("function canonicalModelId(value) {", 1)[1].split(
        "function normalizeModels(rawModels)", 1
    )[0]
    assert ".includes(" not in canonical
    assert ".endsWith(" not in canonical

    reservation_check = studio.split(
        "if ([profileId, displayName, hardwareModel, ...aliases].some(", 1
    )[1].split(")) {", 1)[0]
    assert "Boolean(builtinModelId(value))" in reservation_check
    assert "canonicalModelId(value)" not in reservation_check


def test_lvgl_fields_fail_closed_for_non_lvgl_receivers() -> None:
    studio = _studio()

    assert 'ROOK: {id:"ROOK"' in studio
    assert 'CHECKERS: {id:"CHECKERS"' in studio
    assert 'touch:true, lvgl:false' in studio
    assert 'panel.hidden = !isLvglModel();' in studio
    assert 'const showLvgl = isLvglModel();' in studio
    assert 'if (!isLvglModel()) return true;' in studio


def test_studio_html_ids_remain_unique() -> None:
    parser = _IdCollector()
    parser.feed(_studio())

    duplicates = [
        identifier for identifier, count in Counter(parser.ids).items() if count > 1
    ]
    assert duplicates == []
