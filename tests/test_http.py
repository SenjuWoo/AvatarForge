"""Real loopback service boundary and UI-resource checks."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from avatarforge.core import ROOT


class LoopbackBoundaryChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "ui", "--no-browser"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        url = cls.process.stdout.readline().strip()
        parsed = urlsplit(url)
        cls.base = f"http://{parsed.netloc}"
        cls.token = parse_qs(parsed.fragment)["token"][0]

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.communicate(timeout=10)

    def post(self, method, token=None, origin=None, host=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        return urlopen(Request(self.base + "/api/" + method, data=b"{}", headers=headers), timeout=20)

    def test_mutation_api_requires_session_token(self):
        with self.assertRaises(HTTPError) as error:
            self.post("jobs")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.post("jobs", self.token) as response:
            self.assertIsInstance(json.load(response), list)

    def test_external_origin_and_rebinding_host_are_rejected(self):
        for headers in ({"origin": "https://example.com"}, {"host": "attacker.example"}):
            with self.assertRaises(HTTPError) as error:
                self.post("jobs", self.token, **headers)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_actual_ui_assets_exist_and_have_security_headers(self):
        for file, marker in (("/", b"AvatarForge"), ("/app.js", b"refreshHistory"), ("/style.css", b"--accent")):
            with urlopen(self.base + file, timeout=10) as response:
                self.assertIn(marker, response.read())
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
