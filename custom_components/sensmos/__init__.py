"""Sensmos — integracja noda ESP32 z Home Assistant."""
from __future__ import annotations

import hashlib
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo

from .api import SensmosApi, SensmosApiError, SensmosAuthError
from .cloud import CloudAuthError, CloudConnectError, SensmosCloud
from .const import (
    CONF_HOST,
    CONF_KEY,
    CONF_MODE,
    CONF_PIN,
    DATA_PLATFORMS,
    DOMAIN,
    MODE_CLOUD,
    MODE_DATA,
    OPT_FEEDS,
    OPT_WEBHOOK,
    PLATFORMS,
    telemetry_key,
)
from .coordinator import SensmosCoordinator
from .direct import SensmosDirect
from .feeder import Feeder
from .get import SensmosGet
from .webhook import async_remove_node_webhook, async_setup_node_webhook

_LOGGER = logging.getLogger(__name__)

SERVICE_PUSH_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("entity_id"): cv.string,
        vol.Required("value"): cv.string,
        vol.Optional("unit", default=""): cv.string,
    }
)

SERVICE_LORA_SEND_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("dst"): vol.All(cv.string, vol.Match(r"^[0-9a-fA-F]{8}$")),
        vol.Optional("sub", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        vol.Required("payload"): vol.All(cv.string, vol.Length(min=1, max=128)),
        vol.Optional("aes", default=True): cv.boolean,
    }
)


async def _async_setup_data_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tryb 'data' — bez fizycznego noda; wysyłka encji HA na żywą mapę."""
    direct = SensmosDirect(hass, entry)
    await direct.async_start()
    device_id = hashlib.sha256(
        ("sensmos-soft:" + entry.data[CONF_KEY]).encode()
    ).hexdigest()

    # podgląd (GET) encji innych nodów jako sensory HA — długi interwał, nie blokuje setupu
    get = SensmosGet(hass, entry)
    await get.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "direct": direct,
        "get": get,
        "device_id": device_id,
        "mode": MODE_DATA,
    }
    await hass.config_entries.async_forward_entry_setups(entry, DATA_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


@callback
def _sync_gateways(hass: HomeAssistant, entry: ConfigEntry, gateways: list[dict]) -> None:
    """Urządzenia wpisu chmury = bramy konta; brama zdjęta z konta znika z HA."""
    dev_reg = dr.async_get(hass)
    ids = {g["device_id"] for g in gateways}
    for g in gateways:
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, g["device_id"])},
            name=g.get("name") or f"LoRaWAN {g['device_id'][:8]}",
            manufacturer="Sensmos",
            model="LoRaWAN gateway",
            serial_number=g.get("eui") or None,
        )
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not {i[1] for i in device.identifiers if i[0] == DOMAIN} & ids:
            dev_reg.async_remove_device(device.id)


async def _async_setup_cloud_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tryb 'cloud' — konto Sensmos tokenem; HA jest „mózgiem" sparowanych bram (§10)."""
    cloud = SensmosCloud(hass, entry, lambda gws: _sync_gateways(hass, entry, gws))
    try:
        await cloud.connect()
    except CloudAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except CloudConnectError as err:
        raise ConfigEntryNotReady(str(err)) from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"mode": MODE_CLOUD, "cloud": cloud}
    entry.async_create_background_task(hass, cloud.run(), f"{DOMAIN}_cloud_{entry.entry_id}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.data.get(CONF_MODE) == MODE_DATA:
        return await _async_setup_data_entry(hass, entry)
    if entry.data.get(CONF_MODE) == MODE_CLOUD:
        return await _async_setup_cloud_entry(hass, entry)

    api = SensmosApi(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PIN]
    )

    try:
        cfg = await api.config()
    except SensmosAuthError as err:
        raise ConfigEntryAuthFailed("invalid_pin") from err
    except SensmosApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    device_id = cfg.get("device_id", entry.unique_id or "unknown")

    info = {}
    try:
        info = await api.info()
    except SensmosApiError:
        pass

    device_info = DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=entry.title,
        manufacturer="Sensmos",
        model="ESP32 Node",
        sw_version=str(info.get("version") or info.get("firmware") or ""),
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )

    # Pole `lora` w /info = FW ≥ lora9 wykryło SX1262 → encje/zdarzenia/serwis LoRa.
    lora = isinstance(info.get("lora"), dict)
    coordinator = SensmosCoordinator(hass, api, device_id, lora=lora)
    await coordinator.async_config_entry_first_refresh()

    feeder = Feeder(hass, api, entry.options.get(OPT_FEEDS, []))
    feeder.start()

    if entry.options.get(OPT_WEBHOOK, True):
        await async_setup_node_webhook(hass, api, device_id)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "feeder": feeder,
        "device_info": device_info,
        "device_id": device_id,
        "lora": lora,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Auto-sprzątanie duchów: urządzenia TEGO wpisu, których tożsamość nie pasuje do
    # aktualnego device_id (np. node przeflashowany → nowe ID, stary wpis wisiał wyszarzony).
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        idents = {i[1] for i in device.identifiers if i[0] == DOMAIN}
        if idents and device_id not in idents:
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    _register_services(hass)
    return True


