#!/usr/bin/env python3
"""Extract credentials from a successful balance query, entirely offline.

Requires Python 3.10 or newer and the standard library only. No request is sent,
and no credential, account number, response body, or local path is printed.
"""

import argparse
import base64
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DEFAULT_USER_AGENT = "Mozilla/5.0 MicroMessenger/8.0"
BALANCE_PATH = "/water-supply-front/waterPayment/customBill"


class ExtractionError(ValueError):
    """An extraction failure with a static, safe-to-print message."""


def _headers(message: dict, name: str) -> list[str]:
    """Read HAR header values without accepting malformed entries."""
    headers = message.get("headers", [])
    if not isinstance(headers, list):
        return []
    return [
        item["value"]
        for item in headers
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].lower() == name
        and isinstance(item.get("value"), str)
    ]


def _request_session(request: dict) -> str | None:
    """Only inspect the outgoing request, never response Set-Cookie."""
    values = {
        match.group(1)
        for header in _headers(request, "cookie")
        for match in re.finditer(r"(?:^|;\s*)SESSION=([^;\s]+)", header)
    }
    cookies = request.get("cookies", [])
    if isinstance(cookies, list):
        values.update(
            item["value"]
            for item in cookies
            if isinstance(item, dict)
            and item.get("name") == "SESSION"
            and isinstance(item.get("value"), str)
        )
    if len(values) != 1:
        return None
    value = values.pop()
    return value if re.fullmatch(r"[A-Za-z0-9+/_=.-]{16,512}", value) else None


def _account(request: dict) -> str | None:
    post = request.get("postData", {})
    if not isinstance(post, dict):
        return None
    if isinstance(post.get("text"), str) and post["text"]:
        values = parse_qs(post["text"], keep_blank_values=True).get("customId", [])
    else:
        params = post.get("params", [])
        if not isinstance(params, list):
            return None
        values = [
            item.get("value")
            for item in params
            if isinstance(item, dict) and item.get("name") == "customId"
        ]
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    return values[0] if re.fullmatch(r"\d{1,40}", values[0]) else None


def _finite_amount(value: object) -> bool:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _observed_time(entry: dict, response: dict) -> float:
    """Prefer server time, then capture time; file order breaks equal times."""
    for value in _headers(response, "date"):
        try:
            observed = parsedate_to_datetime(value)
            return observed.replace(tzinfo=observed.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            pass
    try:
        observed = datetime.fromisoformat(entry.get("startedDateTime", "").replace("Z", "+00:00"))
        return observed.replace(tzinfo=observed.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return 0.0


def _candidate(entry: object) -> tuple[float, dict[str, str]] | None:
    if not isinstance(entry, dict):
        return None
    request, response = entry.get("request"), entry.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        return None
    try:
        url = urlsplit(request.get("url", ""))
        if (
            url.scheme != "https"
            or url.hostname != "www.twwc.net.cn"
            or url.port not in (None, 443)
            or url.username is not None
            or url.password is not None
            or url.path != BALANCE_PATH
            or request.get("method") != "POST"
            or response.get("status") != 200
        ):
            return None
        content = response["content"]
        raw = content["text"]
        if content.get("encoding") == "base64":
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        elif content.get("encoding") not in (None, ""):
            return None
        body = json.loads(raw)
        if not isinstance(body, dict) or str(body.get("code")) != "200":
            return None
        account = _account(request)
        data = body.get("data")
        if (
            account is None
            or not isinstance(data, dict)
            or str(data.get("customId")) != account
            or not _finite_amount(data.get("prepayBalance"))
            or not _finite_amount(data.get("totalAmount"))
        ):
            return None
        credential = _request_session(request)
        if credential is None:
            return None
        user_agents = set(_headers(request, "user-agent"))
        if len(user_agents) > 1:
            return None
        user_agent = next(iter(user_agents), DEFAULT_USER_AGENT)
        if (
            not user_agent
            or len(user_agent) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in user_agent)
        ):
            return None
        return _observed_time(entry, response), {
            "custom_id": account,
            "session": credential,
            "user_agent": user_agent,
        }
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError):
        return None


def extract(path: Path, *, account: str | None = None) -> dict[str, str]:
    """Select the latest validated request for one explicitly identified account."""
    if account is not None and not re.fullmatch(r"\d{1,40}", account):
        raise ExtractionError("The --account value must contain only 1 to 40 digits.")
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = document["log"]["entries"]
        if not isinstance(entries, list):
            raise ValueError
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        raise ExtractionError("Could not read a valid HAR file with log.entries.") from None
    candidates = []
    for position, entry in enumerate(entries):
        candidate = _candidate(entry)
        if candidate is not None:
            timestamp, credentials = candidate
            if account is None or credentials["custom_id"] == account:
                candidates.append((timestamp, position, credentials))
    if not candidates:
        raise ExtractionError(
            "No successful balance query with matching account and SESSION found."
        )
    accounts = {candidate[2]["custom_id"] for candidate in candidates}
    if len(accounts) > 1:
        raise ExtractionError(
            "Multiple accounts have successful queries. Select your account using --account."
        )
    return max(candidates, key=lambda candidate: candidate[:2])[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="Local HAR containing a successful balance query")
    parser.add_argument(
        "--account", help="Choose a billing account when the capture contains several"
    )
    parser.add_argument("--out", type=Path, default=Path("private/credentials.json"))
    args = parser.parse_args()
    try:
        credentials = extract(args.har, account=args.account)
        args.out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_EXCL refuses existing files and symlinks, so nothing is overwritten.
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(credentials, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except ExtractionError as err:
        parser.error(str(err))
    except FileExistsError:
        parser.error(
            "Output already exists. Choose another --out file; existing files are never overwritten."
        )
    except OSError:
        parser.error(
            "Could not write the output. Choose a new file in a private, writable directory."
        )
    print("Credentials saved to the requested local file (mode 0600 on POSIX).")


if __name__ == "__main__":
    main()
