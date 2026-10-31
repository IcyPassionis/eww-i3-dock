"""Small source-driven GTK3 harness, NOT an Eww end-to-end emulator.

Reads the real dock Yuck hierarchy and CSS; never executes callbacks/listeners.
Only the dock's widget/expression subset is supported (unknowns fail loudly).
Eww d87c2fdbfdc012e76d229e4e9ea3325bc0f23e89 primary source:
crates/eww/src/widgets/widget_definitions.rs: resolve_widget_attrs,
connect_first_map, build_gtk_literal, build_gtk_scrolledwindow.
The captured construction-time :visible map hook is intentionally replayed.
GTK uses OffscreenWindow only; no live Eww/i3 IPC or desktop manipulation.
"""
import json
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]


class Atom(str):
    pass


class Expr(str):
    pass


def parse(text):
    tokens = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
        elif char == ';':
            end = text.find('\n', i)
            i = len(text) if end < 0 else end + 1
        elif char in '()[]':
            tokens.append(char)
            i += 1
        elif char == '"':
            value, consumed = json.JSONDecoder().raw_decode(text[i:])
            tokens.append(value)
            i += consumed
        elif char == '{':
            end = i + 1
            quoted = False
            while end < len(text):
                if text[end] == '"' and text[end - 1] != '\\':
                    quoted = not quoted
                if text[end] == '}' and not quoted:
                    break
                end += 1
            if end == len(text):
                raise ValueError('Unclosed expression')
            tokens.append(Expr(text[i + 1:end].strip()))
            i = end + 1
        else:
            end = i + 1
            while end < len(text) and not text[end].isspace() and text[end] not in '()[]':
                end += 1
            tokens.append(Atom(text[i:end]))
            i = end
    cursor = 0

    def take():
        nonlocal cursor
        token = tokens[cursor]
        cursor += 1
        if type(token) is str and token in ('(', '['):
            closing = ')' if token == '(' else ']'
            result = []
            while not (type(tokens[cursor]) is str and tokens[cursor] == closing):
                result.append(take())
            cursor += 1
            return result
        return token

    result = []
    while cursor < len(tokens):
        result.append(take())
    return result


def parts(node):
    attrs, children = {}, []
    index = 1
    while index < len(node):
        item = node[index]
        if isinstance(item, Atom) and item.startswith(':'):
            attrs[item[1:]] = node[index + 1]
            index += 2
        else:
            children.append(item)
            index += 1
    return str(node[0]), attrs, children


def config():
    return parse((ROOT / 'eww.yuck').read_text())


def window_node(name):
    return next(node for node in config() if node[:2] == ['defwindow', name])


def initial_state():
    result = {}
    for node in config():
        if node[0] == 'defvar':
            result[str(node[1])] = {'true': True, 'false': False}[node[2]]
        elif node[:2] == ['deflisten', 'app-buttons']:
            result['app-buttons'] = parts([node[0], *node[2:]])[1]['initial']
    return result


def evaluate(value, state):
    if isinstance(value, Expr):
        match = re.fullmatch(r'([\w-]+)\s*!=\s*("(?:[^"\\]|\\.)*")', value)
        if match:
            return state[match[1]] != json.loads(match[2])
        match = re.fullmatch(r'([\w-]+)\s*\?\s*("[^"]*")\s*:\s*("[^"]*")', value)
        if match:
            return json.loads(match[2] if state[match[1]] else match[3])
        raise ValueError('Unsupported expression: ' + value)
    if isinstance(value, Atom):
        if value in state:
            return state[value]
        if value in ('true', 'false'):
            return value == 'true'
        try:
            return int(value)
        except ValueError:
            raise ValueError('Unsupported atom: ' + value) from None
    return value


Gtk = Gdk = GdkPixbuf = GLib = None
try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
    GTK_AVAILABLE, _ = Gtk.init_check([])
    GTK_SKIP = 'GTK3 has no display (OffscreenWindow still needs a GDK display)'
except (ImportError, ValueError) as error:
    GTK_AVAILABLE = False
    GTK_SKIP = 'optional PyGObject/GTK3 unavailable: ' + str(error)


def drain(seconds=0.25):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.002)


