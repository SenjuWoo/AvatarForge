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
from avatarforge.bone_aliases import map_humanoid


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

    def test_snapshot_write_failure_stops_owned_worker_and_keeps_failed_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.blend"
            source.write_bytes(b"fixture")
            popen = subprocess.Popen
            children = []
            injected = False
            jobs = Jobs(root / "outputs")
            def worker(*args, **kwargs):
                child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
                children.append(child)
                return child
            def persist(path, value):
                nonlocal injected
                if not injected and Path(path).name == "job.json" and value.get("pid"):
                    injected = True
                    raise PermissionError("Injected job snapshot replacement failure")
                return write_json(path, value)
            try:
                with patch("avatarforge.core.discover_tool", return_value=sys.executable), \
                     patch("avatarforge.core.subprocess.Popen", side_effect=worker), \
                     patch("avatarforge.core.write_json", side_effect=persist):
                    job = jobs.start(source)
                    for thread in jobs.threads:
                        thread.join(timeout=5)
                        self.assertFalse(thread.is_alive())
                    self.assertTrue(injected)
                    self.assertTrue(children)
                    self.assertEqual(jobs.get(job["id"])["state"], "failed")
                    self.assertIn("snapshot replacement failure", jobs.get(job["id"])["error"])
                    self.assertIsNotNone(children[0].poll())
                    receipt = read_json(Path(job["output"]) / "receipt.json")
                    self.assertEqual(receipt["state"], "failed")
                    jobs.close()
                    self.assertIsNotNone(children[0].poll())
                    self.assertEqual(source.read_bytes(), b"fixture")
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
                jobs.close()

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

    def test_malformed_history_does_not_disable_healthy_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name, contents in (("array", "[]"), ("null", "null"), ("invalid", "{"),
                                   ("bad-output", json.dumps({"id": "bad-output", "output": None}))):
                folder = root / name
                folder.mkdir()
                (folder / "receipt.json").write_text(contents, encoding="utf-8")
            healthy = root / "healthy"
            write_json(healthy / "receipt.json", {"id": "healthy", "output": str(healthy),
                                                   "state": "complete", "source": "fixture.blend", "preset": "preserve"})
            jobs = Jobs(root)
            self.addCleanup(jobs.close)
            self.assertEqual([job["id"] for job in jobs.list()], ["healthy"])

    def test_malformed_optional_metadata_keeps_failed_conversion_reviewable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            folder = root / "failed"
            write_json(folder / "receipt.json", {"id": "failed", "output": str(folder),
                                                  "state": "failed", "source": "fixture.blend", "preset": "preserve"})
            report = {"status": "blocked", "issues": [{"severity": "error", "message": "fixture failure"}]}
            write_json(folder / "report.json", report)
            for name in ("unity-overrides.json", "unity-project.json", "unity-report.json"):
                (folder / name).write_text("[]", encoding="utf-8")
            jobs = Jobs(root)
            self.addCleanup(jobs.close)
            job = jobs.get("failed")
            self.assertEqual(job["report"], report)
            self.assertNotIn("approved_physics", job)
            self.assertNotIn("unity_project", job)
            self.assertNotIn("unity_report", job)
            self.assertEqual(jobs.list()[0]["state"], "failed")

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


