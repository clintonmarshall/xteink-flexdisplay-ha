"""Constants for the FlexDisplay integration."""

DOMAIN = "flexdisplay"
CONF_API_KEY = "api_key"
DEFAULT_URL = "http://localhost:8099"
EVENT_TYPE = f"{DOMAIN}_event"
BUTTON_EVENT_TYPES = ("back", "confirm", "left", "right", "up", "down", "power")
PLATFORMS = [
    "sensor",
    "binary_sensor",
    "button",
    "event",
    "select",
    "number",
    "switch",
    "time",
    "text",
    "update",
]
