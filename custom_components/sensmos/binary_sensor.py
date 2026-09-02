"""Sensmos — binary sensory: node online, WebSocket do backendu."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SensmosCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SensmosCoordinator = data["coordinator"]
    device_info: DeviceInfo = data["device_info"]
    entities: list[BinarySensorEntity] = [
        NodeOnlineSensor(coordinator, device_info),
        WsConnectedSensor(coordinator, device_info),
    ]
    if coordinator.lora:
        entities.append(LoraEmergencySensor(coordinator, device_info))
    async_add_entities(entities)


class LoraEmergencySensor(CoordinatorEntity[SensmosCoordinator], BinarySensorEntity):
    """Tryb awaryjny LoRa: node stracił uplink i nadaje ≤4 encje beaconem.

    Do automatyzacji typu „przy awarii wpisz own.status=POZAR" — feeder działa po LAN,
    więc wartość trafia do beaconu (ASCII ≤8 znaków) i właściciel widzi ją w apce
    bez internetu.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "LoRa emergency"
    _attr_icon = "mdi:access-point-network"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{coordinator.device_id}_lora_emergency"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.lora_emerg.get("active"))

    @property
    def extra_state_attributes(self) -> dict:
        e = self.coordinator.lora_emerg
        return {"entities": e.get("eids") or [], "webhook": bool(e.get("webhook"))}


class NodeOnlineSensor(CoordinatorEntity[SensmosCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Online"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{coordinator.device_id}_online"

    @property
    def available(self) -> bool:  # zawsze raportuje
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class WsConnectedSensor(CoordinatorEntity[SensmosCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Backend (WS)"
    _attr_icon = "mdi:transit-connection-variant"

    def __init__(
        self, coordinator: SensmosCoordinator, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{coordinator.device_id}_ws"

    @property
    def is_on(self) -> bool:
        cfg = (self.coordinator.data or {}).get("config") or {}
        return bool(cfg.get("ws_connected"))
