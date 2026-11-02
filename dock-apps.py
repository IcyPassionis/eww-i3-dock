#!/usr/bin/env python3
import configparser
import glob
import importlib.util
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
PIN_TICK = "dock-pins"  # i3 tick payload meaning "pinned-apps.json changed"
LAUNCHER_ICON = "/usr/share/icons/Adwaita/symbolic/actions/view-app-grid-symbolic.svg"
# Flatpak exports are symlink trees. They are tiny, so they get a symlink-aware
# walk; the big theme trees keep `-type f` (matching their many symlinks costs
# ~2x on startup, since find then name-tests every link).
FLATPAK_ICON_BASES = (
    "/var/lib/flatpak/exports/share/icons",
    os.path.expanduser("~/.local/share/flatpak/exports/share/icons"),
)
ICON_BASES = [
    os.path.expanduser("~/.local/share/icons"),
    os.path.expanduser("~/.icons"),
    "/usr/share/icons",
    # Flatpak exports (icons/.desktop files of Flatpak apps live only here).
    *FLATPAK_ICON_BASES,
]
APP_DIRS = (
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
)
PIXMAPS = "/usr/share/pixmaps"
IMAGE_EXTS = (".svg", ".png", ".xpm")
XICON_DIR = os.path.expanduser("~/.cache/eww/icons")
ICON_CACHE = {}
FULLSCREEN_SCRIPT = "/home/boncuk/.config/eww/dock-fullscreen.sh"

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
    for root in APP_DIRS:
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


