"""Admin-only sidebar for configuring remote Home Assistant connections."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import (
    CONF_ABOVE,
    CONF_BELOW,
    CONF_ENTITY_ID,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify

from .const import (
    CONF_ENTITY_FRIENDLY_NAME_PREFIX,
    CONF_ENTITY_PREFIX,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_FILTER,
    CONF_IMPORT_ALL,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_ENTITIES,
    CONF_LOAD_COMPONENTS,
    CONF_REMOTE_CONNECTION,
    CONF_SERVICE_PREFIX,
    CONF_SERVICES,
    CONF_SUBSCRIBE_EVENTS,
    DOMAIN,
    REMOTE_ID,
)
from .rest_api import async_get_remote_entity_ids

PANEL_PATH = "remote-homeassistant"
PANEL_JS_PATH = f"/api/{DOMAIN}/panel.js"

_LIST = vol.All([cv.string], vol.Length(max=10000))
_FILTER = vol.Schema(
    {
        vol.Optional(CONF_ENTITY_ID): cv.string,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
        vol.Optional(CONF_ABOVE): vol.Coerce(float),
        vol.Optional(CONF_BELOW): vol.Coerce(float),
    },
    extra=vol.PREVENT_EXTRA,
)
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMPORT_ALL): cv.boolean,
        vol.Required(CONF_INCLUDE_DOMAINS): _LIST,
        vol.Required(CONF_INCLUDE_ENTITIES): _LIST,
        vol.Required(CONF_EXCLUDE_DOMAINS): _LIST,
        vol.Required(CONF_EXCLUDE_ENTITIES): _LIST,
        vol.Required(CONF_LOAD_COMPONENTS): _LIST,
        vol.Required(CONF_SERVICES): _LIST,
        vol.Required(CONF_SUBSCRIBE_EVENTS): _LIST,
        vol.Required(CONF_FILTER): vol.All([_FILTER], vol.Length(max=100)),
        vol.Required(CONF_ENTITY_PREFIX): cv.string,
        vol.Required(CONF_ENTITY_FRIENDLY_NAME_PREFIX): cv.string,
        vol.Required(CONF_SERVICE_PREFIX): cv.string,
    },
    extra=vol.PREVENT_EXTRA,
)


def _entry(hass: HomeAssistant, entry_id: str):
    """Only expose real remote connection entries."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.unique_id == REMOTE_ID:
        return None
    return entry


def _revision(entry) -> str:
    """Detect conflicting edits from a second browser tab."""
    data = json.dumps(dict(entry.options), sort_keys=True, separators=(",", ":"))
    return sha256(data.encode()).hexdigest()


def _panel_options(entry) -> dict[str, Any]:
    """Expose settings only; never send connection credentials to the panel."""
    options = entry.options
    return {
        CONF_IMPORT_ALL: options.get(
            CONF_IMPORT_ALL,
            not (options.get(CONF_INCLUDE_DOMAINS) or options.get(CONF_INCLUDE_ENTITIES)),
        ),
        CONF_INCLUDE_DOMAINS: list(options.get(CONF_INCLUDE_DOMAINS, [])),
        CONF_INCLUDE_ENTITIES: list(options.get(CONF_INCLUDE_ENTITIES, [])),
        CONF_EXCLUDE_DOMAINS: list(options.get(CONF_EXCLUDE_DOMAINS, [])),
        CONF_EXCLUDE_ENTITIES: list(options.get(CONF_EXCLUDE_ENTITIES, [])),
        CONF_LOAD_COMPONENTS: list(options.get(CONF_LOAD_COMPONENTS, [])),
        CONF_SERVICES: list(options.get(CONF_SERVICES, [])),
        CONF_SUBSCRIBE_EVENTS: list(options.get(CONF_SUBSCRIBE_EVENTS, [])),
        CONF_FILTER: [dict(item) for item in options.get(CONF_FILTER, [])],
        CONF_ENTITY_PREFIX: options.get(CONF_ENTITY_PREFIX, ""),
        CONF_ENTITY_FRIENDLY_NAME_PREFIX: options.get(CONF_ENTITY_FRIENDLY_NAME_PREFIX, ""),
        CONF_SERVICE_PREFIX: options.get(CONF_SERVICE_PREFIX) or slugify(entry.title),
    }


def _save_options(hass: HomeAssistant, entry, options: dict, revision: str) -> bool:
    """Validate and persist a complete edit without changing other options."""
    if _revision(entry) != revision:
        return False
    validated = OPTIONS_SCHEMA(options)
    hass.config_entries.async_update_entry(entry, options={**entry.options, **validated})
    return True


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/panel/list"})
@callback
def ws_list(hass, connection, msg):
    """List connection entries without their credentials."""
    connection.send_result(
        msg["id"],
        [
            {"entry_id": entry.entry_id, "title": entry.title}
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id != REMOTE_ID
        ],
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/panel/get", vol.Required("entry_id"): str}
)
@websocket_api.async_response
async def ws_get(hass, connection, msg):
    """Fetch available remote choices and current options for one entry."""
    if (entry := _entry(hass, msg["entry_id"])) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Connection not found")
        return

    remote = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(CONF_REMOTE_CONNECTION)
    entities = set(remote._all_entity_names) if remote is not None else set()
    if not entities:
        try:
            entities.update(await async_get_remote_entity_ids(
                hass,
                entry.data["host"],
                entry.data["port"],
                entry.data.get("secure", False),
                entry.data["access_token"],
                entry.data.get("verify_ssl", True),
            ))
        except Exception:  # Connection status is already reported separately.
            pass

    options = _panel_options(entry)
    entities.update(options[CONF_INCLUDE_ENTITIES])
    entities.update(options[CONF_EXCLUDE_ENTITIES])
    domains = {entity_id.split(".", 1)[0] for entity_id in entities if "." in entity_id}
    domains.update(options[CONF_INCLUDE_DOMAINS])
    domains.update(options[CONF_EXCLUDE_DOMAINS])
    domains.update(options[CONF_LOAD_COMPONENTS])
    services = set(remote.proxy_services.services) if remote is not None else set()
    services.update(options[CONF_SERVICES])

    connection.send_result(msg["id"], {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "revision": _revision(entry),
        "status": remote._connection_state if remote is not None else "not_loaded",
        "entities": sorted(entities),
        "domains": sorted(domains),
        "services": sorted(services),
        "options": options,
    })


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/save",
        vol.Required("entry_id"): str,
        vol.Required("revision"): str,
        vol.Required("options"): dict,
    }
)
@callback
def ws_save(hass, connection, msg):
    """Save settings from the panel; the config entry listener reloads once."""
    if (entry := _entry(hass, msg["entry_id"])) is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Connection not found")
        return
    try:
        saved = _save_options(hass, entry, msg["options"], msg["revision"])
    except vol.Invalid:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Invalid options")
        return
    if not saved:
        connection.send_error(msg["id"], "conflict", "Settings changed; reload the panel")
        return
    connection.send_result(msg["id"], {"revision": _revision(entry)})


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the sidebar and WebSocket commands once per HA instance."""
    await hass.http.async_register_static_paths([
        StaticPathConfig(PANEL_JS_PATH, str(Path(__file__).parent / "panel.js"), True)
    ])
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name="remote-homeassistant-panel",
        sidebar_title="Remote Home Assistant",
        sidebar_icon="mdi:home-network",
        module_url=PANEL_JS_PATH,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    for handler in (ws_list, ws_get, ws_save):
        websocket_api.async_register_command(hass, handler)
