#!/bin/sh
set -eu

snapshot() {
  volume=$(pactl get-sink-volume @DEFAULT_SINK@ 2>/dev/null | awk 'NR == 1 { for (i = 1; i <= NF; i++) if ($i ~ /%$/) { gsub(/%/, "", $i); print $i; exit } }') || true
  case "${volume:-}" in
    ''|*[!0-9]*) volume=0 ;;
  esac
  [ "$volume" -le 100 ] || volume=100

  mute=$(pactl get-sink-mute @DEFAULT_SINK@ 2>/dev/null | awk '{print $2}') || true
  if [ "${mute:-}" = yes ]; then muted=true; else muted=false; fi
  printf '{"volume": %s, "muted": %s}\n' "$volume" "$muted"
}

snapshot
pactl subscribe 2>/dev/null | while IFS= read -r event; do
  case "$event" in
    *"on sink "*|*"on server"*) snapshot ;;
  esac
done
