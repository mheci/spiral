from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from . import validate as v
from .errors import ConfigError
from .models import CompressionPolicy, Firmware
from .profiles import ProfileSet, load_profiles

LOCALE_RE = re.compile(r"^[a-z]{2}_[A-Z]{2}\.UTF-8$")
KEYMAP_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*$")
SWAP_STRATEGIES = ("none", "zram", "partition", "file")


@dataclass(frozen=True)
class DebianConfig:
    suite: str
    release_url: str
    architecture: str
    architecture_suffix: str
    archive_areas: tuple[str, ...]
    keyring: str
    debian_mirror: str
    security_mirror: str
    snapshot: Optional[str]
    volume_id: str
    application_id: str
    publisher: str


@dataclass(frozen=True)
class BootEntry:
    id: str
    label: str
    append: str
    memtest: bool = False


@dataclass(frozen=True)
class BuildConfig:
    name: str
    live_username: str
    live_hostname: str
    boot_timeout_seconds: int
    default_boot_entry: str
    boot_entries: tuple[BootEntry, ...]
    offline_enabled: bool
    offline_package_groups: tuple[str, ...]
    source_date_epoch: str

    @property
    def default_entry(self) -> BootEntry:
        return next(entry for entry in self.boot_entries if entry.id == self.default_boot_entry)


@dataclass(frozen=True)
class HardwareConfig:
    gpu_vendors: Mapping[str, str]
    allow_nouveau_fallback: bool
    secure_boot_dkms: str
    removable_install: bool
    minimum_disk_gib: int
    nvidia_open_min_generation: str


@dataclass(frozen=True)
class InstallerConfig:
    minimum_ram_mib: int
    minimum_disk_gib: int
    defaults: Mapping[str, Any]
    target: str
    log_dir: str
    iso_repo: str
    esp_mib: int
    boot_mib: int
    bios_grub_mib: int
    swap_gib: int
    compression: Mapping[str, CompressionPolicy]
    zram_fraction: float
    swapfile_gib: int
    default_groups: tuple[str, ...]

    def default_str(self, key: str) -> str:
        value = self.defaults.get(key)
        if not isinstance(value, str):
            raise ConfigError(f"config/installer.json: default '{key}' must be a string")
        return value

    def default_bool(self, key: str) -> bool:
        value = self.defaults.get(key)
        if not isinstance(value, bool):
            raise ConfigError(f"config/installer.json: default '{key}' must be a boolean")
        return value


@dataclass(frozen=True)
class Repository:
    root: Path
    debian: DebianConfig
    installer: InstallerConfig
    build: BuildConfig
    hardware: HardwareConfig
    profiles: ProfileSet

    def verify(self) -> None:
        installer = self.installer
        profiles = self.profiles
        if installer.default_str("filesystem") not in profiles.filesystems:
            raise ConfigError("installer default filesystem has no profile")
        if installer.default_str("compression") not in installer.compression:
            raise ConfigError("installer default compression policy is not defined")
        if installer.default_str("kernel") not in profiles.kernels:
            raise ConfigError("installer default kernel has no profile")
        if installer.default_str("desktop") not in profiles.desktops:
            raise ConfigError("installer default desktop has no profile")
        driver = installer.default_str("driver")
        if driver != "auto" and driver not in profiles.drivers:
            raise ConfigError("installer default driver has no profile")
        for group_id in installer.default_groups:
            if group_id not in profiles.packages:
                raise ConfigError(f"default package group '{group_id}' has no profile")
        for group_id in self.build.offline_package_groups:
            if group_id not in profiles.packages:
                raise ConfigError(f"offline package group '{group_id}' has no profile")
            if not profiles.packages[group_id].offline:
                raise ConfigError(f"offline package group '{group_id}' is not marked offline")
        default_kernel = profiles.kernels[installer.default_str("kernel")]
        if self.debian.architecture not in default_kernel.arches:
            raise ConfigError(
                f"default kernel '{default_kernel.id}' does not support {self.debian.architecture}"
            )
        for entry in self.build.boot_entries:
            if entry.memtest and entry.append:
                raise ConfigError(f"boot entry '{entry.id}' is memtest and must not have append options")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing config file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: top level must be an object")
    return value


