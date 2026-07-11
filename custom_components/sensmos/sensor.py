"""Sensmos — sensory: dane subskrypcji (sub.*) + statusy noda (uptime)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_DATA, OPT_FEEDS, POOL_EXCLUDED_PREFIXES
from .coordinator import SensmosCoordinator
from .get import SensmosGet


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]

    # tryb data: sensory to podgląd (GET) opublikowanych encji innych nodów
    if data.get("mode") == MODE_DATA:
        await _setup_get_sensors(hass, entry, async_add_entities, data)
        return

    coordinator: SensmosCoordinator = data["coordinator"]
    device_info: DeviceInfo = data["device_info"]

    # statusy noda
    entities: list[SensorEntity] = [
        UptimeSensor(coordinator, device_info),
    ]

    known: set[str] = set()
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
        # własne encje noda (pub.* natywne + own.* niestandardowe; karmione = też, jako echo)
        for ent in coordinator.node_entities:
            eid = ent.get("entity_id", "")
            if not eid or eid in known:
                continue
            known.add(eid)
            new.append(NodeEntitySensor(coordinator, device_info, eid, fed=eid in fed))
        if new:
            async_add_entities(new)

    async_add_entities(entities)
    _discover()
    entry.async_on_unload(coordinator.async_add_listener(_discover))


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

    def _source(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _find(self) -> dict[str, Any] | None:
        for ent in self._source():
            if ent.get("entity_id") == self._eid:
                return ent
        return None

    @property
    def available(self) -> bool:
        return super().available and self._find() is not None

    @property
    def native_value(self) -> Any:
        ent = self._find()
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
        ent = self._find()
        return (ent or {}).get("unit") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ent = self._find() or {}
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
