# Release and deployment runbook

## 1. Prepare

1. Start a dedicated release task and worktree from `origin/main`.
2. Confirm all intended feature pull requests are merged in Forgejo.
3. Record the exact full commit SHA intended for release and every included
   pull request. Do not substitute a later commit without repeating validation.
4. Choose the semantic version and update
   `flexdisplay_bridge/CHANGELOG.md`.
5. Update the four platform version markers listed in `AGENTS.md` and the
   FlexDisplay platform row in `docs/COMPATIBILITY.md`.
6. For every protocol or minimum-version change, update the compatibility
   matrix with the new minimum and the fallback behavior for older clients.
7. If the Android receiver changes, make `versionName` match both receiver rows
   in the compatibility matrix and increase `versionCode` from the preceding
   receiver release.
8. When the Companion changes, keep its independent version name and strictly
   increasing version code synchronized in `rook_receiver/app/build.gradle`
   and `rook_receiver/release/companion-release.json`.
9. The platform tag used as Companion provenance must contain the Companion and
   its Bridge/Home Assistant contract in `main`, with an accurate platform
   changelog. Do not attach Companion 0.5.0 to the older platform 0.46.0 source
   tag.
10. Classify the release explicitly as either software-only (the packaged
   device-firmware bytes are unchanged) or firmware-bearing.
11. For changed packaged firmware, record the authoritative source repository,
   exact immutable commit or tag, byte size, SHA-256, durable known-good
   recovery artifact, and coordinated release-manifest commit. A filename or
   version string is not provenance.

## 2. Verify

```bash
python3 scripts/check_android_release_metadata.py
python3 scripts/check_release_metadata.py --release X.Y.Z
python3 -m unittest discover -s scripts/tests -v
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
python3 -m venv .venv
.venv/bin/python -m pip install -e './flexdisplay_bridge[test]'
(cd flexdisplay_bridge && ../.venv/bin/python -m pytest tests)
```

Reuse an existing project virtual environment when it already contains the
declared test dependencies. Keep `.venv` untracked.

Forgejo required checks are authoritative. The Forgejo Runner must execute the
baseline commands above and, when affected:

- build the Home Assistant App image;
- run the exact-checkout HACS source validator, public-repository metadata
  validator, and hassfest for the integration; and
- run `./gradlew clean testKioskDebugUnitTest testCompanionDebugUnitTest
  assembleKioskDebug assembleCompanionDebug lintKioskDebug
  lintCompanionDebug` in `rook_receiver/`.

Build the Home Assistant app image and the Android receiver when affected. The
Android gate is:

```bash
cd rook_receiver
./gradlew --no-daemon clean \
  testKioskDebugUnitTest testCompanionDebugUnitTest \
  assembleKioskDebug assembleCompanionDebug \
  lintKioskDebug lintCompanionDebug \
  testCompanionReleaseUnitTest lintCompanionRelease \
  assembleCompanionRelease
```

Firmware releases additionally require verified size and SHA-256 metadata, a
USB-powered canary, successful reboot telemetry, and only then fleet rollout.

### Android signing preflight

The first published Companion APK permanently establishes both application ID
`au.com.ldcs.flexdisplay.rook.companion` and its production signer. Provision a
dedicated disposable Forgejo runner with the `trusted-release` label. Configure
these repository secrets only immediately before an authorised release run:

- `FLEXDISPLAY_COMPANION_KEYSTORE_B64`
- `FLEXDISPLAY_COMPANION_STORE_PASSWORD`
- `FLEXDISPLAY_COMPANION_KEY_ALIAS`
- `FLEXDISPLAY_COMPANION_KEY_PASSWORD`

Generate the production key only as an explicit release operation. Keep the
canonical PKCS12/JKS file and every encoded copy outside Git, and retain two
encrypted offline backups; losing it prevents in-place upgrades. Independently
record the public certificate SHA-256, then add that lowercase fingerprint to
`rook_receiver/release/companion-release-cert.sha256` in the reviewed release
pull request. The release workflow reads the committed fingerprint and refuses
to sign while it is absent. Run this additional release gate:

```bash
python3 scripts/check_android_release_metadata.py --require-signer
```

The publication runner must not run pull-request code or share a persistent
workspace with untrusted jobs. The signing secrets are exposed only to the
short signing step. Before checkout, a token-scoped API preflight requires the
exact source SHA's combined Forgejo status and `Validate / bridge (push)` status
to both be successful. After checkout, the reviewed tag must resolve to that
same SHA and it must still be the exact current Forgejo `main` commit; Gradle
never receives the token or signing secrets. Draft uploads use Forgejo's
automatic, repository-specific workflow token, explicitly scoped to the upload
step.

Forgejo 16 does not provide a verified protected-environment approval or
environment-secret boundary for this repository. The `environment:` YAML key is
therefore not treated as authorisation. Register the isolated runner and load
the repository signing secrets just in time for the explicitly authorised run;
remove the secrets and runner registration after the candidate assets have been
reconciled. A future external secret broker may replace that manual boundary
only if it binds access to this repository, workflow, exact tag and manual
event.

