#!/usr/bin/env bash
set -euo pipefail

SUNSHINE_VERSION=2026.516.143833
SUNSHINE_SHA256=d0ee0a9cfb66f27869b559455f84622d21615047ccf3443c9a2f572ca971c7a2
SUNSHINE_URL="https://github.com/LizardByte/Sunshine/releases/download/v${SUNSHINE_VERSION}/sunshine.AppImage"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [[ "${1:-}" == "--dry-run" ]]; then
  cat <<PLAN
Diarchy host installation plan
- Install official Sunshine ${SUNSHINE_VERSION} rootless under ~/.local/opt
- Verify SHA-256 ${SUNSHINE_SHA256}
- Configure wlroots capture with hardware video encoding (Sunshine selects the encoder)
- Install two systemd user services; leave them stopped and disabled; the Diarchy agent starts them for Streaming mode
- Preserve unrelated configuration and existing lid policy
PLAN
  exit 0
fi

if [[ $# -ne 0 ]]; then
  printf 'usage: %s [--dry-run]\n' "$0" >&2
  exit 64
fi

DEV_ROOT=${DIARCHY_DEV_ROOT:-/dev}
PROC_ROOT=${DIARCHY_PROC_ROOT:-/proc}
kvm_stopped=0

fail() {
  printf '[fail] %s\n' "$1" >&2
  restore_kvm
  exit 1
}

# B-21: this installer stops diarchy-kvm.service while it restarts the streaming units (the two modes never run together).
# Put KVM back when the run ends, also when it fails.
restore_kvm() {
  if (( kvm_stopped )); then
    kvm_stopped=0
    if systemctl --user restart diarchy-kvm.service; then
      printf '[info] diarchy-kvm.service started again\n' >&2
    else
      printf '[warn] diarchy-kvm.service did not start again; run: journalctl --user -u diarchy-kvm.service -n 50\n' >&2
    fi
  fi
}

# B-12: keep a copy of a user file before it is replaced. Files that Diarchy installed earlier (identical to the new
# file, or carrying the marker text) are not copied again.
backup_user_file() {
  local target=$1 source=$2 marker=${3:-} backup
  [[ -f $target ]] || return 0
  if cmp -s "$target" "$source"; then return 0; fi
  if [[ -n $marker ]] && grep -q -- "$marker" "$target"; then return 0; fi
  backup=$target.diarchy-backup-$(date -u +%Y%m%dT%H%M%SZ)
  cp -p "$target" "$backup" || fail "Could not back up $target to $backup; check free disk space and permissions in $(dirname -- "$target")"
  printf '[info] Backed up %s to %s\n' "$target" "$backup"
}

# B-32: after a verified install, remove the other numbered version folders of one product. Never the folder in use.
prune_old_versions() {
  local product=$1 keep=$2 dir
  [[ -d $keep && ! -L $keep ]] || return 0
  for dir in "$OPT_ROOT/$product"-[0-9]*; do
    [[ -d $dir && ! -L $dir && $dir != "$keep" ]] || continue
    if rm -rf -- "$dir"; then
      printf '[info] Removed old %s folder %s\n' "$product" "$dir"
    else
      printf '[warn] Could not remove old %s folder %s\n' "$product" "$dir" >&2
    fi
  done
}

has_lid_switch() {
  compgen -G "$PROC_ROOT/acpi/button/lid/*" >/dev/null
}

step() {
  printf '\n==> %s\n' "$1"
}

for command in curl sha256sum install jq socat openssl mktemp systemctl hyprctl; do
  command -v "$command" >/dev/null || fail "Required command is missing: $command"
done

[[ $(uname -s) == Linux ]] || fail "The host installer requires Linux"
[[ $(uname -m) == x86_64 ]] || fail "The pinned Sunshine build requires x86_64"
shopt -s nullglob
render_nodes=("$DEV_ROOT"/dri/renderD* "$DEV_ROOT"/nvidia0)
shopt -u nullglob
(( ${#render_nodes[@]} > 0 )) ||
  fail "No GPU render device found (/dev/dri/renderD*): hardware encoding is unavailable. Run: diarchy-host probe"
[[ -w $DEV_ROOT/uinput ]] ||
  fail "The current user cannot write /dev/uinput; run the uinput-access command shown by 'diarchy-host probe' (never run sudo from this script)"
"$SCRIPT_DIR/diarchy-display" status >/dev/null ||
  fail "Hyprland IPC is not reachable. Log in to Hyprland on the host (the graphical session must be running), then run this installer again"
if systemctl --user is-active --quiet diarchy-sunshine.service; then
  fail "diarchy-sunshine.service is active (Streaming is running). A re-install would stop it. Stop Streaming first (Diarchy app, or: diarchy-host stream stop), then run this installer again"
fi

CONFIG_DIR=$HOME/.config/sunshine
CACHE_DIR=${XDG_CACHE_HOME:-$HOME/.cache}/diarchy
OPT_ROOT=$HOME/.local/opt
BIN_DIR=$HOME/.local/bin
UNIT_DIR=$HOME/.config/systemd/user
TARGET_DIR=$OPT_ROOT/sunshine-$SUNSHINE_VERSION
APPIMAGE=$CACHE_DIR/sunshine-$SUNSHINE_VERSION.AppImage
CREDENTIAL_FILE=$CONFIG_DIR/.diarchy-ui

step "Downloading and verifying Sunshine ${SUNSHINE_VERSION}"
install -d -m 700 "$CACHE_DIR" "$OPT_ROOT" "$BIN_DIR"
if [[ ! -f "$APPIMAGE" ]] || ! printf '%s  %s\n' "$SUNSHINE_SHA256" "$APPIMAGE" | sha256sum -c - >/dev/null 2>&1; then
  temporary_download=$APPIMAGE.download.$$
  trap 'rm -f "${temporary_download:-}"' EXIT
  curl -sS --fail --location --proto '=https' --tlsv1.2 "$SUNSHINE_URL" --output "$temporary_download" ||
    fail "Could not download Sunshine from $SUNSHINE_URL. Check the network of the host, then run this installer again"
  printf '%s  %s\n' "$SUNSHINE_SHA256" "$temporary_download" | sha256sum -c - >/dev/null ||
    fail "The Sunshine download does not match SHA-256 $SUNSHINE_SHA256. Run this installer again; if it fails again, stop and report it"
  chmod 0755 "$temporary_download"
  mv "$temporary_download" "$APPIMAGE"
  trap - EXIT
fi
chmod 0755 "$APPIMAGE"

if [[ ! -x "$TARGET_DIR/AppRun" ]]; then
  [[ ! -e "$TARGET_DIR" ]] || fail "Refusing to replace unexpected path: $TARGET_DIR"
  stage=$OPT_ROOT/.sunshine-$SUNSHINE_VERSION.$$.stage
  trap 'rm -rf "${stage:-}"' EXIT
  install -d -m 700 "$stage"
  (
    cd "$stage"
    "$APPIMAGE" --appimage-extract >/dev/null
  ) || fail "Sunshine AppImage extraction failed. Check that FUSE is not required and that $OPT_ROOT has free space, then run this installer again"
  [[ -x "$stage/squashfs-root/AppRun" ]] || fail "Sunshine AppImage extraction did not produce AppRun"
  mv "$stage/squashfs-root" "$TARGET_DIR"
  rmdir "$stage"
  trap - EXIT
fi
backup_user_file "$BIN_DIR/sunshine" "$SCRIPT_DIR/sunshine" DIARCHY_LIBVA_PATH
install -m 755 "$SCRIPT_DIR/sunshine" "$BIN_DIR/sunshine" || fail "Could not install $BIN_DIR/sunshine"
version_output=$("$BIN_DIR/sunshine" --version 2>&1) ||
  fail "$BIN_DIR/sunshine --version failed: ${version_output%%$'\n'*}. Run it by hand to see the error"
[[ $version_output == *"$SUNSHINE_VERSION"* ]] ||
  fail "Installed Sunshine version mismatch. Run: $BIN_DIR/sunshine --version"

step "Installing managed configuration"
install -d -m 700 "$CONFIG_DIR" "$UNIT_DIR"
if [[ -f "$CONFIG_DIR/sunshine.conf" ]] &&
  ! grep -q '^# Managed by diarchy' "$CONFIG_DIR/sunshine.conf"; then
  backup=$CONFIG_DIR/sunshine.conf.backup.$(date -u +%Y%m%dT%H%M%SZ)
  install -m 600 "$CONFIG_DIR/sunshine.conf" "$backup"
  printf '[info] Preserved existing Sunshine config at %s\n' "$backup"
fi
install -m 600 "$SCRIPT_DIR/sunshine.conf" "$CONFIG_DIR/sunshine.conf"
backup_user_file "$CONFIG_DIR/apps.json" "$SCRIPT_DIR/apps.json"
install -m 600 "$SCRIPT_DIR/apps.json" "$CONFIG_DIR/apps.json"
install -m 755 "$SCRIPT_DIR/diarchy-display" "$BIN_DIR/diarchy-display"
install -m 755 "$SCRIPT_DIR/diarchy-host-doctor" "$BIN_DIR/diarchy-host-doctor"
install -m 755 "$SCRIPT_DIR/diarchy-check-sunshine-log" \
  "$BIN_DIR/diarchy-check-sunshine-log"
install -m 755 "$SCRIPT_DIR/diarchy-check-sunshine-service" \
  "$BIN_DIR/diarchy-check-sunshine-service"
install -m 644 "$SCRIPT_DIR/systemd/diarchy-display.service" "$UNIT_DIR/diarchy-display.service"
install -m 644 "$SCRIPT_DIR/systemd/diarchy-sunshine.service" "$UNIT_DIR/diarchy-sunshine.service"

ui_username=
ui_password=
if [[ ! -f $CREDENTIAL_FILE && -f $CONFIG_DIR/sunshine_state.json ]]; then
  # B-12: this Sunshine already has a Web UI login that Diarchy did not create. A new random one would replace it.
  printf '[warn] Sunshine credentials already exist in %s. Diarchy keeps them and creates no new Web UI login.\n' "$CONFIG_DIR" >&2
  printf '[warn] To let Diarchy manage the login, delete %s/sunshine_state.json and run this installer again.\n' "$CONFIG_DIR" >&2
else
  if [[ -f "$CREDENTIAL_FILE" ]]; then
    while IFS='=' read -r key value; do
      case "$key" in
        username) ui_username=$value ;;
        password) ui_password=$value ;;
      esac
    done <"$CREDENTIAL_FILE"
  else
    ui_username=host
    ui_password=$(openssl rand -base64 24 | tr -d '\n') || fail "Could not create a Sunshine password (openssl rand failed)"
  fi
  [[ -n "$ui_username" && -n "$ui_password" ]] ||
    fail "Sunshine credential state is missing or invalid. Delete $CREDENTIAL_FILE and run this installer again"
  # SEC-20, accepted risk: `sunshine --creds <user> <password>` takes the password only as an argument. It has no
  # stdin or file form, and writing sunshine_state.json by hand would copy Sunshine's private hash format. The
  # password is visible in /proc/<pid>/cmdline of this one short-lived process to other local users of the host.
  # The Web UI listens for localhost only (origin_web_ui_allowed = pc).
  if ! creds_output=$("$BIN_DIR/sunshine" --creds "$ui_username" "$ui_password" "$CONFIG_DIR/sunshine.conf" 2>&1); then
    creds_output=${creds_output//"$ui_password"/'***'}
    fail "Sunshine rejected the Web UI credentials: ${creds_output%%$'\n'*}. Run: $BIN_DIR/sunshine --version, then run this installer again"
  fi
  if [[ ! -f $CREDENTIAL_FILE ]]; then
    credential_stage=$(mktemp "$CONFIG_DIR/.diarchy-ui.XXXXXX") || fail "Could not create a file in $CONFIG_DIR"
    trap 'rm -f "${credential_stage:-}"' EXIT
    chmod 600 "$credential_stage"
    printf 'username=%s\npassword=%s\n' "$ui_username" "$ui_password" >"$credential_stage"
    mv "$credential_stage" "$CREDENTIAL_FILE"
    trap - EXIT
  fi
  chmod 600 "$CREDENTIAL_FILE"
fi
unset ui_username ui_password creds_output

step "Pinning Sunshine to the GPU that renders the captured output"
# The agent owns the resolver (diarchy_host.gpu); validating the unpinned template could pick an encoder that
# `stream start` will not use. Prefer the installed agent, else the launcher shipped next to this script.
agent=$BIN_DIR/diarchy-host
[[ -x $agent ]] || agent=$SCRIPT_DIR/diarchy-host
"$agent" stream configure >/dev/null || fail "Could not pin Sunshine to the capture GPU (diarchy-host stream configure failed). Run: $agent stream configure"

step "Enabling graphical user services"
if systemctl --user is-active --quiet diarchy-kvm.service; then
  printf '[info] Stopping diarchy-kvm.service while the streaming units restart (KVM and Streaming never run together); it starts again at the end.\n'
  kvm_stopped=1
  systemctl --user stop diarchy-kvm.service ||
    fail "Could not stop diarchy-kvm.service. Run: journalctl --user -u diarchy-kvm.service -n 50"
fi
systemctl --user daemon-reload || fail "systemctl --user daemon-reload failed. Is the user systemd session running? Run: systemctl --user status"
systemctl --user disable diarchy-display.service diarchy-sunshine.service ||
  fail "systemctl --user disable failed for the streaming units. Run: systemctl --user status diarchy-display.service diarchy-sunshine.service"
systemctl --user restart diarchy-display.service diarchy-sunshine.service ||
  fail "systemctl --user restart failed for the streaming units. Run: journalctl --user -u diarchy-display.service -u diarchy-sunshine.service -n 50"
systemctl --user is-active --quiet diarchy-display.service ||
  fail "Display service did not become active. Run: journalctl --user -u diarchy-display.service -n 50"
systemctl --user is-active --quiet diarchy-sunshine.service ||
  fail "Sunshine service did not become active. Run: journalctl --user -u diarchy-sunshine.service -n 50"

for _ in {1..30}; do
  if "$BIN_DIR/diarchy-check-sunshine-service" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if ! selected_output=$("$BIN_DIR/diarchy-check-sunshine-service"); then
  fail "Sunshine capture or hardware encoder validation failed in its current invocation. Run: journalctl --user -u diarchy-sunshine.service -n 50"
fi
if ! selected_family=$("$BIN_DIR/diarchy-check-sunshine-service" --encoder); then
  fail "Sunshine capture or hardware encoder validation failed in its current invocation. Run: journalctl --user -u diarchy-sunshine.service -n 50"
fi
printf '[info] Sunshine capture validated on %s with %s encoding\n' "$selected_output" "$selected_family"

step "Leaving streaming services stopped"
systemctl --user stop diarchy-sunshine.service diarchy-display.service ||
  fail "Could not stop the streaming units. Run: systemctl --user stop diarchy-sunshine.service diarchy-display.service"
restore_kvm

step "Removing old Sunshine versions"
prune_old_versions sunshine "$TARGET_DIR"

if has_lid_switch; then
  step "Checking effective lid policy"
  if ! systemd-analyze cat-config systemd/logind.conf 2>/dev/null | grep -q '^HandleLidSwitch=ignore$'; then
    printf '[warn] Lid close is not ignored. Install a logind drop-in before closing the host lid.\n' >&2
  fi
fi

step "Host installation complete"
printf 'Streaming is started from the Diarchy app (or: diarchy-host stream start).\n'
printf 'Then run %s/diarchy-host-doctor <controller-address> for diagnostics.\n' "$BIN_DIR"
