# Status and limitations

This file is the honest project ledger. A checked box requires evidence, not code.

## Implemented and tested

- [x] Strict JSON configuration loading with unknown-key rejection
- [x] Profile loading and cross-reference validation
- [x] All package names checked against Debian Stable archive indexes in CI
- [x] Debian `InRelease` parsing and signature verification in the build
- [x] Hardware parsers for CPU, memory, firmware, Secure Boot, GPUs, disks, network, audio and USB controllers
- [x] Disk selection, mounted/removable/small disk refusal
- [x] BTRFS layout, compression and swapfile constraints
- [x] Install planning for UEFI, BIOS, GPT, MBR, EXT4, XFS, LUKS, ZRAM, partition swap and BTRFS swapfile
- [x] Driver policy for NVIDIA, AMD, Intel and generic hardware, including Secure Boot fallback
- [x] Package availability pruning and required-package refusal
- [x] Confirmation phrase and critical warning acknowledgements
- [x] Command generation for partitioning, mkfs, LUKS, mounts, fstab, crypttab, debootstrap and GRUB
- [x] Structured logging with secret redaction
- [x] Terminal installer
- [x] Graphical GTK4 installer sharing the same plan builder
- [x] GitHub Actions unit and package checks
- [x] ISO build workflow with ISO structural validation
- [x] Weekly and release workflows
- [x] Full ISO build in GitHub Actions
- [x] UEFI QEMU boot smoke test
- [x] BIOS QEMU boot smoke test
- [x] Complete EXT4 installation in a disposable VM
- [x] Complete BTRFS installation in a disposable VM
- [x] Complete UEFI installation in a disposable VM
- [x] Complete LUKS2 installation and passphrase unlock
- [x] Reboot into the installed system and log in
- [x] BTRFS subvolume layout verified after boot
- [x] BTRFS compression mount option observed on the installed root
- [x] ZRAM swap verified after boot
- [x] Published release with checksums and provenance

## Implemented, validation pending

- [ ] BTRFS swapfile verified with `swapon --show`
- [ ] Snapper timers verified after boot
- [ ] NVIDIA proprietary driver load on physical hardware
- [ ] MOK enrollment on a Secure Boot machine
- [ ] AMD and Intel graphics verification
- [ ] Every desktop and window manager profile tested after installation
- [ ] Upgrade and recovery scenarios

## Not implemented

- [ ] Graphical installer validation on the live desktop
- [ ] Manual partitioning
- [ ] Existing partition reuse and dual boot
- [ ] LVM
- [ ] RAID
- [ ] Multi-disk installation
- [ ] Offline package repository generation on the ISO
- [ ] ARM64 build and boot
- [ ] Secure Boot compatibility validation for third-party modules
- [ ] Custom kernel builds
- [ ] Snapshot boot entries in GRUB
- [ ] Hibernation and resume configuration
- [ ] An automated serial-console installation harness
- [ ] Additional translations beyond the gettext framework
- [ ] Package signing for Spiral's own packages, because Spiral does not yet build packages
- [ ] Persistent live mode

## Known risks

- The installer is a single-process root program. A bug can destroy the selected disk.
- Partitioning is irreversible. There is no rollback.
- The BTRFS layout depends on correct mount ordering, fstab generation and initramfs behavior. This needs real installation tests.
- The ISO build may expose live-build behavior that differs from expectations. Structural validation is necessary but not sufficient.
- GitHub release assets are limited to 2 GiB per file. The live package set is kept small, and release publication fails clearly if the ISO is too large.
- The package check verifies package names, not whether a driver works with a particular chip.
- Secure Boot and NVIDIA claims require hardware validation.

## Release checklist

Before calling a release production ready:

1. CI unit tests and package checks pass.
2. ISO build and structural validation pass.
3. UEFI and BIOS boot smokes pass.
4. At least EXT4 and BTRFS installations pass in disposable VMs.
5. LUKS installation passes in a VM.
6. The installed system reboots and logs in.
7. The package manifest and SHA256SUMS match the uploaded ISO.
8. Documentation matches implemented behavior.
9. Known limitations are still accurate.
