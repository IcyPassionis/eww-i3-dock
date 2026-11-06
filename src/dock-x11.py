"""Native X11/GDK support, loaded lazily; no PyGObject dependency."""
import ctypes as C
import ctypes.util
import importlib.util
import json
import os


class Rectangle(C.Structure):
    _fields_ = [(field, C.c_int) for field in ("x", "y", "width", "height")]


def _function(library, name, arguments, result):
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = result
    return function


def monitor(index=0):
    """Read the same GDK monitor Eww selects, with connector + geometry."""
    try:
        gdk = C.CDLL(ctypes.util.find_library("gdk-3") or "libgdk-3.so.0")
        glib = C.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
        pointer, integer = C.c_void_p, C.c_int
        init = _function(gdk, "gdk_init_check", [pointer, pointer], integer)
        if not init(None, None):
            return None
        # Deliver already queued hotplug notifications on output events only.
        iterate = _function(glib, "g_main_context_iteration", [pointer, integer], integer)
        for _ in range(32):
            if not iterate(None, False):
                break
        display = _function(gdk, "gdk_display_get_default", [], pointer)()
        count = _function(gdk, "gdk_display_get_n_monitors", [pointer], integer)(display)
        if not 0 <= index < count:
            return None
        selected = _function(gdk, "gdk_display_get_monitor", [pointer, integer], pointer)(display, index)
        geometry = Rectangle()
        _function(gdk, "gdk_monitor_get_geometry", [pointer, C.POINTER(Rectangle)], None)(selected, C.byref(geometry))
        rect = {field: getattr(geometry, field) for field in ("x", "y", "width", "height")}
        # Screen's connector API is deprecated but available in GTK 3. Match
        # its geometry to Display's monitor, not its independent ordinal.
        screen = _function(gdk, "gdk_display_get_default_screen", [pointer], pointer)(display)
        if not screen:
            return {"name": None, "rect": rect}
        screen_count = _function(gdk, "gdk_screen_get_n_monitors", [pointer], integer)(screen)
        screen_geometry = _function(gdk, "gdk_screen_get_monitor_geometry", [pointer, integer, C.POINTER(Rectangle)], None)
        plug_name = _function(gdk, "gdk_screen_get_monitor_plug_name", [pointer, integer], pointer)
        free = _function(glib, "g_free", [pointer], None)
        names = []
        for number in range(screen_count):
            other = Rectangle()
            screen_geometry(screen, number, C.byref(other))
            if bytes(other) != bytes(geometry):
                continue
            name = plug_name(screen, number)
            if name:
                try:
                    names.append(C.string_at(name).decode("utf-8", errors="replace"))
                finally:
                    free(name)
        return {"name": names[0] if len(names) == 1 else None, "rect": rect}
    except (OSError, AttributeError):
        return None


class WindowAttributes(C.Structure):
    _fields_ = [
        ("x", C.c_int), ("y", C.c_int), ("width", C.c_int), ("height", C.c_int),
        ("border_width", C.c_int), ("depth", C.c_int), ("visual", C.c_void_p),
        ("root", C.c_ulong), ("klass", C.c_int), ("bit_gravity", C.c_int),
        ("win_gravity", C.c_int), ("backing_store", C.c_int),
        ("backing_planes", C.c_ulong), ("backing_pixel", C.c_ulong),
        ("save_under", C.c_int), ("colormap", C.c_ulong), ("map_installed", C.c_int),
        ("map_state", C.c_int), ("all_event_masks", C.c_long),
        ("your_event_mask", C.c_long), ("do_not_propagate_mask", C.c_long),
        ("override_redirect", C.c_int), ("screen", C.c_void_p),
    ]


DOCK_TITLES = {"Eww - dock", "Eww - dock_open_area"}


def verified_owner(config_dir=None, runtime_dir=None):
    """Load the shared owner verifier lazily; absence or errors fail closed."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dock-owner.py")
    try:
        spec = importlib.util.spec_from_file_location("dock_owner", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        owner = module.verified_owner(config_dir=config_dir, runtime_dir=runtime_dir, probe_socket=False)
        return owner if isinstance(owner, dict) else None
    except Exception:
        return None


def _xlib():
    return C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")


def _property_pid(xlib, display, xid, atom):
    """Read CARDINAL/32 as native unsigned longs (Xlib's LP64 ABI)."""
    pointer, window, atom_t = C.c_void_p, C.c_ulong, C.c_ulong
    get_property = _function(xlib, "XGetWindowProperty", [
        pointer, window, atom_t, C.c_long, C.c_long, C.c_int, atom_t,
        C.POINTER(atom_t), C.POINTER(C.c_int), C.POINTER(C.c_ulong),
        C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ubyte)),
    ], C.c_int)
    actual_type, actual_format = atom_t(), C.c_int()
    count, remaining = C.c_ulong(), C.c_ulong()
    data = C.POINTER(C.c_ubyte)()
    try:
        status = get_property(display, xid, atom, 0, 1, False, 6,
                              C.byref(actual_type), C.byref(actual_format),
                              C.byref(count), C.byref(remaining), C.byref(data))
        if status or actual_type.value != 6 or actual_format.value != 32 or count.value != 1 or remaining.value or not data:
            return None
        return int(C.cast(data, C.POINTER(C.c_ulong))[0])
    finally:
        if data:
            _function(xlib, "XFree", [pointer], C.c_int)(data)


