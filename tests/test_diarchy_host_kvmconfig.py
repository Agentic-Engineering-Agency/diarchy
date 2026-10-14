from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_diarchy_host_support import FP_A, FP_B, HOST

sys.path.insert(0, str(HOST))
from diarchy_host import kvmconfig  # noqa: E402
from diarchy_host.common import AgentError  # noqa: E402

HEADER_BLOCK = (
    "# Managed by diarchy\n"
    'release_bind = ["KeyA", "KeyS", "KeyD", "KeyF"]\n'
    'capture_backend = "layer-shell"\n'
    "port = 4242\n"
    "\n"
    "[authorized_fingerprints]\n"
)
UNMANAGED = (
    'port = 4242\n\n[[clients]]\nhostname = "desk"\n\n'
    f'[authorized_fingerprints]\n"{FP_B.lower()}" = "other"\n'
)
MIXED_FP = ":".join(["aA"] * 32)


class KvmConfigCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("LAN_MOUSE_CONFIG", "XDG_CONFIG_HOME"):
            os.environ.pop(name, None)
        self.config_dir = self.home / ".config" / "lan-mouse"
        self.config = self.config_dir / "config.toml"

    def prepare(self, text: str | bytes) -> None:
        self.config_dir.mkdir(parents=True)
        if isinstance(text, bytes):
            self.config.write_bytes(text)
        else:
            self.config.write_text(text)

    def siblings(self) -> list[str]:
        return sorted(p.name for p in self.config_dir.iterdir()) if self.config_dir.exists() else []

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)


class NormalizeFingerprintTests(unittest.TestCase):
    def test_accepts_any_case_and_returns_uppercase(self) -> None:
        self.assertEqual(kvmconfig.normalize_fingerprint(FP_A.lower()), FP_A)
        self.assertEqual(kvmconfig.normalize_fingerprint(MIXED_FP), FP_A)
        self.assertEqual(kvmconfig.normalize_fingerprint(FP_B), FP_B)

    def test_rejects_malformed_values(self) -> None:
        bad = [
            "",
            ":".join(["AA"] * 31),
            ":".join(["AA"] * 33),
            ":".join(["AA"] * 31 + ["ZZ"]),
            "AA" * 32,
            FP_A + "\n",
            FP_A + " ",
            " " + FP_A,
            FP_A.replace(":", "-"),
            ":".join(["AAA"] + ["AA"] * 31),
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(AgentError) as caught:
                    kvmconfig.normalize_fingerprint(value)
                self.assertEqual(caught.exception.code, "invalid_fingerprint")
                self.assertEqual(
                    caught.exception.message, "fingerprint must be 32 colon-separated hex bytes"
                )


class ConfigPathTests(KvmConfigCase):
    def test_default_xdg_and_explicit_override(self) -> None:
        self.assertEqual(kvmconfig.config_path(), self.config)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "xdg")}):
            self.assertEqual(kvmconfig.config_path(), self.root / "xdg" / "lan-mouse" / "config.toml")
            with mock.patch.dict(os.environ, {"LAN_MOUSE_CONFIG": str(self.root / "x.toml")}):
                self.assertEqual(kvmconfig.config_path(), self.root / "x.toml")


class RenderTests(unittest.TestCase):
    def test_render_without_fingerprint(self) -> None:
        self.assertEqual(kvmconfig.render(None), HEADER_BLOCK)

    def test_render_with_fingerprint_is_lowercase(self) -> None:
        self.assertEqual(kvmconfig.render(FP_A), HEADER_BLOCK + f'"{FP_A.lower()}" = "mac"\n')

    def test_render_lowercases_mixed_case_input(self) -> None:
        self.assertEqual(kvmconfig.render(MIXED_FP), kvmconfig.render(FP_A))


