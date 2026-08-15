"""Constants for the FlexDisplay integration."""

DOMAIN = "flexdisplay"
CONF_API_KEY = "api_key"
DEFAULT_URL = "http://localhost:8099"
EVENT_TYPE = f"{DOMAIN}_event"
NOTIFICATION_EVENT_TYPE = f"{DOMAIN}_notification_response"
MESHTASTIC_EVENT_TYPE = f"{DOMAIN}_meshtastic_message"
SERVICE_CLEAR_MESHTASTIC_UNREAD = "clear_meshtastic_unread"
SERVICE_SEND_MESHTASTIC_MESSAGE = "send_meshtastic_message"
SERVICE_NOTIFY = "notify"
BUTTON_EVENT_TYPES = (
    "back",
    "confirm",
    "left",
    "right",
    "up",
    "down",
    "home",
    "side_previous",
    "side_next",
    "power",
)
NOTIFICATION_RESPONSE_EVENT_TYPES = (
    "action",
    "dismissed",
    "device_timeout",
    "server_expired",
    "bridge_restarted",
    "superseded",
    "cleared",
)
MESHTASTIC_EVENT_TYPES = ("message_received", "message_sent", "message_failed")
PLATFORMS = [
    "sensor",
    "binary_sensor",
    "camera",
    "button",
    "event",
    "image",
    "media_player",
    "notify",
    "select",
    "number",
    "switch",
    "time",
    "text",
    "update",
]
