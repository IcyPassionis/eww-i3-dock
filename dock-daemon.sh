#!/bin/sh
# The only launcher for the long-lived, foreground Eww daemon.
set -u
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
OWNER="$SCRIPT_DIR/dock-owner.py"
EWW=${DOCK_EWW_BIN:-eww}
CONFIG_DIR=${DOCK_CONFIG_DIR:-$SCRIPT_DIR}
CONFIG_DIR=$(CDPATH= cd -- "$CONFIG_DIR" 2>/dev/null && pwd -P) || {
  echo "dock-daemon: config directory does not exist" >&2
  exit 1
}
export DOCK_CONFIG_DIR="$CONFIG_DIR" DOCK_EWW_BIN="$EWW"
if [ -n "${DOCK_RUNTIME_DIR:-}" ]; then
  PATHS=$(python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$DOCK_RUNTIME_DIR" paths) || exit 1
else
  PATHS=$(python3 "$OWNER" --config-dir "$CONFIG_DIR" paths) || exit 1
fi
LOCK=$(printf '%s' "$PATHS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lock_path"])') || exit 1
RUNTIME_DIR=$(printf '%s' "$PATHS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["runtime_dir"])') || exit 1
DOCK_STATE_DIR=$(printf '%s' "$PATHS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["namespace_dir"])') || exit 1
export DOCK_RUNTIME_DIR="$RUNTIME_DIR" DOCK_STATE_DIR
LOG="$RUNTIME_DIR/eww-dock.log"
mkdir -p "$RUNTIME_DIR" || exit 1

# Keep the append-only log bounded. The daemon writes with O_APPEND, so the
# tail is rewritten in place; a line appended during the copy may be lost.
trim_log() {
  [ -f "$LOG" ] || return 0
  log_size=$(wc -c 2>/dev/null <"$LOG" | tr -d ' ')
  case $log_size in ''|*[!0-9]*) return 0 ;; esac
  [ "$log_size" -gt 4194304 ] || return 0
  tail -c 1048576 "$LOG" 2>/dev/null >"$LOG.tmp" && cat "$LOG.tmp" 2>/dev/null >"$LOG"
  rm -f "$LOG.tmp"
}

case ${1:-ensure} in
  ensure)
    trim_log 7>&- 8>&- 9>&-
    # Metadata identity is process/socket based, so a slow Eww IPC response
    # never makes a live owner eligible for replacement.
    if python3 - "$OWNER" "$CONFIG_DIR" "${DOCK_RUNTIME_DIR:-}" <<'PY'
import importlib.util, sys
spec=importlib.util.spec_from_file_location("dock_owner", sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
raise SystemExit(0 if m.verified_owner(sys.argv[2], sys.argv[3] or None) else 1)
PY
    then exit 0; fi
    # Competing reloads can all request ensure; the independent flock admits
    # only one supervisor. Each caller waits a bounded time for its metadata.
    "$0" run 7>&- 8>&- 9>&- </dev/null >>"$LOG" 2>&1 &
    attempts=${DOCK_OWNER_WAIT_ATTEMPTS:-60}
    delay=${DOCK_OWNER_WAIT_DELAY:-0.05}
    budget=$(LC_NUMERIC=C python3 -c 'import sys; print(int(sys.argv[1])*float(sys.argv[2]))' "$attempts" "$delay") || exit 1
    python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$RUNTIME_DIR" wait --timeout "$budget" && exit 0
    echo "dock-daemon: no verified managed Eww owner became ready; see $LOG" >&2
    exit 1
    ;;
  run) : ;;
  *) echo "usage: $0 [ensure|run]" >&2; exit 2 ;;
esac

# fd 7 is lifetime ownership. It is intentionally never unlinked and is
# closed in every child so the daemon cannot keep the supervisor lock alive.
exec 7>"$LOCK" || exit 1
if ! flock -n 7 8>&- 9>&-; then
  exit 0
fi

# A SIGKILLed supervisor leaves its Eww running. Reclaim only the owner.json
# record whose supervisor is gone; any uncertainty leaves it to unmanaged.
if [ -n "${DOCK_RUNTIME_DIR:-}" ]; then
  python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$DOCK_RUNTIME_DIR" reclaim 7>&- 8>&- 9>&- || true
else
  python3 "$OWNER" --config-dir "$CONFIG_DIR" reclaim 7>&- 8>&- 9>&- || true
fi

if [ -n "${DOCK_RUNTIME_DIR:-}" ]; then
  python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$DOCK_RUNTIME_DIR" unmanaged
else
  python3 "$OWNER" --config-dir "$CONFIG_DIR" unmanaged
fi
if [ "$?" -ne 0 ]; then
  echo "dock-daemon: unmanaged Eww instance detected; controlled migration required" >&2
  exit 1
fi

SUPERVISOR_START=$(python3 -c 'import sys; s=open("/proc/%s/stat" % sys.argv[1]).read(); print(s[s.rfind(")")+2:].split()[19])' "$$") || exit 1
CHILD=
RECORDED=false
cleanup() {
  if [ "$RECORDED" = true ]; then
    if [ -n "${DOCK_RUNTIME_DIR:-}" ]; then
      python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$DOCK_RUNTIME_DIR" remove "$$" "$SUPERVISOR_START" >/dev/null 2>&1 || true
    else
      python3 "$OWNER" --config-dir "$CONFIG_DIR" remove "$$" "$SUPERVISOR_START" >/dev/null 2>&1 || true
    fi
  fi
}
terminate() {
  trap '' HUP INT TERM
  if [ -n "$CHILD" ]; then
    stop_child
    wait "$CHILD" 2>/dev/null || true
    CHILD=
  fi
  exit 143
}
stop_child() {
  python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$RUNTIME_DIR" stop "$CHILD" --timeout "${DOCK_OWNER_STOP_TIMEOUT:-1}" 7>&- 8>&- 9>&- || true
}
trap cleanup 0
trap terminate HUP INT TERM

"$EWW" --config "$CONFIG_DIR" --no-daemonize daemon 7>&- 8>&- 9>&- >>"$LOG" 2>&1 &
CHILD=$!
if [ -n "${DOCK_RUNTIME_DIR:-}" ]; then
  RECORDED=true
  if python3 "$OWNER" --config-dir "$CONFIG_DIR" --runtime-dir "$DOCK_RUNTIME_DIR" record "$CHILD" --timeout "${DOCK_OWNER_READY_TIMEOUT:-3}" --supervisor-pid "$$" --supervisor-start-time "$SUPERVISOR_START"; then RECORDED=true
  else stop_child; wait "$CHILD" 2>/dev/null || true; CHILD=; exit 1; fi
else
  RECORDED=true
  if python3 "$OWNER" --config-dir "$CONFIG_DIR" record "$CHILD" --timeout "${DOCK_OWNER_READY_TIMEOUT:-3}" --supervisor-pid "$$" --supervisor-start-time "$SUPERVISOR_START"; then RECORDED=true
  else stop_child; wait "$CHILD" 2>/dev/null || true; CHILD=; exit 1; fi
fi
set +e
wait "$CHILD"
status=$?
set -e
CHILD=
exit "$status"
