import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "check-public-tree")


def run_gate(tree):
    return subprocess.run([SCRIPT, tree], capture_output=True, text=True)


class CheckPublicTreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_rfc1918_address_fails_with_location_only(self):
        self.write("notes.md", "ok line\nhost 10.9" ".8.7 here\n")
        r = run_gate(self.root)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "notes.md:2: rfc1918-ipv4")
        self.assertNotIn("10.9" ".8.7", r.stdout + r.stderr)

    def test_each_kind_is_reported(self):
        cases = {
            "192.1" "68.1.5": "rfc1918-ipv4",
            "172.2" "0.0.1": "rfc1918-ipv4",
            "100.1" "00.1.2": "cgnat-ipv4",
            "aa:bb:cc" ":dd:ee:ff": "mac-address",
            "/Us" "ers/alice/Documents": "users-path",
            "/ho" "me/alice/bin": "home-path",
            "box.tail1234.t" "s.net": "tailnet-name",
            "alice@" "corp.io": "email",
        }
        for value, kind in cases.items():
            with self.subTest(kind=kind):
                self.write("f.txt", f"x {value} y\n")
                r = run_gate(self.root)
                self.assertEqual(r.returncode, 1)
                self.assertIn(kind, r.stdout)
                self.assertNotIn(value, r.stdout)

    def test_documentation_values_pass(self):
        self.write(
            "doc.md",
            "\n".join(
                [
                    "192.0.2.10 198.51.100.7 203.0.113.9 100.64.0.10 100.64.0.20",
                    "mac 00:00:5e:00:53:01 and escaped 00\\:00\\:5e\\:00\\:53\\:02",
                    "paths /Users/user/x /Users/$USER/x /Users/<name>/x /home/user/x",
                    "mail a@example.com b@example.org noreply@github.com 1+x@users.noreply.github.com",
                    "cidr 100.64.0.0/10 10.0.0.0/8 192.168.0.0/16",
                    "",
                ]
            ),
        )
        r = run_gate(self.root)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_publicignore_skips_file_directory_and_glob(self):
        self.write(".publicignore", "# comment\n\nskip.md\nvendor/\nlogs/*.txt\n")
        for rel in ("skip.md", "vendor/a/b.md", "logs/run.txt"):
            self.write(rel, "10.1" ".2.3\n")
        r = run_gate(self.root)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.write("logs/run.md", "10.1" ".2.3\n")
        r = run_gate(self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("logs/run.md:1:", r.stdout)

    def test_denylist_is_case_insensitive_and_hides_literal(self):
        self.write(".personal-data-denylist", "SecretName\n")
        self.write("a.md", "hello secretNAME world\n")
        r = run_gate(self.root)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "a.md:1: denylist")
        self.assertNotIn("secretname", (r.stdout + r.stderr).lower())

    def test_binary_files_are_skipped(self):
        path = os.path.join(self.root, "blob.bin")
        with open(path, "wb") as fh:
            fh.write(b"\x00\x01 10.1" b".2.3 \x00")
        r = run_gate(self.root)
        self.assertEqual(r.returncode, 0)

    def test_git_tree_scans_only_tracked_files(self):
        subprocess.run(["git", "init", "-q", self.root], check=True)
        self.write("tracked.md", "10.1" ".2.3\n")
        self.write("untracked.md", "10.4" ".5.6\n")
        subprocess.run(["git", "-C", self.root, "add", "tracked.md"], check=True)
        r = run_gate(self.root)
        self.assertEqual(r.stdout.strip(), "tracked.md:1: rfc1918-ipv4")

    def test_rfc6598_allows_only_two_documentation_hosts(self):
        self.write("a.md", "100.64.0.10 100.64.0.20\n")
        self.assertEqual(run_gate(self.root).returncode, 0)
        for value in ("100.64.0." "7", "100.64.0." "200", "100.64." "1.1", "100.12" "7.0.1"):
            with self.subTest(value=value):
                self.write("a.md", f"{value}\n")
                r = run_gate(self.root)
                self.assertEqual(r.stdout.strip(), "a.md:1: cgnat-ipv4")
                self.assertNotIn(value, r.stdout)

    def test_cidr_and_url_forms_do_not_hide_addresses(self):
        for value in ("net 10.9" ".8.7/24", "http://10.9" ".8.7/x", "10.9" ".8.7/33"):
            with self.subTest(value=value):
                self.write("a.md", f"{value}\n")
                self.assertEqual(run_gate(self.root).stdout.strip(), "a.md:1: rfc1918-ipv4")
        self.write("a.md", "10.0.0.0/8 100.64.0.0/10 192.168.0.0/16 172.16.0.0/12\n")
        self.assertEqual(run_gate(self.root).returncode, 0)

    def test_secret_patterns_are_reported(self):
        cases = {
            "-----BEGIN OPENSSH PRIV" "ATE KEY-----": "private-key",
            "-----BEGIN RSA PRIV" "ATE KEY-----": "private-key",
            "ssh-ed25519 AAAA" "C3NzaC1lZDI1NTE5AAAAIFakeFakeFake": "ssh-public-key",
            "ssh-rsa AAAA" "B3NzaC1yc2EAAAADAQABFakeFake": "ssh-public-key",
            "gh" "p_" + "a" * 36: "token",
            "github" "_pat_" + "a" * 30: "token",
            "ts" "key-auth-" + "a" * 12: "token",
            "fd7a:115c:" "a1e0::1": "ipv6-tailscale",
            "aa-bb-cc" "-dd-ee-ff": "mac-address",
            "my-box" ".local": "local-hostname",
        }
        for value, kind in cases.items():
            with self.subTest(kind=kind, value=value[:12]):
                self.write("f.txt", f"x {value} y\n")
                r = run_gate(self.root)
                self.assertEqual(r.returncode, 1)
                self.assertIn(kind, r.stdout)
                self.assertNotIn(value, r.stdout)

    def test_local_paths_and_documentation_mac_are_not_hostnames(self):
        self.write("a.md", "$HOME/.local/bin and ~/.local/share and *.local.* and 00-00-5e-00-53-01\n")
        r = run_gate(self.root)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_symlink_target_is_scanned(self):
        subprocess.run(["git", "init", "-q", self.root], check=True)
        os.symlink("/Us" "ers/alice/x", os.path.join(self.root, "link"))
        subprocess.run(["git", "-C", self.root, "add", "link"], check=True)
        r = run_gate(self.root)
        self.assertEqual((r.returncode, r.stdout), (1, "link:1: users-path\n"))
        subprocess.run(["git", "-C", self.root, "rm", "-q", "-f", "link"], check=True)
        os.symlink("../docs/readme.md", os.path.join(self.root, "link"))
        subprocess.run(["git", "-C", self.root, "add", "link"], check=True)
        self.assertEqual(run_gate(self.root).returncode, 0)

    def test_ssh_targets_and_versions_are_not_emails(self):
        self.write("a.md", "ssh user@192.0.2.10 and root@10.0." "0.1 and python@3.14 node@22.1.0\n")
        r = run_gate(self.root)
        self.assertEqual((r.returncode, r.stdout), (1, "a.md:1: rfc1918-ipv4\n"))
        self.write("a.md", "ssh user@192.0.2.10 python@3.14\n")
        self.assertEqual(run_gate(self.root).returncode, 0)
        self.write("a.md", "alice@" "corp.io\n")
        self.assertEqual(run_gate(self.root).stdout.strip(), "a.md:1: email")

    def test_allow_file_accepts_exact_literals_only(self):
        self.write("scripts/public-tree-allow.txt", "# fake\n/Us" "ers/nina\n/ho" "me/test\ntail1234.t" "s.net\n")
        self.write("a.md", "/Us" "ers/nina/.ssh /ho" "me/test/x box.tail1234.t" "s.net\n")
        self.assertEqual(run_gate(self.root).returncode, 0)
        self.write("a.md", "/Us" "ers/ninabob/x\n")
        self.assertEqual(run_gate(self.root).stdout.strip(), "a.md:1: users-path")
        self.write("a.md", "box.other99.t" "s.net\n")
        self.assertEqual(run_gate(self.root).stdout.strip(), "a.md:1: tailnet-name")


if __name__ == "__main__":
    unittest.main()