class Dock:
    def __init__(self, state=None, name='dock'):
        self.state = initial_state()
        if state:
            self.state.update(state)
        self.bindings = []
        self.widgets = []
        self.window = Gtk.OffscreenWindow()
        self.window.set_name(name)
        _, attrs, children = parts([window_node(name)[0], *window_node(name)[2:]])
        _, geometry, _ = parts(attrs['geometry'])
        self.width = int(geometry['width'].removesuffix('px'))
        self.height = int(geometry['height'].removesuffix('px'))
        self.window.set_default_size(self.width, self.height)
        self.window.add(self.build(children[-1]))
        self.window.show_all()  # Eww app.rs uses show_all as well.
        drain(0.05)

    def bind(self, value, setter):
        setter(evaluate(value, self.state))
        if isinstance(value, (Atom, Expr)):
            self.bindings.append((value, setter))

    def build(self, node):
        kind, attrs, children = parts(node)
        if kind == 'box':
            widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if attrs.get('orientation') == 'vertical' else Gtk.Orientation.HORIZONTAL)
            widget.set_homogeneous(evaluate(attrs.get('space-evenly', Atom('true')), self.state))
        elif kind == 'eventbox':
            widget = Gtk.EventBox()
        elif kind == 'revealer':
            widget = Gtk.Revealer()
            transitions = {'none': Gtk.RevealerTransitionType.NONE, 'slideup': Gtk.RevealerTransitionType.SLIDE_UP}
            widget.set_transition_type(transitions[attrs.get('transition', 'none')])
            widget.set_transition_duration(int(attrs.get('duration', '250ms').removesuffix('ms')))
            self.bind(attrs.get('reveal', Atom('false')), widget.set_reveal_child)
        elif kind == 'scroll':
            widget = Gtk.ScrolledWindow()
            widget.set_policy(Gtk.PolicyType.AUTOMATIC if evaluate(attrs.get('hscroll', Atom('true')), self.state) else Gtk.PolicyType.NEVER,
                              Gtk.PolicyType.AUTOMATIC if evaluate(attrs.get('vscroll', Atom('true')), self.state) else Gtk.PolicyType.NEVER)
        elif kind == 'literal':
            widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            def content(value):
                for child in widget.get_children():
                    child.destroy()
                if value:
                    child = self.build(parse(value)[0])
                    widget.add(child)
                    child.show_all()
            self.bind(attrs['content'], content)
        elif kind == 'button':
            widget = Gtk.Button()
        elif kind == 'image':
            widget = Gtk.Image()
            widget.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(attrs['path'], int(attrs.get('image-width', 32)), int(attrs.get('image-height', 32)), True))
        else:
            raise ValueError('Unsupported widget: ' + kind)
        self.widgets.append((widget, kind, attrs))
        for child in children:
            if isinstance(child, list):
                widget.add(self.build(child))
            elif kind == 'button':
                self.bind(child, widget.set_label)
            else:
                raise ValueError('Unexpected child: ' + str(child))
        for cls in attrs.get('class', '').split():
            widget.get_style_context().add_class(cls)
        aligns = {'center': Gtk.Align.CENTER, 'end': Gtk.Align.END, 'start': Gtk.Align.START, 'fill': Gtk.Align.FILL}
        for key in ('halign', 'valign'):
            if key in attrs:
                getattr(widget, 'set_' + key)(aligns[attrs[key]])
        for key in ('hexpand', 'vexpand', 'active'):
            self.bind(attrs.get(key, Atom('true' if key == 'active' else 'false')), getattr(widget, 'set_sensitive' if key == 'active' else 'set_' + key))
        if 'width' in attrs or 'height' in attrs:
            widget.set_size_request(int(attrs.get('width', -1)), int(attrs.get('height', -1)))
        if 'visible' in attrs:
            captured = evaluate(attrs['visible'], self.state)
            handler = [None]
            def first_map(target):
                target.disconnect(handler[0])
                target.set_visible(captured)
            handler[0] = widget.connect('map', first_map)
            self.bind(attrs['visible'], widget.set_visible)
        else:
            widget.show()
        return widget

    def update(self, **values):
        self.state.update(values)
        for value, setter in list(self.bindings):
            if any(re.search(r'(?<![\w-])' + re.escape(key) + r'(?![\w-])', str(value)) for key in values):
                setter(evaluate(value, self.state))

    def class_widgets(self, cls):
        return [widget for widget, _, attrs in self.widgets
                if cls in attrs.get('class', '').split() and widget.get_toplevel() == self.window]

    def close(self):
        self.window.destroy()
        drain(0.01)


def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_path(str(ROOT / 'eww.scss'))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


_DOCK_APPS = None


def dock_apps():
    """Load dock-apps.py once so literals use the real dock_literal wrapper."""
    global _DOCK_APPS
    if _DOCK_APPS is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location('dock_apps_ui', ROOT / 'dock-apps.py')
        assert spec is not None and spec.loader is not None
        _DOCK_APPS = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_DOCK_APPS)
    return _DOCK_APPS


def app_literal(count, max_width=1872):
    icon = '/usr/share/icons/Adwaita/symbolic/actions/view-app-grid-symbolic.svg'
    taskbar = '(box :class "taskbar" :space-evenly false ' + ' '.join(
        '(button :class "entry open" (image :class "icon" :path ' + json.dumps(icon) + ' :image-width 32 :image-height 32))'
        for _ in range(count)) + ')'
    return dock_apps().dock_literal(taskbar, count, max_width)
