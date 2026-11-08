"""Small, asynchronous client for the three observed read-only endpoints.

Only the authenticated account's returned NB meters are queried. Never follow
redirects, accept replacement cookies from error responses, or log raw responses.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from .const import BASE_URL, DEFAULT_USER_AGENT, MAX_RESPONSE_BYTES, REQUEST_TIMEOUT

_AUTH_TEXT = re.compile(
    r"未登[录陆]|请.{0,12}登[录陆]|登[录陆].{0,12}(过期|失效|超时)|"
    r"会话.{0,12}(过期|失效)|认证.{0,12}(失败|过期)|"
    r"unauthorized|not.?logged|session.{0,15}(expir|invalid)",
    re.IGNORECASE,
)


class WaterError(Exception):
    """An error containing only a safe, static reason code."""


class AuthenticationError(WaterError):
    """An explicit authentication rejection."""


class ProtocolError(WaterError):
    """A response that cannot safely be interpreted."""


class ConnectionError(WaterError):
    """A transient transport, rate limit, or server failure."""


def opaque_id(value: str) -> str:
    """Keep raw account and meter numbers out of registry identifiers."""
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_session(value: str) -> str:
    """Accept either a SESSION value or a copied Cookie header."""
    value = value.strip()
    if value.lower().startswith("cookie:"):
        value = value[7:].strip()
    match = re.search(r"(?:^|;\s*)SESSION=([^;\s]+)", value)
    if match:
        value = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9+/_=.-]{16,512}", value):
        raise ValueError("invalid_session")
    return value


def number(value: Any, *, nonnegative: bool = False) -> Decimal:
    """Reject missing, nonfinite, boolean, and malformed amounts."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ProtocolError("invalid_number")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ProtocolError("invalid_number") from None
    if not result.is_finite() or (nonnegative and result < 0):
        raise ProtocolError("invalid_number")
    return result


@dataclass(frozen=True, slots=True)
class Balance:
    balance: Decimal
    due: Decimal


@dataclass(frozen=True, slots=True)
class Reading:
    date: date
    index: Decimal
    consumption: Decimal
    charge: Decimal


