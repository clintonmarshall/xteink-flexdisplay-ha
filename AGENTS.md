# FlexDisplay contributor guide

## Repository authority

- Forgejo is the source of truth and the canonical remote is `origin`.
- GitHub is a read-only compatibility mirror for HACS, public downloads, and
  GitHub Actions. Do not push feature branches, `main`, or tags to GitHub.
- Start every change from an up-to-date `origin/main` in a dedicated Codex
  worktree. Use branches named `codex/<component>-<outcome>`.
- Merge through a Forgejo pull request. Do not commit directly to `main`.

## Component boundaries

- `flexdisplay_bridge/`: Home Assistant app, Bridge API, Dashboard Studio, and
  packaged device firmware.
- `custom_components/flexdisplay/`: HACS/Home Assistant integration.
- `rook_receiver/`: Android receiver for the original 2017 Echo Spot.
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

If the Android receiver changes, also run from `rook_receiver/`:

```bash
./gradlew assembleDebug
```

## Versions and releases

- Bridge app, Python package, integration, and repository release versions
  move together.
- Keep these values identical:
  `flexdisplay_bridge/config.yaml`, `flexdisplay_bridge/pyproject.toml`,
  `flexdisplay_bridge/flexdisplay_bridge/__init__.py`, and
  `custom_components/flexdisplay/manifest.json`.
- Only a release task may bump versions, update the changelog, merge the
  release, create a `vX.Y.Z` tag, or publish release assets.
- Never publish an OTA firmware change without the USB-powered canary and
  checksum gates documented in `docs/RELEASE.md`.

## Task ownership

Use one Codex task per outcome. A feature task may modify one or more connected
components, but it must not publish or deploy. The release task is the sole
integration point for versioning, tags, Forgejo releases, GitHub mirror
verification, and Home Assistant rollout.
