from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from spiral_installer.config import load_repository
from spiral_installer.errors import ConfigError
from spiral_installer.models import Firmware, GpuVendor

from .support import make_disk, make_release, make_repo


class ConfigTests(unittest.TestCase):
    def test_repository_loads(self):
        repo = make_repo()
        self.assertEqual(repo.debian.architecture, "amd64")
        self.assertIn("btrfs", repo.profiles.filesystems)
        self.assertIn("gnome", repo.profiles.desktops)
        self.assertIn("standard", repo.profiles.kernels)
        self.assertIn("firmware", repo.profiles.packages)
        self.assertTrue(repo.build.offline_enabled is False)
        self.assertGreater(len(repo.profiles.packages), 5)

    def test_default_kernel_supports_architecture(self):
        repo = make_repo()
        kernel = repo.profiles.kernels[repo.installer.default_str("kernel")]
        self.assertIn(repo.debian.architecture, kernel.arches)

    def test_unknown_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(Path(__file__).resolve().parents[2] / "config", root / "config")
            shutil.copytree(Path(__file__).resolve().parents[2] / "profiles", root / "profiles")
            path = root / "config" / "installer.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["mystery"] = True
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_repository(root)

    def test_missing_default_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(Path(__file__).resolve().parents[2] / "config", root / "config")
            shutil.copytree(Path(__file__).resolve().parents[2] / "profiles", root / "profiles")
            path = root / "config" / "installer.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["defaults"]["filesystem"] = "zfs"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_repository(root)

    def test_bad_compression_level_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(Path(__file__).resolve().parents[2] / "config", root / "config")
            shutil.copytree(Path(__file__).resolve().parents[2] / "profiles", root / "profiles")
            path = root / "config" / "installer.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["compression"]["maximum"]["level"] = 99
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_repository(root)


