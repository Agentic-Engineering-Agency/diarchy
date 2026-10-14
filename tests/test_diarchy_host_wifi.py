from __future__ import annotations

import json
import unittest

from tests.test_diarchy_host_probe import ProbeCase

AP_ASSOCIATED = "00:00:5E:00:53:01"
AP_OTHER = "00:00:5E:00:53:02"
AP_ROGUE = "00:00:5E:00:53:03"
# `nmcli -t` escapes the colons of a BSSID with a backslash.
NMCLI = (
    "import sys\n"
    "if 'show' in sys.argv:\n"
    "    print('example-connection')\n"
    "else:\n"
    "    print(r'no:00\\:00\\:5E\\:00\\:53\\:02:example-ssid:42')\n"
    "    print(r'yes:00\\:00\\:5E\\:00\\:53\\:01:example-ssid:50')\n"
    "    print(r'no:00\\:00\\:5E\\:00\\:53\\:04:Neighbour:44')\n"
)


def authenticate(bssid: str, interface: str = "wlo1", ssid: str = "example-ssid") -> str:
    return f"{interface}: SME: Trying to authenticate with {bssid.lower()} (SSID='{ssid}' freq=5745 MHz)"


def ping_pong(changes: int) -> str:
    """A journal in which the supplicant switched AP `changes` times (one initial association first)."""
    aps = [AP_OTHER, AP_ASSOCIATED]
    return "\n".join(authenticate(aps[i % 2]) for i in range(changes + 1)) + "\n"


class WifiRoamingTests(ProbeCase):
    def install_wifi_tools(self, journal: str, journal_exit: int = 0, nmcli: str = NMCLI) -> None:
        self.tool(
            "journalctl",
            "import os, sys\n"
            "open(os.environ['FAKE_LOG'], 'a').write('journalctl ' + ' '.join(sys.argv[1:]) + '\\n')\n"
            f"sys.stdout.write({journal!r})\nsys.exit({journal_exit})\n",
            "JOURNALCTL_BIN",
        )
        self.tool("nmcli", nmcli, "NMCLI_BIN")

    def test_ping_pong_between_access_points_while_streaming_recommends_pinning_the_associated(self) -> None:
        self.install_wifi_tools(ping_pong(12))
        action = self.action(self.probe("--streaming"), "wifi-roaming")
        self.assertEqual(action["severity"], "recommended")
        self.assertEqual(
            action["command"],
            f"nmcli connection modify example-connection 802-11-wireless.bssid {AP_ASSOCIATED}"
            " && nmcli connection up example-connection",
        )
        self.assertIn("12", action["reason"])

    def test_a_stronger_rogue_access_point_with_the_same_ssid_is_never_pinned(self) -> None:
        self.install_wifi_tools(ping_pong(12), nmcli=(
            "import sys\n"
            "if 'show' in sys.argv:\n"
            "    print('example-connection')\n"
            "else:\n"
            "    print(r'no:00\\:00\\:5E\\:00\\:53\\:03:example-ssid:99')\n"
            "    print(r'no:00\\:00\\:5E\\:00\\:53\\:02:example-ssid:42')\n"
            "    print(r'yes:00\\:00\\:5E\\:00\\:53\\:01:example-ssid:50')\n"
        ))
        action = self.action(self.probe("--streaming"), "wifi-roaming")
        self.assertIn(AP_ASSOCIATED, action["command"])
        self.assertNotIn(AP_ROGUE, json.dumps(action))
        self.assertNotIn(AP_OTHER, json.dumps(action))

    def test_without_an_associated_access_point_there_is_no_bssid_to_pin(self) -> None:
        self.install_wifi_tools(ping_pong(12), nmcli=(
            "import sys\n"
            "if 'show' in sys.argv:\n"
            "    print('example-connection')\n"
            "else:\n"
            "    print(r'no:00\\:00\\:5E\\:00\\:53\\:03:example-ssid:99')\n"
        ))
        self.assertNotIn("wifi-roaming", self.action_ids(self.probe("--streaming")))

    def test_connection_names_with_spaces_are_quoted(self) -> None:
        self.install_wifi_tools(ping_pong(12), nmcli=(
            "import sys\n"
            "print('Example Connection' if 'show' in sys.argv else r'yes:00\\:00\\:5E\\:00\\:53\\:01:example-ssid:50')\n"
        ))
        command = self.action(self.probe("--streaming"), "wifi-roaming")["command"]
        self.assertIn("modify 'Example Connection' 802-11-wireless.bssid", command)
        self.assertTrue(command.endswith("nmcli connection up 'Example Connection'"))

    def test_roaming_is_only_reported_when_streaming_is_requested(self) -> None:
        self.install_wifi_tools(ping_pong(12))
        self.assertEqual(self.action_ids(self.probe()), [])

    def test_few_access_point_changes_are_normal_roaming(self) -> None:
        self.install_wifi_tools(ping_pong(9))
        self.assertNotIn("wifi-roaming", self.action_ids(self.probe("--streaming")))

    def test_reconnecting_to_the_same_access_point_is_not_roaming(self) -> None:
        self.install_wifi_tools("".join(authenticate(AP_ASSOCIATED) + "\n" for _ in range(30)))
        self.assertNotIn("wifi-roaming", self.action_ids(self.probe("--streaming")))

    def test_unreadable_journal_reports_nothing_and_the_probe_still_succeeds(self) -> None:
        self.install_wifi_tools(ping_pong(12), journal_exit=1)
        self.assertNotIn("wifi-roaming", self.action_ids(self.probe("--streaming")))

    def test_without_a_resolvable_connection_there_is_no_command_to_offer(self) -> None:
        self.install_wifi_tools(ping_pong(12), nmcli="import sys\nsys.exit(1)\n")
        self.assertNotIn("wifi-roaming", self.action_ids(self.probe("--streaming")))

    def test_the_journal_is_read_for_the_supplicant_over_a_day(self) -> None:
        self.install_wifi_tools(ping_pong(12))
        self.probe("--streaming")
        call = next(line for line in self.calls() if line.startswith("journalctl"))
        self.assertIn("-u wpa_supplicant", call)
        self.assertIn("--since -24h", call)

    def test_streaming_actions_keep_their_order_with_roaming_last(self) -> None:
        self.install_wifi_tools(ping_pong(12))
        self.assertEqual(
            self.action_ids(self.probe("--streaming")), ["uinput-access", "lid-policy", "wifi-roaming"],
        )


if __name__ == "__main__":
    unittest.main()
