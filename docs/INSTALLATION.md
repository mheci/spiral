# Installation

## Boot

The ISO boots through GRUB on UEFI and BIOS systems. The menu offers:

- Start Spiral
- Start Spiral (Safe Graphics), which adds `nomodeset`
- Start Spiral (Troubleshooting / Hardware Compatibility), which adds `noapic noacpi nomodeset`
- Memory Test, where the build includes memtest86+ and the firmware supports it

The live session uses XFCE and NetworkManager. Log in as `spiral`; the live account has passwordless sudo because the live medium is disposable.

## Start the installer

Use the "Install Spiral" application from the live desktop for the GTK4 interface, or open a terminal and run:

```sh
spiral-installer
```

Both interfaces use the same planner and execution engine. The terminal interface is the reference path because it can run without a graphical session.

The interface asks for:

1. locale, keymap and timezone
2. network status and online or offline mode
3. hardware summary
4. installation type, currently guided whole-disk only
5. target disk
6. filesystem, BTRFS layout, compression, snapshots and swap
7. optional LUKS2 root encryption
8. kernel profile
9. graphics driver policy
10. desktop environment or window manager
11. additional package groups
12. username, full name, hostname, password and autologin
13. a full summary, warning acknowledgements and an exact confirmation phrase
14. installation phases with logs
15. completion

Inspect the plan without touching a disk:

```sh
spiral-installer --dry-run --codename trixie
```

`--codename` exists only for development dry runs. A real installer reads `/usr/share/spiral/release.json` from the ISO.

## Online and offline

An online installation downloads Debian packages from the configured mirrors. The installer checks that every required package is available before it partitions anything. If the network or a required package is unavailable, it stops before the first disk write.

The live ISO carries no full offline package repository in this release. `config/build.json` has `offline.enabled` set to false. The installer can consume a repository at `/usr/share/spiral/repo`, and the offline resolver is implemented, but the builder does not yet produce that repository. The graphical and terminal interfaces therefore refuse offline mode unless the ISO was built with one.

## Storage scope

Implemented:

- one whole disk
- GPT for UEFI and BIOS
- MBR for BIOS only
- ESP plus root
- separate `/boot` when root is encrypted
- optional swap partition when root is not encrypted
- BTRFS, EXT4 and XFS root filesystems
- LUKS2 root with Argon2id and a separate unencrypted `/boot`

Not implemented:

- manual partition editing
- reusing existing partitions
- dual-boot management
- LVM
- RAID
- multiple target disks
- encrypted `/boot`
- swap partition alongside encrypted root, because that would leak swap data

The installer refuses a disk that is mounted, read-only or removable by default. It warns before destroying partitions that look like another operating system. It still erases the whole disk. No automatic selection is made when several internal disks are present.

## Confirmation

Before any destructive command, the installer lists every partition and warning. Each critical warning must be acknowledged. The exact phrase is:

```
ERASE /dev/disk/by-id/...
```

The path must match the selected stable device path. Passing `--yes` alone is not accepted in unattended mode. Use:

```sh
spiral-installer --unattended choices.json \
  --confirm "ERASE /dev/disk/by-id/..." \
  --ack partitioning-destructive,existing-os \
  --user-password-file /run/secrets/user-password
```

The password file must not be readable by group or others. Unattended files may not contain passwords; they are rejected as unknown keys.

## Encryption and recovery

LUKS2 encrypts the root partition. `/boot` stays unencrypted so GRUB can load the kernel and initramfs without a second GRUB crypto stack. At boot, the initramfs asks for the passphrase.

There is no recovery key and no backdoor. If the passphrase is lost, the data is unrecoverable. The installer states this as a critical warning.

## Snapshots

If Snapper is enabled, the install creates `@snapshots`, installs Snapper, creates a root configuration and enables timeline and cleanup timers. Snapshots are not backups. If the drive fails or the filesystem is damaged, the snapshots fail with it.

## First boot

After installation, reboot. GRUB should list the installed Spiral system and the fallback kernel when one was selected. Log in with the user created during installation. Root login is locked; use `sudo`.

## Failure

If a phase fails:

- later phases do not run
- the log stays at `/var/log/spiral-installer/install.log` in the live session
- `failure.json` records the phase, message and completed phases
- mounted target filesystems are deliberately left mounted for diagnosis
- the target is left partially written

Partitioning and formatting cannot be undone. Reboot the live medium and start again with a new plan. Diagnostics can be copied to a mounted USB medium from the failure prompt.