class HardwareParsingTests(unittest.TestCase):
    def test_lsblk_parses_disk_and_children(self):
        import json

        from spiral_installer.hardware import parse_lsblk_json

        payload = {
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "size": 1000204886016,
                    "type": "disk",
                    "model": "Test Disk",
                    "serial": "SERIAL1",
                    "tran": "sata",
                    "rm": False,
                    "ro": False,
                    "children": [
                        {
                            "name": "sda1",
                            "path": "/dev/sda1",
                            "size": 536870912,
                            "type": "part",
                            "fstype": "vfat",
                            "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                            "mountpoints": ["/boot/efi"],
                        },
                        {
                            "name": "sda2",
                            "path": "/dev/sda2",
                            "size": 999000000000,
                            "type": "part",
                            "fstype": "crypto_LUKS",
                            "mountpoints": [],
                            "children": [
                                {
                                    "name": "cryptroot",
                                    "path": "/dev/mapper/cryptroot",
                                    "size": 998000000000,
                                    "type": "crypt",
                                    "fstype": "btrfs",
                                    "mountpoints": ["/"],
                                }
                            ],
                        },
                    ],
                }
            ]
        }
        disks = parse_lsblk_json(json.dumps(payload))
        self.assertEqual(len(disks), 1)
        disk = disks[0]
        self.assertEqual(disk.path, "/dev/sda")
        self.assertEqual(disk.size_bytes, 1000204886016)
        self.assertEqual(len(disk.partitions), 2)
        self.assertTrue(disk.partitions[1].mounted)
        self.assertTrue(disk.is_busy)

    def test_lspci_parses_display_and_usb(self):
        from spiral_installer.hardware import parse_lspci_devices

        text = (
            '0000:01:00.0 "VGA compatible controller [0300]" "NVIDIA Corporation [10de]" '
            '"GA104 [GeForce RTX 3070] [2484]" -ra1 "Test [0000]" "Test [0000]"\n'
            '0000:00:02.0 "VGA compatible controller [0300]" "Intel Corporation [8086]" '
            '"Alder Lake-P GT2 [Iris Xe Graphics] [46a6]" -r0c "Test [0000]" "Test [0000]"\n'
            '0000:00:14.0 "USB controller [0c03]" "Intel Corporation [8086]" '
            '"Alder Lake PCH USB 3.2 xHCI Host Controller [51ed]" -r01 "Test [0000]" "Test [0000]"\n'
        )
        gpus, usb = parse_lspci_devices(text)
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0].vendor, GpuVendor.NVIDIA)
        self.assertEqual(gpus[0].device_id, "2484")
        self.assertIn("RTX 3070", gpus[0].model)
        self.assertEqual(gpus[1].vendor, GpuVendor.INTEL)
        self.assertEqual(len(usb), 1)

    def test_lscpu_and_meminfo(self):
        from spiral_installer.hardware import parse_lscpu_json, parse_meminfo

        lscpu = {
            "lscpu": [
                {"field": "Architecture:", "data": "x86_64"},
                {"field": "CPU(s):", "data": "16"},
                {"field": "Model name:", "data": "Test CPU"},
            ]
        }
        self.assertEqual(parse_lscpu_json(json.dumps(lscpu)), ("x86_64", "Test CPU", 16))
        self.assertEqual(parse_meminfo("MemTotal:       16384000 kB\n"), 16000)

    def test_secure_boot_parsers(self):
        from spiral_installer.hardware import parse_secure_boot_efivar, parse_secure_boot_text

        self.assertTrue(parse_secure_boot_efivar(b"\x06\x00\x00\x00\x01"))
        self.assertFalse(parse_secure_boot_efivar(b"\x06\x00\x00\x00\x00"))
        self.assertIsNone(parse_secure_boot_efivar(b"x"))
        self.assertTrue(parse_secure_boot_text("SecureBoot enabled"))
        self.assertFalse(parse_secure_boot_text("SecureBoot disabled"))
        self.assertIsNone(parse_secure_boot_text("unknown"))

    def test_nvidia_detect_output(self):
        from spiral_installer.hardware import parse_nvidia_detect_output

        self.assertEqual(
            parse_nvidia_detect_output("Your card is supported by the default drivers.\nInstall nvidia-driver."),
            "nvidia-driver",
        )
        self.assertEqual(
            parse_nvidia_detect_output("Install nvidia-legacy-390xx-driver."),
            "nvidia-legacy-390xx-driver",
        )
        self.assertIsNone(parse_nvidia_detect_output("Your card is not supported by any driver."))


class PackageParsingTests(unittest.TestCase):
    def test_apt_policy(self):
        from spiral_installer.packages import missing_packages, parse_apt_policy

        text = (
            "linux-image-amd64:\n  Installed: (none)\n  Candidate: 6.1.1-1\n  Version table:\n"
            "missing-package:\n  Installed: (none)\n  Candidate: (none)\n  Version table:\n"
        )
        policy = parse_apt_policy(text)
        self.assertEqual(policy["linux-image-amd64"], "6.1.1-1")
        self.assertIsNone(policy["missing-package"])
        self.assertEqual(
            missing_packages(["linux-image-amd64", "missing-package", "not-listed"], policy),
            ["missing-package", "not-listed"],
        )

    def test_packages_index(self):
        from spiral_installer.packages import parse_packages_index

        text = "Package: apt\nVersion: 2.0\n\nPackage: dpkg\nVersion: 1.0\n"
        index = parse_packages_index(text)
        self.assertEqual(index["apt"], "2.0")
        self.assertEqual(index["dpkg"], "1.0")


