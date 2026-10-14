#!/usr/bin/env bash
set -euo pipefail

VERSION=0.11.0
SHA256=c43d211ec1af3eaea0cd585ac5c415776000355c3fcb9742fa2513f32dab55ba
URL="https://github.com/feschber/lan-mouse/releases/download/v${VERSION}/lan-mouse-linux-x86_64"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

CURL_BIN=${CURL_BIN:-curl}
SHA256_BIN=${SHA256_BIN:-sha256sum}
UNAME_BIN=${UNAME_BIN:-uname}
SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-systemctl}
DIARCHY_HOST_BIN=${DIARCHY_HOST_BIN:-$HOME/.local/bin/diarchy-host}

fail() {
  printf 'install-lan-mouse.sh: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: %s [--dry-run] [--authorize-fingerprint FINGERPRINT]\n' "$0" >&2
  exit 64
}

valid_fingerprint() {
  [[ $1 =~ ^([[:xdigit:]]{2}:){31}[[:xdigit:]]{2}$ ]]
}

fingerprint=
dry_run=0
while (( $# )); do
  case $1 in
    --dry-run)
      (( dry_run == 0 )) || usage
      dry_run=1
      shift
      ;;
    --authorize-fingerprint)
      (( $# >= 2 )) || usage
      [[ -z $fingerprint ]] || usage
      fingerprint=$2
      shift 2
      ;;
    *) usage ;;
  esac
done

if [[ -n $fingerprint ]] && ! valid_fingerprint "$fingerprint"; then
  printf 'install-lan-mouse.sh: invalid DTLS fingerprint\n' >&2
  exit 64
fi

if (( dry_run )); then
  cat <<EOF
Diarchy KVM host installation plan
- Install Lan Mouse ${VERSION} rootless under ~/.local/opt/lan-mouse-${VERSION}
- Verify SHA-256 ${SHA256}
- Listen on UDP 4242 inside the graphical user session
- Install diarchy-kvm.service and host doctor
- Write the Lan Mouse configuration through the diarchy-host agent
EOF
  exit 0
fi

[[ -f $DIARCHY_HOST_BIN && -x $DIARCHY_HOST_BIN ]] \
  || fail "diarchy-host agent is not installed (run install-host.sh)"

[[ $("$UNAME_BIN" -s) == Linux ]] || fail "host installer requires Linux"
[[ $("$UNAME_BIN" -m) == x86_64 ]] || fail "pinned release requires x86_64"
for command in "$CURL_BIN" "$SHA256_BIN" "$SYSTEMCTL_BIN" install mktemp cmp date; do
  command -v "$command" >/dev/null 2>&1 || [[ -x $command ]] \
    || fail "required command is missing: $command"
done

OPT_ROOT=$HOME/.local/opt
TARGET_DIR=$OPT_ROOT/lan-mouse-$VERSION
BIN_DIR=$HOME/.local/bin
CACHE_DIR=${XDG_CACHE_HOME:-$HOME/.cache}/diarchy-kvm
DOWNLOAD=$CACHE_DIR/lan-mouse-linux-x86_64-$VERSION
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
UNIT_FILE=$UNIT_DIR/diarchy-kvm.service

install -d -m 700 "$OPT_ROOT" "$BIN_DIR" "$CACHE_DIR" "$UNIT_DIR"

if [[ ! -f $DOWNLOAD ]] || ! printf '%s  %s\n' "$SHA256" "$DOWNLOAD" | "$SHA256_BIN" -c - >/dev/null 2>&1; then
  temporary_download=$DOWNLOAD.download.$$
  trap 'rm -f "${temporary_download:-}"' EXIT
  "$CURL_BIN" -sS --fail --location --proto '=https' --tlsv1.2 \
    --output "$temporary_download" "$URL" \
    || fail "could not download Lan Mouse from $URL; check the network of the host, then run this installer again"
  if ! printf '%s  %s\n' "$SHA256" "$temporary_download" | "$SHA256_BIN" -c - >/dev/null 2>&1; then
    fail "Lan Mouse checksum verification failed"
  fi
  chmod 0755 "$temporary_download"
  mv "$temporary_download" "$DOWNLOAD"
  trap - EXIT
fi

if [[ -e $TARGET_DIR ]]; then
  [[ -d $TARGET_DIR && -x $TARGET_DIR/lan-mouse ]] \
    || fail "refusing to replace unexpected path: $TARGET_DIR"
  installed_version=$("$TARGET_DIR/lan-mouse" --version 2>&1 || true)
  [[ $installed_version == *"lan-mouse $VERSION"* ]] \
    || fail "refusing to replace foreign Lan Mouse directory: $TARGET_DIR"
else
  stage=$OPT_ROOT/.lan-mouse-$VERSION.$$.stage
  trap 'rm -rf "${stage:-}"' EXIT
  install -d -m 700 "$stage"
  install -m 755 "$DOWNLOAD" "$stage/lan-mouse"
  installed_version=$("$stage/lan-mouse" --version 2>&1 || true)
  [[ $installed_version == *"lan-mouse $VERSION"* ]] \
    || fail "downloaded Lan Mouse version mismatch"
  mv "$stage" "$TARGET_DIR"
  trap - EXIT
fi

# B-12: keep a copy of a lan-mouse binary that Diarchy did not install (not identical to any version folder).
if [[ -f $BIN_DIR/lan-mouse ]]; then
  ours=0
  for candidate in "$OPT_ROOT"/lan-mouse-[0-9]*/lan-mouse; do
    if [[ -f $candidate ]] && cmp -s "$BIN_DIR/lan-mouse" "$candidate"; then ours=1; break; fi
  done
  if (( ! ours )); then
    backup=$BIN_DIR/lan-mouse.diarchy-backup-$(date -u +%Y%m%dT%H%M%SZ)
    cp -p "$BIN_DIR/lan-mouse" "$backup" \
      || fail "could not back up $BIN_DIR/lan-mouse to $backup; check free disk space"
    printf '[info] Backed up %s to %s\n' "$BIN_DIR/lan-mouse" "$backup"
  fi
fi
install -m 755 "$TARGET_DIR/lan-mouse" "$BIN_DIR/lan-mouse"

agent_call() {
  local output
  output=$("$DIARCHY_HOST_BIN" kvm "$@") \
    || fail "diarchy-host kvm $1 failed: ${output%%$'\n'*}"
}

install -m 755 "$SCRIPT_DIR/diarchy-kvm-host-doctor" "$BIN_DIR/diarchy-kvm-host-doctor"
install -m 644 "$SCRIPT_DIR/systemd/diarchy-kvm.service" "$UNIT_FILE"

agent_call configure
if [[ -n $fingerprint ]]; then
  agent_call authorize --fingerprint "$fingerprint"
fi

unit_running() {
  local state
  state=$("$SYSTEMCTL_BIN" --user show -p ActiveState --value "$1" 2>/dev/null) || return 1
  case $state in
    active | activating | reloading | deactivating) return 0 ;;
    *) return 1 ;;
  esac
}

