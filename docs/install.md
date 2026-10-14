# Install Diarchy

Diarchy has two parts: a menu-bar app on an Apple Silicon Mac (macOS 14 or later) and a host agent on an Omarchy
machine (Arch Linux with Hyprland). You need both.

The beta app is ad-hoc signed and not notarized, because there is no paid Apple developer account.
macOS shows a Gatekeeper warning on the first start. This page shows how to open it safely.

Requirement on the Mac: Apple Command Line Tools. If they are missing, run `xcode-select --install`.

Host limits of this beta: x86_64 only (the pinned Sunshine and Lan Mouse builds are x86_64), a Wayland Hyprland session,
and a user account with `systemd --user`. No step of the installers uses `sudo`. When a step needs it, the host agent
prints the command and you run it ([ADR 0002](adr/0002-no-sudo-required-actions.md)).

## a. Download the release (recommended)

1. Download the release files from <https://github.com/Sebastiangmz/diarchy/releases>. Use the browser, or the GitHub CLI:

   ```sh
   cd ~/Downloads
   gh release download v0.1.0-beta.1 --repo Sebastiangmz/diarchy --pattern 'Diarchy-*.zip' --pattern SHA256SUMS
   ```

2. Check the SHA-256 sum. Run it in the folder that holds both files. Each line must say `OK`.

   ```sh
   shasum -a 256 -c SHA256SUMS
   ```

   If a line says `FAILED`, delete the files and download them again. Do not open the app.

3. Open the zip in Finder. Double-click `Diarchy-v0.1.0-beta.1.zip`. Then drag `Diarchy.app` to the Applications folder in Finder.

4. Open `Diarchy.app` the first time. macOS blocks it because it is not notarized. Do these steps:
   1. Open System Settings, then Privacy & Security.
   2. Scroll to the message about Diarchy and click "Open Anyway". Confirm with your password or Touch ID.

   macOS 15 and later have no "Open" button in the first dialog. Use "Open Anyway".
   macOS asks only once for each downloaded version.

5. Alternative to step 4, for users who checked the SHA-256 sum: remove the quarantine flag from this one app.

   ```sh
   xattr -dr com.apple.quarantine /Applications/Diarchy.app
   ```

   Do not turn off Gatekeeper for the whole Mac. Do not use this command on other files.

Diarchy appears in the menu bar. Open it and follow the setup checklist. The checklist installs the Mac tools, pairs SSH,
and installs the host agent over SSH (see section c).

Known limitation: the app can run from an App Translocation path if you moved it with Terminal. Move it with Finder instead.

## b. Build from source

Requirements: Xcode or the Swift 6 toolchain, and the macOS command line tools.

```sh
git clone https://github.com/Sebastiangmz/diarchy.git
cd diarchy
app/scripts/build-app.sh
```

The script builds a release app for arm64, ad-hoc signs it, and prints the path: `app/build/Diarchy.app`.
Copy it to `/Applications`. To copy it to `~/Applications` and register it, run `app/scripts/build-app.sh --install`.
An app that you build on your own Mac does not carry the quarantine flag, so Gatekeeper does not block it.

## c. Set up the Omarchy host

The app does these steps for you in the setup checklist. This section shows them for manual use.

1. On the host, make sure SSH works and you know the user name and an address of the host (for example `192.0.2.10`).
   The host needs `python3`, `curl`, `sha256sum`, `jq`, `socat`, `openssl`, `systemctl` and `hyprctl`.
2. On the Mac, create or choose an SSH key and copy it to the host:

   ```sh
   ssh-copy-id -p 22 -i ~/.ssh/id_ed25519.pub user@192.0.2.10
   ```

   Compare the host key fingerprint with the one on the host (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`)
   before you accept it.
3. The app writes `~/.config/diarchy/diarchy.conf` on the Mac. Use Settings, then Connection, to set the user, address and port.
4. Install the host agent. On the host, from a clone of this repository:

   ```sh
   git clone https://github.com/Sebastiangmz/diarchy.git
   bash diarchy/host/install-host.sh --with-streaming
   ```

   Leave out `--with-streaming` if you want KVM, clipboard and files only. The installer does this:
   - installs the agent in `~/.local/share/diarchy/agent` and `~/.local/bin/diarchy-host`;
   - runs `diarchy-host probe` and stops if the platform is not supported or a required action is pending;
   - installs Lan Mouse 0.11.0 and the clipboard and file portal tools, each with a pinned SHA-256 check;
   - with `--with-streaming`, installs Sunshine and the `diarchy-sunshine` and `diarchy-display` user units.
5. Run the commands that `diarchy-host probe` prints for pending steps, if any. Typical ones are a udev rule for
   `/dev/uinput` and a logind drop-in so closing the lid does not suspend a laptop host. Read each command before you run it.
6. Check the host. Run `~/.local/bin/diarchy-kvm-host-doctor` (installed in all cases). `~/.local/bin/diarchy-host-doctor <mac-address>`
   exists only after you install with `--with-streaming`.
7. Pair KVM and Streaming from the app. KVM pairing exchanges the Lan Mouse certificate fingerprint.
   Streaming pairing uses a PIN in Moonlight.

Install notes:
- The installer refuses to run while `diarchy-sunshine.service` is active. Stop Streaming first (`diarchy-host stream stop`).
- If the installer replaces `~/.local/bin/sunshine`, `~/.config/sunshine/apps.json` or `~/.local/bin/lan-mouse`, it first saves a
  copy named `<file>.diarchy-backup-<UTC stamp>`.
- `install-host.sh` stops and removes units, commands and folders of the old `homelab-*` install. The app does this for you.
  The Mac installer removes old `homelab-*` commands and old Home Lab apps in the same way.
- One host can pair with more than one Mac. A new authorization keeps the earlier ones. To remove a Mac, see
  "Remove an old Mac from the host" in [uninstall.md](uninstall.md).

On the Mac, the setup checklist also runs `client/install-lan-mouse.sh` (Lan Mouse app), `client/install-clipboard-file-transfer.sh`
(clipboard bridge, Diarchy Drop and Diarchy File Portal) and, for Streaming, `client/install.sh` (Moonlight 6.1.0).
Grant Accessibility to Lan Mouse in System Settings, then Privacy & Security, when the checklist asks for it.

## d. Tailscale (optional)

Diarchy works on a LAN. Tailscale adds a route that works from another network.

1. Install Tailscale on the Mac and on the host, and sign in on both. Diarchy does not install Tailscale.
2. In Diarchy Settings, Connection, set the Tailscale address of the host (for example `100.64.0.10`) next to the LAN address.
   Diarchy tries the LAN address first and then the Tailscale address.
3. On a host with `ufw`, allow Lan Mouse on the Tailscale interface. The probe prints the exact command.
   It opens UDP port 4242 to every device in your tailnet. Lan Mouse still needs the paired certificate.

## Next

- Uninstall: [uninstall.md](uninstall.md).
- Something does not work? Open a [bug report](https://github.com/Sebastiangmz/diarchy/issues/new?template=bug.yml).
- It works? Send a [hardware report](https://github.com/Sebastiangmz/diarchy/issues/new?template=hardware-report.yml).
