#!/bin/sh
set -eu

STATE=${DOCK_STATE_DIR:?DOCK_STATE_DIR is required}
mkdir -p "$STATE"

with_state_lock() {
  exec 7>"$STATE/state.lock"
  flock 7
  "$@"
  result=$?
  flock -u 7
  exec 7>&-
  return "$result"
}

increment_open_count() {
  window=$1
  count_file="$STATE/open-count-$window"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  echo $((count + 1)) > "$count_file"
}

add_window() {
  window=$1
  touch "$STATE/windows"
  if ! grep -Fxq "$window" "$STATE/windows"; then
    printf '%s\n' "$window" >> "$STATE/windows"
  fi
}

remove_window() {
  window=$1
  touch "$STATE/windows"
  temporary="$STATE/windows.new.$$"
  grep -Fxv "$window" "$STATE/windows" > "$temporary" || true
  mv "$temporary" "$STATE/windows"
}

command=${1:-}
case "$command" in
  daemon)
    delay=$(cat "$STATE/daemon-delay" 2>/dev/null || echo 0)
    sleep "$delay"
    touch "$STATE/ready"
    if [ -f "$STATE/daemon-hold" ]; then
      echo $$ > "$STATE/daemon-pid"
      while [ -f "$STATE/daemon-hold" ]; do
        sleep 0.05
      done
    fi
    ;;
  active-windows)
    [ -f "$STATE/ready" ] || exit 1
    while IFS= read -r window; do
      [ -n "$window" ] && printf 'fake: %s\n' "$window"
    done < "$STATE/windows"
    ;;
  open)
    [ -f "$STATE/ready" ] || exit 1
    window=${2:?window is required}
    [ ! -f "$STATE/fail-open-$window" ] || exit 1
    delay=$(cat "$STATE/open-delay" 2>/dev/null || echo 0)
    sleep "$delay"
    with_state_lock increment_open_count "$window"
    with_state_lock add_window "$window"
    ;;
  close)
    [ -f "$STATE/ready" ] || exit 1
    window=${2:?window is required}
    with_state_lock remove_window "$window"
    ;;
  update)
    [ -f "$STATE/ready" ] || exit 1
    ;;
  *)
    printf 'unsupported fake eww command: %s\n' "$command" >&2
    exit 2
    ;;
esac
