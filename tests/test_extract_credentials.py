"""Offline HAR extraction using invented credentials, accounts, and amounts."""

import base64
import copy
import json
import os
import stat
import sys

import pytest

from tools.extract_credentials import DEFAULT_USER_AGENT, ExtractionError, extract, main

ACCOUNT_A = "1000000081"
ACCOUNT_B = "1000000082"
CREDENTIAL_A = "test-session-" + "a" * 24
CREDENTIAL_B = "test-session-" + "b" * 24


def captured_query(account=ACCOUNT_A, credential=CREDENTIAL_A, captured="2024-02-01T12:00:00Z"):
    return {
        "startedDateTime": captured,
        "request": {
            "method": "POST",
            "url": "https://www.twwc.net.cn/water-supply-front/waterPayment/customBill",
            "headers": [{"name": "Cookie", "value": f"SESSION={credential}"}],
            "postData": {"text": f"customId={account}"},
        },
        "response": {
            "status": 200,
            "headers": [],
            "content": {
                "text": json.dumps(
                    {
                        "code": 200,
                        "data": {
                            "customId": account,
                            "prepayBalance": "73.60",
                            "totalAmount": "0",
                        },
                    }
                )
            },
        },
    }


def write_har(tmp_path, entries):
    path = tmp_path / "synthetic.har"
    path.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return path


def test_success_uses_request_cookie_not_replacement_response(tmp_path):
    query = captured_query()
    query["response"]["headers"] = [{"name": "Set-Cookie", "value": f"SESSION={CREDENTIAL_B}"}]
    result = extract(write_har(tmp_path, [query]))
    assert result == {
        "custom_id": ACCOUNT_A,
        "session": CREDENTIAL_A,
        "user_agent": DEFAULT_USER_AGENT,
    }


@pytest.mark.parametrize("status,code", [(401, 401), (404, 200), (200, 401), (200, 500)])
def test_failed_queries_never_import_new_response_cookies(tmp_path, status, code):
    query = captured_query()
    query["response"]["status"] = status
    body = json.loads(query["response"]["content"]["text"])
    body["code"] = code
    query["response"]["content"]["text"] = json.dumps(body)
    query["response"]["headers"] = [{"name": "Set-Cookie", "value": f"SESSION={CREDENTIAL_B}"}]
    with pytest.raises(ExtractionError, match="No successful balance query"):
        extract(write_har(tmp_path, [query]))


@pytest.mark.parametrize("amount", [None, True, "NaN", "Infinity", "not-an-amount"])
def test_invalid_balance_is_not_success(tmp_path, amount):
    query = captured_query()
    body = json.loads(query["response"]["content"]["text"])
    body["data"]["prepayBalance"] = amount
    query["response"]["content"]["text"] = json.dumps(body)
    with pytest.raises(ExtractionError):
        extract(write_har(tmp_path, [query]))


def test_account_mismatch_is_not_imported(tmp_path):
    query = captured_query()
    query["request"]["postData"]["text"] = f"customId={ACCOUNT_B}"
    with pytest.raises(ExtractionError):
        extract(write_har(tmp_path, [query]))


def test_multiple_accounts_need_explicit_selection_without_disclosing_them(tmp_path):
    path = write_har(tmp_path, [captured_query(), captured_query(ACCOUNT_B, CREDENTIAL_B)])
    with pytest.raises(ExtractionError, match="Multiple accounts") as raised:
        extract(path)
    assert ACCOUNT_A not in str(raised.value)
    assert ACCOUNT_B not in str(raised.value)
    assert extract(path, account=ACCOUNT_B)["session"] == CREDENTIAL_B
    with pytest.raises(ExtractionError, match="No successful balance query"):
        extract(path, account="1000000099")


def test_latest_valid_query_is_selected_regardless_of_file_order(tmp_path):
    older = captured_query(captured="2024-02-01T12:00:00Z")
    newer = captured_query(credential=CREDENTIAL_B, captured="2024-02-02T12:00:00Z")
    assert extract(write_har(tmp_path, [newer, older]))["session"] == CREDENTIAL_B


