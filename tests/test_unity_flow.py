"""Project preservation and verdict gates without a licensed editor in CI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import avatarforge.core as core
from avatarforge.app import Service


class ToolSelectionChecks(unittest.TestCase):
    def test_automatic_blender_requires_five_but_manual_45_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older, modern = root / "4.5/blender.exe", root / "5.2/blender.exe"
            for path in (older, modern):
                path.parent.mkdir()
                path.write_bytes(b"generated version fixture; never executed")
            environment = {k: v for k, v in os.environ.items() if k not in {"AVATARFORGE_BLENDER", "BLENDER_PATH"}}
            versions = {str(older.resolve()): (4, 5, 0), str(modern.resolve()): (5, 2, 2)}
            with patch.object(core, "ROOT", root), patch.dict(os.environ, environment, clear=True), \
                 patch.object(core, "_windows_process_paths", return_value=[str(older), str(modern)]), \
                 patch.object(core.shutil, "which", return_value=None), \
                 patch.object(core, "blender_version", side_effect=lambda p: versions.get(str(p))):
                self.assertEqual(core.discover_tool("blender"), str(modern.resolve()))
                self.assertEqual(core.discover_tool("blender", str(older)), str(older.resolve()))
                with patch.dict(os.environ, {"AVATARFORGE_BLENDER": str(older)}):
                    self.assertEqual(core.discover_tool("blender"), str(older.resolve()))
                with patch.object(core, "_windows_process_paths", return_value=[str(older)]):
                    self.assertIsNone(core.discover_tool("blender"))


class UnityEnvironmentChecks(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Package Manager environment")
    def test_missing_allusersprofile_is_recovered_without_changing_parent(self):
        with patch.dict(os.environ, {"PROGRAMDATA": "generated-programdata"}, clear=True):
            self.assertEqual(core.unity_environment()["ALLUSERSPROFILE"], "generated-programdata")
            self.assertNotIn("ALLUSERSPROFILE", os.environ)
            with patch.dict(os.environ, {"ALLUSERSPROFILE": "selected-profile"}):
                self.assertEqual(core.unity_environment()["ALLUSERSPROFILE"], "selected-profile")
        environment = {key: value for key, value in os.environ.items() if key not in {"ALLUSERSPROFILE", "PROGRAMDATA"}}
        with patch.dict(os.environ, environment, clear=True):
            self.assertTrue(Path(core.unity_environment()["ALLUSERSPROFILE"]).is_dir())
            self.assertNotIn("ALLUSERSPROFILE", os.environ)
            self.assertNotIn("PROGRAMDATA", os.environ)


class UnityFailureChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="AvatarForgeUnityFlow-")
        self.addCleanup(self.temporary.cleanup)
        # Windows TEMP can use an 8.3 alias (RUNNER~1); compare the canonical
        # destination recorded by the app, not an alternate spelling of it.
        self.root = Path(self.temporary.name).resolve()
        self.folder = self.root / "outputs" / "completed"
        self.folder.mkdir(parents=True)
        (self.folder / "model.fbx").write_bytes(b"generated input; never sent to an editor")
        core.write_json(self.folder / "report.json", {"schema_version": 1, "preset": "preserve"})
        core.write_json(self.folder / "receipt.json", {"id": "completed", "state": "complete", "output": str(self.folder), "source": "fixture.blend", "preset": "preserve"})
        vpm = self.root / ".runtime/vpm/vpm.exe"
        vpm.parent.mkdir(parents=True)
        vpm.write_bytes(b"generated marker; never executed")
        core.write_json(self.root / "unity/Packages/dev.senjuwoo.avatarforge/package.json", {"name": "dev.senjuwoo.avatarforge", "version": "fixture"})
        catalog = core.read_json(core.ROOT / "avatarforge/dependencies.json")
        core.write_json(self.root / "avatarforge/dependencies.json", catalog)
        self.sdk_version = catalog["dependencies"]["vrchat_sdk"]["version"]
        self.project = self.root / "new-unity"
        self.calls = []

    def prepare(self, exit_code=2, verdict="blocked", save_assets=True, timeout=False, approved_physics=None):
        def child(args, **kwargs):
            self.calls.append(args)
            if len(args) > 1 and args[1] == "new":
                (self.project / "Assets").mkdir(parents=True)
                (self.project / "ProjectSettings").mkdir()
                core.write_json(self.project / "Packages/manifest.json", {"dependencies": {}})
                core.write_json(self.project / "Packages/vpm-manifest.json", {"dependencies": {}, "locked": {}})
                for name in ("com.vrchat.base", "com.vrchat.avatars"):
                    core.write_json(self.project / "Packages" / name / "package.json", {"name": name, "version": self.sdk_version})
            if "-batchmode" in args:
                if timeout:
                    self.assertEqual(kwargs["timeout"], 1800)
                    raise subprocess.TimeoutExpired(args, kwargs["timeout"])
                if verdict is not None:
                    report = {"status": verdict, "issues": [{"severity": "error", "message": "generated rig error"}] if verdict == "blocked" else []}
                    if save_assets and verdict != "blocked":
                        for key, name in (("prefab", "Avatar.prefab"), ("scene", "Preview.unity")):
                            asset = "Assets/AvatarForge/Completed/" + name
                            path = self.project / asset
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text("generated saved asset marker; never opened", encoding="utf-8")
                            report[key] = asset
                    core.write_json(self.folder / "unity-report.json", report)
                return subprocess.CompletedProcess(args, exit_code)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(core, "ROOT", self.root), patch.object(core, "discover_tool", return_value="generated-Unity.exe"), patch.object(core, "run_owned", side_effect=child):
            return core.prepare_unity(self.folder, self.project, approved_physics)

    def test_other_client_cannot_prepare_or_change_approval_and_exit_releases_lock(self):
        core.write_json(self.folder / "unity-overrides.json", {"approved_physics": ["Breast.L"]})
        script = (
            "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from avatarforge.core import _unity_preparation_lock; "
            "lock=_unity_preparation_lock(Path(sys.argv[2])); lock.__enter__(); "
            "print('LOCKED',flush=True); sys.stdin.read()"
        )
        child = subprocess.Popen([sys.executable, "-c", script, str(Path(core.__file__).resolve().parents[1]), str(self.folder)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            with self.assertRaisesRegex(ValueError, "already running"):
                self.prepare(approved_physics=[])
            with self.assertRaisesRegex(ValueError, "already running"):
                core.approve_physics(self.folder, [])
            self.assertEqual(self.calls, [])
            self.assertEqual(core.read_json(self.folder / "unity-overrides.json"), {"approved_physics": ["Breast.L"]})
        finally:
            child.terminate()
            child.communicate(timeout=15)
        self.prepare(exit_code=0, verdict="needs_review", approved_physics=[])
        self.assertEqual(core.read_json(self.folder / "unity-overrides.json"), {"approved_physics": []})

    def test_approval_none_preserves_selection_and_empty_list_clears_it(self):
        core.write_json(self.folder / "report.json", {"schema_version": 1, "preset": "preserve", "physics": [{"bone": "Breast.L"}]})
        core.approve_physics(self.folder, ["Breast.L"])
        self.prepare(exit_code=0, verdict="needs_review")
        self.assertEqual(core.read_json(self.folder / "unity-overrides.json"), {"approved_physics": ["Breast.L"]})
        with self.assertRaisesRegex(ValueError, "only roots suggested"):
            core.approve_physics(self.folder, ["unknown"])
        self.assertEqual(core.read_json(self.folder / "unity-overrides.json"), {"approved_physics": ["Breast.L"]})
        core.approve_physics(self.folder, [])
        self.assertEqual(core.read_json(self.folder / "unity-overrides.json"), {"approved_physics": []})

    def test_blocked_import_keeps_project_report_and_plain_editor_open(self):
        with self.assertRaisesRegex(RuntimeError, "exit 2"):
            self.prepare()
        self.assertTrue(self.project.is_dir())
        link = core.read_json(self.folder / "unity-project.json")
        self.assertEqual(link, {"project": str(self.project), "import_state": "blocked"})
        service = Service(self.folder.parent)
        self.addCleanup(service.jobs.close)
        job = service.invoke("job", {"id": "completed"})
        self.assertEqual(job["unity_report"]["status"], "blocked")
        with patch.object(core, "discover_tool", return_value="generated-Unity.exe"), patch("avatarforge.app.subprocess.Popen") as editor:
            self.assertEqual(service.invoke("open", {"id": "completed", "kind": "unity"}), {"opened": "unity"})
            self.assertEqual(editor.call_args.args[0], ["generated-Unity.exe", "-projectPath", str(self.project)])
            self.assertEqual(editor.call_args.kwargs["env"], core.unity_environment())
        self.assertEqual({args[3] for args in self.calls if len(args) > 3 and args[1:3] == ["add", "package"]},
                         {"com.vrchat.base@" + self.sdk_version, "com.vrchat.avatars@" + self.sdk_version})

    def test_blocked_verdict_is_rejected_even_with_zero_editor_exit(self):
        with self.assertRaisesRegex(RuntimeError, "import is blocked"):
            self.prepare(exit_code=0)
        self.assertEqual(core.read_json(self.folder / "unity-project.json")["import_state"], "blocked")

    def test_previous_verdict_cannot_certify_new_invocation(self):
        previous = {"status": "ready", "issues": []}
        core.write_json(self.folder / "unity-report.json", previous)
        with self.assertRaisesRegex(RuntimeError, "new import verdict"):
            self.prepare(exit_code=0, verdict=None)
        self.assertEqual(core.read_json(self.folder / "unity-project.json")["import_state"], "failed")
        self.assertFalse((self.folder / "unity-report.json").exists())
        kept = list(self.folder.glob("unity-report.previous-*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(core.read_json(kept[0]), previous)

    def test_success_requires_current_verdict_and_records_complete_state(self):
        result = self.prepare(exit_code=0, verdict="needs_review")
        self.assertEqual(result["report"]["status"], "needs_review")
        self.assertEqual(core.read_json(self.folder / "unity-project.json")["import_state"], "complete")

    def test_ready_verdict_without_saved_assets_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "usable prefab and preview scene"):
            self.prepare(exit_code=0, verdict="ready", save_assets=False)
        self.assertTrue(self.project.is_dir())
        self.assertEqual(core.read_json(self.folder / "unity-project.json")["import_state"], "failed")
        self.assertEqual(core.read_json(self.folder / "unity-report.json")["status"], "ready")

    def test_import_timeout_keeps_project_and_records_recovery_state(self):
        with self.assertRaisesRegex(RuntimeError, "exceeded 30 minutes"):
            self.prepare(timeout=True)
        self.assertTrue((self.project / "ProjectSettings").is_dir())
        self.assertEqual(core.read_json(self.folder / "unity-project.json"),
                         {"project": str(self.project), "import_state": "timed_out"})
        self.assertFalse((self.folder / "unity-report.json").exists())

    def test_failed_report_is_visible_without_legacy_project_link(self):
        core.write_json(self.folder / "unity-report.json", {"status": "blocked", "issues": [{"message": "generated failure"}]})
        jobs = core.Jobs(self.folder.parent)
        self.addCleanup(jobs.close)
        self.assertEqual(jobs.get("completed")["unity_report"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