class CommandTests(unittest.TestCase):
    def test_gpt_partition_commands_keep_root_as_rest(self):
        from spiral_installer import commands
        from spiral_installer.plan import InstallChoices, PlanBuilder

        report, disk = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release()).build(InstallChoices(disk=disk.stable_path))
        rendered = commands.partition_commands(plan)
        self.assertEqual(rendered[0][0], "wipefs")
        self.assertTrue(any("--new=1:0:+512M" in " ".join(argv) for argv in rendered))
        root_command = next(argv for argv in rendered if "--typecode=2:8300" in argv)
        self.assertIn("--new=2:0:0", root_command)

    def test_fstab_uses_uuid_and_subvolumes(self):
        from spiral_installer import commands
        from spiral_installer.plan import InstallChoices, PlanBuilder

        report, disk = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release()).build(InstallChoices(disk=disk.stable_path))
        text = commands.render_fstab(plan, root_uuid="ROOT-UUID", esp_uuid="ESP-UUID")
        self.assertIn("UUID=ROOT-UUID", text)
        self.assertIn("subvol=@", text)
        self.assertIn("/boot/efi", text)
        self.assertIn("UUID=ESP-UUID", text)
        self.assertNotIn("secret", text.lower())

    def test_crypttab(self):
        from spiral_installer import commands

        self.assertEqual(
            commands.render_crypttab("LUKS-UUID", "cryptroot"),
            "cryptroot UUID=LUKS-UUID none luks\n",
        )

    def test_sources_list(self):
        from spiral_installer import commands
        from spiral_installer.plan import InstallChoices, PlanBuilder

        repo = make_repo()
        report, disk = make_disk()
        plan = PlanBuilder(repo, report, make_release()).build(InstallChoices(disk=disk.stable_path))
        text = commands.render_sources_list(repo.debian, plan)
        self.assertIn("trixie", text)
        self.assertIn("trixie-security", text)
        self.assertIn("non-free-firmware", text)

    def test_debootstrap_command(self):
        from spiral_installer import commands
        from spiral_installer.plan import InstallChoices, PlanBuilder

        repo = make_repo()
        report, disk = make_disk()
        plan = PlanBuilder(repo, report, make_release()).build(InstallChoices(disk=disk.stable_path))
        argv = commands.debootstrap_command(plan, repo.debian, "/target")
        self.assertIn("--arch=amd64", argv)
        self.assertIn("trixie", argv)
        self.assertIn("/target", argv)

    def test_grub_commands(self):
        from spiral_installer import commands
        from spiral_installer.plan import InstallChoices, PlanBuilder

        report, disk = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release()).build(InstallChoices(disk=disk.stable_path))
        argv = commands.grub_install_command(plan, "/target")
        self.assertIn("--target=x86_64-efi", argv)
        self.assertIn("--efi-directory=/boot/efi", argv)
        removable = commands.grub_removable_command(plan, "/target")
        self.assertIn("--removable", removable)
        self.assertIn("--no-nvram", removable)

    def test_secure_boot_grub_flag(self):
        from spiral_installer import commands
        from spiral_installer.plan import InstallChoices, PlanBuilder

        report, disk = make_disk(secure_boot=True)
        plan = PlanBuilder(make_repo(), report, make_release()).build(InstallChoices(disk=disk.stable_path))
        argv = commands.grub_install_command(plan, "/target")
        self.assertIn("--uefi-secure-boot", argv)


class ReleaseTests(unittest.TestCase):
    def test_missing_release_is_an_error(self):
        from spiral_installer.errors import SpiralError
        from spiral_installer.release import load_release

        with self.assertRaises(SpiralError):
            load_release("/nonexistent/release.json")

    def test_codename_override(self):
        from spiral_installer.release import load_release

        release = load_release("/nonexistent/release.json", "trixie")
        self.assertEqual(release.codename, "trixie")

    def test_secret_repr_is_redacted(self):
        from spiral_installer.util import Secret

        secret = Secret("hunter2hunter2")
        self.assertEqual(repr(secret), "Secret(***)")
        self.assertEqual(str(secret), "***")
        self.assertNotIn("hunter2", repr(secret))


if __name__ == "__main__":
    unittest.main()
