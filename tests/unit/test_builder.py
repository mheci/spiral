from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.unit.support import ROOT, make_repo


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DebianDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("discover_debian", ROOT / "builder" / "scripts" / "discover-debian.py")

    def test_parse_inrelease(self):
        text = (
            "Origin: Debian\n"
            "Suite: stable\n"
            "Codename: trixie\n"
            "Version: 13.0\n"
            "Date: Thu, 13 Nov 2026 00:00:00 UTC\n"
        )
        fields = self.module.parse_inrelease(text)
        self.assertEqual(fields["Codename"], "trixie")
        self.assertEqual(fields["Version"], "13.0")

    def test_invalid_codename(self):
        with self.assertRaises(ValueError):
            self.module.parse_inrelease("Suite: stable\nCodename: ../../etc\nVersion: 1\n")

    def test_missing_fields(self):
        with self.assertRaises(ValueError):
            self.module.parse_inrelease("Suite: stable\n")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("render_config", ROOT / "builder" / "render_config.py")

    def test_grub_entries_and_default(self):
        repo = make_repo()
        bios = self.module.render_grub(repo, "bios")
        uefi = self.module.render_grub(repo, "uefi")
        self.assertIn("set default=0", bios)
        self.assertIn("Start Spiral", bios)
        self.assertIn("Safe Graphics", bios)
        self.assertIn("@KERNEL_LIVE@", bios)
        self.assertIn("@INITRD_LIVE@", bios)
        self.assertIn("MEMTEST", bios)
        self.assertNotIn("MEMTEST", uefi)
        self.assertIn("Troubleshooting", uefi)

    def test_boot_append(self):
        repo = make_repo()
        text = self.module.boot_append(repo)
        self.assertIn("boot=live components", text)
        self.assertIn("console=ttyS0,115200", text)
        self.assertIn(f"username={repo.build.live_username}", text)


class ManifestTests(unittest.TestCase):
    def test_dpkg_status_parser(self):
        module = load_module("package_manifest", ROOT / "builder" / "scripts" / "package_manifest.py")
        text = (
            "Package: apt\nStatus: install ok installed\nArchitecture: amd64\nVersion: 2.0\n\n"
            "Package: removed\nStatus: deinstall ok config-files\nArchitecture: amd64\nVersion: 1.0\n"
        )
        packages = module.parse_status(text)
        self.assertEqual(packages, {"apt": "2.0 (amd64)"})


class PackageCheckTests(unittest.TestCase):
    def test_expand_and_live_list(self):
        module = load_module("check_packages", ROOT / "builder" / "scripts" / "check-packages.py")
        self.assertEqual(module.expand(["linux-image-{arch}"], "amd64"), {"linux-image-amd64"})
        path = ROOT / "builder" / "live" / "config" / "package-lists" / "spiral.list.chroot"
        names = module.read_live_list(path)
        self.assertIn("debootstrap", names)
        self.assertIn("live-boot", names)


if __name__ == "__main__":
    unittest.main()
