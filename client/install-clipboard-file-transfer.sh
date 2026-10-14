#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
DROP_BUNDLE_ID=dev.sebastiangmz.diarchy-drop
FILE_PORTAL_BUNDLE_ID=dev.sebastiangmz.diarchy-file-portal
APPLICATIONS_DIR=${APPLICATIONS_DIR:-"$HOME/Applications"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}
DROP_APP="$APPLICATIONS_DIR/Diarchy Drop.app"
FILE_PORTAL_APP="$APPLICATIONS_DIR/Diarchy File Portal.app"

UNAME_BIN=${UNAME_BIN:-/usr/bin/uname}
OSACOMPILE_BIN=${OSACOMPILE_BIN:-/usr/bin/osacompile}
SWIFTC_BIN=${SWIFTC_BIN:-/usr/bin/swiftc}
CODESIGN_BIN=${CODESIGN_BIN:-/usr/bin/codesign}
PLIST_BUDDY_BIN=${PLIST_BUDDY_BIN:-/usr/libexec/PlistBuddy}
INSTALL_BIN=${INSTALL_BIN:-/usr/bin/install}
MV_BIN=${MV_BIN:-/bin/mv}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}
ATOMIC_APP_REPLACE_BIN=${ATOMIC_APP_REPLACE_BIN:-}
PBCOPY_BIN=${PBCOPY_BIN:-/usr/bin/pbcopy}
PBPASTE_BIN=${PBPASTE_BIN:-/usr/bin/pbpaste}
SSH_BIN=${SSH_BIN:-/usr/bin/ssh}
RSYNC_BIN=${RSYNC_BIN:-/usr/bin/rsync}

fail() {
  printf '[fail] install-clipboard-file-transfer.sh: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: %s [--dry-run]\n' "$0" >&2
  exit 64
}

bundle_id() {
  "$PLIST_BUDDY_BIN" -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null
}

refuse_symlink() {
  local path=$1
  [[ ! -L "$path" ]] || fail "refusing to replace symlink: $path"
}

assert_replaceable_bundle() {
  local path=$1 expected=$2 actual
  refuse_symlink "$path"
  [[ -e "$path" ]] || return 0
  [[ -d "$path" ]] || fail "refusing to replace non-bundle path: $path"
  actual=$(bundle_id "$path") || fail "refusing to replace bundle without identity: $path"
  [[ "$actual" == "$expected" ]] || fail "refusing to replace $path with foreign bundle identifier $actual"
}

assert_replaceable_dir() {
  local path=$1
  refuse_symlink "$path"
  [[ ! -e "$path" || -d "$path" ]] || fail "refusing to replace non-directory path: $path"
}

