#!/bin/sh
# Context-menu actions for the dock popover. Reads the context stored by
# dock-ctx.sh, performs the action, then closes the menu.
# Usage: dock-menu.sh pin|closeopen|moveto <1-10>
#
# moveto moves the right-clicked window with i3-msg by workspace *number*:
# i3 maps `workspace number N` onto `N: name` variants, so numbered-named
# setups work; pure-named workspaces (no leading number) are not offered.
#
# NOTE: detached like the other widget commands (eww kills slow ones, and
# launching an app certainly outlives the 200ms default widget timeout).
if [ -z "$DOCK_MENU_DETACHED" ]; then
  DOCK_MENU_DETACHED=1 setsid "$0" "$@" >/dev/null 2>&1 < /dev/null &
  exit 0
fi
python3 - "$@" <<'PYEOF' >/dev/null 2>&1
import configparser, json, os, re, subprocess, sys

CTX = "/tmp/eww-dock-menu.json"
PINNED = os.path.expanduser("~/.config/eww/pinned-apps.json")
APP_DIRS = (os.path.expanduser("~/.local/share/applications"), "/usr/share/applications")

action = sys.argv[1] if len(sys.argv) > 1 else ""
target = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    with open(CTX, encoding="utf-8") as file:
        ctx = json.load(file)
except (OSError, json.JSONDecodeError):
    ctx = {}


def close_menu():
    subprocess.run(["eww", "update", "menu-open=false", "menu-move-open=false"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def load_pinned():
    try:
        with open(PINNED, encoding="utf-8") as file:
            items = json.load(file)
        return items if isinstance(items, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_pinned(items):
    tmp = PINNED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(tmp, PINNED)


def stem_of(entry):
    return (entry.get("desktop") or "").removesuffix(".desktop").lower()


def toggle_pin():
    stem = (ctx.get("desktop") or "").lower()
    if not stem:
        return
    items = load_pinned()
    if any(stem_of(entry) == stem for entry in items):
        save_pinned([entry for entry in items if stem_of(entry) != stem])
        return
    cls = ctx.get("class") or ""
    desktop_file = next((os.path.join(app_dir, stem + ".desktop") for app_dir in APP_DIRS
                         if os.path.isfile(os.path.join(app_dir, stem + ".desktop"))), None)
    if desktop_file is None:
        return
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(desktop_file, encoding="utf-8")
        name = parser["Desktop Entry"].get("Name", stem)
    except (KeyError, configparser.Error, OSError):
        name = stem
    items.append({
        "name": name,
        "desktop": stem + ".desktop",
        "match": re.escape(cls) if cls else re.escape(stem),
        "command": "gtk-launch " + stem + ".desktop",
    })
    save_pinned(items)


def close_or_open():
    con = ctx.get("con") or ""
    if re.fullmatch(r"\d+", con):
        subprocess.run(["i3-msg", f"[con_id={con}] kill"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    command = ctx.get("command") or ""
    if command:
        subprocess.Popen(["sh", "-c", command], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)


def move_to_workspace(number):
    # Strict allowlist: buttons only ever send 1-10, anything else is dropped.
    if number not in {str(n) for n in range(1, 11)}:
        return
    con = ctx.get("con") or ""
    if not re.fullmatch(r"\d+", con):
        return
    subprocess.run(["i3-msg", f"[con_id={con}] move container to workspace number {number}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


try:
    if action == "pin":
        toggle_pin()
    elif action == "closeopen":
        close_or_open()
    elif action == "moveto":
        move_to_workspace(target)
finally:
    close_menu()
PYEOF
