"""Sensmos — koordynator odpytujący node."""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import lora_frame
from .api import SensmosApi, SensmosApiError
from .const import (
    AVAIL_GRACE_S,
    DOMAIN,
    EVENT_LORA_CMD,
    EVENT_LORA_FRAME,
    GW_STATS_GRACE_S,
    OPT_GATEWAYS,
    SCAN_INTERVAL_S,
    SLOW_EVERY_N_CYCLES,
    telemetry_key,
)

_LOGGER = logging.getLogger(__name__)


class LoraMixin:
    """LoRa wspólna dla noda i bramy (tryb cloud): zdarzenia z nowych wpisów inboxu i odczyt ringu."""

    hass: HomeAssistant
    config_entry: ConfigEntry | None
    device_id: str
    data: dict[str, Any] | None
    _lora_seen: set[tuple] | None

    def _fire_new(self, inbox: dict[str, Any]) -> None:
        cmds = (inbox.get("cmds") or {}).get("items") or []
        frames = (inbox.get("frames") or {}).get("items") or []
        keys: set[tuple] = set()
        for c in cmds:
            keys.add(("cmd", c.get("ts"), c.get("payload")))
        for f in frames:
            keys.add(("frame", f.get("ts"), f.get("sub"), f.get("text") or f.get("hex")))
        if self._lora_seen is None:
            self._lora_seen = keys
            return
        # device_id = id urządzenia w rejestrze HA (dziennik „Aktywność" filtruje po nim),
        # node_id = device_id Sensmos. Każdy nowy wpis = osobne zdarzenie — także gdy kilka
        # ramek przyszło w jednym oknie pollu (sensor pokazuje wtedy tylko ostatnią).
        base = {"device_id": self._ha_device_id(), "node_id": self.device_id}
        for c in cmds:
            if ("cmd", c.get("ts"), c.get("payload")) not in self._lora_seen:
                self.hass.bus.async_fire(
                    EVENT_LORA_CMD, {**base, "cmd": c.get("payload"), "ts": c.get("ts")}
                )
        for f in frames:
            key = ("frame", f.get("ts"), f.get("sub"), f.get("text") or f.get("hex"))
            if key not in self._lora_seen:
                self.hass.bus.async_fire(
                    EVENT_LORA_FRAME,
                    {
                        **base,
                        "sub": f.get("sub", 0),
                        "text": f.get("text"),
                        "hex": f.get("hex"),
                        "enc": bool(f.get("enc")),
                        "via": f.get("via"),
                        "ts": f.get("ts"),
                    },
                )
        self._lora_seen = keys

    def _ha_device_id(self) -> str | None:
        # Wśród urządzeń TEGO wpisu: od HA 2026.8 identyfikator nie jest unikalny między wpisami,
        # async_get_device(identifiers=…) jest wycofane (ostrzeżenie), a w 2027.8 przestaje działać.
        if self.config_entry is None:
            return None
        for dev in dr.async_entries_for_config_entry(dr.async_get(self.hass), self.config_entry.entry_id):
            if (DOMAIN, self.device_id) in dev.identifiers:
                return dev.id
        return None

    @property
    def lora_emerg(self) -> dict[str, Any]:
        return (self.data or {}).get("lora", {}).get("emerg") or {}

    @property
    def lora_cmds(self) -> list[dict[str, Any]]:
        inbox = (self.data or {}).get("lora", {}).get("inbox") or {}
        return (inbox.get("cmds") or {}).get("items") or []

    @property
    def lora_frames(self) -> list[dict[str, Any]]:
        inbox = (self.data or {}).get("lora", {}).get("inbox") or {}
        return (inbox.get("frames") or {}).get("items") or []



class SensmosCoordinator(LoraMixin, DataUpdateCoordinator[dict[str, Any]]):
    """Status co 30 s; config/native co N cykli."""

    def __init__(
        self, hass: HomeAssistant, api: SensmosApi, device_id: str, lora: bool = False
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_id[:8]}",
            update_interval=timedelta(seconds=SCAN_INTERVAL_S),
        )
        self.api = api
        self.device_id = device_id
        self.lora = lora   # /info niesie pole `lora` → node ma radio (FW ≥ lora9)
        self._cycle = 0
        self._slow: dict[str, Any] = {"config": {}, "native": [], "lora_info": {}}
        self._last_ok = 0.0   # monotonic ostatniego udanego /data/status
        self._lora: dict[str, Any] = {"inbox": {}, "emerg": {}}
        # Zdarzenia tylko dla NOWYCH wpisów inboxu: pierwszy poll = baseline (historia sprzed
        # startu HA nie strzela automatyzacjami). Klucz (ts, treść) — ring noda jest mały
        # (cmds 8 / frames 6), więc zbiór nie rośnie.
        self._lora_seen: set[tuple] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.api.data_status()
        except SensmosApiError as err:
            raise UpdateFailed(str(err)) from err
        self._last_ok = time.monotonic()

        if self.lora:
            await self._poll_lora()

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
            if self.lora:
                try:
                    info = await self.api.info()
                    self._slow["lora_info"] = info.get("lora") or {}
                except SensmosApiError as err:
                    _LOGGER.debug("Slow fetch info failed: %s", err)
        self._cycle += 1

        return {"status": status, "lora": self._lora, **self._slow}

    # ── LoRa: inbox (komendy awaryjne + ramki DATA) i stan trybu awaryjnego ──
    # Odpytujemy API noda w tym samym cyklu co /data/status — CELOWO bez webhooka:
    # webhook LoRa na nodzie to slot usera (np. UniFi), integracja nie ma prawa go zabrać.
    async def _poll_lora(self) -> None:
        try:
            inbox = await self.api.lora_inbox()
        except SensmosApiError as err:
            _LOGGER.debug("LoRa inbox fetch failed: %s", err)
            return
        self._lora["inbox"] = inbox
        try:
            self._lora["emerg"] = await self.api.lora_emerg()
        except SensmosApiError as err:
            _LOGGER.debug("LoRa emerg fetch failed: %s", err)
        self._fire_new(inbox)

    @property
    def lora_info(self) -> dict[str, Any]:
        return (self.data or {}).get("lora_info") or {}

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


