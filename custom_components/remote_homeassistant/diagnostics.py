"""Downloadable diagnostics for a remote Home Assistant connection."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_REMOTE_CONNECTION, DOMAIN, REMOTE_ID


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return operational data without configuration or remote entity details."""
    if entry.unique_id == REMOTE_ID:
        return {
            "connection_state": "not_applicable",
            "imported_entity_count": 0,
            "last_error": None,
        }

    remote_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    remote = remote_data.get(CONF_REMOTE_CONNECTION)
    if remote is None:
        return {
            "connection_state": "not_loaded",
            "imported_entity_count": 0,
            "last_error": None,
        }

    return {
        "connection_state": remote._connection_state,
        "imported_entity_count": len(remote._entities),
        "last_error": remote._last_error,
    }
