"""Sensmos — koordynator odpytujący node."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SensmosApi, SensmosApiError
from .const import (
    AVAIL_GRACE_S,
    DOMAIN,
    SCAN_INTERVAL_S,
    SLOW_EVERY_N_CYCLES,
    telemetry_key,
)

_LOGGER = logging.getLogger(__name__)


class SensmosCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Status co 30 s; config/native co N cykli."""

    def __init__(
        self, hass: HomeAssistant, api: SensmosApi, device_id: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_id[:8]}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_S),
        )
        self.api = api
        self.device_id = device_id
        self._cycle = 0
        self._slow: dict[str, Any] = {"config": {}, "native": []}
        self._last_ok = 0.0   # monotonic ostatniego udanego /data/status

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.api.data_status()
        except SensmosApiError as err:
            raise UpdateFailed(str(err)) from err
        self._last_ok = time.monotonic()

        if self._cycle % SLOW_EVERY_N_CYCLES == 0:
            try:
                self._slow["config"] = await self.api.config()
            except SensmosApiError as err:
                _LOGGER.debug("Slow fetch config failed: %s", err)
            try:
                native = await self.api.data_native()
                self._slow["native"] = native.get("entities", [])
            except SensmosApiError as err:
                _LOGGER.debug("Slow fetch native failed: %s", err)
        self._cycle += 1

        return {"status": status, **self._slow}

    @property
    def entities_alive(self) -> bool:
        """Encje dostępne, gdy ostatni poll się udał LUB jesteśmy w oknie grace po nim.

        Chroni przed migotaniem: pojedynczy timeout /data/status nie zdejmuje wszystkich
        sensorów, bo HA i tak trzyma ostatni dobry snapshot (wartości nadal świeże).
        """
        if self.last_update_success:
            return True
        return (time.monotonic() - self._last_ok) < AVAIL_GRACE_S

    @property
    def pool_entities(self) -> list[dict[str, Any]]:
        if not self.data:
            return []
        return self.data["status"].get("pool", []) or []

    @property
    def node_entities(self) -> list[dict[str, Any]]:
        """Własne encje noda: pub.* (natywne) + own.* (niestandardowe), BEZ telemetrii.

        Telemetria ma osobne sensory diagnostyczne (mon_entities) — gdyby została też tutaj,
        powstałby drugi komplet encji na te same wartości. Odwrotnie też: mon.<klucz spoza
        MON_KEYS> (ktoś wepchnął go POST-em /data) telemetrią NIE jest i ma zostać zwykłą
        encją — inaczej zniknąłby bez śladu.
        """
        if not self.data:
            return []
        status = self.data.get("status") or {}
        src = (
            (status.get("pub") or [])
            + (status.get("own") or [])
            + (status.get("mon") or [])
        )
        return [e for e in src if not telemetry_key(e.get("entity_id", ""))]

    @property
    def mon_entities(self) -> list[dict[str, Any]]:
        """Telemetria noda (WiFi/NET/uptime) — niezależnie od wersji FW.

        FW ≥0.75: własna tablica status["mon"] (mon.<klucz>).
        Starsze FW: te same encje siedzą w status["pub"] jako pub.<klucz> — bierzemy je
        po zamkniętej liście MON_KEYS, więc flota bez OTA daje dokładnie te same sensory.
        """
        if not self.data:
            return []
        status = self.data.get("status") or {}
        out = [e for e in (status.get("mon") or []) if telemetry_key(e.get("entity_id", ""))]
        seen = {telemetry_key(e.get("entity_id", "")) for e in out}
        for ent in status.get("pub") or []:
            key = telemetry_key(ent.get("entity_id", ""))
            if key and key not in seen:
                out.append(ent)
        return out

    @property
    def native_entities(self) -> list[dict[str, Any]]:
        if not self.data:
            return []
        return self.data.get("native", []) or []
