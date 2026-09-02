"""Sensmos — asynchroniczny klient HTTP noda."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class SensmosApiError(Exception):
    """Błąd komunikacji z nodem."""


class SensmosAuthError(SensmosApiError):
    """Błędny PIN."""


class SensmosApi:
    """Klient API noda Sensmos (HTTP, Bearer PIN)."""

    def __init__(self, session: aiohttp.ClientSession, host: str, pin: str) -> None:
        self._session = session
        self._base = f"http://{host}"
        self._pin = pin

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._pin}",
        }

    async def _request(
        self, method: str, path: str, json: dict | None = None, timeout: float = 8
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                resp = await self._session.request(
                    method, f"{self._base}{path}", headers=self._headers, json=json
                )
                if resp.status == 403:
                    raise SensmosAuthError("invalid_pin")
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise SensmosApiError(
                        body.get("error", f"HTTP {resp.status}")
                        if isinstance(body, dict)
                        else f"HTTP {resp.status}"
                    )
                return body if isinstance(body, dict) else {}
        except (TimeoutError, aiohttp.ClientError) as err:
            raise SensmosApiError(f"connection: {err}") from err

    # ── Odczyty ───────────────────────────────────────────────

    async def info(self) -> dict[str, Any]:
        """GET /info — bez auth (device_id, city, version)."""
        try:
            async with asyncio.timeout(5):
                resp = await self._session.get(f"{self._base}/info")
                return await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise SensmosApiError(f"connection: {err}") from err

    async def config(self) -> dict[str, Any]:
        return await self._request("GET", "/config")

    async def data_status(self) -> dict[str, Any]:
        return await self._request("GET", "/data/status")

    async def data_native(self) -> dict[str, Any]:
        return await self._request("GET", "/data/native")

    async def remote_available(self, esp_id: str) -> dict[str, Any]:
        # 0.73: katalog czytamy z BE wprost (publiczne /v1/data/available), nie z noda —
        # /remote/available na nodzie skasowane (było proxy tego samego publicznego odczytu).
        from .const import BE_AVAILABLE_URL
        try:
            async with asyncio.timeout(12):
                resp = await self._session.get(f"{BE_AVAILABLE_URL}{esp_id}")
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise SensmosApiError(
                        body.get("error", f"HTTP {resp.status}")
                        if isinstance(body, dict)
                        else f"HTTP {resp.status}"
                    )
                return body if isinstance(body, dict) else {}
        except (TimeoutError, aiohttp.ClientError) as err:
            raise SensmosApiError(f"connection: {err}") from err

    # ── LoRa (FW ≥ lora9; tylko nody, których /info ma pole `lora`) ──

    async def lora_inbox(self) -> dict[str, Any]:
        """GET /lora/inbox — {cmds:{items:[{ts,payload}]}, frames:{items:[{ts,sub,enc,via,text|hex}]}}."""
        return await self._request("GET", "/lora/inbox")

    async def lora_emerg(self) -> dict[str, Any]:
        """GET /node/lora_emerg — {eids:[...], active:bool, webhook, webhook_get}."""
        return await self._request("GET", "/node/lora_emerg")

    async def lora_send(
        self, dst: str, payload: str, sub: int = 0, aes: bool = True
    ) -> dict[str, Any]:
        """POST /node/lorasend — nadanie ramki DATA własnym radiem noda (ryczałt SEND)."""
        return await self._request(
            "POST",
            "/node/lorasend",
            {"dst": dst, "sub": sub, "payload": payload, "aes": aes},
        )

    # ── Zapisy ────────────────────────────────────────────────

    async def push_data(self, entity_id: str, value: str, unit: str = "") -> None:
        await self._request(
            "POST", "/data", {"entity_id": entity_id, "value": value, "unit": unit}
        )

    async def set_integration_url(self, url: str) -> None:
        await self._request("POST", "/config", {"integration_url": url})

    async def subscribe(self, esp_id: str, days: int, prefix: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/remote/subscribe",
            {"esp_id": esp_id, "days": days, "prefix": prefix},
            timeout=15,
        )