def test_base64_params_and_har_request_cookies_are_supported(tmp_path):
    query = captured_query()
    content = query["response"]["content"]
    content.update(text=base64.b64encode(content["text"].encode()).decode(), encoding="base64")
    query["request"]["postData"] = {"params": [{"name": "customId", "value": ACCOUNT_A}]}
    query["request"]["headers"] = []
    query["request"]["cookies"] = [{"name": "SESSION", "value": CREDENTIAL_A}]
    assert extract(write_har(tmp_path, [query]))["session"] == CREDENTIAL_A


@pytest.mark.parametrize(
    "url",
    [
        "http://www.twwc.net.cn/water-supply-front/waterPayment/customBill",
        "https://example.invalid/water-supply-front/waterPayment/customBill",
        "https://www.twwc.net.cn/water-supply-front/waterService/getUseWater",
        "https://user@example.invalid/water-supply-front/waterPayment/customBill",
    ],
)
def test_unrelated_or_insecure_endpoints_are_ignored(tmp_path, url):
    query = captured_query()
    query["request"]["url"] = url
    with pytest.raises(ExtractionError):
        extract(write_har(tmp_path, [query]))


def test_conflicting_request_credentials_are_not_guessed(tmp_path):
    query = captured_query()
    query["request"]["cookies"] = [{"name": "SESSION", "value": CREDENTIAL_B}]
    with pytest.raises(ExtractionError):
        extract(write_har(tmp_path, [query]))


def test_response_only_cookie_is_not_an_authenticated_request(tmp_path):
    query = captured_query()
    query["request"]["headers"] = []
    query["response"]["headers"] = [{"name": "Set-Cookie", "value": f"SESSION={CREDENTIAL_A}"}]
    with pytest.raises(ExtractionError):
        extract(write_har(tmp_path, [query]))


@pytest.mark.parametrize("agent", ["bad\r\nheader", "x" * 1025])
def test_unsafe_user_agent_is_rejected(tmp_path, agent):
    query = captured_query()
    query["request"]["headers"].append({"name": "User-Agent", "value": agent})
    with pytest.raises(ExtractionError):
        extract(write_har(tmp_path, [query]))


def test_malformed_entries_are_skipped_without_printing_their_contents(tmp_path):
    query = captured_query()
    invalid = copy.deepcopy(query)
    invalid["response"]["content"]["text"] = "not json"
    path = write_har(tmp_path, [None, {"request": []}, invalid, query])
    assert extract(path)["session"] == CREDENTIAL_A


def test_cli_creates_private_file_without_echoing_credentials_or_path(
    tmp_path, monkeypatch, capsys
):
    path = write_har(tmp_path, [captured_query()])
    target = tmp_path / "private" / "credentials.json"
    monkeypatch.setattr(sys, "argv", ["extract_credentials.py", str(path), "--out", str(target)])
    main()
    output = capsys.readouterr()
    saved = json.loads(target.read_text())
    assert saved["session"] == CREDENTIAL_A
    assert CREDENTIAL_A not in output.out + output.err
    assert ACCOUNT_A not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    original = target.read_bytes()
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    assert target.read_bytes() == original
    assert "already exists" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_cli_refuses_output_symlink(tmp_path, monkeypatch, capsys):
    path = write_har(tmp_path, [captured_query()])
    existing = tmp_path / "existing.json"
    existing.write_text("original")
    target = tmp_path / "linked.json"
    target.symlink_to(existing)
    monkeypatch.setattr(sys, "argv", ["extract_credentials.py", str(path), "--out", str(target)])
    with pytest.raises(SystemExit):
        main()
    assert existing.read_text() == "original"
    assert "already exists" in capsys.readouterr().err


def test_cli_invalid_har_fails_without_traceback_or_file_content(tmp_path, monkeypatch, capsys):
    path = tmp_path / "invalid.har"
    path.write_text(CREDENTIAL_A)
    monkeypatch.setattr(sys, "argv", ["extract_credentials.py", str(path)])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    output = capsys.readouterr()
    assert "Could not read a valid HAR" in output.err
    assert CREDENTIAL_A not in output.out + output.err
    assert str(path) not in output.out + output.err
    assert "Traceback" not in output.err
