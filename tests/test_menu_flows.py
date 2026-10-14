"""Exercise menu navigation, reconfiguration and filter subentries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import (
    async_entry_filters,
    async_migrate_entry,
)
from custom_components.remote_homeassistant.config_flow import ConfigFlow
from custom_components.remote_homeassistant.const import (
    CONF_REMOTE_CONNECTION,
    DOMAIN,
    REMOTE_ID,
)
from custom_components.remote_homeassistant.rest_api import CannotConnect

UUID = "1234567890abcdef1234567890abcdef"
FLOW = "custom_components.remote_homeassistant.config_flow"


def remote_entry(**kwargs):
    """Return a connection entry to a remote instance."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=UUID,
        title="Remote",
        data={
            "host": "old.invalid",
            "port": 8123,
            CONF_ACCESS_TOKEN: "old-token",
            "secure": False,
            "verify_ssl": True,
            "max_message_size": 1024,
        },
        minor_version=2,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_remote_node_menu_entry_has_no_options(hass, enable_custom_integrations):
    """The remote node choice is a translated menu entry without options."""
    menu = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert menu["type"] is FlowResultType.MENU
    assert menu["menu_options"] == ["connection_details", "remote_node"]

    result = await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "remote_node"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == REMOTE_ID
    assert not ConfigFlow.async_supports_options_flow(entry)
    assert ConfigFlow.async_get_supported_subentry_types(entry) == {}


@pytest.mark.asyncio
async def test_reconfigure_changes_host_and_keeps_token(
    hass, monkeypatch, enable_custom_integrations
):
    """An empty token keeps the saved one and the remote must stay the same."""
    entry = remote_entry()
    entry.add_to_hass(hass)
    validate = AsyncMock(side_effect=[CannotConnect, {"uuid": UUID, "title": "Remote"}])
    monkeypatch.setattr(f"{FLOW}.validate_input", validate)
    monkeypatch.setattr(f"{FLOW}.async_get_remote_entity_ids", AsyncMock(return_value=[]))

    form = await entry.start_reconfigure_flow(hass)
    assert form["step_id"] == "reconfigure"
    user_input = {
        "host": "new.invalid",
        "port": 8443,
        "secure": True,
        "advanced": {"verify_ssl": False, "max_message_size": 2048},
    }

    form = await hass.config_entries.flow.async_configure(form["flow_id"], user_input)
    assert form["errors"] == {"base": "cannot_connect"}
    assert entry.data["host"] == "old.invalid"

    result = await hass.config_entries.flow.async_configure(form["flow_id"], user_input)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {
        "host": "new.invalid",
        "port": 8443,
        CONF_ACCESS_TOKEN: "old-token",
        "secure": True,
        "verify_ssl": False,
        "max_message_size": 2048,
    }
    assert validate.await_args.args[1][CONF_ACCESS_TOKEN] == "old-token"


@pytest.mark.asyncio
async def test_reconfigure_rejects_other_instance_and_yaml(
    hass, monkeypatch, enable_custom_integrations
):
    """Reconfiguration cannot swap the remote or edit YAML connections."""
    entry = remote_entry()
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        f"{FLOW}.validate_input",
        AsyncMock(return_value={"uuid": "f" * 32, "title": "Other"}),
    )
    monkeypatch.setattr(f"{FLOW}.async_get_remote_entity_ids", AsyncMock(return_value=[]))

    form = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"host": "other.invalid", "port": 8123, CONF_ACCESS_TOKEN: "x", "advanced": {"max_message_size": 1}},
    )
    assert result["reason"] == "wrong_instance"
    assert entry.data["host"] == "old.invalid"

    yaml_entry = MockConfigEntry(
        domain=DOMAIN, unique_id="a" * 32, source=SOURCE_IMPORT, data={}
    )
    yaml_entry.add_to_hass(hass)
    result = await yaml_entry.start_reconfigure_flow(hass)
    assert result["reason"] == "yaml_managed"


@pytest.mark.asyncio
async def test_filter_subentries_can_be_added_and_edited(
    hass, enable_custom_integrations
):
    """Each filter is its own subentry and needs at least one limit."""
    entry = remote_entry()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        CONF_REMOTE_CONNECTION: SimpleNamespace(_all_entity_names={"sensor.power"})
    }

    form = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "filter"), context={"source": SOURCE_USER}
    )
    assert form["step_id"] == "user"
    form = await hass.config_entries.subentries.async_configure(
        form["flow_id"], {"entity_id": "sensor.power"}
    )
    assert form["errors"] == {"base": "missing_limit"}

    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {"entity_id": "sensor.*_power", "unit_of_measurement": "W", "below": 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.title == "sensor.*_power, W, < 0"
    assert async_entry_filters(entry) == [
        {"entity_id": "sensor.*_power", "unit_of_measurement": "W", "above": None, "below": 0}
    ]

    form = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert form["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"], {"entity_id": "sensor.*_power", "above": 5000}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry.subentry_id].title == "sensor.*_power, > 5000"


@pytest.mark.asyncio
async def test_migration_moves_ui_filters_to_subentries(hass):
    """UI filters become subentries; YAML filters stay in options."""
    filter_conf = {
        "entity_id": "sensor.power", "unit_of_measurement": None,
        "above": 10.0, "below": None,
    }
    ui_entry = MockConfigEntry(
        domain=DOMAIN, unique_id=UUID, minor_version=1,
        options={"filter": [filter_conf], "include_domains": ["sensor"]},
    )
    yaml_entry = MockConfigEntry(
        domain=DOMAIN, unique_id="a" * 32, minor_version=1, source=SOURCE_IMPORT,
        options={"filter": [filter_conf]},
    )
    ui_entry.add_to_hass(hass)
    yaml_entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, ui_entry)
    assert await async_migrate_entry(hass, yaml_entry)

    assert ui_entry.minor_version == 2
    assert ui_entry.options == {"include_domains": ["sensor"]}
    (subentry,) = ui_entry.subentries.values()
    assert subentry.title == "sensor.power, > 10"
    assert async_entry_filters(ui_entry) == [filter_conf]

    assert yaml_entry.minor_version == 2
    assert yaml_entry.subentries == {}
    assert async_entry_filters(yaml_entry) == [filter_conf]
