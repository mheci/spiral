from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import validate as v
from .errors import ConfigError
from .util import dedupe


@dataclass(frozen=True)
class SubvolumeSpec:
    name: str
    mountpoint: str
    compressed: bool
    noatime: bool
    when: Optional[str] = None


@dataclass(frozen=True)
class LayoutSpec:
    id: str
    label: str
    description: str
    subvolumes: tuple[SubvolumeSpec, ...]

    def active(self, snapshots: bool, swapfile: bool) -> tuple[SubvolumeSpec, ...]:
        result = []
        for sub in self.subvolumes:
            if sub.when == "snapshots" and not snapshots:
                continue
            if sub.when == "swapfile" and not swapfile:
                continue
            result.append(sub)
        return tuple(result)


@dataclass(frozen=True)
class FilesystemProfile:
    id: str
    label: str
    kind: str
    tools: tuple[str, ...]
    supports_encryption: bool
    supports_swap_partition: bool
    supports_swap_file: bool
    default_layout: Optional[str]
    layouts: dict[str, LayoutSpec]


@dataclass(frozen=True)
class KernelProfile:
    id: str
    label: str
    description: str
    packages: tuple[str, ...]
    fallback_packages: tuple[str, ...]
    arches: tuple[str, ...]
    requires_packages_available: tuple[str, ...]
    package_groups: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class DesktopProfile:
    id: str
    kind: str
    label: str
    description: str
    packages: tuple[str, ...]
    display_manager: Optional[str]
    session: Optional[str]
    status: str
    requires_packages_available: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class DriverProfile:
    id: str
    label: str
    description: str
    vendor: str
    packages: tuple[str, ...]
    required_available: tuple[str, ...]
    modules: tuple[str, ...]
    requires_headers: bool
    secure_boot: str
    fallback: Optional[str]
    status: str
    notes: str


@dataclass(frozen=True)
class PackageGroup:
    id: str
    label: str
    category: str
    description: str
    packages: tuple[str, ...]
    required_available: tuple[str, ...]
    architectures: tuple[str, ...]
    default: bool
    offline: bool


@dataclass(frozen=True)
class ProfileSet:
    filesystems: dict[str, FilesystemProfile]
    kernels: dict[str, KernelProfile]
    desktops: dict[str, DesktopProfile]
    drivers: dict[str, DriverProfile]
    packages: dict[str, PackageGroup]

    def kernel(self, profile_id: str) -> KernelProfile:
        try:
            return self.kernels[profile_id]
        except KeyError:
            raise ConfigError(
                f"unknown kernel profile '{profile_id}'; available: {', '.join(sorted(self.kernels))}"
            ) from None

    def desktop(self, profile_id: str) -> DesktopProfile:
        try:
            return self.desktops[profile_id]
        except KeyError:
            raise ConfigError(
                f"unknown desktop profile '{profile_id}'; available: {', '.join(sorted(self.desktops))}"
            ) from None

    def filesystem(self, profile_id: str) -> FilesystemProfile:
        try:
            return self.filesystems[profile_id]
        except KeyError:
            raise ConfigError(
                f"unknown filesystem profile '{profile_id}'; available: {', '.join(sorted(self.filesystems))}"
            ) from None

    def driver(self, profile_id: str) -> DriverProfile:
        try:
            return self.drivers[profile_id]
        except KeyError:
            raise ConfigError(
                f"unknown driver profile '{profile_id}'; available: {', '.join(sorted(self.drivers))}"
            ) from None

    def package_group(self, group_id: str) -> PackageGroup:
        try:
            return self.packages[group_id]
        except KeyError:
            raise ConfigError(
                f"unknown package group '{group_id}'; available: {', '.join(sorted(self.packages))}"
            ) from None


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: top level must be an object")
    return value


def _load_dir(path: Path, label: str) -> list[tuple[Path, dict]]:
    if not path.is_dir():
        raise ConfigError(f"missing profile directory: {path}")
    files = sorted(path.glob("*.json"))
    if not files:
        raise ConfigError(f"no {label} profiles in {path}")
    return [(item, _load_json(item)) for item in files]


