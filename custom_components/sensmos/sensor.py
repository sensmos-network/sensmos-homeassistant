"""Sensmos — sensory: dane subskrypcji (sub.*) + statusy noda (uptime)."""
from __future__ import annotations

import time
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ENTITY_GRACE_S,
    MODE_CLOUD,
    MODE_DATA,
    OPT_FEEDS,
    POOL_EXCLUDED_PREFIXES,
    telemetry_key,
)
from .coordinator import GatewayCoordinator, SensmosCoordinator
from .get import SensmosGet


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]

    # tryb data: sensory to podgląd (GET) opublikowanych encji innych nodów
    if data.get("mode") == MODE_DATA:
        await _setup_get_sensors(hass, entry, async_add_entities, data)
        return
    if data.get("mode") == MODE_CLOUD:
        for coord in data["coordinators"].values():
            _setup_gateway_sensors(entry, async_add_entities, coord)
        return

    coordinator: SensmosCoordinator = data["coordinator"]
    device_info: DeviceInfo = data["device_info"]

    # statusy noda
    entities: list[SensorEntity] = [
        UptimeSensor(coordinator, device_info),
    ]
    if coordinator.lora:
        entities += [
            LoraLastCommandSensor(coordinator, device_info),
            LoraRoleSensor(coordinator, device_info),
        ]

    known: set[str] = set()
    known_mon: set[str] = set()   # osobno: kluczem jest goły klucz telemetrii, nie eid
    known_subs: set[int] = set()  # LoRa: sensor per pod-adres (sub) widziany w inboxie
    # Encje karmione z HA TEŻ pokazujemy (echo: user widzi, co node realnie publikuje —
    # weryfikacja end-to-end). Pętlę HA→node→HA tnie guard w feederze (źródło z domeny
    # sensmos = odmowa), nie ukrywanie sensora.
    fed = {f["node_entity"] for f in entry.options.get(OPT_FEEDS, [])}

    @callback
    def _discover() -> None:
        new: list[SensorEntity] = []
        # dane z subskrypcji (pool → sub.*/prefix usera)
        for ent in coordinator.pool_entities:
            eid = ent.get("entity_id", "")
            if not eid or eid in known or eid.startswith(POOL_EXCLUDED_PREFIXES):
                continue
            known.add(eid)
            new.append(PoolSensor(coordinator, device_info, eid))
        # telemetria noda (mon.<klucz> z FW ≥0.75 albo pub.<klucz> ze starszych) → diagnostyka
        for ent in coordinator.mon_entities:
            key = telemetry_key(ent.get("entity_id", ""))
            if not key or key in known_mon:
                continue
            known_mon.add(key)
            new.append(MonSensor(coordinator, device_info, key))
        # własne encje noda (pub.* natywne + own.* niestandardowe; karmione = też, jako echo)
        for ent in coordinator.node_entities:
            eid = ent.get("entity_id", "")
            if not eid or eid in known:
                continue
            known.add(eid)
            new.append(NodeEntitySensor(coordinator, device_info, eid, fed=eid in fed))
        # ramki LoRa: jeden sensor na pod-adres (sub 0 = node-baza, 1..255 = czujniki)
        if coordinator.lora:
            for fr in coordinator.lora_frames:
                sub = int(fr.get("sub") or 0)
                if sub in known_subs:
                    continue
                known_subs.add(sub)
                new.append(LoraFrameSensor(coordinator, device_info, sub))
        if new:
            async_add_entities(new)

    async_add_entities(entities)
    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


