from __future__ import annotations

from typing import Optional

from .config import InstallerConfig
from .errors import ValidationError
from .models import CompressionPolicy, SubvolumePlan
from .profiles import FilesystemProfile, LayoutSpec


def make_compression_policy(
    policy_id: str, algorithm: str, level: Optional[int]
) -> CompressionPolicy:
    allowed = ("zstd", "zlib", "lzo", "none")
    if algorithm not in allowed:
        raise ValidationError(f"unsupported compression algorithm '{algorithm}'")
    if algorithm == "zstd":
        if level is not None and not 1 <= level <= 15:
            raise ValidationError("zstd compression level must be between 1 and 15")
    elif algorithm == "zlib":
        if level is not None and not 1 <= level <= 9:
            raise ValidationError("zlib compression level must be between 1 and 9")
    elif level is not None:
        raise ValidationError(f"{algorithm} compression does not take a level")
    return CompressionPolicy(id=policy_id, algorithm=algorithm, level=level)


def resolve_compression(
    config: InstallerConfig,
    policy_id: Optional[str],
    filesystem: FilesystemProfile,
    algorithm: Optional[str] = None,
    level: Optional[int] = None,
) -> CompressionPolicy:
    if filesystem.kind != "btrfs":
        return CompressionPolicy(id="none", algorithm="none")
    policy_id = policy_id or "balanced"
    if policy_id == "custom":
        if not algorithm:
            raise ValidationError("custom compression requires an algorithm")
        return make_compression_policy("custom", algorithm, level)
    try:
        return config.compression[policy_id]
    except KeyError:
        raise ValidationError(
            f"unknown compression policy '{policy_id}'; available: "
            f"{', '.join(sorted(config.compression))}"
        ) from None


def build_subvolumes(
    layout: LayoutSpec,
    compression: CompressionPolicy,
    snapshots: bool,
    swapfile: bool,
) -> tuple[SubvolumePlan, ...]:
    result: list[SubvolumePlan] = []
    for spec in layout.active(snapshots=snapshots, swapfile=swapfile):
        options = [f"subvol={spec.name}"]
        if compression.algorithm != "none":
            if spec.compressed and compression.mount_option:
                options.append(compression.mount_option)
            elif not spec.compressed:
                options.append("compress=no")
        if spec.noatime:
            options.append("noatime")
        result.append(
            SubvolumePlan(
                name=spec.name,
                mountpoint=spec.mountpoint,
                options=tuple(options),
            )
        )
    return tuple(result)


def validate_swapfile(
    filesystem: FilesystemProfile,
    subvolumes: tuple[SubvolumePlan, ...],
    swap_mib: int,
) -> None:
    if not filesystem.supports_swap_file:
        raise ValidationError(f"{filesystem.label} does not support a swapfile")
    swap_subvol = next((s for s in subvolumes if s.name == "@swap"), None)
    if swap_subvol is None:
        raise ValidationError("BTRFS swapfile requires an @swap subvolume in the layout")
    if any(option.startswith("compress=") and option != "compress=no" for option in swap_subvol.options):
        raise ValidationError("the @swap subvolume must not be compressed")
    if swap_mib < 512:
        raise ValidationError("swapfile must be at least 512 MiB")


def swapfile_requires_manual_nocow(compression: CompressionPolicy) -> bool:
    """A swapfile cannot live on a compressed subvolume, even when files opt out."""
    return compression.algorithm != "none"


def render_swapfile_fstab(device_uuid: str, swapfile_path: str) -> str:
    return f"UUID={device_uuid} {swapfile_path} none swap sw 0 0\n"
