#!/bin/sh
set -eu

if [ "${1:-}" = toggle-mute ]; then
  pactl set-sink-mute @DEFAULT_SINK@ toggle
  exit
fi

location=$(xdotool getmouselocation --shell)
x=$(printf '%s\n' "$location" | awk -F= '$1 == "X" {print $2}')
y=$(printf '%s\n' "$location" | awk -F= '$1 == "Y" {print $2}')

# Find which XRandR monitor contains the click and position the popup in its local space.
monitor=$(xrandr --query | awk -v px="$x" -v py="$y" '
  / connected/ {
    for (i = 3; i <= NF; i++) {
      if ($i ~ /^[0-9]+x[0-9]+\+[0-9]+\+[0-9]+$/) {
        split($i, g, /x|\+/)
        if (px >= g[3] && px < g[3] + g[1] && py >= g[4] && py < g[4] + g[2]) {
          print $1, g[1], g[2], g[3], g[4]
          exit
        }
      }
    }
  }')

if [ -n "$monitor" ]; then
  read -r monitor_name monitor_width monitor_height monitor_x monitor_y <<EOF
$monitor
EOF
  screen=$(xrandr --listmonitors | awk -v name="$monitor_name" 'NR > 1 && $NF == name {i=$1; sub(":", "", i); print i; exit}')
  local_x=$((x - monitor_x))
  local_y=$((y - monitor_y))
else
  monitor_width=1920
  monitor_height=1080
  screen=0
  local_x=$x
  local_y=$y
fi

width=292
height=112
popup_x=$((local_x - width / 2))
popup_y=$((local_y + 20))
[ "$popup_x" -ge 12 ] || popup_x=12
max_x=$((monitor_width - width - 12))
[ "$popup_x" -le "$max_x" ] || popup_x=$max_x
[ "$popup_y" -ge 39 ] || popup_y=39
max_y=$((monitor_height - height - 12))
[ "$popup_y" -le "$max_y" ] || popup_y=$max_y

. "$(dirname -- "$0")/dock-common.sh"
dock_eww open volume-popup --screen "${screen:-0}" --pos "${popup_x}x${popup_y}" --size "${width}x${height}" --anchor "top left" --toggle
