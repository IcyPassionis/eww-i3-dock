#!/bin/sh
# Show dock (trigger exists only while dock is closed, so this is one-shot
# per genuine enter).
#
# Publish synchronously, before the callback can be detached or blocked on the
# transition lock. Only one trigger enter schedules this bounded worker.
. "$(dirname -- "$0")/dock-common.sh"
if [ -z "$DOCK_SHOW_DETACHED" ]; then
  dock_publish_hover show || exit 1
  DOCK_SHOW_DETACHED=1 setsid "$0" "$HOVER_TOKEN" 8>&- 9>&- >/dev/null 2>&1 < /dev/null &
  exit 0
fi
SHOW_TOKEN=${1:-}
if [ -z "$SHOW_TOKEN" ]; then
  dock_publish_hover show || exit 1
  SHOW_TOKEN=$HOVER_TOKEN
fi
dock_hover_matches "$SHOW_TOKEN" || exit 0
# Serialize transitions via bounded flock so a latest show can take over after
# a slow owner. Only trigger entries start workers; dock hover stays shell-only.
# (A lockdir here once leaked behind a hung `eww open dock` and bricked every
# later show until manually removed.)
dock_lock "${DOCK_SHOW_LOCK_WAIT:-5}" || exit 1
dock_hover_matches "$SHOW_TOKEN" || exit 0
dock_trigger_left_matches "$SHOW_TOKEN" && exit 0

# A confirmed fullscreen state wins over hover. `unknown` is intentionally
# allowed through so a transient i3 IPC failure does not make the dock unusable.
fullscreen_request=$(dock_read_request) || exit 1
fullscreen=$(dock_run python3 "$DOCK_APP_SCRIPT" --fullscreen 2>/dev/null) || fullscreen=unknown
dock_refresh_fullscreen "$fullscreen" "$fullscreen_request" || exit 1
if [ "$fullscreen" = true ]; then
  dock_reconcile_fullscreen || exit 1
  exit 0
fi


# Guard: `eww open` on an open window recreates it (that + dock `:onhover`
# used to be a 15x/s destroy/create storm that vetoed every hide).
# Bump only on a real transition: a bump from a delayed duplicate while the
# dock is already open would veto a legitimate pending hide.
dock_windows=$(dock_eww active-windows 2>/dev/null) || exit 1
if dock_snapshot_has dock; then
  dock_eww close dock_open_area >/dev/null 2>&1 || true
else
  # Fullscreen and active-window IPC may be slow. Revalidate the trigger
  # crossing immediately before committing the open mutation.
  dock_hover_matches "$SHOW_TOKEN" || exit 0
  dock_trigger_left_matches "$SHOW_TOKEN" && exit 0
  # Keep the show token live through open/confirmation so a trigger leave can
  # still be associated with this transition.
  dock_bump_legacy_seq || exit 1
  dock_eww update menu-open=false menu-move-open=false >/dev/null 2>&1 || true
  dock_eww open dock >/dev/null 2>&1 || true
  # Keep the trigger mapped when opening fails so another hover can retry.
  if dock_wait_window dock; then
    if dock_hover_matches "$SHOW_TOKEN" && dock_trigger_left_matches "$SHOW_TOKEN"; then
      # A trigger leave during a slow open means this show may be stale. Query
      # fullscreen again before rollback; a real dock entry changes the hover
      # token and wins this check, while a fresh fullscreen suppress remains
      # authoritative through the shared request reconciliation.
      dock_fs_snapshot=$(dock_read_request) || exit 1
      dock_fs_now=$(dock_run python3 "$DOCK_APP_SCRIPT" --fullscreen 2>/dev/null) || dock_fs_now=unknown
      dock_refresh_fullscreen "$dock_fs_now" "$dock_fs_snapshot" || exit 1
      dock_reconcile_fullscreen || exit 1
      if [ "$dock_fs_now" = false ] && dock_hover_matches "$SHOW_TOKEN" \
          && dock_trigger_left_matches "$SHOW_TOKEN"; then
        dock_windows=$(dock_eww active-windows 2>/dev/null) || exit 1
        if ! dock_snapshot_has dock_open_area; then
          dock_eww open dock_open_area >/dev/null 2>&1 || true
          dock_wait_window dock_open_area || exit 1
        fi
        # Recheck both causes after trigger confirmation. A dock-entry callback
        # supersedes the show token and keeps the now-usable dock visible.
        dock_current_request=$(dock_read_request) || exit 1
        case "$dock_current_request" in *' suppress') dock_reconcile_fullscreen || exit 1 ;; *)
          if dock_hover_matches "$SHOW_TOKEN" && dock_trigger_left_matches "$SHOW_TOKEN" \
              && dock_window_active dock_open_area; then
            dock_eww close dock >/dev/null 2>&1 || true
          else
            dock_eww close dock_open_area >/dev/null 2>&1 || true
          fi
          ;;
        esac
      fi
    else
      dock_eww close dock_open_area >/dev/null 2>&1 || true
    fi
  fi
fi

# A fullscreen request may have arrived while this show owned the lock.
# Finish its latest intent rather than leave the timed-out caller stranded.
dock_reconcile_fullscreen || exit 1
dock_raise
