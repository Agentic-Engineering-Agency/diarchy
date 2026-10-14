from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "client" / "diarchy-route.sh"
LAN_HOST = "192.0.2.10"
TAILSCALE_HOST = "100.64.0.10"
SSH_USER = "labuser"
HOST_KEY_ALIAS = "lab-host-key"
UNSET = object()


def write_config(
    path: Path,
    lan: str,
    tailscale: str,
    port: str | None,
    identity: str | None,
) -> None:
    lines = ["# Managed by Diarchy", f"DIARCHY_SSH_USER={SSH_USER}"]
    lines.append(f"DIARCHY_LAN_HOST={lan}")
    lines.append(f"DIARCHY_TAILSCALE_HOST={tailscale}")
    lines.append(f"DIARCHY_HOST_KEY_ALIAS={HOST_KEY_ALIAS}")
    if port is not None:
        lines.append(f"DIARCHY_SSH_PORT={port}")
    if identity is not None:
        lines.append(f"DIARCHY_SSH_IDENTITY_FILE={identity}")
    path.write_text("\n".join(lines) + "\n")


def run_route(
    scenario: str,
    forced: str = "auto",
    lan: str = LAN_HOST,
    tailscale: str = TAILSCALE_HOST,
    port: str | None = None,
    identity: str | None = None,
    config: object = UNSET,
    source_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        ping = temp / "ping"
        route = temp / "route"
        connect = temp / "nc"
        log = temp / "calls.log"
        log.write_text("")

        ping.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "scenario = os.environ['FAKE_SCENARIO']\n"
            "host = sys.argv[-1]\n"
            "with open(os.environ['FAKE_LOG'], 'a') as log:\n"
            "    log.write(f'ping {host}\\n')\n"
            "lan = os.environ['FAKE_LAN']\n"
            "tail = os.environ['FAKE_TAIL']\n"
            "reachable = {\n"
            "    'lan-ok': {lan},\n"
            "    'lan-tcp-only': {tail},\n"
            "    'lan-utun': {lan, tail},\n"
            "    'lan-down': {tail},\n"
            "    'tailscale-ok': {tail},\n"
            "    'all-down': set(),\n"
            "}[scenario]\n"
            "if host not in reachable:\n"
            "    print('4 packets transmitted, 0 packets received, 100.0% packet loss')\n"
            "    raise SystemExit(2)\n"
            "for index, value in enumerate((3.0, 4.0, 5.0, 6.0), 1):\n"
            "    print(f'64 bytes from {host}: icmp_seq={index} ttl=64 time={value} ms')\n"
            "print('4 packets transmitted, 4 packets received, 0% packet loss')\n"
        )
        route.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "scenario = os.environ['FAKE_SCENARIO']\n"
            "host = sys.argv[-1]\n"
            "lan = os.environ['FAKE_LAN']\n"
            "interface = 'utun4' if scenario == 'lan-utun' and host == lan else 'en0'\n"
            "print(f'   route to: {host}')\n"
            "print(f'  interface: {interface}')\n"
        )
        connect.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "scenario = os.environ['FAKE_SCENARIO']\n"
            "host = sys.argv[-2]\n"
            "with open(os.environ['FAKE_LOG'], 'a') as log:\n"
            "    log.write(f'nc {host} {sys.argv[-1]}\\n')\n"
            "lan = os.environ['FAKE_LAN']\n"
            "tail = os.environ['FAKE_TAIL']\n"
            "reachable = {\n"
            "    'lan-ok': {lan},\n"
            "    'lan-tcp-only': {lan, tail},\n"
            "    'lan-utun': {lan, tail},\n"
            "    'lan-down': {tail},\n"
            "    'tailscale-ok': {tail},\n"
            "    'all-down': set(),\n"
            "}[scenario]\n"
            "raise SystemExit(0 if host in reachable else 1)\n"
        )
        for executable in (ping, route, connect):
            executable.chmod(0o755)

        if config is UNSET:
            config_path = temp / "diarchy.conf"
            write_config(config_path, lan, tailscale, port, identity)
        else:
            config_path = config

        if source_only:
            command = f"set -euo pipefail; source {ROUTE}; echo sourced"
        else:
            command = (
                "set -euo pipefail; "
                f"source {ROUTE}; "
                f"select_diarchy_route {forced}; "
                "printf '%s\\n' \"${diarchy_ssh_options[@]}\""
            )
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")}
        env.update(
            {
                "HOME": str(temp),
                "DIARCHY_CONFIG": str(config_path),
                "PING_BIN": str(ping),
                "ROUTE_BIN": str(route),
                "CONNECT_BIN": str(connect),
                "FAKE_SCENARIO": scenario,
                "FAKE_LAN": lan,
                "FAKE_TAIL": tailscale,
                "FAKE_LOG": str(log),
            }
        )
        result = subprocess.run(
            ["/usr/bin/env", "bash", "-c", command],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        result.calls = log.read_text().splitlines()
        return result


class RouteSelectionTests(unittest.TestCase):
    def test_auto_prefers_reachable_non_tunnel_lan(self):
        self.assertIn("route=lan", run_route("lan-ok").stderr)

    def test_auto_prefers_tcp_reachable_lan_when_icmp_is_silent(self):
        result = run_route("lan-tcp-only")
        self.assertIn("route=lan", result.stderr)
        self.assertIn("loss_pct=100.0 median_ms=unavailable", result.stderr)

    def test_success_diagnostic_includes_probe_metadata(self):
        result = run_route("lan-ok")
        self.assertIn(
            f"route=lan host={LAN_HOST} interface=en0 loss_pct=0 median_ms=4.500",
            result.stderr,
        )

    def test_utun_route_is_not_classified_as_lan(self):
        self.assertIn("route=tailscale", run_route("lan-utun").stderr)

    def test_auto_falls_back_to_tailscale(self):
        self.assertIn("route=tailscale", run_route("lan-down").stderr)

    def test_auto_unreachable_names_host(self):
        result = run_route("all-down")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Host is unreachable on LAN and Tailscale", result.stderr)

    def test_forced_unreachable_route_fails_closed(self):
        result = run_route("all-down", forced="lan")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forced LAN route is unreachable", result.stderr)

    def test_forced_unreachable_tailscale_fails_closed(self):
        result = run_route("all-down", forced="tailscale")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forced Tailscale route is unreachable", result.stderr)

    def test_unknown_route_selection_fails(self):
        result = run_route("lan-ok", forced="bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown route selection: bogus", result.stderr)

    def test_sourcing_library_has_no_side_effects(self):
        result = run_route("lan-ok", config=Path("/nonexistent/diarchy.conf"), source_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "sourced")
        self.assertEqual(result.stderr, "")

    def test_sourcing_with_cdpath_from_relative_path_yields_single_line_dir(self):
        result = subprocess.run(
            [
                "/usr/bin/env", "bash", "-c",
                'set -euo pipefail; source client/diarchy-route.sh; '
                'printf "%s\\n" "$_DIARCHY_ROUTE_DIR"',
            ],
            cwd=ROOT,
            env={**os.environ, "CDPATH": ".:/"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(ROOT / "client")])

    def test_lan_ssh_uses_configured_host_key_alias(self):
        result = run_route("lan-ok", forced="lan")
        self.assertIn(f"HostKeyAlias={HOST_KEY_ALIAS}", result.stdout)
        self.assertNotIn(f"HostKeyAlias={TAILSCALE_HOST}", result.stdout)

    def test_selected_ssh_options_disable_controlmaster_and_set_host_port(self):
        result = run_route("tailscale-ok", forced="tailscale")
        self.assertIn("ControlMaster=no", result.stdout)
        self.assertIn("PermitLocalCommand=no", result.stdout)
        self.assertIn("ClearAllForwardings=yes", result.stdout)
        self.assertIn("StrictHostKeyChecking=yes", result.stdout)
        self.assertIn("BatchMode=yes", result.stdout)
        self.assertNotIn("accept-new", result.stdout)
        self.assertNotIn("UpdateHostKeys=no", result.stdout)
        self.assertIn(f"HostName={TAILSCALE_HOST}", result.stdout)
        self.assertIn("Port=22", result.stdout)

    def test_ssh_options_order_and_user_without_identity(self):
        result = run_route("lan-ok", forced="lan")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        known_hosts = [line for line in lines if line.startswith("UserKnownHostsFile=")]
        self.assertEqual(len(known_hosts), 1, lines)
        self.assertTrue(known_hosts[0].endswith("/known_hosts"), known_hosts)
        self.assertEqual(
            lines,
            [
                "-o", "ControlMaster=no",
                "-o", "ControlPath=none",
                "-o", "PermitLocalCommand=no",
                "-o", "ClearAllForwardings=yes",
                "-o", f"User={SSH_USER}",
                "-o", f"HostKeyAlias={HOST_KEY_ALIAS}",
                "-o", f"HostName={LAN_HOST}",
                "-o", "Port=22",
                "-o", known_hosts[0],
                "-o", "StrictHostKeyChecking=yes",
                "-o", "BatchMode=yes",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
            ],
        )
        self.assertNotIn("IdentityFile", result.stdout)
        self.assertNotIn("IdentitiesOnly", result.stdout)

    def test_known_hosts_path_is_next_to_the_config_file(self):
        result = subprocess.run(
            ["/usr/bin/env", "bash", "-c", f"source {ROUTE}; diarchy_known_hosts_path"],
            env={**os.environ, "DIARCHY_CONFIG": "/Users/user/cfg dir/diarchy.conf"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.stdout, "/Users/user/cfg dir/known_hosts\n")

    def test_known_hosts_path_defaults_under_home_config(self):
        env = {k: v for k, v in os.environ.items() if k != "DIARCHY_CONFIG"}
        env["HOME"] = "/Users/user"
        result = subprocess.run(
            ["/usr/bin/env", "bash", "-c", f"source {ROUTE}; diarchy_known_hosts_path"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.stdout, "/Users/user/.config/diarchy/known_hosts\n")

    def test_known_hosts_path_with_space_is_quoted_in_ssh_option(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conf_dir = Path(temp_dir) / "cfg dir"
            conf_dir.mkdir()
            config = conf_dir / "diarchy.conf"
            write_config(config, LAN_HOST, TAILSCALE_HOST, None, None)
            result = run_route("lan-ok", forced="lan", config=config)
        values = [line for line in result.stdout.splitlines() if line.startswith("UserKnownHostsFile=")]
        self.assertEqual(values, [f'UserKnownHostsFile="{conf_dir}/known_hosts"'])

    def test_identity_file_adds_identityfile_and_identitiesonly(self):
        result = run_route("lan-ok", forced="lan", identity="/keys/id_lab")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-4:], ["-o", "IdentityFile=/keys/id_lab", "-o", "IdentitiesOnly=yes"])

    def test_identity_file_with_whitespace_is_double_quoted(self):
        result = run_route("lan-ok", forced="lan", identity="/keys/with space/id_lab")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-4:], ["-o", 'IdentityFile="/keys/with space/id_lab"', "-o", "IdentitiesOnly=yes"])

    @unittest.skipUnless(os.access("/usr/bin/ssh", os.X_OK), "/usr/bin/ssh is unavailable")
    def test_whitespace_identity_options_are_accepted_by_real_ssh(self):
        identity = "/keys/with space/id_lab"
        result = run_route("lan-ok", forced="lan", identity=identity)
        options = result.stdout.splitlines()
        probe = subprocess.run(
            ["/usr/bin/ssh", "-G", "-o", "HostName=192.0.2.1", *options, "alias"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertIn(f"identityfile {identity}\n", probe.stdout)

    def test_custom_port_flows_to_probe_and_options(self):
        result = run_route("lan-ok", forced="lan", port="2222")
        self.assertIn("Port=2222", result.stdout)
        self.assertIn(f"nc {LAN_HOST} 2222", result.calls)

    def test_lan_only_config_auto_selects_lan(self):
        result = run_route("lan-ok", tailscale="")
        self.assertIn("route=lan", result.stderr)

    def test_lan_only_config_auto_reports_unreachable_without_probing_tailscale(self):
        result = run_route("all-down", tailscale="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Host is unreachable on LAN and Tailscale", result.stderr)
        self.assertEqual([c for c in result.calls if TAILSCALE_HOST in c], [])

    def test_lan_only_config_forced_tailscale_is_not_configured(self):
        result = run_route("lan-ok", forced="tailscale", tailscale="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("diarchy-route: forced Tailscale route is not configured", result.stderr)
        self.assertEqual(result.calls, [])

    def test_tailscale_only_config_auto_never_probes_lan(self):
        result = run_route("tailscale-ok", lan="")
        self.assertIn("route=tailscale", result.stderr)
        self.assertEqual({c.split()[1] for c in result.calls}, {TAILSCALE_HOST})

    def test_tailscale_only_config_forced_lan_is_not_configured(self):
        result = run_route("tailscale-ok", forced="lan", lan="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("diarchy-route: forced LAN route is not configured", result.stderr)
        self.assertEqual(result.calls, [])

    def test_missing_config_reports_loader_error(self):
        result = run_route("lan-ok", config=Path("/nonexistent/diarchy.conf"))
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r"(?m)^diarchy-config: .+$")
        self.assertEqual(result.calls, [])


if __name__ == "__main__":
    unittest.main()