def _compression_profiles(data: dict, ctx: str) -> dict[str, CompressionPolicy]:
    policies: dict[str, CompressionPolicy] = {}
    for key, value in data.items():
        item_ctx = f"{ctx}.{key}"
        value = v.mapping(value, item_ctx)
        v.check_keys(value, item_ctx, {"algorithm", "level"}, {"algorithm"})
        algorithm = v.text(value, "algorithm", item_ctx, choices=("zstd", "zlib", "lzo", "none"))
        level = value.get("level")
        if level is not None and (isinstance(level, bool) or not isinstance(level, int)):
            v.fail(item_ctx, "'level' must be an integer or null")
        if algorithm == "zstd" and level is not None and not 1 <= level <= 15:
            v.fail(item_ctx, "zstd level must be between 1 and 15")
        if algorithm == "zlib" and level is not None and not 1 <= level <= 9:
            v.fail(item_ctx, "zlib level must be between 1 and 9")
        if algorithm in ("lzo", "none") and level is not None:
            v.fail(item_ctx, f"{algorithm} does not take a level")
        if not v.ID_RE.match(key):
            v.fail(ctx, f"invalid compression profile id {key!r}")
        policies[key] = CompressionPolicy(id=key, algorithm=algorithm, level=level)
    if not policies:
        v.fail(ctx, "at least one compression profile is required")
    return policies


def load_debian_config(path: Path) -> DebianConfig:
    data = _read_json(path)
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "schema_version",
            "suite",
            "release_url",
            "architecture",
            "architecture_suffix",
            "archive_areas",
            "keyring",
            "mirrors",
            "snapshot",
            "iso",
        },
        {
            "schema_version",
            "suite",
            "release_url",
            "architecture",
            "architecture_suffix",
            "archive_areas",
            "keyring",
            "mirrors",
            "iso",
        },
    )
    if v.integer(data, "schema_version", ctx) != 1:
        v.fail(ctx, "unsupported schema_version")
    areas = v.string_list(data, "archive_areas", ctx)
    for area in areas:
        if area not in ("main", "contrib", "non-free", "non-free-firmware"):
            v.fail(ctx, f"unsupported archive area '{area}'")
    mirrors = v.mapping(data["mirrors"], f"{ctx}.mirrors")
    v.check_keys(mirrors, f"{ctx}.mirrors", {"debian", "security"}, {"debian", "security"})
    iso = v.mapping(data["iso"], f"{ctx}.iso")
    v.check_keys(
        iso,
        f"{ctx}.iso",
        {"volume_id", "application_id", "publisher"},
        {"volume_id", "application_id", "publisher"},
    )
    release_url = v.text(data, "release_url", ctx)
    if not release_url.startswith("https://"):
        v.fail(ctx, "release_url must use https")
    return DebianConfig(
        suite=v.text(data, "suite", ctx),
        release_url=release_url,
        architecture=v.arch(data, "architecture", ctx),
        architecture_suffix=v.arch(data, "architecture_suffix", ctx),
        archive_areas=tuple(areas),
        keyring=v.text(data, "keyring", ctx),
        debian_mirror=v.text(mirrors, "debian", f"{ctx}.mirrors"),
        security_mirror=v.text(mirrors, "security", f"{ctx}.mirrors"),
        snapshot=v.optional_text(data, "snapshot", ctx),
        volume_id=v.text(iso, "volume_id", f"{ctx}.iso"),
        application_id=v.text(iso, "application_id", f"{ctx}.iso"),
        publisher=v.text(iso, "publisher", f"{ctx}.iso"),
    )


