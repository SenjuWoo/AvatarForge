import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows native child lifetime')
class NativeChildTests(unittest.TestCase):
    def test_abrupt_parent_exit_stops_only_assigned_child(self):
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        control = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        parent = None
        child_handle = None
        try:
            with tempfile.TemporaryDirectory(prefix='AvatarForge native lifetime – ') as folder:
                pid_file = Path(folder) / 'child.json'
                child = "import json,os,time;from pathlib import Path;Path(" + repr(str(pid_file)) + ").write_text(json.dumps({'pid':os.getpid()}));time.sleep(60)"
                parent_code = ('import sys;sys.path.insert(0,' + repr(str(Path(__file__).resolve().parents[1])) + ');'
                               'from avatarforge.core import run_owned;run_owned([sys.executable,"-c",' + repr(child) + '],parent_lifetime=True)')
                parent = subprocess.Popen([sys.executable, '-c', parent_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                deadline = time.monotonic() + 10
                while not pid_file.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(pid_file.exists(), 'Owned child failed to start')
                child_pid = json.loads(pid_file.read_text())['pid']
                child_handle = api.OpenProcess(0x100000, False, child_pid)  # SYNCHRONIZE only
                self.assertTrue(child_handle)
                self.assertEqual(api.WaitForSingleObject(child_handle, 0), 0x102)
                parent.terminate()
                parent.wait(timeout=10)
                self.assertEqual(api.WaitForSingleObject(child_handle, 5000), 0, 'Child survived abrupt parent exit')
                self.assertIsNone(control.poll(), 'Unrelated process was terminated')
        finally:
            if child_handle:
                api.CloseHandle(child_handle)
            if parent:
                if parent.poll() is None:
                    parent.terminate()
                parent.communicate(timeout=10)
            control.terminate()
            control.wait(timeout=10)


    def test_browse_uses_real_process_api_and_utf8_result(self):
        from unittest.mock import patch
        from avatarforge.app import Service
        real_popen = subprocess.Popen
        selected = 'C:/Models with spaces/模型.blend'
        command = [sys.executable, '-c', 'import sys;sys.stdout.reconfigure(encoding="utf-8");print(' + repr(selected) + ')']
        # Keep the real Popen argument contract, pipes, wait and native lifetime;
        # replace only the interactive dialog with a controlled CLI child in CI.
        def spawn(args, **kwargs):
            self.assertEqual(args[0], 'powershell.exe')
            return real_popen(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix='AvatarForge picker API – ') as folder:
            service = Service(folder)
            try:
                with patch('avatarforge.core.subprocess.Popen', side_effect=spawn):
                    self.assertEqual(service.browse('file'), {'path': selected})
                    self.assertEqual(service.browse('folder'), {'path': selected})
            finally:
                service.jobs.close()
