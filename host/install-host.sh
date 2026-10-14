#!/usr/bin/env bash
# Diarchy host installer: installs the diarchy-host agent, asks it whether this machine is ready,
# and only then runs the component installers. Never executes sudo: pending privileged steps are printed.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
BIN_DIR=$HOME/.local/bin
AGENT_BIN=$BIN_DIR/diarchy-host
AGENT_DIR=${DIARCHY_AGENT_DIR:-$HOME/.local/share/diarchy/agent}

fail() {
  printf '[fail] install-host.sh: %s\n' "$*" >&2
  exit "${FAIL_STATUS:-2}"
}

usage() {
  printf 'usage: %s [--with-streaming]\n' "$0" >&2
  exit 64
}

# Reads the probe envelope on stdin. Exit 0: host may proceed (recommended actions printed as warnings);
# exit 2: the failure was already written to stderr.
# shellcheck disable=SC2016
PROBE_GATE='
import json
import sys


def die(*lines):
    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(2)


def one_line(value):
    return " ".join(str(value).split())


def failed(message):
    die("[fail] install-host.sh: diarchy-host probe failed: " + one_line(message))


try:
    body = json.loads(sys.stdin.read())
except ValueError:
    failed("output is not valid JSON")
if not isinstance(body, dict):
    failed("output is not a JSON object")
if body.get("ok") is not True:
    error = body.get("error")
    failed(error.get("message", "unknown error") if isinstance(error, dict) else "unknown error")
platform = body.get("platform")
actions = body.get("required_actions")
if not isinstance(platform, dict) or not isinstance(platform.get("supported"), bool) or not isinstance(actions, list):
    failed("output lacks platform or required_actions")

if not platform["supported"]:
    reasons = platform.get("reasons")
    reasons = reasons if isinstance(reasons, list) else []
    die("[fail] install-host.sh: unsupported platform: " + "; ".join(one_line(reason) for reason in reasons))

actions = [action for action in actions if isinstance(action, dict)]
required = [action for action in actions if action.get("severity") == "required"]
if required:
    lines = ["[fail] install-host.sh: required host actions are pending"]
    for action in required:
        lines.append("  - %s: %s" % (one_line(action.get("id", "")), one_line(action.get("title", ""))))
        lines.append("    " + one_line(action.get("command", "")))
    die(*lines)

for action in actions:
    if action.get("severity") == "recommended":
        print("[warn] %s: %s" % (one_line(action.get("id", "")), one_line(action.get("command", ""))))
'

install_agent() {
  local parent staged previous
  parent=$(dirname -- "$AGENT_DIR")
  staged=$AGENT_DIR.new.$$
  previous=$AGENT_DIR.old.$$

  install -d -m 700 "$parent" "$BIN_DIR"
  rm -rf "$staged" "$previous"
  # A signal between the two moves leaves AGENT_DIR missing: put the previous agent back before cleaning up.
  trap 'if [[ ! -e $AGENT_DIR && -e $previous ]]; then mv "$previous" "$AGENT_DIR"; fi; rm -rf "$staged" "$previous"' EXIT
  install -d -m 700 "$staged"
  cp -R "$SCRIPT_DIR/diarchy_host" "$staged/diarchy_host"
  find "$staged" -name __pycache__ -type d -prune -exec rm -rf {} +
  # macOS tar/cp can carry AppleDouble metadata files (`._*`); they are never part of the agent.
  find "$staged" -name '._*' -exec rm -rf {} +
  cp "$SCRIPT_DIR/VERSION" "$staged/VERSION"

  if [[ -e $AGENT_DIR ]]; then
    mv "$AGENT_DIR" "$previous"
  fi
  if ! mv "$staged" "$AGENT_DIR"; then
    if [[ -e $previous ]]; then
      mv "$previous" "$AGENT_DIR"
    fi
    fail "could not install the agent into $AGENT_DIR"
  fi
  rm -rf "$previous"
  trap - EXIT

  install -m 755 "$SCRIPT_DIR/diarchy-host" "$AGENT_BIN"
}

# B-07: units, binaries and folders of the pre-rename "homelab" install keep running next to Diarchy (the old
# homelab-kvm.service also listens on UDP 4242). Stop and disable each old unit, then remove the files. Uses only
# shell builtins and rm so it works on the minimal PATH of this installer.
migrate_pre_rename() {
  local systemctl_bin=${SYSTEMCTL_BIN:-systemctl}
  local config_home=${XDG_CONFIG_HOME:-$HOME/.config}
  local unit_dir=$config_home/systemd/user
  local path name removed_unit=0
  local -a units=() paths=()

  shopt -s nullglob
  for path in "$unit_dir"/homelab-*; do
    paths+=("$path")
    name=${path##*/}
    case $name in
      *.service | *.socket | *.timer | *.path | *.target) units+=("$name") ;;
    esac
  done
  for path in "$unit_dir"/*.wants/homelab-* "$unit_dir"/*.requires/homelab-* \
    "$BIN_DIR"/homelab-* "$HOME"/.local/share/homelab* "$config_home"/homelab*; do
    paths+=("$path")
  done
  shopt -u nullglob

  for name in ${units[@]+"${units[@]}"}; do
    "$systemctl_bin" --user stop "$name" ||
      printf '[warn] install-host.sh: could not stop %s. Run: systemctl --user status %s\n' "$name" "$name" >&2
    "$systemctl_bin" --user disable "$name" ||
      printf '[warn] install-host.sh: could not disable %s. Run: systemctl --user disable %s\n' "$name" "$name" >&2
    removed_unit=1
  done
  for path in ${paths[@]+"${paths[@]}"}; do
    [[ -e $path || -L $path ]] || continue
    if rm -rf -- "$path"; then
      printf '[info] Removed pre-rename path %s\n' "$path"
    else
      printf '[warn] install-host.sh: could not remove %s. Remove it by hand.\n' "$path" >&2
    fi
  done
  if (( removed_unit )); then
    "$systemctl_bin" --user daemon-reload ||
      printf '[warn] install-host.sh: systemctl --user daemon-reload failed. Run it by hand.\n' >&2
  fi
}

with_streaming=0
while (( $# )); do
  case $1 in
    --with-streaming) with_streaming=1 ;;
    *) usage ;;
  esac
  shift
done

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
[[ -d $SCRIPT_DIR/diarchy_host ]] || fail "diarchy_host/ is missing next to install-host.sh"
[[ -f $SCRIPT_DIR/diarchy-host ]] || fail "diarchy-host is missing next to install-host.sh"
[[ -f $SCRIPT_DIR/VERSION ]] || fail "VERSION is missing next to install-host.sh"

install_agent

probe_args=(probe)
if (( with_streaming )); then
  probe_args+=(--streaming)
fi
probe_output=$("$AGENT_BIN" "${probe_args[@]}") || true
printf '%s\n' "$probe_output" | python3 -c "$PROBE_GATE" || exit 2

migrate_pre_rename

installers=(install-lan-mouse.sh install-clipboard-file-transfer.sh)
if (( with_streaming )); then
  installers+=(install.sh)
fi
for installer in "${installers[@]}"; do
  bash "$SCRIPT_DIR/$installer" || FAIL_STATUS=1 fail "$installer failed. Run it by hand to see the full output: bash $SCRIPT_DIR/$installer"
done

version=
if [[ -f $SCRIPT_DIR/../VERSION ]]; then
  version=$(<"$SCRIPT_DIR/../VERSION")
else
  version=$(<"$SCRIPT_DIR/VERSION")
fi
printf '[ok] Diarchy host installed (%s)\n' "$version"
