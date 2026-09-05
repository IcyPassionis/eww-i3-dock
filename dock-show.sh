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
LOCKFILE=/tmp/eww-dock-show.lock
# Single-flight via flock on a lockfile: the kernel releases it if this
# process dies. (A lockdir here once leaked behind a hung `eww open dock`
# and bricked every later show until manually removed.)
exec 9>"$LOCKFILE"
flock -n 9 || exit 0
# `eww open` occasionally never replies ("channel closed" in the daemon log);
# bound it so a hung open cannot wedge the show path — next hover retries.
# (-k: eww ignores SIGTERM while wedged, so escalate to KILL.)
show_open() { timeout -k 2 5 eww open dock >/dev/null 2>&1 || true; }
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
  eww update menu-open=false >/dev/null 2>&1 || true
  show_open
  eww close dock_open_area >/dev/null 2>&1 || true
fi
