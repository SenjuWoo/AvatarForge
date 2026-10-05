"""Use the installed Hermes configuration API, never a YAML template."""
from pathlib import Path
import copy
import json
import sys

sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from avatarforge.integrations import _atomic_update, _merge_entry, _without_server


def main():
    import psutil
    import yaml
    from hermes_cli.config import get_config_path, save_config
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (process.info["name"] or "").lower()
            args = process.info["cmdline"] or []
            active = name in {"hermes.exe", "hermes-desktop.exe"} or (
                name.startswith("python") and any(str(a).replace("\\", "/").endswith(
                    ("run_agent.py", "gateway/run.py", "hermes_cli.gateway")) or str(a) == "hermes_cli" for a in args))
            if active:
                raise RuntimeError("Close Hermes and its gateway before registering, then run Connect AI again.")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    request = json.load(sys.stdin)
    path = get_config_path()
    original = path.read_bytes() if path.exists() else None
    before = yaml.safe_load(original.decode("utf-8-sig")) if original else {}
    before = before or {}
    if not isinstance(before, dict) or not isinstance(before.get("mcp_servers", {}), dict):
        raise ValueError("Hermes configuration is not a mapping; preserved.")
    entry = _merge_entry(before.get("mcp_servers", {}).get("avatarforge"), request["entry"])
    if before.get("mcp_servers", {}).get("avatarforge") == entry:
        print(json.dumps({"changed": False, "path": str(path)}))
        return
    updated = copy.deepcopy(before)
    updated.setdefault("mcp_servers", {})["avatarforge"] = entry
    # Official Hermes save_config writes to its active home atomically. Give it
    # only the parsed existing mapping plus our one server, not merged defaults.
    backup = None
    if original is not None:
        import uuid
        folder = Path(request["backup_root"])
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / (uuid.uuid4().hex + ".original")
        backup.write_bytes(original)
        if backup.read_bytes() != original:
            raise OSError("Hermes backup did not verify.")
    try:
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("Hermes settings changed during registration; retry.")
        save_config(updated)
        after = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if after.get("mcp_servers", {}).get("avatarforge") != entry or _without_server(before, "mcp_servers") != _without_server(after, "mcp_servers"):
            raise RuntimeError("Hermes round-trip changed unrelated settings.")
    except BaseException:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
        raise
    print(json.dumps({"changed": True, "path": str(path), "backup": str(backup) if backup else None}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
