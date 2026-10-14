#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}
UNIT_DIR=${XDG_CONFIG_HOME:-"$HOME/.config"}/systemd/user
UNIT_FILE="$UNIT_DIR/diarchy-file-portal.service"
INSTALL_BIN=${INSTALL_BIN:-/usr/bin/install}
WL_COPY_BIN=${WL_COPY_BIN:-/usr/bin/wl-copy}
WL_PASTE_BIN=${WL_PASTE_BIN:-/usr/bin/wl-paste}
RSYNC_BIN=${RSYNC_BIN:-/usr/bin/rsync}
NOTIFY_SEND_BIN=${NOTIFY_SEND_BIN:-/usr/bin/notify-send}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}
SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-/usr/bin/systemctl}
CP_BIN=${CP_BIN:-/bin/cp}
RM_BIN=${RM_BIN:-/bin/rm}
CHMOD_BIN=${CHMOD_BIN:-/bin/chmod}
MKTEMP_BIN=${MKTEMP_BIN:-/usr/bin/mktemp}
MV_BIN=${MV_BIN:-/bin/mv}

fail() {
  printf '[fail] install-clipboard-file-transfer.sh: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'usage: %s [--dry-run]\n' "$0" >&2
  exit 64
}

refuse_symlink() {
  local path=$1
  [[ ! -L "$path" ]] || fail "refusing to replace symlink: $path"
}

assert_replaceable_dir() {
  local path=$1
  refuse_symlink "$path"
  [[ ! -e "$path" || -d "$path" ]] || fail "refusing to replace non-directory path: $path"
}

