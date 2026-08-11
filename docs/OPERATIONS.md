# FlexDisplay operations

## Codex projects and tasks

Use one Codex project for the platform repository. Make the canonical checkout
the project's primary folder and remove historical folders after their
uncommitted work has been archived.

Use one task per outcome, for example a Studio feature, Echo Spot receiver
change, Home Assistant integration change, or release. Let Codex create a
dedicated worktree for implementation tasks. Do not reuse a feature task to
publish a release or perform unrelated cleanup.

Before starting a task, state the affected component and intended outcome. At
handoff, record the Forgejo pull request, validation performed, and any runtime
deployment that remains.

## Git remotes

`origin` is Forgejo and is the only push target. `github` is fetch-only for
diagnostics; its push URL is deliberately disabled. Confirm the invariant with:

```bash
git remote -v
git config --get remote.pushDefault
git config --get push.default
```

Expected values are `origin` for the push default and `simple` for push mode.
Never add a working GitHub push URL to a developer checkout. The Forgejo push
mirror is the only writer to GitHub.

## Change workflow

1. Fetch `origin` and create `codex/<component>-<outcome>` from `origin/main`.
2. Make the smallest coherent change and update component documentation.
3. Run the checks in `AGENTS.md` plus component-specific builds.
4. Push the branch to Forgejo and open a Forgejo pull request.
5. Merge only after the branch is current and reviews/checks permit it.
6. Delete the merged branch and remove its worktree.

`main` is protected against direct pushes. Status-check enforcement must only
be enabled after a Forgejo runner with the `linux-amd64` label is continuously
online and has completed the validation workflow successfully.

## Releases

Follow `docs/RELEASE.md`. Tags and release notes originate in Forgejo. Confirm
the push mirror copied the tag and that GitHub Actions created a full GitHub
Release; a bare GitHub tag is insufficient for HACS.

## Home Assistant sources

Forgejo remains authoritative in both supported deployment arrangements:

- Existing Bridge installations may retain the GitHub compatibility URL so
  their Home Assistant repository slug and app configuration remain stable.
  Updates arrive through the automatic Forgejo-to-GitHub mirror.
- New Bridge installations may add the public Forgejo repository directly when
  the Home Assistant host can reach it.
- HACS installations must use the public GitHub mirror because HACS does not
  consume arbitrary Forgejo repositories.

After every Bridge release, refresh the Home Assistant app store, confirm the
expected version, create a backup, update, and verify `/healthz`, MQTT, FlexHub,
Studio ingress, and one device from each affected family.

## Archiving

Archive rather than delete until the next successful release cycle. An archive
should include a short README recording its source, reason, date, and whether
uncommitted files are present. Never archive the canonical checkout, active
worktrees, runtime configuration, credentials, Home Assistant backups, or the
latest known-good release artifacts.
