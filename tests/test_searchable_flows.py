"""Exercise searchable selectors in initial and options flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TYPE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import SelectSelector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant.config_flow import CONF_MAIN
from custom_components.remote_homeassistant.const import (
    CONF_REMOTE_CONNECTION,
    DOMAIN,
)


def selectors(form):
    """Extract selectors by field name from a flow form."""
    return {marker.schema: value for marker, value in form["data_schema"].schema.items()}


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
    form = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_TYPE: CONF_MAIN}
    )
    form = await hass.config_entries.flow.async_configure(
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

    assert form["step_id"] == "entity_selection"
    for field in ("include_entities", "exclude_entities", "include_domains", "exclude_domains"):
        selector = selectors(form)[field]
        assert isinstance(selector, SelectSelector)
        assert selector.config["multiple"] is True
    assert len(selectors(form)["include_entities"].config["options"]) == 3000
    with pytest.raises(vol.Invalid):
        selectors(form)["include_entities"](["sensor.not_on_remote"])

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"include_entities": ["sensor.remote_2048"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["options"]["include_entities"] == ["sensor.remote_2048"]


@pytest.mark.asyncio
async def test_options_flow_searches_services_and_accepts_new_events(
    hass, enable_custom_integrations
):
    """Saved offline choices stay selectable and events need only one submit."""
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
            _all_entity_names={"sensor.current"},
            proxy_services=SimpleNamespace(services=["light.turn_on"]),
        )
    }

    form = await hass.config_entries.options.async_init(entry.entry_id)
    service_selector = selectors(form)["services"]
    assert isinstance(service_selector, SelectSelector)
    assert service_selector.config["options"] == ["light.turn_on", "switch.legacy"]
    service_selector(["switch.legacy"])

    form = await hass.config_entries.options.async_configure(
        form["flow_id"], {"service_prefix": "remote_", "services": ["switch.legacy"]}
    )
    assert form["step_id"] == "domain_entity_filters"
    assert selectors(form)["include_entities"].config["options"] == [
        "sensor.current", "sensor.old"
    ]
    assert "climate" in selectors(form)["include_domains"].config["options"]

    form = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"include_entities": ["sensor.old"], "include_domains": ["climate"]},
    )
    form = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert form["step_id"] == "events"
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