def _load_layout(path: Path, layout_id: str, data: dict) -> LayoutSpec:
    ctx = f"{path} layouts.{layout_id}"
    data = v.mapping(data, ctx)
    v.check_keys(data, ctx, {"label", "description", "subvolumes"}, {"label", "subvolumes"})
    label = v.text(data, "label", ctx)
    description = v.text(data, "description", ctx, default="")
    entries = data["subvolumes"]
    if not isinstance(entries, list) or not entries:
        v.fail(ctx, "'subvolumes' must be a non-empty array")
    subvolumes: list[SubvolumeSpec] = []
    names: set[str] = set()
    mountpoints: set[str] = set()
    for index, entry in enumerate(entries):
        sub_ctx = f"{ctx}.subvolumes[{index}]"
        entry = v.mapping(entry, sub_ctx)
        v.check_keys(
            entry,
            sub_ctx,
            {"name", "mountpoint", "compressed", "noatime", "when"},
            {"name", "mountpoint", "compressed", "noatime"},
        )
        name = v.text(entry, "name", sub_ctx)
        if not v.SUBVOL_RE.match(name):
            v.fail(sub_ctx, f"invalid subvolume name {name!r}")
        mountpoint = v.text(entry, "mountpoint", sub_ctx)
        if not mountpoint.startswith("/") or ".." in Path(mountpoint).parts:
            v.fail(sub_ctx, f"invalid mountpoint {mountpoint!r}")
        if mountpoint != "/" and mountpoint.endswith("/"):
            v.fail(sub_ctx, "mountpoint must not end with a slash")
        if any(ch.isspace() for ch in mountpoint):
            v.fail(sub_ctx, "mountpoint must not contain whitespace")
        when = v.optional_text(entry, "when", sub_ctx)
        if when not in (None, "snapshots", "swapfile"):
            v.fail(sub_ctx, "'when' must be null, 'snapshots' or 'swapfile'")
        if name in names:
            v.fail(sub_ctx, f"duplicate subvolume {name!r}")
        if mountpoint in mountpoints:
            v.fail(sub_ctx, f"duplicate mountpoint {mountpoint!r}")
        names.add(name)
        mountpoints.add(mountpoint)
        subvolumes.append(
            SubvolumeSpec(
                name=name,
                mountpoint=mountpoint,
                compressed=v.boolean(entry, "compressed", sub_ctx),
                noatime=v.boolean(entry, "noatime", sub_ctx),
                when=when,
            )
        )
    if "/" not in mountpoints:
        v.fail(ctx, "a layout requires a subvolume mounted at /")
    return LayoutSpec(id=layout_id, label=label, description=description, subvolumes=tuple(subvolumes))


def _filesystem(path: Path, data: dict) -> FilesystemProfile:
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "id",
            "label",
            "kind",
            "tools",
            "supports_encryption",
            "supports_swap_partition",
            "supports_swap_file",
            "default_layout",
            "layouts",
            "notes",
        },
        {
            "id",
            "label",
            "kind",
            "tools",
            "supports_encryption",
            "supports_swap_partition",
            "supports_swap_file",
            "default_layout",
            "layouts",
        },
    )
    profile_id = v.identifier(data, "id", ctx)
    kind = v.text(data, "kind", ctx, choices=("btrfs", "ext4", "xfs"))
    layouts_data = data["layouts"]
    if not isinstance(layouts_data, dict):
        v.fail(ctx, "'layouts' must be an object")
    layouts = {key: _load_layout(path, key, value) for key, value in layouts_data.items()}
    default_layout = v.optional_text(data, "default_layout", ctx)
    if default_layout is not None and default_layout not in layouts:
        v.fail(ctx, f"default_layout '{default_layout}' does not exist")
    if kind == "btrfs" and default_layout is None:
        v.fail(ctx, "btrfs requires a default layout")
    if kind != "btrfs" and layouts:
        v.fail(ctx, "only btrfs profiles may define subvolume layouts")
    return FilesystemProfile(
        id=profile_id,
        label=v.text(data, "label", ctx),
        kind=kind,
        tools=tuple(v.package_list(data, "tools", ctx)),
        supports_encryption=v.boolean(data, "supports_encryption", ctx),
        supports_swap_partition=v.boolean(data, "supports_swap_partition", ctx),
        supports_swap_file=v.boolean(data, "supports_swap_file", ctx),
        default_layout=default_layout,
        layouts=layouts,
    )


def _kernel(path: Path, data: dict) -> KernelProfile:
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "id",
            "label",
            "description",
            "packages",
            "fallback_packages",
            "arches",
            "requires_packages_available",
            "package_groups",
            "notes",
        },
        {"id", "label", "description", "packages", "arches", "notes"},
    )
    packages = v.package_list(data, "packages", ctx)
    fallback = v.package_list(data, "fallback_packages", ctx, required=False)
    requires = v.package_list(data, "requires_packages_available", ctx, required=False)
    arches = [v.arch({"arch": value}, "arch", ctx) for value in v.string_list(data, "arches", ctx)]
    groups = v.string_list(data, "package_groups", ctx, required=False)
    for pkg in requires:
        if pkg not in packages:
            v.fail(ctx, f"required package '{pkg}' is not in 'packages'")
    return KernelProfile(
        id=v.identifier(data, "id", ctx),
        label=v.text(data, "label", ctx),
        description=v.text(data, "description", ctx),
        packages=tuple(packages),
        fallback_packages=tuple(fallback),
        arches=tuple(arches),
        requires_packages_available=tuple(requires),
        package_groups=tuple(groups),
        notes=v.text(data, "notes", ctx, default=""),
    )


