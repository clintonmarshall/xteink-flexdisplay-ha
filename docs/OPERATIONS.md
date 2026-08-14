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
fetch-only for
diagnostics; its push URL is deliberately disabled. For this repository only,
the GitHub downstream mirror is an approved compatibility exception for HACS,
existing Home Assistant consumers, and public release downloads that have first
been published through Forgejo. Do not use this exception as precedent for
private home, lab, firmware, or unrelated repositories. The only GitHub writers
are the Forgejo-controlled mirror, gated downstream compatibility-release
automation, and private Security Advisories. Confirm the developer-checkout
invariant with:

```bash
git remote -v
git config --get remote.pushDefault
git config --get push.default
```

Expected values are `origin` for the push default and `simple` for push mode.
Never add a working GitHub push URL to a developer checkout. GitHub writes are
limited to the Forgejo-controlled mirror, downstream compatibility-release
automation after the Forgejo release succeeds, and private Security Advisories.

## Change workflow

1. Fetch `origin` and create `codex/<component>-<outcome>` from `origin/main`.
2. Make the smallest coherent change and update component documentation.
3. Run the checks in `AGENTS.md` plus component-specific builds.
4. Push the branch to Forgejo and open a Forgejo pull request.
5. Merge through Forgejo only after the branch is current and every affected
   authoritative Forgejo check, review, and approval permits it. GitHub checks
   do not replace a Forgejo gate.
6. Delete the merged branch and remove its worktree.

`main` is protected against direct pushes. Status-check enforcement must only
be enabled after a trusted, isolated Forgejo Runner with the required labels is
continuously online and has completed every configured required workflow
successfully without exposing publication credentials to pull-request jobs.

Until that isolated Runner exists, only owner-authored, same-repository
`codex/*` pull requests may execute on the current private-LAN Runner. The
protected-base `pull_request_target` workflow rejects unexpected repository,
owner, base, branch, and commit identities before checkout, checks out the
exact head SHA without persisting credentials, and blanks automatic tokens from
every candidate-executing step. The target event has a privileged token and
base secrets available, so never reference those secrets or let candidate code
run in an un-scrubbed step.
Never approve a workflow from a fork, collaborator, AGit request, or other
untrusted author on this Runner. Feature-branch `push` workflows are disabled;
the `push` trigger is limited to protected `main`. These controls reduce the
current exposure but do not replace Runner isolation.

The migration from a PR-sourced workflow to this protected-base workflow is a
one-time bootstrap: the pull request that introduces it cannot receive the new
target-triggered status until the workflow exists on `main`. Keep that pull
request blocked, validate its exact commit locally and with the Runner parser,
and require a separately reviewed and freshly confirmed bootstrap merge. Do not
treat missing checks as an ordinary success or a reusable bypass.

## Device-family admission

Do not treat new hardware as a supported FlexDisplay family merely because it
can call a Bridge endpoint or Home Assistant. A dedicated architecture task
must first identify the owning repository and firmware channel, define stable
identity and capability evidence, document minimum versions and legacy
fallbacks, review transport and security boundaries, and establish hardware
validation and recovery. Until admission is complete, retain unknown devices as
external, read-only observations and expose no firmware, provisioning, policy,
reset, or command actions for them.

### X4 Pro admission record

X4 Pro firmware remains owned by the external authoritative Forgejo repository
`clintonmarshall/xteink-flexdisplay`; this platform owns only the Bridge,
Studio, MQTT, and Home Assistant capability contract. The external admission
record is `firmware/docs/flex/X4_PRO_HARDWARE_ADMISSION.md` on branch
`codex/x4pro-hardware-admission-current`. That branch is pending review and is
not a published compatibility claim. Its pinned upstream evidence is FreeInk commit
`61f0b2b5c5bb2cb6f84a26fca77535313658d39d`, documented there at
`docs/xteink-x4pro-support.md`. Do not copy the firmware source or board
definitions into this repository.

Admission is an exact conjunction, never a product-name or display-size
inference:

