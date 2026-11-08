"""Run through HA's real config-entry, entity, and coordinator lifecycles."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.tianjin_water.api import opaque_id
from custom_components.tianjin_water.const import BASE_URL, DOMAIN, HISTORY_SECONDS
from custom_components.tianjin_water.coordinator import WaterCoordinator
from custom_components.tianjin_water.diagnostics import async_get_config_entry_diagnostics

from .conftest import ACCOUNT, METER_A, METER_B, SESSION, bill, request_count, setup_responses


def entity_id(hass, entry, suffix):
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}_{suffix}")


async def load_entry(hass, entry, mock):
    setup_responses(mock)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry.runtime_data


async def test_setup_entities_cache_only_and_unload(hass, entry, http_mock):
    coordinator = await load_entry(hass, entry, http_mock)
    assert len(hass.states.async_all("sensor")) == 11
    balance = hass.states.get(entity_id(hass, entry, "balance"))
    assert balance.state == "73.60"
    assert balance.attributes["unit_of_measurement"] == "CNY"
    assert "state_class" not in balance.attributes
    reading_id = entity_id(hass, entry, f"{opaque_id(METER_A)}_index")
    reading = hass.states.get(reading_id)
    assert reading.state == "95"
    assert reading.attributes["device_class"] == "water"
    assert reading.attributes["state_class"] == "total"
    assert reading.attributes["unit_of_measurement"] == "m³"
    period = hass.states.get(entity_id(hass, entry, f"{opaque_id(METER_A)}_consumption"))
    assert "state_class" not in period.attributes
    assert period.attributes["readings"] == [
        {"date": "2024-06-03", "index": "95", "consumption": "3", "charge": "14.70"}
    ]
    assert "readings" in period.state_info["unrecorded_attributes"]
    # Reading all entities, and requesting refresh repeatedly inside cooldown,
    # must not multiply the initial four upstream requests.
    for _ in range(3):
        hass.states.async_all()
        await coordinator.async_refresh()
    assert request_count(http_mock) == 4
    assert await hass.config_entries.async_unload(entry.entry_id)
    await coordinator.async_refresh()
    assert request_count(http_mock) == 4


async def test_balance_poll_does_not_refetch_history(hass, entry, http_mock):
    coordinator = await load_entry(hass, entry, http_mock)
    status_id = entity_id(hass, entry, "collection_status")
    before = hass.states.get(status_id).attributes["last_success"]
    http_mock.post(BASE_URL + "/waterPayment/customBill", payload=bill())
    coordinator._attempt_at -= 601
    await coordinator.async_refresh()
    assert request_count(http_mock) == 5
    assert coordinator.last_update_success
    await hass.async_block_till_done()
    after = hass.states.get(status_id)
    assert after.attributes["last_success"] != before
    assert after.attributes["request_count"] == 5


async def test_device_hierarchy_and_entity_ids_survive_reload(hass, entry, http_mock):
    await load_entry(hass, entry, http_mock)
    registry = dr.async_get(hass)
    account = registry.async_get_device_by_identifier((DOMAIN, entry.unique_id), entry.entry_id)
    assert account is not None
    assert account.entry_type is dr.DeviceEntryType.SERVICE
    meters = [
        registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.unique_id}_{opaque_id(meter_id)}"), entry.entry_id
        )
        for meter_id in (METER_A, METER_B)
    ]
    assert all(meter is not None and meter.via_device_id == account.id for meter in meters)
    device_ids = {
        device.id for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert len(device_ids) == 3
    entity_ids = {
        item.unique_id: (item.entity_id, item.device_id)
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }

    setup_responses(http_mock)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert {
        device.id for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
    } == device_ids
    assert {
        item.unique_id: (item.entity_id, item.device_id)
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    } == entity_ids
    for meter in meters:
        assert registry.async_get(meter.id).via_device_id == account.id


@pytest.mark.parametrize(
    "response,reason",
    [
        ({"status": 401}, "invalid_auth"),
        (
            {"status": 404, "headers": {"Set-Cookie": "SESSION=anonymous_replacement; Path=/"}},
            "unexpected_http_status",
        ),
        ({"body": ""}, "unexpected_response"),
    ],
)
async def test_stop_latch_survives_refresh_and_restart(hass, entry, http_mock, response, reason):
    coordinator = await load_entry(hass, entry, http_mock)
    http_mock.post(BASE_URL + "/waterPayment/customBill", **response)
    coordinator._attempt_at -= 601
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.stop_reason == reason
    assert entry.data["stopped"] == reason
    assert entry.data["session"] == SESSION
    assert coordinator.update_interval is None
    assert hass.states.get(entity_id(hass, entry, "balance")).state == "unavailable"
    status = hass.states.get(entity_id(hass, entry, "collection_status")).state
    assert status == ("authentication_required" if reason == "invalid_auth" else "stopped")
    await coordinator.async_refresh()
    assert request_count(http_mock) == 5
    await hass.config_entries.async_unload(entry.entry_id)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert request_count(http_mock) == 5


async def test_three_transient_failures_stop_without_immediate_retry(hass, entry, http_mock):
    coordinator = await load_entry(hass, entry, http_mock)
    for attempt in range(3):
        http_mock.post(BASE_URL + "/waterPayment/customBill", status=503)
        coordinator._attempt_at -= 601
        await coordinator.async_refresh()
        assert request_count(http_mock) == 5 + attempt
    await hass.async_block_till_done()
    assert coordinator.stop_reason == "repeated_connection_error"
    assert hass.states.get(entity_id(hass, entry, "collection_status")).state == "stopped"
    await coordinator.async_refresh()
    assert request_count(http_mock) == 7


async def test_startup_failures_accumulate_across_setup_retries(hass, entry, http_mock):
    # Build new coordinators like HA does for each setup retry; the counter
    # must survive reconstruction and eventually latch the stop.
    from homeassistant.helpers.aiohttp_client import async_create_clientsession
    from homeassistant.helpers.update_coordinator import UpdateFailed

    from custom_components.tianjin_water.api import WaterClient

    for i in range(3):
        http_mock.post(BASE_URL + "/waterPayment/customBill", status=503)
        coordinator = WaterCoordinator(
            hass, entry, WaterClient(async_create_clientsession(hass), ACCOUNT, SESSION)
        )
        with pytest.raises(ConfigEntryError if i == 2 else UpdateFailed):
            await coordinator._async_update_data()
        assert entry.data["connection_failures"] == i + 1
        await coordinator.async_shutdown()
    assert entry.data["stopped"] == "repeated_connection_error"
    assert request_count(http_mock) == 3


async def test_reordered_replaced_and_unsupported_meters(hass, entry, http_mock):
    coordinator = await load_entry(hass, entry, http_mock)
    original = entity_id(hass, entry, f"{opaque_id(METER_A)}_index")
    registry = dr.async_get(hass)
    account = registry.async_get_device_by_identifier((DOMAIN, entry.unique_id), entry.entry_id)
    meters = [
        {"registerNo": METER_B, "meterKind": "NB"},
        {"registerNo": METER_A, "meterKind": "NB"},
    ]
    setup_responses(http_mock, meters)
    coordinator._attempt_at -= 601
    coordinator._history_at -= HISTORY_SECONDS + 1
    await coordinator.async_refresh()
    assert entity_id(hass, entry, f"{opaque_id(METER_A)}_index") == original
    # Removed meter becomes unavailable; replacement gets independent IDs.
    meters = [
        {"registerNo": "3000000001", "meterKind": "NB"},
        {"registerNo": "3000000002", "meterKind": "IC"},
    ]
    setup_responses(http_mock, meters)
    coordinator._attempt_at -= 601
    coordinator._history_at -= HISTORY_SECONDS + 1
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(original).state == "unavailable"
    assert entity_id(hass, entry, f"{opaque_id('3000000001')}_index")
    assert not entity_id(hass, entry, f"{opaque_id('3000000002')}_index")
    replacement = registry.async_get_device_by_identifier(
        (DOMAIN, f"{entry.unique_id}_{opaque_id('3000000001')}"), entry.entry_id
    )
    assert replacement is not None
    assert replacement.via_device_id == account.id


async def test_failure_mid_history_stops_following_requests(hass, entry, http_mock):
    http_mock.post(BASE_URL + "/waterPayment/customBill", payload=bill())
    http_mock.post(BASE_URL + "/waterService/customBillMeter", status=403)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert request_count(http_mock) == 2
    assert entry.data["stopped"] == "invalid_auth"


async def test_diagnostics_allowlist(hass, entry, http_mock):
    await load_entry(hass, entry, http_mock)
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = str(result)
    for secret in (ACCOUNT, SESSION, METER_A, METER_B, entry.unique_id):
        assert secret not in text
    assert result["request_count"] == 4
    assert result["session_ttl"] == "unknown"
