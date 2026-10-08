#!/bin/sh
# Toggle the "Move to workspace" submenu inside the context-menu popover.
# Must NOT touch menu-open: toggling must not dismiss the parent popover
# (cf. CLOSE_MENU on the icon buttons, which deliberately does).
#
# NOTE: detached like the other widget commands (eww kills slow ones).
if [ -z "$DOCK_MOVE_DETACHED" ]; then
  DOCK_MOVE_DETACHED=1 setsid "$0" 8>&- 9>&- >/dev/null 2>&1 < /dev/null &
  exit 0
fi
. "$(dirname -- "$0")/dock-common.sh"
move_open=$(dock_eww get menu-move-open 2>/dev/null) || exit 1
case "$move_open" in
  true) dock_eww update menu-move-open=false >/dev/null 2>&1 ;;
  false) dock_eww update menu-move-open=true >/dev/null 2>&1 ;;
  *) exit 1 ;;
esac
