from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .util import Secret


class Firmware(str, Enum):
    UEFI = "uefi"
    BIOS = "bios"
    UNKNOWN = "unknown"


class GpuVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    VIRTUAL = "virtual"
    UNKNOWN = "unknown"


class FsKind(str, Enum):
    BTRFS = "btrfs"
    EXT4 = "ext4"
    XFS = "xfs"


class PartitionRole(str, Enum):
    ESP = "esp"
    BOOT = "boot"
    BIOS_GRUB = "bios_grub"
    SWAP = "swap"
    ROOT = "root"


class PartitionTable(str, Enum):
    GPT = "gpt"
    MBR = "msdos"


class SwapStrategy(str, Enum):
    NONE = "none"
    ZRAM = "zram"
    PARTITION = "partition"
    FILE = "file"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Warning:
    code: str
    message: str
    severity: Severity = Severity.WARNING

    @property
    def requires_ack(self) -> bool:
        return self.severity is Severity.CRITICAL


@dataclass(frozen=True)
class Partition:
    path: str
    number: int
    size_bytes: int
    fstype: Optional[str] = None
    mountpoints: tuple[str, ...] = ()
    label: Optional[str] = None
    parttype: Optional[str] = None
    children: tuple["Partition", ...] = ()

    @property
    def mounted(self) -> bool:
        if self.mountpoints:
            return True
        return any(child.mounted for child in self.children)

    @property
    def all_mountpoints(self) -> tuple[str, ...]:
        points = list(self.mountpoints)
        for child in self.children:
            points.extend(child.all_mountpoints)
        return tuple(points)


@dataclass(frozen=True)
class Disk:
    path: str
    size_bytes: int
    model: str = ""
    serial: str = ""
    transport: str = ""
    removable: bool = False
    read_only: bool = False
    by_id: Optional[str] = None
    partitions: tuple[Partition, ...] = ()

    @property
    def stable_path(self) -> str:
        return self.by_id or self.path

    @property
    def mounted_partitions(self) -> tuple[Partition, ...]:
        return tuple(p for p in self.partitions if p.mounted)

    @property
    def is_busy(self) -> bool:
        return bool(self.mounted_partitions) or self.read_only

    @property
    def has_foreign_os(self) -> bool:
        foreign = {"ntfs", "ntfs3", "exfat", "hfsplus", "apfs"}
        return any(p.fstype in foreign for p in self.partitions)


@dataclass(frozen=True)
class Gpu:
    address: str
    vendor_id: str
    device_id: str
    vendor: GpuVendor
    model: str = ""
    driver: Optional[str] = None


@dataclass(frozen=True)
class NetworkAdapter:
    name: str
    kind: str
    driver: Optional[str] = None
    wireless: bool = False
    state: Optional[str] = None


@dataclass(frozen=True)
class HardwareReport:
    architecture: str
    cpu_model: str
    cpu_cores: int
    ram_mib: int
    firmware: Firmware
    secure_boot: Optional[bool]
    gpus: tuple[Gpu, ...] = ()
    network: tuple[NetworkAdapter, ...] = ()
    disks: tuple[Disk, ...] = ()
    virtualization: Optional[str] = None
    bluetooth: bool = False
    audio_controllers: tuple[str, ...] = ()
    usb_controllers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def nvidia_gpus(self) -> tuple[Gpu, ...]:
        return tuple(g for g in self.gpus if g.vendor is GpuVendor.NVIDIA)

    @property
    def internal_disks(self) -> tuple[Disk, ...]:
        return tuple(d for d in self.disks if not d.removable)


@dataclass(frozen=True)
class ReleaseInfo:
    suite: str
    codename: str
    version: str
    source: str
    generated_at: str


@dataclass(frozen=True)
class CompressionPolicy:
    id: str
    algorithm: str
    level: Optional[int] = None

    @property
    def mount_option(self) -> Optional[str]:
        if self.algorithm == "none":
            return None
        if self.algorithm == "lzo":
            return "compress=lzo"
        if self.level is None:
            return f"compress={self.algorithm}"
        return f"compress={self.algorithm}:{self.level}"


@dataclass(frozen=True)
class PartitionPlan:
    role: PartitionRole
    number: int
    size_mib: Optional[int]
    fs: str
    type_code: str
    mountpoint: Optional[str] = None

    @property
    def is_encrypted(self) -> bool:
        return self.fs == "luks"


@dataclass(frozen=True)
class SubvolumePlan:
    name: str
    mountpoint: str
    options: tuple[str, ...] = ()

    @property
    def subvol_option(self) -> str:
        return f"subvol={self.name}"


@dataclass(frozen=True)
class SwapPlan:
    strategy: SwapStrategy
    size_mib: int = 0


@dataclass(frozen=True)
class EncryptionPlan:
    enabled: bool
    mapper_name: str = "cryptroot"
    luks_type: str = "luks2"
    pbkdf: str = "argon2id"


@dataclass(frozen=True)
class BootPlan:
    mode: Firmware
    partition_table: PartitionTable
    efi_partition: Optional[PartitionPlan]
    separate_boot: bool

    @property
    def is_uefi(self) -> bool:
        return self.mode is Firmware.UEFI