def load_build_config(path: Path) -> BuildConfig:
    data = _read_json(path)
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {"schema_version", "name", "live", "boot", "offline", "reproducible"},
        {"schema_version", "name", "live", "boot", "offline", "reproducible"},
    )
    if v.integer(data, "schema_version", ctx) != 1:
        v.fail(ctx, "unsupported schema_version")
    live = v.mapping(data["live"], f"{ctx}.live")
    v.check_keys(live, f"{ctx}.live", {"username", "hostname"}, {"username", "hostname"})
    boot = v.mapping(data["boot"], f"{ctx}.boot")
    v.check_keys(
        boot,
        f"{ctx}.boot",
        {"timeout_seconds", "default_entry", "entries"},
        {"timeout_seconds", "default_entry", "entries"},
    )
    entries_data = boot["entries"]
    if not isinstance(entries_data, list) or not entries_data:
        v.fail(f"{ctx}.boot", "'entries' must be a non-empty array")
    entries: list[BootEntry] = []
    for index, raw in enumerate(entries_data):
        entry_ctx = f"{ctx}.boot.entries[{index}]"
        raw = v.mapping(raw, entry_ctx)
        v.check_keys(raw, entry_ctx, {"id", "label", "append", "memtest"}, {"id", "label"})
        entry = BootEntry(
            id=v.identifier(raw, "id", entry_ctx),
            label=v.text(raw, "label", entry_ctx),
            append=v.text(raw, "append", entry_ctx, default=""),
            memtest=v.boolean(raw, "memtest", entry_ctx, default=False),
        )
        entries.append(entry)
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        v.fail(f"{ctx}.boot", "boot entry ids must be unique")
    default_entry = v.text(boot, "default_entry", f"{ctx}.boot")
    if default_entry not in ids:
        v.fail(f"{ctx}.boot", f"default_entry '{default_entry}' does not exist")
    if next(entry for entry in entries if entry.id == default_entry).memtest:
        v.fail(f"{ctx}.boot", "the default boot entry cannot be memtest")
    timeout = v.integer(boot, "timeout_seconds", f"{ctx}.boot")
    if not 1 <= timeout <= 300:
        v.fail(f"{ctx}.boot", "timeout_seconds must be between 1 and 300")
    offline = v.mapping(data["offline"], f"{ctx}.offline")
    v.check_keys(offline, f"{ctx}.offline", {"enabled", "profiles"}, {"enabled", "profiles"})
    reproducible = v.mapping(data["reproducible"], f"{ctx}.reproducible")
    v.check_keys(
        reproducible,
        f"{ctx}.reproducible",
        {"source_date_epoch"},
        {"source_date_epoch"},
    )
    epoch = reproducible["source_date_epoch"]
    if not (isinstance(epoch, str) and (epoch == "git" or epoch.isdigit())):
        v.fail(f"{ctx}.reproducible", "source_date_epoch must be 'git' or a Unix timestamp")
    return BuildConfig(
        name=v.text(data, "name", ctx),
        live_username=v.text(live, "username", f"{ctx}.live"),
        live_hostname=v.text(live, "hostname", f"{ctx}.live"),
        boot_timeout_seconds=timeout,
        default_boot_entry=default_entry,
        boot_entries=tuple(entries),
        offline_enabled=v.boolean(offline, "enabled", f"{ctx}.offline"),
        offline_package_groups=tuple(v.string_list(offline, "profiles", f"{ctx}.offline")),
        source_date_epoch=str(epoch),
    )


def load_hardware_config(path: Path) -> HardwareConfig:
    data = _read_json(path)
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {"schema_version", "gpu_vendors", "driver_policy", "removable_install", "minimum_disk_gib"},
        {"schema_version", "gpu_vendors", "driver_policy", "removable_install", "minimum_disk_gib"},
    )
    if v.integer(data, "schema_version", ctx) != 1:
        v.fail(ctx, "unsupported schema_version")
    vendors = v.mapping(data["gpu_vendors"], f"{ctx}.gpu_vendors")
    if not vendors or any(not isinstance(k, str) or not isinstance(val, str) for k, val in vendors.items()):
        v.fail(f"{ctx}.gpu_vendors", "must be a non-empty mapping of PCI vendor ids to names")
    policy = v.mapping(data["driver_policy"], f"{ctx}.driver_policy")
    v.check_keys(
        policy,
        f"{ctx}.driver_policy",
        {
            "default",
            "nvidia_proprietary_min_generation",
            "nvidia_open_min_generation",
            "allow_nouveau_fallback",
            "secure_boot_dkms",
        },
        {
            "default",
            "nvidia_proprietary_min_generation",
            "nvidia_open_min_generation",
            "allow_nouveau_fallback",
            "secure_boot_dkms",
        },
    )
    return HardwareConfig(
        gpu_vendors=dict(vendors),
        allow_nouveau_fallback=v.boolean(policy, "allow_nouveau_fallback", f"{ctx}.driver_policy"),
        secure_boot_dkms=v.text(policy, "secure_boot_dkms", f"{ctx}.driver_policy"),
        removable_install=v.boolean(data, "removable_install", ctx),
        minimum_disk_gib=v.integer(data, "minimum_disk_gib", ctx),
        nvidia_open_min_generation=v.text(policy, "nvidia_open_min_generation", f"{ctx}.driver_policy"),
    )


