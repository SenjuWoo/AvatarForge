"""Exercise the real Windows installer as Python invokes it, with inherited PS7 paths."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell installer")
class InstallerChildProcessTests(unittest.TestCase):
    def test_incompatible_inherited_module_path_does_not_break_installer(self):
        with tempfile.TemporaryDirectory(prefix="AvatarForgePSModules-") as temporary:
            folder = Path(temporary) / "Microsoft.PowerShell.Utility"
            folder.mkdir()
            (folder / "Microsoft.PowerShell.Utility.psd1").write_text(
                "@{ModuleVersion='7.0.0';PowerShellVersion='7.0';"
                "RootModule='Utility.psm1';FunctionsToExport=@('Get-FileHash')}\n",
                encoding="utf-8",
            )
            (folder / "Utility.psm1").write_text(
                "function Get-FileHash { throw 'Incompatible module was loaded' }\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PSModulePath"] = temporary + os.pathsep + environment.get("PSModulePath", "")
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "tests" / "test_installer.ps1")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("AVATARFORGE_INSTALLER_TESTS_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