@dataclass(frozen=True)
class UserPlan:
    username: str
    full_name: str
    hostname: str
    groups: tuple[str, ...] = ("sudo", "audio", "video", "plugdev", "netdev")
    autologin: bool = False
    shell: str = "/bin/bash"


@dataclass(frozen=True)
class LocalePlan:
    locale: str
    keymap: str
    timezone: str


@dataclass(frozen=True)
class SoftwarePlan:
    kernel_id: str
    kernel_label: str
    kernel_packages: tuple[str, ...]
    fallback_kernel_packages: tuple[str, ...]
    desktop_ids: tuple[str, ...]
    desktop_labels: tuple[str, ...]
    desktop_packages: tuple[str, ...]
    display_manager: Optional[str]
    driver_id: str
    driver_label: str
    driver_packages: tuple[str, ...]
    driver_modules: tuple[str, ...]
    package_groups: tuple[str, ...]
    packages: tuple[str, ...]
    foreign_architectures: tuple[str, ...]
    snapshots: bool = False
    btrfs_maintenance: bool = False


@dataclass(frozen=True)
class InstallPlan:
    release: ReleaseInfo
    architecture: str
    disk: Disk
    firmware: Firmware
    partition_table: PartitionTable
    partitions: tuple[PartitionPlan, ...]
    filesystem_id: str
    filesystem_kind: FsKind
    subvolumes: tuple[SubvolumePlan, ...]
    compression: CompressionPolicy
    swap: SwapPlan
    encryption: EncryptionPlan
    boot: BootPlan
    software: SoftwarePlan
    user: UserPlan
    locale: LocalePlan
    offline: bool
    warnings: tuple[Warning, ...] = ()

    @property
    def esp(self) -> Optional[PartitionPlan]:
        return next((p for p in self.partitions if p.role is PartitionRole.ESP), None)

    @property
    def boot_partition(self) -> Optional[PartitionPlan]:
        return next((p for p in self.partitions if p.role is PartitionRole.BOOT), None)

    @property
    def root_partition(self) -> PartitionPlan:
        for part in self.partitions:
            if part.role is PartitionRole.ROOT:
                return part
        raise ValueError("plan has no root partition")

    @property
    def swap_partition(self) -> Optional[PartitionPlan]:
        return next((p for p in self.partitions if p.role is PartitionRole.SWAP), None)

    @property
    def root_device(self) -> str:
        from .util import partition_path

        raw = partition_path(self.disk.path, self.root_partition.number)
        if self.encryption.enabled:
            return f"/dev/mapper/{self.encryption.mapper_name}"
        return raw

    @property
    def requires_ack(self) -> bool:
        return any(w.requires_ack for w in self.warnings)

    def summary(self) -> dict:
        return {
            "release": {
                "suite": self.release.suite,
                "codename": self.release.codename,
                "version": self.release.version,
            },
            "disk": {
                "path": self.disk.path,
                "stable_path": self.disk.stable_path,
                "model": self.disk.model,
                "size_bytes": self.disk.size_bytes,
            },
            "firmware": self.firmware.value,
            "partition_table": self.partition_table.value,
            "partitions": [
                {
                    "role": p.role.value,
                    "number": p.number,
                    "size_mib": p.size_mib,
                    "fs": p.fs,
                }
                for p in self.partitions
            ],
            "filesystem": self.filesystem_id,
            "compression": {
                "profile": self.compression.id,
                "mount_option": self.compression.mount_option,
            },
            "subvolumes": [
                {"name": s.name, "mountpoint": s.mountpoint, "options": list(s.options)}
                for s in self.subvolumes
            ],
            "swap": {"strategy": self.swap.strategy.value, "size_mib": self.swap.size_mib},
            "encryption": {
                "enabled": self.encryption.enabled,
                "type": self.encryption.luks_type,
                "mapper": self.encryption.mapper_name,
            },
            "boot": {
                "mode": self.boot.mode.value,
                "partition_table": self.boot.partition_table.value,
                "separate_boot": self.boot.separate_boot,
            },
            "kernel": self.software.kernel_id,
            "desktops": list(self.software.desktop_ids),
            "driver": self.software.driver_id,
            "display_manager": self.software.display_manager,
            "package_groups": list(self.software.package_groups),
            "packages": list(self.software.packages),
            "user": {
                "username": self.user.username,
                "hostname": self.user.hostname,
                "autologin": self.user.autologin,
            },
            "locale": {
                "locale": self.locale.locale,
                "keymap": self.locale.keymap,
                "timezone": self.locale.timezone,
            },
            "snapshots": self.software.snapshots,
            "btrfs_maintenance": self.software.btrfs_maintenance,
            "offline": self.offline,
            "warnings": [
                {"code": w.code, "message": w.message, "severity": w.severity.value}
                for w in self.warnings
            ],
        }


@dataclass
class InstallSecrets:
    user_password: Secret = field(repr=False)
    luks_passphrase: Optional[Secret] = field(default=None, repr=False)