@dataclass(frozen=True, slots=True)
class Meter:
    id: str
    kind: str
    latest: Reading | None
    readings: tuple[Reading, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    balance: Balance
    meters: tuple[Meter, ...]


class WaterClient:
    """Use an isolated HA-owned session with a DummyCookieJar."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        custom_id: str,
        credential: str,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._http = session
        self._custom_id = custom_id
        self._credential = credential
        self._user_agent = user_agent
        self._lock = asyncio.Lock()
        self.request_count = 0
        self.last_http_status: int | None = None
        self.last_body_bytes: int | None = None
        self.last_content_type: str | None = None
        self.session_cookie_seen = False
        self._stopped: WaterError | None = None

    async def _request(self, endpoint: str, identifier: str) -> Any:
        async with self._lock:
            # Also latch at the client boundary, so concurrent callers cannot
            # issue another request after an explicit rejection.
            if self._stopped:
                raise self._stopped
            try:
                return await self._request_once(endpoint, identifier)
            except (AuthenticationError, ProtocolError) as err:
                self._stopped = err
                raise

    async def _request_once(self, endpoint: str, identifier: str) -> Any:
        self.request_count += 1
        self.last_body_bytes = None
        self.last_http_status = None
        self.last_content_type = None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": f"SESSION={self._credential}",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": self._user_agent,
            "Origin": "https://www.twwc.net.cn",
            "Referer": BASE_URL
            + (
                "/waterPayment/toWaterBill"
                if endpoint.startswith("/waterPayment/")
                else "/waterService/waterQuery"
            ),
        }
        try:
            async with self._http.post(
                BASE_URL + endpoint,
                data={"customId": identifier},
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                self.last_http_status = response.status
                # Store only coarse metadata, never header values or cookies.
                content_type = response.content_type
                self.last_content_type = (
                    content_type
                    if content_type in ("application/json", "text/html", "text/plain")
                    else "other"
                )
                self.session_cookie_seen = "SESSION" in response.cookies
                if response.status in (401, 403):
                    raise AuthenticationError("invalid_auth")
                if 300 <= response.status < 400:
                    if re.search(r"login|oauth|weixin", response.headers.get("Location", ""), re.I):
                        raise AuthenticationError("invalid_auth")
                    raise ProtocolError("unexpected_redirect")
                if response.status == 429 or response.status >= 500:
                    raise ConnectionError("server_unavailable")
                if response.status != 200:
                    # A 404 or a fresh anonymous SESSION alone is not proof of expiry.
                    raise ProtocolError("unexpected_http_status")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ProtocolError("response_too_large")
                self.last_body_bytes = len(body)
        except aiohttp.ClientError, TimeoutError:
            raise ConnectionError("cannot_connect") from None

        text = body.decode("utf-8", errors="replace")
        try:
            result = json.loads(text)
        except ValueError, RecursionError:
            if _AUTH_TEXT.search(text) or re.search(r"oauth|/login/", text, re.I):
                raise AuthenticationError("invalid_auth") from None
            raise ProtocolError("unexpected_response") from None
        if not isinstance(result, dict):
            raise ProtocolError("unexpected_response")
        code = str(result.get("code", ""))
        message = str(result.get("message", result.get("msg", "")))
        if code in ("401", "403") or _AUTH_TEXT.search(message):
            raise AuthenticationError("invalid_auth")
        if code != "200" or result.get("data") is None:
            raise ProtocolError("business_rejected")
        return result["data"]

    async def balance(self) -> Balance:
        data = await self._request("/waterPayment/customBill", self._custom_id)
        if not isinstance(data, dict) or str(data.get("customId")) != self._custom_id:
            raise ProtocolError("account_mismatch")
        return Balance(number(data.get("prepayBalance")), number(data.get("totalAmount")))

    async def meters(self) -> tuple[Meter, ...]:
        data = await self._request("/waterService/customBillMeter", self._custom_id)
        if not isinstance(data, dict) or not isinstance(data.get("waterMeterList"), list):
            raise ProtocolError("invalid_meter_list")
        # Validate the entire list before requesting any individual meter.
        identifiers: set[str] = set()
        for item in data["waterMeterList"]:
            if not isinstance(item, dict):
                raise ProtocolError("invalid_meter_list")
            register_no = item.get("registerNo")
            if not isinstance(register_no, str) or not re.fullmatch(r"\d{1,40}", register_no):
                raise ProtocolError("invalid_meter_list")
            if register_no in identifiers:
                raise ProtocolError("duplicate_meter")
            identifiers.add(register_no)
        meters = []
        for item in data["waterMeterList"]:
            register_no = item["registerNo"]
            kind = "NB" if item.get("meterKind") == "NB" else "unsupported"
            latest = None
            readings = ()
            if kind == "NB":
                rows = await self._request("/waterService/getUseWater", register_no)
                readings = parse_history(rows)
                latest = readings[-1] if readings else None
            meters.append(Meter(opaque_id(register_no), kind, latest, readings))
        return tuple(sorted(meters, key=lambda meter: meter.id))


def parse_readings(rows: Any) -> Reading | None:
    """Pick by actual date; empty history remains unknown, never zero."""
    readings = parse_history(rows)
    return readings[-1] if readings else None


def parse_history(rows: Any) -> tuple[Reading, ...]:
    """Validate all rows, then retain at most 48 distinct billing periods."""
    if not isinstance(rows, list):
        raise ProtocolError("invalid_history")
    readings: dict[date, Reading] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ProtocolError("invalid_history")
        raw_date = row.get("meterReadingDate")
        if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            raise ProtocolError("invalid_reading_date")
        try:
            reading_date = date.fromisoformat(raw_date)
        except ValueError:
            raise ProtocolError("invalid_reading_date") from None
        reading = Reading(
            reading_date,
            number(row.get("waterIndex"), nonnegative=True),
            number(row.get("waterConsumption"), nonnegative=True),
            number(row.get("waterRate")),
        )
        if reading_date in readings and readings[reading_date] != reading:
            raise ProtocolError("conflicting_readings")
        readings[reading_date] = reading
    return tuple(readings[key] for key in sorted(readings)[-48:])
