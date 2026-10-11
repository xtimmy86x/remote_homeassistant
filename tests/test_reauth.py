"""Reauthenticate a remote connection without recreating its config entry."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.const import CONF_ACCESS_TOKEN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant.const import DOMAIN
from custom_components.remote_homeassistant.rest_api import CannotConnect, InvalidAuth


UUID = "1234567890abcdef1234567890abcdef"


def make_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UUID,
        title="Second Home",
        data={"host": "remote.invalid", "port": 8123, CONF_ACCESS_TOKEN: "old-token"},
        options={"include_entities": ["sensor.temperature"], "entity_prefix": "casa_"},
    )
    entry.add_to_hass(hass)
    return entry


async def start_reauth(hass, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )


@pytest.mark.asyncio
async def test_reauth_checks_token_and_preserves_entry(
    hass, monkeypatch, enable_custom_integrations
):
    """Reject invalid credentials and update only the saved token on success."""
    entry = make_entry(hass)
    reload_listener = AsyncMock()
    entry.add_update_listener(reload_listener)
    validate = AsyncMock(side_effect=[
        InvalidAuth(),
        {"uuid": UUID, "title": "Second Home"},
        {"uuid": UUID, "title": "Second Home"},
    ])
    get_states = AsyncMock(side_effect=[InvalidAuth(), ["sensor.temperature"]])
    monkeypatch.setattr("custom_components.remote_homeassistant.config_flow.validate_input", validate)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.async_get_remote_entity_ids",
        get_states,
    )

    result = await start_reauth(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    for token in ("bad-token", "limited-token"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ACCESS_TOKEN: token}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}
        assert entry.data[CONF_ACCESS_TOKEN] == "old-token"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "new-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {
        "host": "remote.invalid", "port": 8123, CONF_ACCESS_TOKEN: "new-token"
    }
    assert entry.options == {
        "include_entities": ["sensor.temperature"], "entity_prefix": "casa_"
    }
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert validate.await_count == 3
    assert get_states.await_count == 2
    await hass.async_block_till_done()
    reload_listener.assert_awaited_once()


@pytest.mark.asyncio
async def test_reauth_refuses_token_for_another_instance(
    hass, monkeypatch, enable_custom_integrations
):
    """A valid token from a different Home Assistant cannot replace this one."""
    entry = make_entry(hass)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.validate_input",
        AsyncMock(return_value={"uuid": "different-instance", "title": "Other"}),
    )
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.async_get_remote_entity_ids",
        AsyncMock(return_value=[]),
    )
    result = await start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "other-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_instance"
    assert entry.data[CONF_ACCESS_TOKEN] == "old-token"


@pytest.mark.asyncio
async def test_reauth_keeps_form_when_remote_is_offline(
    hass, monkeypatch, enable_custom_integrations
):
    """An offline remote must not make a submitted token permanent."""
    entry = make_entry(hass)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.validate_input",
        AsyncMock(side_effect=CannotConnect()),
    )
    result = await start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ACCESS_TOKEN: "new-token"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_ACCESS_TOKEN] == "old-token"
