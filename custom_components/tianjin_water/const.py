"""Constants for the Tianjin water integration."""

DOMAIN = "tianjin_water"
BASE_URL = "https://www.twwc.net.cn/water-supply-front"
CONF_CUSTOM_ID = "custom_id"
CONF_SESSION = "session"
CONF_USER_AGENT = "user_agent"
CONF_STOPPED = "stopped"
DEFAULT_USER_AGENT = "Mozilla/5.0 MicroMessenger/8.0"
POLL_SECONDS = 600
HISTORY_SECONDS = 21600
REQUEST_TIMEOUT = 20
MIN_REQUEST_INTERVAL = 60
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TRANSIENT_FAILURES = 3
