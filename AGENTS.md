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
  (`checkers`, 960 × 480 landscape), plus a foreground-only Android phone
  Companion (`1200 × 675`). Android receivers must fail closed from ESP
  firmware install, SD-card diagnostics, and embedded-device OTA workflows.
- FlexHub and X3/X4 firmware are external products. Interact with them through
  documented Bridge APIs; do not copy their source into this repository.

## Required verification

Before requesting review:

```bash
python3 scripts/check_release_metadata.py
python3 scripts/check_android_release_metadata.py
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
```

If the Android receiver changes, also run from `rook_receiver/`:

```bash
./gradlew clean assembleDebug lintDebug
./gradlew testKioskDebugUnitTest testCompanionDebugUnitTest
```

When the Companion release packaging or publication contract changes, also run
the unsigned packaging gate. This output is validation input, not a
distributable APK:

```bash
./gradlew testCompanionReleaseUnitTest lintCompanionRelease \
  assembleCompanionRelease
```

If the local machine lacks an Android SDK, report the Android validation as
blocked. Do not treat metadata-only CI as Android validation; the release stays
blocked until a reviewed Forgejo Runner job actually builds the receiver.

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
- Android source versions are independent from the platform version. The
  production publication contract currently covers only the `companion`
  flavor. For a Companion release candidate, keep the derived application ID,
  `versionName`, and `versionCode` in `rook_receiver/app/build.gradle` and
  `rook_receiver/release/companion-release.json` synchronized, and strictly
  increase `versionCode` for every production-signed Companion APK. This does
  not establish a signed kiosk publication channel.
- Only a release task may bump versions, update the changelog, merge the
  release, create a `vX.Y.Z` tag, or publish release assets.
- Sign Companion release assets only on the protected Forgejo publication
  runner. Never commit or copy the Companion signing key to GitHub. Commit only
  its public certificate SHA-256 in
  `rook_receiver/release/companion-release-cert.sha256`, after independently
  comparing it with the offline release record; workflow-generated metadata is
  not the trust source.
- Scope Forgejo's automatic, repository-specific `${{ forgejo.token }}` to the
  draft-upload step. Do not add a long-lived Forgejo release token unless the
  automatic token is proven insufficient on the protected runner.
- Publish a Companion release in this order: create a draft Forgejo release,
  build and sign once, canary the exact signed bytes, publish the unchanged
  Forgejo draft, mirror those assets, then verify the complete GitHub Release.
- Never publish an OTA firmware change without the USB-powered canary and
  checksum gates documented in `docs/RELEASE.md`.
- Do not add or update workflow actions unless each external action is pinned
  to a reviewed full commit SHA with a comment naming the upstream
  release/version. Existing mutable action refs are legacy debt and must be
  remediated in a dedicated CI-hardening pull request before the next release.

## Task ownership

Use one Codex task per outcome. A feature task may modify one or more connected
components, but it must not publish or deploy. The release task is the sole
integration point for versioning, tags, Forgejo releases, GitHub mirror
verification, and Home Assistant rollout.
