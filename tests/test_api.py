"""Exercise the actual aiohttp client with synthetic HTTP responses."""

import asyncio
from decimal import Decimal

import aiohttp
import pytest

from custom_components.tianjin_water.api import (
    AuthenticationError,
    ConnectionError,
    ProtocolError,
    WaterClient,
    normalize_session,
    number,
    parse_history,
    parse_readings,
)
from custom_components.tianjin_water.const import BASE_URL, MAX_RESPONSE_BYTES

from .conftest import (
    ACCOUNT,
    METER_A,
    METER_B,
    SESSION,
    bill,
    history,
    request_count,
    setup_responses,
)


async def test_request_mapping_and_cookie_isolation(http_mock):
    setup_responses(http_mock)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = WaterClient(session, ACCOUNT, SESSION)
        balance = await client.balance()
        meters = await client.meters()
    assert balance.balance == Decimal("73.60")
    assert len(meters) == 2
    calls = [call for group in http_mock.requests.values() for call in group]
    assert [call.kwargs["data"]["customId"] for call in calls] == [
        ACCOUNT,
        ACCOUNT,
        METER_A,
        METER_B,
    ]
    assert all(call.kwargs["headers"]["Cookie"] == f"SESSION={SESSION}" for call in calls)
    assert all(call.kwargs["allow_redirects"] is False for call in calls)


@pytest.mark.parametrize(
    "response",
    [
        {"status": 401},
        {"status": 403},
        {"payload": {"code": "401", "message": "not logged in"}},
        {"status": 302, "headers": {"Location": "https://example.invalid/login/"}},
        {"body": "<html>请重新登录</html>", "content_type": "text/html"},
    ],
)
async def test_auth_stops_concurrent_requests(http_mock, response):
    http_mock.post(BASE_URL + "/waterPayment/customBill", **response)
    async with aiohttp.ClientSession() as session:
        client = WaterClient(session, ACCOUNT, SESSION)
        results = await asyncio.gather(client.balance(), client.balance(), return_exceptions=True)
        assert all(isinstance(result, AuthenticationError) for result in results)
    assert request_count(http_mock) == 1


@pytest.mark.parametrize(
    "response",
    [
        {
            "status": 404,
            "headers": {"Set-Cookie": "SESSION=new_anonymous_session; Path=/"},
            "payload": {"status": 404},
        },
        {"body": ""},
        {"payload": []},
        {"payload": {"code": 200, "data": None}},
        {"body": "<html>Internal Server Error</html>"},
        {"status": 302, "headers": {"Location": "https://example.invalid/unknown"}},
        {"body": "x" * (MAX_RESPONSE_BYTES + 1)},
    ],
)
async def test_ambiguous_responses_are_not_called_auth_expiry(http_mock, response):
    http_mock.post(BASE_URL + "/waterPayment/customBill", **response)
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = WaterClient(session, ACCOUNT, SESSION)
        with pytest.raises(ProtocolError):
            await client.balance()
        with pytest.raises(ProtocolError):
            await client.balance()
        assert not list(session.cookie_jar)
    assert request_count(http_mock) == 1


@pytest.mark.parametrize(
    "response", [{"status": 429}, {"status": 503}, {"exception": TimeoutError()}]
)
async def test_transient_failure_is_not_credential_expiry(http_mock, response):
    http_mock.post(BASE_URL + "/waterPayment/customBill", **response)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ConnectionError):
            await WaterClient(session, ACCOUNT, SESSION).balance()


async def test_mismatched_account(http_mock):
    data = bill()
    data["data"]["customId"] = "9999999999"
    http_mock.post(BASE_URL + "/waterPayment/customBill", payload=data)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ProtocolError, match="account_mismatch"):
            await WaterClient(session, ACCOUNT, SESSION).balance()


def test_history_order_and_empty_history():
    rows = history()["data"]
    rows.append({**rows[0], "meterReadingDate": "2024-04-02", "waterIndex": "92"})
    assert parse_readings(rows).index == 95
    assert parse_readings(list(reversed(rows))).index == 95
    assert parse_readings([]) is None
    rows.append({**rows[0], "waterIndex": "999"})
    with pytest.raises(ProtocolError, match="conflicting"):
        parse_readings(rows)


def test_chart_history_is_sorted_deduplicated_and_bounded():
    from datetime import date, timedelta

    rows = [
        {
            **history()["data"][0],
            "meterReadingDate": (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
        }
        for i in range(60)
    ]
    readings = parse_history([*reversed(rows), rows[-1]])
    assert len(readings) == 48
    assert readings[0].date == date(2024, 1, 13)
    assert readings[-1].date == date(2024, 2, 29)
    assert parse_history([]) == ()


@pytest.mark.parametrize("value", [None, True, "", "NaN", "Infinity", [], {}])
def test_invalid_numbers_never_become_zero(value):
    with pytest.raises(ProtocolError):
        number(value)


def test_session_input():
    assert normalize_session(f"Cookie: other=abc; SESSION={SESSION}; something=x") == SESSION
    assert normalize_session(SESSION) == SESSION
    with pytest.raises(ValueError):
        normalize_session("bad\r\ninjection")
