"""Sensmos — urządzenia LoRa sparowane z kontem (np. komunikator).

Serwer przysyła listę przy logowaniu i po każdym parowaniu, więc urządzenie pojawia się w HA
samo. Każda wiadomość z urządzenia: czujnik „Ostatnia wiadomość”, encja zdarzenia i wpis
w dzienniku — bez nasłuchu w narzędziach deweloperskich.
"""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def ldev_key(id8: str) -> str:
    return f"ldev_{id8}"


def ldev_signal(entry_id: str, id8: str) -> str:
    return f"{DOMAIN}_ldev_{entry_id}_{id8}"


def gw_msg_signal(entry_id: str, gw_device_id: str) -> str:
    return f"{DOMAIN}_gwmsg_{entry_id}_{gw_device_id}"


def ldev_device_info(d: dict[str, Any]) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, ldev_key(d["id8"]))},
        name=d.get("name") or f"LoRa {d['id8']}",
        manufacturer="Sensmos",
        model="LoRa device",
        serial_number=d["id8"],
    )


def clean_ldevs(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for d in items:
        if isinstance(d, dict) and isinstance(d.get("id8"), str) and len(d["id8"]) == 8:
            name = d.get("name")
            out.append({"id8": d["id8"].lower(), "name": name[:32] if isinstance(name, str) else ""})
    return out
