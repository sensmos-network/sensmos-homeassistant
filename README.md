<img src="logo.png" alt="Sensmos" height="80">

# Sensmos — Home Assistant integration (HACS)

Bring Sensmos into Home Assistant — two ways:

- **🔌 Physical node** — connect a Sensmos ESP32 node on your LAN (HTTP API, **no cloud, no broker**): read its data in HA and feed HA data back to it.
- **🟣 Data only** — no hardware: push selected Home Assistant sensors straight to the [live map](https://sensmos.com/map/) as a software node (purple). Pick a passkey, choose entities, done.

> Sensmos is a DePIN sensor network: nodes publish data to a live map, earn GALU, and can subscribe to each other.

## Features

- **Your node's own data as sensors** — the node's native (`pub.*`) and custom (`own.*`) entities show up in HA automatically (entities you feed *from* HA are skipped to avoid loops).
- **Feed the node from HA** — map any HA sensor → a node entity (native `pub.*` or custom `own.*`). Units are converted automatically (kW→W, mV→V, °F→°C…). Binary sensors (motion, door…) are sent as `1`/`0`. No automations to write — the integration tracks state changes and refreshes every 5 min.
- **Subscription sensors** — data your node subscribes to from other nodes (`sub.*` / your prefix) appears as HA sensors as soon as it arrives.
- **Subscribe from HA** — pick a target node's device ID, preview its entities, confirm; sensors appear on their own.
- **Node status** — uptime, online, backend (WS) connectivity.
- **Node telemetry as diagnostics** — WiFi/network health (`wifi_rssi`, `net_ping`, `link_loss`…) lands in the device's *Diagnostic* section instead of cluttering your sensor list.
- **Node events as HA events** — `sensmos_event` (batch_sent, sub_received, ws_connected) and `sensmos_message` (incoming message) for automations. The webhook is configured on the node automatically.

## Installation

1. HACS → Integrations → ⋮ → **Custom repositories** → add this repo (category *Integration*).
2. Install **Sensmos**, then restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → **Sensmos**.
4. Pick a mode:
   - **🔌 Physical node** — enter the **node address** (IP from the Sensmos app, or `sensmos-xxxxxx.local`) and the **PIN**.
   - **🟣 Data only** — enter a **passkey** (≥32 chars; this is your node's identity), optionally a label and lat/lon. Then open **Configure** to choose which HA sensors to publish.

## Data-only mode (push HA sensors to the map)

No Sensmos hardware needed. After adding the integration in **Data only** mode, open **Configure**:

- **➕ Publish a Home Assistant sensor** — pick an HA sensor + the Sensmos entity name. Use `pub.<native>` for a categorized + heatmap entity (e.g. `pub.pm25`, `pub.temp_out`, `pub.batt_soc`) or `own.<anything>` for custom data.
- **➖ Remove a published sensor**.
- **👁️ Preview another node's data** — read any node's published data (real or software) into HA as sensors. Enter its device ID (copy it from the node popup on the map) and a local prefix; you get `prefix.*` sensors. This is a **preview, not realtime** — it polls on a long interval (default 10 min). No GALU, no subscription — it just reads public map data.
- **➖ Stop previewing a node**.
- **⚙️ Settings** — push interval (min 20 s), preview poll interval (min 2 min), map label, optional lat/lon (GeoIP if empty).

Your sensors show up on the [live map](https://sensmos.com/map/) as a purple software node. Numeric sensors send value+unit; binary sensors send `1`/`0`. Up to 50 entities. Native entity names: see the full list in the [ESPHome component README](https://github.com/Galusz/sensmos-esphome#entity-names).

---

The sections below are for the **🔌 physical node** mode.

## Feeding the node (entity mapping)

Settings → Devices & Services → Sensmos → **Configure**:

- **➕ Feed a native entity (`pub.*`)** — pick a network-rewarded native entity (with its unit), then a matching HA sensor; the picker is filtered to compatible sensors and units are converted automatically.
- **➕ Feed a custom entity (`own.*`)** — a name + any HA sensor or binary sensor. Stored on the node as `own.<name>` (usable in node scripts). The unit is taken from the HA entity; binary states become `1`/`0`.
- **➖ Remove a feed mapping**.

## Subscriptions

**Configure → 📡 Subscribe to another node**: enter the device ID, a local prefix (e.g. `neighbor`), and a number of days. You'll see the available entities and the cost; after confirming, data shows up in HA as `neighbor_*` sensors. Billed daily in GALU from the wallet pool.

## Events

```yaml
# Automation triggered by a message received by the node
trigger:
  - platform: event
    event_type: sensmos_message
action:
  - service: notify.mobile_app
    data:
      message: "Node received a message: {{ trigger.event.data }}"
```

## Service `sensmos.push`

Send a value to the node manually (e.g. from an automation):

```yaml
service: sensmos.push
data:
  entity_id: own.tariff
  value: "0.89"
  unit: "PLN/kWh"
```

## LoRa (nodes with a radio, firmware ≥ 0.95-lora9)

If the node reports a radio in `/info` (`lora` field), the integration adds — polling the node's local API every 15 s, no webhook needed:

| Entity / event | What it is |
|---|---|
| `binary_sensor.<node>_lora_emergency` | ON while the node has lost its uplink and is broadcasting its emergency entities over LoRa (attributes: `entities`) |
| `sensor.<node>_lora_last_command` | last emergency command received over the air (from the owner's app) |
| `sensor.<node>_lora_frame`, `…_lora_frame_sub_N` | last DATA frame per sub-address (state = payload; attributes `encrypted`, `via` = `rf`/`ws`, `hex`, `ts`) — one sensor per LoRa sensor behind this node |
| `sensor.<node>_lora_role` | `point` / `scanner`, attributes `board`, `rx_key_set`, `accept_plain` |
| event `sensmos_lora_cmd` | `{device_id, node_id, cmd, ts}` |
| event `sensmos_lora_frame` | `{device_id, node_id, sub, text, hex, enc, via, ts}` |

`device_id` is the Home Assistant device id, `node_id` the Sensmos node id. Every frame and command is one event and one line in the device's logbook ("Activity") — the `lora_frame` sensors only show the latest value at poll time (15 s), so for automations reacting to each frame trigger on the event, not on the sensor state.

Service **`sensmos.lora_send`** transmits a DATA frame from the node's radio to another node or a LoRa sensor (`dst` = 8-hex id of the target node, `sub` = sensor sub-address, `aes` = encrypt with the shared key phrase). Use it to actuate LoRa sensors from automations:

```yaml
service: sensmos.lora_send
data:
  dst: ccd6f51f
  sub: 3
  payload: "valve=off"
```

Emergency automation — when the node goes off-grid, put a status text into one of its emergency entities so the owner sees it in the app over LoRa (values are ASCII, max 8 chars):

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.garage_lora_emergency
    to: "on"
action:
  - service: sensmos.push
    data:
      entity_id: own.status
      value: "ALARM"
```

Frame reception uses a key phrase that never leaves the node (set it in the app: node → LoRa). Emergency commands can only be sent from the owner's app (wallet-signed), not from HA. Receiving frames and transmitting are billed as daily flat fees in GALU (see the app: Wallet → Service expenses).

## Requirements

- A Sensmos node on the same network (firmware with the HTTP API).
- Home Assistant 2024.6+.

## Part of the Sensmos project

| | |
|---|---|
| 🌐 Website | https://sensmos.com |
| 📱 App | https://github.com/Galusz/sensmos-app |
| 🔌 Firmware | https://github.com/Galusz/sensmos-firmware |
| 📜 Protocol | https://github.com/Galusz/sensmos-protocol |
| 💬 Discord | https://discord.gg/ukea386Kqx |

GALU runs on Polygon. © 2026 Sensmos.
