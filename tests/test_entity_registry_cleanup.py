"""Exercise remote entity cleanup against the real Home Assistant registry."""

import inspect
import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import RemoteConnection, async_remove_entry


REMOTE_UUID = "1234567890abcdef1234567890abcdef"
OTHER_UUID = "fedcba0987654321fedcba0987654321"
PREFIX = REMOTE_UUID[:16] + "_"


def make_entry(hass):
    entry = MockConfigEntry(
        domain="remote_homeassistant",
        unique_id=REMOTE_UUID,
        data={"host": "remote.invalid", "port": 8123},
        options={
            "include_entities": ["alarm_control_panel.antifurto_casa"],
            "entity_prefix": "casa_",
            "entity_friendly_name_prefix": "Casa ",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_full_snapshot_prunes_only_stale_imports(hass, entity_registry):
    """A selected import survives alongside unrelated registry entries."""
    entry = make_entry(hass)
    registry = er.async_get(hass)
    selected_id = "alarm_control_panel.casa_antifurto_casa"
    registry.async_get_or_create(
        "alarm_control_panel",
        "remote_homeassistant",
        PREFIX + selected_id,
        suggested_object_id="casa_antifurto_casa",
    )
    stale = registry.async_get_or_create(
        "sensor", "remote_homeassistant", PREFIX + "sensor.old",
        suggested_object_id="old",
    )
    other_remote = registry.async_get_or_create(
        "sensor", "remote_homeassistant", OTHER_UUID[:16] + "_sensor.other",
        suggested_object_id="other",
    )
    connection_sensor = registry.async_get_or_create(
        "sensor", "remote_homeassistant", REMOTE_UUID,
        suggested_object_id="connection",
    )

    remote = RemoteConnection(hass, entry)

    async def call(handler, message_type, **kwargs):
        if message_type == "get_states":
            result = handler({
                "success": True,
                "result": [{
                    "entity_id": "alarm_control_panel.antifurto_casa",
                    "state": "disarmed",
                    "attributes": {"friendly_name": "Antifurto"},
                }],
            })
        elif message_type == "get_services":
            result = handler({"success": True, "result": {}})
        else:
            return
        if inspect.isawaitable(result):
            await result

    remote.call = call
    await remote._init()

    selected = registry.async_get_entity_id(
        "alarm_control_panel", "remote_homeassistant", PREFIX + selected_id
    )
    assert registry.async_get(selected).config_entry_id == entry.entry_id
    assert registry.async_get(selected).original_name == "Casa Antifurto"
    assert hass.states.get(selected_id).state == "disarmed"
    assert registry.async_get(stale.entity_id) is None
    assert registry.async_get(other_remote.entity_id) is not None
    assert registry.async_get(connection_sensor.entity_id) is not None


@pytest.mark.asyncio
async def test_failed_snapshot_keeps_registry(hass, entity_registry):
    """An unsuccessful remote response must never trigger bulk cleanup."""
    entry = make_entry(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor", "remote_homeassistant", PREFIX + "sensor.old",
        suggested_object_id="old",
    )
    remote = RemoteConnection(hass, entry)

    async def call(handler, message_type, **kwargs):
        if message_type == "get_states":
            result = handler({"success": False, "error": {"message": "failed"}})
        elif message_type == "get_services":
            result = handler({"success": True, "result": {}})
        else:
            return
        if inspect.isawaitable(result):
            await result

    remote.call = call
    await remote._init()
    assert registry.async_get(stale.entity_id) is not None


@pytest.mark.asyncio
async def test_removing_connection_prunes_legacy_import(hass, entity_registry):
    """Deleting a connection removes its legacy entries but not other remotes."""
    entry = make_entry(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "sensor", "remote_homeassistant", PREFIX + "sensor.old",
        suggested_object_id="old",
    )
    other = registry.async_get_or_create(
        "sensor", "remote_homeassistant", OTHER_UUID[:16] + "_sensor.other",
        suggested_object_id="other",
    )
    await async_remove_entry(hass, entry)
    assert registry.async_get(legacy.entity_id) is None
    assert registry.async_get(other.entity_id) is not None
