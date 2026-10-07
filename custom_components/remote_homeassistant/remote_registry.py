"""Mirror the registry metadata for imported remote entities."""

from __future__ import annotations

import logging

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class RemoteRegistrySync:
    """Keep source device and area associations scoped to one connection."""

    def __init__(self, hass, entry, prefixed_entity_id):
        self.hass = hass
        self.entry = entry
        self.prefixed_entity_id = prefixed_entity_id
        self.store = Store(hass, 1, f"{DOMAIN}_areas_{entry.unique_id}")
        self.area_ids = {}
        self.loaded = False

    async def async_load(self):
        """Restore the remote area ID to local area ID map."""
        if self.loaded:
            return
        data = await self.store.async_load() or {}
        self.area_ids = data.get("areas", {})
        self.loaded = True

    def _mirrored_devices(self, device_registry):
        """Find devices created for this remote connection, excluding its hub."""
        prefix = f"{self.entry.unique_id}_"
        return [
            device
            for device in device_registry.devices.values()
            if any(
                domain == DOMAIN and identifier.startswith(prefix)
                for domain, identifier in device.identifiers
            )
            and (
                device.config_entry_id == self.entry.entry_id
                if hasattr(device, "config_entry_id")
                else self.entry.entry_id in device.config_entries
            )
        ]

    def _prune_empty_areas(self, area_registry, device_registry, entity_registry,
                           used_source_ids=None):
        """Delete only untouched, unused areas owned by this connection."""
        changed = False
        for source_id, saved in list(self.area_ids.items()):
            if used_source_ids is not None and source_id in used_source_ids:
                continue
            area = area_registry.async_get_area(saved.get("id"))
            if area is None:
                del self.area_ids[source_id]
                changed = True
                continue
            if area.name != saved.get("name"):
                continue
            if any(entity.area_id == area.id for entity in entity_registry.entities.values()):
                continue
            if any(device.area_id == area.id for device in device_registry.devices.values()):
                continue
            area_registry.async_delete(area.id)
            del self.area_ids[source_id]
            changed = True
        return changed

    async def async_remove(self):
        """Remove mirrored devices and unused areas on connection deletion."""
        await self.async_load()
        device_registry = dr.async_get(self.hass)
        for device in self._mirrored_devices(device_registry):
            device_registry.async_remove_device(device.id)

        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        self._prune_empty_areas(area_registry, device_registry, entity_registry)
        await self.store.async_remove()

    async def async_sync(self, source_entities, source_devices, source_areas, active_ids):
        """Attach imported entities to locally mirrored devices and areas."""
        areas = {
            item["area_id"]: item
            for item in source_areas
            if isinstance(item, dict)
            and isinstance(item.get("area_id"), str)
            and isinstance(item.get("name"), str)
        }
        devices = {
            item["id"]: item
            for item in source_devices
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        entities = {
            item["entity_id"]: item
            for item in source_entities
            if isinstance(item, dict) and isinstance(item.get("entity_id"), str)
        }
        area_registry = ar.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        changed = False
        local_devices = {}
        used_areas = set()

        def local_area(source_id):
            nonlocal changed
            if source_id not in areas:
                return None

            source = areas[source_id]
            used_areas.add(source_id)
            desired_name = f"{self.entry.title}: {source['name']}"
            saved = self.area_ids.get(source_id, {})
            existing = area_registry.async_get_area(saved.get("id"))
            if existing is not None:
                # A locally renamed area belongs to the user; keep its name.
                if existing.name == saved.get("name") and existing.name != desired_name:
                    try:
                        existing = area_registry.async_update(existing.id, name=desired_name)
                    except ValueError:
                        _LOGGER.warning("Cannot rename remote area %s", source_id)
                    else:
                        self.area_ids[source_id] = {"id": existing.id, "name": desired_name}
                        changed = True
                return existing.id

            try:
                existing = area_registry.async_create(desired_name)
            except ValueError:
                # Never silently assign remote entities to an unrelated local area.
                unique_name = f"{desired_name} ({self.entry.unique_id[:8]}-{source_id})"
                try:
                    existing = area_registry.async_create(unique_name)
                except ValueError:
                    _LOGGER.warning("Cannot create remote area %s", source_id)
                    return None
            self.area_ids[source_id] = {"id": existing.id, "name": existing.name}
            changed = True
            return existing.id

        def local_device(source_id, visiting=None):
            if source_id in local_devices:
                return local_devices[source_id]
            source = devices.get(source_id)
            if source is None:
                return None
            visiting = visiting or set()
            if source_id in visiting:
                _LOGGER.warning("Cycle in remote device parents at %s", source_id)
                return None
            visiting.add(source_id)
            # Newer HA versions also expose child devices with a parent ID.
            parent_id = source.get("parent_device_id") or source.get("via_device_id")
            parent = local_device(parent_id, visiting) if parent_id else None
            visiting.remove(source_id)

            identifier = (DOMAIN, f"{self.entry.unique_id}_{source_id}")
            kwargs = {
                "config_entry_id": self.entry.entry_id,
                "identifiers": {identifier},
                "name": source.get("name_by_user") or source.get("name"),
                "manufacturer": source.get("manufacturer"),
                "model": source.get("model"),
                "hw_version": source.get("hw_version"),
                "sw_version": source.get("sw_version"),
                "via_device_id": parent,
            }
            device = device_registry.async_get_or_create(**kwargs)
            area_id = local_area(source.get("area_id"))
            if area_id is not None and device.area_id != area_id:
                device_registry.async_update_device(device.id, area_id=area_id)
            local_devices[source_id] = device.id
            return device.id

        for source_id in active_ids:
            source = entities.get(source_id)
            if source is None:
                continue
            local_id = self.prefixed_entity_id(source_id)
            domain = local_id.split(".", 1)[0]
            unique_id = f"{self.entry.unique_id[:16]}_{local_id}"
            entity_id = entity_registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if entity_id is None:
                continue

            device_id = local_device(source.get("device_id")) if source.get("device_id") else None
            area_id = local_area(source.get("area_id"))
            entry = entity_registry.async_get(entity_id)
            update = {}
            if device_id is not None and entry.device_id != device_id:
                update["device_id"] = device_id
            if area_id is not None and entry.area_id != area_id:
                update["area_id"] = area_id
            if update:
                entity_registry.async_update_entity(entity_id, **update)

        used_device_ids = set(local_devices.values())
        for device in self._mirrored_devices(device_registry):
            if device.id not in used_device_ids and not any(
                entity.device_id == device.id
                for entity in entity_registry.entities.values()
            ):
                device_registry.async_remove_device(device.id)
        changed |= self._prune_empty_areas(
            area_registry, device_registry, entity_registry, used_areas
        )
        if changed:
            await self.store.async_save({"areas": self.area_ids})
