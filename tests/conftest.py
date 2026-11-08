"""Synthetic data only; real networking is disabled by pytest-socket."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import aiohttp
import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tianjin_water.api import opaque_id
from custom_components.tianjin_water.const import BASE_URL, DOMAIN

ACCOUNT = "1000000001"
SESSION = "synthetic_session_for_tests_000001"
METER_A = "2000000001"
METER_B = "2000000002"


@pytest.fixture(autouse=True)
def enable_integration(enable_custom_integrations):
    """Let the real HA loader discover this custom integration."""


@pytest.fixture
def entry(hass):
    value = MockConfigEntry(
        domain=DOMAIN,
        title="天津自来水",
        unique_id=opaque_id(ACCOUNT),
        data={"custom_id": ACCOUNT, "session": SESSION},
    )
    value.add_to_hass(hass)
    return value


@pytest.fixture
def http_mock():
    # aioresponses 0.7.9 predates aiohttp 3.14's required stream_writer
    # constructor argument. Keep real ClientResponse/StreamReader behavior;
    # only supply the missing constructor default in the test transport.
    class Response(aiohttp.ClientResponse):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("stream_writer", SimpleNamespace(output_size=0))
            super().__init__(*args, **kwargs)

    class MockHTTP(aioresponses):
        def add(self, *args, **kwargs):
            kwargs.setdefault("response_class", Response)
            return super().add(*args, **kwargs)

    def stream_reader(loop):
        # The mock has no HTTP parser/transport; avoid real protocol backpressure
        # callbacks when feeding the deliberately oversized synthetic response.
        return aiohttp.StreamReader(Mock(_reading_paused=False), limit=65536, loop=loop)

    with (
        patch("aioresponses.core.stream_reader_factory", side_effect=stream_reader),
        MockHTTP() as mock,
    ):
        yield mock


def bill():
    return {
        "code": 200,
        "data": {"customId": ACCOUNT, "prepayBalance": "73.60", "totalAmount": "0.00"},
    }


def history(index="95", consumption="3"):
    return {
        "code": 200,
        "data": [
            {
                "meterReadingDate": "2024-06-03",
                "waterIndex": index,
                "waterConsumption": consumption,
                "waterRate": "14.70",
            }
        ],
    }


def setup_responses(mock, meters=None):
    """Balance, account meters, and two meter-specific histories."""
    mock.post(BASE_URL + "/waterPayment/customBill", payload=bill())
    if meters is None:
        meters = [
            {"registerNo": METER_A, "meterKind": "NB"},
            {"registerNo": METER_B, "meterKind": "NB"},
        ]
    mock.post(
        BASE_URL + "/waterService/customBillMeter",
        payload={"code": 200, "data": {"waterMeterList": meters}},
    )
    for meter in meters:
        if meter["meterKind"] == "NB":
            mock.post(
                BASE_URL + "/waterService/getUseWater",
                payload=history("95" if meter["registerNo"] == METER_A else "0"),
            )


def request_count(mock):
    return sum(len(calls) for calls in mock.requests.values())