def _setup_gateway_sensors(
    entry: ConfigEntry, async_add_entities: AddEntitiesCallback, coord: GatewayCoordinator
) -> None:
    """Brama w trybie cloud: te same encje ramek co node-baza + diagnostyka z BE."""
    info = DeviceInfo(identifiers={(DOMAIN, coord.device_id)})
    async_add_entities(
        [
            GwBeaconsSensor(coord, info),
            GwLastBeaconSensor(coord, info),
            GwFramesSensor(coord, info),
            GwHearsSensor(coord, info),
            GwHeardBySensor(coord, info),
        ]
    )
    known_subs: set[int] = set()

    @callback
    def _discover() -> None:
        new: list[SensorEntity] = []
        for fr in coord.lora_frames:
            sub = int(fr.get("sub") or 0)
            if sub in known_subs:
                continue
            known_subs.add(sub)
            new.append(LoraFrameSensor(coord, info, sub))
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coord.async_add_listener(_discover))


class _GwStat(CoordinatorEntity[GatewayCoordinator], SensorEntity):
    """Diagnostyka bramy z BE (co 60 s) — to samo, co karta bramy w apce."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _key = ""

    def __init__(self, coordinator: GatewayCoordinator, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{coordinator.device_id}_gw_{self._key}"

    @property
    def available(self) -> bool:
        return self.coordinator.stats_fresh


class GwBeaconsSensor(_GwStat):
    """Beacony Sensmos nadane przez bramę (licznik BE — zeruje się przy restarcie serwera)."""

    _key = "beacons"
    _attr_name = "Beacons sent"
    _attr_icon = "mdi:broadcast"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int | None:
        return self.coordinator.stats.get("beacons")


class GwLastBeaconSensor(_GwStat):
    _key = "last_beacon"
    _attr_name = "Last beacon"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        return self.coordinator.last_beacon


class GwFramesSensor(_GwStat):
    """Beacony Sensmos usłyszane przez bramę w 24 h (BE liczy lora_frames; DATA idzie do bazy, nie tu)."""

    _key = "frames_24h"
    _attr_name = "Sensmos beacons heard (24 h)"
    _attr_icon = "mdi:radio-tower"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.coordinator.stats.get("frames_24h")


class GwHearsSensor(_GwStat):
    """Ile nodów brama słyszy (24 h)."""

    _key = "hears"
    _attr_name = "Nodes heard (24 h)"
    _attr_icon = "mdi:ear-hearing"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.coordinator.stats.get("hears")


class GwHeardBySensor(_GwStat):
    """Ile nodów słyszy bramę (24 h)."""

    _key = "heard_by"
    _attr_name = "Heard by nodes (24 h)"
    _attr_icon = "mdi:access-point"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.coordinator.stats.get("heard_by")


async def _setup_get_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    data: dict[str, Any],
) -> None:
    coordinator: SensmosGet | None = data.get("get")
    if coordinator is None:
        return

    known: set[str] = set()

    @callback
    def _discover() -> None:
        new: list[SensorEntity] = []
        for key, ent in (coordinator.data or {}).items():
            if key in known:
                continue
            known.add(key)
            new.append(
                GetSensor(
                    coordinator, entry, ent["device_id"], ent["prefix"], ent["entity_id"]
                )
            )
        if new:
            async_add_entities(new)

    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class GetSensor(CoordinatorEntity[SensmosGet], SensorEntity):
    """Podgląd encji innego noda (GET). Realtime zostaje na nodzie — to tylko snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SensmosGet,
        entry: ConfigEntry,
        device_id: str,
        prefix: str,
        entity_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = f"{device_id}:{entity_id}"
        # BEZ entry.entry_id w tożsamości! Re-add integracji zmienia entry_id → HA tworzył
        # ZDUBLOWANE urządzenia/encje (stare wisiały wyszarzone). Czysty device_id = stabilne.
        self._attr_unique_id = f"get_{device_id[:12]}_{entity_id}"
        self._attr_name = f"{prefix}.{entity_id}"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"get:{device_id}")},
            name=f"Sensmos {prefix} ({device_id[:8]})",
            manufacturer="Sensmos",
            model="Remote node (preview)",
            configuration_url=f"https://sensmos.com/map/?node={device_id}",
        )

    def _ent(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._key)

    @property
    def available(self) -> bool:
        ent = self._ent()
        return super().available and ent is not None and ent.get("online", True)

    @property
    def native_value(self) -> Any:
        ent = self._ent()
        if ent is None:
            return None
        val = ent.get("value")
        try:
            f = float(val)
            return int(f) if f == int(f) else round(f, 4)
        except (TypeError, ValueError):
            self._attr_state_class = None
            return val

    @property
    def native_unit_of_measurement(self) -> str | None:
        return (self._ent() or {}).get("unit") or None


