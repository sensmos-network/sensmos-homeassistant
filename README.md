<img src="logo.png" alt="Sensmos" height="80">

# Sensmos — Home Assistant integration (HACS)

Bring Sensmos into Home Assistant — three ways:

- **🔌 Physical node** — connect a Sensmos ESP32 node on your LAN (HTTP API, **no cloud, no broker**): read its data in HA and feed HA data back to it.
- **🟣 Data only** — no hardware: push selected Home Assistant sensors straight to the [live map](https://sensmos.com/map/) as a software node (purple). Pick a passkey, choose entities, done.
- **☁️ Sensmos account** — your LoRaWAN gateways (e.g. Crankk) paired in the Sensmos app become devices in HA, and HA is their base: it receives your LoRa sensors' frames and decrypts them itself.

> Sensmos is a DePIN sensor network: nodes publish data to a live map, receive GALU allocations, and can subscribe to each other.

## Features

- **Your node's own data as sensors** — the node's native (`pub.*`) and custom (`own.*`) entities show up in HA automatically (entities you feed *from* HA are skipped to avoid loops).
- **Feed the node from HA** — map any HA sensor → a node entity (native `pub.*` or custom `own.*`). Units are converted automatically (kW→W, mV→V, °F→°C…). Binary sensors (motion, door…) are sent as `1`/`0`. No automations to write — the integration tracks state changes and refreshes every 5 min.
- **Subscription sensors** — data your node subscribes to from other nodes (`sub.*` / your prefix) appears as HA sensors as soon as it arrives.
- **Subscribe from HA** — pick a target node's device ID, preview its entities, confirm; sensors appear on their own.
- **Node status** — uptime, online, backend (WS) connectivity.
- **Node telemetry as diagnostics** — WiFi/network health (`wifi_rssi`, `net_ping`, `link_loss`…) lands in the device's *Diagnostic* section instead of cluttering your sensor list.
- **Node events as HA events** — `sensmos_event` (batch_sent, sub_received, ws_connected) and `sensmos_message` (incoming message) for automations. The webhook is configured on the node automatically.

## Installation

One click opens this repository in HACS (HACS must be installed):

[![Open Sensmos in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sensmos-network&repository=sensmos-homeassistant&category=integration)

1. Press the button and **Download** (or HACS → ⋮ → **Custom repositories** → add `https://github.com/sensmos-network/sensmos-homeassistant`, category *Integration*), then restart Home Assistant.
2. **Have a Sensmos node on the same network?** After the restart Home Assistant finds it by itself (0.12.0+): open **Settings → Devices & services**, click **Add** on the discovered *Sensmos* card and type only the node's PIN. If the router later gives the node a new IP, the integration picks it up on its own. Otherwise add the integration by hand:

   [![Add the Sensmos integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sensmos)
3. Pick a mode:
   - **🔌 Physical node** — enter the **node address** (IP from the Sensmos app, or `sensmos-xxxxxx.local`) and the **PIN**.
   - **🟣 Data only** — enter a **passkey** (≥32 chars; this is your node's identity), optionally a label and lat/lon. Then open **Configure** to choose which HA sensors to publish.
   - **☁️ Sensmos account** — HA shows a 12-character code; in the Sensmos app open **Settings → Paired devices → Pair** and enter it.

> **Want Home Assistant itself to be a full node?** On Home Assistant OS use the [Sensmos Hub add-on](https://github.com/sensmos-network/sensmos-hub): a node without an ESP32, with the encrypted tunnel to HA from the app. Step-by-step guide: https://sensmos.com/ha/

## Sensmos account mode (your LoRaWAN gateways)

For LoRaWAN gateways that forward to `sensmos.com:1700` and are paired with your wallet in the Sensmos app (Dashboard → **+** → *Add LoRaWAN gateway (e.g. Crankk)*).

- **Pairing without a wallet in HA** — the app issues a revocable token (scopes `lora.rx`, `lora.tx`) and hands it over sealed with the code shown in HA; the server never sees the code. Revoke it in the app and HA asks you to pair again.
- **Gateways as devices** — every gateway on the account appears as a device; one paired or unpaired in the app appears or disappears here on its own.
- **Your sensors' frames** — a LoRa sensor sends DATA frames to the gateway's Sensmos ID (id8). The server passes each frame on exactly as the sensor sent it and HA decrypts it itself, like a node base: a **LoRa DATA frame sub N** sensor per sensor sub-address, the `sensmos_lora_frame` event and a logbook line. The last frames are kept on disk, so the sensors come back after a restart; a sub-address that drops off the list of recent frames disappears from HA instead of showing *unavailable*.
- **Per-gateway options** (Configure): the **Key phrase** your sensors use (only its SHA-256 is stored) and **Also accept plain frames** (off by default).
- **Diagnostics** — Online, Beacons sent, Last beacon, Sensmos beacons heard (24 h), Nodes heard (24 h), Heard by nodes (24 h); they turn unavailable after 3 minutes without an update from the server.
- **Account card** — messages from devices paired with your wallet in the app (e.g. the Sensmos messenger) land on one **Sensmos account** device: **Last message** (text, from, when, who heard it) and a **Message** event entity (type *message* or *alert*) — use it as an automation trigger straight from the device page. Every message is also a logbook line (“Tatry: „kod:ALARM””). The `sensmos_device_message` bus event stays for advanced automations.
- **Write to the messenger** (0.13.0) — every messenger paired with the account is a `notify` entity (**Messenger Tatry**), so any automation can send it a text with `notify.send_message`, and the action `sensmos.messenger_send` (messenger name or id8, text) does the same and returns `msg_id`. The server encrypts the text with the messenger's key and the nearest Sensmos gateway or node that heard the messenger in the last 6 hours sends it on 869.525 MHz; the messenger confirms by radio. Up to 100 bytes (longer text is cut) and 30 messages an hour per account, because the radio time belongs to other people's gateways and nodes. The `sensmos_device_message_state` event says `delivered` or `failed` (after 5 tries, about 10 minutes). Needs the token scope `lora.tx` (pairing asks for it by default).
- **One bridge: messenger ↔ Meshtastic ↔ MeshCore** — the blueprint [`komunikator_most.yaml`](blueprints/automation/sensmos/komunikator_most.yaml) joins the messenger to a Meshtastic channel (a Meshtastic node with Wi-Fi as the MQTT gateway, JSON output on, a channel named `mqtt` with downlink, and the MQTT integration) and to a MeshCore channel (the [MeshCore integration](https://github.com/meshcore-dev/meshcore-ha) with a companion radio). Messenger text that starts with `m ` goes to Meshtastic, `c ` goes to MeshCore; channel text comes back to the messenger as `M name: text` / `C name: text`. It can also join the Meshtastic and MeshCore channels to each other (`[M] name: text`, `[C] name: text`). No Sensmos Hub needed. Use private channels, not the public LongFast or Public.

  [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fkomunikator_most.yaml)
- If HA is offline, frames addressed to the gateway are dropped (same as an offline node). Reception is billed like a node base (Wallet → Service expenses). Transmitting through the gateway is not available in this mode.

## Data-only mode (push HA sensors to the map)

No Sensmos hardware needed. After adding the integration in **Data only** mode, open **Configure**:

- **➕ Publish a Home Assistant sensor** — pick an HA sensor + the Sensmos entity name. Use `pub.<native>` for a categorized + heatmap entity (e.g. `pub.pm25`, `pub.temp_out`, `pub.batt_soc`) or `own.<anything>` for custom data.
- **➖ Remove a published sensor**.
- **👁️ Preview another node's data** — read any node's published data (real or software) into HA as sensors. Enter its device ID (copy it from the node popup on the map) and a local prefix; you get `prefix.*` sensors. This is a **preview, not realtime** — it polls on a long interval (default 10 min). No GALU, no subscription — it just reads public map data.
- **➖ Stop previewing a node**.
- **⚙️ Settings** — push interval (min 20 s), preview poll interval (min 2 min), map label, optional lat/lon (GeoIP if empty).

Your sensors show up on the [live map](https://sensmos.com/map/) as a purple software node. Numeric sensors send value+unit; binary sensors send `1`/`0`. Up to 50 entities. Native entity names: see the full list in the [ESPHome component README](https://github.com/sensmos-network/sensmos-esphome#entity-names).

---

The sections below are for the **🔌 physical node** mode.

## Feeding the node (entity mapping)

Settings → Devices & Services → Sensmos → **Configure**:

- **➕ Feed a native entity (`pub.*`)** — pick a native entity the network counts for GALU allocation (with its unit), then a matching HA sensor; the picker is filtered to compatible sensors and units are converted automatically.
- **➕ Feed a custom entity (`own.*`)** — a name + any HA sensor or binary sensor. Stored on the node as `own.<name>` (usable in node scripts). The unit is taken from the HA entity; binary states become `1`/`0`.
- **➖ Remove a feed mapping**.

## Subscriptions

**Configure → 📡 Subscribe to another node**: enter the device ID, a local prefix (e.g. `neighbor`), and a number of days. You'll see the available entities and the cost; after confirming, data shows up in HA as `neighbor_*` sensors. Billed daily in GALU from the wallet pool.

## Events

```yaml
# Automation: a message from your paired LoRa device (Sensmos account mode)
trigger:
  - platform: event
    event_type: sensmos_device_message
condition:
  - condition: template
    value_template: "{{ 'kod:ALARM' in trigger.event.data.text }}"
action:
  - service: notify.mobile_app
    data:
      message: "{{ trigger.event.data.name or trigger.event.data.device }}: {{ trigger.event.data.text }}"
```

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
| `sensor.<node>_lora_data_frame`, `…_lora_data_frame_sub_N` | last DATA frame per sub-address (state = payload; attributes `encrypted`, `via` = `rf`/`ws`, `hex`, `ts`) — one sensor per LoRa sensor behind this node; gone when the node's recent-frames list (6) no longer has that sub-address |
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

**Messenger to home without internet** (messenger firmware 0.17-kom): the messenger sends `#n text` straight to this node by radio (app → messenger → Home without internet), the node puts it in its inbox and the integration reads it over the local network, so it works with the internet down on both ends. Import the blueprint [`komunikator_dom.yaml`](blueprints/automation/sensmos/komunikator_dom.yaml): it sends the confirmation `ok #n` back through the node's radio, adds your reply from an `input_text` helper if one is waiting, and notifies your phone. The messenger listens for the answer for a minute after each send and retries up to 3 times. Put the node in Point mode so it hears the messenger all the time.

**Range test** (messenger firmware 0.18-kom): the app's range test sends `t #n` about once a minute while you walk; the same blueprint answers each one with a silent `ok #n` (no notification, your pending reply stays for the next message), and the messenger records how well it heard home. Update the blueprint after upgrading the messenger: re-import it from the button below.

**Reply from the notification:** the blueprint's notification has a **Reply** action (Home Assistant Companion app, Android and iOS). Type your answer right in the notification: within about 50 seconds of the message it goes out by radio at once, while the messenger is still listening; later it waits in the reply helper and travels with the messenger's next `?` question (set *Pytaj o odpowiedź co (min)* in the app). Without a helper the late reply is sent at once anyway, best effort.

**"I'm safe" to the whole family:** fill in *Family* in the blueprint (notify services, comma separated). A message like `jestem bezpieczny`, `jestem ok`, `I'm safe`, `bin sicher` or `estou bem` from the messenger goes to all of them at once: from a place with no mobile signal, by radio to your node, then out through your home's internet. No SMS gateway, no extra service.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fkomunikator_dom.yaml)

**Messenger ↔ Meshtastic:** with a Meshtastic node on ESP32 with Wi-Fi at home as the MQTT gateway and the Sensmos Hub Meshtastic bridge (hub 0.2.0-beta6 or newer, see [sensmos.com/docs/hub/](https://sensmos.com/docs/hub/)), the blueprint [`meshtastic_komunikator.yaml`](blueprints/automation/sensmos/meshtastic_komunikator.yaml) joins the messenger to one Meshtastic channel. A messenger message starting with `m ` goes to the channel as `[name] text` (MQTT JSON downlink `sendtext` on `<root>/2/json/mqtt/`). Text from that channel waits in the reply helper and reaches the messenger with its next `?` question. On the gateway: JSON Output Enabled, Encryption Enabled off, a channel named `mqtt` with downlink, and your private channel (not the public LongFast) with uplink and downlink. Use it next to `komunikator_dom.yaml` with the same node, sub and reply helper.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fmeshtastic_komunikator.yaml)

**Alarm button for your family** (ESPHome recipe [`lora-przycisk.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-przycisk.yaml)): a big button by a bed or in a workshop, on LoRa with no Wi-Fi there. Holding it for 2 s sends `alarm=1`. Import the blueprint [`przycisk_alarmu.yaml`](blueprints/automation/sensmos/przycisk_alarmu.yaml): it notifies the phones you list with an **I'm on it** action, escalates to a second group if nobody answers within a few minutes, and once someone accepts it sends `ack=1` back through the node's radio, so the button's LED stays on and it beeps. Two short presses cancel. This is help from family and neighbours. It does not replace 112.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fprzycisk_alarmu.yaml)

**Quiet at home** (care for an elderly relative): import [`cisza_w_domu.yaml`](blueprints/automation/sensmos/cisza_w_domu.yaml) and pick any entities that change when the person is active: a Sensmos LoRa door reed, a fridge contact, a Zigbee motion sensor. If none of them changes for the hours you set during the day, the family gets a notification with an **I'll check** action, and the others are told someone took it. One notification per quiet spell. This is family care, not certified tele-care, and it does not replace 112. The same blueprint guards **working alone in the field**: pick the LoRa messenger's frame sensor as the activity sensor and a quiet time from 30 min, and the family hears when no message or ping has come in.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fcisza_w_domu.yaml)

**Cold room and freezer** (ESPHome recipe [`lora-chlodnia.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-chlodnia.yaml), or any temperature sensor): import [`chlodnia.yaml`](blueprints/automation/sensmos/chlodnia.yaml), one automation per probe. It notifies when the temperature stays above your limit longer than a door opening, again when it is back to normal, and once when no reading came for too long (power cut, unplugged board). The temperature log is Home Assistant's own history: History → download data as CSV. For HACCP paperwork use a calibrated reference thermometer and compare the probes with it now and then.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fchlodnia.yaml)

**Bike, trailer or machine tracker** (ESPHome recipe [`lora-lokalizator.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-lokalizator.yaml), or any pair of latitude and longitude sensors): import [`lokalizator.yaml`](blueprints/automation/sensmos/lokalizator.yaml), pick the two sensors and a zone. The position appears on the Home Assistant map as `device_tracker.sensmos_<name>`, and when the thing leaves the zone you get one notification with a map link that updates itself with every new position and is cleared when it is back. No SIM card or subscription: it works where a Sensmos node hears the board, so it guards the area around home, the garage or the yard, not the whole country.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Flokalizator.yaml)

**Health of all your LoRa sensors** (Sensmos Hub 0.2.0-beta2 or newer): import [`czujniki_lora.yaml`](blueprints/automation/sensmos/czujniki_lora.yaml) once, not per sensor. You get a notification when any `sensmos_lora` sensor goes quiet and when it is back (the hub's `sensmos_lora_silent` and `sensmos_lora_back` events), and one summary a day listing weak batteries (`bat`, `batt`), low voltage (`v`, `vbat`, `volt`, `mv`), weak radio links (RSSI from the `_heard` entity) and sensors that are still silent. Nothing to report, no message. Skip sensors on a 12 V battery (car, camper) by their sub number.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fczujniki_lora.yaml)

**Frost in the garden** (any thermometer, e.g. the weather station recipe [`lora-pogoda.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-pogoda.yaml)): import [`przymrozek.yaml`](blueprints/automation/sensmos/przymrozek.yaml). In the evening it reads the next 15 hours of your weather integration's forecast (`weather.get_forecasts`, hourly, falling back to daily) and warns when it drops to the threshold (default +1 °C at 2 m, which often means frost on the ground). At night it warns once more when the real thermometer stays below the threshold for 15 minutes. Only in the months you pick (default April–June and September–October).

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fprzymrozek.yaml)

**Water leak with a shut-off valve** (Sensmos `_leak` probes from the garage guard or camper recipes, or any Zigbee probe): import [`zalanie.yaml`](blueprints/automation/sensmos/zalanie.yaml) once for all probes. It first closes the main valve (a `valve` entity, or a `switch` that is on while water flows), then sends a critical notification (iOS critical sound, Android alarm stream) and repeats it every 5 minutes until someone taps *Checking* or the probe dries. The valve stays closed until you open it by hand.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fzalanie.yaml)

**Chicken coop door** (ESPHome recipe [`lora-przelacznik.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-przelacznik.yaml) with a relay driving a linear actuator or a coop door motor): import [`kurnik.yaml`](blueprints/automation/sensmos/kurnik.yaml). It opens after sunrise (never before the time you set) and closes after sunset over radio through your node with LoRa, waits for the board's confirmation, tries once more, and notifies you before the night if the board does not confirm or the optional door contact disagrees. Two commands a day, about 1 s on air each.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fkurnik.yaml)

**Watering with the weather** (ESPHome recipe [`lora-szklarnia.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-szklarnia.yaml) with a valve on a relay): import [`podlewanie.yaml`](blueprints/automation/sensmos/podlewanie.yaml). At the time you set it opens the valve over radio for the set minutes, but skips the day when the soil is still moist, when today's rain (e.g. the `rain_today` meter of the weather station recipe) is enough or when the hourly forecast brings rain in the next 12 hours. Opening and closing are both confirmed by the board; if the close is not confirmed you get a high-priority notification (the board still closes by itself after its `max_open`).

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fpodlewanie.yaml)

**Commands for a board on a battery** (ESPHome recipe [`lora-uspiona.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-uspiona.yaml), which listens for 30 s after each frame): import [`polecenie_po_wybudzeniu.yaml`](blueprints/automation/sensmos/polecenie_po_wybudzeniu.yaml). Flip an `input_boolean` helper; the command waits in HA and goes out through your node with LoRa as soon as the board's next frame arrives, and again after every frame until the board reports the new value. Optional switch-off after N minutes (60 for the recipe's fast reports).

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fpolecenie_po_wybudzeniu.yaml)

**Gate or garage left open after dark** (any door contact, e.g. the garage guard recipe): import [`brama_otwarta.yaml`](blueprints/automation/sensmos/brama_otwarta.yaml). After sunset, or when the door stays open for a set time in the dark, you get a notification. With a node with LoRa and a relay board ([`lora-przelacznik.yaml`](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-przelacznik.yaml)) on the gate it has a **Close** button that closes it by radio, and a second notification says whether the contact confirmed it.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fbrama_otwarta.yaml)

**Rain with a window open** (rain gauge of the [garden weather station](https://github.com/sensmos-network/sensmos-esphome/blob/main/examples/lora-pogoda.yaml) or any weather entity): import [`deszcz_otwarte.yaml`](blueprints/automation/sensmos/deszcz_otwarte.yaml). When it starts to rain and any of the windows, skylights, greenhouse vents or shed doors you picked is open, one notification lists them, at most once an hour.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsensmos-network%2Fsensmos-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsensmos%2Fdeszcz_otwarte.yaml)

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

- A Sensmos node on the same network (firmware with the HTTP API) — or, for the Sensmos account mode, a LoRaWAN gateway paired in the Sensmos app.
- Home Assistant 2024.12+.

## Part of the Sensmos project

| | |
|---|---|
| 🌐 Website | https://sensmos.com |
| 📱 App | https://github.com/sensmos-network/sensmos-app |
| 🔌 Firmware | https://github.com/sensmos-network/sensmos-firmware |
| 🏠 Hub (node without ESP32) | https://github.com/sensmos-network/sensmos-hub |
| 📜 Protocol | https://github.com/sensmos-network/sensmos-protocol |
| 💬 Discord | https://discord.gg/ukea386Kqx |

GALU runs on Polygon. © 2026 Sensmos.
