# Kernel profiles

Kernel profiles are JSON files under `profiles/kernels/`. They select Debian packages. Spiral does not build or patch its own kernel.

## Profiles

| Profile | Packages | Fallback | Notes |
| --- | --- | --- | --- |
| standard | `linux-image-amd64`, headers | none | Debian Stable kernel |
| low-latency | `linux-image-rt-amd64`, headers | standard kernel | PREEMPT_RT, amd64 only |
| cloud | `linux-image-cloud-amd64`, headers | standard kernel | reduced hardware support, VM oriented |
| gaming | standard kernel, headers | none | adds the gaming package group; no custom patches |

`{arch}` placeholders are expanded from `config/debian.json` `architecture_suffix`.

## Availability

Before any disk change, the installer resolves every kernel package through the selected repositories. A profile whose package is missing fails with a clear error. A fallback kernel is installed alongside a low-latency or cloud kernel so the system remains bootable if the alternate kernel cannot drive the hardware.

The live ISO contains the standard Debian kernel. The installed system receives the selected packages.

## Compatibility matrix

| Profile | amd64 | arm64 |
| --- | --- | --- |
| standard | yes | profile declared |
| low-latency | yes | no |
| cloud | yes | profile declared |
| gaming | yes | no |

ARM64 is not a supported build target yet. The profile entries describe intent, not tested support.

## Tradeoffs

- The realtime kernel is not a general performance kernel. It reduces worst-case latency at some throughput cost.
- The cloud kernel removes hardware support that physical machines need.
- Gaming performance comes from firmware, Mesa, the NVIDIA or AMD driver, and GameMode. Spiral does not claim a patched kernel.
- DKMS graphics drivers need matching kernel headers. The standard, low-latency and gaming profiles install headers.

## Future work

- per-release availability matrix generated in CI
- signed custom kernels, if a maintainable signing pipeline exists
- hardware-specific kernel command line tuning
- ARM64 cross-build and boot test