class _Base(CoordinatorEntity[SensmosCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info


class _DynSensor(_Base):
    """Encja dynamiczna noda — wartość/jednostka czytane na żywo z bufora."""

    _uid_kind = "dyn"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo, entity_id: str
    ) -> None:
        super().__init__(coordinator, device_info)
        self._eid = entity_id
        self._attr_unique_id = f"{coordinator.device_id}_{self._uid_kind}_{entity_id}"
        self._attr_name = entity_id
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._cached: dict[str, Any] | None = None   # ostatnia znana encja (sticky przy wyparciu z bufora)
        self._cached_mono = 0.0

    def _source(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _find(self) -> dict[str, Any] | None:
        for ent in self._source():
            if ent.get("entity_id") == self._eid:
                return ent
        return None

    def _current(self) -> dict[str, Any] | None:
        """Encja z bieżącego snapshotu; gdy chwilowo wypadła z małego bufora noda
        (rotacja/ewikcja/prune) — ostatnia znana przez ENTITY_GRACE_S, żeby sensor nie
        migał na 'unavailable' mimo świeżej wartości. Dopiero realnie stara -> None."""
        ent = self._find()
        if ent is not None:
            self._cached = ent
            self._cached_mono = time.monotonic()
            return ent
        if self._cached is not None and (time.monotonic() - self._cached_mono) < ENTITY_GRACE_S:
            return self._cached
        return None

    @property
    def available(self) -> bool:
        # _current() (nie samo _find()) — encja chwilowo wyparta z małego bufora noda zostaje
        # dostępna ze sticky wartością; entities_alive chroni dodatkowo przy nieudanym pollu.
        return self.coordinator.entities_alive and self._current() is not None

    @property
    def native_value(self) -> Any:
        ent = self._current()
        if ent is None:
            return None
        val = ent.get("value")
        try:
            f = float(val)
            return int(f) if f == int(f) else round(f, 4)
        except (TypeError, ValueError):
            self._attr_state_class = None
            return val

    @property
    def native_unit_of_measurement(self) -> str | None:
        ent = self._current()
        return (ent or {}).get("unit") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ent = self._current() or {}
        return {"age_s": ent.get("age_s")}


class PoolSensor(_DynSensor):
    """Encja z subskrypcji (sub.* / prefix usera) widoczna w HA."""

    _uid_kind = "pool"

    def _source(self) -> list[dict[str, Any]]:
        return self.coordinator.pool_entities


class NodeEntitySensor(_DynSensor):
    """Własna encja noda: pub.* (natywna) lub own.* (niestandardowa).

    fed=True → encja karmiona feederem z HA; sensor to ECHO bufora noda
    (weryfikacja end-to-end). NIE używać go jako źródła feedu (guard w feederze).
    """

    _uid_kind = "node"

    def __init__(self, coordinator, device_info, entity_id: str, fed: bool = False) -> None:
        super().__init__(coordinator, device_info, entity_id)
        self._fed = fed

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return {"fed_from_ha": True} if self._fed else None

    def _source(self) -> list[dict[str, Any]]:
        return self.coordinator.node_entities


class MonSensor(_DynSensor):
    """Telemetria noda (WiFi/NET/uptime) — encja diagnostyczna.

    Ta sama wartość przychodzi jako mon.<klucz> (FW ≥0.75) albo pub.<klucz> (starsze FW),
    więc dopasowujemy po KLUCZU, nie po pełnym eid: OTA (w obie strony) nie tworzy
    drugiego sensora i nie gasi istniejącego.
    """

    _uid_kind = "node"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo, key: str
    ) -> None:
        # LEGACY unique_id: człon zostaje "pub.<klucz>" — dokładnie ten string, który ta
        # encja ma dziś jako NodeEntitySensor. Zero migracji, zero duchów, bezpieczny rollback FW.
        super().__init__(coordinator, device_info, f"pub.{key}")
        self._key = key
        self._attr_name = f"mon.{key}"

    def _source(self) -> list[dict[str, Any]]:
        return self.coordinator.mon_entities

    def _find(self) -> dict[str, Any] | None:
        for ent in self._source():
            if telemetry_key(ent.get("entity_id", "")) == self._key:
                return ent
        return None


class LoraLastCommandSensor(_Base):
    """Ostatnia komenda awaryjna CMD odebrana radiem (właściciel → apka → BE → eter)."""

    _attr_icon = "mdi:console-line"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator, device_info)
        self._attr_unique_id = f"{coordinator.device_id}_lora_cmd"
        self._attr_name = "LoRa last command"

    def _last(self) -> dict[str, Any] | None:
        cmds = self.coordinator.lora_cmds
        return cmds[-1] if cmds else None

    @property
    def native_value(self) -> str | None:
        c = self._last()
        return c.get("payload") if c else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self._last() or {}
        return {"ts": c.get("ts")}


