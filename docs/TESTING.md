# Testing

## What runs on every push

`test.yml` runs:

1. byte compilation of the installer and tests
2. the unit suite with `unittest`
3. repository config validation
4. a package-name check against the real Debian Stable `Packages.xz` indexes for main, contrib, non-free and non-free-firmware

The package check is deliberate. Guessing Debian package names is the most common way a derivative build breaks. A stable desktop profile, driver profile, kernel profile or live package that does not exist fails CI with a precise list.

## Unit test coverage

- JSON schema validation and cross references
- profile loading and package name syntax
- Debian `InRelease` parsing
- `lsblk` JSON, `lspci`, `lscpu`, meminfo, Secure Boot and nvidia-detect parsing
- disk selection and safety checks
- BTRFS layout, compression and swapfile constraints
- plan construction for UEFI, BIOS, GPT, MBR, EXT4, LUKS, swapfile, snapshots and Secure Boot driver policy
- package availability failures and optional package pruning
- confirmation phrase and warning acknowledgement
- GRUB configuration rendering
- package manifest parsing
- apt policy and Packages index parsing

Unit tests do not touch disks and do not need root.

## ISO validation

`build-iso.sh` runs `validate-iso.sh`, which checks:

- volume ID `SPIRAL`
- ISO size
- El Torito boot entries including UEFI
- `/live/vmlinuz`, `/live/initrd.img`, `/live/filesystem.squashfs`
- Debian codename inside the squashfs release metadata
- installer entry point, engine and CLI
- configuration and BTRFS profiles
- build information
- desktop launcher
- absence of private SSH material
- GRUB menu labels for normal, safe graphics and troubleshooting boot
- SHA256SUMS

A structural pass is not a boot test and is not an installation test.

## Virtual machine testing

`builder/scripts/smoke-qemu.sh` boots the ISO under QEMU. The `vm-test` workflow runs it against the latest successful build artifact in UEFI mode with OVMF and BIOS mode with SeaBIOS, weekly and on demand. It passes when the serial log reaches a systemd target and no kernel panic appears.

A complete installation test still needs:

- a disposable virtual disk
- serial or graphical automation of the installer
- EXT4, BTRFS and LUKS installation cases
- verification of the installed filesystem, users, GRUB and fstab
- a reboot into the installed system
- a login and a package-manager check

That harness does not exist yet. Until it does, the installer is "implemented" but not "installation-validated" in CI.

## Hardware test plan

Hardware claims require physical or nested-virtual hardware. Record for each test machine:

- vendor, model, firmware version and Secure Boot state
- CPU, RAM, storage controller and disk model
- GPU and driver in use (`lspci -k`)
- network and Wi-Fi chipsets
- installed kernel and firmware packages
- desktop session and display manager
- suspend, audio, Bluetooth, printing and hotkeys
- `dmesg` errors and `journalctl -b -p err`
- BTRFS scrub result and free space
- snapshot create, list and delete
- NVIDIA module load and MOK enrollment state

The following matrix is required before hardware support is called verified:

| Class | Required cases |
| --- | --- |
| Firmware | UEFI Secure Boot on, UEFI off, BIOS GPT, BIOS MBR |
| GPU | NVIDIA Turing or newer, NVIDIA Kepler or older, AMD, Intel, hybrid, VM |
| Storage | SATA SSD, NVMe, USB, encrypted and unencrypted |
| Network | Ethernet, Intel Wi-Fi, Realtek Wi-Fi, no network |
| Desktop | GNOME, KDE, XFCE, minimal, at least one window manager |
| Filesystem | EXT4, BTRFS standard, BTRFS encrypted, XFS |

A package installing successfully is not evidence that the hardware works.

## Manual checks

```sh
PYTHONPATH=installer python3 -m unittest discover -s tests -t . -v
python3 builder/scripts/check-packages.py --root .
```

The package check needs network access and downloads Debian package indexes. Do not run it on a metered connection.
