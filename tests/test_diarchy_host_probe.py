from __future__ import annotations

import sys
import unittest

from pathlib import Path

from tests.test_diarchy_host_gpu import FakeSysfs
from tests.test_diarchy_host_support import AgentCase, FP_A, FP_B

PACKAGE_TOOLS = ("wl-copy", "wl-paste", "rsync", "notify-send", "openssl", "jq", "curl")
NOOP = "pass\n"
EXIT_1 = "import sys\nsys.exit(1)\n"
HYPRCTL_ONE = 'print(\'[{"instance":"abc","wl_socket":"wayland-1","pid":42,"time":1}]\')\n'
UFW = "system:ufw.service"
LAN_MOUSE_OUTPUT = 'print("lan-mouse 0.11.0")\n'
SS_LISTENING = (
    'print("UNCONN 0 0 0.0.0.0:4242 0.0.0.0:*")\n'
    'print("UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:*")\n'
)
INSTALL_PACKAGES_COMMAND = "sudo pacman -S --needed wl-clipboard libnotify jq python-gobject gtk3 gtk-layer-shell"
FIREWALL_LAN_COMMAND = "sudo ufw allow proto udp from 192.0.2.10 to any port 4242"
FIREWALL_TAILSCALE_COMMAND = "sudo ufw allow in on tailscale0 to any port 4242 proto udp"
FIREWALL_STREAMING_LAN_COMMAND = (
    "sudo ufw allow proto tcp from 192.0.2.10 to any port 47984,47989,48010"
    " && sudo ufw allow proto udp from 192.0.2.10 to any port 47998:48000,48010"
)
UINPUT_COMMAND = (
    r"""printf 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"\n'"""
    " | sudo tee /etc/udev/rules.d/60-diarchy-uinput.rules"
    " && sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput"
)
LID_COMMAND = (
    "sudo mkdir -p /etc/systemd/logind.conf.d"
    r" && printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\n'"
    " | sudo tee /etc/systemd/logind.conf.d/60-diarchy.conf"
)


def openssl_printing(text: str) -> str:
    return (
        "import os, sys\n"
        "open(os.environ['FAKE_LOG'], 'a').write('openssl ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        f"print({text!r})\n"
    )


