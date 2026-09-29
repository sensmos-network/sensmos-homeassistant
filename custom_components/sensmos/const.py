"""Sensmos — stałe."""

DOMAIN = "sensmos"

CONF_HOST = "host"
CONF_PIN = "pin"

# Trzy tryby integracji:
#   node  — fizyczny node Sensmos (host + PIN), dwukierunkowo (jak dotąd)
#   data  — bez sprzętu: wybrane encje HA lecą wprost na żywą mapę (programowy node)
#   cloud — konto Sensmos tokenem z apki; urządzenia = sparowane bramy LoRaWAN (§10)
CONF_MODE = "mode"
MODE_NODE = "node"
MODE_DATA = "data"
MODE_CLOUD = "cloud"

# tryb cloud
CONF_BE = "be"
CONF_OWNER = "owner"
CONF_TOKEN = "token"
CONF_SCOPES = "scopes"
BE_URL = "https://api.sensmos.com"
CLOUD_SCOPES = ("lora.rx", "lora.tx")
OPT_GATEWAYS = "gateways"   # {gw device_id: {key: sha256(fraza) hex, open: bool}}
GW_STATS_GRACE_S = 180      # BE przysyła diagnostykę bram co 60 s; starsza = encje niedostępne

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
# PAKIETOWANIE: /data/status to "pakiet" (bieżący snapshot małych buforów noda), nie pełny stan.
# Flota bez OTA (FW <0.75) trzyma ~11 encji telemetrii w pub[16] — na sensory usera zostaje ~5 slotów,
# więc rotują. FW ≥0.75 (mon-split) przeniósł telemetrię do mon[12] i pub[16] jest w całości usera —
# powód rotacji znika po OTA floty, ale bufor dalej jest mały (>16 encji nadal rotuje), więc wartości
# zostają. Częstszy sampling łapie encję w oknie jej życia w buforze, a sticky (_current) akumuluje
# pakiety w komplet. 15 s < typowy czas życia sensora w buforze.
SCAN_INTERVAL_S = 15        # /data/status (było 30 — za rzadko na rotujący bufor)
SLOW_EVERY_N_CYCLES = 20    # /config, /data/native co N cykli (=300 s przy 15 s — jak było)
# Encje zostają "available" tyle po OSTATNIM udanym pollu. Jeden nieudany poll /data/status
# (node zajęty BLE/checknet, WiFi-blip, timeout 8 s) NIE może zdejmować wszystkich sensorów —
# HA wciąż trzyma świeży snapshot, a mapa (BE/WS) ma dane. Realna awaria (>grace) → unavailable.
AVAIL_GRACE_S = 90   # stałe (niezależne od SCAN) ≈ tolerancja ~6 pominiętych polli przy 15 s
# Bufory noda są małe i stałe (pub[16]/own[16]/pool[64]); /data/status zwraca tylko to, co AKTUALNIE
# w buforze. Encja chwilowo wyparta (rotacja/ewikcja przy >slotów encjach, albo prune own.*) znika ze
# snapshotu -> sensor migałby na "unavailable" mimo świeżej wartości. Trzymamy ją "sticky" tyle po
# ostatnim realnym odczycie. (Prawdziwy fix przepełnienia = większe bufory w FW; to jest po stronie HA.)
ENTITY_GRACE_S = 900   # 15 min — mostkuje rotację bufora + keepalive feedera (300 s)

# feeder
FEED_MIN_INTERVAL_S = 15    # min odstęp push per mapowanie
FEED_KEEPALIVE_S = 300      # odśwież wartość na nodzie nawet bez zmiany

# pool — prefiksy wykluczone z sensorów (udostępniamy tylko dane subskrypcji)
# mon.* = telemetria noda (ma własne sensory diagnostyczne) — gdyby uszkodzone/stare FW
# wrzuciło ją do pool[], nie chcemy z tego drugiego kompletu encji.
POOL_EXCLUDED_PREFIXES = ("get.", "msg.", "mon.")

# Telemetria noda — zamknięty zbiór 11 encji (kategoria NET w BE).
# FW ≥0.75 wysyła je w osobnej tablicy status["mon"] jako mon.<klucz>; starsze FW trzymają
# je w status["pub"] jako pub.<klucz> — stąd rozpoznanie po nazwie (flota bez OTA działa dalej).
MON_KEYS = frozenset({
    "wifi_rssi", "wifi_nets", "uptime_s",
    "net_ping", "net_jitter", "net_loss",
    "node_ping", "node_peers",
    "link_ping", "link_jitter", "link_loss",
    # net_score liczy BE (nie node), ale jest kategorii NET i widnieje w katalogu /data/native —
    # bez niego user mógłby go wybrać jako cel feedu i nadpisać wyliczoną jakość łącza na mapie.
    "net_score",
})


# Prefiksy zarezerwowane przez firmware (własne bufory: pub/own/tmp/mon) — subskrypcja ani
# podgląd nie mogą ich użyć. FW ≥0.75 sanityzuje je na „sub" przy odczycie z NVS; tu blokujemy
# u źródła, żeby user nie założył subskrypcji, której encje po aktualizacji zniknęłyby z HA.
RESERVED_PREFIXES = frozenset({"pub", "own", "tmp", "mon"})


def telemetry_key(entity_id: str) -> str | None:
    """entity_id → klucz telemetrii albo None (= zwykła encja usera).

    Zamknięta lista MON_KEYS obowiązuje dla OBU prefiksów: mon.<klucz> (FW ≥0.75)
    i pub.<klucz> (starsze FW). Bramka na mon.* jest konieczna, bo POST /data wpycha
    do bufora mon[] DOWOLNY klucz — bez niej mon.temperature dostałby legacy uid
    "..._node_pub.temperature", czyli ten sam co REALNA encja pub.temperature usera
    (HA odrzuciłby drugą i czujnik zniknąłby). Klucz spoza listy = zwykła encja.
    """
    if entity_id.startswith(("mon.", "pub.")):
        entity_id = entity_id[4:]
    return entity_id if entity_id in MON_KEYS else None


EVENT_NODE = "sensmos_event"
EVENT_MESSAGE = "sensmos_message"
# LoRa (FW ≥ lora9): komendy awaryjne i ramki DATA z inboxu noda (GET /lora/inbox),
# odpytywane w cyklu koordynatora — bez zabierania webhooka noda (to slot usera).
EVENT_LORA_CMD = "sensmos_lora_cmd"
EVENT_LORA_FRAME = "sensmos_lora_frame"
# Wiadomość z urządzenia sparowanego z kontem (np. komunikator: „kod:ALARM”) — tryb chmury.
EVENT_DEVICE_MESSAGE = "sensmos_device_message"

PLATFORMS = ["sensor", "binary_sensor"]
CLOUD_PLATFORMS = ["sensor", "binary_sensor"]
DATA_PLATFORMS = ["sensor"]   # tryb data: tylko sensory (podgląd GET)
