"""UI flows validate, deduplicate, and update existing entries."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tianjin_water.const import BASE_URL, DOMAIN

from .conftest import ACCOUNT, SESSION, bill, request_count


async def test_user_flow(hass, http_mock):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    http_mock.post(BASE_URL + "/waterPayment/customBill", payload=bill())
    with patch("custom_components.tianjin_water.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"custom_id": ACCOUNT, "session": "SESSION=" + SESSION}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["session"] == SESSION
    assert request_count(http_mock) == 1


async def test_duplicate_never_calls_upstream(hass, entry, http_mock):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"custom_id": ACCOUNT, "session": SESSION}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert request_count(http_mock) == 0


@pytest.mark.parametrize(
    "response,error",
    [
        ({"status": 401}, "invalid_auth"),
        ({"status": 404}, "unexpected_response"),
        ({"status": 503}, "cannot_connect"),
    ],
)
async def test_ui_errors_do_not_leak_response(hass, http_mock, response, error):
    http_mock.post(BASE_URL + "/waterPayment/customBill", **response)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={"custom_id": ACCOUNT, "session": SESSION}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_reauth_same_expired_session_does_not_request(hass, entry, http_mock):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "stopped": "invalid_auth"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"session": SESSION})
    assert result["errors"] == {"base": "same_expired_session"}
    assert request_count(http_mock) == 0


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
async def test_replacement_clears_stop_and_keeps_identity(hass, entry, http_mock, source):
    unique_id = entry.unique_id
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "stopped": "invalid_auth", "connection_failures": 2}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": source, "entry_id": entry.entry_id},
        data=entry.data if source == config_entries.SOURCE_REAUTH else None,
    )
    http_mock.post(BASE_URL + "/waterPayment/customBill", payload=bill())
    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"session": SESSION + "new"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert entry.unique_id == unique_id
    assert entry.data["custom_id"] == ACCOUNT
    assert entry.data["session"] == SESSION + "new"
    assert "stopped" not in entry.data
    assert "connection_failures" not in entry.data
    reload.assert_awaited_once_with(entry.entry_id)
