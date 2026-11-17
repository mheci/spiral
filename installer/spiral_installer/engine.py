from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import commands, disks
from .config import Repository
from .errors import InstallError, SpiralError
from .log import InstallLog
from .models import Firmware, InstallPlan, InstallSecrets, PartitionRole, SwapStrategy
from .packages import AptPackageResolver, check_offline_repository
from .plan import Approval, validate_approval
from .runner import CommandRunner
from .util import Secret, human_bytes, partition_path, which


@dataclass(frozen=True)
class Phase:
    id: str
    label: str


@dataclass(frozen=True)
class InstallResult:
    success: bool
    completed: tuple[str, ...]
    log_path: str
    target: str
    summary: dict


PHASES = (
    Phase("preflight", "Preflight checks"),
    Phase("storage", "Partitioning and encryption"),
    Phase("filesystems", "Filesystem creation"),
    Phase("mount", "Mounting the target"),
    Phase("base", "Base system installation"),
    Phase("kernel", "Kernel installation"),
    Phase("firmware", "Firmware installation"),
    Phase("drivers", "Graphics driver installation"),
    Phase("desktops", "Desktop installation"),
    Phase("software", "Additional software"),
    Phase("users", "User account creation"),
    Phase("configure", "System configuration"),
    Phase("bootloader", "Bootloader installation"),
    Phase("initramfs", "Initramfs generation"),
    Phase("verify", "Installation verification"),
    Phase("cleanup", "Cleanup"),
)


