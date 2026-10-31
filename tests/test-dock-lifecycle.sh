#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
# Never restack the live desktop from this isolated shell fixture suite.
export DISPLAY=
export DOCK_OUTPUT=DP-1
TEST_ROOT=$(mktemp -d)
cleanup() {
  for pid_file in $(find "$TEST_ROOT" -name daemon-pid 2>/dev/null); do
    kill "$(cat "$pid_file")" 2>/dev/null || true
  done
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  return 1
}

new_state() {
  case_name=$1
  export DOCK_STATE_DIR="$TEST_ROOT/$case_name/state"
  export DOCK_RUNTIME_DIR="$TEST_ROOT/$case_name/runtime"
  bin="$TEST_ROOT/$case_name/bin"
  mkdir -p "$DOCK_STATE_DIR" "$DOCK_RUNTIME_DIR" "$bin"
  : > "$DOCK_STATE_DIR/windows"
  ln -s "$ROOT/tests/fake-eww.sh" "$bin/eww"
  cat > "$bin/i3-msg" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "-t" ] && [ "${2:-}" = "get_tree" ]; then
  if [ "${DOCK_I3_EXIT:-0}" -ne 0 ]; then
    exit "$DOCK_I3_EXIT"
  fi
  cat "$DOCK_I3_TREE"
  exit 0
fi
if [ "${1:-}" = "-t" ] && [ "${2:-}" = "get_workspaces" ]; then
  if [ "${DOCK_I3_EXIT:-0}" -ne 0 ]; then
    exit "$DOCK_I3_EXIT"
  fi
  cat "$DOCK_I3_WORKSPACES"
  exit 0
fi
exit 1
EOF
  chmod +x "$bin/i3-msg"
  printf '%s\n' '[]' > "$DOCK_STATE_DIR/workspaces.json"
  PATH="$bin:$PATH"
  export PATH
  export DOCK_EWW_BIN="$bin/eww"
  export DOCK_APP_SCRIPT="$ROOT/dock-apps.py"
  export DOCK_I3_WORKSPACES="$DOCK_STATE_DIR/workspaces.json"
  export DOCK_CONFIRM_ATTEMPTS=20
  export DOCK_CONFIRM_DELAY=0.01
  export DOCK_HIDE_DELAY=0.01
}

set_windows() {
  : > "$DOCK_STATE_DIR/windows"
  for window in "$@"; do
    printf '%s\n' "$window" >> "$DOCK_STATE_DIR/windows"
  done
}

assert_windows() {
  expected=$1
  actual=$(sort "$DOCK_STATE_DIR/windows" | paste -sd, -)
  [ "$actual" = "$expected" ] || fail "expected windows '$expected', got '$actual'"
}

assert_open_count() {
  window=$1
  expected=$2
  actual=$(cat "$DOCK_STATE_DIR/open-count-$window" 2>/dev/null || echo 0)
  [ "$actual" = "$expected" ] || fail "expected $window open count $expected, got $actual"
}

test_concurrent_show_opens_one_dock() {
  new_state concurrent-show
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock_open_area
  echo 0.15 > "$DOCK_STATE_DIR/open-delay"

  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh" & first=$!
  sleep 0.02
  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh" & second=$!
  wait "$first"
  wait "$second"

  assert_open_count dock 1
  assert_windows dock
}

test_failed_show_keeps_trigger() {
  new_state failed-show
  touch "$DOCK_STATE_DIR/ready" "$DOCK_STATE_DIR/fail-open-dock"
  set_windows dock_open_area

  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh"

  assert_open_count dock 0
  assert_windows dock_open_area
}

test_successful_show_swaps_trigger_for_dock() {
  new_state successful-show
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock_open_area

  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh"

  assert_open_count dock 1
  assert_windows dock
}

test_failed_hide_keeps_dock() {
  new_state failed-hide
  touch "$DOCK_STATE_DIR/ready" "$DOCK_STATE_DIR/fail-open-dock_open_area"
  set_windows dock

  DOCK_HIDE_DETACHED=1 "$ROOT/dock-schedule-hide.sh"

  assert_open_count dock_open_area 0
  assert_windows dock
}

test_successful_hide_swaps_dock_for_trigger() {
  new_state successful-hide
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock

  DOCK_HIDE_DETACHED=1 "$ROOT/dock-schedule-hide.sh"

  assert_open_count dock_open_area 1
  assert_windows dock_open_area
}

test_start_waits_for_daemon_and_opens_only_trigger() {
  new_state startup
  set_windows dock
  echo 0.7 > "$DOCK_STATE_DIR/daemon-delay"
  export DOCK_CONFIRM_ATTEMPTS=100

  "$ROOT/start-dock.sh"
  attempts=30
  while [ ! -f "$DOCK_STATE_DIR/ready" ] && [ "$attempts" -gt 0 ]; do
    attempts=$((attempts - 1))
    sleep 0.05
  done
  [ -f "$DOCK_STATE_DIR/ready" ] || fail "fake daemon never became ready"

  assert_open_count dock_open_area 1
  assert_windows dock_open_area
}