def _scan(display, xlib):
    pointer, window = C.c_void_p, C.c_ulong
    root_window = _function(xlib, "XDefaultRootWindow", [pointer], window)
    query_tree = _function(xlib, "XQueryTree", [pointer, window, C.POINTER(window), C.POINTER(window), C.POINTER(C.POINTER(window)), C.POINTER(C.c_uint)], C.c_int)
    fetch_name = _function(xlib, "XFetchName", [pointer, window, C.POINTER(pointer)], C.c_int)
    attributes = _function(xlib, "XGetWindowAttributes", [pointer, window, C.POINTER(WindowAttributes)], C.c_int)
    free = _function(xlib, "XFree", [pointer], C.c_int)
    intern = _function(xlib, "XInternAtom", [pointer, C.c_char_p, C.c_int], C.c_ulong)
    pid_atom = intern(display, b"_NET_WM_PID", True)
    root, parent = window(), window()
    children, count = C.POINTER(window)(), C.c_uint()
    if not query_tree(display, root_window(display), C.byref(root), C.byref(parent), C.byref(children), C.byref(count)):
        return None
    try:
        ids = [int(children[index]) for index in range(count.value)]
    finally:
        if children:
            free(children)
    records = []
    for xid in ids:
        name = pointer()
        if not fetch_name(display, xid, C.byref(name)) or not name:
            continue
        try:
            title = C.string_at(name).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        finally:
            free(name)
        if title not in DOCK_TITLES:
            continue
        state = WindowAttributes()
        if not attributes(display, xid, C.byref(state)):
            continue
        records.append({"xid": xid, "title": title, "mapped": state.map_state == 2,
                        "geometry": {key: getattr(state, key) for key in ("x", "y", "width", "height")},
                        "pid": _property_pid(xlib, display, xid, pid_atom)})
    return records


def inventory():
    """Read exact dock root windows. Returns None when X11 scanning fails."""
    try:
        xlib = _xlib()
        pointer = C.c_void_p
        open_display = _function(xlib, "XOpenDisplay", [C.c_char_p], pointer)
        close_display = _function(xlib, "XCloseDisplay", [pointer], C.c_int)
        sync = _function(xlib, "XSync", [pointer, C.c_int], C.c_int)
        set_handler = _function(xlib, "XSetErrorHandler", [pointer], pointer)
    except (OSError, AttributeError):
        return None
    display = open_display(None)
    if not display:
        return None
    handler = C.CFUNCTYPE(C.c_int, pointer, pointer)(lambda _display, _error: 0)
    old_handler = set_handler(C.cast(handler, pointer))
    try:
        result = _scan(display, xlib)
        sync(display, False)  # Deliver/contain BadWindow from a raced close.
        return result
    except Exception:
        return None
    finally:
        close_display(display)
        set_handler(old_handler)


def _raise_xid(xid):
    try:
        xlib = _xlib()
        pointer, window = C.c_void_p, C.c_ulong
        opened = _function(xlib, "XOpenDisplay", [C.c_char_p], pointer)
        set_handler = _function(xlib, "XSetErrorHandler", [pointer], pointer)
        sync = _function(xlib, "XSync", [pointer, C.c_int], C.c_int)
        display = opened(None)
        if not display:
            return False
        handler = C.CFUNCTYPE(C.c_int, pointer, pointer)(lambda _display, _error: 0)
        old_handler = set_handler(C.cast(handler, pointer))
        try:
            _function(xlib, "XRaiseWindow", [pointer, window], C.c_int)(display, xid)
            sync(display, False)
            return True
        finally:
            _function(xlib, "XCloseDisplay", [pointer], C.c_int)(display)
            set_handler(old_handler)
    except (OSError, AttributeError):
        return False


def raise_docks(owner_pid=None):
    """Raise mapped, exact Eww dock root windows; never map or focus one.

    wm-ignore makes these override-redirect root children. XRaiseWindow,
    unlike an EWMH above request, actually restacks them above i3 frames.
    The caller MUST check target-output/global fullscreen immediately first.
    """
    if owner_pid is None:
        owner = verified_owner()
        owner_pid = owner.get("pid") if isinstance(owner, dict) else None
    pid = owner_pid
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return 0
    windows = inventory()
    if not isinstance(windows, list):
        return 0
    raised = 0
    for item in windows:
        if (item.get("title") in DOCK_TITLES and item.get("mapped") is True
                and item.get("pid") == pid and isinstance(item.get("xid"), int)):
            raised += bool(_raise_xid(item["xid"]))
    return raised


if __name__ == "__main__" and "--inventory" in os.sys.argv:
    print(json.dumps(inventory()))