class LoraFrameSensor(_Base):
    """Ostatnia ramka DATA dla danego pod-adresu (sub). Stan = treść (tekst lub hex).

    Ring inboxu noda ma 6 wpisów — gdy ramkę tego sub wyprą inne, sensor trzyma
    ostatnią znaną wartość (sticky), zamiast migać na unavailable.
    """

    _attr_icon = "mdi:radio-tower"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo, sub: int
    ) -> None:
        super().__init__(coordinator, device_info)
        self._sub = sub
        self._attr_unique_id = f"{coordinator.device_id}_lora_frame_{sub}"
        self._attr_name = "LoRa frame" if sub == 0 else f"LoRa frame sub {sub}"
        self._cached: dict[str, Any] | None = None

    def _current(self) -> dict[str, Any] | None:
        for fr in reversed(self.coordinator.lora_frames):
            if int(fr.get("sub") or 0) == self._sub:
                self._cached = fr
                return fr
        return self._cached

    @property
    def native_value(self) -> str | None:
        fr = self._current()
        if fr is None:
            return None
        v = fr.get("text") if fr.get("text") is not None else fr.get("hex")
        # 128 B binarnie = 256 znaków hex, a HA przyjmuje stan ≤255 (inaczej „unknown" i błąd
        # w logu). Pełny hex zostaje w atrybucie `hex`.
        return v[:254] + "…" if v and len(v) > 255 else v

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        fr = self._current() or {}
        return {
            "sub": self._sub,
            "encrypted": bool(fr.get("enc")),
            "via": fr.get("via"),
            "hex": fr.get("hex"),
            "ts": fr.get("ts"),
        }


class LoraRoleSensor(_Base):
    """Rola radia (Point/Scanner) + diagnostyka klucza i odbioru jawnych — z /info.lora."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:antenna"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator, device_info)
        self._attr_unique_id = f"{coordinator.device_id}_lora_role"
        self._attr_name = "LoRa role"

    @property
    def native_value(self) -> str | None:
        info = self.coordinator.lora_info
        if not info:
            return None
        return "point" if info.get("role") == 1 else "scanner"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.coordinator.lora_info
        return {
            "board": info.get("board"),
            "rx_key_set": bool(info.get("rx_key")),
            "accept_plain": bool(info.get("open")),
        }


class UptimeSensor(_Base):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "s"
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator, device_info)
        self._attr_unique_id = f"{coordinator.device_id}_uptime"
        self._attr_name = "Uptime"

    @property
    def native_value(self) -> int | None:
        status = (self.coordinator.data or {}).get("status") or {}
        return status.get("uptime_s")
