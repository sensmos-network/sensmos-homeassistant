"""Sensmos — config flow + options flow (mapowania, subskrypcje)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SensmosApi, SensmosApiError, SensmosAuthError
from .const import (
    BE_GET_URL,
    CONF_HOST,
    CONF_KEY,
    CONF_LABEL,
    CONF_LAT,
    CONF_LON,
    CONF_MODE,
    CONF_PIN,
    DATA_DEFAULT_INTERVAL,
    DATA_MIN_INTERVAL,
    DATA_MIN_KEY_LEN,
    DOMAIN,
    GET_DEFAULT_INTERVAL,
    GET_MIN_INTERVAL,
    MODE_DATA,
    MODE_NODE,
    OPT_FEEDS,
    OPT_GET_INTERVAL,
    OPT_GETS,
    OPT_MAPPINGS,
    OPT_PUSH_INTERVAL,
    OPT_WEBHOOK,
    RESERVED_PREFIXES,
    telemetry_key,
)
from .units import device_classes_for_unit

_ENTITY_RE = re.compile(r"^(pub|own)\.[a-z0-9_]+$")

_SLUG = re.compile(r"[^a-z0-9_]+")


def _slugify(name: str) -> str:
    return _SLUG.sub("_", name.strip().lower()).strip("_")[:28]


class SensmosConfigFlow(ConfigFlow, domain=DOMAIN):
    """Dodanie noda: host + PIN."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> Any:
        # Wybór drogi: fizyczny node (host+PIN) albo same dane (passkey → mapa).
        return self.async_show_menu(step_id="user", menu_options=["node", "data"])

    async def async_step_node(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip().replace("http://", "").rstrip("/")
            pin = user_input[CONF_PIN].strip()
            api = SensmosApi(async_get_clientsession(self.hass), host, pin)
            try:
                cfg = await api.config()
                device_id = cfg.get("device_id", "")
                if not device_id:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(device_id)
                    self._abort_if_unique_id_configured()
                    info = {}
                    try:
                        info = await api.info()
                    except SensmosApiError:
                        pass
                    title = info.get("city") or f"Node {device_id[:8]}"
                    return self.async_create_entry(
                        title=f"Sensmos {title}",
                        data={CONF_MODE: MODE_NODE, CONF_HOST: host, CONF_PIN: pin},
                        options={OPT_FEEDS: [], OPT_WEBHOOK: True},
                    )
            except SensmosAuthError:
                errors["base"] = "invalid_pin"
            except SensmosApiError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="node",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): selector.TextSelector(),
                    vol.Required(CONF_PIN): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_data(self, user_input=None) -> Any:
        """Tryb 'data' — bez sprzętu: passkey + (opcjonalnie) lokalizacja/etykieta."""
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_KEY].strip()
            if len(key) < DATA_MIN_KEY_LEN:
                errors[CONF_KEY] = "key_too_short"
            else:
                device_id = hashlib.sha256(
                    ("sensmos-soft:" + key).encode()
                ).hexdigest()
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                label = (user_input.get(CONF_LABEL) or "").strip()
                opts: dict[str, Any] = {
                    OPT_MAPPINGS: [],
                    OPT_PUSH_INTERVAL: DATA_DEFAULT_INTERVAL,
                }
                if label:
                    opts[CONF_LABEL] = label
                lat, lon = user_input.get(CONF_LAT), user_input.get(CONF_LON)
                if lat is not None and lon is not None:
                    opts[CONF_LAT] = lat
                    opts[CONF_LON] = lon
                return self.async_create_entry(
                    title=f"Sensmos {label or 'data'}",
                    data={CONF_MODE: MODE_DATA, CONF_KEY: key},
                    options=opts,
                )

        return self.async_show_form(
            step_id="data",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional(CONF_LABEL): selector.TextSelector(),
                    vol.Optional(CONF_LAT): vol.Coerce(float),
                    vol.Optional(CONF_LON): vol.Coerce(float),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> SensmosOptionsFlow:
        return SensmosOptionsFlow(entry)


class SensmosOptionsFlow(OptionsFlow):
    """Menu: karmienie pub/own, usuwanie, subskrypcja, ustawienia."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._pending_pub: dict[str, Any] = {}
        self._pending_sub: dict[str, Any] = {}

    # ── helpers ───────────────────────────────────────────────

    def _api(self) -> SensmosApi:
        return SensmosApi(
            async_get_clientsession(self.hass),
            self._entry.data[CONF_HOST],
            self._entry.data[CONF_PIN],
        )

    def _feeds(self) -> list[dict[str, Any]]:
        return list(self._entry.options.get(OPT_FEEDS, []))

    def _save_feeds(self, feeds: list[dict[str, Any]]) -> Any:
        return self.async_create_entry(
            data={**self._entry.options, OPT_FEEDS: feeds}
        )

    # ── menu ──────────────────────────────────────────────────

    async def async_step_init(self, user_input=None) -> Any:
        if self._entry.data.get(CONF_MODE) == MODE_DATA:
            return self.async_show_menu(
                step_id="init",
                menu_options=[
                    "data_add",
                    "data_remove",
                    "get_add",
                    "get_remove",
                    "data_settings",
                ],
            )
        return self.async_show_menu(
            step_id="init",
            menu_options=["feed_pub", "feed_own", "feed_remove", "subscribe", "settings"],
        )

    # ── tryb data: mapowania encja HA → encja Sensmos ─────────

    async def async_step_data_add(self, user_input=None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            entity = user_input["entity"].strip().lower()
            if not _ENTITY_RE.match(entity):
                errors["entity"] = "invalid_entity"
            else:
                maps = list(self._entry.options.get(OPT_MAPPINGS, []))
                maps = [
                    m for m in maps
                    if not (m["ha_entity"] == user_input["ha_entity"] and m["entity"] == entity)
                ]
                maps.append({"ha_entity": user_input["ha_entity"], "entity": entity})
                return self.async_create_entry(
                    data={**self._entry.options, OPT_MAPPINGS: maps}
                )

        return self.async_show_form(
            step_id="data_add",
            data_schema=vol.Schema(
                {
                    vol.Required("ha_entity"): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            filter=selector.EntityFilterSelectorConfig(
                                domain=["sensor", "binary_sensor"]
                            )
                        )
                    ),
                    vol.Required("entity"): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_data_remove(self, user_input=None) -> Any:
        maps = list(self._entry.options.get(OPT_MAPPINGS, []))
        if not maps:
            return self.async_abort(reason="no_feeds")
        if user_input is not None:
            rm = set(user_input["remove"])
            keep = [m for m in maps if f"{m['ha_entity']}|{m['entity']}" not in rm]
            return self.async_create_entry(
                data={**self._entry.options, OPT_MAPPINGS: keep}
            )
        options = [
            selector.SelectOptionDict(
                value=f"{m['ha_entity']}|{m['entity']}",
                label=f"{m['entity']}  ←  {m['ha_entity']}",
            )
            for m in maps
        ]
        return self.async_show_form(
            step_id="data_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("remove"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # ── tryb data: podgląd (GET) innych nodów ─────────────────

    async def async_step_get_add(self, user_input=None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            did = user_input["device_id"].strip().lower()
            prefix = _slugify(user_input.get("prefix") or "node") or "node"
            if prefix in RESERVED_PREFIXES:
                prefix = "node"
            if not re.fullmatch(r"[0-9a-f]{64}", did):
                errors["device_id"] = "invalid_device_id"
            else:
                session = async_get_clientsession(self.hass)
                ok = False
                try:
                    async with session.get(
                        BE_GET_URL + did, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        ok = resp.status == 200
                except (aiohttp.ClientError, TimeoutError):
                    ok = False
                if not ok:
                    errors["base"] = "target_unavailable"
                else:
                    gets = [
                        g
                        for g in self._entry.options.get(OPT_GETS, [])
                        if g["device_id"] != did
                    ]
                    gets.append({"device_id": did, "prefix": prefix})
                    return self.async_create_entry(
                        data={**self._entry.options, OPT_GETS: gets}
                    )

        return self.async_show_form(
            step_id="get_add",
            data_schema=vol.Schema(
                {
                    vol.Required("device_id"): selector.TextSelector(),
                    vol.Required("prefix", default="node"): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_get_remove(self, user_input=None) -> Any:
        gets = list(self._entry.options.get(OPT_GETS, []))
        if not gets:
            return self.async_abort(reason="no_gets")
        if user_input is not None:
            rm = set(user_input["remove"])
            keep = [g for g in gets if g["device_id"] not in rm]
            return self.async_create_entry(
                data={**self._entry.options, OPT_GETS: keep}
            )
        options = [
            selector.SelectOptionDict(
                value=g["device_id"],
                label=f"{g.get('prefix', 'node')}  ←  {g['device_id'][:12]}…",
            )
            for g in gets
        ]
        return self.async_show_form(
            step_id="get_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("remove"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_data_settings(self, user_input=None) -> Any:
        o = self._entry.options
        if user_input is not None:
            changes = {
                **o,
                OPT_PUSH_INTERVAL: int(user_input[OPT_PUSH_INTERVAL]),
                OPT_GET_INTERVAL: int(user_input[OPT_GET_INTERVAL]),
            }
            label = (user_input.get(CONF_LABEL) or "").strip()
            changes[CONF_LABEL] = label if label else None
            lat, lon = user_input.get(CONF_LAT), user_input.get(CONF_LON)
            if lat is not None and lon is not None:
                changes[CONF_LAT], changes[CONF_LON] = lat, lon
            else:
                changes.pop(CONF_LAT, None)
                changes.pop(CONF_LON, None)
            return self.async_create_entry(data=changes)

        return self.async_show_form(
            step_id="data_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_PUSH_INTERVAL,
                        default=o.get(OPT_PUSH_INTERVAL, DATA_DEFAULT_INTERVAL),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=DATA_MIN_INTERVAL, max=3600, step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        OPT_GET_INTERVAL,
                        default=o.get(OPT_GET_INTERVAL, GET_DEFAULT_INTERVAL),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=GET_MIN_INTERVAL, max=3600, step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_LABEL, description={"suggested_value": o.get(CONF_LABEL)}
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_LAT, description={"suggested_value": o.get(CONF_LAT)}
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_LON, description={"suggested_value": o.get(CONF_LON)}
                    ): vol.Coerce(float),
                }
            ),
        )

    # ── karmienie: encja natywna (pub.*) ──────────────────────

    async def async_step_feed_pub(self, user_input=None) -> Any:
        api = self._api()
        try:
            native = (await api.data_native()).get("entities", [])
        except SensmosApiError:
            return self.async_abort(reason="cannot_connect")

        mapped = {f["node_entity"] for f in self._feeds()}
        options = []
        for ent in native:
            pub_id = ent.get("pub_id") or f"pub.{ent['entity_id']}"
            # telemetria noda (mon.*/pub.<klucz NET>) nie jest celem karmienia — node liczy ją sam
            if pub_id in mapped or telemetry_key(pub_id):
                continue
            unit = ent.get("unit") or ""
            label = f"{pub_id}  [{unit}]" if unit else pub_id
            options.append(selector.SelectOptionDict(value=f"{pub_id}|{unit}", label=label))

        if not options:
            return self.async_abort(reason="no_native_left")

        if user_input is not None:
            pub_id, _, unit = user_input["node_entity"].partition("|")
            self._pending_pub = {"node_entity": pub_id, "unit": unit}
            return await self.async_step_feed_pub_entity()

        return self.async_show_form(
            step_id="feed_pub",
            data_schema=vol.Schema(
                {
                    vol.Required("node_entity"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_feed_pub_entity(self, user_input=None) -> Any:
        unit = self._pending_pub.get("unit", "")
        if user_input is not None:
            feeds = self._feeds()
            feeds.append(
                {
                    "node_entity": self._pending_pub["node_entity"],
                    "ha_entity": user_input["ha_entity"],
                    "unit": unit,
                }
            )
            return self._save_feeds(feeds)

        dev_classes = device_classes_for_unit(unit)
        ent_filter = (
            selector.EntityFilterSelectorConfig(domain="sensor", device_class=dev_classes)
            if dev_classes
            else selector.EntityFilterSelectorConfig(domain=["sensor", "binary_sensor"])
        )
        return self.async_show_form(
            step_id="feed_pub_entity",
            data_schema=vol.Schema(
                {
                    vol.Required("ha_entity"): selector.EntitySelector(
                        selector.EntitySelectorConfig(filter=ent_filter)
                    )
                }
            ),
            description_placeholders={
                "node_entity": self._pending_pub["node_entity"],
                "unit": unit or "—",
            },
        )

    # ── karmienie: własna encja (own.*) ───────────────────────

    async def async_step_feed_own(self, user_input=None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = _slugify(user_input["name"])
            if not name:
                errors["name"] = "invalid_name"
            else:
                node_entity = f"own.{name}"
                feeds = self._feeds()
                if any(f["node_entity"] == node_entity for f in feeds):
                    errors["name"] = "already_mapped"
                else:
                    feeds.append(
                        {
                            "node_entity": node_entity,
                            "ha_entity": user_input["ha_entity"],
                            "unit": "",  # jednostka przejmowana z encji HA
                        }
                    )
                    return self._save_feeds(feeds)

        return self.async_show_form(
            step_id="feed_own",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): selector.TextSelector(),
                    vol.Required("ha_entity"): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            filter=selector.EntityFilterSelectorConfig(
                                domain=["sensor", "binary_sensor"]
                            )
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ── usuwanie mapowań ──────────────────────────────────────

    async def async_step_feed_remove(self, user_input=None) -> Any:
        feeds = self._feeds()
        if not feeds:
            return self.async_abort(reason="no_feeds")

        if user_input is not None:
            keep = [
                f for f in feeds if f["node_entity"] not in user_input["remove"]
            ]
            return self._save_feeds(keep)

        options = [
            selector.SelectOptionDict(
                value=f["node_entity"],
                label=f"{f['node_entity']}  ←  {f['ha_entity']}",
            )
            for f in feeds
        ]
        return self.async_show_form(
            step_id="feed_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("remove"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # ── subskrypcja innego noda ───────────────────────────────

    async def async_step_subscribe(self, user_input=None) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            esp_id = user_input["esp_id"].strip()
            prefix = _slugify(user_input.get("prefix") or "sub") or "sub"
            if prefix in RESERVED_PREFIXES:
                prefix = "sub"   # zarezerwowane przez FW (bufory pub/own/tmp/mon)
            days = int(user_input.get("days", 7))
            api = self._api()
            try:
                avail = await api.remote_available(esp_id)
            except SensmosApiError:
                errors["base"] = "target_unavailable"
            else:
                pub = avail.get("pub", []) or []
                listing = "\n".join(
                    f"• {e.get('entity_id', '?')}"
                    + (f" [{e['unit']}]" if e.get("unit") else "")
                    for e in pub[:20]
                ) or "—"
                self._pending_sub = {
                    "esp_id": esp_id,
                    "prefix": prefix,
                    "days": days,
                    "listing": listing,
                    "count": len(pub),
                }
                return await self.async_step_subscribe_confirm()

        return self.async_show_form(
            step_id="subscribe",
            data_schema=vol.Schema(
                {
                    vol.Required("esp_id"): selector.TextSelector(),
                    vol.Required("prefix", default="sub"): selector.TextSelector(),
                    vol.Required("days", default=7): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=30, step=1)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_subscribe_confirm(self, user_input=None) -> Any:
        p = self._pending_sub
        if user_input is not None:
            api = self._api()
            try:
                await api.subscribe(p["esp_id"], p["days"], p["prefix"])
            except SensmosApiError as err:
                if "insufficient" in str(err):
                    return self.async_abort(reason="insufficient_balance")
                return self.async_abort(reason="subscribe_failed")
            return self.async_abort(reason="subscribe_ok")

        return self.async_show_form(
            step_id="subscribe_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "esp_id": p["esp_id"][:16] + "…",
                "prefix": p["prefix"],
                "days": str(p["days"]),
                "count": str(p["count"]),
                "listing": p["listing"],
            },
        )

    # ── ustawienia ────────────────────────────────────────────

    async def async_step_settings(self, user_input=None) -> Any:
        if user_input is not None:
            return self.async_create_entry(
                data={**self._entry.options, OPT_WEBHOOK: user_input[OPT_WEBHOOK]}
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_WEBHOOK,
                        default=self._entry.options.get(OPT_WEBHOOK, True),
                    ): selector.BooleanSelector()
                }
            ),
        )
