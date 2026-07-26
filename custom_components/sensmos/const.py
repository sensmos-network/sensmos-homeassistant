"""Sensmos — stałe."""

DOMAIN = "sensmos"

CONF_HOST = "host"
CONF_PIN = "pin"

# Dwa tryby integracji:
#   node — fizyczny node Sensmos (host + PIN), dwukierunkowo (jak dotąd)
#   data — bez sprzętu: wybrane encje HA lecą wprost na żywą mapę (programowy node)
CONF_MODE = "mode"
MODE_NODE = "node"
MODE_DATA = "data"

# tryb data
CONF_KEY = "key"        # passkey ≥32 znaki → device_id = sha256("sensmos-soft:"+key)
CONF_LABEL = "label"
CONF_LAT = "lat"
CONF_LON = "lon"
BE_INGEST_URL = "https://api.sensmos.com/v1/ingest"
BE_GET_URL = "https://api.sensmos.com/v1/ingest/get/"  # + device_id
BE_AVAILABLE_URL = "https://api.sensmos.com/v1/data/available/"  # + device_id (katalog, publiczne)
DATA_MIN_KEY_LEN = 32
DATA_DEFAULT_INTERVAL = 60
DATA_MIN_INTERVAL = 20

# tryb data — podgląd (GET) opublikowanych encji innych nodów (realtime zostaje na nodzie)
GET_DEFAULT_INTERVAL = 600   # 10 min — to tylko podgląd
GET_MIN_INTERVAL = 120       # min 2 min

# entry.options
OPT_FEEDS = "feeds"              # [{node_entity, ha_entity, unit}]   (tryb node)
OPT_WEBHOOK = "webhook_enabled"  # bool                              (tryb node)
OPT_MAPPINGS = "mappings"        # [{ha_entity, entity}]             (tryb data)
OPT_PUSH_INTERVAL = "push_interval"  # sekundy                       (tryb data)
OPT_GETS = "gets"               # [{device_id, prefix}]             (tryb data — podgląd)
OPT_GET_INTERVAL = "get_interval"  # sekundy                        (tryb data)

# coordinator
SCAN_INTERVAL_S = 30        # /data/status
SLOW_EVERY_N_CYCLES = 10    # /config, /data/native co N cykli
# Encje zostają "available" tyle po OSTATNIM udanym pollu. Jeden nieudany poll /data/status
# (node zajęty BLE/checknet, WiFi-blip, timeout 8 s) NIE może zdejmować wszystkich sensorów —
# HA wciąż trzyma świeży snapshot, a mapa (BE/WS) ma dane. Realna awaria (>grace) → unavailable.
AVAIL_GRACE_S = SCAN_INTERVAL_S * 3   # 90 s ≈ 2 pominięte polle

# feeder
FEED_MIN_INTERVAL_S = 15    # min odstęp push per mapowanie
FEED_KEEPALIVE_S = 300      # odśwież wartość na nodzie nawet bez zmiany

# pool — prefiksy wykluczone z sensorów (udostępniamy tylko dane subskrypcji)
POOL_EXCLUDED_PREFIXES = ("get.", "msg.")

EVENT_NODE = "sensmos_event"
EVENT_MESSAGE = "sensmos_message"

PLATFORMS = ["sensor", "binary_sensor"]
DATA_PLATFORMS = ["sensor"]   # tryb data: tylko sensory (podgląd GET)
