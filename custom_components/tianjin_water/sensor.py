"""Account and meter sensors with explicit billing-period semantics."""

from datetime import date
from decimal import Decimal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import WaterConfigEntry
from .const import DOMAIN
from .coordinator import WaterCoordinator

PARALLEL_UPDATES = 0  # The coordinator serializes and throttles all requests.

ACCOUNT_SENSORS = (
    SensorEntityDescription(
        key="balance",
        translation_key="balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CNY",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="due",
        translation_key="due",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CNY",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="collection_status",
        translation_key="collection_status",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["running", "retrying", "authentication_required", "stopped"],
    ),
)
METER_SENSORS = (
    SensorEntityDescription(
        key="index",
        translation_key="index",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="consumption",
        translation_key="consumption",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="charge",
        translation_key="charge",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="CNY",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="date",
        translation_key="date",
        device_class=SensorDeviceClass.DATE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: WaterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add account entities, then discover new meters on history updates."""
    coordinator = entry.runtime_data
    # Register the account before scheduling entities so each meter can refer to
    # its stable registry ID, even while the account entities are still loading.
    account_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, str(entry.unique_id))},
        name=entry.title,
        manufacturer="天津市自来水集团",
        entry_type=DeviceEntryType.SERVICE,
    )
    async_add_entities(
        [
            WaterSensor(coordinator, description, account_device_id=account_device.id)
            for description in ACCOUNT_SENSORS
        ]
    )
    known_meters: set[str] = set()

    @callback
    def add_meters() -> None:
        if coordinator.data is None:
            return
        new = []
        for meter in coordinator.data.meters:
            if meter.kind != "NB" or meter.id in known_meters:
                continue
            known_meters.add(meter.id)
            new.extend(
                WaterSensor(coordinator, description, meter.id, account_device_id=account_device.id)
                for description in METER_SENSORS
            )
        if new:
            async_add_entities(new)

    add_meters()
    entry.async_on_unload(coordinator.async_add_listener(add_meters))


class WaterSensor(CoordinatorEntity[WaterCoordinator], SensorEntity):
    _attr_has_entity_name = True
    # Chart data and poll metadata are live cache, not recorder history.
    _unrecorded_attributes = frozenset(
        {"readings", "last_success", "history_updated", "request_count", "consecutive_errors"}
    )

    def __init__(
        self,
        coordinator: WaterCoordinator,
        description: SensorEntityDescription,
        meter_id: str | None = None,
        *,
        account_device_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._meter_id = meter_id
        account_id = coordinator.entry.unique_id
        account_device = (DOMAIN, str(account_id))
        device_id = f"{account_id}_{meter_id}" if meter_id else str(account_id)
        self._attr_unique_id = f"{device_id}_{description.key}"
        if meter_id:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device_id)},
                name=f"NB 水表 {meter_id[:6]}",
                manufacturer="天津市自来水集团",
                model="NB",
                via_device_id=account_device_id,
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={account_device},
                name=coordinator.entry.title,
                manufacturer="天津市自来水集团",
                entry_type=DeviceEntryType.SERVICE,
            )

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "collection_status":
            coordinator = self.coordinator
            return {
                "last_success": coordinator.last_success.isoformat()
                if coordinator.last_success
                else None,
                "history_updated": coordinator.history_updated.isoformat()
                if coordinator.history_updated
                else None,
                "request_count": coordinator.client.request_count,
                "consecutive_errors": coordinator.consecutive_errors,
                "stop_reason": coordinator.stop_reason,
            }
        if self.entity_description.key != "consumption" or not self.available:
            return None
        meter = next((m for m in self.coordinator.data.meters if m.id == self._meter_id), None)
        return {
            "readings": [
                {
                    "date": row.date.isoformat(),
                    "index": str(row.index),
                    "consumption": str(row.consumption),
                    "charge": str(row.charge),
                }
                for row in meter.readings
            ]
            if meter
            else []
        }

    @property
    def available(self) -> bool:
        if self.entity_description.key == "collection_status":
            return True
        if not super().available or self.coordinator.data is None:
            return False
        if self._meter_id:
            return any(
                m.id == self._meter_id and m.kind == "NB" for m in self.coordinator.data.meters
            )
        return True

    @property
    def native_value(self) -> Decimal | date | str | None:
        key = self.entity_description.key
        if key == "collection_status":
            if self.coordinator.stop_reason == "invalid_auth":
                return "authentication_required"
            if self.coordinator.stop_reason:
                return "stopped"
            return "running" if self.coordinator.last_update_success else "retrying"
        data = self.coordinator.data
        if data is None:
            return None
        if not self._meter_id:
            return getattr(data.balance, key)
        meter = next((m for m in data.meters if m.id == self._meter_id), None)
        return getattr(meter.latest, key) if meter and meter.latest else None
