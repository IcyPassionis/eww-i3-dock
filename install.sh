#!/bin/sh
# Install eww-i3-dock into the Eww config directory (default ~/.config/eww).
set -eu
case ${1:-} in
  '') REPLACE_PINS=false ;;
  --replace-pins) REPLACE_PINS=true ;;
  *) printf 'usage: %s [--replace-pins]\n' "$0" >&2; exit 2 ;;
esac
SRC="$(cd "$(dirname "$0")" && pwd)/src"
DEST=${DOCK_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/eww}
mkdir -p "$DEST"
for file in "$SRC"/*; do
  [ -f "$file" ] || continue
  case $(basename "$file") in
    pinned-apps.json) ;;
    *) cp "$file" "$DEST/" ;;
  esac
done
if [ ! -e "$DEST/pinned-apps.json" ] || [ "$REPLACE_PINS" = true ]; then
  cp "$SRC/pinned-apps.json" "$DEST/pinned-apps.json"
fi
chmod +x "$DEST"/*.sh "$DEST"/*.py
echo "installed to $DEST"
