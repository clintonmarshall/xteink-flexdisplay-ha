# Security

Please report vulnerabilities privately through GitHub Security Advisories
when available. Private advisory intake is a narrow approved exception to the
downstream-only GitHub policy; it does not authorize GitHub branches, pull
requests, tags, direct release publication, or public vulnerability issues.

Do not include:

- Home Assistant access tokens
- MQTT passwords
- Wi-Fi credentials
- device flash backups
- personal dashboard images

Keep the bridge API on a trusted LAN and configure a long random Bridge API key
before exposing command endpoints beyond that LAN.
