#!/bin/sh
. "$(dirname -- "$0")/dock-common.sh"

# Invalidate old timers, then wait only briefly for a running transition.
# Concurrent i3 reloads must return rather than form an unbounded queue.
dock_bump_seq || exit 1
dock_lock "$DOCK_LOCK_TIMEOUT" || exit 1
fullscreen_request=$(dock_read_request) || exit 1
fullscreen=$(dock_run python3 "$DOCK_APP_SCRIPT" --fullscreen 2>/dev/null) || fullscreen=unknown
dock_refresh_fullscreen "$fullscreen" "$fullscreen_request" || exit 1

wait_for_daemon() {
  attempts=$CONFIRM_ATTEMPTS
  while [ "$attempts" -gt 0 ]; do
    dock_eww active-windows >/dev/null 2>&1 && return 0
    attempts=$((attempts - 1))
    [ "$attempts" -gt 0 ] && dock_pause "$CONFIRM_DELAY"
  done
  return 1
}

# The lifetime supervisor owns the foreground daemon and its own flock. It
# returns for a verified existing owner, even when a short IPC request stalls.
"$DOCK_SCRIPT_DIR/dock-daemon.sh" ensure || exit 1
wait_for_daemon || {
  echo "start-dock: verified Eww owner is not answering IPC" >&2
  exit 1
}

dock_windows=$(dock_eww active-windows 2>/dev/null) || {
  echo "start-dock: Eww owner is not answering the window query" >&2
  exit 1
}
if dock_snapshot_has dock; then
  # A visible dock is already a healthy startup state. Do not recreate it or
  # briefly reset its context-menu/listener state on i3 reload.
  if dock_snapshot_has dock_open_area; then dock_eww close dock_open_area >/dev/null 2>&1 || true; fi
else
  if ! dock_snapshot_has dock_open_area; then
    dock_eww open dock_open_area >/dev/null 2>&1 || true
  fi
  dock_wait_window dock_open_area || exit 1
fi
dock_reconcile_fullscreen || exit 1
dock_raise
