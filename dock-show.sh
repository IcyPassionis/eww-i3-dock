#!/bin/sh
# Show dock (trigger exists only while dock is closed, so this is one-shot
# per genuine enter).
#
# NOTE: eww kills widget commands that outlive its internal timeout, so the
# work is detached (new session) and single-flight with the other transitions.
if [ -z "$DOCK_SHOW_DETACHED" ]; then
  DOCK_SHOW_DETACHED=1 setsid "$0" >/dev/null 2>&1 < /dev/null &
  exit 0
fi
EWW=${DOCK_EWW_BIN:-eww}
RUNTIME_DIR=${DOCK_RUNTIME_DIR:-/tmp}
LOCKFILE="$RUNTIME_DIR/eww-dock-show.lock"
SEQ_LOCK="$RUNTIME_DIR/eww-dock-seq.lock"
SEQ="$RUNTIME_DIR/eww-dock-seq"
CONFIRM_ATTEMPTS=${DOCK_CONFIRM_ATTEMPTS:-20}
CONFIRM_DELAY=${DOCK_CONFIRM_DELAY:-0.05}

mkdir -p "$RUNTIME_DIR"
# Serialize transitions via non-blocking flock so repeated hover callbacks do
# not build a process queue. The kernel releases the lock if a process dies.
# (A lockdir here once leaked behind a hung `eww open dock` and bricked every
# later show until manually removed.)
exec 9>"$LOCKFILE"
flock -n 9 || exit 0
# `eww open` occasionally never replies ("channel closed" in the daemon log);
# bound it so a hung open cannot wedge the show path — next hover retries.
# (-k: eww ignores SIGTERM while wedged, so escalate to KILL.)
show_open() { timeout -k 2 5 "$EWW" open dock >/dev/null 2>&1 || true; }
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
  # FD-based flock: runs in this sh (flock -c would use $SHELL, e.g. fish).
  # Descriptor 8 is intentionally distinct from the lifetime show lock on 9.
  exec 8>"$SEQ_LOCK"
  flock 8
  n=$(cat "$SEQ" 2>/dev/null || echo 0)
  echo $((n + 1)) > "$SEQ"
  flock -u 8
  exec 8>&-
}
# Guard: `eww open` on an open window recreates it (that + dock `:onhover`
# used to be a 15x/s destroy/create storm that vetoed every hide).
# Bump only on a real transition: a bump from a delayed duplicate while the
# dock is already open would veto a legitimate pending hide.
if window_active dock; then
  "$EWW" close dock_open_area >/dev/null 2>&1 || true
else
  bump_seq
  "$EWW" update menu-open=false menu-move-open=false >/dev/null 2>&1 || true
  show_open
  # Keep the trigger mapped when opening fails so another hover can retry.
  if wait_for_window dock; then
    "$EWW" close dock_open_area >/dev/null 2>&1 || true
  fi
fi
