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
  DOCK_MENU_DETACHED=1 setsid "$0" "$@" 8>&- 9>&- >/dev/null 2>&1 < /dev/null &
  exit 0
fi
. "$(dirname -- "$0")/dock-common.sh"
export DOCK_PINNED_FILE=${DOCK_PINNED_FILE:-$DOCK_SCRIPT_DIR/pinned-apps.json}
dock_run python3 - "$@" <<'PYEOF' >/dev/null 2>&1
import configparser, fcntl, json, os, re, stat, subprocess, sys, tempfile, time

CTX = os.path.join(os.environ["DOCK_RUNTIME_DIR"], "eww-dock-menu.json")
PINNED = os.environ["DOCK_PINNED_FILE"]
# Sidecar lock: save_pinned replaces PINNED, so its inode is not a stable lock.
PIN_LOCK = PINNED + ".lock"
# Below dock_run's 1s budget, so an abort is ours rather than a SIGKILL.
PIN_LOCK_TIMEOUT = 0.5
IPC_TIMEOUT = float(os.environ.get("DOCK_IPC_TIMEOUT", "1"))
APP_DIRS = (
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
)

action = sys.argv[1] if len(sys.argv) > 1 else ""
target = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    with open(CTX, encoding="utf-8") as file:
        ctx = json.load(file)
except (OSError, json.JSONDecodeError):
    ctx = {}


def i3_command(command):
    try:
        subprocess.run([os.environ.get("DOCK_I3_BIN", "i3-msg"), command],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, timeout=IPC_TIMEOUT, close_fds=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def i3_pin_tick():
    # Wake dock-apps.py --listen: it only re-renders on i3 events, so without
    # this a pin/unpin would stay invisible until the next window event.
    try:
        subprocess.run([os.environ.get("DOCK_I3_BIN", "i3-msg"), "-t", "send_tick", "dock-pins"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, timeout=IPC_TIMEOUT, close_fds=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


class PinAbort(Exception):
    # Stops a pin action before any write; the menu still dismisses.
    pass


def load_pinned():
    try:
        with open(PINNED, encoding="utf-8") as file:
            items = json.load(file)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        # ValueError covers both JSONDecodeError and UnicodeDecodeError.
        raise PinAbort()
    # Anything but a list of objects is not ours to rewrite: an empty write
    # here would silently discard every hand-pinned entry.
    if not isinstance(items, list) or not all(isinstance(entry, dict) for entry in items):
        raise PinAbort()
    return items


def save_pinned(items):
    try:
        mode = stat.S_IMODE(os.stat(PINNED).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PINNED) or ".", prefix=".pinned-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(items, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, PINNED)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    i3_pin_tick()


def stem_of(entry):
    return (entry.get("desktop") or "").removesuffix(".desktop").lower()


def find_desktop_file(stem):
    wanted = stem.lower() + ".desktop"
    for app_dir in APP_DIRS:
        try:
            names = os.listdir(app_dir)
        except OSError:
            continue
        for name in sorted(names):
            if name.lower() == wanted:
                return os.path.join(app_dir, name)
    return None


def toggle_pin():
    stem = (ctx.get("desktop") or "").lower()
    if not stem:
        return
    items = load_pinned()
    if any(stem_of(entry) == stem for entry in items):
        save_pinned([entry for entry in items if stem_of(entry) != stem])
        return
    cls = ctx.get("class") or ""
    desktop_file = find_desktop_file(stem)
    if desktop_file is None:
        return
    # Keep the file's real case: Flatpak ids (dev.zed.Zed) are case-sensitive
    # for gtk-launch, while the dock context only carries a lowercased stem.
    stem = os.path.basename(desktop_file)[:-len(".desktop")]
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(desktop_file, encoding="utf-8")
        name = parser["Desktop Entry"].get("Name", stem)
    except (KeyError, configparser.Error, OSError, UnicodeDecodeError):
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
        i3_command(f"[con_id={con}] kill")
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
    i3_command(f"[con_id={con}] move container to workspace number {number}")


def locked_toggle_pin():
    # Hold the lock across load and save so a double click cannot interleave.
    # Bounded: a held lock aborts instead of queueing behind a stuck writer.
    with open(PIN_LOCK, "a") as lock:
        deadline = time.monotonic() + PIN_LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PinAbort()
                time.sleep(0.02)
        toggle_pin()


if action == "pin":
    try:
        locked_toggle_pin()
    except (PinAbort, OSError):
        pass
elif action == "closeopen":
    close_or_open()
elif action == "moveto":
    move_to_workspace(target)
PYEOF
# Always dismiss, including if the action helper exceeds its deadline.
action_result=$?
dock_eww update menu-open=false menu-move-open=false >/dev/null 2>&1 || exit 1
exit "$action_result"
