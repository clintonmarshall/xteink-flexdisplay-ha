# FlexDisplay roadmap

This roadmap starts from the current coordinated release instead of retaining
completed work as if it were still queued. Exact release and distribution
state is generated from `release-manifest.json` into
[`docs/RELEASE_STATUS.md`](docs/RELEASE_STATUS.md). The changelog remains the
complete version-by-version history.

Roadmap status describes repository work only. It does not establish a Home
Assistant deployment, Android installation, firmware rollout, device check-in,
or physical result.

## Current baseline — Platform 0.49.0

Status: released source; see the generated release status for each publication
and deployment boundary.

The current platform includes:

- Dashboard Studio, previews, reusable content, Quick Cards, photo frames,
  mixed-content channels, screen history, health, groups, policies, audit and
  support tooling.
- Capability-aware Bridge, MQTT and Home Assistant surfaces for X3/X4, Note 4,
  Android and admitted LVGL receiver contracts. Unknown or incompatible
  families remain read-only.
- Packaged X3/X4 OpenDisplay firmware, guarded canary-first OTA controls and
  explicit recovery gates.
- Android receiver contracts for the original Echo Spot, Echo Show 5 and the
  foreground-only phone Companion, including rectangular colour profiles.
- FlexHub status and policy integration while keeping the hub a thin local
  gateway rather than a second fleet controller.
- Forgejo-authoritative validation, protected release workflows and a
  downstream GitHub compatibility boundary.

## Next — Release truth and provenance

Status: implemented in this source; review and merge remain separate gates.

- Keep one checked-in coordinated release manifest for platform, Android,
  distribution and packaged-artifact state.
- Generate the human-readable release snapshot from that manifest and reject
  stale generated documentation in CI.
- Validate packaged bytes against the manifest, Home Assistant App defaults and
  Bridge runtime defaults.
- Fail closed when firmware-bearing releases lack immutable source, recovery,
  checksum and USB-canary evidence.
- Reconcile the separate Factory Kit manifest against this platform record in
  its owning release repository.

## Next — Protocol security and compatibility

Status: planned.

- Port the dormant TRMNL BYOS response-conformance parser onto current firmware
  and test current TRMNL, stock Terminus and legacy response fixtures.
- Vendor one reviewed immutable OpenDisplay protocol definition and generate or
  validate C++, Python and frontend constants from it.
- Design per-device ownership, credential rotation, authenticated commands and
  replay resistance before widening OpenDisplay control.
- Document conservative fallback behaviour for every protocol and receiver
  version change.

## Next — Reliability and hardware evidence

Status: planned; each device mutation retains its own confirmation gate.

- Refresh the CrossPoint power, memory and download correctness review, then
  canary only the changes that match admitted X3/X4 hardware.
- Replace blocking FlexHub fleet refresh work with bounded asynchronous
  behaviour and require a meaningful memory/endpoint soak before wider rollout.
- Complete Note 4 clean builds, physical display/input/audio/power/sleep tests,
  recovery evidence and an independently versioned release path.
- Keep X4 Pro and future families external and read-only until stable identity,
  capability, hardware, transport, recovery and compatibility evidence is
  complete.

## Next — Platform simplification

Status: planned.

- Split the Bridge monolith into routers and services by device, content,
  fleet, firmware, receiver and diagnostics concern.
- Split Dashboard Studio into native modules before considering a new frontend
  build framework.
- Define one generated capability and entity schema for Bridge, Studio, MQTT,
  the Home Assistant integration and documentation.
- Centre operator tools on one journey: create content, assign targets,
  preview, publish and observe. Keep firmware and protocol diagnostics in an
  explicit Operations area.
- Support two clear installation paths: MQTT discovery as the simplest default
  and HACS for richer native behaviour, with overlap detection and migration
  guidance.

## Later candidates

These remain useful but should not displace release truth, security,
reliability and simplification work:

- Multi-device Fleet Canvas layouts and synchronized refresh.
- Stable, beta and development firmware channels after signed-manifest and
  rollback foundations exist.
- Additional LVGL or e-paper families only through the documented architecture
  admission process.
- Battery prediction and richer fleet trend analysis after telemetry quality
  and retention semantics are defined.
