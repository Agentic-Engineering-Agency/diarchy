#!/usr/bin/env bash

DIARCHY_SSH_ALIAS=${DIARCHY_SSH_ALIAS:-diarchy}
_DIARCHY_ROUTE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PING_BIN=${PING_BIN:-/sbin/ping}
ROUTE_BIN=${ROUTE_BIN:-/sbin/route}
CONNECT_BIN=${CONNECT_BIN:-/usr/bin/nc}

DIARCHY_ROUTE_NAME=${DIARCHY_ROUTE_NAME:-}
DIARCHY_ROUTE_HOST=${DIARCHY_ROUTE_HOST:-}
diarchy_ssh_options=()
DIARCHY_ROUTE_INTERFACE=${DIARCHY_ROUTE_INTERFACE:-}
DIARCHY_ROUTE_LOSS_PCT=${DIARCHY_ROUTE_LOSS_PCT:-}
DIARCHY_ROUTE_MEDIAN_MS=${DIARCHY_ROUTE_MEDIAN_MS:-}

_diarchy_route_fail() {
  printf 'diarchy-route: %s\n' "$*" >&2
  return 1
}

# Pinned host keys live next to the configuration file.
diarchy_known_hosts_path() {
  local conf=${DIARCHY_CONFIG:-${HOME:-}/.config/diarchy/diarchy.conf}
  printf '%s/known_hosts\n' "$(dirname -- "$conf")"
}

# Succeed when the pinned file has a key for the alias in the first field.
diarchy_host_key_pinned() {
  local alias=$1 file
  file=$(diarchy_known_hosts_path)
  [[ -r "$file" ]] || return 1
  awk -v alias="$alias" '
    /^[[:space:]]*(#|$)/ { next }
    NF >= 3 {
      count = split($1, names, ",")
      for (i = 1; i <= count; i++) {
        if (names[i] == alias) { found = 1; exit }
      }
    }
    END { exit found ? 0 : 1 }
  ' "$file"
}

# One fast reachability check per route (route lookup, then a 1 s TCP connect).
# A single ping runs only after the route is chosen, for the diagnostic line.
_diarchy_route_probe() {
  local route_name=$1 host=$2 ping_output route_output interface loss median

  if ! route_output=$("$ROUTE_BIN" -n get "$host" 2>&1); then
    return 1
  fi
  interface=$(printf '%s\n' "$route_output" | awk '$1 == "interface:" { print $2; exit }')
  [[ -n "$interface" ]] || return 1

  if [[ "$route_name" == lan ]]; then
    case "$interface" in
      utun*|tun*|tap*|tailscale*|ipsec*|ppp*) return 1 ;;
    esac
  fi

  "$CONNECT_BIN" -z -G 1 -w 1 "$host" "$DIARCHY_SSH_PORT" >/dev/null 2>&1 || return 1

  if ! ping_output=$("$PING_BIN" -n -c 1 -W 500 "$host" 2>&1); then
    :
  fi
  loss=$(printf '%s\n' "$ping_output" | awk '
    /packet loss/ {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /%$/) {
          gsub(/%/, "", $i)
          print $i
          exit
        }
      }
    }
  ')
  loss=${loss:-100}
  median=$(printf '%s\n' "$ping_output" \
    | sed -nE 's/.*time[=<]([0-9.]+) ms.*/\1/p' \
    | sort -n | awk '
      { values[NR] = $1 }
      END {
        if (NR % 2 == 1) {
          printf "%.3f", values[(NR + 1) / 2]
        } else if (NR > 0) {
          printf "%.3f", (values[NR / 2] + values[NR / 2 + 1]) / 2
        }
      }
    ')
  median=${median:-unavailable}
  DIARCHY_ROUTE_INTERFACE=$interface
  DIARCHY_ROUTE_LOSS_PCT=$loss
  DIARCHY_ROUTE_MEDIAN_MS=$median
}