Forgejo 16 restricts `GET /repos/{owner}/{repo}/tag_protections` to repository
administrators, while its automatic Actions token has repository-write rather
than repository-admin access. The workflow therefore fails closed on source
status but cannot truthfully inspect tag-protection rules with that token.
Before creating the tag, an administrator must verify in **Settings > Tags**
that the requested `vX.Y.Z` is covered by the reviewed release-tag protection;
do not substitute a broader static administrator token in the signing workflow.

The committed workflow intentionally targets the unavailable `trusted-release`
label. Provision and review that isolated runner, its JDK/Android SDK, protected
environment and signer record in the release task before dispatching it. The
ordinary Forgejo validation job does not currently build Android; local Android
evidence is useful for feature review but is not the first-publish authority.

### Android Companion publication contract

1. Merge the green release pull request in Forgejo and wait for
   `Validate / bridge (push)` to succeed on the resulting exact `main` SHA.
2. As a repository administrator, verify the reviewed tag-protection rule in
   **Settings > Tags**, then create and push the annotated `vX.Y.Z` tag to
   Forgejo only.
3. Create an associated **draft** Forgejo release.
4. When the release contains the Companion, manually run
   `Publish signed Android Companion candidate`, supplying the
   tag, its exact 40-character commit, and the draft release ID.
5. The protected runner builds and signs once, rejects the Android Debug
   certificate, verifies package/version/manifest/signer, and uploads these
   immutable assets:
   `flexdisplay-companion-VERSION-vcCODE.apk`, `.apk.sha256`, and
   `.metadata.json`.
6. Install that exact SHA-256 on one Galaxy canary. Verify direct Bridge port
   8099 check-in, camera/mic/speaker privacy behavior, foreground/background
   lifecycle, and rollback readiness.
7. Publish the same Forgejo draft without replacing or rebuilding any asset.
8. Verify the Forgejo push mirror copied `main` and the tag to GitHub, then
   mirror the already-signed assets from Forgejo and verify byte-for-byte hashes
   on the complete GitHub Release. GitHub never receives the Android signing
   key. HACS does not treat a bare tag as a published version.

The Android candidate workflow deliberately stops after reconciling the draft
Forgejo assets. Draft promotion and GitHub asset mirroring are separate release
operations and are not automated by this feature pull request. Do not call the
first Android release published until both are implemented or performed through
reviewed Forgejo-controlled release steps and the hashes match.

Record the successful required checks and their exact tested commit. GitHub
checks are downstream evidence only. A missing, skipped, zero-coverage, or
unavailable affected-component check blocks the release.

The Forgejo exact-head suite validates the candidate's HACS and integration
manifests, repository layout, brand asset, license presence, and hassfest
results without exposing a GitHub token. It separately validates the stable
public GitHub repository properties through the anonymous API. After merge,
the downstream GitHub workflow runs the content-addressed upstream HACS action
against the exact mirrored commit. A missing or failed affected-component gate
at either boundary blocks release.

Record the exact Home Assistant Core version used for release verification in
the release evidence; do not put the live Home Assistant hostname or address in
the compatibility matrix.

## 3. Device-firmware gate

For a software-only release, do not flash or OTA-update a device. Use read-only
telemetry and preview checks plus separately authorized, non-destructive
rendering or control checks for each affected family. An unchanged firmware
version or checksum is not permission to queue a write.

For a firmware-bearing release, use one dedicated USB-powered development
canary before any fleet operation. Before writing, record and verify:

- stable hardware identity; the current serial path or IP address is only a
  locator;
- current firmware version, power state, and intended write/recovery
  partition;
- the exact target artifact, byte size, and SHA-256;
- a durable known-good recovery artifact with checksum and build provenance;
  and
- the documented USB recovery procedure.

Obtain fresh confirmation immediately before the firmware write, naming the
canary, effect, expected interruption, verification, and recovery path. After
the write, independently confirm the same stable identity, target version,
successful reboot telemetry, Bridge check-in, and affected-family smoke test.
Do not treat an upload-complete message or reboot alone as success.

If the canary regresses, stop the rollout and recover only that canary through
the documented USB path; recovery is another firmware write and retains its
confirmation gate. A broader rollout requires a separate exact authorization
after the canary evidence has been reviewed.

## 4. Publish

Publication requires a checked-in, reviewed Forgejo Runner workflow, a trusted
Runner, and protected immutable annotated `vX.Y.Z` tags. If any of those
controls are absent or unverified, publication is blocked; do not fall back to
local tag or release commands, a raw API, or direct GitHub publication.

1. Recheck the Forgejo pull request base, exact full head SHA, open state,
   mergeability, approvals, and required checks. Obtain the separately required
   merge confirmation, then merge through Forgejo.
2. Re-resolve the merge commit and require the full authoritative Forgejo check
   set to pass on that exact commit. A pull-request head result does not validate
   a different merge commit.
