#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
VERSION=6.1.0
DMG_NAME="Moonlight-${VERSION}.dmg"
DMG_URL="https://github.com/moonlight-stream/moonlight-qt/releases/download/v${VERSION}/${DMG_NAME}"
DMG_SHA256=d494740eead8ad4e620cdc8feedb56083bc29cabbbeef34cb82585fd87725fa2
MOONLIGHT_BUNDLE_ID=com.moonlight-stream.Moonlight
APPLICATIONS_DIR=${APPLICATIONS_DIR:-"$HOME/Applications"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}
MOONLIGHT_APP="$APPLICATIONS_DIR/Moonlight.app"

CURL_BIN=${CURL_BIN:-/usr/bin/curl}
SHA256_BIN=${SHA256_BIN:-/usr/bin/shasum}
HDIUTIL_BIN=${HDIUTIL_BIN:-/usr/bin/hdiutil}
CODESIGN_BIN=${CODESIGN_BIN:-/usr/bin/codesign}
SPCTL_BIN=${SPCTL_BIN:-/usr/sbin/spctl}
PLIST_BUDDY_BIN=${PLIST_BUDDY_BIN:-/usr/libexec/PlistBuddy}
DITTO_BIN=${DITTO_BIN:-/usr/bin/ditto}

fail() {
  printf 'install.sh: %s\n' "$*" >&2
  exit 1
}

bundle_id() {
  "$PLIST_BUDDY_BIN" -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null
}

assert_replaceable() {
  local path=$1 expected=$2 actual
  [[ -e "$path" ]] || return 0
  [[ -d "$path" ]] || fail "refusing to replace non-bundle path: $path"
  actual=$(bundle_id "$path") || fail "refusing to replace bundle without an identity: $path"
  [[ "$actual" == "$expected" ]] \
    || fail "refusing to replace $path with foreign bundle identifier $actual"
}

if [[ ${1:-} == --dry-run ]]; then
  [[ $# == 1 ]] || fail "usage: install.sh [--dry-run]"
  cat <<EOF
Would install Moonlight $VERSION from:
  $DMG_URL
Pinned SHA-256:
  $DMG_SHA256
Moonlight destination:
  $MOONLIGHT_APP
Command destinations:
  $BIN_DIR/diarchy-stream
  $BIN_DIR/diarchy-client-doctor
  $BIN_DIR/diarchy-config.sh
  $BIN_DIR/diarchy-host-key
EOF
  exit 0
elif (( $# > 0 )); then
  fail "usage: install.sh [--dry-run]"
fi

for command in "$CURL_BIN" "$SHA256_BIN" "$HDIUTIL_BIN" "$CODESIGN_BIN" \
  "$SPCTL_BIN" "$PLIST_BUDDY_BIN" "$DITTO_BIN"; do
  [[ -x "$command" ]] || fail "required command is not executable: $command"
done

tmp_base=${TMPDIR:-$HOME/Library/Caches}
mkdir -p -- "$tmp_base"
work_dir=$(mktemp -d "$tmp_base/diarchy-client.XXXXXX")
mount_dir="$work_dir/mount"
dmg="$work_dir/$DMG_NAME"
mounted=0
cleanup() {
  if (( mounted )); then
    "$HDIUTIL_BIN" detach "$mount_dir" -quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$work_dir"
}
trap cleanup EXIT INT TERM
mkdir -p "$mount_dir"

printf '[info] Downloading Moonlight %s\n' "$VERSION"
"$CURL_BIN" --fail --location --proto '=https' --proto-redir '=https' --silent --show-error -o "$dmg" "$DMG_URL"
actual_sha=$("$SHA256_BIN" -a 256 "$dmg" | awk '{print $1}')
[[ "$actual_sha" == "$DMG_SHA256" ]] \
  || fail "Moonlight checksum mismatch: expected $DMG_SHA256, got $actual_sha"

"$HDIUTIL_BIN" attach "$dmg" -nobrowse -readonly -mountpoint "$mount_dir" -quiet
mounted=1
source_app="$mount_dir/Moonlight.app"
[[ -d "$source_app" ]] || fail "Moonlight.app is missing from the mounted release"
[[ "$(bundle_id "$source_app")" == "$MOONLIGHT_BUNDLE_ID" ]] \
  || fail "downloaded Moonlight bundle identifier is unexpected"
"$CODESIGN_BIN" --verify --deep --strict "$source_app"
"$SPCTL_BIN" --assess --type execute "$source_app"

assert_replaceable "$MOONLIGHT_APP" "$MOONLIGHT_BUNDLE_ID"
mkdir -p "$APPLICATIONS_DIR" "$BIN_DIR"

moonlight_stage="$APPLICATIONS_DIR/.Moonlight.app.stage.$$"
"$DITTO_BIN" "$source_app" "$moonlight_stage"
[[ "$(bundle_id "$moonlight_stage")" == "$MOONLIGHT_BUNDLE_ID" ]] \
  || fail "staged Moonlight bundle identity changed"
# Swap by rename: keep the old bundle until the new one is in place.
if [[ -e $MOONLIGHT_APP ]]; then
  moonlight_backup="$APPLICATIONS_DIR/.Moonlight.app.old.$$"
  rm -rf "$moonlight_backup"
  mv "$MOONLIGHT_APP" "$moonlight_backup" || fail "could not move the existing Moonlight.app aside"
  if ! mv "$moonlight_stage" "$MOONLIGHT_APP"; then
    mv "$moonlight_backup" "$MOONLIGHT_APP" || true
    fail "could not install Moonlight.app; the previous version was restored"
  fi
  rm -rf "$moonlight_backup"
else
  mv "$moonlight_stage" "$MOONLIGHT_APP"
fi

install -m 0755 "$SOURCE_DIR/diarchy-stream" "$BIN_DIR/diarchy-stream"
install -m 0755 "$SOURCE_DIR/diarchy-client-doctor" \
  "$BIN_DIR/diarchy-client-doctor"
install -m 0755 "$SOURCE_DIR/diarchy-config.sh" "$BIN_DIR/diarchy-config.sh"
install -m 0755 "$SOURCE_DIR/diarchy-host-key" "$BIN_DIR/diarchy-host-key"
printf '[ok] Installed Moonlight %s at %s\n' "$VERSION" "$MOONLIGHT_APP"
printf '[info] Run %s to verify routes and signatures\n' \
  "$BIN_DIR/diarchy-client-doctor"
printf '[info] Open Diarchy.app to start Streaming\n'
