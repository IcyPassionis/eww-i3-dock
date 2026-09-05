#!/usr/bin/env python3
import configparser
import glob
import json
import os
import re
import subprocess
import sys
import time

PINNED = "/home/boncuk/.config/eww/pinned-apps.json"
LAUNCHER_ICON = "/usr/share/icons/Adwaita/symbolic/actions/view-app-grid-symbolic.svg"
ICON_ROOTS = [
    os.path.expanduser("~/.local/share/icons"),
    os.path.expanduser("~/.icons"),
    "/usr/share/icons",
    "/usr/share/pixmaps",
]
ICON_CACHE = {}


def tree_windows(node):
    if not isinstance(node, dict):
        return
    if node.get("window") and node.get("window_type") != "dock":
        props = node.get("window_properties") or {}
        yield {
            "id": node["id"],
            "title": node.get("name") or "Uygulama",
            "class": (props.get("class") or "").lower(),
            "instance": (props.get("instance") or "").lower(),
        }
    for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
        yield from tree_windows(child)


def desktop_files():
    paths = []
    for root in (os.path.expanduser("~/.local/share/applications"), "/usr/share/applications"):
        paths.extend(glob.glob(os.path.join(root, "*.desktop")))
    return paths


def desktop_info(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
        section = parser["Desktop Entry"]
        return {
            "file": path,
            "name": section.get("Name", ""),
            "icon": section.get("Icon", ""),
            "wmclass": section.get("StartupWMClass", "").lower(),
            "exec": section.get("Exec", "").lower(),
            "stem": os.path.splitext(os.path.basename(path))[0].lower(),
        }
    except (KeyError, configparser.Error, OSError):
        return None


def icon_path(icon_name):
    if not icon_name:
        return LAUNCHER_ICON
    if os.path.isabs(icon_name) and os.path.exists(icon_name):
        return icon_name
    name = os.path.splitext(os.path.basename(icon_name))[0]
    if name in ICON_CACHE:
        return ICON_CACHE[name]
    # `find` stops at the first match.  Unlike the old recursive Python glob,
    # this is quick even with large icon themes and runs only once per icon
    # while the listener process is alive.
    candidates = []
    try:
        result = subprocess.run(
            ["find", *ICON_ROOTS, "-type", "f", "(",
             "-iname", name + ".svg", "-o", "-iname", name + ".png",
             "-o", "-iname", name + ".xpm", ")", "-print", "-quit"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            candidates.append(result.stdout.strip().splitlines()[0])
    except OSError:
        pass
    if not candidates:
        ICON_CACHE[name] = LAUNCHER_ICON
        return LAUNCHER_ICON
    candidates.sort(key=lambda path: ("scalable" not in path, "symbolic" in path, len(path)))
    ICON_CACHE[name] = candidates[0]
    return ICON_CACHE[name]


def app_for_window(window, entries):
    values = (window["class"], window["instance"])
    for entry in entries:
        identifiers = (entry["wmclass"], entry["stem"], entry["name"].lower())
        if any(identifier and any(identifier in value or value in identifier for value in values) for identifier in identifiers):
            return entry
    return None


def yuck(value):
    return json.dumps(str(value), ensure_ascii=False)


def button(name, icon, action, classes):
    return (
        '(button :class ' + yuck("entry " + classes) +
        ' :onclick ' + yuck(action) +
        ' :tooltip ' + yuck(name) +
        ' (image :class "icon" :path ' + yuck(icon) +
        ' :image-width 32 :image-height 32))'
    )


def render(pinned, entries):
    try:
        raw = subprocess.check_output(["i3-msg", "-t", "get_tree"], text=True)
        windows = list(tree_windows(json.loads(raw)))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        windows = []

    result = [button("Uygulamalar", LAUNCHER_ICON, "rofi -modi drun,run -show drun", "launcher")]
    used = set()

    for item in pinned:
        pattern = re.compile(item.get("match", ""), re.IGNORECASE)
        match = next((window for window in windows if pattern.search(window["class"] + " " + window["instance"])), None)
        desktop = next((entry for entry in entries if entry["stem"] == item.get("desktop", "").removesuffix(".desktop").lower()), None)
        if desktop is None and match:
            desktop = app_for_window(match, entries)
        icon = icon_path((desktop or {}).get("icon", ""))
        action = f"i3-msg '[con_id={match['id']}] focus'" if match else item["command"]
        result.append(button(item["name"], icon, action, "pinned open" if match else "pinned"))
        if match:
            used.add(match["id"])

    for window in windows:
        if window["id"] in used:
            continue
        desktop = app_for_window(window, entries)
        icon = icon_path((desktop or {}).get("icon", ""))
        result.append(button(window["title"], icon, f"i3-msg '[con_id={window['id']}] focus'", "running open"))

    print('(box :class "taskbar" :space-evenly false ' + " ".join(result) + ')')
    sys.stdout.flush()


def main():
    with open(PINNED, encoding="utf-8") as file:
        pinned = json.load(file)
    entries = [entry for path in desktop_files() if (entry := desktop_info(path))]

    render(pinned, entries)
    if "--listen" not in sys.argv:
        return

    # i3 sends one line per window/workspace event.  Keeping this process alive
    # avoids rescanning icons and rebuilding the widget every second.
    subscription = subprocess.Popen(
        ["i3-msg", "-t", "subscribe", "-m", '["window", "workspace"]'],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        for _event in subscription.stdout:
            time.sleep(0.08)
            render(pinned, entries)
    finally:
        subscription.terminate()


if __name__ == "__main__":
    main()
