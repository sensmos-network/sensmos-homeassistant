"""Sensmos — opis zdarzeń LoRa dla dziennika HA („Aktywność" na karcie urządzenia).

Bez tego zdarzenia były widoczne tylko w nasłuchu Developer tools; sensor pokazuje
jedynie ostatnią wartość z pollu, więc ramki z tego samego okna 15 s ginęły z widoku.
"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_DEVICE_MESSAGE, EVENT_LORA_CMD, EVENT_LORA_FRAME


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    @callback
    def describe_frame(event: Event) -> dict[str, str]:
        d = event.data
        sub = d.get("sub") or 0
        body = d.get("text")
        if body is None:
            body = d.get("hex") or ""
        tags = [d.get("via") or "?"]
        if d.get("enc"):
            tags.append("AES")
        return {
            LOGBOOK_ENTRY_NAME: "LoRa frame" + (f" sub {sub}" if sub else ""),
            LOGBOOK_ENTRY_MESSAGE: f"received: {body} ({', '.join(tags)})",
        }

    @callback
    def describe_cmd(event: Event) -> dict[str, str]:
        return {
            LOGBOOK_ENTRY_NAME: "LoRa command",
            LOGBOOK_ENTRY_MESSAGE: f"received: {event.data.get('cmd', '')}",
        }

    @callback
    def describe_device_message(event: Event) -> dict[str, str]:
        d = event.data
        text = f"„{d.get('text', '')}”"
        return {
            LOGBOOK_ENTRY_NAME: d.get("name") or f"LoRa {d.get('device', '')}",
            LOGBOOK_ENTRY_MESSAGE: f"🚨 {text}" if d.get("alert") else text,
        }

    async_describe_event(DOMAIN, EVENT_LORA_FRAME, describe_frame)
    async_describe_event(DOMAIN, EVENT_DEVICE_MESSAGE, describe_device_message)
    async_describe_event(DOMAIN, EVENT_LORA_CMD, describe_cmd)
