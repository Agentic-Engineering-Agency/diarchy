import Foundation

/// One single-line JSON envelope per host-agent payload (spec §6.2/§6.3), as the agent prints it (without `\n`).
/// Addresses follow RFC 5737; fingerprints are 32 uppercase hex groups.
enum Fixtures {
    static let fingerprintAA = Array(repeating: "AA", count: 32).joined(separator: ":")
    static let fingerprintBB = Array(repeating: "BB", count: 32).joined(separator: ":")
    static let fingerprintCC = Array(repeating: "CC", count: 32).joined(separator: ":")

    // MARK: version

    static let version = #"{"schema":1,"ok":true,"command":"version","agent_version":"1.0.0"}"#

    // MARK: probe

    /// Supported host, Lan Mouse running, one required action (`install-packages`).
    static var probeFull: String {
        probe(actions: [installPackages])
    }

    /// Unsupported platform: adapter `unsupported`, two reasons, no host fingerprint, nothing installed.
    static let probeUnsupported =
        #"{"schema":1,"ok":true,"command":"probe","agent_version":"1.0.0","#
        + #""platform":{"supported":false,"adapter":"unsupported","os_id":"ubuntu","flavor":null,"compositor":null,"machine":"aarch64","reasons":["host is not x86_64","no live Hyprland instance"]},"#
        + #""session":{"graphical_session_active":false},"#
        + #""components":{"lan_mouse":{"installed":false,"version":null,"service_active":false,"listening_udp":false},"clipboard":{"installed":false},"file_portal":{"installed":false,"service_active":false},"sunshine":{"installed":false,"service_active":false}},"#
        + #""kvm":{"host_fingerprint":null,"authorized_fingerprints":[],"port":4242},"required_actions":[]}"#

    static let installPackages =
        #"{"id":"install-packages","severity":"required","title":"Install missing packages","reason":"wl-copy and rsync are missing","command":"sudo pacman -S --needed wl-clipboard rsync"}"#
    static let firewallLAN =
        #"{"id":"firewall-lan","severity":"recommended","title":"Allow Lan Mouse through ufw (LAN)","reason":"ufw is active and would block UDP 4242","command":"sudo ufw allow proto udp from 192.0.2.5 to any port 4242"}"#
    static let firewallTailscale =
        #"{"id":"firewall-tailscale","severity":"recommended","title":"Allow Lan Mouse through ufw (Tailscale)","reason":"ufw is active and would block UDP 4242 on tailscale0","command":"sudo ufw allow in on tailscale0 to any port 4242 proto udp"}"#
    static let firewallStreamingLAN =
        #"{"id":"firewall-streaming-lan","severity":"recommended","title":"Allow streaming from the controller","reason":"ufw is active and would block Moonlight from the local network.","command":"sudo ufw allow proto tcp from 192.0.2.5 to any port 47984,47989,48010 && sudo ufw allow proto udp from 192.0.2.5 to any port 47998:48000,48010"}"#
    static let uinputAccess =
        #"{"id":"uinput-access","severity":"required","title":"Allow access to /dev/uinput","reason":"Sunshine cannot create virtual input devices","command":"echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/60-diarchy-uinput.rules"}"#
    static let lidPolicy =
        #"{"id":"lid-policy","severity":"recommended","title":"Keep the host awake with the lid closed","reason":"logind suspends on lid close","command":"sudo mkdir -p /etc/systemd/logind.conf.d"}"#
    static let wifiRoaming =
        #"{"id":"wifi-roaming","severity":"recommended","title":"Stop the host from hopping between Wi-Fi access points","reason":"The host changed access point 82 times in the last day","command":"nmcli connection modify 'example-connection' 802-11-wireless.bssid 00:00:5e:00:53:01 && nmcli connection up 'example-connection'"}"#
    static let unknownAction =
        #"{"id":"future-action","severity":"recommended","title":"A future action","reason":"Something new","command":"true"}"#

