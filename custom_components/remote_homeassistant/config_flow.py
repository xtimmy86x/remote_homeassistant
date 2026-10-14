"""Config flow for Remote Home-Assistant integration."""
from __future__ import annotations
import logging
from typing import Any, Mapping

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries, core
from homeassistant.config_entries import SOURCE_IMPORT, ConfigSubentryFlow
from homeassistant.const import (
    CONF_ABOVE,
    CONF_ACCESS_TOKEN,
    CONF_BELOW,
    CONF_ENTITY_ID,
    CONF_HOST,
    CONF_PORT,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_VERIFY_SSL,
    CONF_TYPE,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.instance_id import async_get
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from . import async_entry_filters, async_yaml_to_config_entry, entity_selected, filter_title
from .const import (
    CONF_ENTITY_PREFIX,  # pylint:disable=unused-import
    CONF_ENTITY_FRIENDLY_NAME_PREFIX,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_ENTITIES,
    CONF_LOAD_COMPONENTS,
    CONF_OPTIONS,
    CONF_REMOTE,
    CONF_REMOTE_CONNECTION,
    CONF_SECURE,
    CONF_SERVICE_PREFIX,
    CONF_SERVICES,
    CONF_MAX_MSG_SIZE,
    CONF_SUBSCRIBE_EVENTS,
    DOMAIN,
    REMOTE_ID,
    DEFAULT_MAX_MSG_SIZE,
    SUBENTRY_TYPE_FILTER,
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

CONF_IMPORT_ALL = "import_all"
CONF_ADVANCED = "advanced"
CONF_INCLUDE = "include"
CONF_EXCLUDE = "exclude"

ADVANCED_CONNECTION_OPTIONS = (CONF_VERIFY_SSL, CONF_MAX_MSG_SIZE)
FILTER_OPTIONS = [CONF_ENTITY_ID, CONF_UNIT_OF_MEASUREMENT, CONF_ABOVE, CONF_BELOW]


def _searchable_multi_select(options, *, custom_value=False):
    """Show a searchable multi-picker for values from the remote instance."""
    return SelectSelector(
        SelectSelectorConfig(
            options=sorted(set(options)),
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=custom_value,
        )
    )


def _connection_schema(*, token_required: bool) -> vol.Schema:
    """Return connection fields, with rarely changed ones in a folded section."""
    token = vol.Required if token_required else vol.Optional
    return vol.Schema(
        {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT): int,
            token(CONF_ACCESS_TOKEN): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Optional(CONF_SECURE): bool,
            vol.Required(CONF_ADVANCED): section(
                vol.Schema(
                    {
                        vol.Optional(CONF_VERIFY_SSL): bool,
                        vol.Required(CONF_MAX_MSG_SIZE): int,
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _connection_values(conf: Mapping[str, Any]) -> dict[str, Any]:
    """Nest stored connection data the way the connection form shows it."""
    values = {
        key: value
        for key, value in conf.items()
        if key in (CONF_HOST, CONF_PORT, CONF_ACCESS_TOKEN, CONF_SECURE)
    }
    values[CONF_ADVANCED] = {
        key: conf[key] for key in ADVANCED_CONNECTION_OPTIONS if key in conf
    }
    return values


def _flatten_connection(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten connection form input into config entry data."""
    data = {k: v for k, v in user_input.items() if k != CONF_ADVANCED}
    data.update(user_input.get(CONF_ADVANCED, {}))
    data.setdefault(CONF_SECURE, False)
    data.setdefault(CONF_VERIFY_SSL, False)
    return data


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


async def _async_validate_connection(hass: core.HomeAssistant, conf):
    """Check discovery and state access, returning errors or remote details."""
    try:
        info = await validate_input(hass, conf)
        entities = await async_get_remote_entity_ids(
            hass,
            conf[CONF_HOST],
            conf[CONF_PORT],
            conf.get(CONF_SECURE, False),
            conf[CONF_ACCESS_TOKEN],
            conf.get(CONF_VERIFY_SSL, True),
        )
    except InvalidAuth:
        return {"base": "invalid_auth"}, None, []
    except CannotConnect:
        return {"base": "cannot_connect"}, None, []
    except EndpointMissing:
        return {"base": "missing_endpoint"}, None, []
    except UnsupportedVersion:
        return {"base": "unsupported_version"}, None, []
    except (ApiProblem, BadResponse):
        return {"base": "api_problem"}, None, []
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected error validating the remote connection")
        return {"base": "unknown"}, None, []
    return {}, info, entities


@callback
def _remote_connection(hass: core.HomeAssistant, entry_id: str):
    """Return the running connection of an entry, if it is loaded."""
    return hass.data.get(DOMAIN, {}).get(entry_id, {}).get(CONF_REMOTE_CONNECTION)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Remote Home-Assistant."""

    VERSION = 1
    MINOR_VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize a new ConfigFlow."""
        self.prefill = {
            CONF_PORT: 8123,
            CONF_SECURE: True,
            CONF_VERIFY_SSL: True,
            CONF_MAX_MSG_SIZE: DEFAULT_MAX_MSG_SIZE,
        }
        self._connection_data: dict[str, Any] | None = None
        self._remote_title: str | None = None
        self._remote_entities: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow for this handler."""
        # config_entry internally set by OptionsFlow (read-only Property)
        return OptionsFlowHandler()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry) -> bool:
        """Hide the configure button on the remote node entry."""
        return config_entry.unique_id != REMOTE_ID

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        """Offer attribute filters on connections to a remote instance."""
        if config_entry.unique_id == REMOTE_ID:
            return {}
        return {SUBENTRY_TYPE_FILTER: FilterSubentryFlowHandler}

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Ask for a replacement token when the remote rejects the current one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Validate the new token against the same remote installation."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            data = {**entry.data, CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN]}
            errors, info, _ = await _async_validate_connection(self.hass, data)
            if not errors:
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

    async def async_step_reconfigure(self, user_input=None):
        """Change host, port, token or TLS settings of an existing connection."""
        entry = self._get_reconfigure_entry()
        if entry.unique_id == REMOTE_ID:
            return self.async_abort(reason="not_supported")
        if entry.source == SOURCE_IMPORT:
            return self.async_abort(reason="yaml_managed")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **_flatten_connection(user_input)}
            # An empty token field keeps the saved token.
            if not data.get(CONF_ACCESS_TOKEN):
                data[CONF_ACCESS_TOKEN] = entry.data[CONF_ACCESS_TOKEN]
            errors, info, _ = await _async_validate_connection(self.hass, data)
            if not errors:
                await self.async_set_unique_id(info["uuid"])
                self._abort_if_unique_id_mismatch(reason="wrong_instance")
                # The existing update listener reloads the entry once.
                return self.async_update_and_abort(entry, data=data)

        values = _connection_values(
            {**entry.data, **(_flatten_connection(user_input) if user_input else {})}
        )
        values.pop(CONF_ACCESS_TOKEN, None)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(token_required=False), values
            ),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_user(self, user_input=None):
        """Let the user choose which side of the connection this instance is."""
        return self.async_show_menu(
            step_id="user", menu_options=["connection_details", "remote_node"]
        )

    async def async_step_remote_node(self, user_input=None):
        """Allow other instances to connect to this one."""
        await self.async_set_unique_id(REMOTE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Remote instance", data={CONF_TYPE: CONF_REMOTE}
        )

    async def async_step_connection_details(self, user_input=None):
        """Handle the connection details step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _flatten_connection(user_input)
            errors, info, entities = await _async_validate_connection(self.hass, data)
            if not errors:
                await self.async_set_unique_id(info["uuid"])
                self._abort_if_unique_id_configured()
                self._connection_data = data
                self._remote_title = info["title"]
                self._remote_entities = entities
                return await self.async_step_entity_selection()

        values = _connection_values(
            {**self.prefill, **(_flatten_connection(user_input) if user_input else {})}
        )
        return self.async_show_form(
            step_id="connection_details",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(token_required=True), values
            ),
            errors=errors,
        )

    async def async_step_entity_selection(self, user_input=None):
        """Choose which remote entities are imported on the first connection."""
        if self._connection_data is None or self._remote_title is None:
            return await self.async_step_connection_details()

        errors: dict[str, str] = {}
        if user_input is not None:
            import_all = user_input.get(CONF_IMPORT_ALL, False)
            include = user_input.get(CONF_INCLUDE, {})
            exclude = user_input.get(CONF_EXCLUDE, {})
            include_domains = include.get(CONF_INCLUDE_DOMAINS, [])
            include_entities = include.get(CONF_INCLUDE_ENTITIES, [])

            if not import_all and not (include_domains or include_entities):
                errors["base"] = "select_entities"
            else:
                options = {
                    CONF_INCLUDE_DOMAINS: [] if import_all else include_domains,
                    CONF_INCLUDE_ENTITIES: [] if import_all else include_entities,
                    CONF_EXCLUDE_DOMAINS: exclude.get(CONF_EXCLUDE_DOMAINS, []),
                    CONF_EXCLUDE_ENTITIES: exclude.get(CONF_EXCLUDE_ENTITIES, []),
                    CONF_ENTITY_PREFIX: user_input.get(CONF_ENTITY_PREFIX, ""),
                    CONF_ENTITY_FRIENDLY_NAME_PREFIX: user_input.get(
                        CONF_ENTITY_FRIENDLY_NAME_PREFIX, ""
                    ),
                }
                # Import options before setup starts, as for YAML entries.
                data = {**self._connection_data, CONF_OPTIONS: options}
                return self.async_create_entry(title=self._remote_title, data=data)

        domains = sorted(
            {entity.split(".", 1)[0] for entity in self._remote_entities}
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_IMPORT_ALL): bool,
                **_include_exclude_schema(domains, self._remote_entities),
                vol.Optional(CONF_ENTITY_PREFIX): str,
                vol.Optional(CONF_ENTITY_FRIENDLY_NAME_PREFIX): str,
            }
        )
        return self.async_show_form(
            step_id="entity_selection",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            description_placeholders={"count": str(len(self._remote_entities))},
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
            CONF_VERIFY_SSL: True,
            CONF_MAX_MSG_SIZE: DEFAULT_MAX_MSG_SIZE,
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


def _include_exclude_schema(domains, entities) -> dict:
    """Return include and exclude pickers grouped in two sections."""
    return {
        vol.Required(CONF_INCLUDE): section(
            vol.Schema(
                {
                    vol.Optional(CONF_INCLUDE_DOMAINS): _searchable_multi_select(domains),
                    vol.Optional(CONF_INCLUDE_ENTITIES): _searchable_multi_select(entities),
                }
            )
        ),
        vol.Required(CONF_EXCLUDE): section(
            vol.Schema(
                {
                    vol.Optional(CONF_EXCLUDE_DOMAINS): _searchable_multi_select(domains),
                    vol.Optional(CONF_EXCLUDE_ENTITIES): _searchable_multi_select(entities),
                }
            ),
            {"collapsed": True},
        ),
    }


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the Home Assistant remote integration.

    Each menu entry saves on its own, so one change needs one form.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Show the option groups together with what they currently select."""
        if self.config_entry.unique_id == REMOTE_ID:
            return self.async_abort(reason="not_supported")

        return self.async_show_menu(
            step_id="init",
            menu_options=["entities", "naming", "services", "events"],
            description_placeholders={
                **self._selection_summary(self.config_entry.options),
                "filters": str(len(async_entry_filters(self.config_entry))),
            },
        )

    async def async_step_entities(self, user_input=None):
        """Choose included and excluded domains and entities."""
        if user_input is not None:
            include = user_input.get(CONF_INCLUDE, {})
            exclude = user_input.get(CONF_EXCLUDE, {})
            return self._save(
                {
                    CONF_INCLUDE_DOMAINS: include.get(CONF_INCLUDE_DOMAINS, []),
                    CONF_INCLUDE_ENTITIES: include.get(CONF_INCLUDE_ENTITIES, []),
                    CONF_EXCLUDE_DOMAINS: exclude.get(CONF_EXCLUDE_DOMAINS, []),
                    CONF_EXCLUDE_ENTITIES: exclude.get(CONF_EXCLUDE_ENTITIES, []),
                }
            )

        options = self.config_entry.options
        domains, entities = self._domains_and_entities()
        return self.async_show_form(
            step_id="entities",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_include_exclude_schema(domains, entities)),
                {
                    CONF_INCLUDE: {
                        key: options[key]
                        for key in (CONF_INCLUDE_DOMAINS, CONF_INCLUDE_ENTITIES)
                        if key in options
                    },
                    CONF_EXCLUDE: {
                        key: options[key]
                        for key in (CONF_EXCLUDE_DOMAINS, CONF_EXCLUDE_ENTITIES)
                        if key in options
                    },
                },
            ),
            description_placeholders=self._selection_summary(options),
        )

    async def async_step_naming(self, user_input=None):
        """Set the prefixes added to imported entity IDs and names."""
        if user_input is not None:
            return self._save(
                {
                    CONF_ENTITY_PREFIX: user_input.get(CONF_ENTITY_PREFIX, ""),
                    CONF_ENTITY_FRIENDLY_NAME_PREFIX: user_input.get(
                        CONF_ENTITY_FRIENDLY_NAME_PREFIX, ""
                    ),
                }
            )

        return self.async_show_form(
            step_id="naming",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_ENTITY_PREFIX): str,
                        vol.Optional(CONF_ENTITY_FRIENDLY_NAME_PREFIX): str,
                    }
                ),
                self.config_entry.options,
            ),
        )

    async def async_step_services(self, user_input=None):
        """Choose remote services to proxy and components to load locally."""
        if user_input is not None:
            return self._save(
                {
                    CONF_LOAD_COMPONENTS: user_input.get(CONF_LOAD_COMPONENTS, []),
                    CONF_SERVICE_PREFIX: user_input[CONF_SERVICE_PREFIX],
                    CONF_SERVICES: user_input.get(CONF_SERVICES, []),
                }
            )

        options = self.config_entry.options
        domains, _ = self._domains_and_entities()
        remote = _remote_connection(self.hass, self.config_entry.entry_id)
        services = remote.proxy_services.services if remote else []
        return self.async_show_form(
            step_id="services",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_SERVICE_PREFIX,
                            default=options.get(CONF_SERVICE_PREFIX)
                            or slugify(self.config_entry.title),
                        ): str,
                        vol.Optional(CONF_SERVICES): _searchable_multi_select(
                            services + options.get(CONF_SERVICES, [])
                        ),
                        vol.Optional(CONF_LOAD_COMPONENTS): _searchable_multi_select(
                            domains + options.get(CONF_LOAD_COMPONENTS, [])
                        ),
                    }
                ),
                {
                    key: options[key]
                    for key in (CONF_SERVICES, CONF_LOAD_COMPONENTS)
                    if key in options
                },
            ),
        )

    async def async_step_events(self, user_input=None):
        """Manage event options."""
        if user_input is not None:
            return self._save(
                {CONF_SUBSCRIBE_EVENTS: user_input.get(CONF_SUBSCRIBE_EVENTS, [])}
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="events",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_SUBSCRIBE_EVENTS): _searchable_multi_select(
                            options.get(CONF_SUBSCRIBE_EVENTS, []),
                            custom_value=True,
                        ),
                    }
                ),
                options,
            ),
        )

    @callback
    def _save(self, changes: dict[str, Any]):
        """Store one group of options and keep the others."""
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **changes}
        )

    def _selection_summary(self, options: Mapping[str, Any]) -> dict[str, str]:
        """Count remote entities imported with the given include and exclude lists."""
        remote = _remote_connection(self.hass, self.config_entry.entry_id)
        remote_entities = remote._all_entity_names if remote else set()
        selected = sum(
            entity_selected(
                entity_id,
                set(options.get(CONF_INCLUDE_ENTITIES, [])),
                set(options.get(CONF_INCLUDE_DOMAINS, [])),
                set(options.get(CONF_EXCLUDE_ENTITIES, [])),
                set(options.get(CONF_EXCLUDE_DOMAINS, [])),
            )
            for entity_id in remote_entities
        )
        return {"selected": str(selected), "total": str(len(remote_entities))}

    def _domains_and_entities(self):
        """Return all entities and domains exposed by remote instance."""
        remote = _remote_connection(self.hass, self.config_entry.entry_id)
        options = self.config_entry.options

        # Include entities we have in the config explicitly, otherwise they will be
        # pre-selected and not possible to remove if they are no longer present on
        # the remote host.
        entities = sorted(
            (remote._all_entity_names if remote else set())
            | set(options.get(CONF_INCLUDE_ENTITIES, []))
            | set(options.get(CONF_EXCLUDE_ENTITIES, []))
        )
        domains = sorted(
            {entity_id.split(".")[0] for entity_id in entities}
            | set(options.get(CONF_INCLUDE_DOMAINS, []))
            | set(options.get(CONF_EXCLUDE_DOMAINS, []))
        )
        return domains, entities


class FilterSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one attribute filter of a remote connection."""

    async def async_step_user(self, user_input=None):
        """Add a filter."""
        return await self._async_filter_form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Edit a filter."""
        return await self._async_filter_form("reconfigure", user_input)

    async def _async_filter_form(self, step_id: str, user_input):
        """Show the filter form and store a valid filter."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {key: user_input.get(key) for key in FILTER_OPTIONS}
            if data[CONF_ABOVE] is None and data[CONF_BELOW] is None:
                errors["base"] = "missing_limit"
            elif step_id == "user":
                return self.async_create_entry(title=filter_title(data), data=data)
            else:
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=filter_title(data),
                    data=data,
                )

        if user_input is None and step_id == "reconfigure":
            user_input = dict(self._get_reconfigure_subentry().data)

        remote = _remote_connection(self.hass, self._entry_id)
        entities = sorted(remote._all_entity_names) if remote else []
        number = NumberSelector(
            NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_ENTITY_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=entities,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(CONF_UNIT_OF_MEASUREMENT): str,
                vol.Optional(CONF_ABOVE): number,
                vol.Optional(CONF_BELOW): number,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )
