from __future__ import annotations

import unittest
from pathlib import Path

from spiral_installer.config import load_repository
from spiral_installer.errors import ValidationError
from spiral_installer.models import Firmware
from spiral_installer.plan import InstallChoices, PlanBuilder

from .support import make_disk, make_release, make_repo
from .test_plan import FakeResolver, nvidia_gpu


class ExternalRepoTests(unittest.TestCase):
    def test_external_repos_load(self):
        repo = make_repo()
        self.assertIn("nvidia-cuda", repo.external_repos)
        self.assertIn("xanmod", repo.external_repos)
        self.assertIn("liquorix", repo.external_repos)
        self.assertIn("scx", repo.external_repos)
        nvidia = repo.external_repos["nvidia-cuda"]
        self.assertIn("cuda-keyring", nvidia.keyring_url_template or "")
        self.assertIn("developer.download.nvidia.com", nvidia.keyring_url_template)
        xanmod = repo.external_repos["xanmod"]
        self.assertIn("xanmod.org", xanmod.key_url)
        self.assertIn("signed-by", xanmod.sources_line_template)
        liquorix = repo.external_repos["liquorix"]
        self.assertIn("liquorix.net", liquorix.url)

    def test_kernel_extra_profiles_exist(self):
        repo = make_repo()
        for kid in ("xanmod", "xanmod-lts", "liquorix", "tkg"):
            self.assertIn(kid, repo.profiles.kernels, f"kernel {kid} missing")
        # Standard still default
        self.assertEqual(repo.installer.default_str("kernel"), "standard")
        # Extra kernels have external_repo
        self.assertEqual(repo.profiles.kernel("xanmod").external_repo, "xanmod")
        self.assertEqual(repo.profiles.kernel("liquorix").external_repo, "liquorix")
        self.assertEqual(repo.profiles.kernel("tkg").external_repo, "tkg")
        # TKG is experimental unsigned
        tkg = repo.profiles.kernel("tkg")
        self.assertEqual(tkg.status, "experimental")
        self.assertEqual(tkg.secure_boot, "unsigned")

    def test_driver_cuda_profiles_exist(self):
        repo = make_repo()
        self.assertIn("nvidia-open-cuda", repo.profiles.drivers)
        self.assertIn("nvidia-cuda", repo.profiles.drivers)
        open_cuda = repo.profiles.driver("nvidia-open-cuda")
        self.assertEqual(open_cuda.external_repo, "nvidia-cuda")
        self.assertEqual(open_cuda.secure_boot, "requires_enrollment")
        self.assertIn("nvidia-open", open_cuda.packages)

    def test_package_groups_cuda_scx_exist(self):
        repo = make_repo()
        self.assertIn("cuda", repo.profiles.packages)
        self.assertIn("scx", repo.profiles.packages)
        scx = repo.profiles.package_group("scx")
        self.assertIn("scx", scx.packages)
        self.assertIn("scx-loader", scx.packages)

    def test_scx_defaults(self):
        repo = make_repo()
        self.assertTrue(repo.installer.defaults.get("scx") is True)
        self.assertEqual(repo.installer.defaults.get("scx_scheduler"), "lavd")

    def test_plan_auto_nvidia_prefers_open_cuda(self):
        gpu = nvidia_gpu()
        report, _ = make_disk(gpus=(gpu,), secure_boot=False)
        plan = PlanBuilder(make_repo(), report, make_release(), resolver=FakeResolver()).build(
            InstallChoices(disk=report.disks[0].stable_path)
        )
        # Per user request, auto should prefer open when available
        self.assertEqual(plan.software.driver_id, "nvidia-open-cuda")
        self.assertIn("nvidia-cuda", plan.software.external_repos)

    def test_plan_explicit_xanmod_kernel_adds_external_repo(self):
        report, _ = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release(), resolver=FakeResolver()).build(
            InstallChoices(disk=report.disks[0].stable_path, kernel="xanmod")
        )
        self.assertEqual(plan.software.kernel_id, "xanmod")
        self.assertIn("xanmod", plan.software.external_repos)
        self.assertTrue(plan.software.scx_enabled)
        self.assertEqual(plan.software.scx_scheduler, "lavd")

    def test_plan_offline_with_external_fails(self):
        report, _ = make_disk()
        # Offline build is disabled in test ISO; any offline should raise
        with self.assertRaises(ValidationError) as cm:
            PlanBuilder(make_repo(), report, make_release(), resolver=FakeResolver()).build(
                InstallChoices(disk=report.disks[0].stable_path, kernel="xanmod", offline=True)
            )
        self.assertIn("offline", str(cm.exception).lower())

    def test_plan_scx_disabled_removes_scx_group(self):
        report, _ = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release(), resolver=FakeResolver()).build(
            InstallChoices(disk=report.disks[0].stable_path, scx=False)
        )
        self.assertFalse(plan.software.scx_enabled)
        self.assertNotIn("scx", plan.software.package_groups)
        self.assertNotIn("scx", plan.software.packages)

    def test_plan_scx_standard_kernel_warns(self):
        report, _ = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release(), resolver=FakeResolver()).build(
            InstallChoices(disk=report.disks[0].stable_path)
        )
        self.assertTrue(any(w.code == "scx-standard-kernel" for w in plan.warnings))
        self.assertTrue(any(w.code == "scx-enabled" for w in plan.warnings))

    def test_plan_summary_includes_scx_and_external(self):
        report, _ = make_disk()
        plan = PlanBuilder(make_repo(), report, make_release(), resolver=FakeResolver()).build(
            InstallChoices(disk=report.disks[0].stable_path, kernel="liquorix")
        )
        summary = plan.summary()
        self.assertIn("external_repos", summary)
        self.assertIn("scx", summary)
        self.assertEqual(summary["scx"]["scheduler"], "lavd")

    def test_engine_has_external_and_scx_methods(self):
        from spiral_installer.engine import Installer

        self.assertTrue(hasattr(Installer, "_setup_external_repos"))
        self.assertTrue(hasattr(Installer, "_configure_scx"))
        self.assertTrue(hasattr(Installer, "_write_tkg_helper"))


if __name__ == "__main__":
    unittest.main()
