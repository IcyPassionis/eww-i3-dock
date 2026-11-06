#!/bin/sh
# Shared lifecycle boundary: subprocesses never inherit our flock descriptors.
DOCK_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EWW=${DOCK_EWW_BIN:-eww}
DOCK_OWNER_HELPER="$DOCK_SCRIPT_DIR/dock-owner.py"
CONFIG_DIR=${DOCK_CONFIG_DIR:-$DOCK_SCRIPT_DIR}
CONFIG_DIR=$(CDPATH= cd -- "$CONFIG_DIR" 2>/dev/null && pwd -P) || exit 1
DOCK_APP_SCRIPT=${DOCK_APP_SCRIPT:-$DOCK_SCRIPT_DIR/dock-apps.py}
DOCK_IPC_TIMEOUT=${DOCK_IPC_TIMEOUT:-1}
DOCK_IPC_KILL_AFTER=${DOCK_IPC_KILL_AFTER:-0.2}
DOCK_LOCK_TIMEOUT=${DOCK_LOCK_TIMEOUT:-0.5}
DOCK_SEQ_LOCK_TIMEOUT=${DOCK_SEQ_LOCK_TIMEOUT:-0.2}
CONFIRM_ATTEMPTS=${DOCK_CONFIRM_ATTEMPTS:-20}
CONFIRM_DELAY=${DOCK_CONFIRM_DELAY:-0.05}
if [ -n "${DOCK_STATE_DIR:-}" ]; then
  # The managed daemon exports its already-resolved namespace. Hover callbacks
  # must stay shell-only; parsing owner paths here would launch Python twice.
  STATE_DIR=$DOCK_STATE_DIR
  RUNTIME_DIR=${DOCK_RUNTIME_DIR:-$STATE_DIR}