class ProbeCase(AgentCase):
    """A fully supported Omarchy host; tests degrade one condition at a time."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_sudo()
        self.os_release = self.root / "os-release"
        self.os_release.write_text("NAME=Arch Linux\nID=arch\n")
        self.sys_net = self.root / "sys-net"
        self.sys_net.mkdir()
        self.uinput = self.root / "dev-uinput"
        self.logind_dirs = [self.root / "logind-lib", self.root / "logind-etc"]
        self.env.update({
            "DIARCHY_OS_RELEASE": str(self.os_release),
            "DIARCHY_MACHINE": "x86_64",
            "DIARCHY_SYS_NET": str(self.sys_net),
            "DIARCHY_UINPUT_PATH": str(self.uinput),
            "DIARCHY_LOGIND_DIRS": ":".join(str(path) for path in self.logind_dirs),
        })
        self.proc = self.root / "proc"
        self.sysfs = FakeSysfs(self.root / "sys")
        self.add_lid("open")
        self.sysfs.gpu("card0", "i915", "renderD128", boot_vga=True, connectors=("eDP-1",))
        self.env.update({"DIARCHY_PROC_ROOT": str(self.proc), "DIARCHY_SYSFS_ROOT": str(self.sysfs.root)})
        self.tool("hyprctl", HYPRCTL_ONE, "HYPRCTL_BIN")
        self.fake_systemctl()
        for name in PACKAGE_TOOLS:
            self.tool(name, NOOP)
        self.tool("openssl", NOOP, "OPENSSL_BIN")
        self.tool("python3", NOOP, "PYTHON_BIN")
        (self.home / ".local/share/omarchy").mkdir(parents=True)

    def tearDown(self) -> None:
        self.assertEqual(self.sudo_calls(), [], "the agent must never invoke sudo")
        super().tearDown()

    def install_bin(self, name: str, body: str = NOOP) -> None:
        path = self.home / ".local/bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)

    def add_lid(self, state: str) -> None:
        directory = self.proc / "acpi/button/lid/LID0"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "state").write_text(f"state:      {state}\n")

    def write_logind(self, directory: int, relative: str, text: str) -> None:
        path = self.logind_dirs[directory] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def probe(self, *args: str) -> dict:
        result, body = self.agent("probe", *args)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIs(body["ok"], True)
        return body

    def action_ids(self, body: dict) -> list[str]:
        return [action["id"] for action in body["required_actions"]]

    def action(self, body: dict, action_id: str) -> dict:
        matches = [action for action in body["required_actions"] if action["id"] == action_id]
        self.assertEqual(len(matches), 1, body["required_actions"])
        return matches[0]


class PlatformPayloadTests(ProbeCase):
    def test_supported_omarchy_host(self) -> None:
        result, body = self.agent("probe")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(body["schema"], 1)
        self.assertIs(body["ok"], True)
        self.assertEqual(body["command"], "probe")
        self.assertEqual(body["agent_version"], "0.1.0-beta.1")
        self.assertEqual(set(body), {"schema", "ok", "command", "agent_version", "platform", "session",
                                     "components", "kvm", "required_actions"})
        platform = body["platform"]
        self.assertIs(platform["supported"], True)
        self.assertEqual(platform["adapter"], "hyprland-arch")
        self.assertEqual(platform["flavor"], "omarchy")
        self.assertEqual(platform["reasons"], [])
        self.assertEqual(body["required_actions"], [])
        self.assertEqual(body["kvm"]["port"], 4242)

    def test_non_arch_host_is_unsupported_with_no_actions(self) -> None:
        self.os_release.write_text("NAME=Ubuntu\nID=ubuntu\nID_LIKE=debian\n")
        self.fake_systemctl(active=[UFW])
        body = self.probe("--controller-ip", "192.0.2.10", "--streaming")
        self.assertIs(body["platform"]["supported"], False)
        self.assertEqual(body["platform"]["adapter"], "unsupported")
        self.assertTrue(body["platform"]["reasons"])
        self.assertEqual(body["required_actions"], [])

    def test_no_hyprland_instance_is_unsupported(self) -> None:
        self.tool("hyprctl", 'print("[]")\n', "HYPRCTL_BIN")
        body = self.probe()
        self.assertIs(body["platform"]["supported"], False)
        self.assertIsNone(body["platform"]["compositor"])
        self.assertTrue(any("Hyprland" in reason for reason in body["platform"]["reasons"]))
        self.assertEqual(body["required_actions"], [])
        self.assertEqual(body["platform"]["os_id"], "arch")

    def test_graphical_session_mirrors_systemctl(self) -> None:
        self.assertIs(self.probe()["session"]["graphical_session_active"], False)
        self.fake_systemctl(active=["graphical-session.target"])
        self.assertEqual(self.probe()["session"], {"graphical_session_active": True})

    def test_graphical_session_is_computed_even_on_unsupported_platform(self) -> None:
        self.os_release.write_text("NAME=Ubuntu\nID=ubuntu\n")
        self.fake_systemctl(active=["graphical-session.target"])
        body = self.probe()
        self.assertIs(body["platform"]["supported"], False)
        self.assertEqual(body["session"], {"graphical_session_active": True})
        self.assertIn("--user is-active --quiet graphical-session.target", self.calls())

    def test_graphical_session_user_scope_only(self) -> None:
        self.fake_systemctl(active=["system:graphical-session.target"])
        self.assertEqual(self.probe()["session"], {"graphical_session_active": False})


class InstallPackagesTests(ProbeCase):
    def test_missing_packages_produce_one_required_action(self) -> None:
        for name in ("jq", "wl-paste", "notify-send"):
            (self.tools / name).unlink()
        self.tool("python3", EXIT_1, "PYTHON_BIN")
        body = self.probe()
        self.assertEqual(self.action_ids(body), ["install-packages"])
        action = body["required_actions"][0]
        self.assertEqual(action["severity"], "required")
        self.assertEqual(action["command"], INSTALL_PACKAGES_COMMAND)
        self.assertEqual(set(action), {"id", "severity", "title", "reason", "command"})
        self.assertTrue(action["title"] and action["reason"])

    def test_nothing_missing_means_no_action(self) -> None:
        self.assertNotIn("install-packages", self.action_ids(self.probe()))


class FirewallTests(ProbeCase):
    def test_lan_rule_needs_active_ufw_and_controller_ip(self) -> None:
        self.fake_systemctl(active=[UFW])
        body = self.probe("--controller-ip", "192.0.2.10")
        action = self.action(body, "firewall-lan")
        self.assertEqual(action["severity"], "recommended")
        self.assertEqual(action["command"], FIREWALL_LAN_COMMAND)
        self.assertEqual(self.action_ids(body), ["firewall-lan"])

    def test_no_lan_rule_without_controller_ip(self) -> None:
        self.fake_systemctl(active=[UFW])
        self.assertEqual(self.action_ids(self.probe()), [])

    def test_no_rules_when_ufw_inactive(self) -> None:
        (self.sys_net / "tailscale0").mkdir()
        self.assertEqual(self.action_ids(self.probe("--controller-ip", "192.0.2.10")), [])

    def test_user_scope_ufw_unit_does_not_count(self) -> None:
        self.fake_systemctl(active=["ufw.service"])
        self.assertEqual(self.action_ids(self.probe("--controller-ip", "192.0.2.10")), [])

    def test_tailscale_rule_needs_interface(self) -> None:
        self.fake_systemctl(active=[UFW])
        self.assertEqual(self.action_ids(self.probe()), [])
        (self.sys_net / "tailscale0").mkdir()
        body = self.probe()
        action = self.action(body, "firewall-tailscale")
        self.assertEqual(action["severity"], "recommended")
        self.assertEqual(action["command"], FIREWALL_TAILSCALE_COMMAND)

    def test_both_rules_in_order(self) -> None:
        self.fake_systemctl(active=[UFW])
        (self.sys_net / "tailscale0").mkdir()
        body = self.probe("--controller-ip", "192.0.2.10")
        self.assertEqual(self.action_ids(body), ["firewall-lan", "firewall-tailscale"])

    def test_install_packages_comes_first(self) -> None:
        (self.tools / "jq").unlink()
        self.fake_systemctl(active=[UFW])
        body = self.probe("--controller-ip", "192.0.2.10")
        self.assertEqual(self.action_ids(body), ["install-packages", "firewall-lan"])


class StreamingFirewallTests(ProbeCase):
    def test_streaming_with_ufw_and_controller_ip_recommends_opening_sunshine_ports(self) -> None:
        self.fake_systemctl(active=[UFW])
        body = self.probe("--streaming", "--controller-ip", "192.0.2.10")
        action = self.action(body, "firewall-streaming-lan")
        self.assertEqual(action["severity"], "recommended")
        self.assertEqual(action["command"], FIREWALL_STREAMING_LAN_COMMAND)
        self.assertEqual(action["title"], "Allow streaming from the controller")
        self.assertEqual(
            action["reason"], "ufw is active and would block Moonlight from the local network.")
        self.assertNotIn("47990", action["command"])
        self.assertEqual(
            self.action_ids(body), ["firewall-lan", "firewall-streaming-lan", "uinput-access", "lid-policy"])

    def test_absent_without_the_streaming_flag(self) -> None:
        self.fake_systemctl(active=[UFW])
        self.assertNotIn("firewall-streaming-lan", self.action_ids(self.probe("--controller-ip", "192.0.2.10")))

    def test_absent_when_ufw_is_inactive(self) -> None:
        body = self.probe("--streaming", "--controller-ip", "192.0.2.10")
        self.assertNotIn("firewall-streaming-lan", self.action_ids(body))

    def test_absent_without_controller_ip(self) -> None:
        self.fake_systemctl(active=[UFW])
        self.assertNotIn("firewall-streaming-lan", self.action_ids(self.probe("--streaming")))


class StreamingActionTests(ProbeCase):
    def test_kvm_only_host_has_no_streaming_actions(self) -> None:
        self.assertEqual(self.action_ids(self.probe()), [])

    def test_streaming_flag_triggers_both_actions(self) -> None:
        body = self.probe("--streaming")
        self.assertEqual(self.action_ids(body), ["uinput-access", "lid-policy"])
        uinput, lid = body["required_actions"]
        self.assertEqual((uinput["severity"], uinput["command"]), ("required", UINPUT_COMMAND))
        self.assertEqual((lid["severity"], lid["command"]), ("recommended", LID_COMMAND))
        self.assertIn(r"\n'", uinput["command"])
        self.assertNotIn("\n", uinput["command"] + lid["command"])

    def test_sunshine_binary_triggers_both_actions_without_flag(self) -> None:
        self.install_bin("sunshine")
        self.assertEqual(self.action_ids(self.probe()), ["uinput-access", "lid-policy"])

    def test_flag_order_is_free(self) -> None:
        body = self.probe("--streaming", "--controller-ip", "192.0.2.10")
        self.assertEqual(self.action_ids(body), ["uinput-access", "lid-policy"])

    def test_writable_uinput_needs_no_action(self) -> None:
        self.uinput.write_text("")
        self.assertEqual(self.action_ids(self.probe("--streaming")), ["lid-policy"])

    def test_lid_ignore_in_drop_in_needs_no_action(self) -> None:
        self.write_logind(1, "logind.conf.d/x.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.assertEqual(self.action_ids(self.probe("--streaming")), ["uinput-access"])

    def test_explicit_suspend_still_needs_action(self) -> None:
        self.write_logind(0, "logind.conf", "[Login]\n#HandleLidSwitch=ignore\nHandleLidSwitch=suspend\n")
        self.assertIn("lid-policy", self.action_ids(self.probe("--streaming")))

    def test_later_directory_overrides_earlier(self) -> None:
        self.write_logind(0, "logind.conf", "[Login]\nHandleLidSwitch=suspend\n")
        self.write_logind(1, "logind.conf.d/50-lid.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.assertNotIn("lid-policy", self.action_ids(self.probe("--streaming")))
        self.write_logind(0, "logind.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.write_logind(1, "logind.conf.d/50-lid.conf", "[Login]\nHandleLidSwitch=suspend\n")
        self.assertIn("lid-policy", self.action_ids(self.probe("--streaming")))

    def test_drop_ins_apply_in_filename_order(self) -> None:
        self.write_logind(1, "logind.conf.d/20-b.conf", "[Login]\nHandleLidSwitch=suspend\n")
        self.write_logind(1, "logind.conf.d/10-a.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.assertIn("lid-policy", self.action_ids(self.probe("--streaming")))
        self.write_logind(1, "logind.conf.d/30-c.conf", "[Login]\nHandleLidSwitch=lock\n")
        self.assertNotIn("lid-policy", self.action_ids(self.probe("--streaming")))

    def test_drop_in_beats_main_file_of_same_directory(self) -> None:
        self.write_logind(1, "logind.conf", "[Login]\nHandleLidSwitch=suspend\n")
        self.write_logind(1, "logind.conf.d/a.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.assertNotIn("lid-policy", self.action_ids(self.probe("--streaming")))

    def test_other_sections_and_empty_assignment(self) -> None:
        self.write_logind(0, "logind.conf", "[Login]\nHandleLidSwitch=ignore\n")
        self.write_logind(1, "logind.conf", "[Other]\nHandleLidSwitch=suspend\n")
        self.assertNotIn("lid-policy", self.action_ids(self.probe("--streaming")))
        self.write_logind(1, "logind.conf", "[Login]\nHandleLidSwitch=\n")  # empty resets to the default
        self.assertIn("lid-policy", self.action_ids(self.probe("--streaming")))

    def test_streaming_actions_absent_on_unsupported_platform(self) -> None:
        self.os_release.write_text("NAME=Ubuntu\nID=ubuntu\n")
        self.assertEqual(self.probe("--streaming")["required_actions"], [])


class ComponentTests(ProbeCase):
    ABSENT = {
        "lan_mouse": {"installed": False, "version": None, "service_active": False, "listening_udp": False},
        "clipboard": {"installed": False},
        "file_portal": {"installed": False, "service_active": False},
        "sunshine": {"installed": False, "service_active": False},
    }

    def test_absent_components(self) -> None:
        self.assertEqual(self.probe()["components"], self.ABSENT)

    def test_lan_mouse_fully_running(self) -> None:
        self.install_bin("lan-mouse", LAN_MOUSE_OUTPUT)
        self.tool("ss", SS_LISTENING, "SS_BIN")
        self.fake_systemctl(active=["diarchy-kvm.service"])
        self.assertEqual(self.probe()["components"]["lan_mouse"],
                         {"installed": True, "version": "0.11.0", "service_active": True, "listening_udp": True})

    def test_lan_mouse_bin_override(self) -> None:
        path = self.tool("elsewhere", LAN_MOUSE_OUTPUT, "LAN_MOUSE_BIN")
        self.assertTrue(path.exists())
        lan_mouse = self.probe()["components"]["lan_mouse"]
        self.assertEqual((lan_mouse["installed"], lan_mouse["version"]), (True, "0.11.0"))

    def test_lan_mouse_binary_must_be_executable(self) -> None:
        self.install_bin("lan-mouse", LAN_MOUSE_OUTPUT)
        (self.home / ".local/bin/lan-mouse").chmod(0o644)
        self.assertEqual(self.probe()["components"]["lan_mouse"]["installed"], False)

    def test_unparseable_or_failing_version_is_null(self) -> None:
        self.install_bin("lan-mouse", 'print("something else")\n')
        self.assertEqual(self.probe()["components"]["lan_mouse"]["version"], None)
        self.install_bin("lan-mouse", EXIT_1)
        lan_mouse = self.probe()["components"]["lan_mouse"]
        self.assertEqual((lan_mouse["installed"], lan_mouse["version"]), (True, None))

    def test_listening_only_on_the_kvm_port(self) -> None:
        self.tool("ss", 'print("UNCONN 0 0 0.0.0.0:42420 0.0.0.0:*")\nprint("UNCONN 0 0 [::]:4243 [::]:*")\n',
                  "SS_BIN")
        self.assertIs(self.probe()["components"]["lan_mouse"]["listening_udp"], False)
        self.tool("ss", 'print("UNCONN 0 0 [::]:4242 [::]:*")\n', "SS_BIN")
        self.assertIs(self.probe()["components"]["lan_mouse"]["listening_udp"], True)

    def test_failing_ss_means_not_listening(self) -> None:
        self.tool("ss", 'print("UNCONN 0 0 0.0.0.0:4242 0.0.0.0:*")\nimport sys\nsys.exit(1)\n', "SS_BIN")
        self.assertIs(self.probe()["components"]["lan_mouse"]["listening_udp"], False)

    def test_ss_uses_numeric_listening_udp_flags(self) -> None:
        self.tool("ss", "import os, sys\nopen(os.environ['FAKE_LOG'], 'a').write('ss ' + ' '.join(sys.argv[1:]) + '\\n')\n",
                  "SS_BIN")
        self.probe()
        self.assertIn("ss -H -lun", self.calls())

    def test_clipboard_file_portal_and_sunshine(self) -> None:
        self.install_bin("diarchy-clipboard-remote")
        self.install_bin("diarchy-file-portal")
        self.install_bin("sunshine")
        self.fake_systemctl(active=["diarchy-file-portal.service", "diarchy-sunshine.service"])
        components = self.probe()["components"]
        self.assertEqual(components["clipboard"], {"installed": True})
        self.assertEqual(components["file_portal"], {"installed": False, "service_active": True})
        self.assertEqual(components["sunshine"], {"installed": True, "service_active": True})
        unit = self.home / ".config/systemd/user/diarchy-file-portal.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n")
        self.assertEqual(self.probe()["components"]["file_portal"], {"installed": True, "service_active": True})

    def test_file_portal_needs_its_binary_too(self) -> None:
        unit = self.home / ".config/systemd/user/diarchy-file-portal.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Service]\n")
        self.assertEqual(self.probe()["components"]["file_portal"]["installed"], False)

    def test_tailscale_interface_does_not_change_components(self) -> None:
        before = self.probe()["components"]
        (self.sys_net / "tailscale0").mkdir()
        self.assertEqual(self.probe()["components"], before)


class KvmPayloadTests(ProbeCase):
    def cert(self, name: str = "lan-mouse.pem") -> str:
        path = self.home / ".config/lan-mouse" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-----BEGIN CERTIFICATE-----\n")
        return str(path)

    def test_defaults_without_cert_or_config(self) -> None:
        self.assertEqual(self.probe()["kvm"],
                         {"host_fingerprint": None, "authorized_fingerprints": [], "port": 4242})

    def test_host_fingerprint_is_uppercased_openssl_output(self) -> None:
        pem = self.cert()
        self.tool("openssl", openssl_printing("sha256 Fingerprint=" + ":".join(["aa"] * 32)), "OPENSSL_BIN")
        self.assertEqual(self.probe()["kvm"]["host_fingerprint"], FP_A)
        self.assertIn(f"openssl x509 -in {pem} -noout -fingerprint -sha256", self.calls())

    def test_cert_override(self) -> None:
        pem = self.cert("other.pem")
        self.tool("openssl", openssl_printing("SHA256 Fingerprint=" + ":".join(["BB"] * 32)), "OPENSSL_BIN")
        self.env["LAN_MOUSE_CERT"] = pem
        self.assertEqual(self.probe()["kvm"]["host_fingerprint"], FP_B)
        self.assertIn(f"openssl x509 -in {pem} -noout -fingerprint -sha256", self.calls())

    def test_xdg_config_home_locates_cert_and_config(self) -> None:
        xdg = self.root / "xdg"
        (xdg / "lan-mouse").mkdir(parents=True)
        (xdg / "lan-mouse" / "lan-mouse.pem").write_text("-----BEGIN CERTIFICATE-----\n")
        (xdg / "lan-mouse" / "config.toml").write_text(f'[authorized_fingerprints]\n"{FP_B.lower()}" = "mac"\n')
        self.tool("openssl", openssl_printing("SHA256 Fingerprint=" + ":".join(["aa"] * 32)), "OPENSSL_BIN")
        self.env["XDG_CONFIG_HOME"] = str(xdg)
        kvm = self.probe()["kvm"]
        self.assertEqual(kvm["host_fingerprint"], FP_A)
        self.assertEqual(kvm["authorized_fingerprints"], [FP_B])
        self.assertIn(f"openssl x509 -in {xdg}/lan-mouse/lan-mouse.pem -noout -fingerprint -sha256", self.calls())

    def test_garbage_openssl_output_is_null(self) -> None:
        self.cert()
        for text in ("not a fingerprint", "sha256 Fingerprint=zz:zz", "sha256 Fingerprint=" + ":".join(["aa"] * 31),
                     "sha256 Fingerprint=" + ":".join(["aa"] * 32) + ";ls", ""):
            with self.subTest(text=text):
                self.tool("openssl", openssl_printing(text), "OPENSSL_BIN")
                self.assertIsNone(self.probe()["kvm"]["host_fingerprint"])

    def test_failing_openssl_is_null(self) -> None:
        self.cert()
        self.tool("openssl", EXIT_1, "OPENSSL_BIN")
        self.assertIsNone(self.probe()["kvm"]["host_fingerprint"])

    def test_authorized_fingerprints_are_uppercase_and_ordered(self) -> None:
        config = self.home / ".config/lan-mouse/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            'port = 4242\n\n[authorized_fingerprints]\n'
            f'"{FP_A.lower()}" = "mac"\n"{FP_B}" = "other"\n'
        )
        self.assertEqual(self.probe()["kvm"]["authorized_fingerprints"], [FP_A, FP_B])


class DegradationTests(ProbeCase):
    def test_no_external_tools_at_all(self) -> None:
        for name in ("hyprctl", "systemctl", "ss", "openssl"):
            (self.tools / name).unlink(missing_ok=True)
        for variable in ("HYPRCTL_BIN", "SYSTEMCTL_BIN", "OPENSSL_BIN"):
            del self.env[variable]
        self.cert = self.home / ".config/lan-mouse/lan-mouse.pem"
        self.cert.parent.mkdir(parents=True)
        self.cert.write_text("pem")
        result, body = self.agent("probe", "--streaming", "--controller-ip", "192.0.2.10")
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIs(body["ok"], True)
        self.assertIs(body["platform"]["supported"], False)
        self.assertEqual(body["session"], {"graphical_session_active": False})
        self.assertEqual(body["components"], ComponentTests.ABSENT)
        self.assertEqual(body["kvm"], {"host_fingerprint": None, "authorized_fingerprints": [], "port": 4242})
        self.assertEqual(body["required_actions"], [])

    def test_supported_host_without_ss_and_openssl_still_probes(self) -> None:
        (self.tools / "ss").unlink(missing_ok=True)
        (self.tools / "openssl").unlink()
        del self.env["OPENSSL_BIN"]
        self.cert = self.home / ".config/lan-mouse/lan-mouse.pem"
        self.cert.parent.mkdir(parents=True)
        self.cert.write_text("pem")
        body = self.probe()
        self.assertIsNone(body["kvm"]["host_fingerprint"])
        self.assertIs(body["components"]["lan_mouse"]["listening_udp"], False)
        self.assertEqual(self.action_ids(body), ["install-packages"])
        self.assertEqual(self.action(body, "install-packages")["command"], "sudo pacman -S --needed openssl")

    def test_ss_exiting_one_is_conservative(self) -> None:
        self.tool("ss", EXIT_1, "SS_BIN")
        body = self.probe()
        self.assertIs(body["components"]["lan_mouse"]["listening_udp"], False)

    def test_undecodable_logind_file_falls_back_to_default(self) -> None:
        self.write_logind(0, "logind.conf", "")
        (self.logind_dirs[0] / "logind.conf").write_bytes(b"\xff\xfe[Login]\nHandleLidSwitch=ignore\n")
        self.assertIn("lid-policy", self.action_ids(self.probe("--streaming")))


class LidPresenceTests(ProbeCase):
    def test_desktop_with_suspend_policy_gets_no_lid_action_or_text(self) -> None:
        for lid in (self.proc / "acpi/button/lid").iterdir():
            (lid / "state").unlink()
            lid.rmdir()
        self.write_logind(0, "logind.conf", "[Login]\nHandleLidSwitch=suspend\n")
        body = self.probe("--streaming")
        self.assertNotIn("lid-policy", self.action_ids(body))
        self.assertNotIn("lid", str(body["required_actions"]).lower())

    def test_laptop_with_suspend_policy_gets_the_action(self) -> None:
        self.assertEqual(self.action(self.probe("--streaming"), "lid-policy")["command"], LID_COMMAND)


class GpuActionTests(ProbeCase):
    def gpu_host(self, driver: str, render: str | None = "renderD129") -> None:
        self.sysfs = FakeSysfs(self.root / "other-sys")
        self.sysfs.gpu("card0", driver, render, boot_vga=True)
        self.env["DIARCHY_SYSFS_ROOT"] = str(self.sysfs.root)

    def test_supported_gpu_has_no_gpu_action(self) -> None:
        self.assertEqual([i for i in self.action_ids(self.probe("--streaming")) if i.startswith(("gpu", "nvidia"))], [])

    def test_unsupported_driver_gets_a_reason_and_no_command(self) -> None:
        self.gpu_host("nouveau")
        action = self.action(self.probe("--streaming"), "gpu-unsupported")
        self.assertEqual(action["command"], "")
        self.assertIn("nouveau", action["reason"])

    def test_no_render_node_is_unsupported(self) -> None:
        self.gpu_host("evdi", None)
        self.assertIn("render node", self.action(self.probe("--streaming"), "gpu-unsupported")["reason"])

    def test_nvidia_with_modeset_off_shows_the_fix_without_running_it(self) -> None:
        self.gpu_host("nvidia")
        param = self.sysfs.root / "module/nvidia_drm/parameters/modeset"
        param.parent.mkdir(parents=True)
        param.write_text("N\n")
        action = self.action(self.probe("--streaming"), "nvidia-modeset")
        self.assertIn("options nvidia_drm modeset=1", action["command"])
        self.assertNotIn("gpu-unsupported", self.action_ids(self.probe("--streaming")))

    def test_nvidia_with_unknown_modeset_shows_no_action(self) -> None:
        self.gpu_host("nvidia")
        self.assertNotIn("nvidia-modeset", self.action_ids(self.probe("--streaming")))

    def test_nvidia_with_modeset_needs_no_action(self) -> None:
        self.gpu_host("nvidia")
        param = self.sysfs.root / "module/nvidia_drm/parameters/modeset"
        param.parent.mkdir(parents=True)
        param.write_text("Y\n")
        self.assertNotIn("nvidia-modeset", self.action_ids(self.probe("--streaming")))


class TailscaleScopeTests(ProbeCase):
    def test_action_text_warns_the_rule_covers_all_peers_and_shows_scoping(self) -> None:
        (self.sys_net / "tailscale0").mkdir()
        self.fake_systemctl(active=[UFW])
        action = self.action(self.probe(), "firewall-tailscale")
        self.assertEqual(action["command"], FIREWALL_TAILSCALE_COMMAND)
        self.assertIn("all tailnet peers", action["reason"])
        self.assertIn("from 100.64.0.20", action["reason"])


class UsageTests(ProbeCase):
    def assert_usage(self, *args: str) -> None:
        result, body = self.agent("probe", *args)
        self.assertEqual(result.returncode, 64, (args, result.stdout))
        self.assertIs(body["ok"], False)
        self.assertEqual(body["command"], "probe")
        self.assertEqual(body["error"]["code"], "usage")

    def test_invalid_controller_ips(self) -> None:
        for value in ("2001:db8::1", "host.example", "1.2.3", "192.0.2.1;ls", "192.000.002.001", " 192.0.2.1",
                      "192.0.2.1 ", "192.0.2.256", "", "192.0.2.1\n", "0x7f.0.0.1", "192.0.2.1/24",
                      "$(id)", "--streaming"):
            with self.subTest(value=value):
                self.assert_usage("--controller-ip", value)

    def test_invalid_flag_shapes(self) -> None:
        for args in (("--bogus",), ("extra",), ("--streaming", "--streaming"), ("--controller-ip",),
                     ("--controller-ip", "192.0.2.10", "--controller-ip", "192.0.2.11"),
                     ("--controller-ip=192.0.2.10",), ("--streaming", "--controller-ip")):
            with self.subTest(args=args):
                self.assert_usage(*args)

    def test_valid_controller_ip(self) -> None:
        self.fake_systemctl(active=[UFW])
        body = self.probe("--controller-ip", "192.0.2.10")
        self.assertEqual(self.action(body, "firewall-lan")["command"], FIREWALL_LAN_COMMAND)

    def test_usage_errors_run_no_tools(self) -> None:
        self.assert_usage("--controller-ip", "nope")
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
