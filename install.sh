#!/bin/sh
# Install eww-i3-dock into the live Eww config directory.
set -eu
case ${1:-} in
  '') REPLACE_PINS=false ;;
  --replace-pins) REPLACE_PINS=true ;;
  *) printf 'usage: %s [--replace-pins]\n' "$0" >&2; exit 2 ;;
esac
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.config/eww"
mkdir -p "$DEST"
cp "$SRC/eww.yuck" "$SRC/eww.scss" "$SRC/dock-apps.py" "$SRC/dock-owner.py" "$SRC/dock-daemon.sh" "$SRC/dock-common.sh" "$SRC/start-dock.sh" "$SRC/install.sh" "$SRC/dock-show.sh" "$SRC/dock-fullscreen.sh" "$SRC/dock-cancel-hide.sh" "$SRC/dock-schedule-hide.sh" "$SRC/dock-ctx.sh" "$SRC/dock-menu.sh" "$SRC/dock-move-toggle.sh" "$SRC/dock-hit-width.sh" "$SRC/volume-popup.sh" "$SRC/volume-listen.sh" "$DEST/"
if [ ! -e "$DEST/pinned-apps.json" ] || [ "$REPLACE_PINS" = true ]; then
  cp "$SRC/pinned-apps.json" "$DEST/pinned-apps.json"
fi
if [ -f "$SRC/dock-x11.py" ]; then cp "$SRC/dock-x11.py" "$DEST/"; fi
chmod +x "$DEST/dock-apps.py" "$DEST/dock-owner.py" "$DEST/dock-daemon.sh" "$DEST/start-dock.sh" "$DEST/dock-show.sh" "$DEST/dock-fullscreen.sh" "$DEST/dock-cancel-hide.sh" "$DEST/dock-schedule-hide.sh" "$DEST/dock-ctx.sh" "$DEST/dock-menu.sh" "$DEST/dock-move-toggle.sh" "$DEST/dock-hit-width.sh" "$DEST/volume-popup.sh" "$DEST/volume-listen.sh"
echo "installed to $DEST"
