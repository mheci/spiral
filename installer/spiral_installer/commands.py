from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from .config import DebianConfig
from .models import InstallPlan, PartitionPlan, PartitionRole, PartitionTable, SwapStrategy

MIB = 1024**2


@dataclass(frozen=True)
class FstabEntry:
    spec: str
    mountpoint: str
    fstype: str
    options: str
    dump: int = 0
    passno: int = 0


def _part(plan: InstallPlan, role: PartitionRole) -> Optional[PartitionPlan]:
    return next((part for part in plan.partitions if part.role is role), None)


def _device(plan: InstallPlan, part: PartitionPlan) -> str:
    from .util import partition_path

    return partition_path(plan.disk.path, part.number)


def _label(role: PartitionRole) -> str:
    return {
        PartitionRole.ESP: "spiral-esp",
        PartitionRole.BOOT: "spiral-boot",
        PartitionRole.BIOS_GRUB: "spiral-bios",
        PartitionRole.SWAP: "spiral-swap",
        PartitionRole.ROOT: "spiral-root",
    }[role]


def partition_commands(plan: InstallPlan) -> list[list[str]]:
    if plan.partition_table is PartitionTable.GPT:
        commands = [
            ["wipefs", "--all", "--force", plan.disk.path],
            ["sgdisk", "--zap-all", plan.disk.path],
        ]
        for part in plan.partitions:
            size = "0" if part.role is PartitionRole.ROOT else (
                f"+{part.size_mib}M" if part.size_mib else "0"
            )
            new = f"--new={part.number}:0:{size}"
            commands.append(
                [
                    "sgdisk",
                    new,
                    f"--typecode={part.number}:{part.type_code}",
                    f"--change-name={part.number}:{_label(part.role)}",
                    plan.disk.path,
                ]
            )
        commands.extend([["partprobe", plan.disk.path], ["udevadm", "settle"]])
        return commands

    commands = [
        ["wipefs", "--all", "--force", plan.disk.path],
        ["parted", "-s", plan.disk.path, "mklabel", "msdos"],
    ]
    start_mib = 1
    for position, part in enumerate(plan.partitions):
        end = "100%" if position == len(plan.partitions) - 1 else f"{start_mib + (part.size_mib or 0)}MiB"
        fs_type = {
            "vfat": "fat32",
            "ext4": "ext4",
            "swap": "linux-swap",
            "btrfs": "btrfs",
            "xfs": "xfs",
            "luks": "ext2",
            "none": "ext2",
        }[part.fs]
        commands.append(
            [
                "parted",
                "-s",
                "-a",
                "optimal",
                plan.disk.path,
                "mkpart",
                "primary",
                fs_type,
                f"{start_mib}MiB",
                end,
            ]
        )
        if part.size_mib:
            start_mib += part.size_mib
    commands.extend([["partprobe", plan.disk.path], ["udevadm", "settle"]])
    return commands


def format_commands(plan: InstallPlan) -> list[list[str]]:
    commands: list[list[str]] = []
    esp = _part(plan, PartitionRole.ESP)
    boot = _part(plan, PartitionRole.BOOT)
    swap = _part(plan, PartitionRole.SWAP)
    root = plan.root_partition
    if esp:
        commands.append(["mkfs.vfat", "-F", "32", "-n", "SPIRAL_EFI", _device(plan, esp)])
    if boot:
        commands.append(["mkfs.ext4", "-F", "-L", "SPIRAL_BOOT", _device(plan, boot)])
    if swap:
        commands.append(["mkswap", "-L", "SPIRAL_SWAP", _device(plan, swap)])
    if plan.encryption.enabled:
        commands.append(
            [
                "cryptsetup",
                "--batch-mode",
                "--type",
                plan.encryption.luks_type,
                "--pbkdf",
                plan.encryption.pbkdf,
                "--cipher",
                "aes-xts-plain64",
                "--key-size",
                "512",
                "--hash",
                "sha256",
                "--label",
                "spiral-crypt",
                "--key-file",
                "-",
                "luksFormat",
                _device(plan, root),
            ]
        )
        commands.append(
            [
                "cryptsetup",
                "--key-file",
                "-",
                "open",
                _device(plan, root),
                plan.encryption.mapper_name,
            ]
        )
    device = plan.root_device
    if plan.filesystem_kind.value == "btrfs":
        commands.append(["mkfs.btrfs", "-L", "SPIRAL", "-f", device])
    elif plan.filesystem_kind.value == "ext4":
        commands.append(["mkfs.ext4", "-F", "-L", "SPIRAL", device])
    elif plan.filesystem_kind.value == "xfs":
        commands.append(["mkfs.xfs", "-f", "-L", "SPIRAL", device])
    else:
        raise ValueError(f"unsupported filesystem {plan.filesystem_kind}")
    return commands


