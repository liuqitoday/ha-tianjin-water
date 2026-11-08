# Contributing

Issues and pull requests are welcome. This repository contains a Home Assistant custom integration,
not an official utility API client. Keep changes within read-only queries for the configured account.

## Development

Use Python 3.14 for integration development. The standalone HAR extraction and release tools support
Python 3.10+ and use only the standard library.

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
pytest
ruff check custom_components tests tools
ruff format --check custom_components tests tools
python tools/check_project.py
python tools/build_release.py
```

The pinned pytest-homeassistant-custom-component dependency installs Home Assistant Core 2026.10.4.
Tests exercise the real config flow, entity/device registries and coordinator, while HTTP responses
are synthetic. pytest-socket blocks real network access. A test-only compatibility adapter supplies
fields required by aiohttp 3.14 to aioresponses 0.7.9; it does not patch the production integration.

CI also validates the integration with Hassfest and HACS. Workflows use read-only permissions and
pinned action revisions; pull requests do not receive credentials or a live utility account.

## Before submitting

- Review `git diff --cached` and `git ls-files`. Do not add captures, credentials, personal data,
  machine paths, private IPs, `.storage`, logs or screenshots from a real household.
- Use invented values and dates in tests; do not copy real bill values even after removing a name.
- Keep `strings.json` and both translations in sync, with clear errors and no raw server responses.
- Preserve account/meter unique IDs across reconfiguration, and never restart stopped polling implicitly.
- Add meaningful tests for behavioral changes. Pure styling/docs changes do not need implementation-mirroring tests.
- Update the changelog and keep manifest / project / diagnostics versions consistent for a release.

## Release

`tools/build_release.py` produces a deterministic allowlisted archive under `dist/`, with
`custom_components/tianjin_water/` at its root. It excludes tests, tools, runtime state and captures.
The HACS configuration installs from the repository's integration directory rather than this ZIP.

A maintainer checks CI, runs the local privacy review, tags `v<manifest version>` and uploads
`tianjin_water.zip` with `SHA256SUMS` to the GitHub release. Do not publish unreviewed workspace files
or depend on `.gitignore` to sanitize an archive.
