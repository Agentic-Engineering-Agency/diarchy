from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT_TOOL = ROOT / "client" / "diarchy-mac-fingerprint"
FP_UPPER = ":".join(["AB", "CD", "EF", "01"] * 8)
FP_LOWER = FP_UPPER.lower()


class MacFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            FINGERPRINT_TOOL.exists(), "client/diarchy-mac-fingerprint must exist"
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.cert = self.home / "lan-mouse.pem"
        self.log = self.home / "openssl.log"

    def fake_openssl(self, *, stdout: str, exit_code: int = 0) -> Path:
        script = self.home / "openssl"
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{self.log}'\n"
            f"printf '%s\\n' '{stdout}'\n"
            f"exit {exit_code}\n"
        )
        script.chmod(0o755)
        return script

    def run_tool(
        self,
        openssl: Path,
        *,
        extra_env: dict[str, str] | None = None,
        unset: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "LAN_MOUSE_CERT": str(self.cert),
            "OPENSSL_BIN": str(openssl),
        }
        if extra_env:
            env.update(extra_env)
        for key in unset:
            env.pop(key, None)
        return subprocess.run(
            [str(FINGERPRINT_TOOL)], env=env, text=True, capture_output=True, check=False
        )

    def test_parses_libressl_output(self) -> None:
        self.cert.write_text("pem")
        openssl = self.fake_openssl(stdout=f"SHA256 Fingerprint={FP_LOWER}")
        result = self.run_tool(openssl)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, FP_UPPER + "\n")
        self.assertEqual(
            self.log.read_text(), f"x509 -in {self.cert} -noout -fingerprint -sha256\n"
        )

    def test_parses_openssl_output(self) -> None:
        self.cert.write_text("pem")
        openssl = self.fake_openssl(stdout=f"sha256 Fingerprint={FP_UPPER}")
        result = self.run_tool(openssl)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, FP_UPPER + "\n")

    def test_missing_certificate(self) -> None:
        openssl = self.fake_openssl(stdout=f"sha256 Fingerprint={FP_UPPER}")
        result = self.run_tool(openssl)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "diarchy-mac-fingerprint: Lan Mouse certificate not found; start Lan Mouse once\n",
        )
        self.assertFalse(self.log.exists())

    def test_openssl_failure_is_invalid_certificate(self) -> None:
        self.cert.write_text("pem")
        openssl = self.fake_openssl(stdout="unable to load certificate", exit_code=1)
        result = self.run_tool(openssl)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr, "diarchy-mac-fingerprint: Lan Mouse certificate is invalid\n"
        )

    def test_garbage_output_is_invalid_certificate(self) -> None:
        self.cert.write_text("pem")
        for garbage in ("", "no equals sign here", "sha256 Fingerprint=AB:CD", "x=zz"):
            with self.subTest(garbage=garbage):
                openssl = self.fake_openssl(stdout=garbage)
                result = self.run_tool(openssl)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr,
                    "diarchy-mac-fingerprint: Lan Mouse certificate is invalid\n",
                )

    def test_default_certificate_path_uses_xdg_config_home(self) -> None:
        xdg = self.home / "xdg"
        (xdg / "lan-mouse").mkdir(parents=True)
        (xdg / "lan-mouse" / "lan-mouse.pem").write_text("pem")
        openssl = self.fake_openssl(stdout=f"sha256 Fingerprint={FP_UPPER}")
        result = self.run_tool(
            openssl, extra_env={"XDG_CONFIG_HOME": str(xdg)}, unset=("LAN_MOUSE_CERT",)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, FP_UPPER + "\n")

    def test_default_certificate_path_uses_home_config(self) -> None:
        (self.home / ".config" / "lan-mouse").mkdir(parents=True)
        (self.home / ".config" / "lan-mouse" / "lan-mouse.pem").write_text("pem")
        openssl = self.fake_openssl(stdout=f"sha256 Fingerprint={FP_UPPER}")
        result = self.run_tool(openssl, unset=("LAN_MOUSE_CERT",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, FP_UPPER + "\n")

    def test_does_not_require_diarchy_config(self) -> None:
        self.cert.write_text("pem")
        openssl = self.fake_openssl(stdout=f"sha256 Fingerprint={FP_UPPER}")
        result = self.run_tool(
            openssl, extra_env={"DIARCHY_CONFIG": str(self.home / "absent.conf")}
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