class WriteTests(KvmConfigCase):
    def test_creates_dir_0700_file_0600_with_rendered_content(self) -> None:
        kvmconfig.write(FP_A)
        self.assertEqual(self.mode(self.config_dir), 0o700)
        self.assertEqual(self.mode(self.config), 0o600)
        self.assertEqual(self.config.read_text(), kvmconfig.render(FP_A))

    def test_write_none_produces_empty_authorized_table(self) -> None:
        kvmconfig.write(None)
        self.assertEqual(self.config.read_text(), HEADER_BLOCK)

    def test_rewriting_managed_file_leaves_no_backup_or_temp(self) -> None:
        kvmconfig.write(FP_A)
        kvmconfig.write(FP_B)
        kvmconfig.write(None)
        self.assertEqual(self.siblings(), ["config.toml"])
        self.assertEqual(self.config.read_text(), kvmconfig.render(None))

    def test_unmanaged_file_is_backed_up_byte_for_byte(self) -> None:
        original = UNMANAGED.encode() + b"\xff\xfe raw bytes\r\n"
        self.prepare(original)
        self.config.chmod(0o644)
        kvmconfig.write(FP_A)
        backups = [n for n in self.siblings() if n.startswith("config.toml.backup.")]
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0], r"^config\.toml\.backup\.\d{8}T\d{6}Z$")
        backup = self.config_dir / backups[0]
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(self.mode(backup), 0o600)
        self.assertEqual(self.config.read_text(), kvmconfig.render(FP_A))
        self.assertEqual(self.mode(self.config), 0o600)
        self.assertEqual(self.siblings(), ["config.toml", backups[0]])

    def test_unmanaged_file_with_managed_header_not_on_first_line_is_backed_up(self) -> None:
        self.prepare("\n# Managed by diarchy\nport = 1\n")
        kvmconfig.write(None)
        self.assertEqual(len([n for n in self.siblings() if ".backup." in n]), 1)

    def test_empty_unmanaged_file_is_backed_up(self) -> None:
        self.prepare("")
        kvmconfig.write(None)
        self.assertEqual(len([n for n in self.siblings() if ".backup." in n]), 1)

    def test_invalid_fingerprint_is_rejected_before_any_side_effect(self) -> None:
        for value in (":".join(["AA"] * 31), "ZZ:" * 31 + "ZZ", FP_A + "\n"):
            with self.subTest(value=value):
                with self.assertRaises(AgentError) as caught:
                    kvmconfig.write(value)
                self.assertEqual(caught.exception.code, "invalid_fingerprint")
                self.assertFalse(self.config_dir.exists())

    def test_invalid_fingerprint_does_not_back_up_or_touch_existing_file(self) -> None:
        self.prepare(UNMANAGED)
        with self.assertRaises(AgentError):
            kvmconfig.write("nope")
        self.assertEqual(self.siblings(), ["config.toml"])
        self.assertEqual(self.config.read_text(), UNMANAGED)

    def test_honours_lan_mouse_config_override(self) -> None:
        target = self.root / "custom" / "nested" / "lm.toml"
        with mock.patch.dict(os.environ, {"LAN_MOUSE_CONFIG": str(target)}):
            kvmconfig.write(FP_A)
        self.assertEqual(target.read_text(), kvmconfig.render(FP_A))
        self.assertFalse(self.config_dir.exists())


