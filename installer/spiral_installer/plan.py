from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, Sequence

from . import btrfs, disks, validate as v
from .config import LOCALE_RE, TIMEZONE_RE, KEYMAP_RE, Repository
from .errors import ValidationError
from .models import (
    BootPlan,
    CompressionPolicy,
    Disk,
    EncryptionPlan,
    Firmware,
    FsKind,
    HardwareReport,
    InstallPlan,
    LocalePlan,
    PartitionPlan,
    PartitionRole,
    PartitionTable,
    ReleaseInfo,
    Severity,
    SoftwarePlan,
    SubvolumePlan,
    SwapPlan,
    SwapStrategy,
    UserPlan,
    Warning,
)
from .profiles import DesktopProfile, DriverProfile, FilesystemProfile, KernelProfile, ProfileSet
from .util import dedupe, human_bytes

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
RESERVED_USERS = {"root", "daemon", "bin", "sys", "sync", "nobody", "systemd-network", "systemd-resolve"}
MIN_ROOT_MIB = 5120


class PackageResolver(Protocol):
    def missing(self, packages: Sequence[str]) -> list[str]:
        """Return packages that cannot be resolved from the configured repositories."""


@dataclass(frozen=True)
class InstallChoices:
    disk: Optional[str] = None
    filesystem: Optional[str] = None
    layout: Optional[str] = None
    compression: Optional[str] = None
    compression_algorithm: Optional[str] = None
    compression_level: Optional[int] = None
    swap: Optional[str] = None
    encryption: bool = False
    kernel: Optional[str] = None
    desktops: Optional[tuple[str, ...]] = None
    driver: Optional[str] = None
    package_groups: tuple[str, ...] = ()
    snapshots: Optional[bool] = None
    btrfs_maintenance: Optional[bool] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    hostname: Optional[str] = None
    autologin: Optional[bool] = None
    locale: Optional[str] = None
    keymap: Optional[str] = None
    timezone: Optional[str] = None
    partition_table: Optional[str] = None
    offline: bool = False
    mode: str = "guided"

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "InstallChoices":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValidationError(f"unknown unattended keys: {', '.join(unknown)}")
        values = dict(data)
        desktops = values.get("desktops")
        if desktops is not None:
            if not isinstance(desktops, list) or any(not isinstance(item, str) for item in desktops):
                raise ValidationError("'desktops' must be an array of profile ids")
            values["desktops"] = tuple(desktops)
        groups = values.get("package_groups")
        if groups is not None:
            if not isinstance(groups, list) or any(not isinstance(item, str) for item in groups):
                raise ValidationError("'package_groups' must be an array of group ids")
            values["package_groups"] = tuple(groups)
        for key in ("encryption", "autologin", "offline", "snapshots", "btrfs_maintenance"):
            if key in values and not isinstance(values[key], bool):
                raise ValidationError(f"'{key}' must be a boolean")
        for key in (
            "disk",
            "filesystem",
            "layout",
            "compression",
            "compression_algorithm",
            "swap",
            "kernel",
            "driver",
            "username",
            "full_name",
            "hostname",
            "locale",
            "keymap",
            "timezone",
            "partition_table",
            "mode",
        ):
            if key in values and values[key] is not None and not isinstance(values[key], str):
                raise ValidationError(f"'{key}' must be a string")
        level = values.get("compression_level")
        if level is not None and (isinstance(level, bool) or not isinstance(level, int)):
            raise ValidationError("'compression_level' must be an integer")
        try:
            return cls(**values)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValidationError(f"invalid unattended configuration: {exc}") from exc


@dataclass(frozen=True)
class Approval:
    phrase: str
    acknowledged: frozenset[str] = frozenset()


def confirmation_phrase(plan: InstallPlan) -> str:
    return f"ERASE {plan.disk.stable_path}"


