# Uninstall Diarchy

This page lists everything that Diarchy and its installers create. Diarchy installs no LaunchAgent and no root-owned file.
The installers never use `sudo`. Only the optional host steps that you ran yourself (section "Host: system files") are
outside your home directory.

Read each command before you run it. Several commands delete folders. The paths below are the defaults.

## 1. On the Mac

### Stop and sign out

1. In Diarchy, switch to Off. Then choose Quit.
2. If you enabled "Launch at login": open System Settings, then General, then Login Items. Remove Diarchy.
   If Diarchy is still open, turn the setting off in Diarchy Settings first.
3. Quit Lan Mouse, Moonlight, Diarchy Drop and Diarchy File Portal if they run.

### Apps

Diarchy installs or builds these apps. The Diarchy app is in `/Applications` if you followed the install page.
If you built it with `--install`, it is in `~/Applications`.

```sh
rm -rf /Applications/Diarchy.app
rm -rf ~/Applications/Diarchy.app
rm -rf "$HOME/Applications/Lan Mouse.app"
rm -rf "$HOME/Applications/Moonlight.app"
rm -rf "$HOME/Applications/Diarchy Drop.app"
rm -rf "$HOME/Applications/Diarchy File Portal.app"
```

`Lan Mouse.app` and `Moonlight.app` are the upstream programs. Remove them only if you do not use them for other things.
An old `~/Applications/Diarchy KVM.app` can exist from an earlier version. Remove it too.

### Commands in `~/.local/bin`

```sh
cd ~/.local/bin && rm -f \
  diarchy-clipboard-bridge diarchy-clipboard-file-transfer-client-doctor diarchy-client-doctor \
  diarchy-config.sh diarchy-file diarchy-file-portal-receiver diarchy-file-portal-send \
  diarchy-kvm diarchy-kvm-accessibility-check diarchy-kvm-client-doctor diarchy-kvm-configure \
  diarchy-host-key diarchy-mac-fingerprint diarchy-route.sh diarchy-ssh diarchy-stream
```

### Configuration and data

```sh
# Diarchy configuration (diarchy.conf) and the pinned SSH host key (known_hosts)
rm -rf ~/.config/diarchy
# Lan Mouse configuration and certificate (upstream files, also rendered by Diarchy)
rm -rf ~/.config/lan-mouse
# Received files. Check the folder first: it can hold your files.
rm -rf ~/Drop/from-host
rmdir ~/Drop 2>/dev/null || true
# Runtime and lock files (they are in $TMPDIR and also vanish after a restart)
rm -rf "${TMPDIR:-/tmp}"/diarchy-*
```

Diarchy pins the host SSH key in `~/.config/diarchy/known_hosts`. The command above removes it. Diarchy never writes
to `~/.ssh/known_hosts`.

Diarchy does not edit `~/.ssh/config`. If you copied your SSH key to the host, remove the line from `~/.ssh/authorized_keys`
on the host.

### Permissions and other apps

- System Settings, then Privacy & Security, then Accessibility: remove Lan Mouse.
- Tailscale and Moonlight keep their own settings. Diarchy does not install Tailscale.
- Moonlight settings are in `~/Library/Preferences/com.moonlight-stream.Moonlight.plist`.

## 2. On the Omarchy host

Run these on the host as your normal user.

### Stop and disable the units

Diarchy installs four systemd user units. Each unit file is in `~/.config/systemd/user`.

```sh
systemctl --user disable --now diarchy-kvm.service diarchy-sunshine.service \
  diarchy-display.service diarchy-file-portal.service
rm -f ~/.config/systemd/user/diarchy-kvm.service ~/.config/systemd/user/diarchy-sunshine.service \
  ~/.config/systemd/user/diarchy-display.service ~/.config/systemd/user/diarchy-file-portal.service
systemctl --user daemon-reload
```

### Commands in `~/.local/bin`

