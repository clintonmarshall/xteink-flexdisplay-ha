# FlexDisplay contributor guide

## Repository authority

- Forgejo is the source of truth and the canonical remote is `origin`.
- For this repository only, GitHub is an approved downstream compatibility
  exception for HACS, existing Home Assistant consumers, and public release
  assets that have first been published through Forgejo. Forgejo remains
  authoritative. Do not use this repository's GitHub mirror as precedent for
  private home, lab, firmware, or unrelated repositories.
- GitHub is read-only from developer checkouts. Do not push feature branches,
  `main`, or tags to GitHub.
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
- ESP colour/LVGL receiver firmware, when introduced, belongs in the
  authoritative Forgejo `xteink-flexdisplay` repository. Until a target is
  merged there and recorded in `docs/COMPATIBILITY.md`, treat it as experimental
  and unavailable; do not copy its firmware source into this repository.

Every receiver family and protocol revision must declare its owning repository,
immutable family and board identity, versioned and bounded capability schema,
minimum compatible Bridge/receiver versions, fallback behaviour, artifact type,
and recovery method in `docs/COMPONENTS.md` and `docs/COMPATIBILITY.md`. Commands
and OTA artifacts must fail closed across families. A user-editable model name
is never sufficient evidence for firmware routing.

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
./gradlew clean assembleDebug lintDebug
```

If the local machine lacks an Android SDK, report the Android validation as
blocked and rely only on a Forgejo Runner Android job after verifying that job
actually builds the receiver.

When Android receiver headers, capabilities, screen profile, interaction
behavior, or `versionName` change, update `docs/COMPATIBILITY.md` and
`rook_receiver/README.md` in the same feature branch. Document fallback behavior
for older receiver versions.

Run every additional check that applies to the changed component:

- Build the Home Assistant App image when its package, dependencies, entrypoint,
  configuration, or Bridge runtime changes.
- Run repository-provided HACS and hassfest-equivalent validation when the Home
  Assistant integration or its metadata changes. If equivalent Forgejo checks
  are absent, report that validation gap and do not claim the coverage passed.
- For Dashboard Studio changes, run the repository-provided JavaScript and
  behavioural tests. If save/load or responsive-preview behaviour changed but
  no matching executable test exists, report the missing gate explicitly.
- For an embedded receiver, run its authoritative repository's clean target
  build and protocol-bound tests; do not validate copied or cached source.
- For a persistent-state schema or storage-path change, test forward migration
  plus corrupt, truncated, missing, and already-current state. Document
  downgrade support and preserve pre-migration data until recovery is verified.

Forgejo is the pre-merge validation authority. A component-critical check that
does not yet run there is a release-blocking infrastructure gap; a downstream
GitHub result is supporting evidence, not a substitute. Record each applicable
command, result, and coverage count at handoff. A successful command that
exercised zero intended targets is a failure.

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
- Any embedded-device read, canary, recovery, or write must also satisfy
  `docs/EMBEDDED_DEVICE_SAFETY.md`; a build or feature task is not device-write
  authorization.
- Do not add or update workflow actions unless each external action is pinned
  to a reviewed full commit SHA with a comment naming the upstream
  release/version. Existing mutable action refs are legacy debt and must be
  remediated in a dedicated CI-hardening pull request before the next release.

## Task ownership

Use one Codex task per outcome. A feature task may modify one or more connected
components, but it must not publish or deploy. A physical firmware canary is a
separate validation outcome that requires verified device identity and recovery
plus fresh confirmation immediately before writing. Follow
`docs/EMBEDDED_DEVICE_SAFETY.md`. The release task is the sole integration point
for versioning, tags, Forgejo releases, GitHub mirror verification, and Home
Assistant rollout.

Before closing, archiving, or removing a feature worktree, report its branch,
full HEAD SHA, clean/dirty state, Forgejo pull request or explicitly unpushed
status, validation, and remaining deployment work. Never leave source, patches,
builds, or recovery artifacts solely in an ephemeral Codex task mirror. Do not
remove unpushed or uncommitted work until its diff and required artifacts are in
an approved durable location.
