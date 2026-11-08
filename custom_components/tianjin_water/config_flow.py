"""UI setup, credential replacement, and reauthentication."""

import re
from collections.abc import Mapping
from typing import Any

import aiohttp
import probatio as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    AuthenticationError,
    ConnectionError,
    ProtocolError,
    WaterClient,
    normalize_session,
    opaque_id,
)
from .const import (
    CONF_CUSTOM_ID,
    CONF_SESSION,
    CONF_STOPPED,
    CONF_USER_AGENT,
    DEFAULT_USER_AGENT,
    DOMAIN,
)


def credential_schema(*, include_account: bool) -> vol.Schema:
    """Never prefill saved credentials into a form."""
    fields = {}
    if include_account:
        fields[vol.Required(CONF_CUSTOM_ID)] = TextSelector()
    fields[vol.Required(CONF_SESSION)] = TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )
    fields[vol.Optional(CONF_USER_AGENT)] = TextSelector()
    return vol.Schema(fields)


class TianjinWaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a single billing account, with multiple meters."""

    VERSION = 1

    async def _validate(self, data: dict[str, str]) -> str | None:
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
        )
        try:
            await WaterClient(
                session, data[CONF_CUSTOM_ID], data[CONF_SESSION], data[CONF_USER_AGENT]
            ).balance()
        except AuthenticationError:
            return "invalid_auth"
        except ConnectionError:
            return "cannot_connect"
        except ProtocolError:
            return "unexpected_response"
        finally:
            # HA owns the shared connector. Detach this short-lived session.
            session.detach()
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._credentials_step("user", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._credentials_step("reauth_confirm", user_input, self._get_reauth_entry())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._credentials_step(
            "reconfigure", user_input, self._get_reconfigure_entry()
        )

    async def _credentials_step(
        self, step: str, user_input: dict[str, Any] | None, entry: ConfigEntry | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                custom_id = str(
                    entry.data[CONF_CUSTOM_ID] if entry else user_input[CONF_CUSTOM_ID]
                ).strip()
                if not re.fullmatch(r"\d{1,40}", custom_id):
                    raise ValueError("invalid_account")
                credential = normalize_session(user_input[CONF_SESSION])
                user_agent = user_input.get(CONF_USER_AGENT, "").strip() or (
                    entry.data.get(CONF_USER_AGENT, DEFAULT_USER_AGENT)
                    if entry
                    else DEFAULT_USER_AGENT
                )
                if len(user_agent) > 1024 or any(
                    ord(char) < 32 or ord(char) == 127 for char in user_agent
                ):
                    raise ValueError("invalid_user_agent")
                data = {
                    CONF_CUSTOM_ID: custom_id,
                    CONF_SESSION: credential,
                    CONF_USER_AGENT: user_agent,
                }
                await self.async_set_unique_id(opaque_id(custom_id))
                if entry is None:
                    self._abort_if_unique_id_configured()
                elif (
                    entry.data.get(CONF_STOPPED) == "invalid_auth"
                    and credential == entry.data[CONF_SESSION]
                ):
                    raise ValueError("same_expired_session")
            except ValueError as err:
                errors["base"] = str(err)
            else:
                if error := await self._validate(data):
                    errors["base"] = error
                elif entry is not None:
                    # Replace data to clear the persisted stop/failure state.
                    return self.async_update_reload_and_abort(entry, data=data)
                else:
                    return self.async_create_entry(title="天津自来水", data=data)
        return self.async_show_form(
            step_id=step,
            data_schema=credential_schema(include_account=entry is None),
            errors=errors,
        )
