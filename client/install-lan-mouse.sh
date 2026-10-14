#!/usr/bin/env bash
set -euo pipefail

VERSION=0.11.0
ARCHIVE=lan-mouse-macos-arm64.zip
URL="https://github.com/feschber/lan-mouse/releases/download/v${VERSION}/${ARCHIVE}"
SHA256=5ff9965d05be7f125b1d75b9e7259d874ec98aa086ce40012522bc25406500a9
LAN_MOUSE_BUNDLE_ID=de.feschber.LanMouse
TOGGLE_BUNDLE_ID=dev.sebastiangmz.diarchy-kvm
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

APPLICATIONS_DIR=${APPLICATIONS_DIR:-$HOME/Applications}
BIN_DIR=${BIN_DIR:-$HOME/.local/bin}
LAN_MOUSE_APP="$APPLICATIONS_DIR/Lan Mouse.app"
LEGACY_TOGGLE_APP="$APPLICATIONS_DIR/Diarchy KVM.app"

CURL_BIN=${CURL_BIN:-/usr/bin/curl}
SHA256_BIN=${SHA256_BIN:-/usr/bin/shasum}
DITTO_BIN=${DITTO_BIN:-/usr/bin/ditto}
CODESIGN_BIN=${CODESIGN_BIN:-/usr/bin/codesign}
XATTR_BIN=${XATTR_BIN:-/usr/bin/xattr}
OPEN_BIN=${OPEN_BIN:-/usr/bin/open}
PLIST_BUDDY_BIN=${PLIST_BUDDY_BIN:-/usr/libexec/PlistBuddy}
UNAME_BIN=${UNAME_BIN:-/usr/bin/uname}

fail() {
  printf 'install-lan-mouse.sh: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: %s [--dry-run]\n' "$0" >&2
  exit 64
}

bundle_id() {
  "$PLIST_BUDDY_BIN" -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null
}

assert_replaceable() {
  local path=$1 expected=$2 actual
  [[ -e $path ]] || return 0
  [[ -d $path ]] || fail "refusing to replace non-bundle path: $path"
  actual=$(bundle_id "$path") || fail "refusing to replace bundle without identity: $path"
  [[ $actual == "$expected" ]] \
    || fail "refusing to replace $path with foreign bundle identifier $actual"
}

dry_run=0
while (( $# )); do
  case $1 in
    --dry-run)
      (( dry_run == 0 )) || usage
      dry_run=1
      shift
      ;;
    *) usage ;;
  esac
done

if (( dry_run )); then
  cat <<EOF
Diarchy KVM Mac installation plan
- Install Lan Mouse ${VERSION} at ~/Applications/Lan Mouse.app
- Verify SHA-256 ${SHA256}
- Install diarchy-kvm, diarchy-kvm-client-doctor, diarchy-kvm-accessibility-check, diarchy-config.sh, diarchy-route.sh, diarchy-ssh, diarchy-kvm-configure, diarchy-mac-fingerprint and diarchy-host-key under ~/.local/bin without a LaunchAgent
- Remove pre-rename ~/.local/bin/homelab-* commands and Home Lab Mirror, Home Lab Drop and HomeLab apps that this project owns
- Remove the legacy ~/Applications/Diarchy KVM.app only when its bundle identifier is ${TOGGLE_BUNDLE_ID}
- Run diarchy-kvm-configure to render ~/.config/lan-mouse/config.toml only when diarchy.conf loads
- Require the clipboard/file-transfer client installer before KVM enablement
EOF
  exit 0
fi

[[ $("$UNAME_BIN" -s) == Darwin ]] || fail "client installer requires macOS"
[[ $("$UNAME_BIN" -m) == arm64 ]] || fail "pinned release requires Apple Silicon"
for command in "$CURL_BIN" "$SHA256_BIN" "$DITTO_BIN" "$CODESIGN_BIN" \
  "$XATTR_BIN" "$OPEN_BIN" "$PLIST_BUDDY_BIN"; do
  [[ -x $command ]] || fail "required command is not executable: $command"
done

assert_replaceable "$LAN_MOUSE_APP" "$LAN_MOUSE_BUNDLE_ID"

tmp_base=${TMPDIR:-$HOME/Library/Caches}
mkdir -p -- "$tmp_base"
work_dir=$(mktemp -d "$tmp_base/diarchy-kvm-client.XXXXXX")
archive=$work_dir/$ARCHIVE
extract_dir=$work_dir/extract
mkdir -p "$extract_dir"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT INT TERM

"$CURL_BIN" --fail --location --proto '=https' --proto-redir '=https' --silent --show-error -o "$archive" "$URL"
actual_sha=$("$SHA256_BIN" -a 256 "$archive" | awk '{print $1}')
[[ $actual_sha == "$SHA256" ]] || fail "Lan Mouse checksum verification failed"
"$DITTO_BIN" -x -k "$archive" "$extract_dir"
source_app="$extract_dir/Lan Mouse.app"
source_binary="$source_app/Contents/MacOS/lan-mouse"
[[ -x $source_binary ]] || fail "release archive does not contain Lan Mouse.app"
[[ $(bundle_id "$source_app") == "$LAN_MOUSE_BUNDLE_ID" ]] \
  || fail "downloaded Lan Mouse bundle identifier is unexpected"
