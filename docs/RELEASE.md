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
8. Classify the release explicitly as either software-only (the packaged
   device-firmware bytes are unchanged) or firmware-bearing.
9. For changed packaged firmware, record the authoritative source repository,
   exact immutable commit or tag, byte size, SHA-256, durable known-good
   recovery artifact, and coordinated release-manifest commit. A filename or
   version string is not provenance.

## 2. Verify

```bash
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
- run the available local HACS schemas and hassfest for the integration; and
- run `./gradlew clean assembleDebug lintDebug` in `rook_receiver/`.

Record the successful required checks and their exact tested commit. GitHub
checks are downstream evidence only. A missing, skipped, zero-coverage, or
unavailable affected-component check blocks the release.

Full HACS repository validation is not yet available on the Forgejo Runner.
Until an equivalent local validator is reviewed and implemented, any change to
`custom_components/flexdisplay/` or `hacs.json` is blocked from review and
release even when the local schemas and hassfest pass.

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

After rollback, repeat the relevant health and affected-family checks above.
Do not move, replace, or delete an existing tag or published asset. Correct the
cause in a new patch release and repeat the normal release path.
