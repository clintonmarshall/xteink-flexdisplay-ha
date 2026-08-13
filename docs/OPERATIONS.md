# FlexDisplay operations

## Codex projects and tasks

Use one Codex project for the platform repository. Make the canonical checkout
the project's primary folder. Do not remove a historical folder while it is the
only copy of source, a patch, build evidence, or recovery material.

Use one task per outcome, for example a Studio feature, Echo Spot receiver
change, Home Assistant integration change, or release. Let Codex create a
dedicated worktree for implementation tasks. Do not reuse a feature task to
publish a release or perform unrelated cleanup.

Before starting a task, state the affected component and intended outcome. At
handoff, record the branch, full HEAD SHA, clean/dirty state, Forgejo pull request
or explicitly unpushed status, every applicable validation command and result,
and any physical validation, release, or runtime deployment that remains. Never
leave uncommitted source or patches solely in an ephemeral Codex task mirror.

A physical firmware canary is a separate validation outcome. Feature work may
build or simulate a firmware target, but it does not inherit permission to open
a device serial port, write a lab canary, deploy, or widen a fleet rollout.
Follow the staged identity, read/backup, durable recovery, fresh-confirmation,
and acceptance gates in `docs/EMBEDDED_DEVICE_SAFETY.md`.

## Git remotes

`origin` is Forgejo and is the only push target. `github` is fetch-only for
diagnostics; its push URL is deliberately disabled. For this repository only,
the GitHub downstream mirror is an approved compatibility exception for HACS,
existing Home Assistant consumers, and public release downloads that have first
been published through Forgejo. Do not use this exception as precedent for
private home, lab, firmware, or unrelated repositories. Confirm the invariant
with:

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
6. Confirm the handoff record and durable copies exist, then delete the merged
   branch and remove its worktree.

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

The authoritative deployment and rollback checklist is in `docs/RELEASE.md`.
Publication is not deployment authorization; obtain fresh confirmation for the
exact target before an update, restart, restore, or device operation.

## Archiving

Archive rather than delete until the next successful release cycle. A source
archive should include a short README recording its source, branch and full SHA,
reason, date, validation, and whether uncommitted files are present. Never place
runtime configuration, credentials, full-flash/NVS/configuration backups, Home
Assistant backups, or the latest known-good release artifacts in a repository or
general source archive. Store required recovery material separately in an
approved durable, owner-only location, record size and SHA-256 without exposing
secret contents, and verify its recoverability before removing the original.
Never archive or remove the canonical checkout or an active worktree.
