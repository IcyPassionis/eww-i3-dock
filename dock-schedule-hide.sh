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
eww close dock >/dev/null 2>&1 || true
eww open dock_open_area >/dev/null 2>&1 || true
