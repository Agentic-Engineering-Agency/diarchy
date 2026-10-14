#!/usr/bin/env bash

_hlc_fail() {
    printf 'diarchy-config: %s\n' "$1" >&2
    return 1
}

diarchy_config_load() {
    local _hlc_path="${DIARCHY_CONFIG:-${HOME:-}/.config/diarchy/diarchy.conf}"
    local _hlc_header='# Managed by Diarchy'
    local _hlc_line _hlc_number=0 _hlc_key _hlc_value
    local _hlc_file _hlc_regex='^([A-Z0-9_]+)=(.*)$'
    local _hlc_keys='DIARCHY_SSH_USER DIARCHY_LAN_HOST DIARCHY_TAILSCALE_HOST DIARCHY_SSH_PORT DIARCHY_SSH_IDENTITY_FILE DIARCHY_HOST_KEY_ALIAS DIARCHY_KVM_SIDE DIARCHY_HOST_FINGERPRINT DIARCHY_STREAM_RESOLUTION DIARCHY_STREAM_FPS DIARCHY_STREAM_OVERLAY DIARCHY_STREAM_OUTPUT'
    local _hlc_seen_DIARCHY_SSH_USER=0 _hlc_seen_DIARCHY_LAN_HOST=0
    local _hlc_seen_DIARCHY_TAILSCALE_HOST=0 _hlc_seen_DIARCHY_SSH_PORT=0
    local _hlc_seen_DIARCHY_SSH_IDENTITY_FILE=0 _hlc_seen_DIARCHY_HOST_KEY_ALIAS=0
    local _hlc_seen_DIARCHY_KVM_SIDE=0 _hlc_seen_DIARCHY_HOST_FINGERPRINT=0
    local _hlc_seen_DIARCHY_STREAM_RESOLUTION=0 _hlc_seen_DIARCHY_STREAM_FPS=0
    local _hlc_seen_DIARCHY_STREAM_OVERLAY=0 _hlc_seen_DIARCHY_STREAM_OUTPUT=0
    local _hlc_k
    for _hlc_k in $_hlc_keys; do
        local "_hlc_file_$_hlc_k=" "_hlc_value_$_hlc_k="
    done

    _hlc_file=0
    if [[ -e "$_hlc_path" ]]; then
        if [[ ! -f "$_hlc_path" || ! -r "$_hlc_path" ]]; then
            _hlc_fail "cannot read configuration file: $_hlc_path"
            return 1
        fi
        _hlc_file=1
    fi

    if [[ $_hlc_file -eq 1 ]]; then
        while IFS= read -r _hlc_line || [[ -n "$_hlc_line" ]]; do
            _hlc_number=$((_hlc_number + 1))
            if [[ $_hlc_number -eq 1 ]]; then
                if [[ "$_hlc_line" != "$_hlc_header" ]]; then
                    _hlc_fail 'first line must be "# Managed by Diarchy"'
                    return 1
                fi
                continue
            fi
            if [[ "$_hlc_line" == *$'\r'* ]]; then
                _hlc_fail "line $_hlc_number: carriage returns are not allowed"
                return 1
            fi
            [[ -z "$_hlc_line" || "${_hlc_line:0:1}" == '#' ]] && continue
            if [[ ! "$_hlc_line" =~ $_hlc_regex ]]; then
                _hlc_fail "line $_hlc_number: expected KEY=VALUE"
                return 1
            fi
            _hlc_key="${BASH_REMATCH[1]}"
            _hlc_value="${BASH_REMATCH[2]}"
            case " $_hlc_keys " in
                *" $_hlc_key "*) ;;
                *)
                    _hlc_fail "line $_hlc_number: unknown key $_hlc_key"
                    return 1
                    ;;
            esac
            case "$_hlc_key" in
                DIARCHY_SSH_USER) [[ $_hlc_seen_DIARCHY_SSH_USER -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_SSH_USER=1 ;;
                DIARCHY_LAN_HOST) [[ $_hlc_seen_DIARCHY_LAN_HOST -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_LAN_HOST=1 ;;
                DIARCHY_TAILSCALE_HOST) [[ $_hlc_seen_DIARCHY_TAILSCALE_HOST -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_TAILSCALE_HOST=1 ;;
                DIARCHY_SSH_PORT) [[ $_hlc_seen_DIARCHY_SSH_PORT -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_SSH_PORT=1 ;;
                DIARCHY_SSH_IDENTITY_FILE) [[ $_hlc_seen_DIARCHY_SSH_IDENTITY_FILE -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_SSH_IDENTITY_FILE=1 ;;
                DIARCHY_HOST_KEY_ALIAS) [[ $_hlc_seen_DIARCHY_HOST_KEY_ALIAS -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_HOST_KEY_ALIAS=1 ;;
                DIARCHY_KVM_SIDE) [[ $_hlc_seen_DIARCHY_KVM_SIDE -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_KVM_SIDE=1 ;;
                DIARCHY_HOST_FINGERPRINT) [[ $_hlc_seen_DIARCHY_HOST_FINGERPRINT -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_HOST_FINGERPRINT=1 ;;
                DIARCHY_STREAM_RESOLUTION) [[ $_hlc_seen_DIARCHY_STREAM_RESOLUTION -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_STREAM_RESOLUTION=1 ;;
                DIARCHY_STREAM_FPS) [[ $_hlc_seen_DIARCHY_STREAM_FPS -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_STREAM_FPS=1 ;;
                DIARCHY_STREAM_OVERLAY) [[ $_hlc_seen_DIARCHY_STREAM_OVERLAY -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_STREAM_OVERLAY=1 ;;
                DIARCHY_STREAM_OUTPUT) [[ $_hlc_seen_DIARCHY_STREAM_OUTPUT -eq 1 ]] && { _hlc_fail "line $_hlc_number: duplicate key $_hlc_key"; return 1; } ; _hlc_seen_DIARCHY_STREAM_OUTPUT=1 ;;
            esac
            if [[ "$_hlc_value" == *\"* || "$_hlc_value" == *\'* ]]; then
                _hlc_fail 'quoted values are not allowed'
                return 1
            fi
            printf -v "_hlc_file_$_hlc_key" '%s' "$_hlc_value"
        done < "$_hlc_path"
        if [[ $_hlc_number -eq 0 ]]; then
            _hlc_fail 'first line must be "# Managed by Diarchy"'
            return 1
        fi
    fi

    if [[ $_hlc_file -eq 0 && ( -z "${DIARCHY_SSH_USER:-}" || ( -z "${DIARCHY_LAN_HOST:-}" && -z "${DIARCHY_TAILSCALE_HOST:-}" ) ) ]]; then
        _hlc_fail 'Diarchy is not configured (open the app to set it up)'
        return 1
    fi

    local _hlc_file_var
    for _hlc_k in $_hlc_keys; do
        _hlc_value="${!_hlc_k:-}"
        if [[ -z "$_hlc_value" ]]; then
            _hlc_file_var="_hlc_file_$_hlc_k"
            _hlc_value="${!_hlc_file_var:-}"
        fi
        if [[ -z "$_hlc_value" ]]; then
            case "$_hlc_k" in
                DIARCHY_SSH_PORT) _hlc_value=22 ;;
                DIARCHY_KVM_SIDE) _hlc_value=left ;;
                DIARCHY_STREAM_RESOLUTION) _hlc_value=1920x1080 ;;
                DIARCHY_STREAM_FPS) _hlc_value=60 ;;
                DIARCHY_STREAM_OVERLAY) _hlc_value=0 ;;
                DIARCHY_STREAM_OUTPUT) _hlc_value=auto ;;
            esac
        fi
        printf -v "_hlc_value_$_hlc_k" '%s' "$_hlc_value"
    done

    local _hlc_user="$_hlc_value_DIARCHY_SSH_USER"
    local _hlc_lan="$_hlc_value_DIARCHY_LAN_HOST"
    local _hlc_tail="$_hlc_value_DIARCHY_TAILSCALE_HOST"
    local _hlc_port="$_hlc_value_DIARCHY_SSH_PORT"
    local _hlc_identity="$_hlc_value_DIARCHY_SSH_IDENTITY_FILE"
    local _hlc_alias="$_hlc_value_DIARCHY_HOST_KEY_ALIAS"
    local _hlc_side="$_hlc_value_DIARCHY_KVM_SIDE"
    local _hlc_fp="$_hlc_value_DIARCHY_HOST_FINGERPRINT"
    local _hlc_resolution="$_hlc_value_DIARCHY_STREAM_RESOLUTION"
    local _hlc_fps="$_hlc_value_DIARCHY_STREAM_FPS"
    local _hlc_overlay="$_hlc_value_DIARCHY_STREAM_OVERLAY"
    local _hlc_output="$_hlc_value_DIARCHY_STREAM_OUTPUT"


    local _hlc_user_re='^[a-z_][a-z0-9_-]{0,31}$' _hlc_host_re='^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'
    local _hlc_port_re='^[0-9]{1,5}$' _hlc_identity_re='^/.*$' _hlc_side_re='^(left|right|top|bottom)$'
    local _hlc_fp_re='^([0-9A-F]{2}:){31}[0-9A-F]{2}$' _hlc_resolution_re='^[0-9]{3,4}x[0-9]{3,4}$'
    local _hlc_fps_re='^[0-9]{1,3}$' _hlc_overlay_re='^(0|1)$' _hlc_output_re='^[A-Za-z0-9-]{1,32}$'
    [[ -n "$_hlc_user" ]] || { _hlc_fail 'DIARCHY_SSH_USER is required'; return 1; }
    [[ "$_hlc_user" =~ $_hlc_user_re ]] || { _hlc_fail 'DIARCHY_SSH_USER is invalid'; return 1; }
    [[ -z "$_hlc_lan" || "$_hlc_lan" =~ $_hlc_host_re ]] || { _hlc_fail 'DIARCHY_LAN_HOST is invalid'; return 1; }
    [[ -z "$_hlc_tail" || "$_hlc_tail" =~ $_hlc_host_re ]] || { _hlc_fail 'DIARCHY_TAILSCALE_HOST is invalid'; return 1; }
    [[ "$_hlc_port" =~ $_hlc_port_re ]] && ((10#$_hlc_port >= 1 && 10#$_hlc_port <= 65535)) || { _hlc_fail 'DIARCHY_SSH_PORT is invalid'; return 1; }
    [[ -z "$_hlc_identity" || "$_hlc_identity" =~ $_hlc_identity_re ]] || { _hlc_fail 'DIARCHY_SSH_IDENTITY_FILE is invalid'; return 1; }
    case "$_hlc_identity" in *$'\n'*|*\"*) _hlc_fail 'DIARCHY_SSH_IDENTITY_FILE is invalid'; return 1 ;; esac
    case "/$_hlc_identity/" in */../*) _hlc_fail 'DIARCHY_SSH_IDENTITY_FILE is invalid'; return 1 ;; esac
    [[ -z "$_hlc_alias" ]] && { _hlc_alias="$_hlc_tail"; [[ -z "$_hlc_alias" ]] && _hlc_alias="$_hlc_lan"; }
    [[ -z "$_hlc_alias" || "$_hlc_alias" =~ $_hlc_host_re ]] || { _hlc_fail 'DIARCHY_HOST_KEY_ALIAS is invalid'; return 1; }
    [[ "$_hlc_side" =~ $_hlc_side_re ]] || { _hlc_fail 'DIARCHY_KVM_SIDE is invalid'; return 1; }
    [[ -z "$_hlc_fp" || "$_hlc_fp" =~ $_hlc_fp_re ]] || { _hlc_fail 'DIARCHY_HOST_FINGERPRINT is invalid'; return 1; }
    [[ "$_hlc_resolution" =~ $_hlc_resolution_re ]] || { _hlc_fail 'DIARCHY_STREAM_RESOLUTION is invalid'; return 1; }
    [[ "$_hlc_fps" =~ $_hlc_fps_re ]] && ((10#$_hlc_fps >= 30 && 10#$_hlc_fps <= 240)) || { _hlc_fail 'DIARCHY_STREAM_FPS is invalid'; return 1; }
    [[ "$_hlc_overlay" =~ $_hlc_overlay_re ]] || { _hlc_fail 'DIARCHY_STREAM_OVERLAY is invalid'; return 1; }
    [[ "$_hlc_output" == auto || "$_hlc_output" =~ $_hlc_output_re ]] || { _hlc_fail 'DIARCHY_STREAM_OUTPUT is invalid'; return 1; }
    [[ -n "$_hlc_lan$_hlc_tail" ]] || { _hlc_fail 'at least one of DIARCHY_LAN_HOST or DIARCHY_TAILSCALE_HOST is required'; return 1; }
    export DIARCHY_SSH_USER="$_hlc_user" DIARCHY_LAN_HOST="$_hlc_lan" DIARCHY_TAILSCALE_HOST="$_hlc_tail" DIARCHY_SSH_PORT="$_hlc_port" DIARCHY_SSH_IDENTITY_FILE="$_hlc_identity" DIARCHY_HOST_KEY_ALIAS="$_hlc_alias" DIARCHY_KVM_SIDE="$_hlc_side" DIARCHY_HOST_FINGERPRINT="$_hlc_fp" DIARCHY_STREAM_RESOLUTION="$_hlc_resolution" DIARCHY_STREAM_FPS="$_hlc_fps" DIARCHY_STREAM_OVERLAY="$_hlc_overlay" DIARCHY_STREAM_OUTPUT="$_hlc_output"
    return 0
}
