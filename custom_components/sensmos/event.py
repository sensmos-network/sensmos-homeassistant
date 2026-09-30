"""Sensmos — encja zdarzenia „Wiadomość” na karcie konta (tryb chmury)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_CLOUD
from .ldev import account_device_info, account_key, msg_signal


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if hass.data[DOMAIN][entry.entry_id].get("mode") != MODE_CLOUD:
        return
    async_add_entities([AccountMessageEvent(entry)])


class AccountMessageEvent(EventEntity):
    """Każda wiadomość z urządzenia konta; „alarm”, gdy urządzenie oznaczyło ją jako pilną."""

    _attr_has_entity_name = True
    _attr_translation_key = "ldev_message"
    _attr_event_types = ["message", "alert"]
    _attr_icon = "mdi:message-badge"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{account_key(entry)}_message"
        self._attr_device_info = account_device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, msg_signal(self._entry_id), self._on_msg))

    @callback
    def _on_msg(self, m: dict[str, Any]) -> None:
        self._trigger_event("alert" if m.get("alert") else "message", {
            "text": m.get("text"),
            "from": m.get("name") or f"LoRa {m.get('device', '')}",
            "device": m.get("device"),
            "via": m.get("rx"), "rssi": m.get("rssi"), "snr": m.get("snr"),
        })
        self.async_write_ha_state()