def _desktop(path: Path, data: dict) -> DesktopProfile:
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "id",
            "kind",
            "label",
            "description",
            "packages",
            "display_manager",
            "session",
            "status",
            "requires_packages_available",
            "notes",
        },
        {"id", "kind", "label", "description", "packages", "status", "notes"},
    )
    packages = v.package_list(data, "packages", ctx)
    requires = v.package_list(data, "requires_packages_available", ctx, required=False)
    for pkg in requires:
        if pkg not in packages:
            v.fail(ctx, f"required package '{pkg}' is not in 'packages'")
    return DesktopProfile(
        id=v.identifier(data, "id", ctx),
        kind=v.text(data, "kind", ctx, choices=("desktop", "window-manager", "minimal")),
        label=v.text(data, "label", ctx),
        description=v.text(data, "description", ctx),
        packages=tuple(packages),
        display_manager=v.optional_text(data, "display_manager", ctx),
        session=v.optional_text(data, "session", ctx),
        status=v.text(data, "status", ctx, choices=("stable", "experimental")),
        requires_packages_available=tuple(requires),
        notes=v.text(data, "notes", ctx, default=""),
    )


def _driver(path: Path, data: dict) -> DriverProfile:
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "id",
            "label",
            "description",
            "vendor",
            "packages",
            "required_available",
            "modules",
            "requires_headers",
            "secure_boot",
            "fallback",
            "status",
            "notes",
        },
        {
            "id",
            "label",
            "description",
            "vendor",
            "packages",
            "modules",
            "requires_headers",
            "secure_boot",
            "fallback",
            "status",
            "notes",
        },
    )
    packages = v.package_list(data, "packages", ctx)
    required = v.package_list(data, "required_available", ctx, required=False)
    for pkg in required:
        if pkg not in packages:
            v.fail(ctx, f"required package '{pkg}' is not in 'packages'")
    return DriverProfile(
        id=v.identifier(data, "id", ctx),
        label=v.text(data, "label", ctx),
        description=v.text(data, "description", ctx),
        vendor=v.text(data, "vendor", ctx, choices=("nvidia", "amd", "intel", "generic")),
        packages=tuple(packages),
        required_available=tuple(required),
        modules=tuple(v.string_list(data, "modules", ctx, required=False)),
        requires_headers=v.boolean(data, "requires_headers", ctx),
        secure_boot=v.text(
            data,
            "secure_boot",
            ctx,
            choices=("signed_in_kernel", "requires_enrollment", "not_applicable"),
        ),
        fallback=v.optional_text(data, "fallback", ctx),
        status=v.text(data, "status", ctx, choices=("stable", "conditional")),
        notes=v.text(data, "notes", ctx, default=""),
    )


def _package_group(path: Path, data: dict) -> PackageGroup:
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "id",
            "label",
            "category",
            "description",
            "packages",
            "required_available",
            "architectures",
            "default",
            "offline",
            "notes",
        },
        {"id", "label", "category", "description", "packages", "default", "offline"},
    )
    packages = v.package_list(data, "packages", ctx)
    required = v.package_list(data, "required_available", ctx, required=False)
    for pkg in required:
        if pkg not in packages:
            v.fail(ctx, f"required package '{pkg}' is not in 'packages'")
    arches = [v.arch({"arch": value}, "arch", ctx) for value in v.string_list(data, "architectures", ctx, required=False)]
    return PackageGroup(
        id=v.identifier(data, "id", ctx),
        label=v.text(data, "label", ctx),
        category=v.text(data, "category", ctx),
        description=v.text(data, "description", ctx),
        packages=tuple(packages),
        required_available=tuple(required),
        architectures=tuple(arches),
        default=v.boolean(data, "default", ctx),
        offline=v.boolean(data, "offline", ctx),
    )


def _unique(items: list, label: str, path: Optional[Path] = None) -> dict:
    result = {}
    for item in items:
        if item.id in result:
            raise ConfigError(f"duplicate {label} profile id '{item.id}'")
        result[item.id] = item
    return result


def load_profiles(profiles_dir: Path) -> ProfileSet:
    filesystems = [_filesystem(path, data) for path, data in _load_dir(profiles_dir / "filesystems", "filesystem")]
    kernels = [_kernel(path, data) for path, data in _load_dir(profiles_dir / "kernels", "kernel")]
    desktops = [_desktop(path, data) for path, data in _load_dir(profiles_dir / "desktops", "desktop")]
    drivers = [_driver(path, data) for path, data in _load_dir(profiles_dir / "drivers", "driver")]
    packages = [_package_group(path, data) for path, data in _load_dir(profiles_dir / "packages", "package")]

    result = ProfileSet(
        filesystems=_unique(filesystems, "filesystem"),
        kernels=_unique(kernels, "kernel"),
        desktops=_unique(desktops, "desktop"),
        drivers=_unique(drivers, "driver"),
        packages=_unique(packages, "package"),
    )

    for kernel in result.kernels.values():
        for group_id in kernel.package_groups:
            if group_id not in result.packages:
                raise ConfigError(
                    f"kernel '{kernel.id}' references unknown package group '{group_id}'"
                )
    for driver in result.drivers.values():
        if driver.fallback and driver.fallback not in result.drivers:
            raise ConfigError(
                f"driver '{driver.id}' references unknown fallback driver '{driver.fallback}'"
            )
    return result
