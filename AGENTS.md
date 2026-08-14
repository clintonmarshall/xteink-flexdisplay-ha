# FlexDisplay contributor guide

## Repository authority

- Forgejo is the source of truth and the canonical remote is `origin`.
- For this repository only, GitHub is an approved downstream compatibility
  exception for HACS, existing Home Assistant consumers, and public release
  assets that have first been published through Forgejo. Forgejo remains
  authoritative. Do not use this repository's GitHub mirror as precedent for
  private home, lab, firmware, or unrelated repositories.
- The Forgejo-controlled mirror may replicate only refs that already exist in
  Forgejo. Downstream automation may create a compatibility release only after
  the matching Forgejo release exists at the same commit. Private GitHub
  Security Advisories are the sole other write exception.
- GitHub is read-only from developer checkouts. Do not push branches, `main`,
  tags, or releases to GitHub from a developer checkout.
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
- Adding another device family requires a dedicated architecture task that
  defines its owning repository, stable identity and capability evidence,
  compatibility and fallback behavior, transport/security boundary, hardware
  validation, and recovery path. Until that review is complete, keep the family
  external and read-only; do not expose firmware, provisioning, policy, reset,
  or command actions for it.

## Required verification

Before requesting review:

```bash
python3 scripts/check_release_metadata.py
python3 -m unittest discover -s scripts/tests -v
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
python3 -m venv .venv
.venv/bin/python -m pip install -e './flexdisplay_bridge[test]'
(cd flexdisplay_bridge && ../.venv/bin/python -m pytest tests)
```

Reuse an existing project virtual environment when it already contains the
declared test dependencies. The local `.venv` is disposable and must remain
untracked.

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

Forgejo required checks are authoritative. Every eligible owner-authored,
same-repository pull request must run the protected-base
`pull_request_target` validation on its exact head SHA. Rejected fork,
collaborator, AGit, and other non-owner candidates run only the metadata policy
gate and remain blocked. When affected, Forgejo must also build the Home
Assistant App image, run the available local HACS schemas and hassfest for the
integration, and run the Android build and lint checks. Integration-source
changes remain blocked until full HACS repository validation is available on
the Forgejo Runner. GitHub checks are downstream evidence only. If an affected
check is absent, skipped, or unavailable on the trusted Forgejo Runner, review
and release are blocked.

## Versions and releases

- Bridge app, Python package, integration, and repository release versions
  move together.
- Keep these values identical:
  `flexdisplay_bridge/config.yaml`, `flexdisplay_bridge/pyproject.toml`,
  `flexdisplay_bridge/flexdisplay_bridge/__init__.py`, and
  `custom_components/flexdisplay/manifest.json`.
- Release metadata must also contain the matching
  `flexdisplay_bridge/CHANGELOG.md` heading and FlexDisplay platform row in
  `docs/COMPATIBILITY.md`. When a release contains Android changes, its
  `versionName` must match both receiver rows and its `versionCode` must increase
  from the previous receiver release.
- A changed packaged firmware binary must record its authoritative source
  repository, exact immutable commit or tag, byte size, SHA-256, durable
  known-good recovery artifact, USB-canary evidence, and coordinated
  release-manifest commit. Until a checked-in manifest schema and validator are
  implemented, record this as manually reviewed release evidence and keep
  publication blocked if any field cannot be verified. Do not infer provenance
  from a filename or version string.
- Only a release task may bump versions, update the changelog, merge the
  release, create a `vX.Y.Z` tag, or publish release assets.
- Do not add or update workflow actions unless each external action is pinned
  to a reviewed full commit SHA with a comment naming the upstream
  release/version. Existing mutable action refs are legacy debt and must be
  remediated in a dedicated CI-hardening pull request before the next release.
- Merge only through Forgejo after its required checks pass. Publish and deploy
  only through a reviewed trusted Forgejo Runner workflow from a protected,
  immutable `vX.Y.Z` tag resolving to the exact tested commit. If the workflow,
  protection, credentials, or tag-to-commit match is absent or unverified, stop;
  do not fall back to manual tags, release commands, raw APIs, or a direct
  GitHub dispatch.
- A release whose packaged device firmware bytes are unchanged is non-flashing
  by default. Use read-only telemetry and preview checks plus separately
  authorized, non-destructive rendering or control smoke tests; do not schedule
  a firmware write merely to validate a Bridge, Studio, integration, or Android
  receiver release.
- Never publish or roll out changed device firmware without the stable-identity,
  recovery-artifact, checksum, USB-powered canary, post-reboot check-in, and
  affected-family smoke-test gates in `docs/RELEASE.md`.

## Deployment and recovery

- `docs/RELEASE.md` is the canonical Home Assistant deployment and rollback
  checklist; other documentation must link to it rather than repeat a subset.
- Before a Home Assistant deployment, resolve the exact inventory record,
  record prior component versions and the backup identifier, validate the
  effective configuration, and run `ha core check`. Obtain fresh confirmation
  immediately before dispatching a deployment and again immediately before a
  Home Assistant Core restart, restore, or other separately gated action. State
  the target, interruption, verification, and recovery path.
- Verify Bridge, integration, and device-firmware rollback independently. A
  successful Bridge rollback does not establish that integration state,
  persistent data, or a firmware canary was recovered.

## Task ownership

Use one Codex task per outcome. A feature task may modify one or more connected
components, but it must not publish or deploy. The release task is the sole
integration point for versioning, tags, Forgejo releases, GitHub mirror
verification, and Home Assistant rollout.
