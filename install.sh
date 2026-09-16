#!/bin/sh
# Install eww-i3-dock into the live Eww config directory.
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.config/eww"
mkdir -p "$DEST"
cp "$SRC/eww.yuck" "$SRC/eww.scss" "$SRC/dock-apps.py" "$SRC/pinned-apps.json" "$SRC/start-dock.sh" "$SRC/install.sh" "$SRC/dock-show.sh" "$SRC/dock-fullscreen.sh" "$SRC/dock-cancel-hide.sh" "$SRC/dock-schedule-hide.sh" "$SRC/dock-ctx.sh" "$SRC/dock-menu.sh" "$SRC/dock-move-toggle.sh" "$DEST/"
chmod +x "$DEST/dock-apps.py" "$DEST/start-dock.sh" "$DEST/dock-show.sh" "$DEST/dock-fullscreen.sh" "$DEST/dock-cancel-hide.sh" "$DEST/dock-schedule-hide.sh" "$DEST/dock-ctx.sh" "$DEST/dock-menu.sh" "$DEST/dock-move-toggle.sh"
echo "installed to $DEST"
