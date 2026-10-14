from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "client" / "diarchy-route.sh"
LAN_HOST = "192.0.2.10"
TAILSCALE_HOST = "100.64.0.10"

# Fake tools wait as long as the real ones would for an unreachable host.
FAKE_PING = """#!/usr/bin/env python3
import sys, time
args = sys.argv[1:]
count = int(args[args.index('-c') + 1]) if '-c' in args else 1_000_000
host = args[-1]
open(sys.argv[0] + '.log', 'a').write(' '.join(args) + '\\n')
if host != '100.64.0.10':
    time.sleep(count)
    print(f'{count} packets transmitted, 0 packets received, 100.0% packet loss')
    raise SystemExit(2)
for i in range(count):
    print(f'64 bytes from {host}: icmp_seq={i} ttl=64 time=20.0 ms')
print(f'{count} packets transmitted, {count} packets received, 0% packet loss')
"""
FAKE_NC = """#!/usr/bin/env python3
import sys, time
args = sys.argv[1:]
host, port = args[-2], args[-1]
timeout = int(args[args.index('-G') + 1]) if '-G' in args else 75
open(sys.argv[0] + '.log', 'a').write(' '.join(args) + '\\n')
if host == '100.64.0.10':
    raise SystemExit(0)
time.sleep(timeout)
raise SystemExit(1)
"""
FAKE_ROUTE = """#!/usr/bin/env python3
import sys
host = sys.argv[-1]
print(f'   route to: {host}')
print('  interface: ' + ('utun4' if host == '100.64.0.10' else 'en0'))
"""


class RouteSpeedTests(unittest.TestCase):
    def test_away_from_home_route_choice_finishes_under_3_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            for name, body in (("ping", FAKE_PING), ("nc", FAKE_NC), ("route", FAKE_ROUTE)):
                (temp / name).write_text(body)
                (temp / name).chmod(0o755)
            conf = temp / "diarchy.conf"
            conf.write_text(
                "# Managed by Diarchy\n"
                "DIARCHY_SSH_USER=labuser\n"
                f"DIARCHY_LAN_HOST={LAN_HOST}\n"
                f"DIARCHY_TAILSCALE_HOST={TAILSCALE_HOST}\n"
                "DIARCHY_HOST_KEY_ALIAS=lab-host-key\n"
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith("DIARCHY_")}
            env.update(
                HOME=str(temp),
                DIARCHY_CONFIG=str(conf),
                PING_BIN=str(temp / "ping"),
                ROUTE_BIN=str(temp / "route"),
                CONNECT_BIN=str(temp / "nc"),
            )
            started = time.monotonic()
            result = subprocess.run(
                ["/usr/bin/env", "bash", "-c", f"set -euo pipefail; source {ROUTE}; select_diarchy_route auto"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("route=tailscale", result.stderr)
            self.assertLess(elapsed, 3.0)
            ping_log = temp / "ping.log"
            for line in ping_log.read_text().splitlines() if ping_log.exists() else []:
                self.assertIn("-c 1 ", line + " ")


if __name__ == "__main__":
    unittest.main()
