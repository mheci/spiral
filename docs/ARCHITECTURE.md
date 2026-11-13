# Architecture

Spiral has three parts that share one source tree.

1. The ISO builder runs Debian live-build from signed Debian metadata.
2. The installer plans an installation from hardware facts and profiles, then executes that plan in ordered phases.
3. GitHub Actions validates the configuration, builds the ISO, validates its structure, and publishes artifacts.

The terminal and GTK4 interfaces are thin front ends over `PlanBuilder` and `Installer`. Planning is pure and testable. Anything that needs root, block devices or a Debian host lives in the execution engine and is exercised only on a real live system or VM.

## Repository layout

```
config/              global Debian, build, installer and hardware configuration
profiles/            kernel, desktop, driver, filesystem and package profiles
installer/           Python installer package and terminal interface
builder/             live-build tree, rendering, release and validation scripts
tests/unit/          pure Python tests run on every push
docs/                engineering and operating documentation
.github/workflows/   test, build, weekly, release, security and dependency workflows
assets/branding/     replaceable logo and wallpaper
```

## ISO build flow

1. `discover-debian.py` fetches `dists/stable/InRelease` from deb.debian.org, verifies the clearsigned file with `gpgv` and the Debian archive keyring, and parses `Codename`, `Version`, `Suite` and `Date`.
2. `build-iso.sh` renders GRUB configuration, release metadata and build information from repository configuration.
3. The installer package, profiles, configuration and branding are copied into the live-build chroot includes tree.
4. `lb config` selects Debian trixie or whatever the discovered codename is, the configured mirror, all archive areas including `non-free` and `non-free-firmware`, hybrid ISO output, GRUB PC and GRUB EFI, and a live boot command line.
5. `lb build` bootstraps the chroot with debootstrap, installs the live package list through APT, builds the squashfs, installs both bootloaders and creates the hybrid ISO.
6. The build creates a package manifest from the chroot dpkg database, a build manifest with the ISO SHA256, and a release tag.
7. `validate-iso.sh` checks the volume ID, El Torito BIOS and UEFI entries, kernel, initramfs, squashfs contents, installer files, release metadata and GRUB menu labels.

There is no upstream Debian ISO download. The trust root is the Debian archive keyring plus APT verification of package indexes and packages.

## Installer domain model

The plan is the single source of truth.

- `InstallChoices` is what the user selected.
- `HardwareReport` is what the machine reported.
- `InstallPlan` is the validated result: release, disk, partition table, partition list, filesystem, subvolumes, compression, swap, encryption, bootloader, software selection, user, locale and warnings.
- `InstallSecrets` holds the user password and LUKS passphrase separately, in memory only.
- `Approval` holds the exact confirmation phrase and acknowledged warning codes.

`PlanBuilder` resolves defaults and profiles into an `InstallPlan`. It refuses unsafe combinations before any disk change: mounted or removable target, insufficient RAM or disk, an unencrypted swap partition beside encrypted root, snapshots on non-BTRFS, unsupported swapfiles, invalid users or hostnames, and unavailable required packages when a resolver is available.

The plan summary contains no secrets and is safe to write to disk or logs.

## Execution phases

`engine.py` runs these phases in order:

1. preflight
2. storage (partitioning)
3. filesystems (mkfs, LUKS format and open)
4. mount
5. base (debootstrap, sources, bind mounts, APT update)
6. kernel
7. firmware
8. drivers
9. desktops
10. software
11. users
12. configure (fstab, crypttab, locale, timezone, snapshots, zram, autologin, GRUB defaults)
13. bootloader
14. initramfs
15. verify
16. cleanup

Each phase logs start and completion. A failure stops the next phase, records the failure, completed phases and target mount state, and leaves the mounts in place for diagnosis. Partitioning and formatting cannot be rolled back. The installer never claims otherwise.

## Boot chain

- UEFI: Debian signed shim and GRUB packages are installed, `grub-install` targets `x86_64-efi`, and `/boot/efi` must be a mountpoint. The ESP is a 512 MiB FAT32 partition.
- BIOS with GPT: a 1 MiB BIOS boot partition is created and `grub-install --target=i386-pc` writes to the disk.
- BIOS with MBR: the disk uses a DOS label and GRUB embeds in the MBR gap.
- Encrypted root: `/boot` is a separate unencrypted EXT4 partition, the root partition is LUKS2 with Argon2id, and `/etc/crypttab` is written before initramfs generation.
- BTRFS root: `GRUB_CMDLINE_LINUX` includes `rootflags=subvol=@` and GRUB preloads the btrfs module.

## Configuration and profiles

JSON is used everywhere. The installer needs only the Python standard library, and strict loaders reject unknown keys, missing fields, duplicate IDs, bad package names, invalid mountpoints and broken cross references.

Profiles are data, not code. Adding a desktop, kernel, driver or package group means adding a validated JSON file. Code should not branch on desktop names or kernel names.

## CI design

- `test.yml` runs unit tests, config validation and a package-name check against the real Debian Stable archive indexes.
- `build.yml` builds the ISO in a Debian trixie container, validates it, uploads it, and optionally publishes a release.
- `weekly-rebuild.yml` calls the build workflow every Sunday. A release is skipped only when the Debian version, package manifest and git commit all match the previous release. The validation build still runs.
- `release.yml` publishes on a `v*` tag and forces publication.
- `security.yml` runs static checks and dependency audits.
- `dependency-update.yml` reports stale pinned actions.

## Extension points

- Another architecture needs a profile architecture entry, bootloader package mapping and CI runner.
- Another filesystem needs a validated profile plus mkfs and fstab support in commands and the engine.
- Another desktop is a profile file.
- Another kernel is a profile file plus package availability.
- Manual partitioning needs a parser for an explicit partition plan and new validation rules. It is not implemented.
