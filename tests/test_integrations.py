"""Provider configuration preservation and real stdio discovery from clean homes."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from avatarforge.core import ROOT, Jobs, write_json
from avatarforge.integrations import CLIENTS, JsonDocument, _atomic_update, client_path, connection, handshake, merge_config, register


class IntegrationChecks(unittest.TestCase):
    def test_jsonc_comments_trailing_commas_and_unknown_settings_survive(self):
        text = '{\n// My chosen model\n"model":"my-local-model",\n"mcpServers": { /* retained */ "other": {"command":"tool"}, },\n"nested": [1,true,{"value":null},],\n}\n'
        entry = {"command": "portable python.exe", "args": ["モデル/run.py", "mcp"], "env": {"AVATARFORGE_MANAGED": "1"}}
        result = merge_config(text, "json", "mcpServers", entry)
        data = JsonDocument(result).root["value"]
        self.assertEqual(data["model"], "my-local-model")
        self.assertEqual(data["mcpServers"]["other"], {"command": "tool"})
        self.assertEqual(data["nested"], [1, True, {"value": None}])
        self.assertIn("// My chosen model", result)
        self.assertIn("/* retained */", result)
        self.assertEqual(merge_config(result, "json", "mcpServers", entry), result)

    def test_existing_server_filters_disabled_state_and_environment_survive(self):
        old = {"mcp_servers": {"avatarforge": {"command": "old python", "args": ["old/run.py", "mcp"],
               "enabled": False, "enabled_tools": ["avatarforge_scan"], "env": {"AVATARFORGE_MANAGED": "1", "CUSTOM": "keep"}}}, "model": "local"}
        text = 'model = "local"\n[mcp_servers.avatarforge]\ncommand="old python"\nargs=["old/run.py","mcp"]\nenabled=false\nenabled_tools=["avatarforge_scan"]\n[mcp_servers.avatarforge.env]\nAVATARFORGE_MANAGED="1"\nCUSTOM="keep"\n[mcp_servers.other]\ncommand="existing-tool"\n'
        import tomllib
        result = merge_config(text, "toml", "mcp_servers", connection(ROOT))
        data = tomllib.loads(result)
        server = data["mcp_servers"]["avatarforge"]
        self.assertFalse(server["enabled"])
        self.assertEqual(server["enabled_tools"], ["avatarforge_scan"])
        self.assertEqual(server["env"]["CUSTOM"], "keep")
        self.assertEqual(data["mcp_servers"]["other"]["command"], "existing-tool")
        self.assertEqual(data["model"], old["model"])
        self.assertEqual(merge_config(result, "toml", "mcp_servers", connection(ROOT)), result)

    def test_conflicts_duplicates_and_malformed_files_are_not_overwritten(self):
        desired = connection(ROOT)
        for text in ('{"mcpServers":{"avatarforge":{"command":"my-own-tool"}}}', '{"a":1,"a":2}', '{"mcpServers":[]}', '{', '{"bad":[1,'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                merge_config(text, "json", "mcpServers", desired)

    def test_atomic_backup_unchanged_second_run_and_write_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            original = b'\xef\xbb\xbf{"model":"keep"}\r\n'
            path.write_bytes(original)
            first = _atomic_update(path, b'{"model":"keep","mcpServers":{}}', Path(temp) / "backups", original)
            self.assertEqual(Path(first["backup"]).read_bytes(), original)
            self.assertFalse(_atomic_update(path, path.read_bytes(), Path(temp) / "backups")["changed"])
            before = path.read_bytes()
            with patch("avatarforge.integrations.os.replace", side_effect=PermissionError("locked")), self.assertRaises(PermissionError):
                _atomic_update(path, b"changed", Path(temp) / "backups", before)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(path.parent.glob("*.tmp")))
            missing = Path(temp) / "new.json"
            missing.write_bytes(b"someone else's new config")
            with self.assertRaises(ValueError):
                _atomic_update(missing, b"overwrite", Path(temp) / "backups", None)

    def test_real_mcp_and_registration_matrix_on_clean_unicode_home(self):
        with tempfile.TemporaryDirectory(prefix="AvatarForge AI – ") as temp:
            root = Path(temp) / "App folder"
            root.mkdir()
            shutil.copy2(ROOT / "run.py", root / "run.py")
            shutil.copytree(ROOT / "avatarforge", root / "avatarforge", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "skills", root / "skills")
            home, appdata = Path(temp) / "モデル Home", Path(temp) / "Roaming"
            home.mkdir()
            keys = [k for k in CLIENTS if k != "hermes"]
            for key in keys:
                path = client_path(key, home, appdata)
                path.parent.mkdir(parents=True, exist_ok=True)
                text = 'model="keep"\n[mcp_servers.other]\ncommand="keep"\n' if key == "codex" else '{"model":"keep","unknown":{"personal":true}}'
                path.write_text(text, encoding="utf-8")
            result = register(keys, root=root, home=home, appdata=appdata)
            self.assertEqual(result["status"], "registered", result)
            self.assertEqual(result["handshake"]["tool_count"], 7)
            for item in result["clients"]:
                self.assertEqual(item["state"], "registered")
                self.assertTrue(Path(item["config"]["backup"]).is_file())
                if item["skill"]:
                    self.assertEqual(len(list(Path(item["skill"]["path"]).parent.iterdir())), 1)
            again = register(keys, root=root, home=home, appdata=appdata)
            self.assertTrue(all(not item["config"]["changed"] for item in again["clients"]), again)
            for key in keys:
                text = client_path(key, home, appdata).read_text(encoding="utf-8")
                self.assertIn("keep", text)
            self.assertTrue((root / ".runtime/ai-connection/mcp.json").is_file())
            self.assertFalse((home / ".hermes").exists())

    def test_project_scope_never_falls_back_to_user_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            project = Path(temp) / "project"
            with patch("avatarforge.integrations.handshake", return_value={"tool_count": 7}):
                result = register(["claude-desktop", "windsurf", "hermes"], root=ROOT, home=home, project=project)
            self.assertEqual(result["status"], "needs_attention")
            self.assertTrue(all(item["state"] == "blocked" for item in result["clients"]))
            self.assertFalse(home.exists())

    def test_personally_modified_skill_is_preserved_before_config_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            skill = home / ".agents/skills/avatarforge/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("<!-- AvatarForge-managed skill -->\nMy personal instructions.")
            config = home / ".codex/config.toml"
            config.parent.mkdir()
            config.write_text('model="my-model"\n')
            before = config.read_bytes()
            with patch("avatarforge.integrations.handshake", return_value={"tool_count": 7}):
                result = register(["codex"], root=ROOT, home=home)
            self.assertEqual(result["status"], "needs_attention")
            self.assertEqual(config.read_bytes(), before)
            self.assertIn("My personal instructions.", skill.read_text())

    def test_moved_conversion_history_uses_its_actual_owned_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "converted"
            write_json(folder / "receipt.json", {"id": "converted", "state": "complete", "output": "/old/app/outputs/converted",
                       "output_relative": "converted", "app_root": "/old/app", "addon_paths": ["/old/app/.runtime/addons"]})
            jobs = Jobs(root)
            self.assertEqual(jobs.get("converted")["output"], str(folder.resolve()))
            self.assertEqual(jobs.get("converted")["addon_paths"], [str(ROOT / ".runtime/addons")])
            jobs.close()


if __name__ == "__main__":
    unittest.main()
