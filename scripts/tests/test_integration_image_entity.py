from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "custom_components" / "flexdisplay" / "image.py"


class IntegrationImageEntityTests(unittest.TestCase):
    def test_current_screen_initializes_home_assistant_image_entity(self) -> None:
        saved_modules = dict(sys.modules)
        try:
            homeassistant = ModuleType("homeassistant")
            components = ModuleType("homeassistant.components")
            image_module = ModuleType("homeassistant.components.image")
            config_entries = ModuleType("homeassistant.config_entries")
            core = ModuleType("homeassistant.core")
            helpers = ModuleType("homeassistant.helpers")
            entity_platform = ModuleType("homeassistant.helpers.entity_platform")
            util = ModuleType("homeassistant.util")
            dt = ModuleType("homeassistant.util.dt")

            class ImageEntity:
                def __init__(self, hass, verify_ssl: bool = False) -> None:
                    self.image_hass = hass
                    self.verify_ssl = verify_ssl
                    self.access_tokens = ["initialized"]

            class ConfigEntry:
                pass

            class HomeAssistant:
                pass

            image_module.ImageEntity = ImageEntity
            config_entries.ConfigEntry = ConfigEntry
            core.HomeAssistant = HomeAssistant
            entity_platform.AddEntitiesCallback = object
            dt.parse_datetime = lambda value: value

            package = ModuleType("custom_components.flexdisplay")
            package.__path__ = [str(MODULE_PATH.parent)]
            api = ModuleType("custom_components.flexdisplay.api")
            entity = ModuleType("custom_components.flexdisplay.entity")

            class FlexDisplayApiError(Exception):
                pass

            class FlexDisplayEntity:
                def __init__(self, coordinator, device_id: str) -> None:
                    self.coordinator = coordinator
                    self.device_id = device_id

            api.FlexDisplayApiError = FlexDisplayApiError
            entity.FlexDisplayEntity = FlexDisplayEntity
            entity.setup_dynamic_entities = lambda *args, **kwargs: None

            sys.modules.update(
                {
                    "homeassistant": homeassistant,
                    "homeassistant.components": components,
                    "homeassistant.components.image": image_module,
                    "homeassistant.config_entries": config_entries,
                    "homeassistant.core": core,
                    "homeassistant.helpers": helpers,
                    "homeassistant.helpers.entity_platform": entity_platform,
                    "homeassistant.util": util,
                    "homeassistant.util.dt": dt,
                    "custom_components.flexdisplay": package,
                    "custom_components.flexdisplay.api": api,
                    "custom_components.flexdisplay.entity": entity,
                }
            )

            spec = importlib.util.spec_from_file_location(
                "custom_components.flexdisplay.image", MODULE_PATH
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            hass = HomeAssistant()
            coordinator = object()
            current_screen = module.FlexDisplayCurrentScreen(
                hass, coordinator, "device-1"
            )

            self.assertIs(current_screen.image_hass, hass)
            self.assertIs(current_screen.coordinator, coordinator)
            self.assertEqual(current_screen.device_id, "device-1")
            self.assertEqual(current_screen.access_tokens, ["initialized"])
            self.assertEqual(current_screen._attr_unique_id, "device-1_current_screen")
        finally:
            sys.modules.clear()
            sys.modules.update(saved_modules)


if __name__ == "__main__":
    unittest.main()
