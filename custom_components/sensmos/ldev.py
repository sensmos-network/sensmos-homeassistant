"""Sensmos — karta konta w trybie chmury.

Wiadomości z urządzeń LoRa sparowanych z kontem (np. komunikator) trafiają na jedno urządzenie
„Konto Sensmos”: czujnik „Ostatnia wiadomość”, encja zdarzenia i wpis w dzienniku.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def account_key(entry: ConfigEntry) -> str:
    return f"account_{entry.unique_id or entry.entry_id}"


def msg_signal(entry_id: str) -> str:
    return f"{DOMAIN}_msg_{entry_id}"


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, account_key(entry))},
        translation_key="account",
        manufacturer="Sensmos",
        model="Account",
    )
