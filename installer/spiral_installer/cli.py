from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import commands
from .config import Repository, default_root, load_repository
from .diagnostics import export_diagnostics, write_hardware_report
from .engine import Installer
from .errors import ConfigError, InstallError, SpiralError, ValidationError
from .hardware import NvidiaDetection, collect_hardware, detect_nvidia
from .i18n import _, setup as setup_i18n
from .models import (
    Disk,
    Firmware,
    HardwareReport,
    InstallSecrets,
    InstallPlan,
    PartitionRole,
    Severity,
    SwapStrategy,
    Warning,
)
from .packages import AptPackageResolver, OfflinePackageResolver
from .plan import Approval, InstallChoices, PlanBuilder, confirmation_phrase
from .release import load_release
from .util import Secret, human_bytes, run_capture, which

STEP_TITLES = (
    "Welcome",
    "Network",
    "Hardware",
    "Installation type",
    "Storage",
    "Filesystem",
    "Encryption",
    "Kernel",
    "Graphics drivers",
    "Desktop environment",
    "Software",
    "User account",
    "Summary",
    "Installation",
    "Completion",
)


def _say(message: str = "") -> None:
    print(message, flush=True)


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)


class Wizard:
    def __init__(self, repo: Repository, hardware: HardwareReport, release, args) -> None:
        self.repo = repo
        self.hardware = hardware
        self.release = release
        self.args = args
        self.answers: dict = {}
        self.user_password: Optional[Secret] = None
        self.luks_passphrase: Optional[Secret] = None
        self.nvidia: NvidiaDetection = NvidiaDetection(False, None, None)

    def _step(self, number: int, title: str) -> None:
        _say()
        _say(f"--- Step {number}/15: {_(title)} ---")

    def _ask(self, prompt: str, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default not in (None, "") else ""
        try:
            value = input(f"{prompt}{suffix}: ").strip()
        except EOFError as exc:
            raise SpiralError("input ended before the installer finished") from exc
        return value or (default or "")

    def _yes(self, prompt: str, default: bool = False) -> bool:
        marker = "Y/n" if default else "y/N"
        while True:
            value = self._ask(f"{prompt} [{marker}]", "").lower()
            if not value:
                return default
            if value in ("y", "yes"):
                return True
            if value in ("n", "no"):
                return False
            _say("Please answer yes or no.")

    def _choose(self, prompt: str, options: Sequence[tuple[str, str]], default_index: int = 0) -> str:
        for index, (value, label) in enumerate(options, start=1):
            _say(f"  {index}. {label}")
        while True:
            value = self._ask(prompt, str(default_index + 1))
            if value.isdigit() and 1 <= int(value) <= len(options):
                return options[int(value) - 1][0]
            matches = [option for option in options if option[0] == value]
            if matches:
                return value
            _say("Enter the number of an option.")

    def _multi(self, prompt: str, options: Sequence[tuple[str, str]], default: Sequence[str] = ()) -> tuple[str, ...]:
        for index, (value, label) in enumerate(options, start=1):
            _say(f"  {index}. {label}")
        _say("Enter numbers separated by commas, or 'none'.")
        default_text = ",".join(str(i + 1) for i, (value, _) in enumerate(options) if value in default)
        while True:
            value = self._ask(prompt, default_text or "none")
            if value.lower() == "none":
                return ()
            pieces = [piece.strip() for piece in value.split(",") if piece.strip()]
            if all(piece.isdigit() and 1 <= int(piece) <= len(options) for piece in pieces):
                return tuple(options[int(piece) - 1][0] for piece in pieces)
            _say("Enter valid option numbers.")

    def _secret(self, prompt: str, minimum: int = 8) -> Secret:
        while True:
            first = getpass.getpass(prompt + ": ")
            if len(first) < minimum:
                _say(f"Use at least {minimum} characters.")
                continue
            second = getpass.getpass("Repeat: ")
            if first != second:
                _say("The values do not match.")
                continue
            return Secret(first)

    def run(self) -> InstallChoices:
        self._welcome()
        self._network()
        self._hardware()
        self._install_type()
        self._storage()
        self._filesystem()
        self._encryption()
        self._kernel()
        self._driver()
        self._desktop()
        self._software()
        self._user()
        return InstallChoices(**self.answers)

    def _welcome(self) -> None:
        self._step(1, "Welcome")
        _say(f"Spiral {self.release.version}")
        _say(f"Debian base: {self.release.version} ({self.release.codename})")
        _say("This installer erases one disk. Read every summary before you confirm.")
        self.answers["locale"] = self._ask("Locale", self.repo.installer.default_str("locale"))
        self.answers["keymap"] = self._ask("Keyboard layout", self.repo.installer.default_str("keymap"))
        self.answers["timezone"] = self._ask("Timezone", self.repo.installer.default_str("timezone"))
        _say("The graphical installer provides Orca and high-contrast options. This terminal interface is keyboard-only.")

    def _network(self) -> None:
        self._step(2, "Network")
        state = "unknown"
        if which("nmcli"):
            result = run_capture(["nmcli", "-t", "-f", "STATE", "general"], timeout=10)
            state = (result.stdout or "unknown").strip() or "unknown"
        adapters = ", ".join(
            f"{item.name} ({item.kind}, {item.state or 'unknown'})" for item in self.hardware.network
        ) or "none detected"
        _say(f"NetworkManager: {state}")
        _say(f"Adapters: {adapters}")
        _say("Online installation downloads Debian packages. Offline mode needs an ISO that carries them.")
        if which("nmtui") and self._yes("Open the text network manager now?", default=False):
            subprocess.call(["nmtui"])
        offline = self._yes("Install offline", default=False)
        if offline:
            if not self.repo.build.offline_enabled:
                _say("This ISO was built without an offline package repository. Staying online.")
                offline = False
            else:
                _say("Only selections present on the medium will pass preflight.")
        self.answers["offline"] = offline

    def _hardware(self) -> None:
        self._step(3, "Hardware")
        hw = self.hardware
        _say(f"CPU: {hw.cpu_model or 'unknown'} ({hw.cpu_cores} threads)")
        _say(f"Memory: {hw.ram_mib} MiB")
        _say(f"Firmware: {hw.firmware.value.upper()}")
        secure = "unknown" if hw.secure_boot is None else ("enabled" if hw.secure_boot else "disabled")
        _say(f"Secure Boot: {secure}")
        gpus = ", ".join(f"{gpu.model} [{gpu.vendor.value}]" for gpu in hw.gpus) or "none detected"
        _say(f"Graphics: {gpus}")
        _say(f"Virtualization: {hw.virtualization or 'none detected'}")
        for disk in hw.disks:
            mounted = "mounted" if disk.is_busy else "free"
            _say(
                f"Disk: {disk.stable_path} {human_bytes(disk.size_bytes)} "
                f"{disk.model} {disk.transport} ({mounted}, {len(disk.partitions)} partitions)"
            )

    def _install_type(self) -> None:
        self._step(4, "Installation type")
        _say("This release implements guided whole-disk installation.")
        _say("Manual partitioning, dual-boot reuse and LVM are not implemented in this release.")
        self.answers["mode"] = "guided"

    def _storage(self) -> None:
        self._step(5, "Storage")
        disks = list(self.hardware.disks)
        if not disks:
            raise SpiralError("no disks were detected")
        options = []
        for disk in disks:
            flags = []
            if disk.removable:
                flags.append("removable")
            if disk.is_busy:
                flags.append("in use")
            if disk.has_foreign_os:
                flags.append("existing OS")
            note = f" ({', '.join(flags)})" if flags else ""
            options.append(
                (
                    disk.stable_path,
                    f"{disk.stable_path}  {human_bytes(disk.size_bytes)}  {disk.model} {disk.transport}{note}",
                )
            )
        internal = [disk for disk in disks if not disk.removable and not disk.is_busy]
        default = 0
        if len(internal) == 1:
            default = [i for i, disk in enumerate(disks) if disk.stable_path == internal[0].stable_path][0]
        self.answers["disk"] = self._choose("Target disk", options, default)

    def _filesystem(self) -> None:
        self._step(6, "Filesystem")
        filesystems = sorted(self.repo.profiles.filesystems.values(), key=lambda item: (item.kind != "btrfs", item.id))
        fs_options = [(item.id, f"{item.label} (default layout: {item.default_layout or 'n/a'})") for item in filesystems]
        self.answers["filesystem"] = self._choose("Filesystem", fs_options, 0)
        profile = self.repo.profiles.filesystem(self.answers["filesystem"])
        if profile.kind == "btrfs":
            layouts = [(item.id, f"{item.label}: {item.description}") for item in profile.layouts.values()]
            self.answers["layout"] = self._choose("BTRFS layout", layouts, 0)
            compression = [
                (item.id, f"{item.id} ({item.mount_option or 'off'})")
                for item in self.repo.installer.compression.values()
            ]
            self.answers["compression"] = self._choose("Compression", compression, 0)
            self.answers["snapshots"] = self._yes("Enable Snapper snapshots? (not backups)", default=False)
            self.answers["btrfs_maintenance"] = self._yes("Enable monthly BTRFS scrub?", default=False)
        else:
            self.answers["layout"] = None
            self.answers["compression"] = None
            self.answers["snapshots"] = False
            self.answers["btrfs_maintenance"] = False
        swap_options = [
            ("zram", "ZRAM (compressed RAM, no hibernation)"),
            ("partition", "Swap partition"),
            ("none", "No swap"),
        ]
        if profile.supports_swap_file:
            swap_options.insert(1, ("file", "BTRFS swapfile (hibernation needs extra configuration)"))
        self.answers["swap"] = self._choose("Swap", swap_options, 0)

    def _encryption(self) -> None:
        self._step(7, "Encryption")
        if not self._yes("Encrypt the root filesystem with LUKS2?", default=False):
            self.answers["encryption"] = False
            self.luks_passphrase = None
            return
        self.answers["encryption"] = True
        _say("The passphrase cannot be recovered. There is no backdoor.")
        self.luks_passphrase = self._secret("LUKS passphrase")
        if self.answers.get("swap") == "partition":
            _say("An unencrypted swap partition would leak data. Switching to ZRAM.")
            self.answers["swap"] = "zram"

    def _kernel(self) -> None:
        self._step(8, "Kernel")
        options = []
        for kernel in sorted(self.repo.profiles.kernels.values(), key=lambda item: item.id):
            options.append((kernel.id, f"{kernel.label}: {kernel.description}"))
        self.answers["kernel"] = self._choose("Kernel profile", options, 0)

    def _recommended_driver(self) -> str:
        if self.hardware.nvidia_gpus:
            if self.hardware.secure_boot:
                return "nouveau"
            return "nvidia-proprietary"
        if any(gpu.vendor.value == "amd" for gpu in self.hardware.gpus):
            return "amd"
        if any(gpu.vendor.value == "intel" for gpu in self.hardware.gpus):
            return "intel"
        return "generic"

    def _driver(self) -> None:
        self._step(9, "Graphics drivers")
        if self.hardware.nvidia_gpus:
            self.nvidia = detect_nvidia()
            if self.nvidia.available:
                if self.nvidia.supported is False:
                    _say("Debian's nvidia-detect reports no supported NVIDIA driver. Nouveau is recommended.")
                elif self.nvidia.package:
                    _say(f"Debian's nvidia-detect recommends: {self.nvidia.package}")
        recommended = self._recommended_driver()
        options = [("auto", f"Automatic (recommended: {recommended})")]
        for driver in sorted(self.repo.profiles.drivers.values(), key=lambda item: item.id):
            options.append((driver.id, f"{driver.label}: {driver.description}"))
        self.answers["driver"] = self._choose("Driver policy", options, 0)

    def _desktop(self) -> None:
        self._step(10, "Desktop environment")
        desktops = sorted(
            self.repo.profiles.desktops.values(),
            key=lambda item: (item.kind != "desktop", item.id),
        )
        options = []
        for item in desktops:
            note = " (experimental)" if item.status == "experimental" else ""
            options.append((item.id, f"{item.label} [{item.kind}]{note}: {item.description}"))
        selected = self._multi("Desktop profiles", options, (self.repo.installer.default_str("desktop"),))
        self.answers["desktops"] = selected

    def _software(self) -> None:
        self._step(11, "Software")
        skip = set(self.repo.installer.default_groups)
        options = [
            (group.id, f"{group.label}: {group.description}")
            for group in sorted(self.repo.profiles.packages.values(), key=lambda item: item.id)
            if group.id not in skip
        ]
        if self.answers.get("offline"):
            allowed = set(self.repo.build.offline_package_groups)
            options = [option for option in options if option[0] in allowed]
        self.answers["package_groups"] = self._multi("Additional package groups", options, ())

    def _user(self) -> None:
        self._step(12, "User account")
        self.answers["username"] = self._ask(
            "Username", self.repo.installer.default_str("username")
        )
        self.answers["full_name"] = self._ask("Full name", "Spiral User")
        self.answers["hostname"] = self._ask(
            "Hostname", self.repo.installer.default_str("hostname")
        )
        self.user_password = self._secret("User password")
        desktop_ids = self.answers.get("desktops") or ()
        manager = None
        if desktop_ids:
            manager = self.repo.profiles.desktop(desktop_ids[0]).display_manager
        if manager in ("gdm3", "lightdm", "sddm"):
            self.answers["autologin"] = self._yes("Automatic login for this user?", default=False)
        else:
            self.answers["autologin"] = False

    def confirm(self, plan: InstallPlan) -> Approval:
        _say()
        _say("=== Installation summary ===")
        print_plan(plan)
        acknowledged: set[str] = set()
        for warning in plan.warnings:
            if warning.requires_ack:
                _say()
                _say(f"WARNING [{warning.code}]: {warning.message}")
                if not self._yes("Acknowledge this warning?", default=False):
                    raise SpiralError("installation cancelled because a warning was not acknowledged")
                acknowledged.add(warning.code)
        phrase = confirmation_phrase(plan)
        _say()
        _say("Type this exact phrase to begin destructive installation:")
        _say(f"  {phrase}")
        entered = self._ask("Confirmation")
        return Approval(phrase=entered, acknowledged=frozenset(acknowledged))


def print_plan(plan: InstallPlan) -> None:
    _say(f"Debian release: {plan.release.version} ({plan.release.codename})")
    _say(f"Target disk: {plan.disk.stable_path} {human_bytes(plan.disk.size_bytes)} {plan.disk.model}")
    _say(f"Firmware: {plan.firmware.value.upper()}  Partition table: {plan.partition_table.value.upper()}")
    for part in plan.partitions:
        size = "rest" if part.role is PartitionRole.ROOT else f"{part.size_mib} MiB"
        _say(f"  {part.number}: {part.role.value:<9} {size:<10} {part.fs:<6} {part.mountpoint or ''}")
    _say(f"Filesystem: {plan.filesystem_id}  Compression: {plan.compression.mount_option or 'off'}")
    for subvolume in plan.subvolumes:
        _say(f"  {subvolume.name:<14} -> {subvolume.mountpoint:<14} {', '.join(subvolume.options)}")
    _say(f"Swap: {plan.swap.strategy.value}" + (f" ({plan.swap.size_mib} MiB)" if plan.swap.size_mib else ""))
    _say(f"Encryption: {'LUKS2' if plan.encryption.enabled else 'disabled'}")
    _say(f"Kernel: {plan.software.kernel_label} ({plan.software.kernel_id})")
    _say(f"Desktop: {', '.join(plan.software.desktop_labels) or 'none'}")
    _say(f"Driver: {plan.software.driver_label}")
    _say(f"Packages: {len(plan.software.packages)}")
    _say(f"User: {plan.user.username} on {plan.user.hostname}, autologin={plan.user.autologin}")
    _say(f"Locale: {plan.locale.locale}, keymap {plan.locale.keymap}, timezone {plan.locale.timezone}")
    if plan.warnings:
        _say("Warnings:")
        for warning in plan.warnings:
            _say(f"  [{warning.severity.value}] {warning.code}: {warning.message}")


def print_dry_run(plan: InstallPlan, repo: Repository) -> None:
    _say()
    _say("=== Planned commands, nothing has been executed ===")
    for title, group in (
        ("Partitioning", commands.partition_commands(plan)),
        ("Filesystems", commands.format_commands(plan)),
        ("Mounts", commands.mount_commands(plan, "/target")),
        ("Base system", [commands.debootstrap_command(plan, repo.debian, "/target")]),
    ):
        _say(f"{title}:")
        for argv in group:
            _say("  " + shlex.join(argv))
    _say("Packages:")
    _say("  " + ", ".join(plan.software.packages))


def load_choices(path: str) -> InstallChoices:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpiralError(f"cannot read unattended configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise SpiralError("unattended configuration must be a JSON object")
    if "choices" in data:
        data = data["choices"]
        if not isinstance(data, dict):
            raise SpiralError("'choices' must be a JSON object")
    return InstallChoices.from_mapping(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spiral-installer",
        description="Install Spiral, a Debian-based distribution, from the live image.",
    )
    parser.add_argument("--root", default=str(default_root()), help="Spiral data root")
    parser.add_argument(
        "--release-file",
        default="/usr/share/spiral/release.json",
        help="Debian release metadata written by the ISO builder",
    )
    parser.add_argument("--codename", help="Debian codename for a dry run without release metadata")
    parser.add_argument("--unattended", metavar="FILE", help="JSON file with installer choices")
    parser.add_argument("--gui", action="store_true", help="use the graphical installer")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and commands, change nothing")
    parser.add_argument("--check-packages", action="store_true", help="refresh package metadata before planning")
    parser.add_argument("--confirm", help="exact destructive confirmation phrase (unattended mode)")
    parser.add_argument("--ack", default="", help="comma-separated warning codes to acknowledge")
    parser.add_argument("--target", help="target mount directory (development only)")
    parser.add_argument("--log", help="installation log path")
    parser.add_argument("--language", help="gettext language code")
    parser.add_argument(
        "--user-password-file",
        help="file containing the user password (unattended mode, mode 0600)",
    )
    parser.add_argument(
        "--luks-passphrase-file",
        help="file containing the LUKS passphrase (unattended mode, mode 0600)",
    )
    parser.add_argument("--version", action="version", version="Spiral installer 0.1")
    return parser


def _read_secret_file(path: str, label: str) -> Secret:
    file_path = Path(path)
    try:
        mode = file_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise SpiralError(f"{label} file must not be readable by group or others")
        value = file_path.read_text(encoding="utf-8").splitlines()[0]
    except OSError as exc:
        raise SpiralError(f"cannot read {label} file: {exc}") from exc
    if not value:
        raise SpiralError(f"{label} file is empty")
    return Secret(value)


def _unattended_secrets(args, plan: InstallPlan) -> InstallSecrets:
    password = None
    if args.user_password_file:
        password = _read_secret_file(args.user_password_file, "user password")
    else:
        password = Secret(getpass.getpass("User password: "))
    luks = None
    if plan.encryption.enabled:
        if args.luks_passphrase_file:
            luks = _read_secret_file(args.luks_passphrase_file, "LUKS passphrase")
        else:
            luks = Secret(getpass.getpass("LUKS passphrase: "))
    return InstallSecrets(user_password=password, luks_passphrase=luks)


def _make_resolver(repo: Repository, release, choices: InstallChoices, dry_run: bool, check: bool):
    if dry_run and not check:
        return None
    if choices.offline:
        try:
            return OfflinePackageResolver.from_directory(
                Path(repo.installer.iso_repo),
                release.codename,
                repo.debian.architecture,
            )
        except RuntimeError as exc:
            raise SpiralError(str(exc)) from exc
    resolver = AptPackageResolver(lambda argv, **kw: run_capture(argv, **kw))
    try:
        resolver.refresh()
    except RuntimeError as exc:
        raise SpiralError(str(exc)) from exc
    return resolver


def _approval_from_args(plan: InstallPlan, args) -> Approval:
    if not args.confirm:
        phrase = confirmation_phrase(plan)
        codes = ",".join(w.code for w in plan.warnings if w.requires_ack)
        raise SpiralError(
            f"unattended installation requires --confirm '{phrase}'"
            + (f" and --ack {codes}" if codes else "")
        )
    acknowledged = frozenset(code.strip() for code in args.ack.split(",") if code.strip())
    return Approval(phrase=args.confirm, acknowledged=acknowledged)


def _offer_diagnostics(log_dir: str) -> None:
    candidates = []
    for root in (Path("/media"), Path("/run/media")):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_dir() and os.path.ismount(path):
                    candidates.append(path)
            except OSError:
                continue
    _say(f"Installation logs are preserved at {log_dir}.")
    if not candidates:
        return
    _say("Mounted removable media:")
    for index, path in enumerate(candidates, start=1):
        _say(f"  {index}. {path}")
    answer = input("Copy diagnostics to a medium (number, or Enter to skip): ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(candidates):
        return
    try:
        target = export_diagnostics(log_dir, candidates[int(answer) - 1])
        _say(f"Diagnostics copied to {target}")
    except SpiralError as exc:
        _fail(exc.message)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_i18n(args.language)
    log_dir = str(Path(args.log).parent) if args.log else "/var/log/spiral-installer"
    plan: Optional[InstallPlan] = None
    hardware: Optional[HardwareReport] = None
    try:
        repo = load_repository(Path(args.root))
        release = load_release(args.release_file, args.codename)
        hardware = collect_hardware(dict(repo.hardware.gpu_vendors))
        if not args.dry_run and os.geteuid() != 0:
            raise SpiralError("the installer must run as root")
        if args.gui:
            if args.dry_run:
                raise SpiralError("--gui cannot be combined with --dry-run")
            from .gui import run_gui

            return run_gui(repo, hardware, release)
        if args.unattended:
            choices = load_choices(args.unattended)
            wizard = None
        else:
            wizard = Wizard(repo, hardware, release, args)
            choices = wizard.run()
        resolver = _make_resolver(repo, release, choices, args.dry_run, args.check_packages)
        nvidia = wizard.nvidia if wizard else detect_nvidia()
        builder = PlanBuilder(
            repo,
            hardware,
            release,
            resolver=resolver,
            nvidia_package=nvidia.package,
            nvidia_supported=nvidia.supported,
        )
        plan = builder.build(choices)
        if args.dry_run:
            print_plan(plan)
            print_dry_run(plan, repo)
            _say("Dry run complete. No disk was changed.")
            return 0
        if wizard is None:
            approval = _approval_from_args(plan, args)
            secrets = _unattended_secrets(args, plan)
        else:
            assert wizard.user_password is not None
            secrets = InstallSecrets(
                user_password=wizard.user_password,
                luks_passphrase=wizard.luks_passphrase,
            )
            approval = wizard.confirm(plan)
        installer = Installer(
            repo,
            plan,
            secrets,
            approval,
            target=args.target,
            log_path=args.log,
            echo=True,
        )
        result = installer.install()
        _say()
        _say("=== Installation complete ===")
        _say(f"Debian {plan.release.version} ({plan.release.codename})")
        _say(f"Kernel: {plan.software.kernel_id}")
        _say(f"Filesystem: {plan.filesystem_id}")
        _say(f"Desktop: {', '.join(plan.software.desktop_labels)}")
        _say(f"Driver: {plan.software.driver_id}")
        _say(f"Log: {result.log_path}")
        _say("You can now reboot into the new system.")
        if wizard and wizard._yes("Reboot now?", default=False):
            subprocess.call(["reboot"])
        return 0
    except ValidationError as exc:
        _fail(exc.message)
        for issue in exc.issues:
            _fail(f"- {issue}")
        return 2
    except InstallError as exc:
        _say()
        _say(f"Installation failed in phase '{exc.phase or 'unknown'}'.")
        _say(exc.message)
        if exc.completed:
            _say("Completed phases: " + ", ".join(exc.completed))
        _say("No rollback is possible after partitioning. The target may be partially written.")
        _say("The log records what ran. Reboot the live image before retrying.")
        if not args.dry_run:
            _offer_diagnostics(log_dir)
        return 1
    except ConfigError as exc:
        _fail(f"configuration error: {exc.message}")
        return 2
    except SpiralError as exc:
        _fail(exc.message)
        return 2
    except KeyboardInterrupt:
        _fail("cancelled by the user")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
