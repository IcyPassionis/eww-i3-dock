#!/usr/bin/env python3
"""Run: /usr/bin/python3 -m unittest discover -s tests -p 'test_luna_ui.py' -v.

Optional GTK3/PyGObject tests skip explicitly when dependencies/display are
missing. Uses actual Yuck/CSS with GTK OffscreenWindow, not live dock windows.
The harness models only the pinned Eww widget/property seam, not Eww IPC.
"""
import unittest

from luna_ui_gtk import (Dock, GTK_AVAILABLE, GTK_SKIP, app_literal, drain,
                         load_css, config, parts, window_node, initial_state,
                         evaluate, Atom, Expr, Gtk)


class DockConfigTests(unittest.TestCase):
    def test_trigger_keeps_single_listener_referenced_and_sensitive(self):
        trigger = window_node('dock_open_area')[-1]
        kind, attrs, _ = parts(trigger)
        self.assertEqual(kind, 'eventbox')
        active = attrs.get('active')
        self.assertIsInstance(active, (Atom, Expr), 'Trigger must retain a reactive listener dependency')
        self.assertIn('app-buttons', str(active), 'Hidden dock would stop the event-driven listener')
        self.assertTrue(evaluate(active, initial_state()), 'Initial (box) must accept edge hover')
        self.assertTrue(evaluate(active, {'app-buttons': app_literal(30)}))
        listeners = [node for node in config() if node[:2] == ['deflisten', 'app-buttons']]
        self.assertEqual(len(listeners), 1)
        self.assertIn('--listen', listeners[0][-1])

    def test_window_contract_and_menu_animation_are_preserved(self):
        widths = {'dock': '1px', 'dock_open_area': '760px'}
        for name in ('dock', 'dock_open_area'):
            _, attrs, _ = parts([window_node(name)[0], *window_node(name)[2:]])
            self.assertEqual(parts(attrs['geometry'])[1]['width'], widths[name])
            self.assertEqual(attrs['windowtype'], 'dock')
            self.assertEqual(attrs['wm-ignore'], 'true')
            self.assertNotIn('reserve', attrs)
        _, dock_attrs, _ = parts([window_node('dock')[0], *window_node('dock')[2:]])
        self.assertEqual(dock_attrs['resizable'], 'false', 'Dock must track content width')
        def descendants(node):
            yield node
            for child in parts(node)[2]:
                if isinstance(child, list):
                    yield from descendants(child)
        menu = next(parts(node)[1] for node in descendants(window_node('dock')[-1])
                    if parts(node)[1].get('reveal') == 'menu-open')
        self.assertEqual(menu['transition'], 'slideup')
        self.assertEqual(menu['duration'], '150ms')