```sh
cd ~/.local/bin && rm -f \
  diarchy-host diarchy-display diarchy-host-doctor diarchy-kvm-host-doctor \
  diarchy-check-sunshine-log diarchy-check-sunshine-service \
  diarchy-clipboard-remote diarchy-file-transfer-host-doctor \
  diarchy-file-portal diarchy-file-portal-remote diarchy-file-portal-ingest \
  lan-mouse sunshine
```

`lan-mouse` and `sunshine` here are the files that the installers placed. `sunshine` is a wrapper script from Diarchy.
Keep them if you use Sunshine or Lan Mouse for other things.

If you changed `sunshine`, `apps.json` or `lan-mouse` before an install, the installer saved a copy first. The copies
are named `<file>.diarchy-backup-<UTC stamp>`. They are in `~/.local/bin` and `~/.config/sunshine`. Delete them with
`rm -f ~/.local/bin/*.diarchy-backup-* ~/.config/sunshine/*.diarchy-backup-*`.

### Remove an old Mac from the host

The host keeps the certificate fingerprint of every Mac that you paired. It never removes one by itself. A Mac that
gets a new Lan Mouse certificate (for example after a reinstall) adds a second entry. The old entry stays valid.
WARNING: An old entry lets that Mac certificate connect to Lan Mouse on the host. Remove entries of Macs that you do not use.

The list is in `~/.config/lan-mouse/config.toml` on the host. Each entry is one line in the `[authorized_fingerprints]` section:

```toml
[authorized_fingerprints]
"aa:bb:...:ff" = "mac"
```

1. Stop KVM: `systemctl --user stop diarchy-kvm.service`.
2. Open `~/.config/lan-mouse/config.toml`. Delete the line of the old fingerprint. Keep the first line of the file,
   `# Managed by diarchy`, exactly as it is. If you remove it, Diarchy treats the file as yours. The next write then saves
   the file as `config.toml.backup.<UTC stamp>` and replaces it with a new one.
3. Start KVM from the Diarchy app.

### Programs, caches, configuration and data

```sh
# Pinned upstream programs (use the version that you installed)
rm -rf ~/.local/opt/sunshine-2026.516.143833 ~/.local/opt/lan-mouse-0.11.0
# Download caches
rm -rf ~/.cache/diarchy ~/.cache/diarchy-kvm
# Agent and payload copied by the app
rm -rf ~/.local/share/diarchy
# Host configuration (display.env with the stream output)
rm -rf ~/.config/diarchy
# Lan Mouse configuration and certificate
rm -rf ~/.config/lan-mouse
# Sunshine configuration, paired clients and the Web UI credential file (.diarchy-ui)
rm -rf ~/.config/sunshine
# Drop folders. Check them first: they can hold your files.
rm -rf ~/Drop/from-mac ~/Drop/to-mac
rmdir ~/Drop 2>/dev/null || true
```

If you use `XDG_CONFIG_HOME` or `XDG_CACHE_HOME`, replace `~/.config` and `~/.cache` with those paths.
A pinned version in `~/.local/opt` can differ if you installed another release. List the folder first.
The installer for the clipboard tools can leave `~/.diarchy-file-transfer.install.lock` if it was killed. Remove it.

### Host: system files (only if you created them)

The host agent prints these commands but never runs them. If you ran them, undo them with `sudo`:

```sh
# udev rule for /dev/uinput
sudo rm -f /etc/udev/rules.d/60-diarchy-uinput.rules
sudo udevadm control --reload
# lid policy drop-in (restores the default lid behavior after the next logind restart or reboot)
sudo rm -f /etc/systemd/logind.conf.d/60-diarchy.conf
# firewall rules that you added for Lan Mouse (list them first)
sudo ufw status numbered
```

Delete only the `ufw` rules for UDP port 4242 that you added for Diarchy.

## 3. Check that nothing is left

On the Mac:

```sh
ls ~/.local/bin | grep -i diarchy
ls ~/Applications /Applications | grep -i -E 'diarchy|lan mouse|moonlight'
```

On the host:

```sh
systemctl --user list-unit-files | grep -i diarchy
ls ~/.local/bin | grep -i -E 'diarchy|lan-mouse|sunshine'
```

Both lists must be empty, except for programs that you chose to keep.
