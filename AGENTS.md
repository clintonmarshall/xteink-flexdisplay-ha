# FlexDisplay contributor guide

## Repository authority

- Forgejo is the source of truth and the canonical remote is `origin`.
- For this repository only, GitHub is an approved downstream compatibility
  exception for HACS, existing Home Assistant consumers, and public release
  assets that have first been published through Forgejo. Forgejo remains
  authoritative. Do not use this repository's GitHub mirror as precedent for
  private home, lab, firmware, or unrelated repositories.
- GitHub is read-only from developer checkouts. Do not push feature branches,
  `main`, tags, releases, or assets to GitHub. Only the Forgejo push mirror and
  trusted downstream compatibility-release workflow may write there.
- Start every change from an up-to-date `origin/main` in a dedicated Codex
  worktree. Use branches named `codex/<component>-<outcome>`.
- Merge through a Forgejo pull request. Do not commit directly to `main`.

## Component boundaries

- `flexdisplay_bridge/`: Home Assistant app, Bridge API, Dashboard Studio, and
  packaged device firmware.
- `custom_components/flexdisplay/`: HACS/Home Assistant integration.
- `rook_receiver/`: Android receiver for Amazon LineageOS devices, currently
  the original 2017 Echo Spot (`rook`, 480 × 480 round) and Echo Show 5 1st gen
  (`checkers`, 960 × 480 landscape). Android receivers must fail closed from
  ESP firmware install, SD-card diagnostics, and embedded-device OTA workflows.
- FlexHub and X3/X4 firmware are external products. Interact with them through
  documented Bridge APIs; do not copy their source into this repository.

## Required verification

Before requesting review:

```bash
python3 scripts/check_release_metadata.py
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
```

Also run `python3 scripts/check_studio_javascript.py` when Dashboard Studio
changes. When Bridge packaging changes, build a wheel and install it
non-editably before the test run. When the Home Assistant App changes, build
the supported architecture image and smoke-test `/healthz` for the expected
version under an isolated read-only configuration.

If the Android receiver changes, also run from `rook_receiver/`:

```bash
./gradlew clean assembleDebug lintDebug
```

If the local machine lacks an Android SDK, report the Android validation as
blocked and rely only on a Forgejo Runner Android job after verifying that job
actually builds the receiver.

When Android receiver headers, capabilities, screen profile, interaction
behavior, or `versionName` change, update `docs/COMPATIBILITY.md` and
`rook_receiver/README.md` in the same feature branch. Document fallback behavior
for older receiver versions.

## Versions and releases

- Bridge app, Python package, integration, and repository release versions
  move together.
- Keep these values identical:
  `flexdisplay_bridge/config.yaml`, `flexdisplay_bridge/pyproject.toml`,
  `flexdisplay_bridge/flexdisplay_bridge/__init__.py`, and
  `custom_components/flexdisplay/manifest.json`.
- Only a release task may bump versions, update the changelog, merge the
  release, create a `vX.Y.Z` tag, or publish release assets.
- Publish only through the reviewed Forgejo workflow on the dedicated trusted
  `trusted-release` Runner. The workflow must use a protected immutable
  `vX.Y.Z` tag at the exact tested `main` commit. If the workflow, Runner, tag
  protection, or required credential path is unavailable, publication is
  blocked; do not fall back to local tags, raw APIs, the browser, or direct
  GitHub publication.
- When release notes say packaged firmware is unchanged, compare every
  firmware artifact plus its configured version, URL, size, and SHA-256 with
  the previous published platform tag, and require complete source/build
  provenance in `flexdisplay_bridge/firmware/provenance.json`. Identical bytes
  do not substitute for provenance. Any byte or metadata difference makes the
  release firmware-bearing and activates the firmware gates below.
- Never publish an OTA firmware change without the USB-powered canary and
  checksum gates documented in `docs/RELEASE.md`.
- Do not add or update workflow actions unless each external action is pinned
  to a reviewed full commit SHA with a comment naming the upstream
  release/version. Existing mutable action refs are legacy debt and must be
  remediated in a dedicated CI-hardening pull request before the next release.

## Deployment and device safety

- Publishing never authorizes Home Assistant deployment, restart, restore, or
  device operations. Follow `docs/RELEASE.md` and obtain the required fresh
  confirmation for each production mutation.
- A software-only release uses read-only device telemetry and non-destructive
  Studio preview checks. It must not send restart, refresh, install, OTA, flash,
  or other device commands merely to prove compatibility.
- Determine whether an existing installation uses HACS, a manual integration,
  or MQTT-owned entities before changing it. A release must not create a new
  HACS config entry or change entity ownership implicitly.

## Task ownership

Use one Codex task per outcome. A feature task may modify one or more connected
components, but it must not publish or deploy. The release task is the sole
integration point for versioning, tags, Forgejo releases, GitHub mirror
verification, and Home Assistant rollout.
