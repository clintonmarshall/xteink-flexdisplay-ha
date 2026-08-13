# Release and deployment runbook

## 1. Prepare

1. Start a dedicated release task and worktree from `origin/main`.
2. Confirm all intended feature pull requests are merged in Forgejo.
3. Choose the semantic version and update the changelog.
4. Update the four version markers listed in `AGENTS.md`.
5. Update `docs/COMPATIBILITY.md` when device or API compatibility changes.
6. When the Companion changes, choose its independent version name and
   strictly increasing version code in both `app/build.gradle` and
   `release/companion-release.json`.
7. The platform tag used as Companion provenance must contain the Companion and
   its Bridge/HA contract in `main`, with an accurate platform changelog. Do not
   attach Companion 0.5.0 to the older platform 0.46.0 source tag.

## 2. Verify

```bash
python3 scripts/check_release_metadata.py X.Y.Z
python3 scripts/check_android_release_metadata.py
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
```

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

## 3. Publish

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

## 4. Deploy

1. Refresh the Home Assistant app store and confirm the expected Bridge update.
2. Create a Home Assistant backup.
3. Update the Bridge and verify `/healthz`, Home Assistant API access, MQTT,
   Studio ingress and one non-destructive render.
4. Refresh HACS, update the integration, restart Home Assistant, and confirm
   the integration version.
5. Test one device of each affected family before broader rollout.
6. Install the signed Companion APK manually on the confirmed Android serial,
   then verify the APK SHA-256, signer, package/version, Bridge check-in, and
   local privacy defaults before treating it as a daily endpoint.

The current Galaxy canary uses the same application ID but an Android Debug
certificate. Android will not upgrade it in place to the production signer.
Record the non-secret Bridge URL/device ID and local policy choices, uninstall
the debug app, remove or re-pair its pinned Bridge identity, install the signed
APK, and restore runtime grants and local policies. That one-time migration
clears private app data. Later same-signer, higher-version-code updates preserve
data normally.

## Rollback

Do not move or replace an existing tag. Reinstall the last known-good Bridge
version and restore its backup if persistent data changed. Publish a new patch
version for the fix, then repeat the normal release path.

Do not roll an Android release back by reusing a version code or changing its
signer. Build known-good source with the same production key and a higher
version code, verify it, and repeat draft/canary/promotion.