else
  if [ -n "${DOCK_RUNTIME_DIR:-}" ]; then
    DOCK_PATHS=$(python3 "$DOCK_OWNER_HELPER" --config-dir "$CONFIG_DIR" --runtime-dir "$DOCK_RUNTIME_DIR" paths) || exit 1
  else
    DOCK_PATHS=$(python3 "$DOCK_OWNER_HELPER" --config-dir "$CONFIG_DIR" paths) || exit 1
  fi
  RUNTIME_DIR=$(printf '%s' "$DOCK_PATHS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["runtime_dir"])') || exit 1
  STATE_DIR=$(printf '%s' "$DOCK_PATHS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["namespace_dir"])') || exit 1
fi
SEQ="$RUNTIME_DIR/eww-dock-seq"
SEQ_LOCK="$RUNTIME_DIR/eww-dock-seq.lock"
TRANSITION_LOCK="$RUNTIME_DIR/eww-dock-show.lock"
HOVER_SEQ="$STATE_DIR/eww-dock-hover-seq"
HOVER_INTENT="$STATE_DIR/eww-dock-hover-intent"
TRIGGER_LEFT="$STATE_DIR/eww-dock-trigger-left"
FULLSCREEN_REVISION="$STATE_DIR/eww-dock-fullscreen-revision"
FULLSCREEN_REQUEST="$STATE_DIR/eww-dock-fullscreen-request"
FULLSCREEN_APPLIED="$STATE_DIR/eww-dock-fullscreen-applied"
export DOCK_RUNTIME_DIR="$RUNTIME_DIR" DOCK_STATE_DIR="$STATE_DIR" DOCK_EWW_BIN="$EWW" DOCK_CONFIG_DIR="$CONFIG_DIR"

dock_atomic_write() {
  dock_write_path=$1
  dock_write_value=$2
  dock_write_tmp="$dock_write_path.tmp.$$"
  printf '%s\n' "$dock_write_value" > "$dock_write_tmp" || return 1
  mv -f -- "$dock_write_tmp" "$dock_write_path"
}

dock_run() {
  LC_NUMERIC=C timeout -k "$DOCK_IPC_KILL_AFTER" "$DOCK_IPC_TIMEOUT" "$@" 8>&- 9>&-
}
dock_eww() { dock_run "$EWW" --config "$CONFIG_DIR" --no-daemonize "$@"; }
dock_pause() { dock_run sleep "$1"; }
dock_read_seq() {
  dock_generation=0
  if [ -r "$SEQ" ]; then IFS= read -r dock_generation < "$SEQ" || true; fi
  case "$dock_generation" in ''|*[!0-9]*) dock_generation=0 ;; esac
  printf '%s\n' "$dock_generation"
}
dock_bump_legacy_seq() {
  exec 8>"$SEQ_LOCK"
  # flock itself must retain the descriptor it locks; its wait is bounded.
  if ! LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 8>&-
    return 1
  fi
  dock_generation=$(dock_read_seq)
  dock_atomic_write "$SEQ" "$((dock_generation + 1))" || { exec 8>&-; return 1; }
  exec 8>&-
}
dock_bump_seq() {
  dock_publish_hover cancel || return 1
  dock_bump_legacy_seq
}
 dock_publish_hover() {
  dock_hover_action=$1
  exec 8>"$SEQ_LOCK"
  if ! LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 8>&-
    return 1
  fi
  dock_hover_generation=0
  if [ -r "$HOVER_SEQ" ]; then IFS= read -r dock_hover_generation < "$HOVER_SEQ" || true; fi
  case "$dock_hover_generation" in ''|*[!0-9]*) dock_hover_generation=0 ;; esac
  dock_hover_generation=$((dock_hover_generation + 1))
  dock_atomic_write "$HOVER_SEQ" "$dock_hover_generation" || { exec 8>&-; return 1; }
  HOVER_TOKEN="$dock_hover_generation"
  dock_atomic_write "$HOVER_INTENT" "$HOVER_TOKEN $dock_hover_action" || { exec 8>&-; return 1; }
  exec 8>&-
}
dock_read_hover() {
  dock_hover_request=''
  if [ -r "$HOVER_INTENT" ]; then IFS= read -r dock_hover_request < "$HOVER_INTENT" || true; fi
  printf '%s\n' "$dock_hover_request"
}
dock_hover_matches() { [ "$(dock_read_hover)" = "$1 show" ]; }
dock_note_trigger_left() {
  exec 8>"$SEQ_LOCK"
  if ! LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 8>&-
    return 1
  fi
  dock_hover_request=$(dock_read_hover)
  case "$dock_hover_request" in *' show') dock_atomic_write "$TRIGGER_LEFT" "${dock_hover_request%% *}" ;; esac
  exec 8>&-
}
dock_trigger_left_matches() {
  [ -r "$TRIGGER_LEFT" ] && [ "$(cat "$TRIGGER_LEFT")" = "$1" ]
}
dock_lock() {
  exec 9>"$TRANSITION_LOCK"
  LC_NUMERIC=C flock -w "$1" 9 8>&-
  dock_lock_status=$?
  if [ "$dock_lock_status" -eq 0 ]; then trap dock_unlock_handoff EXIT; fi
  return "$dock_lock_status"
}
dock_unlock_handoff() {
  # Hold the publisher lock across releasing the transition lock. A request
  # arriving at the owner's final-check boundary then waits, publishes, and
  # acquires the now-free transition lock itself.
  exec 8>"$SEQ_LOCK"
  if LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 9>&-
    exec 8>&-
  else
    exec 9>&-
    exec 8>&-
  fi
}
dock_window_active() {
  dock_windows=$(dock_eww active-windows 2>/dev/null) || return 1
  case "
$dock_windows
" in *": $1
"*) return 0 ;; *) return 1 ;; esac
}
dock_wait_window() {
  dock_attempts=$CONFIRM_ATTEMPTS
  while [ "$dock_attempts" -gt 0 ]; do
    dock_window_active "$1" && return 0
    dock_attempts=$((dock_attempts - 1))
    [ "$dock_attempts" -gt 0 ] && dock_pause "$CONFIRM_DELAY"
  done
  return 1
}
dock_raise() {
  # Optional on older installed app scripts; never hold a transition hostage.
  dock_run python3 "$DOCK_APP_SCRIPT" --raise >/dev/null 2>&1 || true
}

