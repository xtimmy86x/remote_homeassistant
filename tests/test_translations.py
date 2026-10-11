"""Check the reauthentication result shown in the Home Assistant UI."""

import pytest
from homeassistant.helpers.translation import async_get_translations

from custom_components.remote_homeassistant.const import DOMAIN


@pytest.mark.asyncio
async def test_reauth_success_has_italian_and_english_text(
    hass, enable_custom_integrations
):
    """Older Home Assistant versions resolve the abort reason locally."""
    key = f"component.{DOMAIN}.config.abort.reauth_successful"
    en = await async_get_translations(hass, "en", "config", integrations={DOMAIN})
    it = await async_get_translations(hass, "it", "config", integrations={DOMAIN})

    assert en[key] == (
        "Reauthentication successful. The remote connection is using the new token."
    )
    assert it[key] == (
        "Riautenticazione riuscita. La connessione remota usa il nuovo token."
    )
    # Adding a partial Italian file still leaves earlier setup steps readable.
    assert it[f"component.{DOMAIN}.config.step.connection_details.title"] == (
        "Connection details"
    )
