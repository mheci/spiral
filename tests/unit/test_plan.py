from __future__ import annotations

import json
import unittest
from dataclasses import replace

from spiral_installer.errors import ValidationError
from spiral_installer.models import (
    Disk,
    Firmware,
    Gpu,
    GpuVendor,
    Partition,
    Severity,
)
from spiral_installer.plan import (
    Approval,
    InstallChoices,
    PlanBuilder,
    confirmation_phrase,
    validate_approval,
)

from .support import make_disk, make_release, make_repo


class FakeResolver:
    def __init__(self, missing=()):
        self.missing_packages = set(missing)

    def missing(self, packages):
        return sorted(set(packages) & self.missing_packages)


def nvidia_gpu() -> Gpu:
    return Gpu(
        address="0000:01:00.0",
        vendor_id="10de",
        device_id="2484",
        vendor=GpuVendor.NVIDIA,
        model="NVIDIA GA104 [GeForce RTX 3070]",
    )


class PlanTests(unittest.TestCase):
    def build(self, choices=None, hardware=None, resolver=None, nvidia_package=None, nvidia_supported=None):
        report, disk = hardware or make_disk()
        choices = choices or InstallChoices(disk=disk.stable_path)
        if isinstance(choices, InstallChoices) and choices.disk is None:
            choices = replace(choices, disk=disk.stable_path)
        return PlanBuilder(
            make_repo(),
            report,
            make_release(),
            resolver=resolver,
            nvidia_package=nvidia_package,
            nvidia_supported=nvidia_supported,
        ).build(choices)

    def test_default_uefi_btrfs_plan(self):
        plan = self.build()
        self.assertEqual(len(plan.partitions), 2)
        self.assertEqual([p.role.value for p in plan.partitions], ["esp", "root"])
        self.assertEqual(plan.filesystem_kind.value, "btrfs")
        self.assertEqual(plan.compression.mount_option, "compress=zstd:3")
        names = [s.name for s in plan.subvolumes]
        self.assertEqual(names[:3], ["@", "@home", "@var"])
        self.assertIn("@var_log", names)
        self.assertIn("@cache", names)
        self.assertIn("@tmp", names)
        self.assertNotIn("@snapshots", names)
        self.assertNotIn("@swap", names)
        self.assertEqual(plan.software.desktop_ids, ("gnome",))
        self.assertIn("task-gnome-desktop", plan.software.packages)
        self.assertIn("gdm3", plan.software.packages)
        self.assertIn("linux-image-amd64", plan.software.packages)
        self.assertIn("grub-efi-amd64", plan.software.packages)
        self.assertIn("firmware-linux-free", plan.software.packages)
        self.assertEqual(plan.software.driver_id, "generic")
        self.assertTrue(plan.requires_ack)
        self.assertTrue(any(w.code == "partitioning-destructive" for w in plan.warnings))
        json.dumps(plan.summary())

    def test_ext4_plan(self):
        plan = self.build(InstallChoices(filesystem="ext4"))
        self.assertEqual(plan.filesystem_kind.value, "ext4")
        self.assertEqual(plan.subvolumes, ())
        self.assertEqual(plan.compression.algorithm, "none")
        self.assertIn("e2fsprogs", plan.software.packages)

    def test_encrypted_plan_has_separate_boot_and_no_swap_partition(self):
        plan = self.build(InstallChoices(encryption=True, swap="zram"))
        self.assertTrue(plan.encryption.enabled)
        self.assertTrue(plan.boot.separate_boot)
        self.assertEqual([p.role.value for p in plan.partitions], ["esp", "boot", "root"])
        self.assertEqual(plan.root_partition.fs, "luks")
        self.assertEqual(plan.root_device, "/dev/mapper/cryptroot")
        self.assertIn("cryptsetup", plan.software.packages)
        self.assertIn("cryptsetup-initramfs", plan.software.packages)
        self.assertIn("encryption-no-recovery", [w.code for w in plan.warnings])

    def test_swapfile_creates_swap_subvolume(self):
        plan = self.build(InstallChoices(swap="file"))
        swap = next(s for s in plan.subvolumes if s.name == "@swap")
        self.assertEqual(swap.mountpoint, "/swap")
        self.assertIn("compress=no", swap.options)
        self.assertEqual(plan.swap.strategy.value, "file")
        self.assertEqual(plan.swap.size_mib, 4096)

    def test_snapshots_add_subvolume_and_package(self):
        plan = self.build(InstallChoices(snapshots=True))
        self.assertIn("snapper", plan.software.packages)
        self.assertTrue(any(s.mountpoint == "/.snapshots" for s in plan.subvolumes))
        self.assertTrue(any(w.code == "snapshots-are-not-backups" for w in plan.warnings))

    def test_bios_gpt_plan(self):
        report, _ = make_disk(firmware=Firmware.BIOS)
        plan = self.build(InstallChoices(), hardware=(report, report.disks[0]))
        self.assertEqual([p.role.value for p in plan.partitions], ["bios_grub", "root"])
        self.assertEqual(plan.firmware, Firmware.BIOS)
        self.assertIn("grub-pc", plan.software.packages)

    def test_bios_mbr_plan(self):
        report, _ = make_disk(firmware=Firmware.BIOS)
        plan = self.build(
            InstallChoices(partition_table="msdos"),
            hardware=(report, report.disks[0]),
        )
        self.assertEqual(plan.partition_table.value, "msdos")
        self.assertEqual([p.role.value for p in plan.partitions], ["root"])

    def test_secure_boot_auto_selects_nouveau(self):
        gpu = nvidia_gpu()
        report, _ = make_disk(gpus=(gpu,), secure_boot=True)
        plan = self.build(InstallChoices(), hardware=(report, report.disks[0]))
        self.assertEqual(plan.software.driver_id, "nouveau")
        self.assertTrue(any(w.code == "secure-boot-signed-driver" for w in plan.warnings))

    def test_explicit_proprietary_requires_ack_under_secure_boot(self):
        gpu = nvidia_gpu()
        report, _ = make_disk(gpus=(gpu,), secure_boot=True)
        plan = self.build(
            InstallChoices(driver="nvidia-proprietary"),
            hardware=(report, report.disks[0]),
        )
        self.assertEqual(plan.software.driver_id, "nvidia-proprietary")
        warning = next(w for w in plan.warnings if w.code == "secure-boot-driver")
        self.assertEqual(warning.severity, Severity.CRITICAL)

    def test_missing_nvidia_driver_falls_back(self):
        gpu = nvidia_gpu()
        report, _ = make_disk(gpus=(gpu,), secure_boot=False)
        plan = self.build(
            InstallChoices(),
            hardware=(report, report.disks[0]),
            resolver=FakeResolver({"nvidia-driver"}),
        )
        self.assertEqual(plan.software.driver_id, "nouveau")
        self.assertTrue(any(w.code == "driver-fallback" for w in plan.warnings))

    def test_missing_desktop_package_is_fatal(self):
        with self.assertRaises(ValidationError):
            self.build(resolver=FakeResolver({"task-gnome-desktop"}))

    def test_optional_driver_package_is_dropped(self):
        gpu = nvidia_gpu()
        report, _ = make_disk(gpus=(gpu,), secure_boot=False)
        plan = self.build(
            InstallChoices(driver="nvidia-proprietary"),
            hardware=(report, report.disks[0]),
            resolver=FakeResolver({"nvidia-settings"}),
        )
        self.assertNotIn("nvidia-settings", plan.software.packages)
        self.assertTrue(any(w.code == "optional-packages-skipped" for w in plan.warnings))

    def test_nvidia_detect_package_override(self):
        gpu = nvidia_gpu()
        report, _ = make_disk(gpus=(gpu,), secure_boot=False)
        plan = self.build(
            InstallChoices(),
            hardware=(report, report.disks[0]),
            nvidia_package="nvidia-legacy-390xx-driver",
            nvidia_supported=True,
        )
        self.assertEqual(plan.software.driver_id, "nvidia-proprietary")
        self.assertIn("nvidia-legacy-390xx-driver", plan.software.packages)
        self.assertNotIn("nvidia-driver", plan.software.packages)

    def test_invalid_combinations_are_rejected(self):
        cases = [
            (InstallChoices(filesystem="ext4", snapshots=True), None),
            (InstallChoices(encryption=True, swap="partition"), None),
            (InstallChoices(partition_table="msdos"), None),
            (InstallChoices(swap="file", filesystem="ext4"), None),
            (InstallChoices(username="root"), None),
            (InstallChoices(username="Bad Name"), None),
            (InstallChoices(hostname="bad_host_name!"), None),
            (InstallChoices(offline=True), None),
            (InstallChoices(mode="manual"), None),
        ]
        for choices, _ in cases:
            with self.subTest(choices=choices):
                with self.assertRaises(ValidationError):
                    self.build(choices)

    def test_mounted_disk_is_rejected(self):
        report, disk = make_disk()
        mounted = replace(
            disk,
            partitions=(
                Partition(path="/dev/sda1", number=1, size_bytes=1024, fstype="ext4", mountpoints=("/",)),
            ),
        )
        report = replace(report, disks=(mounted,))
        with self.assertRaises(ValidationError):
            self.build(hardware=(report, mounted))

    def test_removable_disk_is_rejected(self):
        report, disk = make_disk()
        removable = replace(disk, removable=True)
        report = replace(report, disks=(removable,))
        with self.assertRaises(ValidationError):
            self.build(hardware=(report, removable))

    def test_small_disk_is_rejected(self):
        report, disk = make_disk(size_gib=10)
        with self.assertRaises(ValidationError):
            self.build(hardware=(report, disk))

    def test_approval_requires_phrase_and_acknowledgements(self):
        plan = self.build()
        critical = frozenset(w.code for w in plan.warnings if w.requires_ack)
        with self.assertRaises(ValidationError):
            validate_approval(plan, None)
        with self.assertRaises(ValidationError):
            validate_approval(plan, Approval(phrase="yes"))
        validate_approval(plan, Approval(phrase=confirmation_phrase(plan), acknowledged=critical))
        with self.assertRaises(ValidationError):
            validate_approval(plan, Approval(phrase=confirmation_phrase(plan), acknowledged=frozenset()))

    def test_unknown_disk_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.build(InstallChoices(disk="/dev/nope"))


if __name__ == "__main__":
    unittest.main()
