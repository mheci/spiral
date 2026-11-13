from __future__ import annotations

from pathlib import Path
from typing import Optional

from spiral_installer.config import Repository, load_repository
from spiral_installer.models import (
    Disk,
    Firmware,
    HardwareReport,
    ReleaseInfo,
)

ROOT = Path(__file__).resolve().parents[2]


def make_repo() -> Repository:
    return load_repository(ROOT)


def make_release(codename: str = "trixie") -> ReleaseInfo:
    return ReleaseInfo(
        suite="stable",
        codename=codename,
        version="13.0",
        source="unit test",
        generated_at="2026-11-13T00:00:00+00:00",
    )


def make_disk(
    path: str = "/dev/sda",
    size_gib: int = 100,
    firmware: Firmware = Firmware.UEFI,
    secure_boot: Optional[bool] = False,
    gpus=(),
    disk_path: Optional[str] = None,
) -> tuple[HardwareReport, Disk]:
    disk = Disk(
        path=path,
        size_bytes=size_gib * 1024**3,
        model="Test Disk",
        serial="TEST123",
        transport="sata",
        removable=False,
        by_id=f"/dev/disk/by-id/ata-{path.rsplit('/', 1)[-1]}",
    )
    report = HardwareReport(
        architecture="amd64",
        cpu_model="Test CPU",
        cpu_cores=8,
        ram_mib=16384,
        firmware=firmware,
        secure_boot=secure_boot,
        gpus=tuple(gpus),
        disks=(disk,),
    )
    return report, disk
