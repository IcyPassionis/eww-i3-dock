#!/bin/sh
# Hovering the already-open dock: only invalidate a pending delayed hide.
# Deliberately zero IPC (`eww open` on an open window recreates it, which
# refires onhover under a stationary cursor into a hot loop). May run often.
. "$(dirname -- "$0")/dock-common.sh"
if [ "${1:-}" = trigger-left ]; then
  # This event can be a synthetic crossing from the trigger being unmapped as
  # the dock appears. Record it against the current show token without
  # replacing that intent; the show worker only uses it as a pre-open veto.
  dock_note_trigger_left
else
  dock_bump_seq
fi
