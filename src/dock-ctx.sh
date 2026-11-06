#!/bin/sh
# Right-click on a dock icon: store menu context, show the popover, and veto
# any pending delayed hide (right-click must not race the 300ms hide timer).
# Args: con_id class desktop command pinned(1/0) running(1/0)
#
# NOTE: eww kills widget commands that outlive its internal timeout, so this
# detaches (new session) and exits at once like dock-show.sh.
if [ -z "$DOCK_CTX_DETACHED" ]; then
  DOCK_CTX_DETACHED=1 setsid "$0" "$@" 8>&- 9>&- >/dev/null 2>&1 < /dev/null &
  exit 0
fi
CON="$1"; CLS="$2"; DESK="$3"; CMD="$4"; PINNED="$5"; RUNNING="$6"
. "$(dirname -- "$0")/dock-common.sh"
# Invalidate hide before any slow serialization or Eww update.
dock_bump_seq || exit 1
dock_run python3 - "$RUNTIME_DIR/eww-dock-menu.json" "$CON" "$CLS" "$DESK" "$CMD" <<'PYEOF' >/dev/null 2>&1
import json, os, sys
path, con, cls, desk, cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
temporary = path + ".tmp." + str(os.getpid())
with open(temporary, "w", encoding="utf-8") as file:
    json.dump({"con": con, "class": cls, "desktop": desk, "command": cmd}, file)
os.replace(temporary, path)
PYEOF
[ "$?" -eq 0 ] || exit 1
if [ "$PINNED" = "1" ]; then P=true; else P=false; fi
if [ "$RUNNING" = "1" ]; then R=true; else R=false; fi
if [ -n "$DESK" ]; then H=true; else H=false; fi
dock_eww update menu-open=true "menu-pinned=$P" "menu-running=$R" "menu-has-desktop=$H" menu-move-open=false >/dev/null 2>&1