3. Obtain fresh publication confirmation naming the tag, exact tested commit,
   assets, destinations, and rollback constraints.
4. Dispatch the reviewed Forgejo tag-promotion job with that exact commit. It
   may create the protected immutable annotated tag but must not publish
   release assets from an untagged ref.
5. Verify the tag exists in Forgejo and resolves to the exact tested commit,
   then allow the tag-triggered Forgejo publishing job to run from that tag. It
   must recheck metadata and attach checksum-verified assets.
6. Verify Forgejo contains the immutable tag, release, expected assets, and
   checksums before considering downstream distribution.
7. Verify the Forgejo-controlled mirror copied `main` and the same tag and
   commit to GitHub.
8. After the corresponding Forgejo release and assets are verified, allow the
   reviewed publisher to send its authenticated post-publication handoff to the
   downstream GitHub workflow. Verify the GitHub tag and release resolve to the
   same commit. Do not manually dispatch GitHub or treat mirrored tag arrival as
   an alternate publication route. HACS does not treat a bare tag as a
   published version.

## 5. Deploy to Home Assistant

Publishing does not authorize deployment. Deployment must use an approved,
tag-scoped Forgejo Runner workflow. If that workflow or its fixed-purpose
credential path is absent or unverified, stop; do not substitute manual UI,
shell, SSH, or raw API operations.

1. Read the exact Home Assistant inventory record and verify the current target,
   environment, transport, and approved deployment path.
2. Record the currently installed Bridge and integration versions, the desired
   tag and commit, persistent-data scope, and the last known-good versions.
3. Read the configured App/HACS sources and remote release metadata. Confirm
   only one repository source exposes the Bridge App slug and that the intended
   versions are available without changing runtime state.
4. Validate the effective configuration and run `ha core check`. Treat zero
   intended components or devices as a failed check, not a successful no-op.
5. Obtain fresh confirmation immediately before dispatching the tag-scoped
   deployment. State the exact Home Assistant target, versions, expected
   interruption, health checks, and rollback path.
6. Dispatch the approved pre-restart deployment stage. It must create the
   affected-scope Home Assistant backup, record its identifier and restore
   implications, refresh the App metadata, and update the Bridge App. Abort if
   the backup cannot be identified and verified.
7. Verify `/healthz`, Home Assistant API access, MQTT and FlexHub when
   configured, Studio ingress, and one separately authorized non-destructive
   render.
8. If the HACS integration is affected, use the approved Runner stage to install
   it, run `ha core check` against the staged result, and stop before restarting
   Home Assistant Core. A successful pre-install check does not validate the
   updated integration.
9. Immediately before a required Home Assistant Core restart, obtain fresh
   confirmation naming the target, interruption, checks, and recovery path,
   then dispatch the approved restart stage. Do not treat the earlier deployment
   confirmation as restart authorization.
10. Confirm the installed Bridge and integration versions, Home Assistant
    health, entity availability, and absence of new errors.
11. Run read-only telemetry and preview checks plus any separately authorized,
    non-destructive render or control smoke test on one device from each
    affected family. A software-only release must not queue firmware.

When the signed Companion APK is included, install the exact canary-approved
APK only on the confirmed Android serial, then verify its SHA-256, signer,
package/version, Bridge check-in, foreground/background lifecycle, and local
privacy defaults before treating it as a daily endpoint.

The current Galaxy canary uses the same application ID but an Android Debug
certificate. Android will not upgrade it in place to the production signer.
Record the non-secret Bridge URL/device ID and local policy choices, uninstall
the debug app, remove or re-pair its pinned Bridge identity, install the signed
APK, and restore runtime grants and local policies. That one-time migration
clears private app data. Later same-signer, higher-version-code updates preserve
data normally. Installation, uninstall, re-pairing, and permission changes each
remain separately authorized device/deployment actions.

## Rollback

Stop further deployment or device rollout at the first unexplained regression
and preserve the exact release, health, and device evidence. Diagnose Bridge,
integration, persistent data, and device firmware as separate rollback scopes.

- Reinstall the last known-good Bridge version through the approved Runner
  path. Restore a Home Assistant backup only when persistent data changed or the
  verified rollback procedure requires it.
- Reinstall the last known-good integration version when affected, then run
  `ha core check` against that restored state before any Home Assistant Core
  restart. The restart keeps its own fresh-confirmation gate.
- Recover a failed firmware canary only with its verified recovery artifact and
  documented USB procedure; do not broaden recovery to the fleet.
- Obtain fresh confirmation immediately before any rollback deployment,
  restart, restore, or firmware write, naming the interruption and verification
  path.
- Do not roll an Android release back by reusing a version code or changing its
  signer. Build known-good source with the same production key and a higher
  version code, verify it, and repeat the draft, canary, and promotion path.

After rollback, repeat the relevant health and affected-family checks above.
Do not move, replace, or delete an existing tag or published asset. Correct the
cause in a new patch release and repeat the normal release path.
