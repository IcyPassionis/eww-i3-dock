#!/bin/sh
# Set the trigger's hover width. dock-apps.py calls this on width changes only.
. "$(dirname -- "$0")/dock-common.sh"
case ${1:-} in ''|*[!0-9]*) exit 2 ;; esac
dock_eww update dock-hit-width="$1" >/dev/null 2>&1