test_startup_does_not_leave_transition_lock_in_daemon() {
  new_state startup-lock
  set_windows dock
  touch "$DOCK_STATE_DIR/daemon-hold"

  "$ROOT/start-dock.sh"

  if ! flock -n "$DOCK_RUNTIME_DIR/eww-dock-show.lock" true; then
    fail "daemon inherited and retained the dock transition lock"
  fi
  rm -f "$DOCK_STATE_DIR/daemon-hold"
}

test_fullscreen_suppression_swaps_dock_to_trigger() {
  new_state fullscreen-suppression
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock

  DOCK_FULLSCREEN_DETACHED=1 "$ROOT/dock-fullscreen.sh" suppress

  assert_open_count dock_open_area 1
  assert_windows dock_open_area
}

test_fullscreen_show_guard_blocks_confirmed_fullscreen() {
  new_state fullscreen-show-guard
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock_open_area
  printf '%s\n' '{"type":"root","nodes":[{"type":"output","name":"DP-1","nodes":[{"type":"workspace","id":456,"fullscreen_mode":1,"nodes":[{"type":"con","window":123,"fullscreen_mode":1,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]}],"floating_nodes":[]}]}' > "$DOCK_STATE_DIR/tree.json"
  printf '%s\n' '[{"id":456,"output":"DP-1","visible":true}]' > "$DOCK_STATE_DIR/workspaces.json"
  export DOCK_I3_TREE="$DOCK_STATE_DIR/tree.json"

  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh"

  assert_open_count dock 0
  assert_windows dock_open_area
}

test_fullscreen_show_guard_allows_other_output() {
  new_state other-output-fullscreen
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock_open_area
  printf '%s\n' '{"type":"root","nodes":[{"type":"output","name":"DP-1","nodes":[{"type":"workspace","id":456,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]},{"type":"output","name":"HDMI-1","nodes":[{"type":"workspace","id":789,"fullscreen_mode":1,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]}]}' > "$DOCK_STATE_DIR/tree.json"
  printf '%s\n' '[{"id":456,"output":"DP-1","visible":true},{"id":789,"output":"HDMI-1","visible":true}]' > "$DOCK_STATE_DIR/workspaces.json"
  export DOCK_I3_TREE="$DOCK_STATE_DIR/tree.json"

  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh"

  assert_open_count dock 1
  assert_windows dock
}

test_fullscreen_show_guard_allows_ipc_failure() {
  new_state fullscreen-ipc-failure
  touch "$DOCK_STATE_DIR/ready"
  set_windows dock_open_area
  export DOCK_I3_TREE="$DOCK_STATE_DIR/tree.json"
  export DOCK_I3_EXIT=1

  DOCK_SHOW_DETACHED=1 "$ROOT/dock-show.sh"

  assert_open_count dock 1
  assert_windows dock
}

run_show_tests() {
  test_concurrent_show_opens_one_dock
  test_failed_show_keeps_trigger
  test_successful_show_swaps_trigger_for_dock
  printf 'PASS: show lifecycle tests\n'
}

run_hide_start_tests() {
  test_failed_hide_keeps_dock
  test_successful_hide_swaps_dock_for_trigger
  test_start_waits_for_daemon_and_opens_only_trigger
  test_startup_does_not_leave_transition_lock_in_daemon
  printf 'PASS: hide/start lifecycle tests\n'
}

run_fullscreen_tests() {
  test_fullscreen_suppression_swaps_dock_to_trigger
  test_fullscreen_show_guard_blocks_confirmed_fullscreen
  test_fullscreen_show_guard_allows_other_output
  test_fullscreen_show_guard_allows_ipc_failure
  printf 'PASS: fullscreen lifecycle tests\n'
}

run_all_tests() {
  run_show_tests
  run_hide_start_tests
  run_fullscreen_tests
}

case ${1:-all} in
  all) run_all_tests ;;
  show) run_show_tests ;;
  hide-start) run_hide_start_tests ;;
  concurrent-show) test_concurrent_show_opens_one_dock ;;
  failed-show) test_failed_show_keeps_trigger ;;
  successful-show) test_successful_show_swaps_trigger_for_dock ;;
  failed-hide) test_failed_hide_keeps_dock ;;
  successful-hide) test_successful_hide_swaps_dock_for_trigger ;;
  startup) test_start_waits_for_daemon_and_opens_only_trigger ;;
  startup-lock) test_startup_does_not_leave_transition_lock_in_daemon ;;
  fullscreen) run_fullscreen_tests ;;
  *) fail "unknown test group: $1" ;;
esac
