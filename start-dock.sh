#!/bin/sh

eww daemon >/tmp/eww-dock.log 2>&1 &
sleep 0.5
eww open dock_open_area >/dev/null 2>&1 || true
eww open dock >/dev/null 2>&1 || true
