# X3/X4 firmware 0.39.0 provenance

This record closes the historical provenance gap for the unchanged X3/X4
firmware packaged by the FlexDisplay Bridge. It does not authorize a new
firmware release, device write, or fleet rollout.

## Shipped artifact

- Packaged path: `flexdisplay_bridge/firmware/firmware.bin`
- Size: `5,976,336` bytes
- SHA-256: `eb9a788cdbbcd16a1c51cf19d1a42894a0975bf387976bd2a1c8d8c604820dd7`
- Target: ESP32-C3 with 16 MiB flash
- Embedded version: `1.5.0-flexdisplay.0.39.0`
- Embedded compile time: `2026-08-12 02:15:42 AEST`
- ESP-IDF version: `5.5.2`
- Embedded ELF SHA-256: `33c20e0cb4ab5153e8cf83bb1e629454ba8f87f5f792867f08dcd4106b247f2b`

The ESP image checksum and appended validation hash both pass `esptool`
validation.

## Authoritative source and original build

- Repository: `clintonmarshall/xteink-flexdisplay`
- Merge commit: `25545971633bc5dfbe5d5699c6bf44283c9ab3b9`
- Feature commit: `808faf47493ccb1dd1bfacd1a1ef968be2a1ad3e`
- Exact source tree: `606417b3fb527612f0959aa31e8c166ee66f1f07`
- FreeInk SDK submodule: `61f0b2b5c5bb2cb6f84a26fca77535313658d39d`
- Lucide submodule: `c81680e066f45b640743ca78ae36cdedda3f0318`
- Forgejo PR: `clintonmarshall/xteink-flexdisplay#3`
- Post-merge checks: Bridge validation passed; firmware validation passed.

The original build directory survived in the clean historical feature
worktree. The feature and merge commits resolve to the same exact source tree.
Its firmware is byte-for-byte identical to the packaged artifact, and its ELF
hash is identical to the ELF hash embedded in that artifact. The original
outputs were copied without modification to the owner-only recovery store:

`owner-recovery://FlexDisplay/Recovery/X3-X4-v0.39.0-2554597/`

| Original output | Bytes | SHA-256 |
| --- | ---: | --- |
| `firmware.bin` | 5,976,336 | `eb9a788cdbbcd16a1c51cf19d1a42894a0975bf387976bd2a1c8d8c604820dd7` |
| `firmware.elf` | 72,826,120 | `33c20e0cb4ab5153e8cf83bb1e629454ba8f87f5f792867f08dcd4106b247f2b` |
| `firmware.map` | 21,775,777 | `7ed3473921e90a039b8c5d1f5cfe16f4089582606d7c95a95d33aee4e1ba6a36` |

A clean rebuild of the exact merge commit and recursive submodules completed
successfully on 2026-09-11 with the pinned Espressif platform, Arduino 3.3.7,
ESP-IDF 5.5.2 and the declared library revisions. The rebuilt image reports the
same target and application version. It is not byte-identical because this
historical build embeds compilation times and non-reproducible link output;
therefore the rebuilt image must not replace the shipped bytes. The preserved
original BIN, ELF and map are the authoritative artifact-to-source record.

## USB-powered canary

The exact packaged firmware was installed on USB-powered development canary
`X3-5DB308` on 2026-08-12 after a full 16 MiB flash backup. The post-reboot
Bridge check-in reported `1.5.0-flexdisplay.0.39.0`; SD-card status, minimum
heap, dashboard refresh and device health checks passed. The canary was then
recorded as verified before any wider rollout.

Canary recovery artifact:

- Reference: `owner-recovery://FlexDisplay/Recovery/X3-X4-v0.39.0-2554597/X3-5DB308-pre-v0.39.0-ota-full-16MB.bin`
- Size: `16,777,216` bytes
- SHA-256: `fd0ccff56e6c54da47f63ad5280849903346565590c4f461346eb857a0f8c0d1`

This recovery image belongs only to stable identity `X3-5DB308`. It must not be
written to another device. Any future firmware-bearing release still requires
a new exact-source build record, stable-identity USB canary, post-reboot check,
affected-family smoke test and separately authorized rollout.
