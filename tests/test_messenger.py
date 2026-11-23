"""Komunikator w trybie konta: HA pisze do komunikatora (usługa i notify), stan doręczenia, lista komunikatorów."""
import asyncio
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.sensmos.cloud import SensmosCloud, clean_text
from custom_components.sensmos.const import CONF_BE, CONF_MODE, CONF_TOKEN, DOMAIN, MODE_CLOUD


class FakeWs:
    """Gniazdo /v1/term: każde ldev_send dostaje odpowiedź jak z serwera."""

    def __init__(self, cloud, answer=None):
        self.cloud, self.sent, self.closed = cloud, [], False
        self.answer = answer or (lambda m: {"type": "ldev_sent", "ref": m["ref"], "ok": True, "msg_id": str(40 + len(self.sent))})

    async def send_json(self, m):
        self.sent.append(m)
        if m.get("type") == "ldev_send" and self.answer:
            reply = self.answer(m)
            if reply is not None:
                asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.cloud._handle(__import__("json").dumps(reply))))

    async def close(self):
        self.closed = True


LDEVS = [{"id8": "aabbccdd", "name": "Tatry"}, {"id8": "11223344", "name": "Działka"}]


@pytest.fixture
async def cloud_entry(hass):
    holder = {}

    async def fake_connect(self):
        self._ws = FakeWs(self)
        self._set_gateways([])
        self._set_ldevs(LDEVS, notify=False)
        holder["cloud"] = self

    async def fake_run(self):
        await asyncio.sleep(0)

    entry = MockConfigEntry(domain=DOMAIN, unique_id="0x" + "ab" * 20,
                            data={CONF_MODE: MODE_CLOUD, CONF_BE: "https://api.sensmos.com", CONF_TOKEN: "smt_x"})
    entry.add_to_hass(hass)
    with patch.object(SensmosCloud, "connect", fake_connect), patch.object(SensmosCloud, "run", fake_run):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, holder["cloud"]


def test_clean_text_cuts_at_100_bytes_on_whole_letters():
    assert clean_text("a\nb\t c") == "a b c"
    t = clean_text("ż" * 80)                     # 2 bajty na literę
    assert t == "ż" * 50 and len(t.encode()) == 100
    assert clean_text("x" * 99 + "ą") == "x" * 99   # pół litery nie wchodzi


async def test_service_sends_by_name_and_returns_msg_id(hass, cloud_entry):
    entry, cloud = cloud_entry
    r = await hass.services.async_call(DOMAIN, "messenger_send", {"messenger": "tatry", "message": "Brama\notwarta"},
                                       blocking=True, return_response=True)
    assert r == {"msg_id": "41", "messenger": "aabbccdd"}
    assert cloud._ws.sent[-1] == {"type": "ldev_send", "ref": "1", "id8": "aabbccdd", "text": "Brama otwarta"}


async def test_notify_entity_per_messenger(hass, cloud_entry):
    entry, cloud = cloud_entry
    ids = sorted(s.entity_id for s in hass.states.async_all("notify"))
    assert len(ids) == 2, ids
    target = next(e for e in ids if "dzialka" in e or "11223344" in e)
    await hass.services.async_call("notify", "send_message", {"entity_id": target, "message": "Woda w piwnicy", "title": "Dom"},
                                   blocking=True)
    assert cloud._ws.sent[-1]["id8"] == "11223344"
    assert cloud._ws.sent[-1]["text"] == "Dom: Woda w piwnicy"


async def test_refusal_and_unknown_messenger_raise(hass, cloud_entry):
    entry, cloud = cloud_entry
    cloud._ws.answer = lambda m: {"type": "ldev_sent", "ref": m["ref"], "ok": False, "error": "rate_limit"}
    with pytest.raises(HomeAssistantError, match="rate_limit"):
        await hass.services.async_call(DOMAIN, "messenger_send", {"messenger": "aabbccdd", "message": "x"}, blocking=True)
    with pytest.raises(HomeAssistantError, match="not found"):
        await hass.services.async_call(DOMAIN, "messenger_send", {"messenger": "Nieznany", "message": "x"}, blocking=True)
    assert not cloud._pending


async def test_no_answer_times_out(hass, cloud_entry):
    entry, cloud = cloud_entry
    cloud._ws.answer = lambda m: None
    with patch("custom_components.sensmos.cloud.SEND_TIMEOUT_S", 0.05), pytest.raises(HomeAssistantError, match="no answer"):
        await hass.services.async_call(DOMAIN, "messenger_send", {"messenger": "aabbccdd", "message": "x"}, blocking=True)
    assert not cloud._pending


async def test_delivery_state_event(hass, cloud_entry):
    entry, cloud = cloud_entry
    events = async_capture_events(hass, "sensmos_device_message_state")
    await cloud._handle('{"type":"ldev_state","msg_id":"41","dev":"AABBCCDD","state":"delivered"}')
    await hass.async_block_till_done()
    assert len(events) == 1
    d = events[0].data
    assert (d["msg_id"], d["device"], d["state"]) == ("41", "aabbccdd", "delivered")
    assert d["device_id"]


async def test_new_messenger_reloads_entry(hass, cloud_entry):
    entry, cloud = cloud_entry
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await cloud._handle('{"type":"ldevs","ldevs":[{"id8":"aabbccdd","name":"Tatry"}]}')
        reload.assert_called_once_with(entry.entry_id)
        reload.reset_mock()
        await cloud._handle('{"type":"ldevs","ldevs":[{"id8":"aabbccdd","name":"Tatry 2"}]}')
        reload.assert_not_called()
