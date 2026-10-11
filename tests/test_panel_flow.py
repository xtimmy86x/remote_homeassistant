"""Initial setup creates a connection; selection is handled in the sidebar."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TYPE
from homeassistant.data_entry_flow import FlowResultType

from custom_components.remote_homeassistant.config_flow import CONF_MAIN
from custom_components.remote_homeassistant.const import DOMAIN


@pytest.mark.asyncio
async def test_initial_flow_only_creates_connection(
    hass, monkeypatch, enable_custom_integrations
):
    """A new connection starts without importing thousands of remote states."""
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.validate_input",
        AsyncMock(return_value={"uuid": "1234567890abcdef1234567890abcdef", "title": "Remote"}),
    )
    lookup = AsyncMock()
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.async_get_remote_entity_ids",
        lookup,
    )

    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_TYPE: CONF_MAIN}
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            "host": "remote.invalid",
            "port": 8123,
            CONF_ACCESS_TOKEN: "token",
            "max_message_size": 1024,
            "secure": False,
            "verify_ssl": False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["options"] == {"import_all": False}
    lookup.assert_not_awaited()
