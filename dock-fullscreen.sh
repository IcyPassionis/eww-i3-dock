#!/bin/sh
# Synchronize the dock with i3 fullscreen transitions.
# `suppress` swaps the visible dock back to the transparent trigger.
# `restore` makes sure the trigger exists after fullscreen ends.
if [ -z "$DOCK_FULLSCREEN_DETACHED" ]; then
  DOCK_FULLSCREEN_DETACHED=1 setsid "$0" "$@" >/dev/null 2>&1 < /dev/null &
  exit 0
fi

EWW=${DOCK_EWW_BIN:-eww}
RUNTIME_DIR=${DOCK_RUNTIME_DIR:-/tmp}
LOCKFILE="$RUNTIME_DIR/eww-dock-show.lock"
SEQ_LOCK="$RUNTIME_DIR/eww-dock-seq.lock"
SEQ="$RUNTIME_DIR/eww-dock-seq"
CONFIRM_ATTEMPTS=${DOCK_CONFIRM_ATTEMPTS:-20}
CONFIRM_DELAY=${DOCK_CONFIRM_DELAY:-0.05}
ACTION=${1:-}

[ "$ACTION" = suppress ] || [ "$ACTION" = restore ] || exit 2
mkdir -p "$RUNTIME_DIR"

window_active() {
  "$EWW" active-windows 2>/dev/null | grep -q ": $1\$"
}

wait_for_window() {
  window=$1
  attempts=$CONFIRM_ATTEMPTS
  while [ "$attempts" -gt 0 ]; do
    window_active "$window" && return 0
    attempts=$((attempts - 1))
    sleep "$CONFIRM_DELAY"
  done
  return 1
}

bump_seq() {
  exec 8>"$SEQ_LOCK"
  flock 8
  n=$(cat "$SEQ" 2>/dev/null || echo 0)
  echo $((n + 1)) > "$SEQ"
  flock -u 8
  exec 8>&-
}

exec 9>"$LOCKFILE"
flock -n 9 || exit 0
bump_seq

if [ "$ACTION" = suppress ]; then
  # Preserve a recovery point before removing the usable dock. A failed open
  # does not prevent suppression: the restore action retries after fullscreen.
  if ! window_active dock_open_area; then
    timeout -k 2 5 "$EWW" open dock_open_area >/dev/null 2>&1 || true
    wait_for_window dock_open_area || true
  fi
  if window_active dock; then
    "$EWW" update menu-open=false menu-move-open=false >/dev/null 2>&1 || true
    "$EWW" close dock >/dev/null 2>&1 || true
  fi
else
  if ! window_active dock && ! window_active dock_open_area; then
    timeout -k 2 5 "$EWW" open dock_open_area >/dev/null 2>&1 || true
  fi
fi
