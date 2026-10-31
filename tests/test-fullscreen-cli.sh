#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TEST_ROOT=$(mktemp -d)
cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT INT TERM

FAKE_BIN="$TEST_ROOT/bin"
mkdir -p "$FAKE_BIN"

assert_status() {
  name=$1
  expected=$2
  tree=$3
  workspaces=$4
  printf '%s\n' "$tree" > "$TEST_ROOT/tree"
  printf '%s\n' "$workspaces" > "$TEST_ROOT/workspaces"
  actual=$(PATH="$FAKE_BIN:$PATH" python3 "$ROOT/dock-apps.py" --fullscreen)
  [ "$actual" = "$expected" ] || {
    printf 'FAIL: %s expected %s, got %s\n' "$name" "$expected" "$actual" >&2
    exit 1
  }
}

cat > "$FAKE_BIN/i3-msg" <<'EOF'
#!/bin/sh
if [ "${1:-}" = "-t" ] && [ "${2:-}" = "get_tree" ]; then
  cat "$DOCK_TEST_TREE"
  exit 0
fi
if [ "${1:-}" = "-t" ] && [ "${2:-}" = "get_workspaces" ]; then
  cat "$DOCK_TEST_WORKSPACES"
  exit 0
fi
exit 1
EOF
chmod +x "$FAKE_BIN/i3-msg"

# Fixture connector is explicit: synthetic i3 order is not GDK order.
export DOCK_OUTPUT=DP-1
export DOCK_TEST_TREE="$TEST_ROOT/tree"
export DOCK_TEST_WORKSPACES="$TEST_ROOT/workspaces"

assert_status output-zero-fullscreen true '{"type":"root","nodes":[{"type":"output","name":"DP-1","nodes":[{"type":"workspace","id":456,"fullscreen_mode":1,"nodes":[{"type":"con","window":123,"fullscreen_mode":1,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]}],"floating_nodes":[]},{"type":"output","name":"HDMI-1","nodes":[],"floating_nodes":[]}]}' '[{"id":456,"output":"DP-1","visible":true}]'
assert_status other-output-fullscreen false '{"type":"root","nodes":[{"type":"output","name":"DP-1","nodes":[{"type":"workspace","id":456,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]},{"type":"output","name":"HDMI-1","nodes":[{"type":"workspace","id":789,"fullscreen_mode":1,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]}]}' '[{"id":456,"output":"DP-1","visible":true},{"id":789,"output":"HDMI-1","visible":true}]'
assert_status global-fullscreen true '{"type":"root","nodes":[{"type":"output","name":"DP-1","nodes":[{"type":"workspace","id":456,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]},{"type":"output","name":"HDMI-1","nodes":[{"type":"workspace","id":789,"fullscreen_mode":2,"nodes":[{"type":"con","window":456,"fullscreen_mode":2,"nodes":[],"floating_nodes":[]}],"floating_nodes":[]}],"floating_nodes":[]}]}' '[{"id":456,"output":"DP-1","visible":true},{"id":789,"output":"HDMI-1","visible":true}]'

cat > "$FAKE_BIN/i3-msg" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$FAKE_BIN/i3-msg"
actual=$(PATH="$FAKE_BIN:$PATH" python3 "$ROOT/dock-apps.py" --fullscreen)
[ "$actual" = unknown ] || {
  printf 'FAIL: ipc-failure expected unknown, got %s\n' "$actual" >&2
  exit 1
}

printf 'PASS: fullscreen CLI tests\n'
