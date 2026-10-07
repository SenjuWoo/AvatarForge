"""Local MCP registration. Merge one owned server; never replace a user profile."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from .core import ROOT, local_path

NAME = "avatarforge"
MARKER = "AVATARFORGE_MANAGED"
_UNSPECIFIED = object()
CLIENTS = {
    "codex": ("Codex", "toml", "mcp_servers", ".codex/config.toml", "codex"),
    "claude-code": ("Claude Code", "json", "mcpServers", ".claude.json", "claude"),
    "claude-desktop": ("Claude Desktop", "json", "mcpServers", "@appdata/Claude/claude_desktop_config.json", ""),
    "cursor": ("Cursor", "json", "mcpServers", ".cursor/mcp.json", "cursor"),
    "gemini": ("Gemini CLI", "json", "mcpServers", ".gemini/settings.json", "gemini"),
    "copilot": ("GitHub Copilot", "json", "mcpServers", ".copilot/mcp-config.json", "copilot"),
    "vscode": ("VS Code", "json", "servers", "@appdata/Code/User/mcp.json", "code"),
    "windsurf": ("Windsurf / Devin", "json", "mcpServers", "@appdata/devin/mcp_config.json", "windsurf"),
    "opencode": ("OpenCode", "json", "mcp", ".config/opencode/opencode.json", "opencode"),
    "hermes": ("Hermes", "yaml", "mcp_servers", ".hermes/config.yaml", "hermes"),
}
PROJECT_FILES = {"codex": ".codex/config.toml", "claude-code": ".mcp.json", "cursor": ".cursor/mcp.json",
                 "gemini": ".gemini/settings.json", "copilot": ".mcp.json", "vscode": ".vscode/mcp.json",
                 "opencode": "opencode.json"}
SKILL_FOLDERS = {"codex": ".agents/skills/avatarforge", "claude-code": ".claude/skills/avatarforge",
                 "gemini": ".gemini/skills/avatarforge", "hermes": ".hermes/skills/avatarforge"}


class JsonDocument:
    """JSON/JSONC spans let us keep comments, formatting and all unrelated bytes."""
    def __init__(self, text):
        self.text = text
        self.tokens = []
        pattern = re.compile(r'\s+|//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[{}\[\],:]|(?:true|false|null)\b|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?')
        position = 0
        while position < len(text):
            match = pattern.match(text, position)
            if not match:
                raise ValueError("Invalid JSON configuration; existing bytes were preserved.")
            value = match.group()
            if not value.isspace() and not value.startswith(("//", "/*")):
                self.tokens.append((value, match.start(), match.end()))
            position = match.end()
        self.index = 0
        try:
            self.root = self.node()
        except (IndexError, TypeError, json.JSONDecodeError):
            raise ValueError("Incomplete or invalid JSON configuration; preserved.") from None
        if self.index != len(self.tokens) or not isinstance(self.root["value"], dict):
            raise ValueError("Client configuration must be a JSON object.")

    def take(self, expected=None):
        if self.index >= len(self.tokens):
            raise ValueError("Incomplete JSON configuration.")
        token = self.tokens[self.index]
        if expected and token[0] != expected:
            raise ValueError("Invalid JSON configuration.")
        self.index += 1
        return token

    def node(self):
        token = self.take()
        value, start, end = token
        children = {}
        trailing = False
        if value in ("{", "["):
            closing = "}" if value == "{" else "]"
            result = {} if value == "{" else []
            while self.index < len(self.tokens) and self.tokens[self.index][0] != closing:
                if value == "{":
                    key = json.loads(self.take()[0])
                    if not isinstance(key, str) or key in result:
                        raise ValueError("Duplicate or invalid configuration key.")
                    self.take(":")
                    child = self.node()
                    result[key] = child["value"]
                    children[key] = child
                else:
                    result.append(self.node()["value"])
                if self.tokens[self.index][0] == closing:
                    break
                self.take(",")
                trailing = self.tokens[self.index][0] == closing
            close = self.take(closing)
            return {"value": result, "start": start, "end": close[2], "close": close[1],
                    "children": children, "trailing": trailing}
        return {"value": json.loads(value), "start": start, "end": end, "children": children}

    def set_server(self, namespace, entry):
        parent = self.root["children"].get(namespace)
        if parent is None:
            parent = self.root
            key, value = namespace, {NAME: entry}
        else:
            if not isinstance(parent["value"], dict):
                raise ValueError("MCP configuration namespace must be an object.")
            key, value = NAME, entry
        node = parent["children"].get(key)
        encoded = json.dumps(value, ensure_ascii=False, indent=2)
        if node:
            return self.text[:node["start"]] + encoded + self.text[node["end"]:]
        separator = "," if parent["value"] and not parent["trailing"] else ""
        insertion = separator + "\n  " + json.dumps(key) + ": " + encoded + "\n"
        return self.text[:parent["close"]] + insertion + self.text[parent["close"]:]


def _without_server(document, namespace):
    result = copy.deepcopy(document)
    servers = result.get(namespace)
    if isinstance(servers, dict):
        servers.pop(NAME, None)
        if not servers:
            result.pop(namespace, None)
    return result


def _merge_entry(current, desired):
    if current is not None and not isinstance(current, dict):
        raise ValueError("An incompatible avatarforge entry already exists; preserved.")
    current = copy.deepcopy(current or {})
    environment_key = "environment" if desired.get("type") == "local" else "env"
    environment = current.get(environment_key, {})
    if not isinstance(environment, dict):
        raise ValueError("Existing avatarforge environment is not an object; preserved.")
    if current and environment.get(MARKER) != "1":
        old_args = current.get("args", current.get("command", []))
        legacy = isinstance(old_args, list) and any(
            isinstance(p, str) and Path(p).name == "run.py" and Path(p).parent.name.lower() == "avatarforge"
            for p in old_args) and "mcp" in old_args
        if not legacy:
            raise ValueError("An unowned avatarforge server already exists; preserved.")
    for key, value in desired.items():
        if key == environment_key:
            current[key] = {**environment, **value}
        elif key == "enabled" and key in current:
            continue
        else:
            current[key] = value
    return current


def _toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + _toml_value(v) for k, v in value.items()) + " }"
    raise ValueError("Unsupported existing TOML value in AvatarForge server; preserved.")


def merge_config(text, kind, namespace, desired):
    if kind == "json":
        document = JsonDocument(text or "{}")
        before = document.root["value"]
        servers = before.get(namespace, {})
        if not isinstance(servers, dict):
            raise ValueError("MCP namespace is not an object; preserved.")
        entry = _merge_entry(servers.get(NAME), desired)
        if servers.get(NAME) == entry:
            return text or "{}"
        result = document.set_server(namespace, entry)
        after = JsonDocument(result).root["value"]
    else:
        try:
            import tomllib
        except ImportError:
            raise ValueError("Codex registration needs the bundled Python or Python 3.11+.") from None
        before = tomllib.loads(text)
        entry = _merge_entry(before.get(namespace, {}).get(NAME), desired)
        if before.get(namespace, {}).get(NAME) == entry:
            return text
        headers = list(re.finditer(r"(?m)^\s*\[[^\r\n]+\][ \t]*(?:#[^\r\n]*)?\r?$", text))
        owned = re.compile(r'^\[\s*mcp_servers\s*\.\s*(?:avatarforge|"avatarforge"|\'avatarforge\')(?:\s*\.|\s*\])')
        spans = [(h.start(), headers[i+1].start() if i+1 < len(headers) else len(text))
                 for i, h in enumerate(headers) if owned.match(h.group().strip())]
        if NAME in before.get(namespace, {}) and not spans:
            raise ValueError("Inline Codex MCP tables need native client editing; existing file preserved.")
        result = text
        for start, end in reversed(spans):
            result = result[:start] + result[end:]
        result = result.rstrip() + "\n\n[mcp_servers.avatarforge]\n" + "\n".join(
            key + " = " + _toml_value(value) for key, value in entry.items()) + "\n"
        after = tomllib.loads(result)
    if _without_server(before, namespace) != _without_server(after, namespace):
        raise ValueError("Unrelated configuration changed; no write performed.")
    return result


def _atomic_update(path, payload, backup_root, previous=_UNSPECIFIED):
    path, backup_root = Path(path), Path(backup_root)
    existing = path.read_bytes() if path.exists() else None
    if previous is not _UNSPECIFIED and existing != previous:
        raise ValueError("Configuration changed during registration; retry.")
    if existing == payload:
        return {"changed": False, "path": str(path)}
    backup = None
    if existing is not None:
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / (uuid.uuid4().hex + ".original")
        backup.write_bytes(existing)
        if backup.read_bytes() != existing:
            raise OSError("Configuration backup did not verify.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".avatarforge-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(payload)
        if existing is not None:
            shutil.copymode(path, temporary)
        if (path.read_bytes() if path.exists() else None) != existing:
            raise ValueError("Configuration changed during registration; retry.")
        os.replace(temporary, path)
        if path.read_bytes() != payload:
            raise OSError("Written configuration did not verify.")
    except BaseException:
        if path.exists() and path.read_bytes() == payload:
            if existing is None:
                path.unlink()
            else:
                path.write_bytes(existing)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {"changed": True, "path": str(path), "backup": str(backup) if backup else None}


def connection(root=ROOT):
    root = Path(root).resolve()
    executable = root / ".runtime/python/python.exe"
    if not executable.is_file():
        executable = Path(sys.executable).resolve()
    return {"command": str(executable), "args": [str(root / "run.py"), "mcp"], "env": {MARKER: "1"}}


def handshake(root=ROOT):
    entry = connection(root)
    messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "AvatarForge setup", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    result = subprocess.run([entry["command"], *entry["args"]], input="\n".join(json.dumps(m) for m in messages) + "\n",
                            capture_output=True, text=True, encoding="utf-8", timeout=20, cwd=root,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    if result.returncode or len(replies) != 2 or replies[0].get("result", {}).get("serverInfo", {}).get("name") != "AvatarForge":
        raise RuntimeError("AvatarForge MCP could not initialize. Run INSTALL-TOOLS.bat first.")
    tools = replies[1].get("result", {}).get("tools", [])
    if len(tools) != 7 or any(not t.get("name", "").startswith("avatarforge_") for t in tools):
        raise RuntimeError("AvatarForge MCP tool discovery did not verify.")
    return {"initialized": True, "tool_count": len(tools), "server": replies[0]["result"]["serverInfo"]}


def client_path(client, home=None, appdata=None, project=None):
    home = Path(home or Path.home())
    appdata = Path(appdata or os.environ.get("APPDATA", home / ".config"))
    if project:
        return Path(local_path(project)) / PROJECT_FILES[client] if client in PROJECT_FILES else None
    location = CLIENTS[client][3]
    if client == "codex" and home == Path.home() and os.environ.get("CODEX_HOME"):
        return Path(os.environ["CODEX_HOME"]) / "config.toml"
    if client == "copilot" and home == Path.home() and os.environ.get("COPILOT_HOME"):
        return Path(os.environ["COPILOT_HOME"]) / "mcp-config.json"
    if client == "hermes" and home == Path.home() and os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]) / "config.yaml"
    if client == "windsurf" and (home / ".codeium/windsurf").exists():
        return home / ".codeium/windsurf/mcp_config.json"
    if client == "opencode":
        folder = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) if home == Path.home() else home / ".config"
        path = folder / "opencode/opencode.json"
        return path.with_suffix(".jsonc") if path.with_suffix(".jsonc").exists() else path
    return appdata / location.removeprefix("@appdata/") if location.startswith("@appdata/") else home / location


def clients(home=None, appdata=None, project=None):
    result = []
    for key, (label, kind, namespace, _, command) in CLIENTS.items():
        user_path = client_path(key, home, appdata)
        path = client_path(key, home, appdata, project)
        marker = (Path(home or Path.home()) / ".claude") if key == "claude-code" else user_path.parent
        installed = user_path.exists() or marker.is_dir() or bool(command and shutil.which(command))
        result.append({"id": key, "name": label, "detected": installed, "config": str(path) if path else None,
                       "project_supported": key in PROJECT_FILES, "skill_included": key in SKILL_FOLDERS})
    return {"clients": result, "connection": connection(), "no_ai_required": True}


def _skill_plan(client, root=ROOT, home=None, project=None, appdata=None):
    if client not in SKILL_FOLDERS:
        return None
    base = Path(local_path(project)) if project else Path(home or Path.home())
    destination = base / SKILL_FOLDERS[client] / "SKILL.md"
    if client == "hermes":
        destination = client_path(client, home, appdata).parent / "skills/avatarforge/SKILL.md"
    payload = (Path(root) / "skills/avatarforge/SKILL.md").read_bytes()
    existing = destination.read_bytes() if destination.exists() else None
    receipt_path = Path(root) / ".runtime/provider-skills" / (client + ".json")
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if existing is not None and existing != payload and receipt.get("sha256") != hashlib.sha256(existing).hexdigest():
        raise ValueError("An existing AvatarForge skill has personal changes; preserved.")
    return destination, payload, existing, receipt_path


def install_skill(client, root=ROOT, home=None, project=None, appdata=None):
    plan = _skill_plan(client, root, home, project, appdata)
    if plan is None:
        return None
    destination, payload, existing, receipt_path = plan
    result = _atomic_update(destination, payload, Path(root) / ".runtime/provider-backups" / client, existing)
    record = {"path": str(destination), "sha256": hashlib.sha256(payload).hexdigest()}
    _atomic_update(receipt_path, (json.dumps(record) + "\n").encode(), Path(root) / ".runtime/provider-backups" / client)
    return result


def register(selected="auto", root=ROOT, home=None, appdata=None, project=None):
    root = Path(root).resolve()
    available = clients(home, appdata, project)["clients"]
    selected = [c["id"] for c in available if c["detected"]] if selected == "auto" else selected
    if not isinstance(selected, list) or any(c not in CLIENTS for c in selected):
        raise ValueError("Choose supported AI clients or auto.")
    proof = handshake(root)
    generic = root / ".runtime/ai-connection/mcp.json"
    _atomic_update(generic, (json.dumps({"mcpServers": {NAME: connection(root)}}, indent=2) + "\n").encode(), root / ".runtime/provider-backups/generic")
    results = []
    for client in dict.fromkeys(selected):
        path = client_path(client, home, appdata, project)
        try:
            if path is None:
                raise ValueError("This client has no reviewed project scope. Choose user scope or import the generic entry.")
            _skill_plan(client, root, home, project, appdata)
            kind, namespace = CLIENTS[client][1:3]
            desired = connection(root)
            if client == "opencode":
                desired = {"type": "local", "command": [desired["command"], *desired["args"]],
                           "environment": desired["env"], "enabled": True}
            elif client in {"claude-code", "vscode"}:
                desired["type"] = "stdio"
            if kind == "yaml":
                result = _register_hermes(path, desired, root)
            else:
                existing = path.read_bytes() if path.exists() else b""
                text = existing.decode("utf-8-sig")
                updated = merge_config(text, kind, namespace, desired)
                payload = updated.encode("utf-8")
                if existing.startswith(b"\xef\xbb\xbf"):
                    payload = b"\xef\xbb\xbf" + payload
                result = _atomic_update(path, payload, root / ".runtime/provider-backups" / client, existing if path.exists() else None)
            try:
                skill = install_skill(client, root, home, project, appdata)
            except (ValueError, OSError) as error:
                results.append({"client": client, "state": "needs_attention", "config": result,
                                "error": "MCP configuration registered, but usage skill could not be updated: " + str(error)})
                continue
            results.append({"client": client, "state": "registered", "config": result, "skill": skill,
                            "next_step": "Restart or reload this AI client to discover AvatarForge."})
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            results.append({"client": client, "state": "blocked", "error": str(error)})
    return {"handshake": proof, "clients": results, "generic_config": str(generic),
            "status": "needs_attention" if any(r["state"] != "registered" for r in results) else "registered"}


def _register_hermes(path, entry, root):
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "hermes/hermes-agent"
    python = Path(os.environ.get("HERMES_PYTHON", local / "venv/Scripts/python.exe"))
    if not python.is_file():
        raise ValueError("Hermes native Python was not found. Import the generated MCP entry with hermes mcp add, or use HERMES_PYTHON.")
    environment = os.environ.copy()
    environment["HERMES_HOME"] = str(path.parent)
    result = subprocess.run([str(python), str(root / "tools/hermes_register.py")],
                            input=json.dumps({"entry": entry, "backup_root": str(root / ".runtime/provider-backups/hermes")}),
                            text=True, encoding="utf-8", capture_output=True, cwd=local, env=environment, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Hermes native registration failed; original settings preserved.")
    return json.loads(result.stdout)
