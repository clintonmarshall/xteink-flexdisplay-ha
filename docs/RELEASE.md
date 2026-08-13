# Release and deployment runbook

## 1. Prepare

1. Start a dedicated release task and worktree from `origin/main`.
2. Confirm all intended feature pull requests are merged in Forgejo.
3. Record the exact full commit SHA and included pull requests. A later commit
   requires a new validation record.
4. Choose the semantic version, update the changelog and the four version
   markers listed in `AGENTS.md`, and update the FlexDisplay platform row in
   `docs/COMPATIBILITY.md`.
5. Update compatibility minimums and fallback behavior when a protocol or
   device contract changes.
6. Classify the release with `scripts/check_firmware_release.py` as
   `software-only` or `firmware-bearing`.
7. Before making an App version store-visible, inspect each deployment target's
   installed version and automatic-update setting. When automatic update is
   enabled, create and verify a partial rollback backup before publication or
   obtain confirmation to disable automatic update temporarily.

## 2. Verify

```bash
python3 scripts/check_release_metadata.py --release X.Y.Z
python3 scripts/check_firmware_release.py --classification software-only
python3 scripts/check_studio_javascript.py
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
git diff --check
```

When affected, also build and smoke-test the Home Assistant App image, build
and non-editably install the Python wheel, run repository-local HACS and
hassfest validation, and run `./gradlew clean assembleDebug lintDebug` for the
Android receiver. Record the exact commit, test count, architecture, artifact
checksums, and warnings. A missing or skipped affected-component check blocks
the release; downstream GitHub checks are supporting evidence only.

## 3. Device-firmware gate

For a software-only release, verify packaged firmware binaries and their
configured version, URL, size, and SHA-256 are identical to the preceding
published platform tag. Also require `flexdisplay_bridge/firmware/provenance.json`
to identify each artifact's source repository, immutable full commit and tag,
clean source state, target family/board, build command/toolchain, artifact type,
and partition write scope. Identical historical bytes are not complete build
provenance. Do not flash, OTA-update, restart, refresh, or command a device.
Read-only telemetry and non-destructive Studio preview checks are the default
compatibility evidence.

For changed packaged firmware, record its authoritative source repository,
immutable commit or tag, byte size, SHA-256, manifest commit, and known-good
recovery artifact. Then obtain fresh authorization for one USB-powered
development canary after verifying its stable identity, power, active/recovery
partition, intended artifact, and recovery procedure. After the write,
independently verify the same identity, target version, reboot telemetry,
Bridge check-in, and affected-family smoke test. A broader rollout requires
separate exact authorization.

## 4. Publish

Publication uses `.forgejo/workflows/publish.yml` on the dedicated trusted
`trusted-release` Runner. The initial workflow supports software-only
releases. Firmware-bearing publication remains blocked until a reviewed
workflow revision verifies and attaches its canary/provenance evidence.

If the trusted Runner, protected immutable `v*` tag rule, reviewed workflow, or
fixed-purpose credentials are absent, stop. Do not create a tag or release
locally, use a raw API or browser fallback, or publish directly to GitHub.

1. Merge the release pull request through Forgejo after checking its exact base,
   head SHA, mergeability, approvals, and required checks.
2. Require the authoritative post-merge `main` validation to succeed at that
   exact commit.
3. Obtain fresh publication confirmation naming the tag, full commit SHA,
   software/firmware classification, destinations, and rollback constraints.
4. Dispatch the trusted Forgejo workflow with the exact values and confirmation
   phrase. It revalidates, creates the immutable annotated tag, publishes the
   Forgejo release from the matching changelog section, waits for the mirror,
   then dispatches the downstream GitHub compatibility release.
5. Verify the Forgejo and GitHub tag objects, peeled commits, canonical release
   bodies, published/non-draft state, and required assets are identical. Verify
   the preceding release and tag remain unchanged. HACS does not treat a bare
   GitHub tag as a published version.

## 5. Deploy

Publishing does not authorize deployment. Re-read the exact Home Assistant
inventory record and record the installed Bridge/integration versions, desired
tag and commit, persistent-data scope, App source/slug, last-known-good
versions, automatic-update state, and verified rollback backup identifier.

1. Obtain fresh deployment confirmation naming the target, versions,
   interruption, health checks, and rollback path.
2. Reload the App store and re-read installed/latest state. If automatic update
   already moved the App, do not issue a second update; audit the rollback
   backup and proceed to health verification.
3. Otherwise update with a verified affected-scope backup, then confirm the App
   is started at the expected version.
4. Verify `/healthz` reports the expected version and healthy Home Assistant,
   MQTT and FlexHub state when configured. Verify `/api/v1/system`, Studio
   ingress, and one non-destructive preview render without a Home Assistant
   error.
5. Determine the active integration source. Refresh/update HACS only when an
   existing HACS FlexDisplay installation is registered. When MQTT owns the
   entities and no HACS repository/config entry exists, do not create one; mark
   the HACS gate not applicable. A residual manual integration requires an
   exact-commit backup, staging, `ha core check`, restart confirmation, health
   verification, and rollback procedure.
6. A Home Assistant Core restart requires its own fresh confirmation. Afterward
   verify installed versions, entity availability, and absence of new errors.
7. For software-only releases, device verification remains read-only and no
   firmware or device command may be queued.

## Rollback

Stop at the first unexplained regression. Treat the Bridge App, integration,
persistent data, and device firmware as separate rollback scopes. Reinstall the
last-known-good App/integration through the approved path; restore a Home
Assistant backup only when persistent data changed or the verified procedure
requires it. Restart, restore, rollback deployment, and firmware recovery writes
each require fresh confirmation.

Never move, replace, or delete an existing tag, release, or published artifact.
Correct the cause in a new patch release and repeat the normal release path.
