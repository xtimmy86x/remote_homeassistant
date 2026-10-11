"""Config flow for Remote Home-Assistant integration."""
from __future__ import annotations
import logging
import enum
from typing import Any, Mapping

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries, core
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_VERIFY_SSL,
    CONF_TYPE,
)
from homeassistant.helpers.instance_id import async_get
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from . import async_yaml_to_config_entry
from .const import (
    CONF_MAIN,
    CONF_OPTIONS,
    CONF_REMOTE,
    CONF_IMPORT_ALL,
    CONF_SECURE,
    CONF_MAX_MSG_SIZE,
    DOMAIN,
    REMOTE_ID,
    DEFAULT_MAX_MSG_SIZE,
)
from .rest_api import (
    ApiProblem,
    BadResponse,
    CannotConnect,
    EndpointMissing,
    InvalidAuth,
    UnsupportedVersion,
    async_get_discovery_info,
    async_get_remote_entity_ids,
)

_LOGGER = logging.getLogger(__name__)

async def validate_input(hass: core.HomeAssistant, conf):
    """Validate the user input allows us to connect."""
    try:
        info = await async_get_discovery_info(
            hass,
            conf[CONF_HOST],
            conf[CONF_PORT],
            conf.get(CONF_SECURE, False),
            conf[CONF_ACCESS_TOKEN],
            conf.get(CONF_VERIFY_SSL, False),
        )
    except OSError as exc:
        raise CannotConnect() from exc

    return {"title": info["location_name"], "uuid": info["uuid"]}


class InstanceType(enum.Enum):
    """Possible options for instance type."""

    remote = "Setup as remote node"
    main = "Add a remote"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Remote Home-Assistant."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize a new ConfigFlow."""
        self.prefill = {
            CONF_PORT: 8123,
            CONF_SECURE: True,
            CONF_MAX_MSG_SIZE: DEFAULT_MAX_MSG_SIZE,
        }

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Ask for a replacement token when the remote rejects the current one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate the new token against the same remote installation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            data = {**entry.data, CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN]}
            try:
                info = await validate_input(self.hass, data)
                await async_get_remote_entity_ids(
                    self.hass,
                    data[CONF_HOST],
                    data[CONF_PORT],
                    data.get(CONF_SECURE, False),
                    data[CONF_ACCESS_TOKEN],
                    data.get(CONF_VERIFY_SSL, True),
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except EndpointMissing:
                errors["base"] = "missing_endpoint"
            except UnsupportedVersion:
                errors["base"] = "unsupported_version"
            except (ApiProblem, BadResponse):
                errors["base"] = "api_problem"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error validating replacement token")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["uuid"])
                self._abort_if_unique_id_mismatch(reason="wrong_instance")
                # The existing update listener reloads the entry once.
                return self.async_update_and_abort(
                    entry,
                    data_updates={CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_ACCESS_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }),
            errors=errors,
        )

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input[CONF_TYPE] == CONF_REMOTE:
                await self.async_set_unique_id(REMOTE_ID)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Remote instance", data=user_input
                )

            elif user_input[CONF_TYPE] == CONF_MAIN:
                return await self.async_step_connection_details()

            errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_TYPE): vol.In([CONF_REMOTE, CONF_MAIN])}
            ),
            errors=errors,
        )

    async def async_step_connection_details(self, user_input=None):
        """Handle the connection details step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except (ApiProblem, BadResponse):
                errors["base"] = "api_problem"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except UnsupportedVersion:
                errors["base"] = "unsupported_version"
            except EndpointMissing:
                errors["base"] = "missing_endpoint"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["uuid"])
                self._abort_if_unique_id_configured()
                # New entries import nothing until explicitly configured in the panel.
                return self.async_create_entry(
                    title=info["title"],
                    data={**user_input, CONF_OPTIONS: {CONF_IMPORT_ALL: False}},
                )

        user_input = user_input or {}
        host = user_input.get(CONF_HOST, self.prefill.get(CONF_HOST) or vol.UNDEFINED)
        port = user_input.get(CONF_PORT, self.prefill.get(CONF_PORT) or vol.UNDEFINED)
        secure = user_input.get(
            CONF_SECURE, self.prefill.get(CONF_SECURE) or vol.UNDEFINED
        )
        max_msg_size = user_input.get(
            CONF_MAX_MSG_SIZE, self.prefill.get(CONF_MAX_MSG_SIZE) or vol.UNDEFINED
        )
        return self.async_show_form(
            step_id="connection_details",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=host): str,
                    vol.Required(CONF_PORT, default=port): int,
                    vol.Required(
                        CONF_ACCESS_TOKEN,
                        default=user_input.get(CONF_ACCESS_TOKEN, vol.UNDEFINED),
                    ): str,
                    vol.Required(CONF_MAX_MSG_SIZE, default=max_msg_size): int,
                    vol.Optional(CONF_SECURE, default=secure): bool,
                    vol.Optional(
                        CONF_VERIFY_SSL,
                        default=user_input.get(CONF_VERIFY_SSL, True),
                    ): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info):
        """Handle instance discovered via zeroconf."""
        properties = discovery_info.properties
        port = discovery_info.port
        uuid = properties["uuid"]

        await self.async_set_unique_id(uuid)
        self._abort_if_unique_id_configured()

        if await async_get(self.hass) == uuid:
            return self.async_abort(reason="already_configured")

        url = properties.get("internal_url")
        if not url:
            url = properties.get("base_url")
        url = urlparse(url)

        self.prefill = {
            CONF_HOST: url.hostname,
            CONF_PORT: port,
            CONF_SECURE: url.scheme == "https",
        }

        # pylint: disable=no-member # https://github.com/PyCQA/pylint/issues/3167
        self.context["identifier"] = self.unique_id
        self.context["title_placeholders"] = {"name": properties["location_name"]}
        return await self.async_step_connection_details()

    async def async_step_import(self, user_input):
        """Handle import from YAML."""
        try:
            info = await validate_input(self.hass, user_input)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("import of %s failed", user_input[CONF_HOST])
            return self.async_abort(reason="import_failed")

        conf, options = async_yaml_to_config_entry(user_input)

        # Options cannot be set here, so store them in a special key and import them
        # before setting up an entry
        conf[CONF_OPTIONS] = options

        await self.async_set_unique_id(info["uuid"])
        self._abort_if_unique_id_configured(updates=conf)

        return self.async_create_entry(title=f"{info['title']} (YAML)", data=conf)