class PlanBuilder:
    def __init__(
        self,
        repo: Repository,
        hardware: HardwareReport,
        release: ReleaseInfo,
        resolver: Optional[PackageResolver] = None,
        nvidia_package: Optional[str] = None,
        nvidia_supported: Optional[bool] = None,
    ) -> None:
        self.repo = repo
        self.hardware = hardware
        self.release = release
        self.resolver = resolver
        self.nvidia_package = nvidia_package
        self.nvidia_supported = nvidia_supported
        self.warnings: list[Warning] = []

    def _warn(self, code: str, message: str, severity: Severity = Severity.WARNING) -> None:
        warning = Warning(code=code, message=message, severity=severity)
        if warning not in self.warnings:
            self.warnings.append(warning)

    def _default(self, key: str) -> str:
        return self.repo.installer.default_str(key)

    def _default_bool(self, key: str) -> bool:
        return bool(self.repo.installer.defaults.get(key, False))

    def _missing(self, packages: Sequence[str]) -> list[str]:
        if self.resolver is None:
            return []
        return sorted(set(self.resolver.missing(packages)))

    def build(self, choices: InstallChoices) -> InstallPlan:
        if choices.mode != "guided":
            raise ValidationError(
                f"installation mode '{choices.mode}' is not implemented; guided mode is available"
            )
        self._validate_environment()
        if not HOSTNAME_RE.match(self.release.codename):
            raise ValidationError(f"invalid Debian codename '{self.release.codename}'")
        if choices.offline and not self.repo.build.offline_enabled:
            raise ValidationError("this Spiral ISO was built without an offline package repository")

        minimum_disk = max(self.repo.installer.minimum_disk_gib, self.repo.hardware.minimum_disk_gib)
        disk = disks.select_disk(
            self.hardware, choices.disk, self.repo.hardware, minimum_disk
        )
        self._check_disk_busy(disk)
        self.warnings.extend(disks.disk_warnings(disk, self.repo.hardware))

        filesystem = self.repo.profiles.filesystem(choices.filesystem or self._default("filesystem"))
        encryption = bool(choices.encryption)
        if encryption and not filesystem.supports_encryption:
            raise ValidationError(f"{filesystem.label} does not support encryption")

        swap = choices.swap or self._default("swap")
        try:
            swap_strategy = SwapStrategy(swap)
        except ValueError:
            raise ValidationError(
                "swap must be one of: " + ", ".join(item.value for item in SwapStrategy)
            ) from None
        self._validate_swap(swap_strategy, filesystem, encryption)
        if swap_strategy is SwapStrategy.PARTITION:
            swap_mib = self.repo.installer.swap_gib * 1024
        elif swap_strategy is SwapStrategy.FILE:
            swap_mib = self.repo.installer.swapfile_gib * 1024
        else:
            swap_mib = 0

        try:
            table = PartitionTable(
                disks.partition_table_for(self.hardware.firmware, choices.partition_table, disk.size_bytes)
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from None

        snapshots = self._choice_bool(choices.snapshots, "snapshots")
        maintenance = self._choice_bool(choices.btrfs_maintenance, "btrfs_maintenance")
        if snapshots and filesystem.kind != "btrfs":
            raise ValidationError("snapshots require BTRFS")
        if maintenance and filesystem.kind != "btrfs":
            raise ValidationError("BTRFS maintenance requires BTRFS")
        if swap_strategy is SwapStrategy.FILE and filesystem.kind != "btrfs":
            raise ValidationError("the implemented swapfile path requires BTRFS")

        partitions = self._partitions(
            disk=disk,
            table=table,
            encryption=encryption,
            swap=swap_strategy,
            swap_mib=swap_mib,
            root_fs=filesystem.kind,
        )
        compression = btrfs.resolve_compression(
            self.repo.installer,
            choices.compression,
            filesystem,
            algorithm=choices.compression_algorithm,
            level=choices.compression_level,
        )
        if filesystem.kind != "btrfs" and choices.compression not in (None, "none"):
            self._warn(
                "compression-ignored",
                f"{filesystem.label} does not support transparent compression; the BTRFS "
                "compression choice will not be applied.",
                Severity.INFO,
            )

        layout_id = choices.layout or filesystem.default_layout
        subvolumes: tuple[SubvolumePlan, ...] = ()
        if filesystem.kind == "btrfs":
            if layout_id is None or layout_id not in filesystem.layouts:
                raise ValidationError(
                    f"BTRFS layout '{layout_id}' does not exist; available: "
                    + ", ".join(sorted(filesystem.layouts))
                )
            layout = filesystem.layouts[layout_id]
            subvolumes = btrfs.build_subvolumes(
                layout,
                compression,
                snapshots=snapshots,
                swapfile=swap_strategy is SwapStrategy.FILE,
            )
            if snapshots and not any(s.mountpoint == "/.snapshots" for s in subvolumes):
                raise ValidationError(
                    f"layout '{layout_id}' has no /.snapshots subvolume; snapshots cannot be enabled"
                )
            if swap_strategy is SwapStrategy.FILE:
                btrfs.validate_swapfile(filesystem, subvolumes, self.repo.installer.swapfile_gib * 1024)
            if compression.algorithm != "none" and any(
                s.name == "@swap" and any(o.startswith("compress=") and o != "compress=no" for o in s.options)
                for s in subvolumes
            ):
                raise ValidationError("the @swap subvolume must not be compressed")

        boot = BootPlan(
            mode=self.hardware.firmware,
            partition_table=table,
            efi_partition=next((p for p in partitions if p.role is PartitionRole.ESP), None),
            separate_boot=encryption,
            secure_boot=bool(self.hardware.secure_boot),
        )

        software = self._software(
            choices,
            filesystem,
            partitions,
            compression,
            swap_strategy,
            snapshots,
            maintenance,
        )
        user = self._user(choices, software)
        locale = self._locale(choices)

        if encryption:
            self._warn(
                "encryption-no-recovery",
                "The LUKS passphrase cannot be recovered. Without it the installed system "
                "and its data are unrecoverable.",
                Severity.CRITICAL,
            )
        if swap_strategy is SwapStrategy.FILE:
            self._warn(
                "swapfile-resume",
                "Hibernation is not configured. The swapfile is created correctly, but resume "
                "requires an explicit kernel resume configuration.",
                Severity.WARNING,
            )
        if swap_strategy is SwapStrategy.ZRAM:
            self._warn(
                "zram-no-hibernation",
                "ZRAM swap does not support hibernation.",
                Severity.INFO,
            )
        if snapshots:
            self._warn(
                "snapshots-are-not-backups",
                "Snapshots protect against local mistakes, not disk failure or ransomware. "
                "They are not backups.",
                Severity.INFO,
            )
        if maintenance:
            self._warn(
                "no-automatic-balance",
                "A scrub timer is enabled. Spiral does not run automatic balance or repair.",
                Severity.INFO,
            )
        if table is PartitionTable.MBR:
            self._warn(
                "mbr-deprecated",
                "The MBR partition table is legacy. GPT is recommended where the firmware supports it.",
                Severity.WARNING,
            )
        if self.hardware.firmware is Firmware.BIOS:
            self._warn(
                "bios-mode",
                "The live medium booted in BIOS mode, so the installed system will use the legacy "
                "GRUB boot path.",
                Severity.INFO,
            )
        if self.hardware.secure_boot is None and self.hardware.firmware is Firmware.UEFI:
            self._warn(
                "secure-boot-unknown",
                "Secure Boot state could not be determined. The Debian signed boot chain will be "
                "installed, but third-party kernel modules may require enrollment.",
                Severity.WARNING,
            )
        for message in self.hardware.warnings:
            self._warn("hardware-probe", f"Hardware probe: {message}", Severity.INFO)
        self._warn(
            "partitioning-destructive",
            f"All partitions and data on {disk.stable_path} "
            f"({human_bytes(disk.size_bytes)}) will be destroyed.",
            Severity.CRITICAL,
        )
        return InstallPlan(
            release=self.release,
            architecture=self.repo.debian.architecture,
            disk=disk,
            firmware=self.hardware.firmware,
            partition_table=table,
            partitions=partitions,
            filesystem_id=filesystem.id,
            filesystem_kind=FsKind(filesystem.kind),
            subvolumes=subvolumes,
            compression=compression,
            swap=SwapPlan(strategy=swap_strategy, size_mib=swap_mib),
            encryption=EncryptionPlan(enabled=encryption),
            boot=boot,
            software=software,
            user=user,
            locale=locale,
            offline=choices.offline,
            warnings=tuple(self.warnings),
        )

    def _validate_environment(self) -> None:
        if self.hardware.architecture != self.repo.debian.architecture:
            raise ValidationError(
                f"the live medium is {self.hardware.architecture} but this build targets "
                f"{self.repo.debian.architecture}"
            )
        if self.hardware.ram_mib < self.repo.installer.minimum_ram_mib:
            raise ValidationError(
                f"{self.hardware.ram_mib} MiB of RAM detected; at least "
                f"{self.repo.installer.minimum_ram_mib} MiB is required"
            )
        if self.hardware.ram_mib < 4096:
            self._warn(
                "low-ram",
                f"{self.hardware.ram_mib} MiB of RAM is enough to install but tight for a full desktop.",
                Severity.WARNING,
            )
        if self.hardware.firmware is Firmware.UNKNOWN:
            raise ValidationError("could not determine whether the live medium booted in UEFI or BIOS mode")

    def _check_disk_busy(self, disk: Disk) -> None:
        if disk.is_busy:
            points = [p for part in disk.mounted_partitions for p in part.all_mountpoints]
            raise ValidationError(
                f"{disk.stable_path} is in use ({', '.join(points)}); it may be the live medium"
            )

    def _choice_bool(self, value: Optional[bool], key: str) -> bool:
        if value is not None:
            return value
        return bool(self.repo.installer.defaults.get(key, False))

    def _validate_swap(
        self,
        swap: SwapStrategy,
        filesystem: FilesystemProfile,
        encryption: bool,
    ) -> None:
        if swap is SwapStrategy.PARTITION and encryption:
            raise ValidationError(
                "an encryption-enabled install cannot use an unencrypted swap partition; "
                "choose ZRAM, a BTRFS swapfile or no swap"
            )
        if swap is SwapStrategy.PARTITION and not filesystem.supports_swap_partition:
            raise ValidationError(f"{filesystem.label} does not support a swap partition")
        if swap is SwapStrategy.FILE and not filesystem.supports_swap_file:
            raise ValidationError(f"{filesystem.label} does not support a swapfile")

    def _partitions(
        self,
        disk: Disk,
        table: PartitionTable,
        encryption: bool,
        swap: SwapStrategy,
        swap_mib: int,
        root_fs: str,
    ) -> tuple[PartitionPlan, ...]:
        cfg = self.repo.installer
        parts: list[PartitionPlan] = []
        number = 0

        def add(role: PartitionRole, size: Optional[int], fs: str, code: str, mount: Optional[str] = None) -> None:
            nonlocal number
            number += 1
            parts.append(
                PartitionPlan(
                    role=role,
                    number=number,
                    size_mib=size,
                    fs=fs,
                    type_code=code,
                    mountpoint=mount,
                )
            )

        if self.hardware.firmware is Firmware.UEFI:
            add(PartitionRole.ESP, cfg.esp_mib, "vfat", "EF00", "/boot/efi")
        elif table is PartitionTable.GPT:
            add(PartitionRole.BIOS_GRUB, cfg.bios_grub_mib, "none", "EF02")
        if encryption:
            add(PartitionRole.BOOT, cfg.boot_mib, "ext4", "8300", "/boot")
        if swap is SwapStrategy.PARTITION:
            add(PartitionRole.SWAP, swap_mib, "swap", "8200")
        fixed_mib = sum(part.size_mib or 0 for part in parts)
        disk_mib = disk.size_bytes // (1024**2)
        remaining = disk_mib - fixed_mib - 2
        if remaining < MIN_ROOT_MIB:
            raise ValidationError(
                f"the disk is too small for the requested layout: root would get {remaining} MiB; "
                f"at least {MIN_ROOT_MIB} MiB is required"
            )
        root_code = "8309" if encryption else "8300"
        root_fs_value = "luks" if encryption else root_fs
        add(PartitionRole.ROOT, remaining, root_fs_value, root_code, "/")
        return tuple(parts)

    def _kernel_packages(self, profile: KernelProfile) -> tuple[tuple[str, ...], tuple[str, ...]]:
        suffix = self.repo.debian.architecture_suffix

        def expand(values: Sequence[str]) -> tuple[str, ...]:
            return tuple(value.replace("{arch}", suffix) for value in values)

        return expand(profile.packages), expand(profile.fallback_packages)

    def _desktop_selection(self, choices: InstallChoices) -> list[DesktopProfile]:
        ids = choices.desktops or (self._default("desktop"),)
        if not ids:
            raise ValidationError("select at least one desktop profile or 'minimal'")
        if len(set(ids)) != len(ids):
            raise ValidationError("desktop profiles must not be repeated")
        return [self.repo.profiles.desktop(item) for item in ids]

    def _software(
        self,
        choices: InstallChoices,
        filesystem: FilesystemProfile,
        partitions: tuple[PartitionPlan, ...],
        compression: CompressionPolicy,
        swap: SwapStrategy,
        snapshots: bool,
        maintenance: bool,
    ) -> SoftwarePlan:
        profiles = self.repo.profiles
        kernel = profiles.kernel(choices.kernel or self._default("kernel"))
        if self.repo.debian.architecture not in kernel.arches:
            raise ValidationError(
                f"kernel profile '{kernel.id}' does not support {self.repo.debian.architecture}"
            )
        kernel_packages, fallback_packages = self._kernel_packages(kernel)
        missing_kernel = self._missing(kernel_packages)
        if missing_kernel:
            raise ValidationError(
                f"kernel profile '{kernel.id}' is unavailable on Debian {self.release.codename}: "
                + ", ".join(missing_kernel)
            )

        desktops = self._desktop_selection(choices)
        display_manager = desktops[0].display_manager
        desktop_packages: list[str] = []
        for desktop in desktops:
            desktop_packages.extend(desktop.packages)
            if desktop.status == "experimental":
                self._warn(
                    "experimental-desktop",
                    f"{desktop.label} is experimental on this Debian release.",
                    Severity.INFO,
                )
            missing = self._missing(desktop.packages)
            if missing:
                raise ValidationError(
                    f"desktop profile '{desktop.id}' needs packages that are unavailable: "
                    + ", ".join(missing)
                )
        if display_manager and display_manager not in desktop_packages:
            desktop_packages.append(display_manager)
        other_dms = {
            desktop.display_manager
            for desktop in desktops[1:]
            if desktop.display_manager and desktop.display_manager != display_manager
        }
        if other_dms:
            self._warn(
                "multiple-display-managers",
                f"Only {display_manager} will be installed. Other selected desktops expect: "
                + ", ".join(sorted(other_dms)),
                Severity.WARNING,
            )

        driver = self._driver(choices)
        driver_packages = list(driver["packages"])
        driver_required = list(driver["required"])
        if driver["headers"]:
            headers = f"linux-headers-{self.repo.debian.architecture_suffix}"
            if headers not in driver_packages:
                driver_packages.append(headers)
            driver_required.append(headers)

        group_ids = dedupe(
            list(self.repo.installer.default_groups)
            + list(choices.package_groups)
            + list(kernel.package_groups)
        )
        group_packages: list[str] = []
        foreign_arches: list[str] = []
        required: list[str] = list(kernel_packages) + list(desktop_packages) + driver_required
        required.extend(filesystem.tools)
        for group_id in group_ids:
            group = profiles.package_group(group_id)
            if choices.offline and not group.offline:
                raise ValidationError(
                    f"package group '{group_id}' is not available on the offline medium"
                )
            group_packages.extend(group.packages)
            required.extend(group.required_available)
            for arch in group.architectures:
                if arch not in foreign_arches:
                    foreign_architectures.append(arch)
        for arch in foreign_arches:
            if self.repo.debian.architecture != "amd64" or arch != "i386":
                raise ValidationError(f"foreign architecture '{arch}' is not supported by this build")

        extra_required: list[str] = []
        if profiles.filesystem(filesystem.id).kind == "btrfs" or any(
            part.fs == "luks" for part in partitions
        ):
            extra_required.extend(filesystem.tools)
        if swap is SwapStrategy.ZRAM:
            extra_required.append("zram-tools")
        if snapshots:
            extra_required.extend(["snapper"])
        if maintenance:
            extra_required.extend(["btrfsmaintenance"])
        if any(part.fs == "luks" for part in partitions):
            extra_required.extend(["cryptsetup", "cryptsetup-initramfs"])
        if self.hardware.firmware is Firmware.UEFI:
            extra_required.extend(["grub-efi-" + self.repo.debian.architecture, "efibootmgr"])
            if self.repo.debian.architecture == "amd64":
                extra_required.extend(["grub-efi-amd64-signed", "shim-signed"])
        else:
            extra_required.extend(["grub-pc"])

        all_packages = dedupe(
            list(kernel_packages)
            + list(fallback_packages)
            + list(desktop_packages)
            + list(group_packages)
            + list(driver_packages)
            + list(driver["packages"])
            + list(filesystem.tools)
            + extra_required
        )
        required = dedupe(required + extra_required)
        if self.resolver is not None:
            missing = set(self._missing(all_packages))
            fatal = sorted(missing.intersection(required))
            if fatal:
                raise ValidationError(
                    "required packages are unavailable on Debian "
                    f"{self.release.codename}: " + ", ".join(fatal)
                )
            optional_missing = sorted(missing.difference(required))
            if optional_missing:
                all_packages = [pkg for pkg in all_packages if pkg not in optional_missing]
                self._warn(
                    "optional-packages-skipped",
                    "Optional packages unavailable on this release were skipped: "
                    + ", ".join(optional_missing),
                    Severity.INFO,
                )
            fallback_packages = tuple(
                pkg for pkg in fallback_packages if pkg not in missing
            )
        if choices.offline:
            allowed = {
                pkg
                for group_id in self.repo.build.offline_package_groups
                for pkg in profiles.package_group(group_id).packages
            }
            unavailable = sorted(set(all_packages) - allowed)
            if unavailable:
                raise ValidationError(
                    "offline install cannot satisfy these packages from the medium: "
                    + ", ".join(unavailable)
                )

        if driver["note"]:
            self._warn("secure-boot-driver", driver["note"], driver["severity"])

        return SoftwarePlan(
            kernel_id=kernel.id,
            kernel_label=kernel.label,
            kernel_packages=kernel_packages,
            fallback_kernel_packages=fallback_packages,
            desktop_ids=tuple(desktop.id for desktop in desktops),
            desktop_labels=tuple(desktop.label for desktop in desktops),
            desktop_packages=tuple(desktop_packages),
            display_manager=display_manager,
            driver_id=driver["id"],
            driver_label=driver["label"],
            driver_packages=tuple(driver["packages"]),
            driver_modules=tuple(driver["modules"]),
            package_groups=tuple(group_ids),
            packages=tuple(all_packages),
            foreign_architectures=tuple(foreign_arches),
            snapshots=snapshots,
            btrfs_maintenance=maintenance,
        )

    def _driver(self, choices: InstallChoices) -> dict:
        requested = choices.driver or self._default("driver")
        nvidia = bool(self.hardware.nvidia_gpus)
        if requested == "auto":
            if nvidia and self.nvidia_supported is not False:
                selected = self.repo.profiles.driver("nvidia-proprietary")
                if self.hardware.secure_boot and self.repo.hardware.allow_nouveau_fallback:
                    fallback = self.repo.profiles.driver("nouveau")
                    selected = fallback
                    self._warn(
                        "secure-boot-signed-driver",
                        "Secure Boot is enabled. Nouveau is selected because the proprietary "
                        "NVIDIA modules would need MOK enrollment before they could load. "
                        "Spiral does not disable Secure Boot.",
                        Severity.WARNING,
                    )
            elif nvidia:
                selected = self.repo.profiles.driver("nouveau")
                self._warn(
                    "nvidia-unsupported",
                    "Debian's NVIDIA detector did not report support for this GPU; Nouveau "
                    "will be used. The proprietary driver cannot be guaranteed on this hardware.",
                    Severity.WARNING,
                )
            elif any(gpu.vendor.value == "amd" for gpu in self.hardware.gpus):
                selected = self.repo.profiles.driver("amd")
            elif any(gpu.vendor.value == "intel" for gpu in self.hardware.gpus):
                selected = self.repo.profiles.driver("intel")
            else:
                selected = self.repo.profiles.driver("generic")
        else:
            selected = self.repo.profiles.driver(requested)

        if requested != "auto" and nvidia:
            if selected.vendor != "nvidia":
                self._warn(
                    "driver-vendor-mismatch",
                    f"Driver profile '{selected.id}' was selected explicitly but the detected "
                    "GPU is NVIDIA.",
                    Severity.WARNING,
                )
        if nvidia and len(self.hardware.nvidia_gpus) > 1:
            self._warn(
                "multi-gpu",
                "Multiple NVIDIA GPUs detected. PRIME and multi-GPU configuration is not "
                "automatic; review the installed driver configuration.",
                Severity.INFO,
            )
        hybrid = nvidia and any(
            gpu.vendor.value in ("intel", "amd") for gpu in self.hardware.gpus
        )
        if hybrid:
            self._warn(
                "hybrid-graphics",
                "Hybrid graphics detected. The primary GPU driver is installed; switching "
                "between GPUs requires desktop-specific PRIME configuration.",
                Severity.INFO,
            )

        packages = list(selected.packages)
        required = list(selected.required_available)
        label = selected.label
        note = ""
        severity = Severity.WARNING
        if (
            selected.id == "nvidia-proprietary"
            and self.nvidia_package
            and requested in ("auto", "nvidia-proprietary")
        ):
            v.package_name(self.nvidia_package, "nvidia-detect")
            if self.nvidia_package not in packages:
                if self._missing([self.nvidia_package]):
                    self._warn(
                        "nvidia-detect-unavailable",
                        f"nvidia-detect recommended '{self.nvidia_package}', but that package is "
                        "not available. The default NVIDIA meta-package will be used and "
                        "compatibility is verified by APT during installation.",
                        Severity.WARNING,
                    )
                else:
                    packages = [pkg for pkg in packages if pkg != "nvidia-driver"]
                    packages.insert(0, self.nvidia_package)
                    required = [pkg for pkg in required if pkg != "nvidia-driver"]
                    required.insert(0, self.nvidia_package)
                    label = f"{label} ({self.nvidia_package})"

        if selected.secure_boot == "requires_enrollment":
            if self.hardware.secure_boot:
                if requested == "auto":
                    if self.repo.hardware.allow_nouveau_fallback:
                        fallback = self.repo.profiles.driver("nouveau")
                        if not self._missing(fallback.required_available):
                            return self._driver_dict(fallback, note="")
                        packages = list(fallback.packages)
                        required = list(fallback.required_available)
                        selected = fallback
                note = (
                    "Secure Boot is enabled and the proprietary modules require MOK "
                    "enrollment after installation. Until then they will not load. "
                    "Spiral does not disable Secure Boot."
                )
                severity = Severity.CRITICAL
            elif self.hardware.secure_boot is None:
                note = (
                    "Secure Boot state is unknown. If it is enabled, the proprietary modules "
                    "require MOK enrollment before they can load."
                )
                severity = Severity.CRITICAL

        missing = self._missing(required)
        if missing:
            if selected.fallback:
                fallback = self.repo.profiles.driver(selected.fallback)
                if not self._missing(fallback.required_available):
                    self._warn(
                        "driver-fallback",
                        f"Driver '{selected.id}' is unavailable ({', '.join(missing)}); "
                        f"using '{fallback.id}' instead.",
                        Severity.WARNING,
                    )
                    return self._driver_dict(fallback, note="")
            raise ValidationError(
                f"driver profile '{selected.id}' requires packages that are unavailable: "
                + ", ".join(missing)
            )
        return self._driver_dict(selected, note=note, severity=severity, packages=packages, required=required, label=label)

    def _driver_dict(
        self,
        profile: DriverProfile,
        note: str,
        severity: Severity = Severity.WARNING,
        packages: Optional[list[str]] = None,
        required: Optional[list[str]] = None,
        label: Optional[str] = None,
    ) -> dict:
        return {
            "id": profile.id,
            "label": label or profile.label,
            "packages": packages or list(profile.packages),
            "required": required or list(profile.required_available),
            "modules": list(profile.modules),
            "headers": profile.requires_headers,
            "note": note,
            "severity": severity,
        }

    def _user(self, choices: InstallChoices, software: SoftwarePlan) -> UserPlan:
        username = choices.username or self._default("username")
        hostname = choices.hostname or self._default("hostname")
        full_name = choices.full_name or "Spiral User"
        autologin = choices.autologin if choices.autologin is not None else self.repo.installer.default_bool("autologin")
        if not USERNAME_RE.match(username) or username in RESERVED_USERS:
            raise ValidationError(
                "username must start with a lowercase letter or underscore, use only "
                "lowercase letters, digits, underscores and hyphens, and must not be reserved"
            )
        if not HOSTNAME_RE.match(hostname) or len(hostname) > 63:
            raise ValidationError("hostname must be a valid single DNS label")
        if not full_name.strip() or len(full_name) > 80 or any(ch in full_name for ch in ":\n\r\t"):
            raise ValidationError("full name must be 1-80 characters and contain no colons or control characters")
        if autologin and software.display_manager not in ("gdm3", "lightdm", "sddm"):
            raise ValidationError("automatic login requires a supported graphical display manager")
        return UserPlan(
            username=username,
            full_name=full_name.strip(),
            hostname=hostname,
            autologin=bool(autologin),
        )

    def _locale(self, choices: InstallChoices) -> LocalePlan:
        locale = choices.locale or self._default("locale")
        keymap = choices.keymap or self._default("keymap")
        timezone = choices.timezone or self._default("timezone")
        if not LOCALE_RE.match(locale):
            raise ValidationError("locale must look like en_US.UTF-8")
        if not KEYMAP_RE.match(keymap):
            raise ValidationError("keymap contains invalid characters")
        if not TIMEZONE_RE.match(timezone) or ".." in timezone:
            raise ValidationError("timezone is not a valid zone name")
        return LocalePlan(locale=locale, keymap=keymap, timezone=timezone)


def validate_approval(plan: InstallPlan, approval: Optional[Approval]) -> None:
    if approval is None:
        raise ValidationError("destructive installation requires explicit confirmation")
    expected = confirmation_phrase(plan)
    if approval.phrase != expected:
        raise ValidationError(
            f"confirmation phrase does not match; expected exactly: {expected}"
        )
    missing = sorted(
        warning.code for warning in plan.warnings if warning.requires_ack and warning.code not in approval.acknowledged
    )
    if missing:
        raise ValidationError(
            "these warnings must be acknowledged: " + ", ".join(missing)
        )
