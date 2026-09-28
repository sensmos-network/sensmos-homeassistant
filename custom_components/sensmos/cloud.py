"""Sensmos — tryb chmury: HA ↔ BE (/v1/term) tokenem konta (LORA-MESSAGING.md §10).

Sparowana brama LoRaWAN nie ma własnej sesji jak node — jej „mózgiem" jest HA właściciela.
Jedno gniazdo na konto; urządzenia wpisu = bramy, które BE przysyła przy logowaniu i po zmianie.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BE, CONF_TOKEN

_LOGGER = logging.getLogger(__name__)

REVOKED = 4003          # BE zamyka tym kodem gniazdo, którego token odwołano
BACKOFF_MIN_S = 5
BACKOFF_MAX_S = 300


class CloudAuthError(Exception):
    """Token odwołany, wygasły albo bez zakresu `lora.rx` — trzeba sparować od nowa."""


class CloudConnectError(Exception):
    """BE nieosiągalny albo zerwał w trakcie logowania — próbujemy dalej."""


class SensmosCloud:
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_gateways: Callable[[list[dict[str, Any]]], None],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_gateways = on_gateways
        be = entry.data[CONF_BE].rstrip("/")
        self._url = be.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/term"
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._closing = False
        self.gateways: list[dict[str, Any]] = []

    async def connect(self) -> None:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(15):
                ws = await session.ws_connect(self._url, heartbeat=30)
                await ws.send_json({"type": "auth", "lora": 1, "token": self.entry.data[CONF_TOKEN]})
                msg = await ws.receive()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CloudConnectError(str(err) or type(err).__name__) from err
        m: dict[str, Any] = {}
        if msg.type is aiohttp.WSMsgType.TEXT:
            try:
                m = json.loads(msg.data)
            except ValueError:
                pass
        if m.get("type") == "auth" and m.get("ok") is True:
            self._ws = ws
            self._set_gateways(m.get("gateways") or [])
            return
        await ws.close()
        err = str(m.get("error") or "no auth reply")
        # Odmowa za token to koniec; reszta (błąd bazy, restart BE w trakcie) mija sama.
        if err.startswith("token"):
            raise CloudAuthError(err)
        raise CloudConnectError(err)

    async def run(self) -> None:
        """Czyta gniazdo, po zerwaniu łączy od nowa. Kończy się dopiero przy odwołaniu tokenu."""
        delay = BACKOFF_MIN_S
        while not self._closing:
            if self._ws is None:
                try:
                    await self.connect()
                except CloudAuthError as err:
                    self._reauth(err)
                    return
                except CloudConnectError as err:
                    _LOGGER.debug("Sensmos cloud: %s — retry in %s s", err, delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, BACKOFF_MAX_S)
                    continue
                delay = BACKOFF_MIN_S
                _LOGGER.info("Sensmos cloud: connected, %d gateway(s)", len(self.gateways))
            ws = self._ws
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    self._handle(msg.data)
            self._ws = None
            if self._closing:
                return
            if ws.close_code == REVOKED:
                self._reauth("access revoked")
                return
            _LOGGER.info("Sensmos cloud: connection lost (code %s), reconnecting", ws.close_code)
            await asyncio.sleep(BACKOFF_MIN_S)

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            await self._ws.close()

    def _reauth(self, why: Any) -> None:
        _LOGGER.warning("Sensmos cloud: %s — pair Home Assistant again", why)
        self.entry.async_start_reauth(self.hass)

    def _handle(self, raw: str) -> None:
        try:
            m = json.loads(raw)
        except ValueError:
            return
        if m.get("type") == "gateways":
            self._set_gateways(m.get("gateways") or [])

    def _set_gateways(self, gws: list[Any]) -> None:
        self.gateways = [g for g in gws if isinstance(g, dict) and g.get("device_id")]
        self._on_gateways(self.gateways)
