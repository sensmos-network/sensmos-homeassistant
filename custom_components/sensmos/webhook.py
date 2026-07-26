"""Sensmos — zdarzenia noda jako eventy HA.

Rejestruje webhook HA i ustawia go jako integration_url noda.
Node POST-uje {device_id, action, uptime_s, data} przy zdarzeniach:
message_received, batch_sent, sub_received, ws_connected.

Eventy na busie HA:
- sensmos_event   — każde zdarzenie
- sensmos_message — dodatkowo dla action == message_received
"""
from __future__ import annotations

import logging

from aiohttp.web import Request, Response
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import SensmosApi, SensmosApiError
from .const import DOMAIN, EVENT_MESSAGE, EVENT_NODE

_LOGGER = logging.getLogger(__name__)


def _webhook_id(device_id: str) -> str:
    return f"{DOMAIN}_{device_id[:16]}"


async def async_setup_node_webhook(
    hass: HomeAssistant, api: SensmosApi, device_id: str
) -> str | None:
    """Zarejestruj webhook + wpisz URL do noda. Zwraca webhook_id albo None."""
    wh_id = _webhook_id(device_id)

    async def handle(hass: HomeAssistant, webhook_id: str, request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError:
            return Response(status=400)
        action = payload.get("action", "")
        data = payload.get("data")
        hass.bus.async_fire(
            EVENT_NODE,
            {"device_id": payload.get("device_id", device_id), "action": action, "data": data},
        )
        if action == "message_received":
            msg = data if isinstance(data, dict) else {"raw": data}
            hass.bus.async_fire(
                EVENT_MESSAGE, {"device_id": payload.get("device_id", device_id), **msg}
            )
        return Response(status=200)

    try:
        webhook.async_register(hass, DOMAIN, f"Sensmos {device_id[:8]}", wh_id, handle)
    except ValueError:
        pass  # już zarejestrowany (reload)

    try:
        base = get_url(hass, prefer_external=False, allow_internal=True, allow_ip=True)
    except NoURLAvailableError:
        _LOGGER.warning("No HA instance URL — event webhook not set on the node")
        return wh_id

    url = f"{base}{webhook.async_generate_path(wh_id)}"
    try:
        await api.set_integration_url(url)
        _LOGGER.info("integration_url noda %s → %s", device_id[:8], url)
    except SensmosApiError as err:
        _LOGGER.warning("Failed to set integration_url: %s", err)
    return wh_id


async def async_remove_node_webhook(
    hass: HomeAssistant, api: SensmosApi | None, device_id: str, clear_node: bool
) -> None:
    """Wyrejestruj webhook; opcjonalnie wyczyść integration_url na nodzie."""
    webhook.async_unregister(hass, _webhook_id(device_id))
    if clear_node and api is not None:
        try:
            await api.set_integration_url("")
        except SensmosApiError:
            pass