    static func probe(actions: [String]) -> String {
        #"{"schema":1,"ok":true,"command":"probe","agent_version":"1.0.0","#
            + #""platform":{"supported":true,"adapter":"hyprland-arch","os_id":"arch","flavor":"omarchy","compositor":"hyprland","machine":"x86_64","reasons":[]},"#
            + #""session":{"graphical_session_active":true},"#
            + #""components":{"lan_mouse":{"installed":true,"version":"0.11.0","service_active":true,"listening_udp":true},"clipboard":{"installed":true},"file_portal":{"installed":true,"service_active":true},"sunshine":{"installed":false,"service_active":false}},"#
            + #""kvm":{"host_fingerprint":"\#(fingerprintAA)","authorized_fingerprints":["\#(fingerprintBB)"],"port":4242},"#
            + #""required_actions":[\#(actions.joined(separator: ","))]}"#
    }

    // MARK: kvm

    static var kvmAuthorize: String {
        #"{"schema":1,"ok":true,"command":"kvm authorize","authorized":"\#(fingerprintAA)"}"#
    }
    static let kvmConfigure = #"{"schema":1,"ok":true,"command":"kvm configure","authorized":null}"#
    static let kvmStart = #"{"schema":1,"ok":true,"command":"kvm start","service_active":true}"#

    // MARK: stream

    static let streamStart = #"{"schema":1,"ok":true,"command":"stream start","service_active":true}"#
    static let streamStop = #"{"schema":1,"ok":true,"command":"stream stop","service_active":false}"#
    static let streamPair = #"{"schema":1,"ok":true,"command":"stream pair","paired":true}"#

    // MARK: report

    /// `diarchy-host report` envelope: one internal monitor, one disabled external monitor, extra fields to ignore.
    static let report =
        #"{"schema":1,"ok":true,"command":"report","diarchy_host_version":"0.1.0-beta.1","#
        + #""os":{"id":"arch","kernel":"6.17.1-arch1-1","omarchy_version":"3.1.0","omarchy_channel":"stable"},"#
        + #""hyprland":{"version":"0.54.0","monitors":[{"name":"eDP-1","width":1920,"height":1080,"refresh":144.0,"internal":true,"enabled":true},{"name":"DP-1","width":2560,"height":1440,"refresh":60.0,"internal":false,"enabled":false}]},"#
        + #""capture":{"output":"eDP-1","render_node":"renderD129","driver":"i915","encoder":"vaapi","unsupported_reason":null},"#
        + #""sunshine":{"installed":true,"version":"2026.516.143833"},"lan_mouse":{"installed":true,"version":"0.11.0"}}"#

    // MARK: failures

    /// Failure envelope for `command`; the agent exits 64 for `usage` and 1 for every other code.
    static func failure(code: String, message: String, command: String = "kvm start") -> String {
        #"{"schema":1,"ok":false,"command":"\#(command)","error":{"code":"\#(code)","message":"\#(message)"}}"#
    }

    /// One failure per spec §6.1 error code: wire code, message, exit code.
    static let failures: [(code: String, message: String, exit: Int32)] = [
        ("usage", "unknown option --bogus", 64),
        ("unsupported_platform", "host platform is not supported", 1),
        ("not_installed", "diarchy-kvm.service is not installed", 1),
        ("no_session", "no Hyprland session is running for this user", 1),
        ("service_failed", "diarchy-kvm.service failed to start", 1),
        ("invalid_fingerprint", "fingerprint must be 32 uppercase hex pairs", 1),
        ("config_write_failed", "cannot write /home/user/.config/lan-mouse/config.toml", 1),
        ("sunshine_unreachable", "Sunshine API is not reachable on 127.0.0.1:47990", 1),
        ("pair_failed", "Sunshine rejected the PIN", 1),
        ("internal", "unexpected KeyError", 1),
    ]
}