# Register intent before waiting for the transition lock. Its current owner
# can finish the latest request even when the requesting caller times out.
dock_request_fullscreen() {
  exec 8>"$SEQ_LOCK"
  if ! LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 8>&-
    return 1
  fi
  if [ "$#" -gt 1 ]; then
    dock_previous=''
    if [ -r "$FULLSCREEN_REQUEST" ]; then IFS= read -r dock_previous < "$FULLSCREEN_REQUEST" || true; fi
    if [ "$dock_previous" != "$2" ]; then exec 8>&-; return 0; fi
  fi
  dock_previous=''
  if [ -r "$FULLSCREEN_REQUEST" ]; then IFS= read -r dock_previous < "$FULLSCREEN_REQUEST" || true; fi
  case "$dock_previous" in *" $1")
    REQUEST_TOKEN=$dock_previous
    exec 8>&-
    return 0
    ;;
  esac
  dock_generation=0
  if [ -r "$FULLSCREEN_REVISION" ]; then IFS= read -r dock_generation < "$FULLSCREEN_REVISION" || true; fi
  case "$dock_generation" in ''|*[!0-9]*) dock_generation=0 ;; esac
  dock_generation=$((dock_generation + 1))
  REQUEST_TOKEN="$dock_generation $1"
  dock_atomic_write "$FULLSCREEN_REVISION" "$dock_generation" || { exec 8>&-; return 1; }
  dock_atomic_write "$FULLSCREEN_REQUEST" "$REQUEST_TOKEN" || { exec 8>&-; return 1; }
  exec 8>&-
}
dock_read_request() {
  exec 8>"$SEQ_LOCK"
  if ! LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 8>&-
    return 1
  fi
  dock_request=''
  if [ -r "$FULLSCREEN_REQUEST" ]; then
    IFS= read -r dock_request < "$FULLSCREEN_REQUEST" || true
  fi
  exec 8>&-
  printf '%s\n' "$dock_request"
}
dock_refresh_fullscreen() {
  # A fresh known query supersedes stale on-disk intent after missed events.
  # Do not bump the hide generation for ordinary duplicate hover callbacks.
  case "$1" in true) dock_desired=suppress ;; false) dock_desired=restore ;; *) return 0 ;; esac
  dock_existing=$(dock_read_request) || return 1
  [ "$1" = false ] && [ -z "$dock_existing" ] && return 0
  case "$dock_existing" in *" $dock_desired") return 0 ;; esac
  # Compare under the sequence lock: an event received during the query is
  # newer than that query's snapshot and must not be overwritten.
  dock_request_fullscreen "$dock_desired" "$2"
}
dock_mark_applied() {
  exec 8>"$SEQ_LOCK"
  if ! LC_NUMERIC=C flock -w "$DOCK_SEQ_LOCK_TIMEOUT" 8 9>&-; then
    exec 8>&-
    return 1
  fi
  dock_current=''
  IFS= read -r dock_current < "$FULLSCREEN_REQUEST" || true
  if [ "$dock_current" != "$1" ]; then exec 8>&-; return 1; fi
  dock_atomic_write "$FULLSCREEN_APPLIED" "$1" || { exec 8>&-; return 1; }
  exec 8>&-
}
dock_snapshot_has() {
  case "
$dock_windows
" in *": $1
"*) return 0 ;; *) return 1 ;; esac
}
dock_reconcile_fullscreen() {
  # Called with fd 9 locked. Bound coalescing rather than polling forever.
  dock_rounds=${DOCK_RECONCILE_ATTEMPTS:-3}
  while [ "$dock_rounds" -gt 0 ]; do
    dock_rounds=$((dock_rounds - 1))
    dock_target=$(dock_read_request) || return 1
    [ -n "$dock_target" ] || return 0
    dock_windows=$(dock_eww active-windows 2>/dev/null) || return 1
    case "$dock_target" in
      *' suppress')
        if ! dock_snapshot_has dock_open_area; then
          dock_eww open dock_open_area >/dev/null 2>&1 || true
          # Never close the visible dock until the trigger is confirmed.
          dock_wait_window dock_open_area || return 1
        fi
        dock_eww update menu-open=false menu-move-open=false >/dev/null 2>&1 || true
        if dock_window_active dock; then
          [ "$dock_target" = "$(dock_read_request)" ] || continue
          dock_eww close dock >/dev/null 2>&1 || return 1
        fi
        ;;
      *' restore')
        if dock_snapshot_has dock; then
          if dock_snapshot_has dock_open_area; then
            dock_eww close dock_open_area >/dev/null 2>&1 || return 1
          fi
        elif ! dock_snapshot_has dock_open_area; then
          dock_eww open dock_open_area >/dev/null 2>&1 || true
          dock_wait_window dock_open_area || return 1
        fi
        ;;
      *) return 1 ;;
    esac
    # IPC success is not proof: read the exact window state before acknowledging.
    dock_windows=$(dock_eww active-windows 2>/dev/null) || return 1
    case "$dock_target" in
      *' suppress')
        dock_snapshot_has dock && return 1
        dock_snapshot_has dock_open_area || return 1
        ;;
      *' restore')
        dock_snapshot_has dock || dock_snapshot_has dock_open_area || return 1
        ;;
    esac
    dock_mark_applied "$dock_target" && return 0
  done
  return 1
}
