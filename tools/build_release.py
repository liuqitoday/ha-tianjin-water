#!/usr/bin/env python3
"""Build a deterministic integration-only ZIP from an explicit file allowlist."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "tianjin_water"
INTEGRATION = ROOT / "custom_components" / DOMAIN
ALLOWED = {
    "__init__.py",
    "api.py",
    "config_flow.py",
    "const.py",
    "coordinator.py",
    "diagnostics.py",
    "sensor.py",
    "manifest.json",
    "strings.json",
    "translations/en.json",
    "translations/zh-Hans.json",
    "brand/icon.png",
    "brand/icon@2x.png",
}


def main() -> None:
    version = json.loads((INTEGRATION / "manifest.json").read_text())["version"]
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{DOMAIN}.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as zipped:
        sources = [(INTEGRATION / name, f"custom_components/{DOMAIN}/{name}") for name in ALLOWED]
        sources.append((ROOT / "LICENSE", "LICENSE"))
        for source, destination in sorted(sources, key=lambda pair: pair[1]):
            if source.is_symlink() or not source.is_file():
                raise ValueError("A release input is missing or is a symlink")
            info = ZipInfo(destination, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, source.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive.name} v{version}: {len(ALLOWED) + 1} allowlisted files")


if __name__ == "__main__":
    main()
