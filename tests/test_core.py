"""Runnable without third-party test packages: python -m unittest discover -s tests."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from avatarforge.core import ROOT, scan, extract_zip, Jobs, sha256, read_json, write_json
from avatarforge.app import compact_job


class LocalEngineChecks(unittest.TestCase):
    def test_scan_selects_models_not_textures_or_source_rig_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "character.BLEND").write_bytes(b"fixture")
            (source / "rig.py").write_text("raise RuntimeError('must not execute')")
            (source / "body.png").write_bytes(b"texture")
            result = scan(source)
            self.assertEqual([m["name"] for m in result["models"]], ["character.BLEND"])
            self.assertFalse(result["choose_model"])

    def test_zip_extract_and_scan_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "model.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("モデル/my character.fbx", b"fixture")
                output.writestr("textures/body.png", b"fixture")
            result = extract_zip(archive, root / "unpacked")
            self.assertEqual(len(result["models"]), 1)
            self.assertTrue(Path(result["models"][0]["path"]).is_file())

    def test_archive_rejects_traversal_windows_drives_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, name in enumerate(["../outside.fbx", "..\\outside.fbx", "C:/outside.fbx", "/outside.fbx"]):
                archive = root / f"bad-{index}.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(name, b"fixture")
                with self.assertRaises(ValueError):
                    extract_zip(archive, root / f"extract-{index}")
            archive = root / "link.zip"
            info = zipfile.ZipInfo("link.fbx")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(info, "outside")
            with self.assertRaises(ValueError):
                extract_zip(archive, root / "extract-link")
            self.assertFalse((root / "outside.fbx").exists())

    def test_archive_limits_before_payload_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "large.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("character.fbx", b"123456")
            with self.assertRaises(ValueError):
                extract_zip(archive, root / "extract", max_bytes=3)
            self.assertFalse((root / "extract" / "character.fbx").exists())

    def test_archive_rejects_case_collisions_and_windows_devices(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, names in enumerate((("body.fbx", "BODY.fbx"), ("CON.fbx",), ("body.fbx.",))):
                archive = root / f"bad-{index}.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    for name in names:
                        output.writestr(name, b"fixture")
                with self.assertRaises(ValueError):
                    extract_zip(archive, root / f"extract-{index}")

    def test_shutdown_terminates_only_the_owned_worker_and_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.blend"
            source.write_bytes(b"fixture")
            popen = subprocess.Popen
            children = []
            def worker(*args, **kwargs):
                child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
                children.append(child)
                return child
            jobs = Jobs(root / "outputs")
            with patch("avatarforge.core.discover_tool", return_value=sys.executable), patch("avatarforge.core.subprocess.Popen", side_effect=worker):
                job = jobs.start(source)
                deadline = time.monotonic() + 5
                while not children and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(children)
                jobs.close()
            self.assertIsNotNone(children[0].poll())
            self.assertEqual(jobs.get(job["id"])["state"], "cancelled")
            self.assertTrue((Path(job["output"]) / "receipt.json").is_file())
            self.assertEqual(source.read_bytes(), b"fixture")

    def test_receipt_roundtrip_and_sha256(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"
            write_json(path, {"source": "モデル", "status": "needs_review"})
            self.assertEqual(read_json(path)["source"], "モデル")
            first = sha256(path)
            write_json(path, {"status": "blocked"})
            self.assertNotEqual(first, sha256(path))

    def test_unknown_preset_never_starts_process(self):
        with self.assertRaises(ValueError):
            Jobs().start("does-not-exist.blend", "delete-bones")

    def test_preserve_cannot_silently_reduce_geometry_from_custom_target(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "fixture.blend"
            source.write_bytes(b"fixture")
            with patch("avatarforge.core.discover_tool", return_value=sys.executable), patch("avatarforge.core.subprocess.Popen") as process:
                with self.assertRaisesRegex(ValueError, "Preserve keeps"):
                    Jobs(Path(temp) / "outputs").start(source, "preserve", {"target_triangles": 1})
                process.assert_not_called()

    def test_completed_and_interrupted_jobs_survive_app_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for job_id, state in (("completed", "complete"), ("pending", "converting")):
                output = root / job_id
                write_json(output / ("receipt.json" if state == "complete" else "job.json"), {"id": job_id, "output": str(output), "state": state, "source": "fixture.blend", "preset": "preserve"})
            jobs = Jobs(root)
            self.assertEqual(jobs.get("completed")["state"], "complete")
            self.assertEqual(jobs.get("pending")["state"], "interrupted")
            self.assertIn("previous app session", jobs.get("pending")["error"])

    def test_job_receipt_cannot_redirect_output_outside_owned_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "bad" / "receipt.json", {"id": "bad", "output": str(root.parent), "state": "complete"})
            self.assertEqual(Jobs(root).list(), [])

    def test_saved_physics_choices_survive_job_reload_and_app_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "completed"
            write_json(folder / "receipt.json", {"id": "completed", "output": str(folder), "state": "complete"})
            write_json(folder / "unity-overrides.json", {"approved_physics": ["Breast.L", "Butt.L"]})
            self.assertEqual(Jobs(root).get("completed")["approved_physics"], ["Breast.L", "Butt.L"])
            write_json(folder / "unity-overrides.json", {"approved_physics": []})
            self.assertEqual(Jobs(root).get("completed")["approved_physics"], [])

    def test_mcp_handshake_and_tool_errors_use_jsonrpc(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "avatarforge_scan", "arguments": {"source": str(ROOT / "not-a-model")}}},
        ]
        process = subprocess.run([sys.executable, str(ROOT / "run.py"), "mcp"], input="\n".join(json.dumps(r) for r in requests) + "\n", capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "AvatarForge")
        self.assertEqual(len(replies[1]["result"]["tools"]), 7)
        self.assertTrue(replies[2]["result"]["isError"])

    def test_mcp_malformed_message_does_not_crash_server(self):
        process = subprocess.run([sys.executable, str(ROOT / "run.py"), "mcp"], input='[]\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n', capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32600)
        self.assertEqual(replies[1]["id"], 9)

    def test_mcp_job_default_avoids_repeating_large_rig_manifests(self):
        report = {"status": "needs_review", "summary": {"bones": 1500}, "required_bones": [f"Bone{i}" for i in range(1500)], "issues": [{"message": "review"}] * 30, "integrity": {"missing_bones": [], "missing_shape_keys": {}, "missing_weighted_bones": []}}
        data = compact_job({"id": "fixture", "state": "complete", "output": str(ROOT / "not-created"), "report": report, "log": "x" * 10000})
        self.assertNotIn("log", data)
        self.assertNotIn("required_bones", data["report"])
        self.assertEqual(data["report"]["issue_count"], 30)
        self.assertLess(len(json.dumps(data)), 2000)


if __name__ == "__main__":
    unittest.main()