class Installer:
    def __init__(
        self,
        repo: Repository,
        plan: InstallPlan,
        secrets: InstallSecrets,
        approval: Approval,
        target: Optional[str] = None,
        log_path: Optional[str] = None,
        echo: bool = True,
        progress: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.repo = repo
        self.plan = plan
        self.secrets = secrets
        self.approval = approval
        self.target = Path(target or repo.installer.target)
        self.log_path = log_path or str(Path(repo.installer.log_dir) / "install.log")
        self.echo = echo
        self.progress = progress
        self.log: Optional[InstallLog] = None
        self.runner: Optional[CommandRunner] = None
        self.completed: list[str] = []
        self._bind_mounts: list[str] = []
        self._target_mounted = False

    def install(self) -> InstallResult:
        if not self.target.is_absolute() or str(self.target) == "/":
            raise InstallError("target directory must be an absolute path other than /")
        log = InstallLog(self.log_path, echo=self.echo, callback=self.progress)
        self.log = log
        self.runner = CommandRunner(log, echo_output=False)
        log.event(
            "info",
            "engine",
            "install-start",
            "starting",
            release=self.plan.release.codename,
            disk=self.plan.disk.stable_path,
            filesystem=self.plan.filesystem_id,
            desktop=",".join(self.plan.software.desktop_ids),
        )
        current = "approval"
        try:
            validate_approval(self.plan, self.approval)
            self._validate_secrets()
            for phase in PHASES:
                current = phase.id
                log.event("info", "engine", "phase-start", phase.label)
                method = getattr(self, f"phase_{phase.id}")
                method()
                self.completed.append(phase.id)
                log.event("info", "engine", "phase-done", phase.label)
        except InstallError as exc:
            exc.completed = list(self.completed)
            exc.phase = exc.phase or current
            self._record_failure(exc)
            raise
        except Exception as exc:
            failure = InstallError(f"unexpected failure: {exc}", phase=current, completed=list(self.completed))
            self._record_failure(failure)
            raise failure from exc
        finally:
            log.close()
        result = InstallResult(
            success=True,
            completed=tuple(self.completed),
            log_path=self.log_path,
            target=str(self.target),
            summary=self.plan.summary(),
        )
        return result

    def _record_failure(self, failure: InstallError) -> None:
        if self.log is None:
            return
        self.log.event(
            "error",
            "engine",
            "install-failed",
            failure.message,
            phase=failure.phase or "unknown",
            completed=",".join(self.completed),
        )
        try:
            path = Path(self.log_path).parent / "failure.json"
            path.write_text(
                json.dumps(
                    {
                        "message": failure.message,
                        "detail": failure.detail,
                        "phase": failure.phase,
                        "completed": self.completed,
                        "target_mounted": self._target_mounted,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
        except OSError:
            pass

    def _validate_secrets(self) -> None:
        password = self.secrets.user_password.reveal()
        if len(password) < 8:
            raise InstallError("user password must be at least 8 characters")
        if self.plan.encryption.enabled:
            if self.secrets.luks_passphrase is None:
                raise InstallError("an encryption passphrase is required for this plan")
            if len(self.secrets.luks_passphrase.reveal()) < 8:
                raise InstallError("the LUKS passphrase must be at least 8 characters")

    def _run(self, argv: list[str], **kwargs) -> object:
        assert self.runner is not None
        return self.runner.run(argv, **kwargs)

    def _exists(self, path: Path) -> bool:
        return path.exists()

    def _wait(self, path: Path, description: str, timeout: int = 15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.5)
        raise InstallError(f"timed out waiting for {description} ({path})")

    def _uuid(self, device: str) -> str:
        result = self._run(["blkid", "-s", "UUID", "-o", "value", device], timeout=30)
        value = (getattr(result, "stdout", "") or "").strip()
        if not value:
            raise InstallError(f"could not read filesystem UUID for {device}")
        return value

    def _write(self, relative: str, content: str, mode: int = 0o644) -> None:
        # Refuse to follow symlinks outside the target: a pre-planted symlink
        # (e.g. from a compromised package) must not redirect writes to the
        # live system. The target is freshly formatted and checked empty in
        # preflight, so this is defence in depth.
        path = self.target / relative.lstrip("/")
        try:
            resolved_target = self.target.resolve()
            resolved_parent = path.parent.resolve()
            resolved_parent.relative_to(resolved_target)
        except (OSError, ValueError) as exc:
            raise InstallError(f"refusing to write outside the target: {relative}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise InstallError(f"refusing to write through a symlink: {relative}")
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def _chroot(self, *argv: str, timeout: Optional[int] = 900, check: bool = True) -> object:
        return self._run(["chroot", str(self.target), *argv], timeout=timeout, check=check)

    def _apt_install(self, packages: list[str], timeout: int = 3600) -> None:
        if not packages:
            return
        if self.log:
            self.log.event("info", "packages", "install", f"{len(packages)} packages")
        self._run(commands.apt_install(str(self.target), packages), timeout=timeout)

    def _groups_packages(self, group_id: str) -> list[str]:
        group = self.repo.profiles.package_group(group_id)
        return list(group.packages)

    def phase_preflight(self) -> None:
        if os.geteuid() != 0:
            raise InstallError("the installer must run as root")
        for program in ("debootstrap", "chroot", "mount", "umount", "wipefs", "partprobe", "blkid"):
            if not which(program):
                raise InstallError(f"required program is missing: {program}")
        if not which("sgdisk") and self.plan.partition_table.value == "gpt":
            raise InstallError("sgdisk is required for GPT partitioning")
        if not which("parted") and self.plan.partition_table.value == "msdos":
            raise InstallError("parted is required for MBR partitioning")
        if self.plan.encryption.enabled and not which("cryptsetup"):
            raise InstallError("cryptsetup is required for an encrypted installation")
        if not which("mkfs." + self.plan.filesystem_kind.value):
            raise InstallError(f"mkfs.{self.plan.filesystem_kind.value} is required")
        if self.plan.esp and not which("mkfs.vfat"):
            raise InstallError("mkfs.vfat is required for the EFI system partition")
        if self.plan.swap_partition and not which("mkswap"):
            raise InstallError("mkswap is required for a swap partition")

        disks.validate_target_disk(
            self.plan.disk, self.repo.hardware, self.repo.installer.minimum_disk_gib
        )
        if self.target.exists():
            if os.path.ismount(self.target):
                raise InstallError(f"{self.target} is already a mountpoint")
            try:
                contents = list(self.target.iterdir())
            except OSError as exc:
                raise InstallError(f"cannot inspect {self.target}: {exc}") from exc
            if contents:
                raise InstallError(f"{self.target} is not empty")
        else:
            self.target.mkdir(parents=True, exist_ok=True)
        if not os.path.ismount("/run/live/medium") and not Path("/lib/live/mount/medium").exists():
            if self.log:
                self.log.event(
                    "warning",
                    "preflight",
                    "live-medium",
                    "could not confirm the live medium mount; disk busy checks still apply",
                )
        release_file = Path("/usr/share/spiral/release.json")
        if release_file.is_file():
            try:
                release = json.loads(release_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise InstallError(f"cannot read the Spiral release metadata: {exc}") from exc
            if release.get("codename") != self.plan.release.codename:
                raise InstallError(
                    "the installed release metadata does not match the plan; refusing to continue"
                )

        if self.plan.offline:
            repo_root = Path(self.repo.installer.iso_repo)
            try:
                missing = check_offline_repository(
                    repo_root,
                    self.plan.release.codename,
                    self.plan.architecture,
                    self.plan.software.packages,
                )
            except RuntimeError as exc:
                raise InstallError(str(exc)) from exc
            if missing:
                raise InstallError(
                    "the offline medium is missing required packages: " + ", ".join(missing)
                )
        else:
            try:
                self._run(
                    ["apt-get", "update", "-o", "Acquire::Retries=3"],
                    timeout=300,
                )
                resolver = AptPackageResolver(lambda argv, **kw: self._run(argv, **kw))
                missing = resolver.missing(self.plan.software.packages)
            except SpiralError as exc:
                raise InstallError(f"network or repository preflight failed: {exc}") from exc
            if missing:
                raise InstallError(
                    "required packages are unavailable before any disk change: " + ", ".join(missing)
                )

    def phase_storage(self) -> None:
        if self.plan.encryption.enabled and self.secrets.luks_passphrase is None:
            raise InstallError("an encryption passphrase is required")
        for argv in commands.partition_commands(self.plan):
            self._run(argv, timeout=600)
        for part in self.plan.partitions:
            if part.fs == "none":
                continue
            self._wait(Path(partition_path(self.plan.disk.path, part.number)), "partition")
        # LUKS format/open happens in the filesystem phase, where the mapper is required.

    def phase_filesystems(self) -> None:
        if self.plan.encryption.enabled and self.secrets.luks_passphrase is None:
            raise InstallError("an encryption passphrase is required")
        commands_list = commands.format_commands(self.plan)
        for argv in commands_list:
            is_luks = bool(argv and argv[0] == "cryptsetup")
            stdin_secret = self.secrets.luks_passphrase if is_luks else None
            # cryptsetup treats --key-file - as raw bytes. A trailing newline
            # would become part of the key and break the initramfs prompt.
            self._run(
                argv,
                stdin_secret=stdin_secret,
                secret_newline=not is_luks,
                timeout=1800,
            )
            if argv and argv[0] == "cryptsetup" and "open" in argv:
                self._wait(Path(f"/dev/mapper/{self.plan.encryption.mapper_name}"), "LUKS mapper")

    def phase_mount(self) -> None:
        self.target.mkdir(parents=True, exist_ok=True)
        if self.plan.filesystem_kind.value == "btrfs":
            self._run(commands.top_level_mount(self.plan, str(self.target)), timeout=120)
            self._target_mounted = True
            for argv in commands.create_subvolume_commands(self.plan, str(self.target)):
                self._run(argv, timeout=120)
            self._run(["umount", str(self.target)], timeout=120)
            self._target_mounted = False
        self._run(commands.mount_commands(self.plan, str(self.target))[0], timeout=120)
        self._target_mounted = True

        boot = next((p for p in self.plan.partitions if p.role is PartitionRole.BOOT), None)
        esp = next((p for p in self.plan.partitions if p.role is PartitionRole.ESP), None)
        if boot:
            (self.target / "boot").mkdir(parents=True, exist_ok=True)
            self._run(["mount", partition_path(self.plan.disk.path, boot.number), str(self.target / "boot")], timeout=120)
        if esp:
            (self.target / "boot" / "efi").mkdir(parents=True, exist_ok=True)
            self._run(["mount", partition_path(self.plan.disk.path, esp.number), str(self.target / "boot" / "efi")], timeout=120)

        if self.plan.filesystem_kind.value == "btrfs":
            subvolumes = sorted(
                (s for s in self.plan.subvolumes if s.mountpoint != "/"),
                key=lambda s: s.mountpoint.count("/"),
            )
            for subvolume in subvolumes:
                mountpoint = self.target / subvolume.mountpoint.lstrip("/")
                mountpoint.mkdir(parents=True, exist_ok=True)
                self._run(
                    [
                        "mount",
                        "-o",
                        ",".join(subvolume.options),
                        self.plan.root_device,
                        str(mountpoint),
                    ],
                    timeout=120,
                )
        for argv in commands.swapfile_commands(self.plan, str(self.target)):
            self._run(argv, timeout=300)
        tmp_path = self.target / "tmp"
        if tmp_path.is_dir():
            tmp_path.chmod(0o1777)

    def phase_base(self) -> None:
        self._run(
            commands.debootstrap_command(self.plan, self.repo.debian, str(self.target)),
            timeout=1800,
        )
        if not (self.target / "etc").is_dir():
            raise InstallError("debootstrap finished without creating /etc")
        (self.target / "tmp").mkdir(mode=0o1777, exist_ok=True)
        (self.target / "tmp").chmod(0o1777)
        (self.target / "etc" / "apt").mkdir(parents=True, exist_ok=True)
        self._write(
            "etc/apt/sources.list",
            commands.render_sources_list(self.repo.debian, self.plan),
        )
        if getattr(self.plan.software, "external_repos", ()):
            self._setup_external_repos()
        resolv = Path("/etc/resolv.conf")
        if resolv.is_file():
            shutil.copyfile(resolv, self.target / "etc" / "resolv.conf")
        self._write("usr/sbin/policy-rc.d", "#!/bin/sh\nexit 101\n", mode=0o755)
        self._bind_mounts_now()
        if self.plan.offline:
            repo_path = self.target / "var" / "cache" / "spiral-repo"
            repo_path.mkdir(parents=True, exist_ok=True)
            self._run(
                ["mount", "--bind", self.repo.installer.iso_repo, str(repo_path)],
                timeout=60,
            )
            self._bind_mounts.append(str(repo_path))
        for arch in self.plan.software.foreign_architectures:
            self._run(commands.add_foreign_architecture(str(self.target), arch), timeout=120)
        self._run(commands.apt_update(str(self.target)), timeout=900)
        base = self._groups_packages("base") if "base" in self.plan.software.package_groups else []
        self._apt_install(base)

    def _bind_mounts_now(self) -> None:
        for source in ("/dev", "/dev/pts", "/proc", "/sys", "/run"):
            destination = self.target / source.lstrip("/")
            destination.mkdir(parents=True, exist_ok=True)
            self._run(["mount", "--bind", source, str(destination)], timeout=60)
            self._bind_mounts.append(str(destination))
        for source in ("/dev", "/sys"):
            self._run(["mount", "--make-rslave", str(self.target / source.lstrip("/"))], timeout=60, check=False)

    def phase_kernel(self) -> None:
        self._apt_install(
            list(self.plan.software.kernel_packages) + list(self.plan.software.fallback_kernel_packages),
            timeout=2400,
        )

    def phase_firmware(self) -> None:
        if "firmware" in self.plan.software.package_groups:
            self._apt_install(self._groups_packages("firmware"), timeout=1800)

    def phase_drivers(self) -> None:
        self._apt_install(list(self.plan.software.driver_packages), timeout=1800)

    def phase_desktops(self) -> None:
        self._apt_install(list(self.plan.software.desktop_packages), timeout=3600)

    def phase_software(self) -> None:
        if self.plan.firmware is Firmware.BIOS:
            selections = (
                f"grub-pc grub-pc/install_devices multiselect {self.plan.disk.path}\n"
                "grub-pc grub-pc/install_devices_empty boolean false\n"
            )
            self._run(
                ["chroot", str(self.target), "debconf-set-selections"],
                stdin_text=selections,
                timeout=60,
            )
        self._apt_install(list(self.plan.software.packages), timeout=3600)

    def phase_users(self) -> None:
        try:
            self._run(commands.useradd_command(self.plan, str(self.target)), timeout=120)
        except InstallError as exc:
            raise InstallError(f"could not create user: {exc.message}") from exc
        credential = Secret(f"{self.plan.user.username}:{self.secrets.user_password.reveal()}")
        self._run(
            ["chroot", str(self.target), "chpasswd"],
            stdin_secret=credential,
            timeout=120,
        )
        self._run(["chroot", str(self.target), "passwd", "-l", "root"], timeout=120)

    def phase_configure(self) -> None:
        self._prepare_fstab()
        self._write_system_files()
        self._remove_policy_rc()
        if self.log:
            self.log.event("info", "configure", "complete", "system configuration written")

    def _prepare_fstab(self) -> None:
        # UUIDs are read only after mkfs, so fstab and crypttab are written here,
        # before initramfs generation, rather than in the storage phase.
        root_uuid = self._uuid(self.plan.root_device)
        boot = next((p for p in self.plan.partitions if p.role is PartitionRole.BOOT), None)
        esp = next((p for p in self.plan.partitions if p.role is PartitionRole.ESP), None)
        swap = next((p for p in self.plan.partitions if p.role is PartitionRole.SWAP), None)
        boot_uuid = self._uuid(partition_path(self.plan.disk.path, boot.number)) if boot else None
        esp_uuid = self._uuid(partition_path(self.plan.disk.path, esp.number)) if esp else None
        swap_uuid = self._uuid(partition_path(self.plan.disk.path, swap.number)) if swap else None
        self._write(
            "etc/fstab",
            commands.render_fstab(self.plan, root_uuid, boot_uuid, esp_uuid, swap_uuid),
        )
        if self.plan.encryption.enabled:
            raw_root = partition_path(self.plan.disk.path, self.plan.root_partition.number)
            luks_uuid = self._uuid(raw_root)
            self._write(
                "etc/crypttab",
                commands.render_crypttab(
                    luks_uuid,
                    self.plan.encryption.mapper_name,
                    self.plan.encryption.tpm_unlock,
                    self.plan.encryption.tpm_pcrs,
                ),
            )

    def _write_system_files(self) -> None:
        user = self.plan.user
        locale = self.plan.locale
        self._write("etc/hostname", user.hostname + "\n")
        self._write("etc/hosts", f"127.0.0.1 localhost\n127.0.1.1 {user.hostname}\n::1 localhost ip6-localhost ip6-loopback\n")
        self._write("etc/locale.gen", f"{locale.locale} UTF-8\n")
        self._chroot("locale-gen", timeout=300)
        self._write("etc/default/locale", f"LANG={locale.locale}\n")
        zoneinfo = self.target / "usr" / "share" / "zoneinfo" / locale.timezone
        if not zoneinfo.is_file():
            raise InstallError(f"timezone data is missing for {locale.timezone}")
        localtime = self.target / "etc" / "localtime"
        if localtime.exists() or localtime.is_symlink():
            localtime.unlink()
        os.symlink(f"/usr/share/zoneinfo/{locale.timezone}", localtime)
        self._write("etc/timezone", locale.timezone + "\n")
        self._write(
            "etc/default/keyboard",
            'XKBMODEL="pc105"\nXKBLAYOUT="%s"\nXKBVARIANT=""\nXKBOPTIONS=""\n' % locale.keymap,
        )
        self._write("etc/vconsole.conf", f"KEYMAP={locale.keymap}\n")
        if self.plan.software.snapshots:
            self._chroot("snapper", "create-config", "/", timeout=300)
            self._chroot("systemctl", "enable", "snapper-timeline.timer", "snapper-cleanup.timer", timeout=120)
        if self.plan.swap.strategy is SwapStrategy.ZRAM:
            percent = int(round(self.repo.installer.zram_fraction * 100))
            self._write("etc/default/zramswap", f"ALGO=zstd\nPERCENT={percent}\n")
            self._chroot("systemctl", "enable", "zramswap.service", timeout=120)
        if self.plan.software.btrfs_maintenance:
            self._chroot("systemctl", "enable", "btrfs-scrub.timer", timeout=120)
        if self.plan.encryption.tpm_unlock:
            self._configure_tpm()
        if user.autologin:
            self._configure_autologin()
        if getattr(self.plan.software, "scx_enabled", False):
            self._configure_scx()
        if self.plan.software.kernel_id == "tkg":
            self._write_tkg_helper()
        grub_default = self.target / "etc" / "default" / "grub"
        existing = grub_default.read_text(encoding="utf-8") if grub_default.is_file() else ""
        console = "console=tty0 console=ttyS0,115200"
        extra = '\nGRUB_DISTRIBUTOR="Spiral"\nGRUB_TIMEOUT=5\n'
        if self.plan.filesystem_kind.value == "btrfs":
            extra += (
                f'GRUB_CMDLINE_LINUX="rootflags=subvol=@ {console}"\n'
                'GRUB_PRELOAD_MODULES="btrfs"\n'
            )
        else:
            extra += f'GRUB_CMDLINE_LINUX="{console}"\n'
        grub_default.write_text(existing.rstrip() + "\n" + extra, encoding="utf-8")
        self._write("etc/issue", f"Spiral {self.plan.release.version} ({self.plan.release.codename})\n")
        assets = self.repo.root / "assets" / "branding"
        if assets.is_dir():
            destination = self.target / "usr" / "share" / "spiral" / "branding"
            shutil.copytree(assets, destination, dirs_exist_ok=True)
        if self.plan.software.snapshots:
            self._write(
                "etc/spiral/snapshots.txt",
                "Snapshots are not backups. Keep an independent copy of important data.\n",
            )

    def _configure_autologin(self) -> None:
        user = self.plan.user
        manager = self.plan.software.display_manager
        # Find the desktop that owns the chosen display manager to pick the correct session name
        # (handles minimal+gnome, gnome+kde, etc.). Falls back to first desktop if needed.
        desktops = []
        for did in self.plan.software.desktop_ids:
            try:
                desktops.append(self.repo.profiles.desktop(did))
            except Exception:
                continue
        candidate = next((d for d in desktops if d.display_manager == manager), None)
        if candidate is None and desktops:
            candidate = desktops[0]
        session = (candidate.session or "") if candidate else ""
        # Normalize session names for GDM/SDDM expectations on Debian trixie
        # GNOME session file is gnome.desktop (or gnome-xorg for X11 fallback)
        # KDE Plasma session is plasma.desktop (plasmax11) or plasmawayland
        if manager == "gdm3":
            # GDM3 on Debian listens to /etc/gdm3/custom.conf [daemon] section;
            # daemon.conf is package-managed but we write both for robustness.
            # GNOME (default) uses session "gnome" which maps to gnome.desktop; GDM picks it automatically
            # when AutomaticLogin is set, but we explicitly set the session for mixed-desktop installs.
            gdm_content = f"[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin={user.username}\n"
            # If we know the session (e.g. gnome) and it is not the default, add it
            if session and session not in ("gnome",):
                gdm_content += f"DefaultSession={session}\n"
            self._write("etc/gdm3/custom.conf", gdm_content)
            self._write("etc/gdm3/daemon.conf", gdm_content)
            # Ensure GDM is enabled (packages install it but not necessarily enabled in debootstrap)
            self._chroot("systemctl", "enable", "gdm3.service", timeout=60, check=False)
            # GNOME also benefits from AccountsService marking - not strictly required for GDM autologin
            if self.log:
                self.log.event("info", "configure", "autologin", f"GDM autologin for {user.username} (session={session or 'gnome'})")
        elif manager == "lightdm":
            self._write(
                "etc/lightdm/lightdm.conf.d/50-spiral-autologin.conf",
                f"[Seat:*]\nautologin-user={user.username}\nautologin-user-timeout=0\n",
            )
            self._chroot("systemctl", "enable", "lightdm.service", timeout=60, check=False)
        elif manager == "sddm":
            # SDDM (KDE Plasma) expects User and Session in [Autologin]. Session names:
            # plasma = plasma.desktop (X11), plasmawayland = wayland. Use the desktop profile session.
            # For KDE, session is "plasma" which correctly resolves to plasma.desktop on Debian.
            sddm_session = session or "plasma"
            self._write(
                "etc/sddm.conf.d/50-spiral-autologin.conf",
                f"[Autologin]\nUser={user.username}\nSession={sddm_session}.desktop\n",
            )
            # Also ensure SDDM is the default display manager if both GDM and SDDM are present
            # (happens with gnome+kde). The chosen DM is already the first-found, so enable it.
            self._chroot("systemctl", "enable", "sddm.service", timeout=60, check=False)
            if self.log:
                self.log.event("info", "configure", "autologin", f"SDDM autologin for {user.username} session={sddm_session}")
        else:
            raise InstallError(f"automatic login is not implemented for {manager}")

    def _configure_tpm(self) -> None:
        """Configure initramfs for TPM2 unlock and attempt enrollment."""
        if not self.plan.encryption.tpm_unlock:
            return
        # Ensure cryptsetup initramfs hook is enabled (required for TPM unlock in initramfs)
        try:
            self._write("etc/cryptsetup-initramfs/conf-hook", "CRYPTSETUP=y\n")
        except Exception:
            pass
        # Write a marker with PCR policy for diagnostics
        self._write("etc/spiral/tpm.json", f'{{"pcrs": "{self.plan.encryption.tpm_pcrs}", "device": "auto"}}\n', mode=0o600)
        if self.log:
            self.log.event("info", "configure", "tpm", f"TPM2 unlock configured PCRs={self.plan.encryption.tpm_pcrs}")
        # Attempt enrollment now (after crypttab is written). Enrollment adds a TPM-bound keyslot
        # to the LUKS device while keeping the passphrase slot as fallback.
        raw_root = partition_path(self.plan.disk.path, self.plan.root_partition.number)
        self._enroll_tpm(raw_root)

    def _enroll_tpm(self, raw_device: str) -> None:
        if self.secrets.luks_passphrase is None:
            if self.log:
                self.log.event("warning", "tpm", "enroll", "no LUKS passphrase available for TPM enrollment; skipping")
            return
        # Check for TPM device in the live environment (host) - if missing, we cannot enroll
        # but the passphrase fallback remains usable.
        # We check both host and target bind-mounted /dev (same devices after bind mounts)
        tpm_present = False
        for candidate in (Path("/dev/tpm0"), Path("/dev/tpmrm0"), self.target / "dev" / "tpm0"):
            try:
                if candidate.exists():
                    tpm_present = True
                    break
            except OSError:
                continue
        # Also check sysfs if /dev check failed (e.g. permissions)
        if not tpm_present:
            try:
                if Path("/sys/class/tpm").is_dir() and any(Path("/sys/class/tpm").iterdir()):
                    tpm_present = True
            except OSError:
                pass
        if not tpm_present:
            if self.log:
                self.log.event("warning", "tpm", "enroll", "no TPM device found (/dev/tpm0); TPM enrollment skipped, passphrase fallback remains")
            return
        pcrs = self.plan.encryption.tpm_pcrs or "7"
        # Prefer systemd-cryptenroll (Debian trixie, systemd >=252 with TPM2 support)
        # Fallback to clevis if systemd-cryptenroll is unavailable or fails
        enroll_success = False
        # Check if systemd-cryptenroll exists in the target (preferred) or host
        has_systemd_enroll = False
        for prefix in (str(self.target), ""):
            # Use which-like check via chroot or host
            try:
                if prefix:
                    res = self._run(["chroot", str(self.target), "which", "systemd-cryptenroll"], timeout=10, check=False)
                    if getattr(res, "returncode", 1) == 0:
                        has_systemd_enroll = True
                        break
                else:
                    if which("systemd-cryptenroll"):
                        has_systemd_enroll = True
                        break
            except Exception:
                continue
        if has_systemd_enroll:
            try:
                argv = commands.tpm_enroll_command(raw_device, pcrs)
                # Run inside chroot if available there, otherwise on host (both see same /dev)
                # Prefer chroot so that the target's systemd version handles its own crypttab semantics
                # Check if chroot has the binary
                chroot_has = False
                try:
                    chk = self._run(["chroot", str(self.target), "which", "systemd-cryptenroll"], timeout=10, check=False)
                    chroot_has = getattr(chk, "returncode", 1) == 0
                except Exception:
                    chroot_has = False
                if chroot_has:
                    self._run(["chroot", str(self.target), *argv], stdin_secret=self.secrets.luks_passphrase, secret_newline=False, timeout=120)
                else:
                    self._run(argv, stdin_secret=self.secrets.luks_passphrase, secret_newline=False, timeout=120)
                enroll_success = True
                if self.log:
                    self.log.event("info", "tpm", "enroll", f"TPM2 enrollment succeeded via systemd-cryptenroll PCRs={pcrs}")
            except Exception as exc:
                if self.log:
                    self.log.event("warning", "tpm", "enroll", f"systemd-cryptenroll failed: {exc}; trying clevis fallback")
        # Fallback: clevis (older but still shipped in Debian)
        if not enroll_success:
            try:
                # Clevis enrollment via clevis luks bind -d <device> tpm2 '{}' requires the device to be unlocked?
                # Actually clevis luks bind works on the LUKS header directly with passphrase via PIN input.
                # We check for clevis availability
                has_clevis = False
                for prefix in (str(self.target), ""):
                    try:
                        if prefix:
                            res = self._run(["chroot", str(self.target), "which", "clevis"], timeout=10, check=False)
                            if getattr(res, "returncode", 1) == 0:
                                has_clevis = True
                                break
                        else:
                            if which("clevis"):
                                has_clevis = True
                                break
                    except Exception:
                        continue
                if has_clevis:
                    # clevis luks bind expects interactive pin; on Debian it can take passphrase via stdin with --key-file=-
                    # We use the host/target clevis wrapper that supports key file
                    clevis_argv = ["chroot", str(self.target), "clevis", "luks", "bind", "-d", raw_device, "tpm2", "{}"]
                    # Check if chroot has clevis, else host
                    try:
                        chk = self._run(["chroot", str(self.target), "which", "clevis"], timeout=10, check=False)
                        if getattr(chk, "returncode", 1) != 0:
                            clevis_argv = ["clevis", "luks", "bind", "-d", raw_device, "tpm2", "{}"]
                    except Exception:
                        clevis_argv = ["clevis", "luks", "bind", "-d", raw_device, "tpm2", "{}"]
                    self._run(clevis_argv, stdin_secret=self.secrets.luks_passphrase, secret_newline=False, timeout=120)
                    enroll_success = True
                    if self.log:
                        self.log.event("info", "tpm", "enroll", "TPM2 enrollment succeeded via clevis")
                else:
                    if self.log:
                        self.log.event("warning", "tpm", "enroll", "neither systemd-cryptenroll nor clevis found; TPM enrollment skipped")
            except Exception as exc:
                if self.log:
                    self.log.event("warning", "tpm", "enroll", f"clevis enrollment failed: {exc}; passphrase fallback remains")
        if not enroll_success and self.log:
            self.log.event("warning", "tpm", "enroll", "TPM enrollment did not succeed; the system will still unlock with the passphrase")

    def _setup_external_repos(self) -> None:
        for repo_id in getattr(self.plan.software, "external_repos", ()):
            repo = self.repo.external_repos.get(repo_id)
            if repo is None:
                if self.log:
                    self.log.event("warning", "external-repo", repo_id, f"unknown external repo {repo_id!r}, skipping")
                continue
            try:
                self._setup_single_external_repo(repo)
                if self.log:
                    self.log.event("info", "external-repo", repo_id, f"configured {repo.label}")
            except Exception as exc:
                if self.log:
                    self.log.event("warning", "external-repo", repo_id, f"failed to configure {repo.label}: {exc}")
                continue

    def _setup_single_external_repo(self, repo) -> None:
        codename = self.plan.release.codename
        dist = (repo.dist_map or {}).get(codename, codename)
        if repo.id == "nvidia-cuda" and getattr(repo, "keyring_url_template", None):
            keyring_url = repo.keyring_url_template.replace("{dist}", dist).replace("{codename}", codename)
            self._run(["chroot", str(self.target), "mkdir", "-p", "/usr/share/keyrings"], timeout=30, check=False)
            deb_tmp = self.target / "tmp" / "cuda-keyring.deb"
            deb_tmp.parent.mkdir(parents=True, exist_ok=True)
            fetched = False
            for cmd in (  # nosec B108 - /tmp is correct for chroot and installer transient files
                ["chroot", str(self.target), "curl", "-fsSL", "--proto", "=https", "--tlsv1.2", keyring_url, "-o", "/tmp/cuda-keyring.deb"],
                ["curl", "-fsSL", "--proto", "=https", "--tlsv1.2", keyring_url, "-o", str(deb_tmp)],
            ):
                try:
                    self._run(cmd, timeout=60, check=False)
                    if (self.target / "tmp" / "cuda-keyring.deb").exists() or deb_tmp.exists():
                        fetched = True
                        break
                except Exception:
                    continue
            if fetched:
                try:
                    self._run(["chroot", str(self.target), "dpkg", "-i", "/tmp/cuda-keyring.deb"], timeout=60, check=False)  # nosec B108
                except Exception:
                    pass
            sources_line = (repo.sources_line_template or "").replace("{dist}", dist).replace("{codename}", codename)
            if sources_line:
                self._write(f"etc/apt/sources.list.d/{repo.id}.list", sources_line + "\n")
            return
        if getattr(repo, "key_url", None) and getattr(repo, "keyring_path", None):
            keyring_path = repo.keyring_path
            target_keyring = self.target / keyring_path.lstrip("/")
            target_keyring.parent.mkdir(parents=True, exist_ok=True)
            key_url = repo.key_url
            tmp_key = self.target / "tmp" / f"{repo.id}.key"
            tmp_key.parent.mkdir(parents=True, exist_ok=True)
            fetched = False
            for cmd in (  # nosec B108 - /tmp is correct for chroot and installer transient files
                ["chroot", str(self.target), "curl", "-fsSL", key_url, "-o", f"/tmp/{repo.id}.key"],
                ["curl", "-fsSL", key_url, "-o", str(tmp_key)],
            ):
                try:
                    res = self._run(cmd, timeout=60, check=False)
                    if getattr(res, "returncode", 1) == 0:
                        fetched = True
                        break
                except Exception:
                    continue
            if fetched:
                src = f"/tmp/{repo.id}.key"  # nosec B108
                dst = keyring_path
                try:
                    self._run(["chroot", str(self.target), "gpg", "--dearmor", "-o", dst, src], timeout=30, check=False)
                except Exception:
                    try:
                        host_src = str(self.target / src.lstrip("/")) if src.startswith("/") else str(tmp_key)
                        self._run(["gpg", "--dearmor", "-o", str(target_keyring), host_src], timeout=30, check=False)
                    except Exception:
                        pass
        sources_line = (repo.sources_line_template or "").replace("{codename}", codename).replace("{dist}", dist)
        if sources_line:
            self._write(f"etc/apt/sources.list.d/{repo.id}.list", sources_line + "\n")

    def _configure_scx(self) -> None:
        scheduler = getattr(self.plan.software, "scx_scheduler", "lavd") or "lavd"
        sched_bin = f"scx_{scheduler}"
        try:
            self._write("etc/default/scx", f'SCX_SCHEDULER={sched_bin}\n')
        except Exception:
            pass
        try:
            (self.target / "etc" / "scx_loader").mkdir(parents=True, exist_ok=True)
            config = (
                f'default_sched = "{sched_bin}"\n'
                f'default_mode = "Auto"\n'
                f'\n'
                f'[scheds.{sched_bin}]\n'
                f'auto_mode = []\n'
                f'gaming_mode = ["--performance"]\n'
                f'lowlatency_mode = ["--performance"]\n'
                f'powersave_mode = ["--powersave"]\n'
                f'server_mode = []\n'
            )
            self._write("etc/scx_loader/config.toml", config)
            self._write("etc/scx_loader.toml", config)
        except Exception:
            pass
        for svc in ("scx_loader.service", "scx.service"):
            try:
                self._run(["chroot", str(self.target), "systemctl", "enable", svc], timeout=30, check=False)
                if self.log:
                    self.log.event("info", "configure", "scx", f"enabled {svc} with {sched_bin}")
                break
            except Exception:
                continue
        try:
            self._write("etc/spiral/scx.json", f'{{\"enabled\": true, \"scheduler\": \"{scheduler}\", \"binary\": \"{sched_bin}\"}}\n')
        except Exception:
            pass

    def _write_tkg_helper(self) -> None:
        script = """#!/bin/bash
set -e
echo "TKG kernel selected but no binary packages exist. Building from source..."
echo "See https://github.com/Frogging-Family/linux-tkg"
if [ "$(id -u)" -ne 0 ]; then echo "Run as root"; exit 1; fi
apt-get update
apt-get install -y git build-essential libncurses-dev bison flex libssl-dev libelf-dev dwarves bc
rm -rf /usr/src/linux-tkg
git clone https://github.com/Frogging-Family/linux-tkg.git /usr/src/linux-tkg
cd /usr/src/linux-tkg
cp customization.cfg customization.cfg.orig
sed -i 's/^_distro=""/_distro="Debian"/' customization.cfg || true
./install.sh install
echo "TKG build finished. Reboot and select TKG kernel in GRUB."
"""
        try:
            self._write("usr/local/sbin/tkg-build.sh", script, mode=0o755)
            self._write("etc/spiral/tkg.txt", "TKG kernel requires manual build via /usr/local/sbin/tkg-build.sh\n")
        except Exception:
            pass

    def _remove_policy_rc(self) -> None:
        policy = self.target / "usr" / "sbin" / "policy-rc.d"
        try:
            policy.unlink()
        except FileNotFoundError:
            pass

    def phase_bootloader(self) -> None:
        if self.plan.firmware.value == "uefi":
            if not os.path.ismount(self.target / "boot" / "efi"):
                raise InstallError("/boot/efi is not mounted; refusing to install the bootloader")
        self._run(commands.grub_install_command(self.plan, str(self.target)), timeout=600)
        if self.plan.firmware is Firmware.UEFI:
            self._run(commands.grub_removable_command(self.plan, str(self.target)), timeout=600)
        self._run(commands.update_grub_command(str(self.target)), timeout=600)
        if not (self.target / "boot" / "grub" / "grub.cfg").is_file():
            raise InstallError("GRUB did not generate /boot/grub/grub.cfg")

    def phase_initramfs(self) -> None:
        self._run(commands.initramfs_command(str(self.target)), timeout=900)
        self._run(commands.update_grub_command(str(self.target)), timeout=600)

    def phase_verify(self) -> None:
        problems = []
        for required in ("etc/fstab", "etc/hostname", "boot/grub/grub.cfg"):
            if not (self.target / required).is_file():
                problems.append(f"missing /{required}")
        if not list((self.target / "boot").glob("vmlinuz-*")):
            problems.append("no kernel image in /boot")
        if not list((self.target / "boot").glob("initrd.img-*")):
            problems.append("no initramfs in /boot")
        if self.plan.encryption.enabled and not (self.target / "etc" / "crypttab").is_file():
            problems.append("encrypted install has no /etc/crypttab")
        passwd = (self.target / "etc" / "passwd").read_text(encoding="utf-8", errors="replace")
        if not any(line.startswith(self.plan.user.username + ":") for line in passwd.splitlines()):
            problems.append("the user account was not created")
        status = self._chroot("passwd", "-S", self.plan.user.username, timeout=60, check=False)
        output = (getattr(status, "stdout", "") or "").split()
        if len(output) >= 2 and output[1] in ("L", "NP"):
            problems.append("the user password is locked or empty")
        if self.plan.firmware.value == "uefi":
            efi_dir = self.target / "boot" / "efi" / "EFI" / "Spiral"
            if not efi_dir.is_dir() or not list(efi_dir.glob("*.efi")):
                problems.append("no EFI bootloader was installed under /boot/efi/EFI/Spiral")
            fallback_dir = self.target / "boot" / "efi" / "EFI" / "BOOT"
            if not fallback_dir.is_dir() or not list(fallback_dir.glob("*.efi")):
                problems.append("no removable-media EFI fallback loader was installed")
        else:
            if not (self.target / "boot" / "grub" / "i386-pc" / "normal.mod").is_file():
                problems.append("BIOS GRUB modules are missing")
        if problems:
            raise InstallError("installation verification failed: " + "; ".join(problems))

    def phase_cleanup(self) -> None:
        if self.log:
            self.log.event("info", "cleanup", "start", "unmounting target")
            try:
                destination = self.target / "var" / "log" / "spiral-installer"
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.log_path, destination / "install.log")
                (destination / "plan.json").write_text(
                    json.dumps(self.plan.summary(), indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                self.log.event("warning", "cleanup", "logs", f"could not copy logs: {exc}")
        self._run(["sync"], timeout=120, check=False)
        result = self._run(["umount", "-R", str(self.target)], timeout=300, check=False)
        if getattr(result, "returncode", 1) != 0:
            if self.log:
                self.log.event(
                    "warning",
                    "cleanup",
                    "umount",
                    "could not unmount the target; reboot before retrying",
                )
        else:
            self._target_mounted = False
        if self.plan.encryption.enabled:
            self._run(
                ["cryptsetup", "close", self.plan.encryption.mapper_name],
                timeout=120,
                check=False,
            )