class HumanoidAliasChecks(unittest.TestCase):
    def names(self, bones, preferred=None):
        mapped, missing, ambiguous = map_humanoid(bones, preferred=preferred)
        return {item["humanName"]: item["boneName"] for item in mapped}, missing, ambiguous

    def test_xps_word_order_maps_required_humanoid(self):
        bones = [
            "root ground", "root hips", "root pelvis",
            "leg left thigh", "leg left knee", "leg left ankle", "leg left toes",
            "leg left thigh adj. 1", "unused leg left toes adj. 1",
            "leg right thigh", "leg right knee", "leg right ankle", "leg right toes",
            "spine lower", "spine middle", "spine upper 2", "spine upper 1",
            "head neck lower", "head neck middle", "head neck upper",
            "arm left shoulder 1", "arm left shoulder 2", "arm left elbow", "arm left wrist",
            "arm left shoulder 1 adj. 1",
            "arm right shoulder 1", "arm right shoulder 2", "arm right elbow", "arm right wrist",
            "armor pelvis left",
        ]
        got, missing, ambiguous = self.names(bones)
        self.assertEqual(missing, [], ambiguous)
        self.assertEqual(got["Hips"], "root hips")
        self.assertEqual(got["Spine"], "spine lower")
        self.assertEqual(got["Chest"], "spine middle")
        self.assertEqual(got["Neck"], "head neck lower")
        self.assertEqual(got["Head"], "head neck upper")
        self.assertEqual(got["LeftUpperLeg"], "leg left thigh")
        self.assertEqual(got["LeftLowerLeg"], "leg left knee")
        self.assertEqual(got["LeftFoot"], "leg left ankle")
        self.assertEqual(got["LeftToes"], "leg left toes")
        self.assertEqual(got["LeftShoulder"], "arm left shoulder 1")
        self.assertEqual(got["LeftUpperArm"], "arm left shoulder 2")
        self.assertEqual(got["LeftLowerArm"], "arm left elbow")
        self.assertEqual(got["LeftHand"], "arm left wrist")
        self.assertEqual(got["RightUpperArm"], "arm right shoulder 2")

    def test_sided_hip_is_the_thigh_and_a_control_word_stays_hips(self):
        bones = ["hips control", "bip_hip_L", "bip_hip_R", "bip_hip_2_L", "bip_knee_L", "bip_knee_R",
                 "bip_foot_L", "bip_foot_R", "bip_spine_0", "bip_head", "Leg L", "Leg R"]
        got, missing, _ambiguous = self.names(bones)
        self.assertEqual(got["Hips"], "hips control")
        self.assertEqual(got["LeftUpperLeg"], "bip_hip_L")
        self.assertEqual(got["RightUpperLeg"], "bip_hip_R")
        self.assertEqual(got["LeftLowerLeg"], "bip_knee_L")
        self.assertNotIn("LeftUpperLeg", missing)

    def test_deform_hip_beats_fk_and_valve_beats_a_helper_hand(self):
        bones = ["DEF-bip_pelvis", "DEF-bip_hip_l", "bip_hip_fk_l", "DEF-bip_hip_r", "bip_hip_fk_r",
                 "DEF-bip_knee_l", "DEF-bip_knee_r", "DEF-bip_foot_l", "DEF-bip_foot_r",
                 "DEF-bip_spine_0", "DEF-bip_head", "ValveBiped.Bip01_R_Hand", "hand.R",
                 "ValveBiped.Bip01_L_UpperArm", "ValveBiped.Bip01_Back_L_UpperArm"]
        preferred = [bone for bone in bones if bone != "hand.R"]
        got, _missing, ambiguous = self.names(bones, preferred)
        self.assertEqual(got["Hips"], "DEF-bip_pelvis")
        self.assertEqual(got["LeftUpperLeg"], "DEF-bip_hip_l")
        self.assertEqual(got["RightHand"], "ValveBiped.Bip01_R_Hand")
        self.assertEqual(got["LeftUpperArm"], "ValveBiped.Bip01_L_UpperArm")
        self.assertEqual(ambiguous, [])

    def test_maya_side_tokens_prefer_the_primary_limb(self):
        bones = ["hips", "spine", "head",
                 "char_bnd_lf_big_UpperLeg_jnt", "char_bnd_lf_mid_UpperLeg_jnt", "char_bnd_lf_small_UpperLeg_jnt",
                 "char_bnd_lf_big_LowerLeg_jnt", "char_bnd_lf_big_Foot_jnt",
                 "char_bnd_rt_big_UpperLeg_jnt", "char_bnd_rt_big_LowerLeg_jnt", "char_bnd_rt_big_Foot_jnt",
                 "char_bnd_big_lf_lowerArm_jnt.001", "char_bnd_big_lf_lowerArm_jnt.002",
                 "char_bnd_big_lf_hand_Jnt.001", "char_bnd_big_lf_hand_Jnt.002",
                 "char_bnd_big_rt_lowerArm_jnt", "char_bnd_big_rt_hand_Jnt"]
        got, missing, ambiguous = self.names(bones)
        self.assertEqual(ambiguous, [])
        self.assertNotIn("LeftUpperLeg", missing)
        self.assertEqual(got["LeftUpperLeg"], "char_bnd_lf_big_UpperLeg_jnt")
        self.assertEqual(got["RightUpperLeg"], "char_bnd_rt_big_UpperLeg_jnt")
        self.assertEqual(got["LeftLowerArm"], "char_bnd_big_lf_lowerArm_jnt.001")
        self.assertEqual(got["LeftHand"], "char_bnd_big_lf_hand_Jnt.001")

    def test_game_deformer_names_stay_explicit(self):
        bones = ["GAME_C1_HIP1", "GAME_C1_SPINE1", "GAME_C1_SPINE2", "GAME_C1_SPINE3", "GAME_C1_NECK1", "GAME_C1_HEAD1",
                 "GAME_L1_clav1", "GAME_L1_arm1", "GAME_L1_arm2", "GAME_L1_arm3", "GAME_L1_leg1", "GAME_L1_leg2", "GAME_L1_leg3",
                 "GAME_R1_clav1", "GAME_R1_arm1", "GAME_R1_arm2", "GAME_R1_arm3", "GAME_R1_leg1", "GAME_R1_leg2", "GAME_R1_leg3"]
        got, missing, ambiguous = self.names(bones)
        self.assertEqual(missing, [])
        self.assertEqual(ambiguous, [])
        self.assertEqual(got["Hips"], "GAME_C1_HIP1")
        self.assertEqual(got["UpperChest"], "GAME_C1_SPINE3")
        self.assertEqual(got["LeftUpperLeg"], "GAME_L1_leg1")
        self.assertEqual(got["RightHand"], "GAME_R1_arm3")

    def test_weighted_game_thigh_beats_deform_armor(self):
        bones = ["GAME_C1_HIP1", "GAME_C1_SPINE1", "GAME_C1_HEAD1",
                 "GAME_L1_LEG1", "GAME_L1_LEG2", "GAME_L1_LEG3",
                 "GAME_R1_LEG1", "GAME_R1_LEG2", "GAME_R1_LEG3",
                 "GAME_L1_ARM1", "GAME_L1_ARM2", "GAME_L1_ARM3",
                 "GAME_R1_ARM1", "GAME_R1_ARM2", "GAME_R1_ARM3",
                 "DEF-Thigh_Armor.L", "DEF-Thigh_Armor.R", "ORG-thigh.L", "ORG-thigh.R"]
        preferred = [bone for bone in bones if bone.startswith("GAME_") or bone.startswith("DEF-")]
        got, missing, ambiguous = self.names(bones, preferred)
        self.assertEqual(missing, [])
        self.assertEqual(ambiguous, [])
        self.assertEqual(got["LeftUpperLeg"], "GAME_L1_LEG1")
        self.assertEqual(got["RightUpperLeg"], "GAME_R1_LEG1")
        self.assertEqual(got["LeftLowerLeg"], "GAME_L1_LEG2")
        self.assertEqual(got["RightLowerLeg"], "GAME_R1_LEG2")

    def test_clean_def_joint_beats_a_weighted_elbow_control(self):
        bones = ["Hips", "Spine", "Head",
                 "LeftUpperLeg", "LeftLowerLeg", "LeftFoot",
                 "RightUpperLeg", "RightLowerLeg", "RightFoot",
                 "LeftUpperArm", "LeftLowerArm", "LeftHand",
                 "RightUpperArm", "RightLowerArm", "RightHand",
                 "DEF-Hips", "DEF-Spine", "DEF-Chest", "DEF-Head",
                 "Chest",
                 "DEF-LeftUpperLeg_1", "DEF-LeftLowerLeg_1", "DEF-LeftFoot",
                 "DEF-RightUpperLeg_1", "DEF-RightLowerLeg_1", "DEF-RightFoot",
                 "DEF-LeftUpperArm_1", "DEF-LeftLowerArm_1", "DEF-LeftHand",
                 "DEF-RightUpperArm_1", "DEF-RightLowerArm_1", "DEF-RightHand",
                 "Elbow.L"]
        preferred = [bone for bone in bones if bone.startswith("DEF-") or bone == "Elbow.L"]
        got, missing, ambiguous = self.names(bones, preferred)
        self.assertEqual(missing, [])
        self.assertEqual(ambiguous, [])
        self.assertEqual(got["LeftLowerArm"], "DEF-LeftLowerArm_1")
        self.assertEqual(got["Chest"], "DEF-Chest")
        self.assertTrue(all(name.startswith("DEF-") for name in got.values()))
        plain, _, plain_ambiguous = self.names(bones)
        self.assertEqual(plain_ambiguous, [])
        self.assertEqual(plain["Chest"], "Chest")
        self.assertEqual(plain["LeftLowerArm"], "LeftLowerArm")
        self.assertEqual(plain["Hips"], "Hips")


if __name__ == "__main__":
    unittest.main()
