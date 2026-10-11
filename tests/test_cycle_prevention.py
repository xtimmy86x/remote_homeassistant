"""Prevent imported states from bouncing between Home Assistant instances."""

import inspect

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import (
    ATTR_REMOTE_ORIGIN,
    RemoteConnection,
)
from custom_components.remote_homeassistant.rest_api import async_get_remote_entity_ids


UUID = "1234567890abcdef1234567890abcdef"


@pytest.mark.asyncio
async def test_initial_entity_picker_omits_imports(hass, monkeypatch):
    """The first setup screen only offers native remote entity IDs."""
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self):
            return [
                {"entity_id": "sensor.native", "attributes": {}},
                {"entity_id": "sensor.imported", "attributes": {
                    ATTR_REMOTE_ORIGIN: UUID,
                }},
            ]

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        "custom_components.remote_homeassistant.rest_api.async_get_clientsession",
        lambda *_args: Session(),
    )
    entities = await async_get_remote_entity_ids(
        hass, "remote.invalid", 8123, False, "token", True
    )
    assert entities == ["sensor.native"]


def make_entry(hass, *, unique_id=UUID, options=None):
    entry = MockConfigEntry(
        domain="remote_homeassistant",
        unique_id=unique_id,
        data={"host": "remote.invalid", "port": 8123},
        options=options or {"include_domains": ["sensor"]},
    )
    entry.add_to_hass(hass)
    return entry


async def start_with_states(remote, states):
    """Feed WebSocket state messages to the integration's real handlers."""
    callbacks = {}

    async def call(handler, message_type, **kwargs):
        if message_type == "subscribe_events":
            callbacks[kwargs["event_type"]] = handler
            return
        message = {"success": True, "result": states if message_type == "get_states" else {}}
        result = handler(message)
        if inspect.isawaitable(result):
            await result

    remote.call = call
    await remote._init()
    return callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"import_all": False}, None),
        ({"import_all": False, "include_entities": ["sensor.native"]}, "42"),
        ({"include_entities": ["sensor.other"]}, None),
        ({"import_all": True, "include_entities": ["sensor.other"]}, "42"),
        ({}, "42"),
    ],
)
async def test_panel_import_mode_preserves_existing_entries(
    hass, entity_registry, options, expected
):
    """Explicit import mode and pre-panel whitelists have stable semantics."""
    entry = make_entry(hass, options={**options, "entity_prefix": "panel_"})
    remote = RemoteConnection(hass, entry)
    await start_with_states(remote, [
        {"entity_id": "sensor.native", "state": "42", "attributes": {}},
    ])

    imported = hass.states.get("sensor.panel_native")
    assert (imported.state if imported else None) == expected
    assert "sensor.native" in remote._all_entity_names


@pytest.mark.asyncio
async def test_snapshot_skips_imported_state_even_when_selected(hass, entity_registry):
    """Explicit selection cannot override the cycle guard or keep a stale copy."""
    entry = make_entry(hass, options={
        "include_entities": ["sensor.native", "sensor.loop"],
        "entity_prefix": "a_",
    })
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor", "remote_homeassistant", f"{UUID[:16]}_sensor.a_loop",
        suggested_object_id="a_loop",
    )
    remote = RemoteConnection(hass, entry)
    await start_with_states(remote, [
        {"entity_id": "sensor.native", "state": "42", "attributes": {}},
        {"entity_id": "sensor.loop", "state": "42", "attributes": {
            ATTR_REMOTE_ORIGIN: "fedcba0987654321fedcba0987654321",
        }},
    ])

    assert hass.states.get("sensor.a_native").state == "42"
    assert hass.states.get("sensor.a_native").attributes[ATTR_REMOTE_ORIGIN] == UUID
    assert hass.states.get("sensor.a_loop") is None
    assert registry.async_get(stale.entity_id) is None
    assert "sensor.loop" not in remote._all_entity_names


@pytest.mark.asyncio
async def test_event_removes_entity_that_became_an_import(hass, entity_registry):
    """A provenance change removes the old mirrored state and registry row."""
    entry = make_entry(hass)
    remote = RemoteConnection(hass, entry)
    callbacks = await start_with_states(remote, [
        {"entity_id": "sensor.temp", "state": "20", "attributes": {}},
    ])
    registry = er.async_get(hass)
    assert hass.states.get("sensor.temp") is not None
    assert registry.async_get_entity_id(
        "sensor", "remote_homeassistant", f"{UUID[:16]}_sensor.temp"
    ) is not None

    callbacks["state_changed"]({
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {"entity_id": "sensor.temp", "new_state": {
                "state": "21", "attributes": {ATTR_REMOTE_ORIGIN: "another-instance"},
            }},
        },
    })
    assert hass.states.get("sensor.temp") is None
    assert registry.async_get_entity_id(
        "sensor", "remote_homeassistant", f"{UUID[:16]}_sensor.temp"
    ) is None
    assert "sensor.temp" not in remote._all_entity_names


@pytest.mark.asyncio
async def test_an_imported_state_is_not_imported_again(hass, entity_registry):
    """The marker survives a state snapshot sent back to another connection."""
    first = RemoteConnection(hass, make_entry(hass, options={
        "include_domains": ["sensor"], "entity_prefix": "b_",
    }))
    await start_with_states(first, [
        {"entity_id": "sensor.temp", "state": "20", "attributes": {}},
    ])
    mirrored = hass.states.get("sensor.b_temp")
    assert mirrored.attributes[ATTR_REMOTE_ORIGIN] == UUID

    other_uuid = "fedcba0987654321fedcba0987654321"
    second = RemoteConnection(hass, make_entry(
        hass, unique_id=other_uuid,
        options={"include_domains": ["sensor"], "entity_prefix": "a_"},
    ))
    await start_with_states(second, [{
        "entity_id": mirrored.entity_id,
        "state": mirrored.state,
        "attributes": dict(mirrored.attributes),
    }])
    assert hass.states.get("sensor.a_b_temp") is None
    assert er.async_get(hass).async_get_entity_id(
        "sensor", "remote_homeassistant", f"{other_uuid[:16]}_sensor.a_b_temp"
    ) is None
