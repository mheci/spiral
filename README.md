# Spiral

Spiral is a Debian-based installation platform. It builds a live ISO from authenticated Debian repositories, boots into a Spiral live environment, and installs Debian Stable onto one disk with a guided installer.

The project is an alpha. It does not yet claim production readiness. The status matrix below is the authoritative list of what works, what is tested, and what is not.

## What Spiral does

- Discovers the current Debian Stable release from signed `InRelease` metadata, with no hardcoded codename.
- Builds the ISO with Debian live-build, APT signature verification, and no downloaded Debian ISO.
- Boots a live XFCE environment from UEFI or BIOS.
- Runs one installer backend from a terminal interface.
- Plans and performs guided whole-disk installation with GPT or MBR, EXT4, BTRFS or XFS, optional LUKS2, and ZRAM, partition swap or a BTRFS swapfile.
- Offers BTRFS subvolume layouts, compression policies, Snapper snapshots and a scrub timer.
- Installs GRUB for UEFI or BIOS, with a separate unencrypted `/boot` when the root filesystem is encrypted.
- Selects a kernel profile, graphics driver policy, desktop environment or window manager, and package groups.
- Verifies package availability before any disk is touched.
- Records every phase, command and failure in a structured log without writing passwords or passphrases.
- Builds and publishes from GitHub Actions, weekly and on demand, with SHA256 checksums and optional GPG signatures.

## Status

| Area | State | Evidence |
| --- | --- | --- |
| Debian release discovery and signature verification | Implemented | Unit tests for parsing, CI uses `gpgv` during build |
| Live ISO construction | Implemented, build in progress | GitHub Actions build workflow |
| UEFI and BIOS boot entries | Implemented | Structural ISO validation checks GRUB labels and El Torito entries |
| Hardware detection | Implemented | Parser unit tests with fixture output |
| BTRFS layouts, compression, swapfile rules | Implemented | Unit tests |
| Installation planning and confirmation | Implemented | Unit tests |
| Terminal installer | Implemented | Unit tests cover the shared backend; full install not yet run in CI |
| Graphical installer | Implemented, not validated | GTK4 wizard sharing the plan builder; launch from the live desktop |
| EXT4, BTRFS, XFS installation | Implemented, hardware validation pending | Requires a disposable VM run |
| LUKS2 installation | Implemented, hardware validation pending | Separate `/boot`, crypttab and initramfs |
| NVIDIA, AMD and Intel driver selection | Implemented, hardware validation pending | Package availability and Secure Boot policy are tested |
| Desktop profiles | Implemented, hardware validation pending | Every package is checked against Debian Stable in CI |
| Automated full installation test | Not implemented | QEMU boot smoke test only |
| Manual partitioning, LVM, RAID, dual-boot reuse | Not implemented | Guided whole-disk installation only |
| Offline package repository on the ISO | Installer path implemented, builder disabled | `build.offline.enabled` is false |
| Secure Boot compatibility with third-party modules | Policy implemented, not validated | MOK enrollment required for DKMS modules |
| ARM64 | Not implemented | Primary architecture is AMD64 |

A feature is not called verified because code exists. The right column says what has actually run.

## Requirements

- A Debian derivative project account on GitHub with Actions enabled.
- An AMD64 machine with UEFI or BIOS firmware to boot the ISO.
- At least 20 GiB of disk for a guided installation.
- Network access for an online installation.

## Download and verify

Releases are published at:

```
https://github.com/mheci/spiral/releases
```

Verify before writing the ISO to removable media:

```sh
sha256sum -c SHA256SUMS
```

If `SHA256SUMS.asc` is published:

```sh
gpg --verify SHA256SUMS.asc SHA256SUMS
```

The repository does not ship a private signing key. Release signing is optional and uses a key supplied through a protected GitHub environment secret.

## Build

The supported build path is GitHub Actions. A manual contributor build runs Debian live-build inside a Debian trixie container and is documented in [docs/BUILD.md](docs/BUILD.md).

## Install

Boot the ISO, open a terminal, and run:

```sh
spiral-installer
```

The installer erases one disk. Read the summary and the confirmation phrase before continuing.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Build pipeline](docs/BUILD.md)
- [Installation and recovery](docs/INSTALLATION.md)
- [BTRFS layouts](docs/BTRFS.md)
- [Kernel profiles](docs/KERNELS.md)
- [NVIDIA and graphics](docs/NVIDIA.md)
- [Testing](docs/TESTING.md)
- [Security](docs/SECURITY.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Status and limitations](docs/STATUS.md)

## Security model

- The build never downloads a Debian installation ISO. It builds from Debian packages over HTTPS.
- The Debian Stable codename is read from `InRelease` after `gpgv` verifies it against the Debian archive keyring.
- APT verifies repository metadata inside the live build and inside the installed system.
- The installer verifies package availability before partitioning and again through APT during installation.
- Passwords and passphrases are passed on standard input, never on command lines or in logs.
- Destructive installation requires an exact phrase and acknowledgement of every critical warning.

See [SECURITY.md](docs/SECURITY.md) for the threat model and its limits.

## License

MIT. See [LICENSE](LICENSE).
