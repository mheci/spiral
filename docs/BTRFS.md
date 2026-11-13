# BTRFS

BTRFS is the default filesystem. The layout is profile data in `profiles/filesystems/btrfs.json`.

## Layouts

### Standard

| Subvolume | Mount point | Compression | Notes |
| --- | --- | --- | --- |
| `@` | `/` | yes | root |
| `@home` | `/home` | yes | user data |
| `@var` | `/var` | yes | variable data |
| `@var_log` | `/var/log` | yes | logs |
| `@cache` | `/var/cache` | no | rebuildable cache |
| `@tmp` | `/tmp` | no | volatile scratch |
| `@snapshots` | `/.snapshots` | no | only when snapshots are enabled |
| `@swap` | `/swap` | no | only for a BTRFS swapfile |

### Minimal

`@` and `@home`, plus the optional snapshot and swap subvolumes.

### Developer

The standard layout plus `@containers` at `/var/lib/containers`.

All layouts are validated for exactly one root mountpoint, unique names, unique mountpoints and safe paths. The plan mounts subvolumes in parent-first order.

## Compression

Policies live in `config/installer.json`.

| Policy | Algorithm | Level | Mount option |
| --- | --- | --- | --- |
| balanced | zstd | 3 | `compress=zstd:3` |
| maximum | zstd | 15 | `compress=zstd:15` |
| performance | lzo | none | `compress=lzo` |
| none | none | none | omitted |

Custom zstd and zlib levels are accepted by the planner within the kernel limits, although the terminal interface exposes the configured policies. Uncompressed subvolumes receive `compress=no` when compression is active, so cache and tmp contents are not recompressed later.

## Swapfile

A BTRFS swapfile has hard requirements.

- It lives in `@swap`.
- `@swap` is mounted without compression.
- The directory is marked NOCOW with `chattr +C`.
- The file is created with `btrfs filesystem mkswapfile`, which initializes a valid swapfile.
- If that command fails, installation stops. Spiral does not fall back to `dd` and hope the file is valid.

Hibernation is not configured. The swapfile is valid for normal swap use. Resume needs a `resume=` kernel argument and a resume hook that matches the actual swapfile extent layout; that is future work.

A swap partition is allowed only when the root filesystem is not encrypted. Mixing an unencrypted swap partition with encrypted root would leak data. ZRAM and the BTRFS swapfile are the encrypted-root options.

## Snapshots

Optional Snapper support:

- `@snapshots` is created and mounted at `/.snapshots`.
- `snapper` is installed.
- `snapper create-config /` creates the root configuration.
- `snapper-timeline.timer` and `snapper-cleanup.timer` are enabled.

GRUB snapshot boot integration is not implemented. The standard Debian GRUB package does not expose snapshot entries by default. The installer does not install `grub-btrfs` from a third-party source.

Snapshots are not backups. The documentation and installer repeat that because it matters.

## Maintenance

Optional BTRFS maintenance installs `btrfsmaintenance` and enables `btrfs-scrub.timer`. Automatic balance and repair are deliberately not run. Balance can be destructive or very slow when used without understanding the current allocation, so it stays a manual administrator action.

## Mount options

The default options are minimal:

- `subvol=<name>`
- `compress=...` or `compress=no`
- `noatime`

No `discard` option is added. Discard behavior is left to the drive and administrator. The installer does not add `space_cache=v2`; current kernels default to the safe space cache behavior.

## Kernel command line

For a BTRFS root, the installer appends:

```
GRUB_CMDLINE_LINUX="rootflags=subvol=@"
GRUB_PRELOAD_MODULES="btrfs"
```

This makes the root subvolume explicit to the kernel and lets GRUB read the kernel from BTRFS on unencrypted installations.

## Not implemented

- custom user-defined subvolume layouts in the UI
- nested subvolume ordering beyond parent-first mounting
- BTRFS RAID profiles
- received snapshot replication
- automatic scrub reporting to the desktop
- snapshot boot entries in GRUB
