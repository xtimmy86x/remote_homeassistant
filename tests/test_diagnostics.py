"""Check downloadable diagnostics and sensitive-data isolation."""

import json

import pytest
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import (
    RemoteConnection,
    STATE_CONNECTED,
    STATE_RECONNECTING,
)
from custom_components.remote_homeassistant.const import (
    CONF_REMOTE_CONNECTION,
    DOMAIN,
    REMOTE_ID,
)
from custom_components.remote_homeassistant.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.asyncio
async def test_diagnostics_report_status_count_and_safe_error(
    hass, enable_custom_integrations
):
    """The download contains operational data but no config or entity IDs."""
    token = "private-test-access-token"
    host = "private-test-host.invalid"
    entity_id = "sensor.private_test_entity"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567890abcdef1234567890abcdef",
        data={"host": host, "port": 8123, "access_token": token},
        options={"include_entities": [entity_id]},
    )
    entry.add_to_hass(hass)
    remote = RemoteConnection(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        CONF_REMOTE_CONNECTION: remote
    }
    remote.set_connection_state(STATE_CONNECTED)
    remote._entities.add(entity_id)
    remote._record_error("websocket_connect_failed")
    remote.set_connection_state(STATE_RECONNECTING)

    integration = await async_get_integration(hass, DOMAIN)
    platform = await integration.async_get_platform("diagnostics")
    diagnostics = await platform.async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics == {
        "connection_state": STATE_RECONNECTING,
        "imported_entity_count": 1,
        "last_error": "websocket_connect_failed",
    }
    exported = json.dumps(diagnostics)
    assert token not in exported
    assert host not in exported
    assert entity_id not in exported


@pytest.mark.asyncio
async def test_diagnostics_handle_unloaded_and_receiver_entries(hass):
    """Downloads remain available when no remote connection is running."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567890abcdef1234567890abcdef",
        data={"access_token": "private-test-access-token"},
    )
    entry.add_to_hass(hass)
    assert await async_get_config_entry_diagnostics(hass, entry) == {
        "connection_state": "not_loaded",
        "imported_entity_count": 0,
        "last_error": None,
    }

    receiver = MockConfigEntry(domain=DOMAIN, unique_id=REMOTE_ID, data={})
    receiver.add_to_hass(hass)
    assert await async_get_config_entry_diagnostics(hass, receiver) == {
        "connection_state": "not_applicable",
        "imported_entity_count": 0,
        "last_error": None,
    }
