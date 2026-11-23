"""Sensmos — tryb chmury: HA ↔ BE (/v1/term) tokenem konta (LORA-MESSAGING.md §10).

Sparowana brama LoRaWAN nie ma własnej sesji jak node — jej „mózgiem" jest HA właściciela.
Jedno gniazdo na konto; urządzenia wpisu = bramy, które BE przysyła przy logowaniu i po zmianie.

Komunikatory konta (od 0.13.0): wiadomość z komunikatora przychodzi jako `ldev_msg`, a HA pisze do
komunikatora `ldev_send` (zakres `lora.tx`). Serwer szyfruje ją kluczem urządzenia i nadaje przez
najbliższą bramę albo noda Sensmos, który słyszał komunikator; `ldev_state` mówi, czy doszło.
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


class CloudSendError(Exception):
    """Serwer nie przyjął wiadomości do komunikatora (brak połączenia, limit, nie Twój komunikator)."""


TEXT_MAX_BYTES = 100       # tyle mieści ramka do komunikatora (BE odrzuca dłuższe)
SEND_TIMEOUT_S = 15


def clean_text(text: str) -> str:
    """Tekst do komunikatora: bez znaków sterujących (nowa linia → spacja), najwyżej 100 bajtów UTF-8.

    Ucina po całych znakach, więc polskie litery nie rozpadają się na pół.
    """
    s = "".join(" " if (ord(c) < 32 or 127 <= ord(c) <= 159) else c for c in str(text))
    s = " ".join(s.split())
    out = s.encode("utf-8")
    if len(out) <= TEXT_MAX_BYTES:
        return s
    return out[:TEXT_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


class SensmosCloud:
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_gateways: Callable[[list[dict[str, Any]]], None],
        on_frame: Callable[[dict[str, Any]], dict[str, Any] | None],
        on_stats: Callable[[list[Any]], None],
        on_ldev_msg: Callable[[dict[str, Any]], None] | None = None,
        on_ldev_state: Callable[[dict[str, Any]], None] | None = None,
        on_ldevs: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_gateways = on_gateways
        self._on_frame = on_frame
        self._on_stats = on_stats
        self._on_ldev_msg = on_ldev_msg
        self._on_ldev_state = on_ldev_state
        self._on_ldevs = on_ldevs
        self._ref = 0
        self._pending: dict[str, asyncio.Future] = {}
        self.ldevs: list[dict[str, Any]] = []
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
            self._set_ldevs(m.get("ldevs") or [], notify=False)
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
                if self._closing:
                    await self.close()
                    return
                delay = BACKOFF_MIN_S
                _LOGGER.info("Sensmos cloud: connected, %d gateway(s)", len(self.gateways))
            ws = self._ws
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    try:
                        await self._handle(msg.data)
                    except Exception:   # jedna zła wiadomość nie może zerwać odbioru
                        _LOGGER.exception("Sensmos cloud: message handling failed")
            self._ws = None
            self._fail_pending("connection lost")
            if self._closing:
                return
            if ws.close_code == REVOKED:
                self._reauth("access revoked")
                return
            _LOGGER.info("Sensmos cloud: connection lost (code %s), reconnecting", ws.close_code)
            await asyncio.sleep(BACKOFF_MIN_S)

    async def close(self) -> None:
        self._closing = True
        self._fail_pending("closing")
        if self._ws is not None:
            await self._ws.close()

    def _reauth(self, why: Any) -> None:
        _LOGGER.warning("Sensmos cloud: %s — pair Home Assistant again", why)
        self.entry.async_start_reauth(self.hass)

    async def _handle(self, raw: str) -> None:
        try:
            m = json.loads(raw)
        except ValueError:
            return
        t = m.get("type")
        if t == "lora_frame":
            # Kwit jak od noda-bazy: bez niego BE nie wie, że ramkę odczytano (i nie nalicza odbioru).
            rcpt = self._on_frame(m)
            if rcpt and self._ws is not None and not self._ws.closed:
                await self._ws.send_json(rcpt)
        elif t == "gw_stats":
            self._on_stats(m.get("gateways") or [])
        elif t == "gateways":
            self._set_gateways(m.get("gateways") or [])
        elif t == "ldev_msg":
            self._on_device_message(m)
        elif t == "ldevs":
            self._set_ldevs(m.get("ldevs") or [])
        elif t == "ldev_sent":
            fut = self._pending.pop(str(m.get("ref")), None)
            if fut is not None and not fut.done():
                fut.set_result(m)
        elif t == "ldev_state":
            if self._on_ldev_state is not None and isinstance(m.get("dev"), str):
                self._on_ldev_state({
                    "msg_id": str(m.get("msg_id") or ""),
                    "device": m["dev"][:8].lower(),
                    "state": str(m.get("state") or ""),
                })

    async def send_ldev(self, id8: str, text: str) -> str:
        """Tekst do komunikatora konta. Zwraca msg_id; stan doręczenia przyjdzie później (`ldev_state`)."""
        id8 = str(id8 or "").lower()
        if len(id8) != 8 or any(c not in "0123456789abcdef" for c in id8):
            raise CloudSendError("messenger id must be 8 hex characters")
        body = clean_text(text)
        if not body:
            raise CloudSendError("empty message")
        ws = self._ws
        if ws is None or ws.closed:
            raise CloudSendError("not connected to Sensmos")
        self._ref += 1
        ref = str(self._ref)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[ref] = fut
        try:
            await ws.send_json({"type": "ldev_send", "ref": ref, "id8": id8, "text": body})
            async with asyncio.timeout(SEND_TIMEOUT_S):
                r = await fut
        except TimeoutError as err:
            raise CloudSendError("no answer from Sensmos") from err
        except (aiohttp.ClientError, ConnectionError) as err:
            raise CloudSendError(str(err) or type(err).__name__) from err
        finally:
            self._pending.pop(ref, None)
        if not r.get("ok"):
            raise CloudSendError(str(r.get("error") or "refused"))
        return str(r.get("msg_id") or "")

    def _fail_pending(self, why: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"ok": False, "error": why})
        self._pending.clear()

    def _set_ldevs(self, items: list[Any], notify: bool = True) -> None:
        ldevs = [
            {"id8": str(d["id8"]).lower(), "name": str(d.get("name") or "")[:32]}
            for d in items
            if isinstance(d, dict) and isinstance(d.get("id8"), str) and len(d["id8"]) == 8
        ]
        changed = {d["id8"] for d in ldevs} != {d["id8"] for d in self.ldevs}
        self.ldevs = ldevs
        if notify and changed and self._on_ldevs is not None:
            self._on_ldevs(ldevs)

    def _on_device_message(self, m: dict[str, Any]) -> None:
        """Urządzenie sparowane z kontem (np. komunikator) wysłało tekst — do automatyzacji."""
        dev, text = m.get("dev"), m.get("text")
        if not isinstance(dev, str) or not isinstance(text, str):
            return
        name = m.get("name")
        if self._on_ldev_msg is None:
            return
        self._on_ldev_msg({
            "device": dev[:8].lower(),
            "name": name[:32] if isinstance(name, str) else None,
            "text": text[:200],
            "alert": bool(m.get("alert")),
            "rx": str(m.get("rx") or "")[:8],
            "rssi": m.get("rssi"),
            "snr": m.get("snr"),
            "ts": m.get("ts"),
        })

    def _set_gateways(self, gws: list[Any]) -> None:
        self.gateways = [g for g in gws if isinstance(g, dict) and g.get("device_id")]
        self._on_gateways(self.gateways)
