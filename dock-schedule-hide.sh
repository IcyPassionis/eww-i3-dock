#!/bin/sh
# Delayed hide: 300ms grace so moving between icons doesn't flicker.
# Aborts if dock-show.sh / dock-cancel-hide.sh bumped the sequence
# meanwhile (re-enter/cancel).
#
# Capture the leave token in the synchronous callback process, before detaching
# the timer. A late-starting child must never adopt a newer generation.
. "$(dirname -- "$0")/dock-common.sh"
if [ -z "$DOCK_HIDE_DETACHED" ]; then
  dock_publish_hover leave || exit 1
  DOCK_HIDE_DETACHED=1 setsid "$0" "$HOVER_TOKEN" 8>&- 9>&- >/dev/null 2>&1 < /dev/null &
  exit 0
fi
HIDE_TOKEN=${1:-}
if [ -z "$HIDE_TOKEN" ]; then
  dock_publish_hover leave || exit 1
  HIDE_TOKEN=$HOVER_TOKEN
fi
HIDE_INTENT="$HIDE_TOKEN leave"
HIDE_DELAY=${DOCK_HIDE_DELAY:-0.3}
dock_read_hover | grep -Fxq "$HIDE_INTENT" || exit 0
dock_pause "$HIDE_DELAY"
dock_read_hover | grep -Fxq "$HIDE_INTENT" || exit 0

# Serialize the replacement with dock-show.sh. Recheck the sequence after
# taking the lock in case a show/cancel transition won the race.
dock_lock "${DOCK_HIDE_LOCK_WAIT:-5}" || exit 1
dock_read_hover | grep -Fxq "$HIDE_INTENT" || exit 0


# Dismiss a stale popover with the dock (its hover veto only fires inside).
dock_eww update menu-open=false menu-move-open=false >/dev/null 2>&1 || true

# Create and confirm the trigger before removing the usable dock. `eww open`
# on an already-open window recreates it, so keep the active-window guard.
dock_windows=$(dock_eww active-windows 2>/dev/null) || exit 1
if dock_snapshot_has dock_open_area; then
  trigger_ready=true
else
  dock_eww open dock_open_area >/dev/null 2>&1 || true
  if dock_wait_window dock_open_area; then
    trigger_ready=true
  else
    trigger_ready=false
  fi
fi

if [ "$trigger_ready" = true ] && dock_window_active dock; then
  # Trigger creation/confirmation can be slow. Cancellation is zero IPC and
  # can advance the generation while we hold the transition lock.
  if ! dock_read_hover | grep -Fxq "$HIDE_INTENT"; then
    dock_eww close dock_open_area >/dev/null 2>&1 || true
    dock_reconcile_fullscreen || exit 1
    dock_raise
    exit 0
  fi
  dock_eww close dock >/dev/null 2>&1 || true
  # A hover can arrive after the pre-close check while the Eww close IPC is
  # still in flight. The transition lock keeps its worker queued, so reconcile
  # that latest request here before releasing ownership of the swap.
  dock_reconcile_fullscreen || exit 1
  dock_current_hover=$(dock_read_hover)
  case "$dock_current_hover" in
    *' cancel'|*' show')
      if [ "$dock_current_hover" != "$HIDE_INTENT" ]; then
        dock_fs_snapshot=$(dock_read_request) || exit 1
        dock_fs_now=$(dock_run python3 "$DOCK_APP_SCRIPT" --fullscreen 2>/dev/null) || dock_fs_now=unknown
        dock_refresh_fullscreen "$dock_fs_now" "$dock_fs_snapshot" || exit 1
        dock_reconcile_fullscreen || exit 1
        if [ "$dock_fs_now" = false ]; then
          dock_current_hover=$(dock_read_hover)
          case "$dock_current_hover" in *' cancel'|*' show')
            dock_windows=$(dock_eww active-windows 2>/dev/null) || exit 1
            if ! dock_snapshot_has dock; then
              dock_eww update menu-open=false menu-move-open=false >/dev/null 2>&1 || true
              dock_eww open dock >/dev/null 2>&1 || true
              dock_wait_window dock || exit 1
            fi
            # Reconcile a fresh fullscreen event after confirmation. Unknown
            # state preserves both usable windows; known suppression wins.
            dock_fs_snapshot=$(dock_read_request) || exit 1
            dock_fs_now=$(dock_run python3 "$DOCK_APP_SCRIPT" --fullscreen 2>/dev/null) || dock_fs_now=unknown
            dock_refresh_fullscreen "$dock_fs_now" "$dock_fs_snapshot" || exit 1
            dock_reconcile_fullscreen || exit 1
            if [ "$dock_fs_now" = false ] && dock_window_active dock; then
              dock_eww close dock_open_area >/dev/null 2>&1 || true
            fi
            ;;
          esac
        fi
      fi
      ;;
  esac
fi
dock_reconcile_fullscreen || exit 1
dock_raise
