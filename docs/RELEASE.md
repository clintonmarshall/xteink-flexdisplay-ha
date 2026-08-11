# Release and deployment runbook

## 1. Prepare

1. Start a dedicated release task and worktree from `origin/main`.
2. Confirm all intended feature pull requests are merged in Forgejo.
3. Choose the semantic version and update the changelog.
4. Update the four version markers listed in `AGENTS.md`.
5. Update `docs/COMPATIBILITY.md` when device or API compatibility changes.

## 2. Verify

```bash
python3 scripts/check_release_metadata.py X.Y.Z
python3 -m compileall -q flexdisplay_bridge/flexdisplay_bridge \
  flexdisplay_bridge/app_runner.py custom_components/flexdisplay
(cd flexdisplay_bridge && python3 -m pytest tests)
```

Build the Home Assistant app image and the Echo Spot receiver when affected.
Firmware releases additionally require verified size and SHA-256 metadata, a
USB-powered canary, successful reboot telemetry, and only then fleet rollout.

## 3. Publish

1. Merge the release pull request in Forgejo.
2. Create and push the annotated `vX.Y.Z` tag to Forgejo only.
3. Create the Forgejo release and attach any required binaries.
4. Verify the Forgejo push mirror copied `main` and the tag to GitHub.
5. Verify GitHub Actions created a full GitHub Release. HACS does not treat a
   bare tag as a published version.

## 4. Deploy

1. Refresh the Home Assistant app store and confirm the expected Bridge update.
2. Create a Home Assistant backup.
3. Update the Bridge and verify `/healthz`, Home Assistant API access, MQTT,
   Studio ingress and one non-destructive render.
4. Refresh HACS, update the integration, restart Home Assistant, and confirm
   the integration version.
5. Test one device of each affected family before broader rollout.

## Rollback

Do not move or replace an existing tag. Reinstall the last known-good Bridge
version and restore its backup if persistent data changed. Publish a new patch
version for the fix, then repeat the normal release path.
