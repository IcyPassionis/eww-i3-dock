#!/bin/sh
# Show dock (trigger exists only while dock is closed, so this is one-shot
# per genuine enter).
#
# NOTE: eww kills widget commands that outlive its internal timeout, so the
# work is detached (new session) and single-flight via an atomic lock dir.
if [ -z "$DOCK_SHOW_DETACHED" ]; then
  DOCK_SHOW_DETACHED=1 setsid "$0" >/dev/null 2>&1 < /dev/null &
  exit 0
fi
LOCKDIR=/tmp/eww-dock-show.lock
i=0
while ! mkdir "$LOCKDIR" 2>/dev/null; do
  i=$((i + 1))
  [ "$i" -ge 50 ] && exit 0
  sleep 0.1
done
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT
bump_seq() {
  # FD-based flock: runs in this sh (flock -c would use $SHELL, e.g. fish).
  exec 9>/tmp/eww-dock-seq.lock
  flock 9
  n=$(cat /tmp/eww-dock-seq 2>/dev/null || echo 0)
  echo $((n + 1)) > /tmp/eww-dock-seq
  flock -u 9
  exec 9>&-
}
# Guard: `eww open` on an open window recreates it (that + dock `:onhover`
# used to be a 15x/s destroy/create storm that vetoed every hide).
# Bump only on a real transition: a bump from a delayed duplicate while the
# dock is already open would veto a legitimate pending hide.
if eww active-windows 2>/dev/null | grep -q ': dock$'; then
  eww close dock_open_area >/dev/null 2>&1 || true
else
  bump_seq
  eww open dock >/dev/null 2>&1 || true
  eww close dock_open_area >/dev/null 2>&1 || true
fi
