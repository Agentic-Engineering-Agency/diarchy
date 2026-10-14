from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDER = ROOT / "client" / "diarchy-file-portal-send"
RECEIVER = ROOT / "client" / "diarchy-file-portal-receiver"


class PortalArgumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / "probe-ran"
        route = self.root / "route.sh"
        route.write_text(
            f"select_diarchy_route() {{ : > '{self.marker}'; exit 3; }}\n",
            encoding="utf-8",
        )
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.root),
            "DIARCHY_FILE_ROUTE_SH": str(route),
            "SSH_BIN": "/nonexistent-ssh",
        }

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_unknown_flag_prints_usage_and_exits_2_without_route_probe(self) -> None:
        for script in (SENDER, RECEIVER):
            with self.subTest(script=script.name):
                result = self.run_script(script, "--bogus")
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
                self.assertFalse(self.marker.exists())

    def test_help_prints_usage_and_exits_0_without_route_probe(self) -> None:
        for script in (SENDER, RECEIVER):
            with self.subTest(script=script.name):
                result = self.run_script(script, "--help")
                self.assertEqual(result.returncode, 0)
                self.assertIn("usage:", result.stdout)
                self.assertFalse(self.marker.exists())

    def test_receiver_rejects_positional_arguments(self) -> None:
        result = self.run_script(RECEIVER, "extra")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.marker.exists())

    def test_sender_still_accepts_dash_dash_before_dash_prefixed_path(self) -> None:
        result = self.run_script(SENDER, "--", "-odd-name")
        self.assertTrue(self.marker.exists())
        self.assertNotEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
