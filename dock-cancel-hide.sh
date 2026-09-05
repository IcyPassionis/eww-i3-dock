#!/bin/sh
# Hovering the already-open dock: only invalidate a pending delayed hide.
# Deliberately zero IPC (`eww open` on an open window recreates it, which
# refires onhover under a stationary cursor into a hot loop). May run often.
bump_seq() {
  # FD-based flock: runs in this sh (flock -c would use $SHELL, e.g. fish).
  exec 9>/tmp/eww-dock-seq.lock
  flock 9
  n=$(cat /tmp/eww-dock-seq 2>/dev/null || echo 0)
  echo $((n + 1)) > /tmp/eww-dock-seq
  flock -u 9
  exec 9>&-
}
bump_seq
