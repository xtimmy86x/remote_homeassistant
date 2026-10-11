"""Check the Italian translations shown in the Home Assistant UI."""

import pytest
from homeassistant.helpers.translation import async_get_translations

from custom_components.remote_homeassistant.const import DOMAIN


@pytest.mark.asyncio
async def test_italian_config_flow_is_translated(
    hass, enable_custom_integrations
):
    """Italian users receive localized configuration-flow text."""
    en = await async_get_translations(hass, "en", "config", integrations={DOMAIN})
    it = await async_get_translations(hass, "it", "config", integrations={DOMAIN})
    options_it = await async_get_translations(
        hass, "it", "options", integrations={DOMAIN}
    )

    assert en[f"component.{DOMAIN}.config.abort.reauth_successful"] == (
        "Reauthentication successful. The remote connection is using the new token."
    )
    assert it[f"component.{DOMAIN}.config.abort.reauth_successful"] == (
        "Riautenticazione riuscita. La connessione remota usa il nuovo token."
    )
    assert it[f"component.{DOMAIN}.config.step.connection_details.title"] == (
        "Dettagli della connessione"
    )
    assert options_it[f"component.{DOMAIN}.options.step.events.data.add_new_event"] == (
        "Aggiungi nuovo evento"
    )
