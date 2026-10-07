"""Mirror device and area metadata using Home Assistant's real registries."""

import inspect

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import RemoteConnection, async_remove_entry
from custom_components.remote_homeassistant.remote_registry import RemoteRegistrySync


UUID = "1234567890abcdef1234567890abcdef"
AREAS = [{"area_id": "kitchen", "name": "Kitchen"}]
DEVICES = [
    {"id": "bridge", "name": "Bridge", "area_id": "kitchen"},
    {"id": "lamp", "name": "Lamp Hub", "manufacturer": "Acme", "model": "M1",
     "area_id": "kitchen", "via_device_id": "bridge"},
    {"id": "child", "name": "Child Device", "parent_device_id": "bridge"},
    {"id": "unused", "name": "Unused"},
]
ENTITIES = [
    {"entity_id": "light.lamp", "device_id": "lamp", "area_id": None},
    {"entity_id": "sensor.air", "device_id": "child", "area_id": "kitchen"},
    {"entity_id": "sensor.unused", "device_id": "unused", "area_id": None},
]


def make_entry(hass):
    entry = MockConfigEntry(
        domain="remote_homeassistant",
        unique_id=UUID,
        title="Second Home",
        data={"host": "remote.invalid", "port": 8123},
        options={"include_entities": ["light.lamp", "sensor.air"],
                 "entity_prefix": "casa_"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_only_imported_entities_get_remote_devices_and_areas(
    hass, entity_registry
):
    """Device ancestry and explicit entity areas are mapped to local IDs."""
    entry = make_entry(hass)
    remote = RemoteConnection(hass, entry)
    responses = {
        "get_states": [
            {"entity_id": "light.lamp", "state": "on", "attributes": {}},
            {"entity_id": "sensor.air", "state": "23", "attributes": {}},
            {"entity_id": "sensor.unused", "state": "0", "attributes": {}},
        ],
        "config/area_registry/list": AREAS,
        "config/device_registry/list": DEVICES,
        "config/entity_registry/list": ENTITIES,
        "get_services": {},
    }

    callbacks = {}

    async def call(handler, message_type, **kwargs):
        if message_type == "subscribe_events":
            callbacks[kwargs["event_type"]] = handler
            return
        result = handler({"success": True, "result": responses[message_type]})
        if inspect.isawaitable(result):
            await result

    remote.call = call
    await remote._init()
    await hass.async_block_till_done()
    assert len(remote._registry_snapshots) == 3, remote._registry_snapshots
    assert remote._registry_sync.area_ids, remote._registry_snapshots

    areas = ar.async_get(hass)
    area = areas.async_get_area_by_name("Second Home: Kitchen")
    assert area is not None, {
        "snapshots": remote._registry_snapshots.keys(),
        "complete": remote._snapshot_complete,
        "names": remote._all_entity_names,
        "entities": remote._entities,
        "areas": list(areas.async_list_areas()),
    }
    devices = dr.async_get(hass)
    bridge = devices.async_get_device_by_identifier(("remote_homeassistant", f"{UUID}_bridge"), entry.entry_id)
    lamp = devices.async_get_device_by_identifier(("remote_homeassistant", f"{UUID}_lamp"), entry.entry_id)
    child = devices.async_get_device_by_identifier(("remote_homeassistant", f"{UUID}_child"), entry.entry_id)
    assert lamp.name == "Lamp Hub"
    assert lamp.manufacturer == "Acme"
    assert lamp.area_id == area.id
    assert lamp.via_device_id == bridge.id
    assert child.via_device_id == bridge.id
    assert devices.async_get_device_by_identifier(
        ("remote_homeassistant", f"{UUID}_unused"), entry.entry_id
    ) is None

    registry = er.async_get(hass)
    light = registry.async_get("light.casa_lamp")
    sensor = registry.async_get("sensor.casa_air")
    assert light.device_id == lamp.id
    assert sensor.device_id == child.id
    assert sensor.area_id == area.id
    assert registry.async_get("sensor.casa_unused") is None

    # A remote registry event refreshes metadata without reconnecting.
    responses["config/area_registry/list"] = [{"area_id": "kitchen", "name": "Dining"}]
    callbacks[ar.EVENT_AREA_REGISTRY_UPDATED]({
        "type": "event", "event": {"event_type": ar.EVENT_AREA_REGISTRY_UPDATED}
    })
    await hass.async_block_till_done()
    assert areas.async_get_area(area.id).name == "Second Home: Dining"

    # The persisted mapping keeps the same local area when the source renames it.
    restarted = RemoteRegistrySync(hass, entry, remote._prefixed_entity_id)
    await restarted.async_load()
    await restarted.async_sync(ENTITIES, DEVICES, [{"area_id": "kitchen", "name": "Pantry"}],
                               {"light.lamp", "sensor.air"})
    assert areas.async_get_area(area.id).name == "Second Home: Pantry"
    assert len(list(areas.async_list_areas())) == 1

    # A name chosen locally stays under the local user's control.
    areas.async_update(area.id, name="My Dining Room")
    await restarted.async_sync(ENTITIES, DEVICES, [{"area_id": "kitchen", "name": "Other"}],
                               {"light.lamp", "sensor.air"})
    assert areas.async_get_area(area.id).name == "My Dining Room"

    await async_remove_entry(hass, entry)
    assert devices.async_get(lamp.id) is None
    assert devices.async_get(bridge.id) is None
    assert areas.async_get_area(area.id) is not None  # Kept after a local rename.


@pytest.mark.asyncio
async def test_remove_connection_cleans_unused_automatic_area(hass, entity_registry):
    """A pristine copied area does not linger when the connection is removed."""
    entry = make_entry(hass)
    sync = RemoteRegistrySync(hass, entry, lambda entity_id: entity_id)
    await sync.async_load()
    area = ar.async_get(hass).async_create("Second Home: Kitchen")
    sync.area_ids = {"kitchen": {"id": area.id, "name": area.name}}
    await sync.store.async_save({"areas": sync.area_ids})
    await async_remove_entry(hass, entry)
    assert ar.async_get(hass).async_get_area(area.id) is None


@pytest.mark.asyncio
async def test_stale_devices_and_areas_are_pruned_after_complete_sync(
    hass, entity_registry
):
    """A removed import leaves no unused copied topology behind."""
    entry = make_entry(hass)
    registry = er.async_get(hass)
    light = registry.async_get_or_create(
        "light", "remote_homeassistant", f"{UUID[:16]}_light.casa_lamp",
        suggested_object_id="casa_lamp", config_entry=entry,
    )
    sync = RemoteRegistrySync(
        hass, entry, lambda source_id: source_id.replace("light.", "light.casa_", 1)
    )
    await sync.async_load()
    await sync.async_sync(ENTITIES, DEVICES, AREAS, {"light.lamp"})
    area = ar.async_get(hass).async_get_area_by_name("Second Home: Kitchen")
    assert area is not None
    assert registry.async_get(light.entity_id).device_id is not None

    registry.async_remove(light.entity_id)
    await sync.async_sync([], [], [], set())
    assert ar.async_get(hass).async_get_area(area.id) is None
    assert dr.async_get(hass).async_get_device_by_identifier(
        ("remote_homeassistant", f"{UUID}_lamp"), entry.entry_id
    ) is None


@pytest.mark.asyncio
async def test_failed_registry_snapshot_does_not_create_devices(hass, entity_registry):
    """Incomplete remote metadata cannot be used for a partial import."""
    entry = make_entry(hass)
    remote = RemoteConnection(hass, entry)

    async def call(handler, message_type, **kwargs):
        if message_type == "subscribe_events":
            return
        if message_type == "get_states":
            result = [{"entity_id": "light.lamp", "state": "on", "attributes": {}}]
        elif message_type == "config/area_registry/list":
            await handler({"success": False, "error": {"message": "denied"}})
            return
        elif message_type == "config/device_registry/list":
            result = DEVICES
        elif message_type == "config/entity_registry/list":
            result = ENTITIES
        else:
            result = {}
        returned = handler({"success": True, "result": result})
        if inspect.isawaitable(returned):
            await returned

    remote.call = call
    await remote._init()
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device_by_identifier(
        ("remote_homeassistant", f"{UUID}_lamp"), entry.entry_id
    ) is None
    assert er.async_get(hass).async_get("light.casa_lamp") is not None


@pytest.mark.asyncio
async def test_registry_responses_wait_for_complete_state_snapshot(hass, entity_registry):
    """Out-of-order registry replies must wait until states are imported."""
    entry = make_entry(hass)
    remote = RemoteConnection(hass, entry)
    pending = {}

    async def call(handler, message_type, **kwargs):
        if message_type == "subscribe_events":
            return
        if message_type == "get_services":
            await handler({"success": True, "result": {}})
            return
        pending[message_type] = handler

    remote.call = call
    await remote._init()
    for command, result in (
        ("config/entity_registry/list", ENTITIES),
        ("config/device_registry/list", DEVICES),
        ("config/area_registry/list", AREAS),
    ):
        await pending[command]({"success": True, "result": result})
    assert ar.async_get(hass).async_get_area_by_name("Second Home: Kitchen") is None

    pending["get_states"]({"success": True, "result": [
        {"entity_id": "light.lamp", "state": "on", "attributes": {}},
    ]})
    await hass.async_block_till_done()
    assert ar.async_get(hass).async_get_area_by_name("Second Home: Kitchen") is not None
