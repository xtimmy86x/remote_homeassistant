"""Check service forwarding with Home Assistant's actual target resolution."""

import inspect

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import RemoteConnection


class Connection:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


@pytest.mark.asyncio
async def test_service_targets_only_this_remote_entities(hass, entity_registry):
    """Direct, area and device targets are translated into source entity IDs."""
    entry = MockConfigEntry(
        domain="remote_homeassistant",
        unique_id="1234567890abcdef1234567890abcdef",
        data={"host": "remote.invalid", "port": 8123},
        options={"include_domains": ["light"], "entity_prefix": "casa_"},
    )
    entry.add_to_hass(hass)
    remote = RemoteConnection(hass, entry)

    async def call(handler, message_type, **kwargs):
        if message_type == "subscribe_events":
            return
        message = {"success": True, "result": (
            [
                {"entity_id": "light.lamp", "state": "off", "attributes": {}},
                {"entity_id": "light.extra", "state": "off", "attributes": {}},
            ] if message_type == "get_states" else {}
        )}
        result = handler(message)
        if inspect.isawaitable(result):
            await result

    remote.call = call
    await remote._init()
    connection = Connection()
    remote._connection = connection

    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    registry.async_update_entity("light.casa_lamp", area_id=area.id)
    local = registry.async_get_or_create(
        "light", "test", "local", suggested_object_id="local",
    )
    registry.async_update_entity(local.entity_id, area_id=area.id)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("remote_homeassistant", "test-device")},
    )
    registry.async_update_entity("light.casa_lamp", device_id=device.id)
    hass.services.async_register("light", "turn_on", lambda call: None)

    async def call_service(service_data, target=None):
        await hass.services.async_call(
            "light", "turn_on", service_data, blocking=True, target=target
        )
        await hass.async_block_till_done()

    await call_service({"brightness": 50}, {
        "entity_id": ["light.casa_lamp", "light.local"],
    })
    assert connection.sent[-1]["service_data"] == {
        "brightness": 50, "entity_id": ["light.lamp"],
    }

    await call_service({"brightness": 80}, {"area_id": area.id})
    assert connection.sent[-1]["service_data"] == {
        "brightness": 80, "entity_id": ["light.lamp"],
    }

    await call_service({}, {"device_id": device.id})
    assert connection.sent[-1]["service_data"] == {
        "entity_id": ["light.lamp"],
    }

    await call_service({}, {"entity_id": "all"})
    assert connection.sent[-1]["service_data"] == {
        "entity_id": ["light.extra", "light.lamp"],
    }

    previous_count = len(connection.sent)
    await call_service({"brightness": 25})
    assert len(connection.sent) == previous_count
