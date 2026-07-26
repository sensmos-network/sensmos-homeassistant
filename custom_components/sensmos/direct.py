"""Sensmos — tryb 'data': wysyłka wybranych encji HA wprost na żywą mapę (/v1/ingest)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    BE_INGEST_URL,
    CONF_KEY,
    CONF_LABEL,
    CONF_LAT,
    CONF_LON,
    DATA_DEFAULT_INTERVAL,
    OPT_MAPPINGS,
    OPT_PUSH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# stany binarne → 1/0
_ON = {"on", "true", "home", "open", "detected", "1"}
_OFF = {"off", "false", "not_home", "closed", "clear", "0"}


class SensmosDirect:
    """Programowy node: co interval czyta zmapowane encje HA i POST-uje na backend."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub = None

    async def async_start(self) -> None:
        secs = int(self.entry.options.get(OPT_PUSH_INTERVAL, DATA_DEFAULT_INTERVAL))
        self._unsub = async_track_time_interval(
            self.hass, self._push, timedelta(seconds=secs)
        )
        await self._push()  # od razu, nie czekaj na pierwszy interwał

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _build_entities(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in self.entry.options.get(OPT_MAPPINGS, []):
            st = self.hass.states.get(m["ha_entity"])
            if st is None or st.state in ("unknown", "unavailable", "", None):
                continue
            raw = str(st.state).lower()
            if raw in _ON:
                val = 1.0
            elif raw in _OFF:
                val = 0.0
            else:
                try:
                    val = float(st.state)
                except (ValueError, TypeError):
                    continue  # nieliczbowe pomijamy
            unit = st.attributes.get("unit_of_measurement") or ""
            out.append({"entity_id": m["entity"], "value": val, "unit": unit})
        return out

    async def _push(self, now=None) -> None:
        entities = self._build_entities()
        if not entities:
            return
        body: dict[str, Any] = {"key": self.entry.data[CONF_KEY], "entities": entities}
        lat = self.entry.options.get(CONF_LAT)
        lon = self.entry.options.get(CONF_LON)
        if lat is not None and lon is not None:
            body["lat"] = lat
            body["lon"] = lon
        label = self.entry.options.get(CONF_LABEL)
        if label:
            body["label"] = label

        try:
            session = async_get_clientsession(self.hass)
            async with session.post(
                BE_INGEST_URL, json=body, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    _LOGGER.debug("Sensmos: sent %d entities", len(entities))
                else:
                    _LOGGER.warning("Sensmos ingest HTTP %s", resp.status)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Sensmos ingest error: %s", err)