if [[ ${1:-} == --dry-run ]]; then
  [[ $# == 1 ]] || usage
  cat <<EOF
Diarchy clipboard/file-transfer client installation plan
- Install reviewed commands under ~/.local/bin
- Install private drop folders under ~/Drop
- Install ~/Applications/Diarchy Drop.app with bundle id $DROP_BUNDLE_ID
- Compile and install ~/Applications/Diarchy File Portal.app with bundle id $FILE_PORTAL_BUNDLE_ID
- Install ~/.local/bin/diarchy-file-portal-receiver
- Install ~/.local/bin/diarchy-file-portal-send
EOF
  exit 0
elif (( $# > 0 )); then
  usage
fi

[[ "$("$UNAME_BIN" -s)" == Darwin ]] || fail "client installer requires macOS"
for command in \
  "$PBCOPY_BIN" \
  "$PBPASTE_BIN" \
  "$SSH_BIN" \
  "$RSYNC_BIN" \
  "$OSACOMPILE_BIN" \
  "$SWIFTC_BIN" \
  "$CODESIGN_BIN" \
  "$PLIST_BUDDY_BIN" \
  "$INSTALL_BIN" \
  "$MV_BIN" \
  "$PYTHON_BIN"; do
  [[ -x "$command" ]] || fail "required command is not executable: $command"
done
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' \
  || fail "required Python version is 3.9 or newer: $PYTHON_BIN"
# SSH pairing is checked only once a Diarchy config exists; the first checklist run
# installs these tools before the app has written one (Ruling R7).
ssh_preflight() {
  (
    # shellcheck source=diarchy-route.sh
    source "$SOURCE_DIR/diarchy-route.sh"
    diag_file=$(mktemp "${TMPDIR:-$HOME/Library/Caches}/diarchy-ssh-preflight.XXXXXX") || fail "cannot create temporary file"
    trap 'rm -f "$diag_file"' EXIT
    if ! select_diarchy_route auto 2>"$diag_file"; then
      reason=$(tail -n 1 "$diag_file")
      fail "cannot verify SSH pairing: ${reason#diarchy-route: }"
    fi
    diarchy_host_key_pinned "$DIARCHY_HOST_KEY_ALIAS" \
      || fail "host key not confirmed. Open Diarchy and confirm the host key."
    # Same argv as diarchy-ssh: pinned route options (strict host key, BatchMode) and a bounded connect.
    if ! "$SSH_BIN" "${diarchy_ssh_options[@]}" \
      -o ConnectTimeout=5 \
      "$DIARCHY_SSH_ALIAS" -- true >/dev/null 2>&1; then
      identity_option=
      [[ -z ${DIARCHY_SSH_IDENTITY_FILE:-} ]] || identity_option=" -i $DIARCHY_SSH_IDENTITY_FILE.pub"
      fail "SSH pairing for $DIARCHY_SSH_USER@$DIARCHY_ROUTE_HOST ($DIARCHY_ROUTE_NAME route) failed; pair manually with ssh-copy-id -p $DIARCHY_SSH_PORT$identity_option $DIARCHY_SSH_USER@$DIARCHY_ROUTE_HOST"
    fi
  ) || exit 1
}
# Same path resolution as diarchy_config_load.
config_path=${DIARCHY_CONFIG:-$HOME/.config/diarchy/diarchy.conf}
if [[ ${DIARCHY_SKIP_SSH_PREFLIGHT:-} == 1 ]]; then
  printf '[info] SSH preflight skipped (checked by the app)\n'
elif config_reason=$( (source "$SOURCE_DIR/diarchy-config.sh" && diarchy_config_load) 2>&1 >/dev/null ); then
  ssh_preflight
elif [[ -e $config_path ]]; then
  printf '[info] diarchy.conf is not usable (%s); skipping SSH preflight\n' \
    "${config_reason#diarchy-config: }"
else
  printf '[info] Not configured yet; skipping SSH preflight\n'
fi
if [[ -n "$ATOMIC_APP_REPLACE_BIN" ]]; then
  [[ -x "$ATOMIC_APP_REPLACE_BIN" ]] || fail "required command is not executable: $ATOMIC_APP_REPLACE_BIN"
fi

for path in \
  "$BIN_DIR/diarchy-clipboard-bridge" \
  "$BIN_DIR/diarchy-kvm" \
  "$BIN_DIR/diarchy-file" \
  "$BIN_DIR/diarchy-route.sh" \
  "$BIN_DIR/diarchy-config.sh" \
  "$BIN_DIR/diarchy-ssh" \
  "$BIN_DIR/diarchy-host-key" \
  "$BIN_DIR/diarchy-clipboard-file-transfer-client-doctor" \
  "$BIN_DIR/diarchy-file-portal-receiver" \
  "$BIN_DIR/diarchy-file-portal-send"; do
  refuse_symlink "$path"
done
assert_replaceable_dir "$HOME/Drop/from-host"
assert_replaceable_bundle "$DROP_APP" "$DROP_BUNDLE_ID"
assert_replaceable_bundle "$FILE_PORTAL_APP" "$FILE_PORTAL_BUNDLE_ID"

work_dir=$(mktemp -d "${TMPDIR:-$HOME/Library/Caches}/diarchy-drop-client.XXXXXX")
backup_dir="$work_dir/backup"
stage_parent=
drop_stage_app=
file_portal_stage_app=
compiled_file_portal_bin="$work_dir/diarchy-file-portal"
atomic_replace_helper="$work_dir/atomic-app-replace.py"
lock_dir="$APPLICATIONS_DIR/.Diarchy Drop.install.lock"
commit_started=0
lock_taken=0

owned_paths=(
  "$BIN_DIR/diarchy-clipboard-bridge"
  "$BIN_DIR/diarchy-kvm"
  "$BIN_DIR/diarchy-file"
  "$BIN_DIR/diarchy-route.sh"
  "$BIN_DIR/diarchy-config.sh"
  "$BIN_DIR/diarchy-ssh"
  "$BIN_DIR/diarchy-host-key"
  "$BIN_DIR/diarchy-clipboard-file-transfer-client-doctor"
  "$BIN_DIR/diarchy-file-portal-receiver"
  "$BIN_DIR/diarchy-file-portal-send"
  "$HOME/Drop/from-host"
  "$DROP_APP"
  "$FILE_PORTAL_APP"
)

restore_backup() {
  local index path saved
  for (( index=${#owned_paths[@]} - 1; index >= 0; index-- )); do
    path=${owned_paths[$index]}
    saved="$backup_dir/$index"
    rm -rf -- "$path"
    if [[ -e "$saved" || -L "$saved" ]]; then
      mkdir -p -- "$(dirname -- "$path")"
      cp -a "$saved" "$path"
    fi
  done
}

release_lock() {
  if (( lock_taken )); then
    rm -f -- "$lock_dir/owner"
    rmdir "$lock_dir" >/dev/null 2>&1 || true
    lock_taken=0
  fi
}

cleanup() {
  local rc=$?
  set +e
  if (( commit_started )) && (( rc != 0 )); then
    restore_backup
  fi
  release_lock
  if [[ -n "$stage_parent" ]]; then
    rm -rf -- "$stage_parent"
  fi
  rm -rf "$work_dir"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$APPLICATIONS_DIR"
lock_attempts=0
while ! mkdir -m 700 "$lock_dir" 2>/dev/null; do
  owner_pid=
  if [[ -d "$lock_dir" && ! -L "$lock_dir" && -f "$lock_dir/owner" ]]; then
    IFS= read -r owner_pid <"$lock_dir/owner" || true
  fi
  if [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
    stale_lock="${lock_dir}.stale.$$"
    if mv "$lock_dir" "$stale_lock" 2>/dev/null; then
      rm -rf -- "$stale_lock"
      continue
    fi
  fi
  lock_attempts=$((lock_attempts + 1))
  (( lock_attempts < 600 )) || fail "timed out waiting for installer lock: $lock_dir"
  sleep 0.05
done
lock_taken=1
printf '%s\n' "$$" >"$lock_dir/owner"

mkdir -p "$backup_dir"
for index in "${!owned_paths[@]}"; do
  path=${owned_paths[$index]}
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a "$path" "$backup_dir/$index"
  fi
done

publish_bundle() {
  local staged=$1 live=$2
  if [[ -e "$live" ]]; then
    if [[ -n "$ATOMIC_APP_REPLACE_BIN" ]]; then
      DIARCHY_INSTALL_PHASE=commit "$ATOMIC_APP_REPLACE_BIN" "$staged" "$live"
    else
      [[ -x "$PYTHON_BIN" ]] || fail "required command is not executable: $PYTHON_BIN"
      cat >"$atomic_replace_helper" <<'PY'
import ctypes
import os
import sys

AT_FDCWD = -2
RENAME_SWAP = 0x00000002

old_path = os.fsencode(sys.argv[1])
new_path = os.fsencode(sys.argv[2])
libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
result = libc.renameatx_np(AT_FDCWD, old_path, AT_FDCWD, new_path, RENAME_SWAP)
if result != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error), sys.argv[2])
PY
      DIARCHY_INSTALL_PHASE=commit "$PYTHON_BIN" "$atomic_replace_helper" "$staged" "$live"
    fi
  else
    DIARCHY_INSTALL_PHASE=commit "$MV_BIN" "$staged" "$live"
  fi
}

build_file_portal_app() {
  "$INSTALL_BIN" -d -m 755 "$file_portal_stage_app/Contents/MacOS" "$file_portal_stage_app/Contents/Resources"
  "$SWIFTC_BIN" -O -framework AppKit \
    "$SOURCE_DIR/File Portal Staging.swift" \
    "$SOURCE_DIR/Diarchy File Portal.swift" \
    -o "$compiled_file_portal_bin"
  "$INSTALL_BIN" -m 755 "$compiled_file_portal_bin" "$file_portal_stage_app/Contents/MacOS/diarchy-file-portal"
  cat >"$file_portal_stage_app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>diarchy-file-portal</string>
  <key>CFBundleIdentifier</key>
  <string>$FILE_PORTAL_BUNDLE_ID</string>
  <key>CFBundleName</key>
  <string>Diarchy File Portal</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
EOF
  "$CODESIGN_BIN" --force --deep --sign - "$file_portal_stage_app"
  [[ "$(bundle_id "$file_portal_stage_app")" == "$FILE_PORTAL_BUNDLE_ID" ]] || fail "compiled file portal bundle identity is unexpected"
}

commit_started=1
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -d -m 700 "$APPLICATIONS_DIR"

DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -d -m 700 "$BIN_DIR" "$HOME/Drop/from-host"
stage_parent=$(mktemp -d "$APPLICATIONS_DIR/.Diarchy Drop.stage.XXXXXX")
drop_stage_app="$stage_parent/Diarchy Drop.app"
file_portal_stage_app="$stage_parent/Diarchy File Portal.app"
"$OSACOMPILE_BIN" -o "$drop_stage_app" "$SOURCE_DIR/Diarchy Drop.applescript"
if ! "$PLIST_BUDDY_BIN" -c "Set :CFBundleIdentifier $DROP_BUNDLE_ID" \
  "$drop_stage_app/Contents/Info.plist" >/dev/null 2>&1; then
  "$PLIST_BUDDY_BIN" -c "Add :CFBundleIdentifier string $DROP_BUNDLE_ID" \
    "$drop_stage_app/Contents/Info.plist"
fi
"$CODESIGN_BIN" --force --deep --sign - "$drop_stage_app"
[[ "$(bundle_id "$drop_stage_app")" == "$DROP_BUNDLE_ID" ]] || fail "compiled drop bundle identity is unexpected"
build_file_portal_app
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-clipboard-bridge" "$BIN_DIR/diarchy-clipboard-bridge"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-kvm" "$BIN_DIR/diarchy-kvm"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-file" "$BIN_DIR/diarchy-file"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-route.sh" "$BIN_DIR/diarchy-route.sh"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-config.sh" "$BIN_DIR/diarchy-config.sh"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-ssh" "$BIN_DIR/diarchy-ssh"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-host-key" "$BIN_DIR/diarchy-host-key"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-clipboard-file-transfer-client-doctor" \
  "$BIN_DIR/diarchy-clipboard-file-transfer-client-doctor"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-file-portal-receiver" \
  "$BIN_DIR/diarchy-file-portal-receiver"
DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m 755 "$SOURCE_DIR/diarchy-file-portal-send" \
  "$BIN_DIR/diarchy-file-portal-send"
publish_bundle "$drop_stage_app" "$DROP_APP"
drop_stage_app=
publish_bundle "$file_portal_stage_app" "$FILE_PORTAL_APP"
file_portal_stage_app=
commit_started=0
release_lock
trap - EXIT
if [[ -n "$stage_parent" ]]; then
  rm -rf -- "$stage_parent"
fi
rm -rf "$work_dir"

printf '[ok] Installed Diarchy clipboard/file-transfer client tools\n'
printf '[ok] Installed Diarchy Drop at %s\n' "$DROP_APP"
printf '[ok] Installed Diarchy File Portal at %s\n' "$FILE_PORTAL_APP"
