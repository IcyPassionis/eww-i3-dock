#!/usr/bin/env python3
import configparser
import glob
import json
import os
import re
import struct
import subprocess
import sys
import time
import zlib

PINNED = "/home/boncuk/.config/eww/pinned-apps.json"
_PINNED = []
_PINNED_MTIME = None
LAUNCHER_ICON = "/usr/share/icons/Adwaita/symbolic/actions/view-app-grid-symbolic.svg"
ICON_BASES = [
    os.path.expanduser("~/.local/share/icons"),
    os.path.expanduser("~/.icons"),
    "/usr/share/icons",
]
PIXMAPS = "/usr/share/pixmaps"
IMAGE_EXTS = (".svg", ".png", ".xpm")
XICON_DIR = os.path.expanduser("~/.cache/eww/icons")
ICON_CACHE = {}

# Active GTK theme + inheritance, resolved once at startup (cheap: a few
# small text files, never per render).
THEME_CHAIN = []


def tree_windows(node):
    if not isinstance(node, dict):
        return
    if node.get("window") and node.get("window_type") != "dock":
        props = node.get("window_properties") or {}
        yield {
            "id": node["id"],
            "xwin": node.get("window"),
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


def norm(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def build_index(entries):
    """Exact-match index over StartupWMClass + file stem (first wins).

    The `.desktop` Name is deliberately excluded: generic names like
    "Browser" or "Files" substring-match unrelated windows (Brave once
    matched a Waydroid "Browser" entry and showed a Jelly icon).
    """
    index = {}
    for entry in entries:
        for key in (norm(entry["wmclass"]), norm(entry["stem"])):
            if key and key not in index:
                index[key] = entry
    return index


def app_for_window(window, entries, index):
    keys = [key for key in (norm(window["class"]), norm(window["instance"])) if key]
    for key in keys:
        if key in index:
            return index[key]
    # Last resort: substring match on wmclass/stem only (never Name), e.g.
    # class "unity" vs stem "unityhub". Exact pass above runs first, so
    # "zen" hits zen.desktop before it could reach "zenity".
    for entry in entries:
        identifiers = [norm(entry["wmclass"]), norm(entry["stem"])]
        if any(identifier and any(identifier in key or key in identifier for key in keys)
               for identifier in identifiers):
            return entry
    return None


def gtk_theme_name():
    for path in (os.path.expanduser("~/.config/gtk-3.0/settings.ini"),
                 os.path.expanduser("~/.config/gtk-4.0/settings.ini")):
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            parser.read(path, encoding="utf-8")
            name = parser.get("Settings", "gtk-icon-theme-name", fallback="").strip()
            if name:
                return name
        except (configparser.Error, OSError):
            continue
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "icon-theme"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        name = result.stdout.strip().strip("'\"")
        if name:
            return name
    except OSError:
        pass
    return "hicolor"


def theme_chain(theme):
    """Active GTK theme + Inherits, ending in hicolor (freedesktop spec)."""
    chain, seen, stack = [], set(), [theme]
    while stack and len(chain) < 8:
        current = stack.pop(0)
        if not current or current in seen:
            continue
        seen.add(current)
        chain.append(current)
        for base in ICON_BASES:
            index_theme = os.path.join(base, current, "index.theme")
            if not os.path.isfile(index_theme):
                continue
            try:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.optionxform = str
                parser.read(index_theme, encoding="utf-8")
                inherits = parser.get("Icon Theme", "Inherits", fallback="")
                for parent in [p.strip() for p in inherits.split(",") if p.strip()]:
                    if parent not in seen:
                        stack.append(parent)
            except (configparser.Error, OSError):
                continue
            break
    if "hicolor" not in seen:
        chain.append("hicolor")
    return chain


def base_rank_of(path):
    for rank, base in enumerate(ICON_BASES):
        if path.startswith(base + "/"):
            return rank
    return 99


def theme_rank_of(path):
    low = "/" + path.lower() + "/"
    for rank, theme in enumerate(THEME_CHAIN):
        if "/" + theme.lower() + "/" in low:
            return rank
    return 99


def rank_icon(paths):
    """Best hit wins: real icons over symbolic, scalable over fixed size,
    largest size next, active GTK theme (in chain order) breaks ties."""

    def size_of(path):
        match = re.search(r"(\d+)[xX](\d+)", path)
        if match:
            return int(match.group(1)) * int(match.group(2))
        match = re.search(r"/(\d+)/", path)  # breeze-style ".../apps/48/..."
        if match:
            return int(match.group(1)) ** 2
        return 0

    def key(path):
        low = path.lower()
        return ("symbolic" in low, "scalable" not in low, -size_of(path),
                base_rank_of(path), theme_rank_of(path), len(path))

    return sorted(paths, key=key)[0]


def find_in_dirs(directories, name):
    """All theme-file hits under the given dirs (small sets, ranked later).

    A single `find` invocation; results are cached in-process in ICON_CACHE
    so this runs at most once per icon name per listener life.
    """
    existing = [d for d in directories if os.path.isdir(d)]
    if not existing:
        return []
    try:
        result = subprocess.run(
            ["find", *existing, "-type", "f", "(",
             "-iname", name + ".svg", "-o", "-iname", name + ".png",
             "-o", "-iname", name + ".xpm", ")", "-print"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


def prefetch_icon_names(icon_names):
    """Resolve many names with ONE `find` walk (startup), then rank per name.

    Cold-start optimization: a per-name walk is ~0.2s x N icons, this is one
    walk total. Results land in ICON_CACHE for find_icon(). Absolute paths
    and cached names are skipped.
    """
    wanted = []
    for raw in icon_names:
        if not raw or os.path.isabs(raw):
            continue
        name = strip_image_ext(raw)
        if name not in ICON_CACHE and name not in wanted:
            wanted.append(name)
    if not wanted:
        return
    conditions = []
    for name in wanted:
        conditions += ["-iname", name + ".svg", "-o",
                       "-iname", name + ".png", "-o",
                       "-iname", name + ".xpm", "-o"]
    conditions.pop()
    try:
        result = subprocess.run(
            ["find", *ICON_BASES, "-type", "f", "(", *conditions, ")", "-print"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        result = None
    hits = {}
    if result is not None:
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            stem = strip_image_ext(line).lower()
            for name in wanted:
                if stem == name.lower():
                    hits.setdefault(name, []).append(line)
                    break
    for name in wanted:
        ICON_CACHE[name] = rank_icon(hits[name]) if hits.get(name) else None


def strip_image_ext(icon_name):
    base = os.path.basename(icon_name)
    low = base.lower()
    for ext in IMAGE_EXTS:
        if low.endswith(ext):
            return base[: -len(ext)]
    # NOT splitext: reverse-DNS names like org.pulseaudio.pavucontrol must
    # keep their dots, or the lookup silently searches a truncated name.
    return base


def find_icon(icon_name):
    """Theme-aware lookup. Returns a path or None (caller picks fallback).

    All hits across every installed theme are ranked together: real icons
    over symbolic, scalable over fixed size, largest size next, then user
    dirs over system dirs, then the active GTK theme chain (breeze-dark ->
    breeze -> hicolor) breaks ties. So a 16px themed icon never beats a
    scalable one from another theme, and your Candy/WhiteSur look survives
    where the GTK theme has no (or only a tiny) hit.
    """
    if not icon_name:
        return None
    if os.path.isabs(icon_name):
        return icon_name if os.path.exists(icon_name) else None
    name = strip_image_ext(icon_name)
    if name in ICON_CACHE:
        return ICON_CACHE[name]

    pool = find_in_dirs(ICON_BASES, name)
    for ext in IMAGE_EXTS:
        candidate = os.path.join(PIXMAPS, name + ext)
        if os.path.exists(candidate):
            pool.append(candidate)
            break
    found = rank_icon(pool) if pool else None

    ICON_CACHE[name] = found
    return found


def parse_net_wm_icon(text):
    """Parse `xprop _NET_WM_ICON` output, return (w, h, pixels) of largest."""
    try:
        numbers = [int(n) for n in re.findall(r"-?\d+", text.split("=", 1)[1])]
    except (IndexError, ValueError):
        return None
    best, i = None, 0
    while i + 1 < len(numbers):
        width, height = numbers[i], numbers[i + 1]
        if not 0 < width <= 1024 or not 0 < height <= 1024:
            break
        pixels = numbers[i + 2: i + 2 + width * height]
        if len(pixels) < width * height:
            break
        if best is None or width * height > best[0] * best[1]:
            best = (width, height, pixels)
        i += 2 + width * height
    return best


def write_png(path, width, height, argb):
    rgba = bytearray(width * height * 4)
    for index, value in enumerate(argb):
        rgba[index * 4] = (value >> 16) & 0xFF
        rgba[index * 4 + 1] = (value >> 8) & 0xFF
        rgba[index * 4 + 2] = value & 0xFF
        rgba[index * 4 + 3] = (value >> 24) & 0xFF

    def chunk(chunk_type, data):
        head = struct.pack(">I", len(data)) + chunk_type + data
        return head + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgba[y * width * 4:(y + 1) * width * 4]) for y in range(height))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as file:
        file.write(png)
    os.replace(tmp, path)


def xwin_icon(xwin, key):
    """Icon straight from the application via _NET_WM_ICON.

    Last-resort fallback when no theme file exists. Cached on disk
    (~/.cache/eww/icons/) so xprop runs once per app ever, plus in-process
    via ICON_CACHE through the caller.
    """
    if not xwin or not key:
        return None
    safe = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-") or "app"
    path = os.path.join(XICON_DIR, safe + ".png")
    if os.path.exists(path):
        return path
    try:
        result = subprocess.run(
            ["xprop", "-id", str(xwin), "_NET_WM_ICON"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or "not found" in result.stdout:
        return None
    parsed = parse_net_wm_icon(result.stdout)
    if parsed is None:
        return None
    width, height, pixels = parsed
    try:
        write_png(path, width, height, pixels)
    except OSError:
        return None
    return path


def icon_for_window(window, entries, index):
    desktop = app_for_window(window, entries, index)
    if desktop:
        found = find_icon(desktop.get("icon", ""))
        if found:
            return found
    key = window["class"] or window["instance"] or window["title"]
    cache_key = "x11:" + key
    if cache_key in ICON_CACHE:
        return ICON_CACHE[cache_key] or LAUNCHER_ICON
    found = xwin_icon(window["xwin"], key) or LAUNCHER_ICON
    ICON_CACHE[cache_key] = found if found != LAUNCHER_ICON else None
    return found


def current_windows():
    try:
        raw = subprocess.check_output(["i3-msg", "-t", "get_tree"], text=True)
        return list(tree_windows(json.loads(raw)))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return []


def load_pinned(force=False):
    """Pinned launchers, re-read when dock-menu.sh rewrites the file.

    A stat per render is cheap (renders only happen on i3 events); without
    this, pin/unpin would not show until the daemon restarts.
    """
    global _PINNED, _PINNED_MTIME
    try:
        mtime = os.path.getmtime(PINNED)
    except OSError:
        return _PINNED
    if force or mtime != _PINNED_MTIME:
        try:
            with open(PINNED, encoding="utf-8") as file:
                items = json.load(file)
            if isinstance(items, list):
                _PINNED = items
                _PINNED_MTIME = mtime
        except (OSError, json.JSONDecodeError):
            pass
    return _PINNED


def yuck(value):
    return json.dumps(str(value), ensure_ascii=False)


def shquote(value):
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def ctx_cmd(con, cls, desktop, command, pinned, running):
    """Right-click handler: dock-ctx.sh shows the popover for this icon."""
    args = [str(con or ""), cls or "", desktop or "", command or "",
            "1" if pinned else "0", "1" if running else "0"]
    return "/home/boncuk/.config/eww/dock-ctx.sh " + " ".join(shquote(arg) for arg in args)


# Dismiss the popover on any left-click (stale menu must not linger).
CLOSE_MENU = "; eww update menu-open=false >/dev/null 2>&1 || true"


def button(name, icon, action, classes, ctx=None):
    parts = (
        '(button :class ' + yuck("entry " + classes) +
        ' :onclick ' + yuck(action + CLOSE_MENU) +
        ' :timeout "5s"'
    )
    if ctx:
        parts += ' :onrightclick ' + yuck(ctx)
    return (
        parts +
        ' :tooltip ' + yuck(name) +
        ' (image :class "icon" :path ' + yuck(icon) +
        ' :image-width 32 :image-height 32))'
    )


def render(entries, index):
    pinned = load_pinned()
    windows = current_windows()

    result = [button("Uygulamalar", LAUNCHER_ICON, "rofi -modi drun,run -show drun", "launcher")]
    used = set()

    for item in pinned:
        pattern = re.compile(item.get("match", ""), re.IGNORECASE)
        match = next((window for window in windows if pattern.search(window["class"] + " " + window["instance"])), None)
        desktop_stem = item.get("desktop", "").removesuffix(".desktop").lower()
        desktop = next((entry for entry in entries if entry["stem"] == desktop_stem), None)
        if desktop is None and match:
            desktop = app_for_window(match, entries, index)
        icon = find_icon((desktop or {}).get("icon", "")) if desktop else None
        if icon is None and match:
            key = match["class"] or match["instance"] or item["name"]
            icon = xwin_icon(match["xwin"], key)
        icon = icon or LAUNCHER_ICON
        action = f"i3-msg '[con_id={match['id']}] focus'" if match else item["command"]
        ctx = ctx_cmd(match["id"] if match else "", match["class"] if match else "",
                      desktop_stem, item["command"], True, match is not None)
        result.append(button(item["name"], icon, action, "pinned open" if match else "pinned", ctx))
        if match:
            used.add(match["id"])

    for window in windows:
        if window["id"] in used:
            continue
        icon = icon_for_window(window, entries, index)
        desktop = app_for_window(window, entries, index)
        ctx = ctx_cmd(window["id"], window["class"], desktop["stem"] if desktop else "",
                      "", False, True)
        result.append(button(window["title"], icon, f"i3-msg '[con_id={window['id']}] focus'", "running open", ctx))

    print('(box :class "taskbar" :space-evenly false ' + " ".join(result) + ')')
    sys.stdout.flush()


def main():
    global THEME_CHAIN
    pinned = load_pinned(force=True)
    entries = [entry for path in desktop_files() if (entry := desktop_info(path))]
    index = build_index(entries)
    THEME_CHAIN = theme_chain(gtk_theme_name())
    # One walk for exactly the icons on screen right now (pinned + running);
    # renders afterwards are cache hits. Prefetching ALL .desktop icons would
    # mean thousands of -iname patterns per file visited (~27s), so scope it.
    needed = []
    windows = current_windows()
    for item in pinned:
        desktop = next((entry for entry in entries
                        if entry["stem"] == item.get("desktop", "").removesuffix(".desktop").lower()), None)
        if desktop:
            needed.append(desktop["icon"])
    for window in windows:
        desktop = app_for_window(window, entries, index)
        if desktop:
            needed.append(desktop["icon"])
    prefetch_icon_names(needed)

    render(entries, index)
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
            render(entries, index)
    finally:
        subscription.terminate()


if __name__ == "__main__":
    main()
