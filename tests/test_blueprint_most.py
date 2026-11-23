"""Blueprint komunikator_most: komunikator ↔ Meshtastic (MQTT JSON) ↔ MeshCore (meshcore-ha), na prawdziwym HA."""
import json
import shutil
from pathlib import Path

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_mqtt_message, async_mock_service

BP = Path(__file__).parent.parent / "blueprints" / "automation" / "sensmos" / "komunikator_most.yaml"
GW = 0x7EFEEE00


@pytest.fixture
async def most(hass, mqtt_mock):
    dst = Path(hass.config.path("blueprints/automation/sensmos"))
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(BP, dst / BP.name)
    calls = {
        "kom": async_mock_service(hass, "sensmos", "messenger_send"),
        "mc": async_mock_service(hass, "meshcore", "send_channel_message"),
    }

    async def setup(**extra):
        inputs = {"messenger": "Tatry", "label": "Piotr", "mt_gateway": "!7efeee00", "mt_root": "msh/EU_868",
                  "mt_channel": 1, "mc_channel": 2, **extra}
        assert await async_setup_component(hass, "automation", {"automation": [{
            "id": "most", "alias": "most",
            "use_blueprint": {"path": "sensmos/komunikator_most.yaml", "input": inputs}}]})
        await hass.async_block_till_done()
        assert hass.states.get("automation.most").state == "on"
        mqtt_mock.async_publish.reset_mock()
        return calls
    return setup


def _published(mqtt_mock):
    return [(c.args[0], json.loads(c.args[1])) for c in mqtt_mock.async_publish.call_args_list]


async def test_messenger_to_meshtastic_and_meshcore(hass, most, mqtt_mock):
    calls = await most()
    hass.bus.async_fire("sensmos_device_message", {"device": "aabbccdd", "name": "Tatry", "text": "m Jestem na szczycie"})
    hass.bus.async_fire("sensmos_device_message", {"device": "aabbccdd", "name": "Tatry", "text": "c Schodzę"})
    hass.bus.async_fire("sensmos_device_message", {"device": "aabbccdd", "name": "Tatry", "text": "zwykła wiadomość"})
    hass.bus.async_fire("sensmos_device_message", {"device": "99999999", "name": "Inny", "text": "m obcy"})
    await hass.async_block_till_done()
    assert _published(mqtt_mock) == [("msh/EU_868/2/json/mqtt/",
                                      {"from": GW, "type": "sendtext", "channel": 1, "payload": "[Piotr] Jestem na szczycie"})]
    assert [dict(c.data) for c in calls["mc"]] == [{"channel_idx": 2, "message": "Piotr: Schodzę", "truncate": True}]
    assert calls["kom"] == []


async def test_meshtastic_channel_to_messenger(hass, most, mqtt_mock):
    calls = await most()
    hass.states.async_set("sensor.meshtastic_7efeee01", "x", {"friendly_name": "Meshtastic Ola"})
    msg = {"from": 0x7EFEEE01, "type": "text", "channel": 1, "payload": {"text": "Obiad o 18"}, "sender": "!7efeee00"}
    async_fire_mqtt_message(hass, "msh/EU_868/2/json/Rodzina/!7efeee00", json.dumps(msg))
    # inny kanał, echo bramki, nie-tekst: pomijane
    async_fire_mqtt_message(hass, "msh/EU_868/2/json/LongFast/!7efeee00", json.dumps({**msg, "channel": 0}))
    async_fire_mqtt_message(hass, "msh/EU_868/2/json/Rodzina/!7efeee00", json.dumps({**msg, "from": GW}))
    async_fire_mqtt_message(hass, "msh/EU_868/2/json/Rodzina/!7efeee00", json.dumps({**msg, "type": "position"}))
    async_fire_mqtt_message(hass, "msh/EU_868/2/json/Rodzina/!7efeee00", "not json")
    await hass.async_block_till_done()
    assert [dict(c.data) for c in calls["kom"]] == [{"messenger": "Tatry", "message": "M Ola: Obiad o 18"}]
    assert calls["mc"] == []                    # bez łączenia sieci


async def test_meshcore_channel_to_messenger_and_join(hass, most, mqtt_mock):
    calls = await most(join_meshes=True)
    base = {"message_type": "channel", "channel_idx": 2, "sender_name": "Kuba", "message": "Jest zasięg?"}
    hass.bus.async_fire("meshcore_message", base)
    hass.bus.async_fire("meshcore_message", {**base, "outgoing": True})          # własna wysyłka
    hass.bus.async_fire("meshcore_message", {**base, "channel_idx": 0})          # Public
    hass.bus.async_fire("meshcore_message", {**base, "message": "[M] Ola: x"})   # echo mostu
    await hass.async_block_till_done()
    assert [dict(c.data) for c in calls["kom"]] == [{"messenger": "Tatry", "message": "C Kuba: Jest zasięg?"}]
    assert _published(mqtt_mock) == [("msh/EU_868/2/json/mqtt/",
                                      {"from": GW, "type": "sendtext", "channel": 1, "payload": "[C] Kuba: Jest zasięg?"})]
    msg = {"from": 0x7EFEEE05, "type": "text", "channel": 1, "payload": {"text": "Tak"}}
    async_fire_mqtt_message(hass, "msh/EU_868/2/json/Rodzina/!7efeee00", json.dumps(msg))
    await hass.async_block_till_done()
    assert dict(calls["mc"][-1].data) == {"channel_idx": 2, "message": "[M] !7efeee05: Tak", "truncate": True}


async def test_only_mention_and_meshcore_off(hass, most, mqtt_mock):
    calls = await most(only_mention=True, mc_channel=-1)
    for t in ["Ogólne gadanie", "Piotr, odbierasz?", "@sensmos test"]:
        async_fire_mqtt_message(hass, "msh/EU_868/2/json/Rodzina/!7efeee00",
                                json.dumps({"from": 0x7EFEEE01, "type": "text", "channel": 1, "payload": {"text": t}}))
    hass.bus.async_fire("meshcore_message", {"message_type": "channel", "channel_idx": 2, "sender_name": "K", "message": "Piotr?"})
    hass.bus.async_fire("sensmos_device_message", {"device": "aabbccdd", "name": "Tatry", "text": "c do MeshCore"})
    await hass.async_block_till_done()
    assert [c.data["message"] for c in calls["kom"]] == ["M !7efeee01: Piotr, odbierasz?", "M !7efeee01: @sensmos test"]
    assert calls["mc"] == []
