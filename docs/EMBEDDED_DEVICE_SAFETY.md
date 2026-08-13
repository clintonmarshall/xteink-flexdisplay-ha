# Embedded-device safety and recovery

This is the project gate for serial access, full-flash reads, physical canaries,
recovery writes, OTA, and fleet rollout. A build, feature task, release, or
previous device approval does not authorize a new physical operation.

## 1. Identify without opening serial

1. Enumerate USB descriptors without opening serial. Bind the current port to an
   immutable chip MAC, hardware UUID, or other identity only when that stable
   descriptor has already been independently mapped to the immutable identity.
   A port name, USB location, or otherwise unmapped serial alone is not identity.
2. If descriptors do not expose a previously verified mapping, record the
   intended descriptor target, obtain authorization for the serial read, then
   read and bind the immutable chip identity before backup or write.
3. Record the expected receiver family, exact model/revision, chip, flash size,
   USB serial, current firmware when known, and removable configuration.
4. Treat any serial open or bootloader tool as device-affecting when it may
   assert DTR/RTS, reset the target, or enter its bootloader. Obtain explicit
   authorization before that first open.
5. Rebind the port to the immutable identity after every reconnect, reset, or
   re-enumeration. Stop on ambiguity or a changed descriptor.

## 2. Read and establish recovery

After read access is authorized, establish a recovery path before any write:

1. For first-time or unknown hardware, recovery work, or a device without a
   current complete recovery set, capture the entire physical flash and any
   removable configuration required for restoration. Use the exact chip and
   flash size; never copy offsets or commands from another family.
2. If complete readout is impossible, record why and verify an exact vendor or
   known-good recovery artifact and restoration procedure for that device and
   revision. A vendor demo archive is not a backup of the installed device.
3. Store recovery material in an approved durable, owner-only location outside
   repositories, worktrees, temporary/cache directories, and ephemeral task
   mirrors. Treat full-flash, NVS, configuration, and removable-media backups as
   credential-bearing secrets; do not commit or inspect secret contents.
4. Record the immutable device identity, capture date, byte size, lowercase
   SHA-256, flash/partition scope, exact restore offsets, tool and command shape,
   bootloader-entry procedure, interruption recovery, and post-restore checks.
5. Re-hash and revalidate identity, scope, completeness, and readability
   immediately before every write. A routine canary or OTA may reuse a current
   recovery set only after that verification.

## 3. Confirm and write

Immediately before writing, obtain fresh confirmation naming:

- immutable device identity and currently rebound port;
- candidate filename, byte size, SHA-256, artifact type, and source provenance;
- exact partition or address range to be written and expected interruption; and
- the independently verified recovery path and restore procedure.

Confirm exclusive port ownership and stable power. Never infer an image type,
partition offset, panel revision, or recovery procedure from another model.
Never erase flash merely to make a normal application update succeed. Stop on
any identity, hash, size, source, target, or recovery mismatch.

## 4. Accept the physical canary

An upload-success line or first reboot is not acceptance. Where supported, read
back and compare the written region, then verify against the same immutable
identity:

- expected build/revision and a new boot or check-in identifier;
- reset reason, stable boot, and absence of a boot loop;
- display output, touch/buttons, retained configuration, and storage readiness;
- authenticated Bridge/API connectivity and capability negotiation;
- one representative render and one bounded representative action;
- model-specific reconnect, sleep/wake, refresh, power, battery, and thermal
  health; and
- two successive healthy check-ins, or the documented model equivalent.

For Bridge-dependent changes, verify a compatible Bridge and prove unrelated
receiver families reject the candidate image, command, action, and OTA route.
Record operator observations for physical checks that cannot be automated. If a
required check is unavailable, label the canary partial and do not publish the
firmware or widen rollout.

## 5. Roll back or widen

Stop widening at the first unexplained failure and reconcile actual state before
retrying, resetting, erasing, restoring, or reflashing. Recovery is another
write and requires fresh confirmation. Repeat the full acceptance checks after
restoration.

A successful canary does not authorize a fleet rollout. Obtain separate exact
authorization, expand in bounded batches, and wait for healthy independent
check-ins from each batch before continuing.
