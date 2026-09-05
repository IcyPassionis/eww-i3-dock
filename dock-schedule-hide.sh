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
SEQ=/tmp/eww-dock-seq
BEFORE=$(cat "$SEQ" 2>/dev/null || echo 0)
sleep 0.3
AFTER=$(cat "$SEQ" 2>/dev/null || echo 0)
[ "$BEFORE" = "$AFTER" ] || exit 0
# Dismiss a stale popover with the dock (its hover veto only fires inside).
eww update menu-open=false >/dev/null 2>&1 || true
# Guard both transitions with active-windows like dock-show.sh (an `eww open`
# on an already-open window hangs wedged; a close on a closed one just errs).
# `eww open` occasionally never replies either; bound it with timeout -k.
if eww active-windows 2>/dev/null | grep -q ': dock$'; then
  eww close dock >/dev/null 2>&1 || true
fi
if eww active-windows 2>/dev/null | grep -q ': dock_open_area$'; then
  : # trigger already open, nothing to do
else
  timeout -k 2 5 eww open dock_open_area >/dev/null 2>&1 || true
fi
