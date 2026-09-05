#!/bin/sh
# Install eww-i3-dock into the live Eww config directory.
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.config/eww"
mkdir -p "$DEST"
cp "$SRC/eww.yuck" "$SRC/eww.scss" "$SRC/dock-apps.py" "$SRC/pinned-apps.json" "$SRC/start-dock.sh" "$DEST/"
chmod +x "$DEST/dock-apps.py" "$DEST/start-dock.sh"
echo "installed to $DEST"
