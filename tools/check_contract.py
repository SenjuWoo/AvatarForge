"""Small distribution/version/privacy contract; no editor installation needed."""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avatarforge import __version__

assert json.loads((ROOT / "unity/Packages/dev.senjuwoo.avatarforge/package.json").read_text())["version"] == __version__
assert f'version = "{__version__}"' in (ROOT / "pyproject.toml").read_text()
catalog = json.loads((ROOT / "avatarforge/dependencies.json").read_text())
for name, entry in catalog["dependencies"].items():
    if "download_url" in entry:
        assert entry["download_url"].startswith("https://"), name
        assert re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")) or re.fullmatch(r"[0-9a-f]{128}", entry.get("sha512", "")), name
assert catalog["dependencies"]["vrchat_base"]["version"] == catalog["dependencies"]["vrchat_sdk"]["version"]
for required in ("START-HERE.bat", "INSTALL-TOOLS.bat", "CONNECT-AI.bat", "skills/avatarforge/SKILL.md", "run.py", "web/index.html", "web/app.js", "web/style.css", "tools/Install.ps1", "LICENSE", "README.md"):
    assert (ROOT / required).is_file(), required
assert (ROOT / "unity/Packages/dev.senjuwoo.avatarforge/LICENSE.md").read_bytes() == (ROOT / "LICENSE").read_bytes(), "Standalone Unity package must retain the same MIT notice"
if (ROOT / ".git").exists():
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    for name in tracked:
        assert not name.startswith((".runtime/", ".local/", "outputs/", "validation/", ".validation/", ".codex/", ".agents/", "dist/")), name
        assert Path(name).suffix.lower() not in {".blend", ".fbx", ".glb", ".gltf", ".obj", ".dae", ".vrm", ".pmx", ".pmd", ".xps", ".mesh", ".ascii", ".smd", ".vta", ".dmx", ".vtf", ".vmt", ".mdl", ".vmdl_c", ".vvd", ".vtx", ".unitypackage"}, name
print("AVATARFORGE_CONTRACT_PASS", __version__)