class GatewayCoordinator(LoraMixin, DataUpdateCoordinator[dict[str, Any]]):
    """Brama LoRaWAN w trybie cloud — bez pollingu: ramki przychodzą z BE po WS, HA dekoduje sam.

    Ring 6 wpisów i powtórka jak w FW. Ring leży też na dysku, bo po restarcie HA nie ma skąd go
    wziąć (node trzyma swój u siebie) — bez tego encje ramek wracałyby dopiero z kolejną ramką.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        gw: dict[str, Any],
        saved: dict[str, Any] | None,
        on_change: Callable[[], None],
    ) -> None:
        # Interwał tylko po to, żeby encje co minutę przeliczyły dostępność: bez niego po ciszy
        # z BE diagnostyka wisiałaby z ostatnią wartością zamiast przejść na „niedostępne".
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_gw_{gw['device_id'][:8]}",
            update_interval=timedelta(seconds=60),
        )
        self.device_id: str = gw["device_id"]
        self._on_change = on_change
        saved = saved or {}
        self._ring: deque[dict[str, Any]] = deque(
            (i for i in saved.get("ring") or [] if isinstance(i, dict)), maxlen=lora_frame.RING
        )
        dup = saved.get("dup") or [0, 0]
        self._dup: tuple[int, int] = (int(dup[0]), int(dup[1]))
        self.stats: dict[str, Any] = {}
        self._stats_at = 0.0
        self.last_beacon: datetime | None = None
        self._lora_seen = None
        self.data = self._snapshot()
        self._fire_new(self.data["lora"]["inbox"])   # linia bazowa: zapisane ramki nie strzelają

    async def _async_update_data(self) -> dict[str, Any]:
        return self.data

    def _snapshot(self) -> dict[str, Any]:
        items = list(self._ring)
        return {
            "lora": {
                "inbox": {
                    "cmds": {"count": 0, "items": []},
                    "frames": {"count": len(items), "items": items},
                },
                "emerg": {},
            }
        }

    @callback
    def on_frame(self, m: dict[str, Any]) -> dict[str, Any] | None:
        """Ramka z BE → dekod → ring/zdarzenie/encje; zwraca kwit do odesłania albo None."""
        o = (self.config_entry.options.get(OPT_GATEWAYS) or {}).get(self.device_id) or {}
        key = bytes.fromhex(o["key"]) if o.get("key") else None
        fr = lora_frame.decode(
            str(m.get("hex") or "").lower(), self.device_id, key, bool(o.get("open"))
        )
        if fr is None:
            return None
        ts = m.get("ts")
        if not isinstance(ts, int) or ts <= 0:
            ts = int(time.time())
        # FW: jeden slot — ta sama suma w oknie 120 s to powtórka (inny nonce, ta sama treść)
        if fr.crc == self._dup[0] and ((ts - self._dup[1]) & 0xFFFFFFFF) < lora_frame.DEDUP_S:
            return None
        self._dup = (fr.crc, ts)
        via = "rf" if str(m.get("rx") or "").lower() == self.device_id[:8] else "ws"
        self._ring.append(lora_frame.ring_item(fr, ts, via))
        snap = self._snapshot()
        self._fire_new(snap["lora"]["inbox"])
        self.async_set_updated_data(snap)
        self._on_change()
        return {
            "type": "lora_data_rcpt",
            "gw": self.device_id,
            "sub": fr.sub,
            "via": via,
            "len": len(fr.payload),
            "enc": fr.enc,
            "fh": fr.fh,
        }

    @callback
    def set_stats(self, s: dict[str, Any]) -> None:
        self.stats = s
        self._stats_at = time.monotonic()
        lb = s.get("last_beacon_s")
        if isinstance(lb, (int, float)) and not isinstance(lb, bool):
            new = dt_util.utcnow() - timedelta(seconds=int(lb))
            # BE podaje „ile sekund temu" — przeliczenie co minutę drga o sekundę; bez tego
            # stan zmieniałby się przy każdej paczce i zaśmiecał historię.
            if self.last_beacon is None or abs((new - self.last_beacon).total_seconds()) > 5:
                self.last_beacon = new
        else:
            self.last_beacon = None
        self.async_update_listeners()

    @property
    def stats_fresh(self) -> bool:
        return self._stats_at > 0 and time.monotonic() - self._stats_at < GW_STATS_GRACE_S

    def dump(self) -> dict[str, Any]:
        return {"ring": list(self._ring), "dup": list(self._dup)}
