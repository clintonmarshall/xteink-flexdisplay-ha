# Release and deployment runbook

## 1. Prepare

1. Start a dedicated release task and worktree from `origin/main`.
2. Confirm all intended feature pull requests are merged in Forgejo.
3. Record the exact full commit SHA and included pull requests. A later commit
   requires a new validation record.
4. Choose the semantic version, update `flexdisplay_bridge/CHANGELOG.md`, the
   four platform version markers in `AGENTS.md`, and the FlexDisplay platform
   row in `docs/COMPATIBILITY.md`.
5. Update compatibility minimums and fallback behavior for protocol changes.
   Android changes must align `versionName` with both receiver rows and
   increase `versionCode` from the preceding receiver release.
6. Classify the release as `software-only` or `firmware-bearing` with
   `scripts/check_firmware_release.py`.
7. Before an App-version merge, inspect each intended Home Assistant target.
   If automatic App updates are enabled, record the current version and a
   verified rollback backup, disclose that the merge may roll out
   automatically, and obtain deployment confirmation immediately before merge.

## 2. Verify

Run the local release baseline:

```bash
python3 scripts/check_release_metadata.py --release X.Y.Z
python3 scripts/check_firmware_release.py --classification software-only
python3 scripts/check_studio_javascript.py
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
git diff --check
```

For a `firmware-bearing` release, substitute that classification only after
the firmware evidence in section 3 is complete. Forgejo required checks are
authoritative and must also, when affected:

- build the Home Assistant App image;
- run hassfest and the repository-local HACS checks;
- build a non-editable Python wheel and install-smoke it; and
- run `./gradlew clean assembleDebug lintDebug` in `rook_receiver/`.

Record each successful check and its exact commit. A missing, skipped,
zero-coverage, or unavailable affected check blocks the release. GitHub checks
are downstream evidence only.

## 3. Device-firmware gate

For a software-only release, do not flash or OTA-update a device. Read-only
telemetry and preview checks, plus separately authorized non-destructive render
or control smoke tests, are sufficient.

For changed packaged firmware, record the authoritative source repository,
immutable commit/tag, byte size, SHA-256, coordinated manifest commit, and a
durable known-good recovery artifact with checksum and build provenance. Then
use one USB-powered development canary. Immediately before writing, verify its
stable hardware identity, current firmware and power, active/recovery
partition, intended artifact, and recovery procedure, and obtain fresh firmware
write confirmation naming the interruption and recovery path.

After the write, independently confirm the same stable identity, target
version, reboot telemetry, Bridge check-in, and affected-family smoke test. An
upload-complete or reboot message alone is not success. A broader rollout needs
separate exact authorization after canary review.

## 4. Publish

Publication uses `.forgejo/workflows/publish.yml` on a dedicated
`trusted-release` Runner. The workflow initially supports software-only
releases; firmware-bearing publication remains blocked until its evidence can
be attached and verified by a reviewed workflow revision.

If the trusted Runner, protected immutable `v*` tag rule, reviewed workflow, or
fixed-purpose downstream credential is absent, stop. Do not create a tag or
release locally, use a raw API, or publish directly to GitHub.

1. Recheck the release pull request base, exact head SHA, open state,
   mergeability, approvals, and required Forgejo checks. Obtain merge
   confirmation, then merge through Forgejo.
2. Resolve the merge commit and require all authoritative checks to pass on
   that exact commit.
3. Dispatch the Forgejo workflow with `operation: verify-only`, `vX.Y.Z`, and
   the exact commit. Require the validation and control checks to pass, then
   confirm that neither the tag nor a release was created.
4. Obtain fresh publication confirmation naming `vX.Y.Z`, the exact commit,
   classification, destinations, and rollback constraints.
5. Dispatch the Forgejo workflow with `operation: publish`, those exact values,
   and its confirmation phrases. The promotion job revalidates the commit and
   creates the immutable tag. The publication job checks out that tag,
   revalidates it, creates the Forgejo release from the canonical changelog
   section, then requests the downstream GitHub compatibility release.
6. Verify the Forgejo tag, release, release body, and commit identity. Then
   verify the mirror and GitHub Release use the identical tag and commit. HACS
   does not treat a bare GitHub tag as a release.

## 5. Deploy to Home Assistant

Publishing does not authorize deployment. Use the exact Home Assistant
inventory record and an approved tag-scoped deployment path. Record installed
Bridge/integration versions, desired tag/commit, persistent-data scope,
last-known-good versions, and the rollback backup identifier. Verify only one
repository source exposes the App slug, validate effective configuration, and
run `ha core check`.

Obtain fresh deployment confirmation immediately before dispatch, naming the
target, versions, interruption, health checks, and rollback. The deployment
must create and verify an affected-scope backup before updating the App. Check
`/healthz`, Home Assistant API access, MQTT and FlexHub when configured, Studio
ingress, and a separately authorized non-destructive render.

If the integration changes, install it through the approved path and stop
before any required Home Assistant Core restart. A restart needs its own fresh
confirmation. After restart, verify installed versions, Home Assistant health,
entity availability, and absence of new errors. Test one device from each
affected family without queuing firmware for a software-only release.

## Rollback

Stop at the first unexplained regression. Treat Bridge, integration, persistent
data, and device firmware as separate rollback scopes. Reinstall the
last-known-good Bridge/integration through the approved path; restore a Home
Assistant backup only when persistent data changed or the verified procedure
requires it. A restart, restore, rollback deployment, or firmware recovery write
needs fresh confirmation at the point of action.

Never move, replace, or delete an existing tag or published asset. Correct the
cause in a new patch release and repeat the normal release path.
