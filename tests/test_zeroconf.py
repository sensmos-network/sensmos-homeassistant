"""Wykrycie noda w LAN (_sensmos._tcp): PIN zamiast adresu, nowe IP znanego noda, Hub pominięty."""
from ipaddress import ip_address
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # starsze HA
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from custom_components.sensmos.api import SensmosAuthError
from custom_components.sensmos.const import CONF_HOST, CONF_MODE, CONF_PIN, DOMAIN, MODE_NODE

DEV = "ab12cd34ef56ab12cd34ef56ab12cd34"


def _info(version="1.12", host="192.168.1.50", port=80, device_id=DEV):
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        hostname=f"sensmos-{device_id[:6]}.local.",
        name=f"sensmos-{device_id[:6]}._sensmos._tcp.local.",
        port=port,
        type="_sensmos._tcp.local.",
        properties={"device_id": device_id, "version": version},
    )


async def _start(hass, info):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )


async def test_discovered_node_asks_only_pin(hass):
    r = await _start(hass, _info())
    assert r["type"] is FlowResultType.FORM
    assert r["step_id"] == "zeroconf_confirm"
    assert r["description_placeholders"]["host"] == "192.168.1.50"
    with patch("custom_components.sensmos.config_flow.SensmosApi.config", return_value={"device_id": DEV}), \
         patch("custom_components.sensmos.config_flow.SensmosApi.info", return_value={"city": "Lodz"}), \
         patch("custom_components.sensmos.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_PIN: " 1234 "})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Sensmos Lodz"
    assert r["data"] == {CONF_MODE: MODE_NODE, CONF_HOST: "192.168.1.50", CONF_PIN: "1234"}
    assert r["result"].unique_id == DEV


async def test_wrong_pin_stays_on_form(hass):
    r = await _start(hass, _info())
    with patch("custom_components.sensmos.config_flow.SensmosApi.config", side_effect=SensmosAuthError("x")):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_PIN: "0000"})
    assert r["type"] is FlowResultType.FORM
    assert r["errors"] == {"base": "invalid_pin"}


async def test_known_node_gets_new_address(hass):
    e = MockConfigEntry(domain=DOMAIN, unique_id=DEV,
                        data={CONF_MODE: MODE_NODE, CONF_HOST: "192.168.1.9", CONF_PIN: "1"})
    e.add_to_hass(hass)
    with patch("custom_components.sensmos.async_setup_entry", return_value=True):
        r = await _start(hass, _info(host="192.168.1.77"))
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "already_configured"
    assert e.data[CONF_HOST] == "192.168.1.77"


async def test_hub_is_skipped(hass):
    r = await _start(hass, _info(version="hub-0.2.0-beta3", port=8080))
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "hub_not_supported"


async def test_no_device_id(hass):
    r = await _start(hass, _info(device_id=""))
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "not_sensmos_device"


async def test_nonstandard_port_kept(hass):
    r = await _start(hass, _info(port=8081))
    assert r["description_placeholders"]["host"] == "192.168.1.50:8081"


async def test_manual_add_still_works(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "node"})
    with patch("custom_components.sensmos.config_flow.SensmosApi.config", return_value={"device_id": DEV}), \
         patch("custom_components.sensmos.config_flow.SensmosApi.info", return_value={}), \
         patch("custom_components.sensmos.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_HOST: "http://10.0.0.5/", CONF_PIN: "9"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"][CONF_HOST] == "10.0.0.5"
