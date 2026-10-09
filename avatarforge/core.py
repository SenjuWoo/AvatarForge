"""Small shared engine for the UI, CLI and MCP. All jobs have their own outputs."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import errno
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FORMATS = {".blend", ".fbx", ".glb", ".gltf", ".obj", ".mdl", ".vmdl_c", ".smd", ".dmx", ".pmx", ".pmd", ".vrm", ".xps", ".mesh", ".ascii", ".dae", ".stl", ".ply"}


def local_path(value, root=None):
    """Canonical selected path, optionally contained in an owned folder.

    Unbounded paths are selected by the local owner; this is not a sandbox.
    Resolve links before checking containment, including Windows junctions.
    """
    if not isinstance(value, (str, os.PathLike)) or not isinstance(os.fspath(value), str):
        raise ValueError("path must be a string")
    if "\x00" in os.fspath(value):
        raise ValueError("path contains a null byte")
    absolute = str(Path(value).expanduser().resolve())
    if root is not None:
        base = str(Path(root).expanduser().resolve())
        try:
            contained = os.path.commonpath([os.path.normcase(absolute), os.path.normcase(base)]) == os.path.normcase(base)
        except ValueError:
            contained = False
        if not contained:
            raise ValueError("path escapes the allowed folder")
    return absolute

PRESETS = {
    "preserve": {"label": "Preserve", "description": "Skip polygon reduction and texture downscaling. Keep the authored outfit state."},
    "balanced": {"label": "PC balanced", "description": "2K texture cap and safe mesh reduction. Keeps shape keys and secondary bones."},
    "mobile": {"label": "Mobile candidate", "description": "1K texture cap and lower geometry targets. Unity/SDK review is still required."},
}
UNITY_VERSION = "2022.3.22f1"
_owned_processes = set()
_owned_process_lock = threading.Lock()


def _stop_owned_processes():
    with _owned_process_lock:
        processes = list(_owned_processes)
    for process in processes:
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            except OSError:
                pass


atexit.register(_stop_owned_processes)


def run_owned(args, **kwargs):
    """Run only a new setup/import child; close it if its app/MCP session exits."""
    timeout = kwargs.pop("timeout", None)
    parent_lifetime = kwargs.pop("parent_lifetime", False)
    process = subprocess.Popen(args, **kwargs)
    lifetime = None
    with _owned_process_lock:
        _owned_processes.add(process)
    try:
        if parent_lifetime:
            from .native_child import NativeChildLifetime
            lifetime = NativeChildLifetime(process)
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        if lifetime:
            lifetime.close()
        with _owned_process_lock:
            _owned_processes.discard(process)


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{Path(path).name} must contain a JSON object.")
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _hash_stream(stream)


def _hash_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _windows_process_paths(name):
    if os.name != "nt":
        return []
    # Fixed process names only; no user input reaches PowerShell source.
    script = f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -Unique | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        paths = json.loads(result.stdout or "[]")
        return [paths] if isinstance(paths, str) else (paths or [])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def discover_tool(name, explicit=None):
    env_name = {"blender": "AVATARFORGE_BLENDER", "unity": "AVATARFORGE_UNITY"}[name]
    candidates = [explicit, os.environ.get(env_name), os.environ.get(name.upper() + "_PATH")]
    selected = {str(item) for item in candidates if item}
    if name == "blender":
        candidates.extend((ROOT / ".runtime" / "blender").glob("**/blender.exe"))
    candidates.extend(_windows_process_paths("blender" if name == "blender" else "Unity"))
    command = shutil.which(name)
    # A shell alias named blender may launch an AI chat, not Blender.
    if command and Path(command).suffix.lower() not in {".bat", ".cmd", ".ps1"}:
        candidates.append(command)
    program = Path(os.environ.get("ProgramFiles", "/nonexistent"))
    if name == "unity":
        candidates.append(program / "Unity" / "Hub" / "Editor" / UNITY_VERSION / "Editor" / "Unity.exe")
    else:
        candidates.extend((program / "Blender Foundation").glob("Blender */blender.exe"))
        program_x86 = Path(os.environ.get("ProgramFiles(x86)", "/nonexistent"))
        candidates.append(program_x86 / "Steam" / "steamapps" / "common" / "Blender" / "blender.exe")
    if explicit:
        candidates = [explicit]
    for item in candidates:
        if item:
            path = Path(local_path(item))
            if path.is_file() and path.suffix.lower() not in {".bat", ".cmd", ".ps1"}:
                if name == "unity" and os.name == "nt" and unity_version(path) != UNITY_VERSION:
                    continue
                if name == "blender":
                    version = blender_version(path)
                    # Modern XPS requires Blender 5. Explicit selections retain
                    # the legacy/native 4.2+ route, including Collada in 4.5.
                    minimum = (4, 2, 0) if str(item) in selected else (5, 0, 0)
                    if version is None or version < minimum:
                        continue
                return str(path.resolve())
    return None


def blender_version(executable):
    try:
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        match = re.search(r"^Blender (\d+)\.(\d+)\.(\d+)", result.stdout, re.MULTILINE)
        return tuple(map(int, match.groups())) if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def unity_version(executable):
    """Read Windows executable metadata without launching/migrating a project."""
    if os.name != "nt":
        return None
    environment = os.environ.copy()
    environment["AVATARFORGE_VERSION_EXE"] = str(executable)
    script = "(Get-Item -LiteralPath $env:AVATARFORGE_VERSION_EXE).VersionInfo.ProductVersion"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=environment, capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        version = result.stdout.strip().split("_")[0]
        return version if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def doctor():
    catalog = read_json(ROOT / "avatarforge" / "dependencies.json") if (ROOT / "avatarforge" / "dependencies.json").exists() else {}
    addons = ROOT / ".runtime" / "addons"
    return {"blender": discover_tool("blender"), "unity": discover_tool("unity"), "unity_version": UNITY_VERSION,
            "presets": PRESETS, "formats": sorted(FORMATS), "addon_paths": [str(addons)] if addons.exists() else [],
            "dependencies": catalog, "no_ai_required": True}


def scan(source):
    path = Path(local_path(source))
    if not path.exists():
        raise ValueError("That model or folder does not exist.")
    if path.is_file():
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                models = [info.filename for info in archive.infolist() if Path(info.filename).suffix.lower() in FORMATS]
            return {"source": str(path), "archive": True, "models": models, "choose_after_extract": len(models) != 1}
        if path.suffix.lower() not in FORMATS:
            raise ValueError("Unsupported file. Select a model, its folder, or a ZIP archive.")
        files = [path]
    else:
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in FORMATS)
    return {"source": str(path), "archive": False, "models": [{"path": str(p), "name": p.relative_to(path).as_posix() if path.is_dir() else p.name, "format": p.suffix.lower(), "bytes": p.stat().st_size} for p in files], "choose_model": len(files) != 1}


def extract_zip(source, destination, max_bytes=8 * 1024 ** 3, max_files=50000):
    """Extract only regular files inside a new owned folder, including Windows paths."""
    destination = Path(local_path(destination))
    destination.mkdir(parents=True, exist_ok=False)
    dest_prefix = os.fspath(destination)
    if not dest_prefix.endswith(os.sep):
        dest_prefix += os.sep
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > max_files or sum(i.file_size for i in infos) > max_bytes:
            raise ValueError("Archive exceeds the 50,000 file / 8 GiB extraction limit.")
        seen = set()
        planned = []
        reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
        for info in infos:
            name = info.filename.replace("\\", "/")
            parts = name.split("/")
            if name.startswith("/") or any(p in {"..", "."} or ":" in p for p in parts) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive contains an unsafe path or symbolic link.")
            if any(p and (p.endswith((".", " ")) or p.split(".")[0].upper() in reserved) for p in parts):
                raise ValueError("Archive contains a reserved Windows filename.")
            key = name.rstrip("/").casefold()
            if key in seen and not info.is_dir():
                raise ValueError("Archive contains duplicate file paths.")
            seen.add(key)
            target_text = local_path(os.path.join(dest_prefix, name), root=destination)
            if not target_text.startswith(dest_prefix):
                raise ValueError("Archive path leaves extraction folder.")
            planned.append((info, target_text))
        for info, target_text in planned:
            if not target_text.startswith(dest_prefix):
                raise ValueError("Archive path leaves extraction folder.")
            if info.is_dir():
                os.makedirs(target_text, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target_text), exist_ok=True)
            with archive.open(info) as source_file, open(target_text, "wb") as handle:
                shutil.copyfileobj(source_file, handle, length=1024 * 1024)
    return scan(destination)


class Jobs:
    def __init__(self, output_root=None):
        self.output_root = Path(output_root or ROOT / "outputs").resolve()
        self.jobs = {}
        self.lock = threading.Lock()
        self.threads = []
        self.closed = False
        atexit.register(self.close)
        if self.output_root.exists():
            for folder in sorted(self.output_root.iterdir()):
                if not folder.is_dir():
                    continue
                receipt = folder / "receipt.json"
                pending = folder / "job.json"
                try:
                    job = read_json(receipt if receipt.exists() else pending)
                    if job.get("id") != folder.name:
                        continue
                    if not isinstance(job.get("output"), str):
                        continue
                    if Path(job["output"]).resolve() != folder.resolve():
                        if job.get("output_relative") != folder.name:
                            continue
                        job["output"] = str(folder.resolve())
                    previous_root = job.get("app_root")
                    if previous_root and previous_root != str(ROOT):
                        job["addon_paths"] = [str(ROOT / Path(p).relative_to(previous_root))
                                              if Path(p).is_relative_to(previous_root) else p
                                              for p in job.get("addon_paths", [])]
                        job["app_root"] = str(ROOT)
                    if job.get("state") in {"queued", "converting"}:
                        job.update(state="interrupted", error="The previous app session ended. Diagnostic output was kept; start a new conversion to retry.")
                    self.jobs[job["id"]] = job
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    def start(self, source, preset="preserve", options=None, blender=None):
        if self.closed:
            raise ValueError("The conversion service is closing.")
        if preset not in PRESETS:
            raise ValueError("Unknown optimization preset.")
        source = Path(local_path(source))
        if not source.is_file() or source.suffix.lower() not in FORMATS:
            raise ValueError("Choose one supported model file before converting.")
        executable = discover_tool("blender", blender)
        if not executable:
            raise ValueError("Blender was not found. Use Install tools or set AVATARFORGE_BLENDER to blender.exe.")
        if options is not None and not isinstance(options, dict):
            raise ValueError("options must be a JSON object.")
        options = dict(options or {})
        if "height" in options and not (0.1 <= float(options["height"]) <= 10):
            raise ValueError("Avatar height must be between 0.1 and 10 metres.")
        if "texture_size" in options and (not isinstance(options["texture_size"], int) or not 0 <= options["texture_size"] <= 8192):
            raise ValueError("Texture size must be an integer between 0 and 8192.")
        if "target_triangles" in options and (not isinstance(options["target_triangles"], int) or not 1 <= options["target_triangles"] <= 10000000):
            raise ValueError("Triangle target must be a positive integer, at most 10 million.")
        if preset == "preserve" and "target_triangles" in options:
            raise ValueError("Choose PC balanced or Mobile candidate to set a triangle target. Preserve keeps the original geometry.")
        extra_addons = options.get("addon_paths", [])
        if not isinstance(extra_addons, list) or any(not isinstance(p, str) or not Path(local_path(p)).is_dir() for p in extra_addons):
            raise ValueError("addon_paths must contain existing local addon folders.")
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        output = self.output_root / job_id
        if output.is_relative_to(source.parent) and source.parent == self.output_root:
            raise ValueError("Use a separate output folder.")
        output.mkdir(parents=True, exist_ok=False)
        job = {"id": job_id, "source": str(source), "output": str(output), "output_relative": job_id,
               "app_root": str(ROOT), "preset": preset, "options": options,
               "addon_paths": [str(ROOT / ".runtime" / "addons")] + [local_path(p) for p in extra_addons], "state": "queued", "started": time.time(), "log": "", "blender": executable}
        write_json(output / "job.json", job)
        with self.lock:
            self.jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job_id,), daemon=True)
        self.threads.append(thread)
        thread.start()
        return self.get(job_id)

    def _run(self, job_id):
        job = self.jobs[job_id]
        output = Path(job["output"])
        source = Path(job["source"])
        process = None
        try:
            original_hash = sha256(source)
            self._update(job_id, state="converting", source_sha256=original_hash)
            args = [job["blender"], "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", "--python", str(ROOT / "avatarforge" / "blender_worker.py"), "--", str(output / "job.json")]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            environment = os.environ.copy()
            for name, subdir in (("BLENDER_USER_CONFIG", "config"), ("BLENDER_USER_SCRIPTS", "scripts"), ("BLENDER_USER_EXTENSIONS", "extensions")):
                isolated = output / "blender-profile" / subdir
                isolated.mkdir(parents=True, exist_ok=True)
                environment[name] = str(isolated)
            with (output / "blender.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, cwd=output, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=environment)
                self._update(job_id, pid=process.pid)
                while process.poll() is None:
                    time.sleep(0.5)
                    if self.jobs[job_id].get("cancel_requested"):
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        self._update(job_id, state="cancelled", finished=time.time())
                        return
                code = process.returncode
            if sha256(source) != original_hash:
                raise RuntimeError("Source file changed during conversion. Check the source before proceeding.")
            report_path = output / "report.json"
            report = read_json(report_path) if report_path.exists() else None
            if report:
                self._update(job_id, report=report, source_unchanged=True)
            if code or not report or report.get("status") == "blocked" or not (output / "model.fbx").is_file():
                errors = [item.get("message", "") for item in (report or {}).get("issues", []) if item.get("severity") == "error"]
                detail = " ".join(errors[:3])
                raise RuntimeError(f"Blender conversion stopped (exit {code}). {detail} See the review items and blender.log.")
            self._update(job_id, state="complete", report=report, finished=time.time(), source_unchanged=True)
        except Exception as error:
            self._update(job_id, state="failed", error=str(error), finished=time.time())
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            write_json(output / "receipt.json", self.get(job_id))

    def _update(self, job_id, **fields):
        with self.lock:
            self.jobs[job_id].update(fields)
            snapshot = dict(self.jobs[job_id])
            write_json(Path(snapshot["output"]) / "job.json", snapshot)

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("Unknown job.")
            job = dict(self.jobs[job_id])
        if not job.get("report") and job.get("state") in {"failed", "interrupted"}:
            try:
                job["report"] = read_json(Path(job["output"]) / "report.json")
            except (OSError, ValueError):
                pass
        log_path = Path(job["output"]) / "blender.log"
        link_path = Path(job["output"]) / "unity-project.json"
        overrides_path = Path(job["output"]) / "unity-overrides.json"
        if overrides_path.is_file():
            try:
                job["approved_physics"] = read_json(overrides_path).get("approved_physics", [])
            except (OSError, ValueError):
                pass
        if link_path.is_file():
            try:
                job["unity_project"] = read_json(link_path)
            except (OSError, ValueError):
                pass
        report_path = Path(job["output"]) / "unity-report.json"
        if report_path.is_file():
            try:
                job["unity_report"] = read_json(report_path)
            except (OSError, ValueError):
                pass
        if log_path.exists():
            with log_path.open("rb") as stream:
                stream.seek(max(0, log_path.stat().st_size - 10000))
                job["log"] = stream.read().decode("utf-8", errors="replace")
        return job

    def cancel(self, job_id):
        job = self.get(job_id)
        if job["state"] in {"queued", "converting"}:
            self._update(job_id, cancel_requested=True)
        return self.get(job_id)

    def list(self):
        with self.lock:
            ids = list(self.jobs)
        return [self.get(i) for i in reversed(ids)]

    def close(self):
        """Cancel owned workers when the CLI, UI or MCP session closes normally."""
        self.closed = True
        with self.lock:
            for job in self.jobs.values():
                if job.get("state") in {"queued", "converting"}:
                    job["cancel_requested"] = True
        deadline = time.monotonic() + 12
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline - time.monotonic()))


def unity_environment():
    """Restore Windows' UPM profile path when an AI host filters its environment."""
    environment = os.environ.copy()
    if os.name == "nt" and any(not environment.get(key) for key in ("ALLUSERSPROFILE", "PROGRAMDATA")):
        path = environment.get("PROGRAMDATA") or environment.get("ALLUSERSPROFILE")
        if not path:
            import ctypes
            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x23, None, 0, buffer):  # CSIDL_COMMON_APPDATA
                raise ValueError("Windows could not locate ProgramData for Unity Package Manager.")
            path = buffer.value
        for key in ("ALLUSERSPROFILE", "PROGRAMDATA"):
            if not environment.get(key):
                environment[key] = path
    return environment


