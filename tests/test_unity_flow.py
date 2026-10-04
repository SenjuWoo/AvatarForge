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
            versions = {str(older): (4, 5, 0), str(modern): (5, 2, 2)}
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


class UnityFailureChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="AvatarForgeUnityFlow-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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

    def prepare(self, exit_code=2, verdict="blocked"):
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
                if verdict is not None:
                    core.write_json(self.folder / "unity-report.json", {"status": verdict, "issues": [{"severity": "error", "message": "generated rig error"}] if verdict == "blocked" else []})
                return subprocess.CompletedProcess(args, exit_code)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(core, "ROOT", self.root), patch.object(core, "discover_tool", return_value="generated-Unity.exe"), patch.object(core, "run_owned", side_effect=child):
            return core.prepare_unity(self.folder, self.project)

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

    def test_failed_report_is_visible_without_legacy_project_link(self):
        core.write_json(self.folder / "unity-report.json", {"status": "blocked", "issues": [{"message": "generated failure"}]})
        jobs = core.Jobs(self.folder.parent)
        self.addCleanup(jobs.close)
        self.assertEqual(jobs.get("completed")["unity_report"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
