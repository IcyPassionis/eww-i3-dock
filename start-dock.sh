#!/bin/sh

EWW=${DOCK_EWW_BIN:-eww}
RUNTIME_DIR=${DOCK_RUNTIME_DIR:-/tmp}
SEQ="$RUNTIME_DIR/eww-dock-seq"
SEQ_LOCK="$RUNTIME_DIR/eww-dock-seq.lock"
TRANSITION_LOCK="$RUNTIME_DIR/eww-dock-show.lock"
CONFIRM_ATTEMPTS=${DOCK_CONFIRM_ATTEMPTS:-50}
CONFIRM_DELAY=${DOCK_CONFIRM_DELAY:-0.1}

mkdir -p "$RUNTIME_DIR"

# Invalidate any pending delayed hide from a previous session.
# Descriptor 8 stays distinct from the lifetime transition lock on 9.
exec 8>"$SEQ_LOCK"
flock 8
n=$(cat "$SEQ" 2>/dev/null || echo 0)
echo $((n + 1)) > "$SEQ"
flock -u 8
exec 8>&-

# Startup and hover/hide swaps share one lock, preventing a trigger created
# during startup from launching a competing show transition.
exec 9>"$TRANSITION_LOCK"
flock 9

window_active() {
  "$EWW" active-windows 2>/dev/null | grep -q ": $1\$"
}
wait_for_daemon() {
  attempts=$CONFIRM_ATTEMPTS
  while [ "$attempts" -gt 0 ]; do
    "$EWW" active-windows >/dev/null 2>&1 && return 0
    attempts=$((attempts - 1))
    sleep "$CONFIRM_DELAY"
  done
  return 1
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

# The long-lived daemon must not inherit fd 9 or it will hold the transition
# lock forever and every later hover will lose the non-blocking flock race.
"$EWW" daemon 9>&- >"$RUNTIME_DIR/eww-dock.log" 2>&1 &
wait_for_daemon || exit 1

# Keep any stale full dock available until its hidden trigger is confirmed.
if ! window_active dock_open_area; then
  timeout -k 2 5 "$EWW" open dock_open_area >/dev/null 2>&1 || true
fi
wait_for_window dock_open_area || exit 1

if window_active dock; then
  "$EWW" close dock >/dev/null 2>&1 || true
fi
