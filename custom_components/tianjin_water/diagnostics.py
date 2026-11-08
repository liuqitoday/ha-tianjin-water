"""Allowlisted diagnostics, without credentials or household identifiers."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import WaterConfigEntry
from .const import CONF_STOPPED, HISTORY_SECONDS, POLL_SECONDS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WaterConfigEntry
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": "0.3.0",
        "poll_seconds": POLL_SECONDS,
        "history_seconds": HISTORY_SECONDS,
        "stop_reason": entry.data.get(CONF_STOPPED),
        "session_ttl": "unknown",
        "keepalive_effect": "unknown",
    }
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return result
    result.update(
        last_update_success=coordinator.last_update_success,
        last_success=coordinator.last_success.isoformat() if coordinator.last_success else None,
        history_updated=coordinator.history_updated.isoformat()
        if coordinator.history_updated
        else None,
        consecutive_errors=coordinator.consecutive_errors,
        request_count=coordinator.client.request_count,
        last_http_status=coordinator.client.last_http_status,
        last_body_bytes=coordinator.client.last_body_bytes,
        last_content_type=coordinator.client.last_content_type,
        session_cookie_seen=coordinator.client.session_cookie_seen,
        meter_count=len(coordinator.data.meters) if coordinator.data else 0,
    )
    return result
