# Changelog

## 0.3.0 — 2026-11-08

First public release.

- Native Home Assistant setup, account deduplication, reconfiguration and reauthentication.
- Balance and amount due, plus NB meter reading, consumption, charge and reading date sensors.
- Shared asynchronous polling: ten-minute balance updates and six-hour meter/history updates.
- Persistent stop state for authentication rejection, unexpected responses and repeated connection failures.
- Up to 48 validated billing-period records for dashboards, excluded from repeated Recorder storage.
- Privacy-preserving diagnostics and isolated Cookie handling, without automatic SESSION replacement.
- Explicit device parent IDs for current Home Assistant device registry APIs.
- Offline HAR extraction with successful-response validation, multi-account selection and safe output files.
- HACS custom-repository metadata, original local icon, installation/authentication guides, CI and MIT license.

Earlier local prototypes were not publicly distributed through this repository.
