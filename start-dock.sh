#!/bin/sh

# Invalidate any pending delayed hide from a previous session.
# (FD-based flock: flock -c would run $SHELL, e.g. fish, not sh.)
exec 9>/tmp/eww-dock-seq.lock
flock 9
n=$(cat /tmp/eww-dock-seq 2>/dev/null || echo 0)
echo $((n + 1)) > /tmp/eww-dock-seq
flock -u 9
exec 9>&-
eww daemon >/tmp/eww-dock.log 2>&1 &
sleep 0.5
eww close dock >/dev/null 2>&1 || true
eww open dock_open_area >/dev/null 2>&1 || true