if [[ ${1:-} == --dry-run ]]; then
  [[ $# == 1 ]] || usage
  cat <<'EOF'
Diarchy clipboard/file-transfer host installation plan
- Install reviewed host commands under ~/.local/bin
- Install private drop folders under ~/Drop
- Install diarchy-file-portal-ingest for atomic Mac portal drops
- Install diarchy-file-portal.service under ~/.config/systemd/user
EOF
  exit 0
elif (( $# > 0 )); then
  usage
fi

for command in "$INSTALL_BIN" "$WL_COPY_BIN" "$WL_PASTE_BIN" "$RSYNC_BIN" "$PYTHON_BIN" "$SYSTEMCTL_BIN" "$NOTIFY_SEND_BIN" "$CP_BIN" "$RM_BIN" "$CHMOD_BIN" "$MKTEMP_BIN" "$MV_BIN"; do
  [[ -x "$command" ]] || fail "required command is not executable: $command"
done
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
  || fail "required Python version is 3.11 or newer: $PYTHON_BIN"
"$PYTHON_BIN" -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("GtkLayerShell", "0.1"); from gi.repository import Gdk, Gtk, GtkLayerShell' \
  || fail "required PyGObject GTK 3 and GtkLayerShell 0.1 are unavailable"
"$SYSTEMCTL_BIN" is-active --quiet sshd.service \
  || fail "required sshd.service is not active"
for path in \
  "$BIN_DIR/diarchy-clipboard-remote" \
  "$BIN_DIR/diarchy-file-transfer-host-doctor" \
  "$BIN_DIR/diarchy-file-portal" \
  "$BIN_DIR/diarchy-file-portal-remote" \
  "$BIN_DIR/diarchy-file-portal-ingest" \
  "$UNIT_FILE"; do
  refuse_symlink "$path"
done
assert_replaceable_dir "$HOME/Drop/from-mac"
assert_replaceable_dir "$HOME/Drop/to-mac"
assert_replaceable_dir "$UNIT_DIR"

lock_dir="$HOME/.diarchy-file-transfer.install.lock"
lock_taken=0
release_lock() {
  if (( lock_taken == 1 )); then
    "$RM_BIN" -f -- "$lock_dir/owner"
    rmdir -- "$lock_dir" >/dev/null 2>&1
    lock_taken=0
  fi
}
early_cleanup() {
  local rc=$?
  set +e
  if [[ -n ${backup_root:-} ]]; then
    "$RM_BIN" -rf -- "$backup_root"
  fi
  release_lock
  exit "$rc"
}
lock_attempts=0
while ! mkdir -m 700 "$lock_dir" 2>/dev/null; do
  lock_attempts=$((lock_attempts + 1))
  (( lock_attempts < 600 )) || fail "timed out waiting for installer lock: $lock_dir"
  sleep 0.05
done
lock_taken=1
trap early_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$$" >"$lock_dir/owner"

service_was_enabled=0
service_was_active=0
"$SYSTEMCTL_BIN" --user is-enabled --quiet diarchy-file-portal.service >/dev/null 2>&1 \
  && service_was_enabled=1
"$SYSTEMCTL_BIN" --user is-active --quiet diarchy-file-portal.service >/dev/null 2>&1 \
  && service_was_active=1

backup_root=$("$MKTEMP_BIN" -d "${TMPDIR:-/tmp}/diarchy-host-install.XXXXXX")
transaction_active=1
stage_files=()
file_destinations=(
  "$BIN_DIR/diarchy-clipboard-remote"
  "$BIN_DIR/diarchy-file-transfer-host-doctor"
  "$BIN_DIR/diarchy-file-portal"
  "$BIN_DIR/diarchy-file-portal-remote"
  "$BIN_DIR/diarchy-file-portal-ingest"
  "$UNIT_FILE"
)
directory_destinations=(
  "$BIN_DIR"
  "$HOME/Drop/from-mac"
  "$HOME/Drop/to-mac"
  "$UNIT_DIR"
)
file_existed=()
directory_existed=()
directory_modes=()

for index in "${!file_destinations[@]}"; do
  destination=${file_destinations[$index]}
  if [[ -e "$destination" ]]; then
    file_existed[$index]=1
    "$CP_BIN" -a -- "$destination" "$backup_root/file-$index"
  else
    file_existed[$index]=0
  fi
done
for index in "${!directory_destinations[@]}"; do
  destination=${directory_destinations[$index]}
  if [[ -d "$destination" ]]; then
    directory_existed[$index]=1
    directory_modes[$index]=$("$PYTHON_BIN" -c 'import os, stat, sys; print(oct(stat.S_IMODE(os.stat(sys.argv[1], follow_symlinks=False).st_mode))[2:])' "$destination")
  else
    directory_existed[$index]=0
    directory_modes[$index]=
  fi
done

rollback() {
  local index destination
  (( transaction_active == 1 )) || return 0
  set +e
  for (( index=${#file_destinations[@]} - 1; index >= 0; index-- )); do
    destination=${file_destinations[$index]}
    "$RM_BIN" -rf -- "$destination"
    if [[ ${file_existed[$index]} == 1 ]]; then
      "$CP_BIN" -a -- "$backup_root/file-$index" "$destination"
    fi
  done
  for (( index=${#directory_destinations[@]} - 1; index >= 0; index-- )); do
    destination=${directory_destinations[$index]}
    if [[ ${directory_existed[$index]} == 1 ]]; then
      "$CHMOD_BIN" "${directory_modes[$index]}" -- "$destination"
    else
      rmdir -- "$destination" >/dev/null 2>&1
    fi
  done
  rmdir -- "$HOME/Drop" "$HOME/.local" "$HOME/.config/systemd/user" \
    "$HOME/.config/systemd" "$HOME/.config" >/dev/null 2>&1
  "$SYSTEMCTL_BIN" --user daemon-reload >/dev/null 2>&1
  if (( service_was_enabled == 1 )); then
    "$SYSTEMCTL_BIN" --user enable diarchy-file-portal.service >/dev/null 2>&1
  else
    "$SYSTEMCTL_BIN" --user disable diarchy-file-portal.service >/dev/null 2>&1
  fi
  if (( service_was_active == 1 )); then
    "$SYSTEMCTL_BIN" --user restart diarchy-file-portal.service >/dev/null 2>&1
  else
    "$SYSTEMCTL_BIN" --user stop diarchy-file-portal.service >/dev/null 2>&1
  fi
}

cleanup() {
  local rc=$?
  set +e
  rollback
  if (( ${#stage_files[@]} > 0 )); then
    "$RM_BIN" -f -- "${stage_files[@]}"
  fi
  "$RM_BIN" -rf -- "$backup_root"
  release_lock
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

publish_file() {
  local source=$1 destination=$2 mode=$3 parent stage
  parent=$(dirname -- "$destination")
  stage=$("$MKTEMP_BIN" "$parent/.diarchy-file-transfer.install.XXXXXX")
  stage_files+=("$stage")
  DIARCHY_INSTALL_PHASE=commit "$INSTALL_BIN" -m "$mode" "$source" "$stage"
  "$MV_BIN" "$stage" "$destination"
}
"$INSTALL_BIN" -d -m 700 "$BIN_DIR" "$HOME/Drop/from-mac" "$HOME/Drop/to-mac" "$UNIT_DIR"
publish_file "$SCRIPT_DIR/diarchy-clipboard-remote" "$BIN_DIR/diarchy-clipboard-remote" 755
publish_file "$SCRIPT_DIR/diarchy-file-transfer-host-doctor" \
  "$BIN_DIR/diarchy-file-transfer-host-doctor" 755
publish_file "$SCRIPT_DIR/diarchy-file-portal" "$BIN_DIR/diarchy-file-portal" 755
publish_file "$SCRIPT_DIR/diarchy-file-portal-remote" "$BIN_DIR/diarchy-file-portal-remote" 755
publish_file "$SCRIPT_DIR/diarchy-file-portal-ingest" "$BIN_DIR/diarchy-file-portal-ingest" 755
publish_file "$SCRIPT_DIR/systemd/diarchy-file-portal.service" "$UNIT_FILE" 644
"$SYSTEMCTL_BIN" --user daemon-reload
"$SYSTEMCTL_BIN" --user enable diarchy-file-portal.service
"$SYSTEMCTL_BIN" --user restart diarchy-file-portal.service

transaction_active=0
printf '[ok] Installed Diarchy clipboard/file-transfer host tools\n'