- Model header `X-FlexDisplay-Model: X4_PRO` maps to platform key `x4_pro`.
- Board header `X-FlexDisplay-Board-ID: xteink_x4_pro` must be present.
- `X-FlexDisplay-Hardware-Revision` and `X-FlexDisplay-MCU-Family` must report
  the confirmed pairing `s3` with `esp32-s3`.
  `X-FlexDisplay-Flash-Size` and `X-FlexDisplay-PSRAM-Size` carry byte counts;
  the admitted S3 capability profile requires exactly 16 MiB flash and 8 MiB
  PSRAM. Both sizes must match any future artifact manifest. A revision, MCU,
  or memory size must not be inferred from the model.
- `X-FlexDisplay-Firmware-Artifact: x4pro_s3` reports the running artifact
  family verbatim for diagnosis. Bridge persists and exposes this value, but
  the header is evidence only and cannot grant firmware eligibility.
- The reviewed S3 contract uses artifact family `x4pro_s3`. A manifest must
  match model, board ID, revision, MCU family, flash size, PSRAM size, and the
  reported artifact family before any future install surface can be enabled.
  No such manifest or artifact is packaged by the platform today, so `install`
  remains absent.
- No P4 X4 Pro has been verified. Any `p4`/`esp32-p4` or other non-S3 report has
  no admitted artifact, pin/partition contract, or device capabilities and
  remains read-only even if it claims S3 capability tokens.

An admitted S3 runtime may report only the explicit tokens `touch`,
`capacitive-home`, `side-buttons`, `frontlight`,
`frontlight-brightness`, `frontlight-warmth`, `frontlight-home-hold`,
`frontlight-timeout`, and `sdmmc`. Physical events are
`home`, `side_previous`, `side_next`, and `power`. Frontlight power, brightness,
and warmth are independent controls. Every X4 Pro check-in must freshly report
all identity, memory, artifact, and capability headers. An omitted field is
cleared rather than inherited from the prior check-in, and missing identity
fields or capability tokens remove the corresponding surface. Non-S3 claims do
not promote a device into the S3 profile.

The existing Bridge check-in transport may retain X4 Pro identity and
telemetry for diagnosis, but an unauthenticated header claim does not authorize
firmware or management. Studio and the authenticated Bridge API, broker ACLs
for MQTT, and the Home Assistant integration must all consume the same
capability descriptor. They must not expose command, reset, provisioning,
frontlight, input, or firmware controls when that descriptor does not explicitly
admit them. Firmware delivery must use the external owner's reviewed immutable
artifact metadata and checksum; it must never reuse the X3/X4 packaged channel.

Before enabling an X4 Pro firmware artifact, record its immutable source commit,
byte size and SHA-256, durable known-good recovery artifact, exact partition and
pin evidence, and stable device identity. Bind one USB-powered S3 canary to that
identity, verify recovery before writing, then verify boot/check-in, rendering,
touch, capacitive Home, side and Power events, frontlight brightness and warmth,
the long-Home frontlight shortcut, and its bounded idle timeout,
SDMMC, and rollback after reboot. Any future non-S3 revision requires its own
separate evidence and canary admission; S3 results cannot authorize it.

Older consumers and older device firmware have a fail-closed fallback. An
`X4_PRO` report without the new identity headers remains a known presentation
profile but read-only; an unknown consumer should classify it as unsupported,
not substring-match it to X4. Unknown capability tokens are ignored, queued
legacy X4 install commands are cancelled when corrected X4 Pro identity arrives,
and the 480 × 800 logical preview does not establish firmware compatibility.

## Releases

Follow `docs/RELEASE.md`. Publishing and deployment run only through reviewed
trusted Forgejo Runner workflows from a protected immutable tag at the exact
tested commit. If that path is absent or unverified, the release is blocked;
there is no manual fallback. After the Forgejo release succeeds, confirm its
controlled mirror copied the same tag and commit. A reviewed authenticated
post-publication handoff may then trigger the downstream GitHub compatibility
release workflow. A bare GitHub tag is insufficient for HACS, and a manual or
tag-arrival dispatch is not an alternate publisher.

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
`docs/RELEASE.md`. Do not duplicate a shorter checklist here. In particular,
publication is not deployment authorization, software-only releases remain
non-flashing, and a Home Assistant Core restart or restore requires fresh
confirmation at the point of action.

## Archiving

Archive rather than delete until the next successful release cycle. An archive
should include a short README recording its source, reason, date, and whether
uncommitted files are present. Never archive the canonical checkout, active
worktrees, runtime configuration, credentials, Home Assistant backups, or the
latest known-good release artifacts.