def run_find(directories, conditions):
    """Icon files under `directories` matching the -iname `conditions`.

    Regular files everywhere; symlinks too, but only under the Flatpak export
    dirs. Dangling links are dropped. Returns [] if find is unavailable.
    """
    hits = []
    for bases, kinds in (
        ([d for d in directories if d not in FLATPAK_ICON_BASES], ["-type", "f"]),
        ([d for d in directories if d in FLATPAK_ICON_BASES],
         ["(", "-type", "f", "-o", "-type", "l", ")"]),
    ):
        bases = [d for d in bases if os.path.isdir(d)]
        if not bases:
            continue
        try:
            result = subprocess.run(
                ["find", *bases, *kinds, "(", *conditions, ")", "-print"],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            continue
        hits += [line for line in result.stdout.strip().splitlines()
                 if line and os.path.exists(line)]
    return hits


def find_in_dirs(directories, name):
    """All theme-file hits under the given dirs (small sets, ranked later).

    Results are cached in-process in ICON_CACHE so this runs at most once
    per icon name per listener life.
    """
    return run_find(directories, ["-iname", name + ".svg", "-o", "-iname", name + ".png",
                                  "-o", "-iname", name + ".xpm"])


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
    hits = {}
    for line in run_find(ICON_BASES, conditions):
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


def _fullscreen_mode(node):
    try:
        return int(node.get("fullscreen_mode", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def tree_has_window_fullscreen(node, mode=None):
    """Return whether an X11 window has the requested fullscreen mode."""
    if not isinstance(node, dict):
        return False
    current = _fullscreen_mode(node)
    if node.get("window") is not None and current > 0 and (mode is None or current == mode):
        return True
    return any(tree_has_window_fullscreen(child, mode)
               for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []))


def output_for_monitor(monitor, outputs):
    """Match physical GDK identity/geometry, never another API's ordinal."""
    if not isinstance(monitor, dict) or not isinstance(outputs, list):
        return None
    active = [output for output in outputs
              if isinstance(output, dict) and output.get("active") is True]
    name = monitor.get("name")
    if name and any(output.get("name") == name for output in active):
        return name
    rect = monitor.get("rect")
    matches = [output.get("name") for output in active
               if rect and output.get("rect") == rect]
    return matches[0] if len(matches) == 1 else None


def native_x11():
    """Load the sibling helper without requiring an importable package."""
    global _NATIVE_X11
    if _NATIVE_X11 is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dock-x11.py")
        spec = importlib.util.spec_from_file_location("dock_x11", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _NATIVE_X11 = module
    return _NATIVE_X11


_NATIVE_X11 = None


def dock_monitor():
    return native_x11().monitor(0)


def current_outputs():
    try:
        raw = subprocess.check_output(["i3-msg", "-t", "get_outputs"], text=True, timeout=2)
        outputs = json.loads(raw)
        return outputs if isinstance(outputs, list) else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def fullscreen_state(tree, workspaces, output=None):
    """Unknown topology/IPC fails closed for restacking and hover reveals."""
    if not isinstance(tree, dict):
        return None
    if tree_has_window_fullscreen(tree, mode=2):
        return True
    if not isinstance(workspaces, list):
        return None
    output = output or os.environ.get("DOCK_OUTPUT")
    if not output:
        output = output_for_monitor(dock_monitor(), current_outputs())
    if not output or not any(node.get("name") == output
                             for node in tree.get("nodes", [])
                             if isinstance(node, dict) and node.get("type") == "output"):
        return None
    return fullscreen_on_output(tree, output, workspaces)


def fullscreen_on_output(tree, output_index, workspaces=None):
    """Return whether an output or global fullscreen suppresses the dock."""
    if tree_has_window_fullscreen(tree, mode=2):
        return True
    outputs = [node for node in (tree.get("nodes") or [])
               if isinstance(node, dict)
               and node.get("type") == "output"
               and node.get("name") != "__i3"]
    # Integer selection remains for legacy fixture/API callers only. Runtime
    # callers resolve a physical GDK monitor to its output name first.
    if isinstance(output_index, int):
        selected = outputs[output_index] if 0 <= output_index < len(outputs) else None
    else:
        selected = next((output for output in outputs
                         if output.get("name") == output_index), None)
    if selected is None or workspaces is None:
        return False
    output_name = selected.get("name")
    visible_ids = {
        workspace.get("id")
        for workspace in workspaces
        if isinstance(workspace, dict)
        and workspace.get("output") == output_name
        and workspace.get("visible") is True
    }
    return tree_has_fullscreen_in_workspaces(selected, visible_ids)


def tree_has_fullscreen_in_workspaces(node, workspace_ids):
    if not isinstance(node, dict):
        return False
    if node.get("type") == "workspace":
        return node.get("id") in workspace_ids and tree_has_window_fullscreen(node)
    return any(tree_has_fullscreen_in_workspaces(child, workspace_ids)
               for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []))


def fullscreen_transition(previous, current):
    """Return the lifecycle action needed for a known fullscreen transition."""
    if current is None or current == previous:
        return None
    return "suppress" if current else "restore"


def sync_fullscreen(action):
    """Ask the lifecycle helper to apply a fullscreen transition."""
    script = os.environ.get("DOCK_FULLSCREEN_SCRIPT", FULLSCREEN_SCRIPT)
    try:
        return subprocess.run(
            [script, action],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def current_tree():
    try:
        raw = subprocess.check_output(["i3-msg", "-t", "get_tree"], text=True, timeout=2)
        tree = json.loads(raw)
        return tree if isinstance(tree, dict) else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def current_workspaces():
    try:
        raw = subprocess.check_output(["i3-msg", "-t", "get_workspaces"], text=True, timeout=2)
        workspaces = json.loads(raw)
        return workspaces if isinstance(workspaces, list) else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def current_windows(tree=None):
    if tree is None:
        tree = current_tree()
    return list(tree_windows(tree)) if tree else []


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
CLOSE_MENU = "; /home/boncuk/.config/eww/dock-menu.sh dismiss >/dev/null 2>&1 || true"


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


# Width model (px) mirroring eww.scss: .entry is 42 min-width + 2x2 padding +
# 2x2 margin, and :hover adds 6. Chrome is .dock-main margin 2x24 + padding
# 2x8 + border 2x2 plus .taskbar padding 2x2. Keep these in sync with eww.scss.
ENTRY_WIDTH = 50
HOVER_EXTRA = 6
DOCK_CHROME = 72
SCREEN_MARGIN = 48
FALLBACK_MAX_WIDTH = 1280
_MAX_WIDTH = None


def dock_max_width():
    """Widest dock window for monitor 0; cached until an output event."""
    global _MAX_WIDTH
    if _MAX_WIDTH is None:
        rect = (dock_monitor() or {}).get("rect") or {}
        width = rect.get("width")
        if not isinstance(width, int) or width <= SCREEN_MARGIN * 4:
            return FALLBACK_MAX_WIDTH  # Unknown monitor: do not cache it.
        _MAX_WIDTH = width - SCREEN_MARGIN
    return _MAX_WIDTH


def dock_literal(taskbar, count, max_width):
    """Content-sized pill; a fixed-width scroll only when it would overflow."""
    main = ('(box :class "dock-main" :space-evenly false :hexpand false'
            ' :halign "center" ' + taskbar + ')')
    if count * ENTRY_WIDTH + HOVER_EXTRA + DOCK_CHROME <= max_width:
        return main
    return ('(scroll :class "dock-scroll" :width ' + str(int(max_width)) +
            ' :hscroll true :vscroll false ' + main + ')')


def render(entries, index, tree=None, previous=None):
    pinned = load_pinned()
    windows = current_windows(tree)

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

    taskbar = '(box :class "taskbar" :space-evenly false ' + " ".join(result) + ')'
    literal = dock_literal(taskbar, len(result), dock_max_width())
    if literal != previous:
        print(literal, flush=True)
    return literal


def raise_docks(tree=None, workspaces=None):
    """Restack only with a freshly known, nonfullscreen target output."""
    tree = current_tree() if tree is None else tree
    workspaces = current_workspaces() if workspaces is None else workspaces
    if fullscreen_state(tree, workspaces) is not False:
        return 0
    return native_x11().raise_docks()


def i3_events():
    """Block on events, reconnect on EOF with capped exponential backoff."""
    delay = 0.1
    while True:
        subscription = None
        try:
            subscription = subprocess.Popen(
                ["i3-msg", "-t", "subscribe", "-m", '["window", "workspace", "output", "tick"]'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, close_fds=True,
            )
            assert subscription.stdout is not None
            # `i3-msg subscribe -m` emits monitor events, not the raw IPC
            # subscription acknowledgement. Read the stream directly: waiting
            # for an ack here discards the first real event and reconnects on
            # quiet desktops before any event arrives.
            for line in subscription.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                # dock-menu.sh sends `i3-msg -t send_tick dock-pins` after it
                # rewrites pinned-apps.json; i3 also sends a first=true tick
                # on subscribe, which carries no payload and is ignored.
                if event.get("payload") == PIN_TICK:
                    event = {"change": "pins"}
                if "change" in event:
                    delay = 0.1
                    yield event
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
        finally:
            if subscription is not None:
                subscription.terminate()
                try:
                    subscription.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    subscription.kill()
                    subscription.wait(timeout=1)
                if subscription.stdout is not None:
                    subscription.stdout.close()
        time.sleep(delay)
        delay = min(delay * 2, 2)


def listen(entries, index, previous=None):
    global _MAX_WIDTH
    for event in i3_events():
        # Output events carry change "unspecified"; window/workspace never do.
        if event.get("change") == "unspecified":
            _MAX_WIDTH = None
        tree = current_tree()
        workspaces = current_workspaces()
        current = fullscreen_state(tree, workspaces)
        # The detached helper acknowledges enqueueing, not application. Always
        # reconcile the latest desired state on events; never remember a lost
        # lock-contention request as already applied.
        if current is not None:
            sync_fullscreen("suppress" if current else "restore")
        if current is False:
            raise_docks()  # Fresh fullscreen check after helper IPC.
        if tree is not None:
            previous = render(entries, index, tree, previous=previous)


def main():
    global THEME_CHAIN
    if "--raise" in sys.argv:
        raise_docks()
        return
    if "--fullscreen" in sys.argv:
        tree = current_tree()
        workspaces = current_workspaces()
        state = fullscreen_state(tree, workspaces)
        print("unknown" if state is None else "true" if state else "false")
        return

    pinned = load_pinned(force=True)
    entries = [entry for path in desktop_files() if (entry := desktop_info(path))]
    index = build_index(entries)
    THEME_CHAIN = theme_chain(gtk_theme_name())
    # One walk for exactly the icons on screen right now (pinned + running);
    # renders afterwards are cache hits. Prefetching ALL .desktop icons would
    # mean thousands of -iname patterns per file visited (~27s), so scope it.
    needed = []
    tree = current_tree()
    windows = current_windows(tree)
    if "--listen" in sys.argv:
        workspaces = current_workspaces()
        current = fullscreen_state(tree, workspaces)
        if current is not None:
            sync_fullscreen("suppress" if current else "restore")
        if current is False:
            raise_docks()
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

    previous = render(entries, index, tree)
    if "--listen" in sys.argv:
        listen(entries, index, previous=previous)


if __name__ == "__main__":
    main()
