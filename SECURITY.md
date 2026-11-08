# Security policy

Security fixes are made for the latest released version. Earlier versions may require an upgrade.

Please report vulnerabilities through GitHub's **private vulnerability reporting**:

https://github.com/liuqitoday/ha-tianjin-water/security/advisories/new

Do not include a real SESSION, account number, meter number, HAR, Home Assistant backup,
or household information. A synthetic reproduction and a description of the affected code
are sufficient. Do not open a public issue for an active credential exposure.

The integration stores credentials in Home Assistant's local config entry and sends queries
directly to the documented water service. See [PRIVACY.md](PRIVACY.md) for the data boundary.
No response-time commitment or upstream service availability guarantee is made.
