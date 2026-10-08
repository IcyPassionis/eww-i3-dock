#!/bin/sh
# Synchronize the dock with i3 fullscreen transitions.
# `suppress` swaps the visible dock back to the transparent trigger.
# `restore` makes sure the trigger exists after fullscreen ends.
if [ -z "$DOCK_FULLSCREEN_DETACHED" ]; then
  DOCK_FULLSCREEN_DETACHED=1 setsid "$0" "$@" 8>&- 9>&- >/dev/null 2>&1 < /dev/null &
  exit 0
fi

. "$(dirname -- "$0")/dock-common.sh"
ACTION=${1:-}

[ "$ACTION" = suppress ] || [ "$ACTION" = restore ] || exit 2
dock_request_fullscreen "$ACTION" || exit 1
dock_lock "$DOCK_LOCK_TIMEOUT" || exit 1
dock_reconcile_fullscreen || exit 1
# A synchronous caller must not mark a superseded action as applied.
[ "$REQUEST_TOKEN" = "$(dock_read_request)" ] || exit 1
dock_raise
