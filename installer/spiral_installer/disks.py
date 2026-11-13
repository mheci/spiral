from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .config import HardwareConfig, InstallerConfig
from .errors import ValidationError
from .models import Disk, Firmware, HardwareReport, Severity, Warning
from .util import human_bytes


def select_disk(
    report: HardwareReport,
    requested: Optional[str],
    config: HardwareConfig,
    minimum_gib: int,
) -> Disk:
    if requested:
        match = next(
            (
                disk
                for disk in report.disks
                if requested in (disk.path, disk.stable_path)
                or (disk.by_id is not None and requested == disk.by_id)
            ),
            None,
        )
        if match is None:
            raise ValidationError(
                f"disk '{requested}' was not detected; available disks: "
                + ", ".join(disk.stable_path for disk in report.disks)
            )
        disk = match
    else:
        candidates = [disk for disk in report.disks if not disk.removable]
        if len(candidates) != 1:
            raise ValidationError(
                "cannot choose a target disk automatically; specify one of: "
                + ", ".join(disk.stable_path for disk in report.disks)
            )
        disk = candidates[0]

    validate_target_disk(disk, config, minimum_gib)
    return disk


def validate_target_disk(disk: Disk, config: HardwareConfig, minimum_gib: int) -> None:
    if disk.read_only:
        raise ValidationError(f"{disk.stable_path} is read-only")
    if disk.removable and not config.removable_install:
        raise ValidationError(
            f"{disk.stable_path} is removable; Spiral refuses to erase removable media by default"
        )
    if disk.is_busy:
        mounts = [
            point
            for partition in disk.mounted_partitions
            for point in partition.all_mountpoints
        ]
        raise ValidationError(
            f"{disk.stable_path} has mounted partitions ({', '.join(mounts)}); "
            "unmount them or choose another disk"
        )
    minimum_bytes = minimum_gib * 1024**3
    if disk.size_bytes < minimum_bytes:
        raise ValidationError(
            f"{disk.stable_path} is {human_bytes(disk.size_bytes)}; "
            f"at least {minimum_gib} GiB is required"
        )
    if not disk.partitions:
        return


def disk_warnings(disk: Disk, config: HardwareConfig) -> list[Warning]:
    warnings: list[Warning] = []
    if disk.removable:
        warnings.append(
            Warning(
                "removable-target",
                f"{disk.stable_path} is removable media. Everything on it will be erased.",
                Severity.CRITICAL,
            )
        )
    if disk.has_foreign_os:
        warnings.append(
            Warning(
                "existing-os",
                f"{disk.stable_path} contains partitions that look like another operating system. "
                "They will be destroyed.",
                Severity.CRITICAL,
            )
        )
    if disk.transport.lower() == "usb":
        warnings.append(
            Warning(
                "usb-target",
                f"{disk.stable_path} is connected over USB. Confirm this is the intended disk.",
                Severity.WARNING,
            )
        )
    if not disk.serial:
        warnings.append(
            Warning(
                "no-serial",
                f"{disk.stable_path} did not report a serial number.",
                Severity.INFO,
            )
        )
    return warnings


def partition_table_for(mode: Firmware, requested: Optional[str], disk_size_bytes: int) -> str:
    if mode is Firmware.UEFI:
        if requested and requested != "gpt":
            raise ValidationError("UEFI installations require a GPT partition table")
        return "gpt"
    table = requested or "gpt"
    if table not in ("gpt", "msdos"):
        raise ValidationError("partition table must be 'gpt' or 'msdos'")
    if table == "msdos" and disk_size_bytes >= 2 * 1024**4:
        raise ValidationError(
            "an MBR partition table cannot address a disk larger than 2 TiB; use GPT"
        )
    return table


def device_paths(disk: Disk) -> list[str]:
    return [disk.path] + [partition.path for partition in disk.partitions]
