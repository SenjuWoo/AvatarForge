"""Dependency-free loopback UI, CLI and compact MCP interface."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser

from . import __version__
from .core import ROOT, Jobs, PRESETS, doctor, scan, extract_zip, prepare_unity, read_json, write_json, run_owned


class Service:
    def __init__(self, output=None):
        self.jobs = Jobs(output)
        self.actions = {}
        self.lock = threading.Lock()
        self.action_lock = threading.Lock()

    def action(self, name, function):
        key = secrets.token_hex(8)
        with self.lock:
            self.actions[key] = {"id": key, "name": name, "state": "running"}
        def run():
            try:
                with self.action_lock:
                    result = function()
                state = {"state": "complete", "result": result}
            except Exception as error:
                state = {"state": "failed", "error": str(error)}
            with self.lock:
                self.actions[key].update(state)
        threading.Thread(target=run, daemon=True).start()
        return dict(self.actions[key])

    def browse(self, kind):
        if os.name != "nt":
            raise ValueError("Enter a file or folder path on this platform.")
        if kind not in {"file", "folder"}:
            raise ValueError("Unknown picker type.")
        script = "Add-Type -AssemblyName System.Windows.Forms; "
        if kind == "folder":
            script += "$d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Choose model folder'; "
        else:
            script += "$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='Model or ZIP|*.blend;*.fbx;*.glb;*.gltf;*.obj;*.mdl;*.vmdl_c;*.smd;*.dmx;*.pmx;*.pmd;*.vrm;*.xps;*.mesh;*.ascii;*.dae;*.stl;*.ply;*.zip|All files|*.*'; "
        prop = "SelectedPath" if kind == "folder" else "FileName"
        script += f"if($d.ShowDialog() -eq 'OK'){{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Write-Output $d.{prop}}}"
        flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script], capture_output=True, timeout=600, creationflags=flags)
        if result.returncode:
            raise ValueError("File picker could not open. Enter the model path instead.")
        return {"path": result.stdout.decode("utf-8-sig", errors="replace").strip()}

    def invoke(self, method, args):
        if method == "ai_clients":
            from .integrations import clients
            return clients(project=args.get("project"))
        if method == "connect_ai":
            from .integrations import register
            return self.action("Connect AI", lambda: register(args.get("clients", "auto"), project=args.get("project")))
        if method == "doctor":
            return doctor()
        if method == "scan":
            return scan(args["source"])
        if method == "browse":
            return self.browse(args.get("kind", "file"))
        if method == "extract":
            destination = self.jobs.output_root / ("input-" + secrets.token_hex(8))
            return extract_zip(args["source"], destination)
        if method == "convert":
            return self.jobs.start(args["source"], args.get("preset", "preserve"), args.get("options"), args.get("blender"))
        if method == "job":
            return self.jobs.get(args["id"])
        if method == "jobs":
            return self.jobs.list()
        if method == "cancel":
            return self.jobs.cancel(args["id"])
        if method == "unity":
            job = self.jobs.get(args["id"])
            if job["state"] != "complete":
                raise ValueError("Finish conversion before preparing Unity.")
            if "approved_physics" in args:
                self.invoke("approve_physics", {"id": args["id"], "bones": args["approved_physics"]})
            return self.action("Prepare Unity", lambda: prepare_unity(job["output"], args.get("project")))
        if method == "action":
            with self.lock:
                if args["id"] not in self.actions:
                    raise ValueError("Unknown action.")
                return dict(self.actions[args["id"]])
        if method == "approve_physics":
            job = self.jobs.get(args["id"])
            report = read_json(Path(job["output"]) / "report.json")
            allowed = {p["bone"] for p in report.get("physics", [])}
            bones = args.get("bones", [])
            if not isinstance(bones, list) or any(b not in allowed for b in bones):
                raise ValueError("Choose only roots suggested by this conversion.")
            write_json(Path(job["output"]) / "unity-overrides.json", {"approved_physics": bones})
            return {"approved": bones}
        if method == "open":
            job = self.jobs.get(args["id"])
            kind = args.get("kind", "folder")
            folder = Path(job["output"])
            if kind == "blender":
                file = folder / "model.blend"
                if not file.is_file():
                    raise ValueError("No converted Blender file yet.")
                subprocess.Popen([job["blender"], "--disable-autoexec", str(file)], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            elif kind == "unity":
                from .core import discover_tool
                link = folder / "unity-project.json"
                project = Path(read_json(link)["project"]) if link.exists() else folder / "UnityProject"
                exe = discover_tool("unity")
                if not exe or not (project / "ProjectSettings").is_dir():
                    raise ValueError("Prepare the Unity project first.")
                command = [exe, "-projectPath", str(project)]
                report = job.get("unity_report") or {}
                if report.get("scene"):
                    command.extend(["-executeMethod", "AvatarForge.Editor.AvatarForgeImporter.OpenPreparedScene", "-avatarForgeInput", str(folder)])
                subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            elif kind == "folder":
                if os.name == "nt":
                    os.startfile(folder)
                else:
                    webbrowser.open(folder.as_uri())
            else:
                raise ValueError("Unknown destination.")
            return {"opened": kind}
        if method == "install":
            component = args.get("component", "core")
            if component not in {"core", "extended", "unity", "optimization", "all"}:
                raise ValueError("Unknown tool group.")
            if os.name != "nt":
                raise ValueError("Use the dependency links on non-Windows systems.")
            def install():
                log = ROOT / ".runtime" / "setup.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("w", encoding="utf-8") as stream:
                    result = run_owned(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools" / "Install.ps1"), "-Component", component], stdout=stream, stderr=subprocess.STDOUT, timeout=1800, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    raise RuntimeError(f"Tool setup stopped. See {log}.")
                return doctor()
            return self.action("Install " + component, install)
        raise ValueError("Unknown operation.")


def serve(port=0, output=None, launch=True):
    service = Service(output)
    token = secrets.token_urlsafe(32)
    web_root = ROOT / "web"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def valid_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            origin = self.headers.get("Origin")
            origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            return self.valid_host() and (origin is None or origin in origins) and secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def do_GET(self):
            if not self.valid_host():
                self.send(403, {"error": "Invalid host."})
                return
            path = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in files:
                filename, kind = files[path]
                self.send(200, (web_root / filename).read_bytes(), kind)
            elif path.startswith("/preview/") and self.authorized():
                try:
                    job = service.jobs.get(path.split("/")[-1])
                    image = Path(job["output"]) / "preview.png"
                    if not image.exists():
                        raise ValueError("No preview.")
                    self.send(200, image.read_bytes(), "image/png")
                except ValueError as error:
                    self.send(404, {"error": str(error)})
            else:
                self.send(404, {"error": "Not found."})

        def do_POST(self):
            if not self.authorized():
                self.send(403, {"error": "Unauthorized local request."})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size < 0 or size > 256 * 1024:
                    raise ValueError("Request exceeds 256 KiB.")
                args = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(args, dict):
                    raise ValueError("Request must be an object.")
                method = urlsplit(self.path).path.removeprefix("/api/")
                if self.path != "/api/" + method:
                    raise ValueError("Unknown route.")
                self.send(200, service.invoke(method, args))
            except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
                self.send(400, {"error": str(error)})
            except Exception as error:
                self.send(500, {"error": str(error)})
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/#token={token}"
    print(url, flush=True)
    if launch:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.jobs.close()


MCP_TOOLS = [
    {"name": "avatarforge_doctor", "description": "Read installed tool paths and supported model formats. No mutation.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "avatarforge_scan", "description": "List model candidates in a local file/folder or ZIP, without converting.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"]}},
    {"name": "avatarforge_convert", "description": "Convert one local model asynchronously into a new folder; preserve source, shape keys and secondary bones. Read verdict with avatarforge_job.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "preset": {"type": "string", "enum": list(PRESETS)}, "options": {"type": "object"}, "blender": {"type": "string"}}, "required": ["source"]}},
    {"name": "avatarforge_job", "description": "Read a compact conversion verdict and artifact paths. Set details=true only when the full manifest/log is needed.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "details": {"type": "boolean"}}, "required": ["id"]}},
    {"name": "avatarforge_cancel", "description": "Cancel only the selected AvatarForge worker process, keeping its output for diagnosis.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "avatarforge_prepare_unity", "description": "Create a fresh VRChat SDK project and import a completed conversion asynchronously. Optional PhysBone roots must come from its report. Never uploads; poll avatarforge_action.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "project": {"type": "string"}, "approved_physics": {"type": "array", "items": {"type": "string"}}}, "required": ["id"]}},
    {"name": "avatarforge_action", "description": "Read Unity preparation progress and a compact import verdict; details=true returns the complete verdict.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "details": {"type": "boolean"}}, "required": ["id"]}},
]


def compact_job(job):
    result = {key: job[key] for key in ("id", "state", "source", "output", "preset", "source_unchanged", "unity_project") if key in job}
    if job.get("error"):
        result["error"] = job["error"][:3000]
    folder = Path(job["output"])
    result["artifacts"] = {name: str(folder / name) for name in ("report.json", "blender.log", "model.blend", "model.fbx", "unity-report.json") if (folder / name).is_file()}
    report = job.get("report")
    if report:
        integrity = report.get("integrity", {})
        issues = report.get("issues", [])
        result["report"] = {"status": report.get("status"), "summary": report.get("summary"), "issues": issues[:20], "issue_count": len(issues),
                            "missing_bone_count": len(integrity.get("missing_bones", [])), "missing_shape_key_groups": len(integrity.get("missing_shape_keys", {})),
                            "missing_weighted_bone_count": len(integrity.get("missing_weighted_bones", [])), "missing_required_humanoid": report.get("missing_required_humanoid", [])}
    return result


def mcp_stdio(output=None):
    service = Service(output)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                request = {}
                raise ValueError("JSON-RPC message must be an object.")
            if "id" not in request:
                continue
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                supported = {"2024-11-05", "2025-03-26", "2025-06-18"}
                requested = params.get("protocolVersion")
                result = {"protocolVersion": requested if requested in supported else "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "AvatarForge", "version": __version__}, "instructions": "Use doctor then scan/convert/job. Native pipeline is deterministic; report needs_review means intervention is required. No avatar is uploaded."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": MCP_TOOLS}
            elif method == "tools/call":
                name = params.get("name", "")
                if name not in {t["name"] for t in MCP_TOOLS}:
                    raise ValueError("Unknown tool.")
                try:
                    operation = "unity" if name == "avatarforge_prepare_unity" else name.removeprefix("avatarforge_")
                    data = service.invoke(operation, params.get("arguments") or {})
                    if name == "avatarforge_job" and not (params.get("arguments") or {}).get("details"):
                        data = compact_job(data)
                    elif name == "avatarforge_doctor":
                        data.pop("dependencies", None)
                        data["dependency_catalog"] = str(ROOT / "avatarforge" / "dependencies.json")
                    elif name == "avatarforge_action" and data.get("result") and not (params.get("arguments") or {}).get("details"):
                        report = data["result"].get("report", {})
                        compact = {key: report[key] for key in ("status", "humanoid_valid", "humanoid_human", "humanoid_pose_verified", "humanoid_pose_error", "descriptor_added", "pipeline_manager_added", "bone_integrity_verified", "blendshape_integrity_verified", "blendshape_defaults_verified", "blendshape_default_count", "skin_weight_integrity_verified", "triangles", "bones", "blendshapes", "physbone_components", "texture_storage_estimate_available", "texture_memory_bytes", "referenced_texture_count", "sdk_build_validated", "uploaded") if key in report}
                        if report.get("optimization"):
                            compact["optimization"] = {key: report["optimization"][key] for key in ("status", "engine", "target_triangles", "before_triangles", "after_triangles") if key in report["optimization"]}
                        compact["issues"] = report.get("issues", [])[:20]
                        compact["issue_count"] = len(report.get("issues", []))
                        data["result"] = {"project": data["result"].get("project"), "report": compact}
                    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
                except Exception as error:
                    result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
            else:
                raise ValueError("Unsupported method.")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as error:
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32600, "message": str(error)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="AvatarForge: local model-to-avatar conversion")
    parser.add_argument("command", nargs="?", default="ui", choices=["ui", "doctor", "scan", "convert", "mcp", "unity", "connect"])
    parser.add_argument("source", nargs="?")
    parser.add_argument("--preset", choices=list(PRESETS), default="preserve")
    parser.add_argument("--options", help="Path to JSON options (humanoid mapping, selected meshes, height)")
    parser.add_argument("--output", help="Output root; each conversion gets a new subfolder")
    parser.add_argument("--blender", help="Explicit Blender executable")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--providers", nargs="+", default=["auto"], help="AI clients to register, or auto for detected clients")
    parser.add_argument("--project", help="Register only for this project when the client supports project scope")
    args = parser.parse_args()
    try:
        if args.command == "ui":
            serve(args.port, args.output, not args.no_browser)
        elif args.command == "mcp":
            mcp_stdio(args.output)
        elif args.command == "doctor":
            print(json.dumps(doctor(), indent=2))
        elif args.command == "connect":
            from .integrations import register
            result = register("auto" if args.providers == ["auto"] else args.providers, project=args.project)
            print(json.dumps(result, indent=2))
            if result["status"] == "needs_attention":
                sys.exit(1)
        elif not args.source:
            parser.error("This command requires a model/folder path.")
        elif args.command == "scan":
            print(json.dumps(scan(args.source), indent=2))
        elif args.command == "unity":
            print(json.dumps(prepare_unity(args.source), indent=2))
        else:
            jobs = Jobs(args.output)
            job = jobs.start(args.source, args.preset, read_json(args.options) if args.options else {}, args.blender)
            print(json.dumps({"id": job["id"], "output": job["output"]}), flush=True)
            while job["state"] in {"queued", "converting"}:
                time.sleep(1)
                job = jobs.get(job["id"])
            print(json.dumps(job, indent=2))
            if job["state"] != "complete":
                sys.exit(1)
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