_diarchy_route_set_selected() {
  DIARCHY_ROUTE_NAME=$1
  DIARCHY_ROUTE_HOST=$2
  export DIARCHY_ROUTE_NAME DIARCHY_ROUTE_HOST DIARCHY_ROUTE_INTERFACE DIARCHY_ROUTE_LOSS_PCT DIARCHY_ROUTE_MEDIAN_MS
  local known_hosts known_hosts_value
  known_hosts=$(diarchy_known_hosts_path)
  known_hosts_value=$known_hosts
  case $known_hosts_value in
    *[[:space:]]*) known_hosts_value="\"$known_hosts_value\"" ;;
  esac
  diarchy_ssh_options=(
    -o "ControlMaster=no"
    -o "ControlPath=none"
    -o "PermitLocalCommand=no"
    -o "ClearAllForwardings=yes"
    -o "User=$DIARCHY_SSH_USER"
    -o "HostKeyAlias=$DIARCHY_HOST_KEY_ALIAS"
    -o "HostName=$DIARCHY_ROUTE_HOST"
    -o "Port=$DIARCHY_SSH_PORT"
    -o "UserKnownHostsFile=$known_hosts_value"
    -o "StrictHostKeyChecking=yes"
    -o "BatchMode=yes"
    -o "ServerAliveInterval=15"
    -o "ServerAliveCountMax=3"
  )
  if [[ -n "${DIARCHY_SSH_IDENTITY_FILE:-}" ]]; then
    # ssh splits an unquoted -o value on whitespace; the loader rejects `"` so
    # quoting is only needed (and safe) when the path has whitespace.
    local identity_value=$DIARCHY_SSH_IDENTITY_FILE
    case $identity_value in
      *[[:space:]]*) identity_value="\"$identity_value\"" ;;
    esac
    diarchy_ssh_options+=(
      -o "IdentityFile=$identity_value"
      -o "IdentitiesOnly=yes"
    )
  fi
  printf 'route=%s host=%s interface=%s loss_pct=%s median_ms=%s\n' \
    "$DIARCHY_ROUTE_NAME" "$DIARCHY_ROUTE_HOST" "$DIARCHY_ROUTE_INTERFACE" \
    "$DIARCHY_ROUTE_LOSS_PCT" "$DIARCHY_ROUTE_MEDIAN_MS" >&2
}

# Probe one route when its host is configured; succeed and select it on success.
_diarchy_route_try() {
  local route_name=$1 host=$2

  [[ -n "$host" ]] || return 1
  _diarchy_route_probe "$route_name" "$host" || return 1
  _diarchy_route_set_selected "$route_name" "$host"
}

select_diarchy_route() {
  local mode=${1:-auto}
  local config_loader=${DIARCHY_CONFIG_SH:-$_DIARCHY_ROUTE_DIR/diarchy-config.sh}

  if [[ ! -r "$config_loader" ]]; then
    _diarchy_route_fail "cannot read config loader: $config_loader"
    return 1
  fi
  # shellcheck source=diarchy-config.sh
  source "$config_loader"
  diarchy_config_load || return 1

  case "$mode" in
    auto)
      _diarchy_route_try lan "$DIARCHY_LAN_HOST" && return 0
      _diarchy_route_try tailscale "$DIARCHY_TAILSCALE_HOST" && return 0
      _diarchy_route_fail "Host is unreachable on LAN and Tailscale"
      ;;
    lan)
      if [[ -z "$DIARCHY_LAN_HOST" ]]; then
        _diarchy_route_fail "forced LAN route is not configured"
        return 1
      fi
      _diarchy_route_try lan "$DIARCHY_LAN_HOST" && return 0
      _diarchy_route_fail "forced LAN route is unreachable"
      ;;
    tailscale)
      if [[ -z "$DIARCHY_TAILSCALE_HOST" ]]; then
        _diarchy_route_fail "forced Tailscale route is not configured"
        return 1
      fi
      _diarchy_route_try tailscale "$DIARCHY_TAILSCALE_HOST" && return 0
      _diarchy_route_fail "forced Tailscale route is unreachable"
      ;;
    *)
      _diarchy_route_fail "unknown route selection: $mode"
      ;;
  esac
}
