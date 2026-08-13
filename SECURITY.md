# Security

GitHub private vulnerability reporting for the compatibility mirror has not yet
been verified as enabled and monitored. Do not present it as an available route
until maintainers verify that current state and record the monitored route here.
If it is enabled, private reporting is the sole intended inbound-write exception
to the otherwise read-only GitHub mirror policy. Remediation code, review,
release, and publication remain authoritative in Forgejo.

Until a verified route is recorded, do not disclose a vulnerability in a public
GitHub or Forgejo ticket. Before a public release, maintainers must confirm that
at least one documented private route is enabled and monitored; if not, update
this file with a verified private Forgejo or security-contact route first.

Do not include:

- Home Assistant access tokens
- MQTT passwords
- Wi-Fi credentials
- device flash backups
- personal dashboard images

Keep the bridge API on a trusted LAN and configure a long random Bridge API key
before exposing command endpoints beyond that LAN.

## Receiver credentials

Receiver manifest, event, and command authentication must be distinct from
Bridge administrator, Home Assistant/Supervisor, MQTT, Wi-Fi, ESPHome API
encryption, and OTA credentials. Receiver credentials must not grant access to
administrator, Home Assistant, provisioning, or OTA APIs that the receiver does
not need. Store credential labels and scope only in the approved inventory and
inject values through approved secret handling. Examples and protocol fixtures
contain placeholders only.

For the colour/LVGL receiver protocol, use a device-scoped credential derived
from a Bridge-only master; do not provision the master or a shared fleet bearer
credential to a receiver. Provide persistent per-device revocation and rotation
state, and require verified TLS whenever its bearer credential crosses the
network. Existing receiver protocols retain their documented credential and
transport contracts until an explicit, tested migration changes them.