class WriteFailureTests(KvmConfigCase):
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores directory modes")
    def test_read_only_dir_raises_and_keeps_original(self) -> None:
        self.prepare(UNMANAGED)
        self.config_dir.chmod(0o500)
        self.addCleanup(self.config_dir.chmod, 0o700)
        with mock.patch("diarchy_host.kvmconfig.os.chmod"), self.assertRaises(AgentError) as caught:  # keep dir read-only
            kvmconfig.write(FP_A)
        self.assertEqual(caught.exception.code, "config_write_failed")
        self.assertEqual(self.config.read_text(), UNMANAGED)
        self.assertEqual(self.siblings(), ["config.toml"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores directory modes")
    def test_read_only_dir_with_managed_file(self) -> None:
        kvmconfig.write(FP_A)
        before = self.config.read_text()
        self.config_dir.chmod(0o500)
        self.addCleanup(self.config_dir.chmod, 0o700)
        with mock.patch("diarchy_host.kvmconfig.os.chmod"), self.assertRaises(AgentError) as caught:  # keep dir read-only
            kvmconfig.write(FP_B)
        self.assertEqual(caught.exception.code, "config_write_failed")
        self.assertEqual(self.config.read_text(), before)

    def test_chmod_failure_is_config_write_failed(self) -> None:
        with mock.patch("diarchy_host.kvmconfig.os.chmod", side_effect=PermissionError(1, "denied")):
            with self.assertRaises(AgentError) as caught:
                kvmconfig.write(FP_A)
        self.assertEqual(caught.exception.code, "config_write_failed")
        self.assertFalse(self.config.exists())

    def test_replace_failure_leaves_no_temp_and_keeps_original(self) -> None:
        self.prepare("# Managed by diarchy\nold = 1\n")
        with mock.patch("diarchy_host.kvmconfig.os.replace", side_effect=OSError(5, "boom")):
            with self.assertRaises(AgentError) as caught:
                kvmconfig.write(FP_A)
        self.assertEqual(caught.exception.code, "config_write_failed")
        self.assertEqual(self.config.read_text(), "# Managed by diarchy\nold = 1\n")
        self.assertEqual(self.siblings(), ["config.toml"])

    def test_fsync_failure_leaves_no_temp_and_keeps_original(self) -> None:
        self.prepare(UNMANAGED)
        real_fsync = os.fsync
        calls: list[int] = []

        def failing_second(fd: int) -> None:
            calls.append(fd)
            if len(calls) == 2:  # first call durably stores the backup, second is the temp file
                raise OSError(5, "boom")
            real_fsync(fd)

        with mock.patch("diarchy_host.kvmconfig.os.fsync", side_effect=failing_second):
            with self.assertRaises(AgentError) as caught:
                kvmconfig.write(FP_A)
        self.assertEqual(caught.exception.code, "config_write_failed")
        self.assertEqual(self.config.read_text(), UNMANAGED)
        backups = [n for n in self.siblings() if ".backup." in n]
        self.assertEqual(len(backups), 1)
        self.assertEqual((self.config_dir / backups[0]).read_text(), UNMANAGED)
        self.assertEqual(self.siblings(), ["config.toml", backups[0]])

    def test_config_path_that_is_a_directory_raises_agent_error(self) -> None:
        self.config.mkdir(parents=True)
        with self.assertRaises(AgentError) as caught:
            kvmconfig.write(FP_A)
        self.assertEqual(caught.exception.code, "config_write_failed")


class ReadAuthorizedTests(KvmConfigCase):
    def test_managed_with_one_fingerprint(self) -> None:
        kvmconfig.write(FP_A)
        self.assertEqual(kvmconfig.read_authorized(), (True, [FP_A]))

    def test_managed_empty(self) -> None:
        kvmconfig.write(None)
        self.assertEqual(kvmconfig.read_authorized(), (True, []))

    def test_unmanaged_with_fingerprint(self) -> None:
        self.prepare(UNMANAGED)
        self.assertEqual(kvmconfig.read_authorized(), (False, [FP_B]))

    def test_missing_file(self) -> None:
        self.assertEqual(kvmconfig.read_authorized(), (False, []))

    def test_unreadable_file(self) -> None:
        self.config.mkdir(parents=True)
        self.assertEqual(kvmconfig.read_authorized(), (False, []))

    def test_non_utf8_file_is_treated_as_unreadable(self) -> None:
        self.prepare(b"\xff\xfe\x00bad")
        self.assertEqual(kvmconfig.read_authorized(), (False, []))

    def test_explicit_path_argument(self) -> None:
        other = self.root / "other.toml"
        other.write_text(kvmconfig.render(FP_B))
        self.assertEqual(kvmconfig.read_authorized(other), (True, [FP_B]))

    def test_ignores_lines_outside_section_and_malformed_names(self) -> None:
        self.prepare(
            "# Managed by diarchy\n"
            f'"{FP_A.lower()}" = "before-section"\n'
            "[authorized_fingerprints]\n"
            f'"{FP_B.lower()}" = "ok"\n'
            f'"{FP_A.lower()}" = mac\n'
            f'"{FP_A.lower()}" = "unterminated\n'
            f'{FP_A.lower()} = "unquoted-key"\n'
            '"not-a-fingerprint" = "x"\n'
            f'"{":".join(["aa"] * 31)}" = "short"\n'
            f'# "{FP_A.lower()}" = "commented"\n'
            "[[clients]]\n"
            f'"{FP_A.lower()}" = "after-section"\n'
        )
        self.assertEqual(kvmconfig.read_authorized(), (True, [FP_B]))

    def test_case_insensitive_read_returns_uppercase(self) -> None:
        for key in (FP_A.lower(), FP_A, MIXED_FP):
            with self.subTest(key=key):
                self.config_dir.mkdir(parents=True, exist_ok=True)
                self.config.write_text(
                    "# Managed by diarchy\n[authorized_fingerprints]\n" + f'"{key}" = "mac"\n')
                self.assertEqual(kvmconfig.read_authorized(), (True, [FP_A]))
                self.assertEqual(kvmconfig.preserved_fingerprints(), [FP_A])

    def test_tolerates_whitespace_and_trailing_comment(self) -> None:
        self.prepare(
            "# Managed by diarchy\n[authorized_fingerprints]\n"
            f'  "{FP_A.lower()}"   =   "mac"   # laptop\n'
        )
        self.assertEqual(kvmconfig.read_authorized(), (True, [FP_A]))


class PreservedFingerprintTests(KvmConfigCase):
    def test_managed_with_fingerprint(self) -> None:
        kvmconfig.write(FP_A)
        self.assertEqual(kvmconfig.preserved_fingerprints(), [FP_A])

    def test_managed_empty(self) -> None:
        kvmconfig.write(None)
        self.assertEqual(kvmconfig.preserved_fingerprints(), [])

    def test_unmanaged(self) -> None:
        self.prepare(UNMANAGED)
        self.assertEqual(kvmconfig.preserved_fingerprints(), [])

    def test_missing(self) -> None:
        self.assertEqual(kvmconfig.preserved_fingerprints(), [])


if __name__ == "__main__":
    unittest.main()
