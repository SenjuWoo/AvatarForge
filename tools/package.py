"""Package only the exact, clean Git commit. Runtime downloads and models are excluded."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avatarforge import __version__

parser = argparse.ArgumentParser()
parser.add_argument("--output", default="dist")
args = parser.parse_args()
subprocess.run([sys.executable, str(ROOT / "tools/check_contract.py")], check=True)
if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT):
    raise SystemExit("Commit the final source before packaging; the working tree is not clean.")
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
archive = output / f"AvatarForge-{__version__}.zip"
subprocess.run(["git", "archive", "--format=zip", "--prefix=AvatarForge/", "--output", str(archive), commit], cwd=ROOT, check=True)
with zipfile.ZipFile(archive) as package:
    assert package.testzip() is None
    assert "AvatarForge/START-HERE.bat" in package.namelist()
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
receipt = {"version": __version__, "commit": commit, "archive": archive.name, "bytes": archive.stat().st_size, "sha256": digest}
(output / "package-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
(output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
print(json.dumps(receipt, indent=2))