"$SYSTEMCTL_BIN" --user daemon-reload \
  || fail "systemctl --user daemon-reload failed; is the user systemd session running? Run: systemctl --user status"
"$SYSTEMCTL_BIN" --user reenable diarchy-kvm.service \
  || fail "systemctl --user reenable diarchy-kvm.service failed; run: systemctl --user status diarchy-kvm.service"
# Streaming owns the input devices: never start Lan Mouse next to Sunshine (it starts again at the next login/mode change).
if unit_running diarchy-sunshine.service || unit_running diarchy-display.service; then
  printf '[info] Streaming is running; leaving diarchy-kvm.service stopped until you switch back to KVM.\n'
else
  "$SYSTEMCTL_BIN" --user restart diarchy-kvm.service \
    || fail "systemctl --user restart diarchy-kvm.service failed; run: journalctl --user -u diarchy-kvm.service -n 50"
  "$SYSTEMCTL_BIN" --user is-active --quiet diarchy-kvm.service \
    || fail "diarchy-kvm.service did not become active; run: journalctl --user -u diarchy-kvm.service -n 50"
fi

version_output=$("$BIN_DIR/lan-mouse" --version 2>&1)
[[ $version_output == *"lan-mouse $VERSION"* ]] || fail "installed version mismatch; run: $BIN_DIR/lan-mouse --version"

# B-32: after a verified install, remove the other numbered version folders. The folder in use stays.
for old in "$OPT_ROOT"/lan-mouse-[0-9]*; do
  [[ -d $old && ! -L $old && $old != "$TARGET_DIR" ]] || continue
  if rm -rf -- "$old"; then
    printf '[info] Removed old Lan Mouse folder %s\n' "$old"
  else
    printf '[warn] could not remove old Lan Mouse folder %s\n' "$old" >&2
  fi
done

printf '[ok] Installed Lan Mouse %s host receiver\n' "$VERSION"
if [[ -z $fingerprint ]]; then
  printf '[next] Pair this host from the Diarchy app (Settings → Connection → Pair).\n'
fi
printf '[info] Run %s/diarchy-kvm-host-doctor after pairing.\n' "$BIN_DIR"