@unittest.skipUnless(GTK_AVAILABLE, GTK_SKIP)
class DockGtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_css()

    def test_prefetch_delay_never_paints_empty_chrome(self):
        dock = Dock()
        try:
            # Hold the actual deflisten initial value throughout a cold prefetch.
            drain(0.3)
            self.assertFalse([w for w in dock.class_widgets('dock-main') if w.get_mapped()],
                             'Empty initial literal paints colored chrome')
            dock.update(**{'app-buttons': app_literal(6)})
            drain()
            main = dock.class_widgets('dock-main')[0]
            self.assertTrue(main.get_mapped(), 'Ready literal must show the bar')
            self.assertEqual(len(dock.class_widgets('entry')), 6)
        finally:
            dock.close()

    def test_width_follows_content_capped_at_monitor_and_entries_accessible(self):
        cap = 1872
        dock = Dock({'app-buttons': app_literal(6, cap)})
        try:
            drain()
            for count in (1, 6, 20, 36, 40, 6):
                dock.update(**{'app-buttons': app_literal(count, cap)})
                drain()
                entries = dock.class_widgets('entry')
                self.assertEqual(len(entries), count)
                width = dock.window.get_allocated_width()
                self.assertLessEqual(width, cap, 'Dock must never exceed the monitor cap')
                scrolls = [widget for widget, kind, _ in dock.widgets
                           if kind == 'scroll' and widget.get_toplevel() == dock.window]
                if count <= 20:
                    self.assertFalse(scrolls, 'Fitting taskbar must not be scroll-wrapped')
                    self.assertLess(width, 1100, 'Window must shrink to its content')
                    if count == 6:
                        self.assertLess(width, 500, 'Few icons must keep a small dock')
                    continue
                self.assertEqual(width, cap, 'Overflowing dock fills exactly the cap')
                scroll = scrolls[0]
                self.assertEqual(scroll.get_policy(), (Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER))
                adjustment = scroll.get_hadjustment()
                # Traverse the real adjustment range and check each actual child
                # can be wholly visible, not merely present in a clipped literal.
                for entry in entries:
                    x, _ = entry.translate_coordinates(scroll, 0, 0)
                    desired = adjustment.get_value() + x - 24
                    end = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
                    adjustment.set_value(max(adjustment.get_lower(), min(end, desired)))
                    drain(0.02)
                    x, y = entry.translate_coordinates(scroll, 0, 0)
                    self.assertGreaterEqual(x, 0)
                    self.assertLessEqual(x + entry.get_allocated_width(), scroll.get_allocated_width())
                    self.assertGreaterEqual(y, 0)
                entries[-1].set_state_flags(Gtk.StateFlags.PRELIGHT, False)
                drain()
                self.assertEqual(dock.window.get_allocated_width(), cap, 'Hover growth must remain bounded')
                image = entries[-1].get_child()
                _, y = image.translate_coordinates(scroll, 0, 0)
                self.assertGreaterEqual(y, 0, 'Hovered icon must not be clipped at the top')
                self.assertLessEqual(y + image.get_allocated_height(), scroll.get_allocated_height())
                # Eww exposes no overlay-scrolling attribute. Check GTK's other
                # system setting too, without changing any desktop-wide settings.
                scroll.set_overlay_scrolling(False)
                drain()
                self.assertEqual(dock.window.get_size(), (cap, 82),
                                 'Nonoverlay scrollbar must fit the original dock height')
                self.assertGreaterEqual(scroll.get_hscrollbar().get_allocated_height(), 4)
        finally:
            dock.close()

    def test_first_click_context_rows_and_first_workspace_toggle(self):
        cases = [(True, True, True), (True, False, True),
                 (False, True, True), (False, True, False)]
        for pinned, running, desktop in cases:
            for context_first in (False, True):
                for reopen in range(2):
                    with self.subTest(pinned=pinned, running=running, desktop=desktop,
                                      context_first=context_first, reopen=reopen):
                        dock = Dock({'app-buttons': app_literal(6)})
                        try:
                            flags = {'menu-pinned': pinned, 'menu-running': running,
                                     'menu-has-desktop': desktop}
                            if context_first:
                                dock.update(**flags)
                                dock.update(**{'menu-open': True})
                            else:
                                dock.update(**{'menu-open': True})
                                dock.update(**flags)
                            drain()
                            rows = {attrs.get('onclick', '').split()[-1]: widget
                                    for widget, kind, attrs in dock.widgets
                                    if kind == 'button' and 'ctx-item' in attrs.get('class', '').split()}
                            pin = rows['pin']
                            move = next(widget for widget, kind, attrs in dock.widgets
                                        if kind == 'button' and attrs.get('onclick', '').endswith('dock-move-toggle.sh'))
                            self.assertEqual(pin.get_mapped(), desktop, 'Pin/Unpin missing on FIRST click')
                            self.assertEqual(move.get_mapped(), running, 'Move missing on FIRST click')
                            self.assertTrue(rows['closeopen'].get_mapped())
                            self.assertEqual(pin.get_label(), 'Unpin from dock' if pinned else 'Pin to dock')
                            self.assertEqual(rows['closeopen'].get_label(), 'Close window' if running else 'Open')
                            first = [(widget.get_mapped(), widget.get_allocated_height())
                                     for widget in (pin, move, rows['closeopen'])]
                            # Identical second invocation must not repair a broken first layout.
                            dock.update(**{'menu-open': True, **flags})
                            drain()
                            self.assertEqual(first, [(widget.get_mapped(), widget.get_allocated_height())
                                                     for widget in (pin, move, rows['closeopen'])])
                            dock.update(**{'menu-move-open': True})
                            drain()
                            workspaces = dock.class_widgets('ws-item')
                            self.assertEqual(len(workspaces), 10)
                            self.assertTrue(all(widget.get_mapped() == running for widget in workspaces),
                                            'Workspace strip missing on FIRST submenu toggle')
                            dock.update(**{'menu-open': False, 'menu-move-open': False,
                                           'menu-running': False, 'menu-has-desktop': False})
                            drain()
                            dock.update(**flags)
                            dock.update(**{'menu-open': True, 'menu-move-open': True})
                            drain()
                            self.assertEqual(pin.get_mapped(), desktop)
                            self.assertEqual(move.get_mapped(), running)
                            self.assertTrue(all(widget.get_mapped() == running for widget in workspaces),
                                            'Submenu must survive hide/reset/reopen')
                        finally:
                            dock.close()


if __name__ == '__main__':
    unittest.main()