"$CODESIGN_BIN" --verify --deep --strict "$source_app" \
  || fail "downloaded Lan Mouse code signature is invalid"
version_output=$("$source_binary" --version 2>&1)
[[ $version_output == *"lan-mouse $VERSION"* ]] || fail "downloaded version mismatch"

mkdir -p "$APPLICATIONS_DIR" "$BIN_DIR"
lan_mouse_stage="$APPLICATIONS_DIR/.Lan Mouse.app.stage.$$"
rm -rf "$lan_mouse_stage"
"$DITTO_BIN" "$source_app" "$lan_mouse_stage"
"$XATTR_BIN" -rd com.apple.quarantine "$lan_mouse_stage"
[[ $(bundle_id "$lan_mouse_stage") == "$LAN_MOUSE_BUNDLE_ID" ]] \
  || fail "staged Lan Mouse bundle identity changed"
"$CODESIGN_BIN" --verify --deep --strict "$lan_mouse_stage" \
  || fail "staged Lan Mouse code signature is invalid"
# Swap by rename: keep the old bundle until the new one is in place.
if [[ -e $LAN_MOUSE_APP ]]; then
  lan_mouse_backup="$APPLICATIONS_DIR/.Lan Mouse.app.old.$$"
  rm -rf "$lan_mouse_backup"
  mv "$LAN_MOUSE_APP" "$lan_mouse_backup" || fail "could not move the existing Lan Mouse.app aside"
  if ! mv "$lan_mouse_stage" "$LAN_MOUSE_APP"; then
    mv "$lan_mouse_backup" "$LAN_MOUSE_APP" || true
    fail "could not install Lan Mouse.app; the previous version was restored"
  fi
  rm -rf "$lan_mouse_backup"
else
  mv "$lan_mouse_stage" "$LAN_MOUSE_APP"
fi

for command in diarchy-kvm diarchy-kvm-client-doctor diarchy-kvm-accessibility-check \
  diarchy-config.sh diarchy-route.sh diarchy-ssh diarchy-kvm-configure \
  diarchy-mac-fingerprint diarchy-host-key; do
  install -m 755 "$SOURCE_DIR/$command" "$BIN_DIR/$command"
done

# Remove what the pre-rename project (Home Lab Mirror) installed: its commands, and its apps when the bundle
# identifier shows this project owns them. Every removal is printed.
shopt -s nullglob
for legacy_command in "$BIN_DIR"/homelab-*; do
  if [[ -f $legacy_command || -L $legacy_command ]]; then
    rm -f -- "$legacy_command"
    printf '[ok] Removed legacy %s\n' "$legacy_command"
  fi
done
for legacy_app in "$APPLICATIONS_DIR"/"Home Lab Mirror"*.app "$APPLICATIONS_DIR"/"Home Lab Drop"*.app \
  "$APPLICATIONS_DIR"/HomeLab*.app; do
  [[ -d $legacy_app && ! -L $legacy_app ]] || continue
  legacy_id=$(bundle_id "$legacy_app" || true)
  if [[ $legacy_id == dev.sebastiangmz.* ]]; then
    rm -rf -- "$legacy_app"
    printf '[ok] Removed legacy %s\n' "$legacy_app"
  else
    printf '[info] Leaving %s in place: it is not owned by this project\n' "$legacy_app"
  fi
done
shopt -u nullglob

# The AppleScript toggle was replaced by the Diarchy app. Remove only a bundle
# this project owns; leave anything else in place.
if [[ -e $LEGACY_TOGGLE_APP ]]; then
  legacy_id=
  [[ -d $LEGACY_TOGGLE_APP ]] && legacy_id=$(bundle_id "$LEGACY_TOGGLE_APP" || true)
  if [[ $legacy_id == "$TOGGLE_BUNDLE_ID" ]]; then
    rm -rf "$LEGACY_TOGGLE_APP"
    printf '[ok] Removed legacy Diarchy KVM.app\n'
  else
    printf '[info] Leaving %s in place: it is not the Diarchy KVM toggle owned by this project\n' \
      "$LEGACY_TOGGLE_APP"
  fi
fi

# Same path resolution as diarchy_config_load.
config_path=${DIARCHY_CONFIG:-$HOME/.config/diarchy/diarchy.conf}
if config_reason=$( (source "$BIN_DIR/diarchy-config.sh" && diarchy_config_load) 2>&1 >/dev/null ); then
  "$BIN_DIR/diarchy-kvm-configure"
elif [[ -e $config_path ]]; then
  printf '[info] diarchy.conf is not usable (%s); skipping Lan Mouse configuration\n' \
    "${config_reason#diarchy-config: }"
else
  printf '[info] Not configured yet; open Diarchy to create diarchy.conf\n'
fi

"$OPEN_BIN" -gj "$LAN_MOUSE_APP"
printf '[ok] Installed Lan Mouse %s and Diarchy KVM commands\n' "$VERSION"
printf '[next] Run %s/install-clipboard-file-transfer.sh, grant Accessibility to Lan Mouse in System Settings, then run %s/diarchy-kvm-client-doctor.\n' "$SOURCE_DIR" "$BIN_DIR"
