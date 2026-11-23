"""Sensmos — powiadomienie na komunikator (tryb chmury, od 0.13.0).

Każdy komunikator sparowany z kontem to encja `notify.*`: `notify.send_message` wysyła tekst przez sieć
Sensmos (najbliższa brama albo nod, który słyszał komunikator), więc automatyzacja HA dociera do kogoś
w polu, w górach albo na działce, gdzie nie ma zasięgu komórki. Do 100 bajtów, dłuższy tekst jest ucinany.
"""
from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import CloudSendError, SensmosCloud
from .const import DOMAIN, MODE_CLOUD
from .ldev import account_device_info, account_key


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    if data.get("mode") != MODE_CLOUD:
        return
    cloud: SensmosCloud = data["cloud"]
    async_add_entities(MessengerNotify(entry, cloud, m["id8"], m["name"]) for m in cloud.ldevs)


def compose(message: str, title: str | None) -> str:
    """Tytuł przed treścią („Brama: otwarta po zmroku”) — komunikator ma jedno pole tekstu."""
    return f"{title}: {message}" if title else message


class MessengerNotify(NotifyEntity):
    """Komunikator Sensmos jako cel powiadomień HA."""

    _attr_has_entity_name = True
    _attr_translation_key = "messenger"
    _attr_icon = "mdi:message-arrow-right"

    def __init__(self, entry: ConfigEntry, cloud: SensmosCloud, id8: str, name: str) -> None:
        self._cloud = cloud
        self._id8 = id8
        self._attr_unique_id = f"{account_key(entry)}_messenger_{id8}"
        self._attr_device_info = account_device_info(entry)
        self._attr_translation_placeholders = {"name": name or id8}
        self._attr_extra_state_attributes = {"messenger": id8}

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        try:
            await self._cloud.send_ldev(self._id8, compose(message, title))
        except CloudSendError as err:
            raise HomeAssistantError(f"Sensmos messenger {self._id8}: {err}") from err
