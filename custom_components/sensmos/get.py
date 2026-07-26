"""Sensmos — tryb 'data': podgląd (GET) opublikowanych encji innych nodów jako sensory HA.

To tylko snapshot — realtime zostaje na nodzie, więc interwał jest długi (domyślnie 10 min).
Pobiera publiczne dane z /v1/ingest/get/<device_id> (działa dla nodów realnych i programowych).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import BE_GET_URL, DOMAIN, GET_DEFAULT_INTERVAL, OPT_GET_INTERVAL, OPT_GETS

_LOGGER = logging.getLogger(__name__)


class SensmosGet(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Co interwał pobiera bieżące encje skonfigurowanych nodów (podgląd, długi interwał)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        secs = int(entry.options.get(OPT_GET_INTERVAL, GET_DEFAULT_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_get",
            update_interval=timedelta(seconds=secs),
        )
        self.entry = entry

    def targets(self) -> list[dict[str, Any]]:
        return list(self.entry.options.get(OPT_GETS, []))

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        targets = self.targets()
        if not targets:
            return out

        session = async_get_clientsession(self.hass)
        for t in targets:
            did = t["device_id"]
            prefix = t.get("prefix") or "node"
            try:
                async with session.get(
                    BE_GET_URL + did, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.debug("Sensmos get %s HTTP %s", did[:8], resp.status)
                        continue
                    payload = await resp.json()
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("Sensmos get %s error: %s", did[:8], err)
                continue

            online = bool(payload.get("active"))
            for e in payload.get("entities", []):
                eid = e.get("entity_id")
                if not eid:
                    continue
                out[f"{did}:{eid}"] = {
                    "device_id": did,
                    "prefix": prefix,
                    "entity_id": eid,
                    "value": e.get("value"),
                    "unit": e.get("unit"),
                    "online": online,
                }
        return out
