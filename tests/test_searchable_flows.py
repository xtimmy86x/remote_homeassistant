"""Exercise searchable selectors in initial and options flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.helpers.selector import SelectSelector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant.const import (
    CONF_REMOTE_CONNECTION,
    DOMAIN,
)


def selectors(form):
    """Extract selectors by field name from a flow form, including sections."""
    found = {}
    for marker, value in form["data_schema"].schema.items():
        if isinstance(value, section):
            found.update(
                {inner.schema: sel for inner, sel in value.schema.schema.items()}
            )
        else:
            found[marker.schema] = value
    return found


@pytest.mark.asyncio
async def test_initial_flow_searches_remote_entities(
    hass, monkeypatch, enable_custom_integrations
):
    """The first setup uses remote choices and preserves the selection."""
    entity_ids = [f"sensor.remote_{index}" for index in range(3000)]
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.validate_input",
        AsyncMock(return_value={"uuid": "1234567890abcdef1234567890abcdef", "title": "Remote"}),
    )
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.config_flow.async_get_remote_entity_ids",
        AsyncMock(return_value=entity_ids),
    )

    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert form["type"] is FlowResultType.MENU
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"next_step_id": "connection_details"}
    )
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            "host": "remote.invalid",
            "port": 8123,
            CONF_ACCESS_TOKEN: "token",
            "secure": False,
            "advanced": {"max_message_size": 1024, "verify_ssl": False},
        },
    )

    assert form["step_id"] == "entity_selection"
    for field in ("include_entities", "exclude_entities", "include_domains", "exclude_domains"):
        selector = selectors(form)[field]
        assert isinstance(selector, SelectSelector)
        assert selector.config["multiple"] is True
    assert len(selectors(form)["include_entities"].config["options"]) == 3000
    with pytest.raises(vol.Invalid):
        selectors(form)["include_entities"](["sensor.not_on_remote"])

    assert form["description_placeholders"] == {"count": "3000"}

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"include": {"include_entities": ["sensor.remote_2048"]}, "exclude": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["max_message_size"] == 1024
    assert result["data"]["verify_ssl"] is False
    assert result["data"]["options"]["include_entities"] == ["sensor.remote_2048"]


@pytest.mark.asyncio
async def test_options_flow_searches_services_and_accepts_new_events(
    hass, enable_custom_integrations
):
    """Saved offline choices stay selectable and each menu group saves alone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567890abcdef1234567890abcdef",
        title="Remote",
        data={"host": "remote.invalid", "port": 8123, "access_token": "token"},
        options={
            "include_entities": ["sensor.old"],
            "include_domains": ["climate"],
            "services": ["switch.legacy"],
            "subscribe_events": ["existing_event"],
        },
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        CONF_REMOTE_CONNECTION: SimpleNamespace(
            _all_entity_names={"sensor.current", "climate.hall", "light.desk"},
            proxy_services=SimpleNamespace(services=["light.turn_on"]),
        )
    }

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] is FlowResultType.MENU
    assert menu["menu_options"] == ["entities", "naming", "services", "events"]
    assert menu["description_placeholders"] == {
        "selected": "1", "total": "3", "filters": "0"
    }

    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "services"}
    )
    service_selector = selectors(form)["services"]
    assert isinstance(service_selector, SelectSelector)
    assert service_selector.config["options"] == ["light.turn_on", "switch.legacy"]
    service_selector(["switch.legacy"])
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"service_prefix": "remote_", "services": ["switch.legacy"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["service_prefix"] == "remote_"
    # Groups not shown in the form keep their saved values.
    assert entry.options["include_entities"] == ["sensor.old"]

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "entities"}
    )
    assert selectors(form)["include_entities"].config["options"] == [
        "climate.hall", "light.desk", "sensor.current", "sensor.old"
    ]
    assert "climate" in selectors(form)["include_domains"].config["options"]
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"include": {"include_domains": ["climate", "light"]}, "exclude": {}},
    )
    assert entry.options["include_domains"] == ["climate", "light"]
    assert entry.options["include_entities"] == []

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "events"}
    )
    event_selector = selectors(form)["subscribe_events"]
    assert event_selector.config["multiple"] is True
    assert event_selector.config["custom_value"] is True
    assert event_selector(["existing_event", "new_event"]) == [
        "existing_event", "new_event"
    ]

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"subscribe_events": ["existing_event", "new_event"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["subscribe_events"] == ["existing_event", "new_event"]
    assert entry.options["services"] == ["switch.legacy"]
