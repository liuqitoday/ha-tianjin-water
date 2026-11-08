#!/usr/bin/env python3
"""Check metadata, translation structure and accidental private-file inclusion."""

import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components/tianjin_water"


def keys(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    return {key for name, child in value.items() for key in keys(child, f"{prefix}.{name}")}


def main() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    version = manifest["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert manifest["domain"] == "tianjin_water"
    assert manifest["codeowners"] == ["@liuqitoday"]
    assert manifest["documentation"] == "https://github.com/liuqitoday/ha-tianjin-water"
    assert manifest["issue_tracker"] == manifest["documentation"] + "/issues"
    assert f'version = "{version}"' in (ROOT / "pyproject.toml").read_text()
    assert f'"version": "{version}"' in (INTEGRATION / "diagnostics.py").read_text()
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["homeassistant"] == "2026.10.4"
    original = json.loads((INTEGRATION / "strings.json").read_text())
    for language in ("en", "zh-Hans"):
        translated = json.loads((INTEGRATION / f"translations/{language}.json").read_text())
        assert keys(original) == keys(translated), f"Translation structure differs: {language}"
    # The extraction tool is deliberately usable without HA or Python 3.14.
    for tool in (ROOT / "tools").glob("*.py"):
        ast.parse(tool.read_text(), feature_version=(3, 10))
    proc = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if proc.returncode == 0 and proc.stdout:
        files = [ROOT / name.decode() for name in proc.stdout.split(b"\0") if name]
    else:
        ignored = {".git", ".venv", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}
        files = [p for p in ROOT.rglob("*") if p.is_file() and not ignored.intersection(p.parts)]
    for path in files:
        relative = path.relative_to(ROOT)
        assert not path.is_symlink(), f"Symlink is not a publication input: {relative}"
        assert not {"private", "runtime", "backups", ".storage"}.intersection(relative.parts)
        assert path.suffix.lower() not in {".har", ".pcap", ".pcapng", ".log", ".pem", ".key"}
        assert not path.name.startswith(("credentials", "token", ".env"))
        if path.suffix in {".py", ".json", ".md", ".toml", ".yml", ".yaml"}:
            text = path.read_text()
            private_address = re.compile(
                r"(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])"
            )
            assert not private_address.search(text), f"Private network address in {relative}"
            assert not re.search(r"/(?:Users|home)/[A-Za-z0-9_-]+/", text), (
                f"Local user path in {relative}"
            )
    print("Metadata, translations, standalone-tool syntax and publication file checks passed")


if __name__ == "__main__":
    main()
