"""Settings panel data isolation and config entry updates."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant.const import DOMAIN, CONF_REMOTE_CONNECTION
from custom_components.remote_homeassistant.panel import (
    _panel_options,
    _revision,
    _save_options,
    ws_get,
)


def make_entry(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234567890abcdef1234567890abcdef",
        title="Remote",
        data={"host": "remote.invalid", "port": 8123, "access_token": "secret"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_panel_read_excludes_credentials_and_keeps_saved_offline_choices(
    hass, monkeypatch
):
    entry = make_entry(hass, {
        "include_entities": ["sensor.old"],
        "services": ["switch.legacy"],
        "load_components": ["climate"],
    })
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        CONF_REMOTE_CONNECTION: SimpleNamespace(
            _all_entity_names={"sensor.current"},
            _connection_state="connected",
            proxy_services=SimpleNamespace(services=["light.turn_on"]),
        )
    }
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        send_result=Mock(),
        send_error=Mock(),
    )
    ws_get(hass, connection, {"id": 1, "entry_id": entry.entry_id})
    await hass.async_block_till_done()

    result = connection.send_result.call_args.args[1]
    assert result["entities"] == ["sensor.current", "sensor.old"]
    assert result["domains"] == ["climate", "sensor"]
    assert result["services"] == ["light.turn_on", "switch.legacy"]
    assert result["options"]["import_all"] is False
    assert "secret" not in str(result)


def test_panel_save_checks_revision_and_preserves_unrelated_options(hass):
    entry = make_entry(hass, {"include_entities": ["sensor.old"], "future_option": 123})
    revision = _revision(entry)
    settings = _panel_options(entry)
    settings["include_entities"] = ["sensor.new"]
    settings["import_all"] = False
    assert _save_options(hass, entry, settings, revision)
    assert entry.options["include_entities"] == ["sensor.new"]
    assert entry.options["future_option"] == 123
    assert not _save_options(hass, entry, settings, revision)


@pytest.mark.asyncio
async def test_panel_is_registered_for_admins(
    hass, hass_ws_client, enable_custom_integrations
):
    from homeassistant.setup import async_setup_component
    from homeassistant.components import frontend

    assert await async_setup_component(hass, DOMAIN, {})
    panel = hass.data[frontend.DATA_PANELS]["remote-homeassistant"]
    assert panel.require_admin is True

    socket = await hass_ws_client()
    await socket.send_json({"id": 1, "type": f"{DOMAIN}/panel/list"})
    assert (await socket.receive_json())["result"] == []
