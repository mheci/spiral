# Troubleshooting

## The ISO does not boot

Try the safe graphics entry first. If that fails, use the troubleshooting entry. On a black screen, add `nomodeset` at the GRUB command line. On firmware problems, check that the medium was written as a hybrid ISO (`dd` or a tool that preserves the ISO 9660 and EFI boot images).

## The live desktop does not start

Switch to a text console (`Ctrl+Alt+F2`), log in as `spiral`, and run `spiral-installer`. NetworkManager can be controlled with `nmtui`.

## No network

Run:

```sh
nmcli device status
nmcli device wifi list
nmtui
```

The installer refuses to partition if required packages are unavailable. That is intentional.

## Disk is not listed

- The disk must be a whole block device, not a partition.
- Mounted disks are refused.
- Removable disks are refused unless the hardware policy is changed.
- The live USB itself is usually mounted and therefore refused.
- Check `lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS`.

## Installer refuses a package

The preflight step refreshes APT metadata and checks the whole package set. Read the missing package names. A profile may reference a package that the current Debian Stable release does not ship. The CI package check should have caught that; update the profile or choose another desktop or driver.

## Secure Boot and NVIDIA

If Secure Boot is enabled and the proprietary driver was selected explicitly, the kernel modules will not load until a MOK is enrolled. The installer warns about this and does not disable Secure Boot. Reboot into the firmware MOK manager after importing the key.

## BTRFS swapfile failure

`btrfs filesystem mkswapfile` must succeed. A failure usually means `btrfs-progs` is too old, the subvolume is compressed, or the directory is not NOCOW. The installer stops; do not create the swapfile by hand on a system that already failed verification.

## Installation failed

The log is at:

```
/var/log/spiral-installer/install.log
```

The structured failure record is:

```
/var/log/spiral-installer/failure.json
```

The target filesystems may still be mounted under `/target`. Reboot the live medium before retrying. Partitioning cannot be undone.

## Bootloader installation failed on UEFI

- Confirm `/boot/efi` is a mounted FAT32 ESP.
- Confirm the firmware is in UEFI mode, not compatibility mode.
- Some firmware refuses new NVRAM entries. Check `efibootmgr -v`.
- Do not continue as if it worked. The installer stops when GRUB fails.

## Installed system drops to an initramfs prompt

For an encrypted root, check that `/etc/crypttab` names the mapper and uses the correct LUKS UUID. For BTRFS, check the `rootflags=subvol=@` kernel argument. Boot the fallback kernel if one was installed.

## Snapper

Check:

```sh
sudo snapper -c root list
systemctl status snapper-timeline.timer
btrfs subvolume list /
```

Snapshots are not backups. Do not rely on them for disk failure recovery.

## Collect diagnostics

From the failure prompt the installer can copy logs to a mounted USB medium. Manually:

```sh
sudo mkdir -p /mnt/usb
sudo mount /dev/sdX1 /mnt/usb
sudo cp -a /var/log/spiral-installer /mnt/usb/
```

Replace `/dev/sdX1` with the correct partition. This is a destructive-risk command if the wrong partition is mounted; check `lsblk` first.
