from __future__ import annotations

import unittest

from tests.test_diarchy_host_kvm import KvmCase, UNIT_KVM
from tests.test_diarchy_host_support import FP_A, FP_B

FP_C = ":".join(["CC"] * 32)


class SecondMacAuthorizeTests(KvmCase):
    """B-31: authorizing a second Mac must not silently remove the first one."""

    def test_second_authorize_keeps_the_first_fingerprint(self) -> None:
        self.fake_systemctl()
        self.run_ok("kvm", "authorize", "--fingerprint", FP_A)
        body = self.run_ok("kvm", "authorize", "--fingerprint", FP_B)
        self.assertEqual(body["authorized"], FP_B)
        self.assertEqual(self.entry_lines(), [f'"{FP_A.lower()}" = "mac"', f'"{FP_B.lower()}" = "mac"'])

    def test_authorizing_the_same_fingerprint_twice_writes_one_entry(self) -> None:
        self.fake_systemctl()
        self.run_ok("kvm", "authorize", "--fingerprint", FP_A)
        self.run_ok("kvm", "authorize", "--fingerprint", FP_A.lower())
        self.assertEqual(self.entry_lines(), [f'"{FP_A.lower()}" = "mac"'])

    def test_configure_keeps_every_authorized_fingerprint(self) -> None:
        self.fake_systemctl()
        for fingerprint in (FP_A, FP_B, FP_C):
            self.run_ok("kvm", "authorize", "--fingerprint", fingerprint)
        self.run_ok("kvm", "configure")
        self.assertEqual(
            self.entry_lines(),
            [f'"{fp.lower()}" = "mac"' for fp in (FP_A, FP_B, FP_C)],
        )


class SystemctlFailureTextTests(KvmCase):
    """B-23 (kvm.py): the systemctl return code is checked and its stderr reaches the error text."""

    def test_failed_start_reports_the_systemctl_stderr(self) -> None:
        self.install_unit_file()
        self.stub_systemctl(
            "if argv[:2] == ['--user', 'start']:\n"
            "    sys.stderr.write('Unit diarchy-kvm.service has a bad setting\\n')\n"
            "    sys.exit(1)\n"
        )
        body = self.run_error("service_failed", 1, "kvm", "start")
        self.assertIn(f"systemctl --user start {UNIT_KVM} failed", body["error"]["message"])
        self.assertIn("has a bad setting", body["error"]["message"])

    def test_failed_restart_after_authorize_reports_the_systemctl_stderr(self) -> None:
        self.stub_systemctl(
            "if 'show' in argv:\n"
            "    print('active')\n"
            "elif argv[:2] == ['--user', 'restart']:\n"
            "    sys.stderr.write('Failed to restart: connection refused\\n')\n"
            "    sys.exit(1)\n"
        )
        body = self.run_error("service_failed", 1, "kvm", "authorize", "--fingerprint", FP_A)
        self.assertIn("connection refused", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