@contextmanager
def _unity_preparation_lock(folder):
    # The OS releases this lock if an AI client or worker exits unexpectedly.
    with (Path(folder) / ".unity-prepare.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            acquire()
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ValueError("Unity preparation is already running for this conversion.") from error
            raise
        yield


def _write_physics_approval(folder, bones):
    report = read_json(folder / "report.json")
    allowed = {p["bone"] for p in report.get("physics", [])}
    if not isinstance(bones, list) or any(b not in allowed for b in bones):
        raise ValueError("Choose only roots suggested by this conversion.")
    write_json(folder / "unity-overrides.json", {"approved_physics": bones})
    return {"approved": bones}


def approve_physics(input_folder, bones):
    folder = Path(input_folder).resolve()
    with _unity_preparation_lock(folder):
        return _write_physics_approval(folder, bones)


def prepare_unity(input_folder, project=None, approved_physics=None):
    """Create a fresh SDK project; serialize its conversion's mutable handoff files."""
    folder = Path(input_folder).resolve()
    if not (folder / "model.fbx").is_file() or not (folder / "report.json").is_file():
        raise ValueError("Choose a completed conversion folder.")
    with _unity_preparation_lock(folder):
        if approved_physics is not None:
            _write_physics_approval(folder, approved_physics)
        return _prepare_unity(folder, project)


def _prepare_unity(folder, project):
    executable = discover_tool("unity")
    if not executable:
        raise ValueError(f"Install Unity {UNITY_VERSION} through Unity Hub / VRChat Creator Companion.")
    vpm = ROOT / ".runtime" / "vpm" / "vpm.exe"
    if not vpm.exists():
        command = shutil.which("vpm")
        if not command:
            raise ValueError("Official VPM is not installed. Use Install tools > Unity project tools, or import the output with the included Unity package.")
        vpm = Path(command)
    # Keep SDK cache/resource paths short on Windows. Each explicit action creates a new project.
    destination = Path(local_path(project)) if project else Path(os.environ.get("USERPROFILE", str(folder))) / "AvatarForgeProjects" / folder.name[-8:]
    if destination.exists():
        if project:
            raise ValueError("Unity destination already exists. Choose a new project folder; existing projects are never overwritten.")
        destination = destination.with_name(destination.name + "-" + uuid.uuid4().hex[:6])
    log_path = folder / "unity-setup.log"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = unity_environment()
    private_dotnet = ROOT / ".runtime" / "dotnet"
    if (private_dotnet / "dotnet.exe").exists():
        environment["DOTNET_ROOT"] = str(private_dotnet)
        environment["DOTNET_ROOT_X64"] = str(private_dotnet)
        environment["PATH"] = str(private_dotnet) + os.pathsep + environment.get("PATH", "")
    with log_path.open("w", encoding="utf-8") as log:
        for args in ([str(vpm), "install", "templates"], [str(vpm), "new", destination.name, "Avatar", "-p", str(destination.parent)]):
            result = run_owned(args, stdout=log, stderr=subprocess.STDOUT, timeout=600, creationflags=flags, env=environment)
            if result.returncode:
                raise RuntimeError(f"VPM stopped (exit {result.returncode}). See unity-setup.log.")
        # VPM's official template can lag behind the SDK. Pin the tested stable
        # SDK on this newly created project; do not edit the user's templates.
        catalog = read_json(ROOT / "avatarforge" / "dependencies.json")["dependencies"]
        sdk_version = catalog["vrchat_sdk"]["version"]
        vpm_manifest_path = destination / "Packages" / "vpm-manifest.json"
        vpm_manifest = read_json(vpm_manifest_path)
        for package_name in ("com.vrchat.base", "com.vrchat.avatars"):
            vpm_manifest.setdefault("dependencies", {})[package_name] = {"version": sdk_version}
            vpm_manifest.setdefault("locked", {}).pop(package_name, None)
        write_json(vpm_manifest_path, vpm_manifest)
        result = run_owned([str(vpm), "resolve", "project", str(destination)], stdout=log, stderr=subprocess.STDOUT, timeout=600, creationflags=flags, env=environment)
        if result.returncode:
            raise RuntimeError(f"VPM SDK resolve stopped (exit {result.returncode}). See unity-setup.log.")
        # Resolve can leave an older, already-present template payload. The
        # official add command refreshes the payload. Pin both packages using
        # VPM's package@version syntax so a later upstream release cannot
        # silently change a fresh installation of this reviewed tool version.
        for package_name in ("com.vrchat.avatars", "com.vrchat.base"):
            result = run_owned([str(vpm), "add", "package", f"{package_name}@{sdk_version}", "-p", str(destination)], stdout=log, stderr=subprocess.STDOUT, timeout=600, creationflags=flags, env=environment)
            if result.returncode:
                raise RuntimeError(f"VPM SDK installation stopped (exit {result.returncode}). See unity-setup.log.")
        for package_name in ("com.vrchat.base", "com.vrchat.avatars"):
            metadata = read_json(destination / "Packages" / package_name / "package.json")
            if metadata.get("version") != sdk_version:
                raise RuntimeError(f"Official VPM installed {package_name} {metadata.get('version')}, but this AvatarForge version was validated with {sdk_version}. Update AvatarForge's reviewed dependency pins before continuing.")
        manifest_path = destination / "Packages" / "manifest.json"
        manifest = read_json(manifest_path)
        package = ROOT / "unity" / "Packages" / "dev.senjuwoo.avatarforge"
        shutil.copytree(package, destination / "Packages" / "dev.senjuwoo.avatarforge")
        manifest.setdefault("dependencies", {})["dev.senjuwoo.avatarforge"] = "file:dev.senjuwoo.avatarforge"
        report = read_json(folder / "report.json")
        if report.get("preset") in {"balanced", "mobile"}:
            # Use only the reviewed managed reducer and supporting packages.
            # An older optional native reducer may remain installed locally;
            # copying every directory would silently reactivate it.
            installer = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tools" / "Install.ps1"), "-Component", "optimization"]
            result = run_owned(installer, stdout=log, stderr=subprocess.STDOUT, timeout=900, creationflags=flags, env=environment)
            if result.returncode:
                raise RuntimeError("Optimization tool setup stopped. The new project was kept. See unity-setup.log.")
            for dependency_id in ("localization", "ndmf", "modular_avatar", "unity_mesh_simplifier"):
                entry = catalog[dependency_id]
                package_name = entry["unity_package"]
                package_path = ROOT / ".runtime" / "unity-packages" / package_name
                metadata = read_json(package_path / "package.json")
                if metadata.get("name") != package_name or metadata.get("version") != entry["version"]:
                    raise RuntimeError(f"Installed {dependency_id} does not match the reviewed package pin. Run Install tools again.")
                shutil.copytree(package_path, destination / "Packages" / package_name)
                manifest["dependencies"][package_name] = "file:" + package_name
                for dependency, version in metadata.get("dependencies", {}).items():
                    manifest["dependencies"].setdefault(dependency, version)
        write_json(manifest_path, manifest)
        # Keep a failed import reviewable in its owned project. A project link
        # records location/progress; only this invocation's verdict proves import.
        link_path = folder / "unity-project.json"
        report_path = folder / "unity-report.json"
        write_json(link_path, {"project": str(destination), "import_state": "running"})
        try:
            if report_path.exists():
                report_path.rename(folder / ("unity-report.previous-" + uuid.uuid4().hex[:8] + ".json"))
            result = run_owned([executable, "-batchmode", "-nographics", "-quit", "-projectPath", str(destination), "-executeMethod", "AvatarForge.Editor.AvatarForgeImporter.Batch", "-avatarForgeInput", str(folder), "-logFile", str(folder / "unity.log")], timeout=1800, stdout=log, stderr=subprocess.STDOUT, creationflags=flags, env=environment)
            if result.returncode:
                raise RuntimeError(f"Unity stopped (exit {result.returncode}). The project was kept for repair at {destination}. See unity.log.")
            if not report_path.exists():
                raise RuntimeError("Unity did not emit a new import verdict. The project was kept for repair. See unity.log.")
            unity_report = read_json(report_path)
            if unity_report.get("status") not in {"ready", "needs_review", "blocked"}:
                raise RuntimeError("Unity emitted an invalid import verdict. See unity-report.json and unity.log.")
            if unity_report["status"] != "blocked":
                for key in ("prefab", "scene"):
                    asset = unity_report.get(key)
                    if not isinstance(asset, str) or not asset.startswith("Assets/AvatarForge/") or not (destination / asset).resolve().is_relative_to(destination) or not (destination / asset).is_file():
                        raise RuntimeError("Unity did not save a usable prefab and preview scene. The project was kept for repair. See unity.log.")
            if unity_report["status"] == "blocked":
                raise RuntimeError("Unity import is blocked. Open the kept project to repair the reported issues, then reimport the conversion folder.")
        except subprocess.TimeoutExpired as error:
            write_json(link_path, {"project": str(destination), "import_state": "timed_out"})
            raise RuntimeError("Unity import exceeded 30 minutes; no completed prefab/scene verdict was received. The project and unity.log were kept. Open the project, let its import finish, then choose AvatarForge > Import conversion folder to complete preparation.") from error
        except Exception:
            state = "failed"
            try:
                if report_path.exists() and read_json(report_path).get("status") == "blocked":
                    state = "blocked"
            except (OSError, ValueError):
                pass
            write_json(link_path, {"project": str(destination), "import_state": state})
            raise
    write_json(link_path, {"project": str(destination), "import_state": "complete", "import_status": unity_report["status"]})
    return {"project": str(destination), "report": unity_report}
