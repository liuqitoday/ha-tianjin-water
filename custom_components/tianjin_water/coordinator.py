"""Coordinate account polling and persist a stop latch across restarts."""

import logging
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AuthenticationError, ConnectionError, ProtocolError, Snapshot, WaterClient
from .const import (
    CONF_STOPPED,
    DOMAIN,
    HISTORY_SECONDS,
    MAX_TRANSIENT_FAILURES,
    MIN_REQUEST_INTERVAL,
    POLL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class WaterCoordinator(DataUpdateCoordinator[Snapshot]):
    """One scheduler per account; no polling inside individual entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: WaterClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_SECONDS),
            # Publish successful poll times even when the balance is unchanged.
            # HA still suppresses unchanged data-entity state writes.
            always_update=True,
        )
        self.client = client
        self.entry = entry
        self.stop_reason: str | None = entry.data.get(CONF_STOPPED)
        self.last_success = None
        self.history_updated = None
        self.consecutive_errors = entry.data.get("connection_failures", 0)
        self._history_at = float("-inf")
        self._attempt_at = float("-inf")
        self._last_error: Exception | None = None

    def _halt(self, reason: str) -> None:
        self.stop_reason = reason
        self.update_interval = None
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_STOPPED: reason}
        )
        if reason != "invalid_auth":
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.entry.entry_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="polling_stopped",
                translation_placeholders={"reason": reason},
            )
        # The base coordinator suppresses notifications after repeated failures.
        # Publish a transition from "retrying" to "stopped" as well.
        self.hass.loop.call_soon(self.async_update_listeners)

    def _raise_if_stopped(self) -> None:
        if self.stop_reason == "invalid_auth":
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")
        if self.stop_reason:
            raise ConfigEntryError(translation_domain=DOMAIN, translation_key="polling_stopped")

    async def _async_update_data(self) -> Snapshot:
        self._raise_if_stopped()
        if monotonic() - self._attempt_at < MIN_REQUEST_INTERVAL:
            if self._last_error:
                raise self._last_error
            return self.data
        self._attempt_at = monotonic()
        try:
            balance = await self.client.balance()
            history_due = self.data is None or monotonic() - self._history_at >= HISTORY_SECONDS
            if history_due:
                meters = await self.client.meters()
                self._history_at = monotonic()
                self.history_updated = dt_util.utcnow()
            else:
                meters = self.data.meters
        except AuthenticationError:
            self._halt("invalid_auth")
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from None
        except ProtocolError as err:
            self._halt(str(err))
            raise ConfigEntryError(
                translation_domain=DOMAIN, translation_key="polling_stopped"
            ) from None
        except ConnectionError:
            self.consecutive_errors += 1
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, "connection_failures": self.consecutive_errors},
            )
            if self.consecutive_errors >= MAX_TRANSIENT_FAILURES:
                self._halt("repeated_connection_error")
                raise ConfigEntryError(
                    translation_domain=DOMAIN, translation_key="polling_stopped"
                ) from None
            self._last_error = UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                retry_after=POLL_SECONDS,
            )
            raise self._last_error from None
        if self.consecutive_errors:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, "connection_failures": 0}
            )
        self.consecutive_errors = 0
        self._last_error = None
        self.last_success = dt_util.utcnow()
        return Snapshot(balance, meters)
