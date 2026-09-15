#!/bin/sh
# Delayed hide: 300ms grace so moving between icons doesn't flicker.
# Aborts if dock-show.sh / dock-cancel-hide.sh bumped the sequence
# meanwhile (re-enter/cancel).
#
# NOTE: eww kills widget commands that outlive its internal timeout, so the
# timer must be detached (new session) and this process must exit at once.
if [ -z "$DOCK_HIDE_DETACHED" ]; then
  DOCK_HIDE_DETACHED=1 setsid "$0" >/dev/null 2>&1 < /dev/null &
  exit 0
fi
EWW=${DOCK_EWW_BIN:-eww}
RUNTIME_DIR=${DOCK_RUNTIME_DIR:-/tmp}
SEQ="$RUNTIME_DIR/eww-dock-seq"
TRANSITION_LOCK="$RUNTIME_DIR/eww-dock-show.lock"
HIDE_DELAY=${DOCK_HIDE_DELAY:-0.3}
CONFIRM_ATTEMPTS=${DOCK_CONFIRM_ATTEMPTS:-20}
CONFIRM_DELAY=${DOCK_CONFIRM_DELAY:-0.05}

mkdir -p "$RUNTIME_DIR"
BEFORE=$(cat "$SEQ" 2>/dev/null || echo 0)
sleep "$HIDE_DELAY"
AFTER=$(cat "$SEQ" 2>/dev/null || echo 0)
[ "$BEFORE" = "$AFTER" ] || exit 0

# Serialize the replacement with dock-show.sh. Recheck the sequence after
# taking the lock in case a show/cancel transition won the race.
exec 9>"$TRANSITION_LOCK"
flock -n 9 || exit 0
AFTER_LOCK=$(cat "$SEQ" 2>/dev/null || echo 0)
[ "$BEFORE" = "$AFTER_LOCK" ] || exit 0

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

# Dismiss a stale popover with the dock (its hover veto only fires inside).
"$EWW" update menu-open=false menu-move-open=false >/dev/null 2>&1 || true

# Create and confirm the trigger before removing the usable dock. `eww open`
# on an already-open window recreates it, so keep the active-window guard.
if window_active dock_open_area; then
  trigger_ready=true
else
  timeout -k 2 5 "$EWW" open dock_open_area >/dev/null 2>&1 || true
  if wait_for_window dock_open_area; then
    trigger_ready=true
  else
    trigger_ready=false
  fi
fi

if [ "$trigger_ready" = true ] && window_active dock; then
  "$EWW" close dock >/dev/null 2>&1 || true
fi