def top_level_mount(plan: InstallPlan, target: str) -> list[str]:
    if plan.filesystem_kind.value == "btrfs":
        return ["mount", "-o", "subvolid=5", plan.root_device, target]
    return ["mount", plan.root_device, target]


def create_subvolume_commands(plan: InstallPlan, target: str) -> list[list[str]]:
    commands = []
    for subvolume in plan.subvolumes:
        path = target.rstrip("/") + "/" + subvolume.name
        commands.append(["btrfs", "subvolume", "create", path])
    return commands


def mount_commands(plan: InstallPlan, target: str) -> list[list[str]]:
    commands: list[list[str]] = []
    if plan.filesystem_kind.value == "btrfs":
        root_subvol = next(s for s in plan.subvolumes if s.mountpoint == "/")
        commands.append(
            ["mount", "-o", ",".join(root_subvol.options), plan.root_device, target]
        )
    else:
        commands.append(["mount", plan.root_device, target])
    boot = _part(plan, PartitionRole.BOOT)
    if boot:
        commands.append(["mount", _device(plan, boot), f"{target}/boot"])
    esp = _part(plan, PartitionRole.ESP)
    if esp:
        commands.append(["mount", _device(plan, esp), f"{target}/boot/efi"])
    if plan.filesystem_kind.value == "btrfs":
        for subvolume in plan.subvolumes:
            if subvolume.mountpoint == "/":
                continue
            mountpoint = target.rstrip("/") + subvolume.mountpoint
            commands.append(
                ["mount", "-o", ",".join(subvolume.options), plan.root_device, mountpoint]
            )
    return commands


def swapfile_commands(plan: InstallPlan, target: str) -> list[list[str]]:
    if plan.swap.strategy is not SwapStrategy.FILE:
        return []
    return [
        ["chattr", "+C", f"{target}/swap"],
        [
            "btrfs",
            "filesystem",
            "mkswapfile",
            f"--size={plan.swap.size_mib}M",
            f"{target}/swap/swapfile",
        ],
    ]


def render_fstab(
    plan: InstallPlan,
    root_uuid: str,
    boot_uuid: Optional[str] = None,
    esp_uuid: Optional[str] = None,
    swap_uuid: Optional[str] = None,
) -> str:
    entries: list[FstabEntry] = []
    if plan.filesystem_kind.value == "btrfs":
        for subvolume in plan.subvolumes:
            entries.append(
                FstabEntry(
                    spec=f"UUID={root_uuid}",
                    mountpoint=subvolume.mountpoint,
                    fstype="btrfs",
                    options=",".join(subvolume.options),
                )
            )
    else:
        options = "defaults"
        entries.append(
            FstabEntry(
                spec=f"UUID={root_uuid}",
                mountpoint="/",
                fstype=plan.filesystem_kind.value,
                options=options,
                passno=1 if plan.filesystem_kind.value == "ext4" else 0,
            )
        )
    if boot_uuid:
        entries.append(
            FstabEntry(
                spec=f"UUID={boot_uuid}",
                mountpoint="/boot",
                fstype="ext4",
                options="defaults",
                passno=2,
            )
        )
    if esp_uuid:
        entries.append(
            FstabEntry(
                spec=f"UUID={esp_uuid}",
                mountpoint="/boot/efi",
                fstype="vfat",
                options="umask=0077",
                passno=1,
            )
        )
    if plan.swap.strategy is SwapStrategy.PARTITION and swap_uuid:
        entries.append(
            FstabEntry(
                spec=f"UUID={swap_uuid}",
                mountpoint="none",
                fstype="swap",
                options="sw",
            )
        )
    elif plan.swap.strategy is SwapStrategy.FILE:
        entries.append(
            FstabEntry(
                spec="/swap/swapfile",
                mountpoint="none",
                fstype="swap",
                options="sw",
            )
        )
    entries.sort(key=lambda entry: (entry.mountpoint != "/", entry.mountpoint))
    lines = ["# <file system> <mount point> <type> <options> <dump> <pass>"]
    for entry in entries:
        lines.append(
            f"{entry.spec:<40} {entry.mountpoint:<15} {entry.fstype:<6} {entry.options:<24} {entry.dump} {entry.passno}"
        )
    return "\n".join(lines) + "\n"