# Przycisk „Usuń urządzenie" w UI urządzenia — HA pokazuje go TYLKO gdy integracja
# implementuje tę funkcję. Pozwalamy usuwać wszystko: żywe urządzenie i tak odtworzy
# się przy najbliższym odświeżeniu encji, a duchy znikają na stałe.
async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].get(entry.entry_id)

    if data and data.get("mode") == MODE_CLOUD:
        await data["cloud"].close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True

    # tryb data — sender (direct) + sensory podglądu (DATA_PLATFORMS)
    if data and data.get("mode") == MODE_DATA:
        data["direct"].stop()
        ok = await hass.config_entries.async_unload_platforms(entry, DATA_PLATFORMS)
        if ok:
            hass.data[DOMAIN].pop(entry.entry_id, None)
        return ok

    if data:
        data["feeder"].stop()
        await async_remove_node_webhook(
            hass, None, data["device_id"], clear_node=False
        )
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Opcje zmienione (mapowania/webhook) → przeładuj wpis."""
    await hass.config_entries.async_reload(entry.entry_id)


# ── Serwisy ───────────────────────────────────────────────────


def _entry_data_for_call(hass: HomeAssistant, call: ServiceCall) -> dict:
    """Znajdź dane wpisu po device_id z rejestru HA (albo jedyny wpis)."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise ValueError("Brak skonfigurowanych nodów Sensmos")

    ha_device_id = call.data.get("device_id")
    if not ha_device_id:
        if len(entries) == 1:
            return next(iter(entries.values()))
        raise ValueError("Wiele nodów — wskaż device_id")

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(ha_device_id)
    if device:
        for ident in device.identifiers:
            if ident[0] == DOMAIN:
                for data in entries.values():
                    if data.get("device_id") == ident[1]:
                        return data
    # fallback: potraktuj jako device_id Sensmos
    for data in entries.values():
        if data.get("device_id", "").startswith(ha_device_id):
            return data
    raise ValueError(f"Nie znaleziono noda: {ha_device_id}")


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "push"):
        return

    async def handle_push(call: ServiceCall) -> None:
        entity_id: str = call.data["entity_id"]
        # ta sama bramka co w feederze/config_flow: telemetria noda jest tylko do odczytu
        if telemetry_key(entity_id):
            raise ValueError(
                f"{entity_id}: telemetria noda (tylko do odczytu) — node liczy ją sam"
            )
        data = _entry_data_for_call(hass, call)
        api: SensmosApi = data["api"]
        await api.push_data(
            entity_id, call.data["value"], call.data.get("unit", "")
        )

    hass.services.async_register(
        DOMAIN, "push", handle_push, schema=SERVICE_PUSH_SCHEMA
    )

    async def handle_lora_send(call: ServiceCall) -> None:
        data = _entry_data_for_call(hass, call)
        if not data.get("lora"):
            raise ValueError("Ten node nie ma radia LoRa")
        api: SensmosApi = data["api"]
        await api.lora_send(
            call.data["dst"].lower(),
            call.data["payload"],
            sub=call.data.get("sub", 0),
            aes=call.data.get("aes", True),
        )

    hass.services.async_register(
        DOMAIN, "lora_send", handle_lora_send, schema=SERVICE_LORA_SEND_SCHEMA
    )
