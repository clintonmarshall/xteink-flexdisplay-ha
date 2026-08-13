# FlexDisplay contributor guide

## Repository authority

- Forgejo is the source of truth and the canonical remote is `origin`.
- GitHub is a downstream compatibility mirror for HACS and public downloads.
  Developer checkouts must not push feature branches, `main`, tags, or releases
  to GitHub. The Forgejo-controlled mirror may copy refs, and a downstream
  release job may publish only after the matching Forgejo release exists at the
  same commit.
- Start every change from an up-to-date `origin/main` in a dedicated Codex
  worktree. Use branches named `codex/<component>-<outcome>`.
- Merge through a Forgejo pull request. Do not commit directly to `main`.

## Component boundaries

- `flexdisplay_bridge/`: Home Assistant app, Bridge API, Dashboard Studio, and
  packaged device firmware.
- `custom_components/flexdisplay/`: HACS/Home Assistant integration.
- `rook_receiver/`: Android receiver for the original 2017 Echo Spot and the
  2019 Echo Show 5.
- FlexHub and X3/X4 firmware are external products. Interact with them through
  documented Bridge APIs; do not copy their source into this repository.
- Admit a new device family only after a dedicated architecture task defines
  its owning repository, stable identity and capabilities, firmware ownership,
  compatibility fallback, transport boundary, hardware validation, and
  recovery path. Until then, keep it fail-closed for firmware, provisioning,
  policy, reset, and command actions.

## Required verification

Before requesting review:

```bash
python3 scripts/check_release_metadata.py
python3 scripts/check_studio_javascript.py
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
git diff --check
```

If the Android receiver changes, also run from `rook_receiver/`:

```bash
./gradlew clean assembleDebug lintDebug
```

Forgejo checks are authoritative. When affected, Forgejo must also build the
Home Assistant App image, validate the integration with hassfest and the local
HACS repository checks, and run the Android build and lint checks. A missing,
skipped, or unavailable affected-component check blocks review and release;
GitHub checks are downstream evidence only.

## Versions and releases

- Bridge app, Python package, integration, and repository release versions
  move together.
- Keep these values identical:
  `flexdisplay_bridge/config.yaml`, `flexdisplay_bridge/pyproject.toml`,
  `flexdisplay_bridge/flexdisplay_bridge/__init__.py`, and
  `custom_components/flexdisplay/manifest.json`.
- Release metadata must also contain a matching changelog heading and
  FlexDisplay platform row in `docs/COMPATIBILITY.md`. Android receiver changes
  must keep both receiver compatibility rows aligned with `versionName` and
  increase `versionCode` from the preceding receiver release.
- Only a release task may bump versions, update the changelog, merge the
  release, create a `vX.Y.Z` tag, or publish release assets.
- Publish only through the reviewed Forgejo workflow on a dedicated trusted
  `trusted-release` Runner from a protected immutable `vX.Y.Z` tag at the exact
  tested commit. If the workflow, Runner, tag protection, or required
  credential path is absent, publication is blocked; do not fall back to local
  tags, raw APIs, or direct GitHub publication.
- A release whose packaged firmware bytes are unchanged is non-flashing by
  default. Never publish or roll out changed device firmware without the
  identity, recovery artifact, checksum, USB-powered canary, post-reboot
  check-in, and affected-family gates in `docs/RELEASE.md`.

## Deployment and recovery

- `docs/RELEASE.md` is the canonical deployment and rollback checklist.
- Before merging an App version change, check whether any target Home Assistant
  instance has automatic App updates enabled. If so, treat the merge as capable
  of deployment: record the installed version and verified rollback backup and
  obtain the required deployment confirmation immediately before merge.
- Bridge, integration, persistent data, and device firmware are separate
  rollback scopes. A successful Bridge rollback does not prove the others were
  recovered.

## Task ownership

Use one Codex task per outcome. A feature task may modify one or more connected
components, but it must not publish or deploy. The release task is the sole
integration point for versioning, tags, Forgejo releases, GitHub mirror
verification, and Home Assistant rollout.