def load_installer_config(path: Path) -> InstallerConfig:
    data = _read_json(path)
    ctx = str(path)
    v.check_keys(
        data,
        ctx,
        {
            "schema_version",
            "default_groups",
            "minimums",
            "defaults",
            "paths",
            "partition",
            "compression",
            "swap",
            "network",
        },
        {
            "schema_version",
            "default_groups",
            "minimums",
            "defaults",
            "paths",
            "partition",
            "compression",
            "swap",
            "network",
        },
    )
    if v.integer(data, "schema_version", ctx) != 1:
        v.fail(ctx, "unsupported schema_version")
    minimums = v.mapping(data["minimums"], f"{ctx}.minimums")
    v.check_keys(minimums, f"{ctx}.minimums", {"ram_mib", "disk_gib"}, {"ram_mib", "disk_gib"})
    ram = v.integer(minimums, "ram_mib", f"{ctx}.minimums")
    disk = v.integer(minimums, "disk_gib", f"{ctx}.minimums")
    if ram < 512:
        v.fail(f"{ctx}.minimums", "ram_mib is implausibly low")
    if disk < 8:
        v.fail(f"{ctx}.minimums", "disk_gib is implausibly low")

    defaults = v.mapping(data["defaults"], f"{ctx}.defaults")
    default_keys = {
        "filesystem",
        "compression",
        "swap",
        "kernel",
        "desktop",
        "driver",
        "bootloader",
        "locale",
        "keymap",
        "timezone",
        "hostname",
        "username",
        "autologin",
        "snapshots",
        "btrfs_maintenance",
    }
    v.check_keys(
        defaults,
        f"{ctx}.defaults",
        default_keys,
        default_keys - {"snapshots", "btrfs_maintenance"},
    )
    for key in ("filesystem", "compression", "swap", "kernel", "desktop", "driver", "bootloader", "locale", "keymap", "timezone", "hostname", "username"):
        v.text(defaults, key, f"{ctx}.defaults")
    if defaults["swap"] not in SWAP_STRATEGIES:
        v.fail(f"{ctx}.defaults", f"'swap' must be one of {', '.join(SWAP_STRATEGIES)}")
    if defaults["bootloader"] != "grub":
        v.fail(f"{ctx}.defaults", "only grub is currently implemented")
    if not LOCALE_RE.match(defaults["locale"]):
        v.fail(f"{ctx}.defaults", "locale must look like en_US.UTF-8")
    if not KEYMAP_RE.match(defaults["keymap"]):
        v.fail(f"{ctx}.defaults", "invalid keymap")
    if not TIMEZONE_RE.match(defaults["timezone"]):
        v.fail(f"{ctx}.defaults", "invalid timezone")
    v.boolean(defaults, "autologin", f"{ctx}.defaults")
    if "snapshots" in defaults:
        v.boolean(defaults, "snapshots", f"{ctx}.defaults")
    if "btrfs_maintenance" in defaults:
        v.boolean(defaults, "btrfs_maintenance", f"{ctx}.defaults")

    paths = v.mapping(data["paths"], f"{ctx}.paths")
    v.check_keys(
        paths,
        f"{ctx}.paths",
        {"target", "log_dir", "iso_repo", "profiles"},
        {"target", "log_dir", "iso_repo", "profiles"},
    )
    for key in ("target", "log_dir", "iso_repo", "profiles"):
        value = v.text(paths, key, f"{ctx}.paths")
        if not value.startswith("/"):
            v.fail(f"{ctx}.paths", f"'{key}' must be absolute")
    partition = v.mapping(data["partition"], f"{ctx}.partition")
    v.check_keys(
        partition,
        f"{ctx}.partition",
        {"esp_mib", "boot_mib", "bios_grub_mib", "swap_gib"},
        {"esp_mib", "boot_mib", "bios_grub_mib", "swap_gib"},
    )
    esp = v.integer(partition, "esp_mib", f"{ctx}.partition")
    boot = v.integer(partition, "boot_mib", f"{ctx}.partition")
    bios = v.integer(partition, "bios_grub_mib", f"{ctx}.partition")
    swap_gib = v.integer(partition, "swap_gib", f"{ctx}.partition")
    if esp < 100:
        v.fail(f"{ctx}.partition", "esp_mib must be at least 100")
    if boot < 300:
        v.fail(f"{ctx}.partition", "boot_mib must be at least 300")
    if bios < 1:
        v.fail(f"{ctx}.partition", "bios_grub_mib must be at least 1")
    if swap_gib < 1:
        v.fail(f"{ctx}.partition", "swap_gib must be at least 1")

    compression_data = v.mapping(data["compression"], f"{ctx}.compression")
    compression = _compression_profiles(compression_data, f"{ctx}.compression")

    swap = v.mapping(data["swap"], f"{ctx}.swap")
    v.check_keys(
        swap,
        f"{ctx}.swap",
        {"zram_fraction", "file_gib"},
        {"zram_fraction", "file_gib"},
    )
    zram_fraction = v.number(swap, "zram_fraction", f"{ctx}.swap")
    if not 0 < zram_fraction <= 1.0:
        v.fail(f"{ctx}.swap", "zram_fraction must be between 0 and 1")
    swapfile_gib = v.integer(swap, "file_gib", f"{ctx}.swap")
    if swapfile_gib < 1:
        v.fail(f"{ctx}.swap", "file_gib must be at least 1")

    network = v.mapping(data["network"], f"{ctx}.network")
    v.check_keys(network, f"{ctx}.network", {"required_for"}, {"required_for"})
    v.string_list(network, "required_for", f"{ctx}.network")

    groups = v.string_list(data, "default_groups", ctx)
    for group in groups:
        if not v.ID_RE.match(group):
            v.fail(ctx, f"invalid package group id {group!r}")

    return InstallerConfig(
        minimum_ram_mib=ram,
        minimum_disk_gib=disk,
        defaults=dict(defaults),
        target=v.text(paths, "target", f"{ctx}.paths"),
        log_dir=v.text(paths, "log_dir", f"{ctx}.paths"),
        iso_repo=v.text(paths, "iso_repo", f"{ctx}.paths"),
        esp_mib=esp,
        boot_mib=boot,
        bios_grub_mib=bios,
        swap_gib=swap_gib,
        compression=compression,
        zram_fraction=zram_fraction,
        swapfile_gib=swapfile_gib,
        default_groups=tuple(groups),
    )


def default_root() -> Path:
    installed = Path("/usr/share/spiral")
    if (installed / "config" / "debian.json").is_file():
        return installed
    return Path(__file__).resolve().parents[2]


def load_repository(root: Optional[Path] = None, profiles_dir: Optional[Path] = None) -> Repository:
    root = Path(root) if root is not None else default_root()
    repo = Repository(
        root=root,
        debian=load_debian_config(root / "config" / "debian.json"),
        installer=load_installer_config(root / "config" / "installer.json"),
        build=load_build_config(root / "config" / "build.json"),
        hardware=load_hardware_config(root / "config" / "hardware.json"),
        profiles=load_profiles(Path(profiles_dir) if profiles_dir else root / "profiles"),
    )
    repo.verify()
    return repo
