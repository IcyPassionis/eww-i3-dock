#!/bin/sh
# Right-click on a dock icon: store menu context, show the popover, and veto
# any pending delayed hide (right-click must not race the 300ms hide timer).
# Args: con_id class desktop command pinned(1/0) running(1/0)
#
# NOTE: eww kills widget commands that outlive its internal timeout, so this
# detaches (new session) and exits at once like dock-show.sh.
if [ -z "$DOCK_CTX_DETACHED" ]; then
  DOCK_CTX_DETACHED=1 setsid "$0" "$@" >/dev/null 2>&1 < /dev/null &
  exit 0
fi
CON="$1"; CLS="$2"; DESK="$3"; CMD="$4"; PINNED="$5"; RUNNING="$6"
python3 - "/tmp/eww-dock-menu.json" "$CON" "$CLS" "$DESK" "$CMD" <<'PYEOF' >/dev/null 2>&1
import json, sys
path, con, cls, desk, cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
with open(path, "w", encoding="utf-8") as file:
    json.dump({"con": con, "class": cls, "desktop": desk, "command": cmd}, file)
PYEOF
if [ "$PINNED" = "1" ]; then P=true; else P=false; fi
if [ "$RUNNING" = "1" ]; then R=true; else R=false; fi
if [ -n "$DESK" ]; then H=true; else H=false; fi
eww update menu-open=true "menu-pinned=$P" "menu-running=$R" "menu-has-desktop=$H" >/dev/null 2>&1 || true
# Veto a pending hide (fd-based flock: flock -c would run $SHELL, e.g. fish).
exec 9>/tmp/eww-dock-seq.lock
flock 9
n=$(cat /tmp/eww-dock-seq 2>/dev/null || echo 0)
echo $((n + 1)) > /tmp/eww-dock-seq
flock -u 9
exec 9>&-
