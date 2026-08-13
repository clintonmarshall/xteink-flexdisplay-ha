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

`origin` is Forgejo and is the only developer push target. `github` is
fetch-only for diagnostics; its push URL is deliberately disabled. Only the
Forgejo-controlled mirror and downstream compatibility-release automation may
write GitHub. Confirm the invariant with:

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
be enabled after the required Forgejo Runner labels are continuously online and
have completed every configured required workflow successfully. Publication
credentials must never be exposed to pull-request jobs.

## Device-family admission

Do not treat new hardware as supported merely because it can call a Bridge
endpoint. First define its owning repository and firmware channel, stable
identity and capability evidence, minimum versions and fallback behavior,
transport/security boundary, hardware validation, and recovery path. Until
that architecture work is complete, unknown devices remain external and
fail-closed for firmware, provisioning, policy, reset, and commands.

## Releases

Follow `docs/RELEASE.md`. Publishing runs only through the reviewed workflow on
a dedicated trusted Runner from a protected immutable tag at the exact tested
commit. If that route is absent or unverified, the release is blocked; there is
no local or raw-API fallback. After the Forgejo release succeeds, verify the
mirror copied the same tag and commit and that downstream automation created a
full GitHub Release; a bare GitHub tag is insufficient for HACS.

## Home Assistant sources

Forgejo remains authoritative in both supported deployment arrangements:

- Existing Bridge installations may retain the GitHub compatibility URL so
  their Home Assistant repository slug and app configuration remain stable.
  Updates arrive through the automatic Forgejo-to-GitHub mirror.
- New Bridge installations may add the public Forgejo repository directly when
  the Home Assistant host can reach it.
- HACS installations must use the public GitHub mirror because HACS does not
  consume arbitrary Forgejo repositories.

The authoritative Home Assistant deployment and rollback checklist is in
`docs/RELEASE.md`; do not duplicate a shorter checklist here. In particular,
publication is not deployment authorization, automatic App updates can make an
App-version merge deployment-capable, and software-only releases must not queue
device firmware.

## Archiving

Archive rather than delete until the next successful release cycle. An archive
should include a short README recording its source, reason, date, and whether
uncommitted files are present. Never archive the canonical checkout, active
worktrees, runtime configuration, credentials, Home Assistant backups, or the
latest known-good release artifacts.