def render_crypttab(luks_uuid: str, mapper: str) -> str:
    return f"{mapper} UUID={luks_uuid} none luks\n"


def render_sources_list(
    debian: DebianConfig,
    plan: InstallPlan,
    offline_repo: str = "/var/cache/spiral-repo",
) -> str:
    codename = plan.release.codename
    areas = " ".join(debian.archive_areas)
    if plan.offline:
        return (
            "# Spiral offline medium. The repository is part of the verified live image.\n"
            f"deb [trusted=yes] file://{offline_repo} {codename} main\n"
        )
    return (
        f"deb {debian.debian_mirror} {codename} {areas}\n"
        f"deb {debian.debian_mirror} {codename}-updates {areas}\n"
        f"deb {debian.security_mirror} {codename}-security {areas}\n"
    )


def debootstrap_command(plan: InstallPlan, debian: DebianConfig, target: str) -> list[str]:
    command = [
        "debootstrap",
        f"--arch={plan.architecture}",
        "--variant=minbase",
        f"--keyring={debian.keyring}",
        "--include=ca-certificates",
    ]
    if plan.offline:
        command.extend(["--components=main", "--no-check-gpg"])
        mirror = "file:///usr/share/spiral/repo"
    else:
        command.append(f"--components={','.join(debian.archive_areas)}")
        mirror = debian.debian_mirror
    command.extend([plan.release.codename, target, mirror])
    return command


def apt_update(target: str) -> list[str]:
    return ["chroot", target, "apt-get", "update"]


def apt_install(target: str, packages: Iterable[str]) -> list[str]:
    return ["chroot", target, "apt-get", "install", "-y", *sorted(set(packages))]


def add_foreign_architecture(target: str, architecture: str) -> list[str]:
    return ["chroot", target, "dpkg", "--add-architecture", architecture]


def grub_install_command(plan: InstallPlan, target: str) -> list[str]:
    if plan.firmware.value == "uefi":
        efivariant = "x86_64" if plan.architecture == "amd64" else plan.architecture
        command = [
            "chroot",
            target,
            "grub-install",
            f"--target={efivariant}-efi",
            "--efi-directory=/boot/efi",
            "--bootloader-id=Spiral",
            "--recheck",
        ]
        if plan.boot.secure_boot:
            command.append("--uefi-secure-boot")
        return command
    return [
        "chroot",
        target,
        "grub-install",
        "--target=i386-pc",
        "--recheck",
        plan.disk.path,
    ]


def grub_removable_command(plan: InstallPlan, target: str) -> list[str]:
    efivariant = "x86_64" if plan.architecture == "amd64" else plan.architecture
    command = [
        "chroot",
        target,
        "grub-install",
        f"--target={efivariant}-efi",
        "--efi-directory=/boot/efi",
        "--removable",
        "--no-nvram",
    ]
    if plan.boot.secure_boot:
        command.append("--uefi-secure-boot")
    return command


def initramfs_command(target: str) -> list[str]:
    return ["chroot", target, "update-initramfs", "-u", "-k", "all"]


def update_grub_command(target: str) -> list[str]:
    return ["chroot", target, "update-grub"]


def install_kernel_module_header_check(plan: InstallPlan) -> list[str]:
    return [f"linux-headers-{plan.architecture}"]


def useradd_command(plan: InstallPlan, target: str) -> list[str]:
    groups = ",".join(plan.user.groups)
    return [
        "chroot",
        target,
        "useradd",
        "-m",
        "-s",
        plan.user.shell,
        "-c",
        plan.user.full_name,
        "-G",
        groups,
        plan.user.username,
    ]
