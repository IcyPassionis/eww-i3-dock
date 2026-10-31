#!/usr/bin/env python3
"""Offline dock runtime regressions: never connect to the live X display."""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dock_apps_runtime", ROOT / "dock-apps.py")
dock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dock)


def topology(fullscreen_output="DP-0"):
    names = ["HDMI-0", "DP-0"]  # Intentionally opposite GDK order.
    outputs, visible, nodes = [], [], []
    for index, name in enumerate(names):
        rect = {"x": index * 1920, "y": 0, "width": 1920, "height": 1080}
        outputs.append({"name": name, "active": True, "rect": rect})
        visible.append({"id": index + 1, "output": name, "visible": True})
        nodes.append({"type": "output", "name": name, "rect": rect, "nodes": [
            {"type": "workspace", "id": index + 1, "nodes": [
                {"window": index + 10, "fullscreen_mode": int(name == fullscreen_output)}]}]})
    return {"nodes": nodes}, visible, outputs


class PhysicalOutputTests(unittest.TestCase):
    def test_gdk_identity_not_i3_array_position_selects_fullscreen(self):
        tree, visible, outputs = topology()
        monitor = {"name": "DP-0", "rect": outputs[1]["rect"]}
        selected = dock.output_for_monitor(monitor, outputs)
        self.assertEqual(selected, "DP-0")
        self.assertTrue(dock.fullscreen_on_output(tree, selected, visible))
        tree, visible, outputs = topology("HDMI-0")
        self.assertFalse(dock.fullscreen_on_output(tree, selected, visible))

    def test_gdk_geometry_matches_when_connector_name_unavailable(self):
        tree, visible, outputs = topology()
        selected = dock.output_for_monitor({"rect": outputs[1]["rect"]}, list(reversed(outputs)))
        self.assertEqual(selected, "DP-0")
        self.assertTrue(dock.fullscreen_on_output(tree, selected, visible))
        outputs.append({"name": "mirror", "active": True, "rect": outputs[1]["rect"]})
        self.assertIsNone(dock.output_for_monitor({"rect": outputs[1]["rect"]}, outputs))

    def test_missing_monitor_resolution_is_unknown_not_output_zero(self):
        from unittest.mock import patch
        tree, visible, outputs = topology()
        with patch.object(dock, "dock_monitor", return_value=None), \
             patch.object(dock, "current_outputs", return_value=outputs):
            self.assertIsNone(dock.fullscreen_state(tree, visible))


class RuntimeIPCTests(unittest.TestCase):
    def test_tree_and_workspaces_timeout_return_unknown(self):
        import subprocess
        from unittest.mock import patch
        for query in (dock.current_tree, dock.current_workspaces):
            with self.subTest(query=query.__name__), patch.object(dock.subprocess, "check_output", side_effect=subprocess.TimeoutExpired("i3-msg", 2)) as request:
                self.assertIsNone(query())
                self.assertGreater(request.call_args.kwargs.get("timeout", 0), 0)
                self.assertLessEqual(request.call_args.kwargs["timeout"], 3)


    def test_fullscreen_helper_timeout_is_bounded_and_retryable(self):
        import subprocess
        from unittest.mock import patch
        with patch.object(dock.subprocess, "run", side_effect=subprocess.TimeoutExpired("helper", 2)) as request:
            self.assertIsNone(dock.sync_fullscreen("suppress"))
        self.assertGreater(request.call_args.kwargs.get("timeout", 0), 0)
        self.assertTrue(request.call_args.kwargs["close_fds"])


class EarlyCLITests(unittest.TestCase):
    def test_fullscreen_cli_uses_physical_monitor_before_icon_work(self):
        import contextlib
        import io
        from unittest.mock import patch
        tree, visible, outputs = topology()
        stream = io.StringIO()
        with patch.object(dock.sys, "argv", ["dock-apps.py", "--fullscreen"]), \
             patch.object(dock, "current_tree", return_value=tree), \
             patch.object(dock, "current_workspaces", return_value=visible), \
             patch.object(dock, "current_outputs", return_value=outputs), \
             patch.object(dock, "dock_monitor", return_value={"rect": outputs[1]["rect"]}), \
             patch.object(dock, "desktop_files", side_effect=AssertionError("CLI loaded desktops")), \
             contextlib.redirect_stdout(stream):
            dock.main()
        self.assertEqual(stream.getvalue(), "true\n")


    def test_raise_cli_is_guarded_and_does_not_load_icons(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        tree, visible, outputs = topology()
        for state in (True, None, False):
            with self.subTest(state=state):
                native = SimpleNamespace(raise_docks=Mock(return_value=2))
                with patch.object(dock.sys, "argv", ["dock-apps.py", "--raise"]), \
                     patch.object(dock, "current_tree", return_value=tree), \
                     patch.object(dock, "current_workspaces", return_value=visible), \
                     patch.object(dock, "fullscreen_state", return_value=state), \
                     patch.object(dock, "native_x11", return_value=native), \
                     patch.object(dock, "desktop_files", side_effect=AssertionError("CLI loaded desktops")):
                    dock.main()
                self.assertEqual(native.raise_docks.call_count, int(state is False))


    def test_nonlistener_render_does_not_change_desktop_lifecycle(self):
        from unittest.mock import patch
        with patch.object(dock.sys, "argv", ["dock-apps.py"]), \
             patch.object(dock, "load_pinned", return_value=[]), \
             patch.object(dock, "desktop_files", return_value=[]), \
             patch.object(dock, "gtk_theme_name", return_value="hicolor"), \
             patch.object(dock, "theme_chain", return_value=["hicolor"]), \
             patch.object(dock, "current_tree", return_value={"nodes": []}), \
             patch.object(dock, "current_windows", return_value=[]), \
             patch.object(dock, "current_workspaces", return_value=[]), \
             patch.object(dock, "fullscreen_state", return_value=False), \
             patch.object(dock, "sync_fullscreen") as sync, \
             patch.object(dock, "raise_docks") as restack, \
             patch.object(dock, "prefetch_icon_names"), \
             patch.object(dock, "render", return_value="literal") as render:
            dock.main()
        render.assert_called_once()
        sync.assert_not_called()
        restack.assert_not_called()


class NativeStackTests(unittest.TestCase):
    def test_only_mapped_exact_dock_titles_are_raised(self):
        import ctypes as C
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        native = dock.native_x11()  # Imports only; all native calls are mocked.
        titles = {11: "Eww - dock", 12: "Editor dock", 13: "Eww - dock_open_area",
                  14: "Eww - dock_open_area", 15: "Eww - volume-popup"}
        children = (C.c_ulong * len(titles))(*titles)
        buffers = [C.create_string_buffer(title.encode()) for title in titles.values()]
        names = dict(zip(titles, buffers))
        def query_tree(display, window, root, parent, result, count):
            count._obj.value = len(titles)
            C.cast(result, C.POINTER(C.POINTER(C.c_ulong)))[0] = C.cast(children, C.POINTER(C.c_ulong))
            return 1
        def fetch_name(display, window, result):
            result._obj.value = C.addressof(names[window])
            return 1
        def attributes(display, window, result):
            result._obj.map_state = 0 if window == 13 else 2
            return 1
        library = SimpleNamespace(
            XOpenDisplay=Mock(return_value=1), XDefaultRootWindow=Mock(return_value=10),
            XQueryTree=Mock(side_effect=query_tree), XFetchName=Mock(side_effect=fetch_name),
            XGetWindowAttributes=Mock(side_effect=attributes), XFree=Mock(),
            XRaiseWindow=Mock(), XSync=Mock(), XCloseDisplay=Mock(),
            XSetErrorHandler=Mock(return_value=None))
        with patch.object(native.C, "CDLL", return_value=library):
            self.assertEqual(native.raise_docks(), 2)
        self.assertEqual([call.args[1] for call in library.XRaiseWindow.call_args_list], [11, 14])
        library.XSync.assert_called()
        library.XCloseDisplay.assert_called_once_with(1)


    def test_native_monitor_handles_missing_screen_without_null_pointer_calls(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        native = dock.native_x11()
        rect = {"x": 1920, "y": 0, "width": 1920, "height": 1080}
        def geometry(monitor, result):
            for field, value in rect.items():
                setattr(result._obj, field, value)
        gdk = SimpleNamespace(
            gdk_init_check=Mock(return_value=1), gdk_display_get_default=Mock(return_value=10),
            gdk_display_get_n_monitors=Mock(return_value=2), gdk_display_get_monitor=Mock(return_value=20),
            gdk_monitor_get_geometry=Mock(side_effect=geometry),
            gdk_display_get_default_screen=Mock(return_value=None),
            gdk_screen_get_n_monitors=Mock(side_effect=AssertionError("null screen queried")))
        glib = SimpleNamespace(g_main_context_iteration=Mock(return_value=0))
        with patch.object(native.C, "CDLL", side_effect=[gdk, glib]):
            self.assertEqual(native.monitor(0), {"name": None, "rect": rect})
        gdk.gdk_display_get_monitor.assert_called_once_with(10, 0)


    def test_native_monitor_matches_connector_by_geometry_not_screen_order(self):
        import ctypes as C
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        native = dock.native_x11()
        rectangles = [{"x": 0, "y": 0, "width": 1920, "height": 1080},
                      {"x": 1920, "y": 0, "width": 1920, "height": 1080}]
        def populate(result, rect):
            for field, value in rect.items():
                setattr(result._obj, field, value)
        name = C.create_string_buffer(b"DP-0")
        gdk = SimpleNamespace(
            gdk_init_check=Mock(return_value=1), gdk_display_get_default=Mock(return_value=10),
            gdk_display_get_n_monitors=Mock(return_value=2), gdk_display_get_monitor=Mock(return_value=20),
            gdk_monitor_get_geometry=Mock(side_effect=lambda monitor, result: populate(result, rectangles[1])),
            gdk_display_get_default_screen=Mock(return_value=30),
            gdk_screen_get_n_monitors=Mock(return_value=2),
            gdk_screen_get_monitor_geometry=Mock(side_effect=lambda screen, index, result: populate(result, rectangles[index])),
            gdk_screen_get_monitor_plug_name=Mock(return_value=C.addressof(name)))
        glib = SimpleNamespace(g_main_context_iteration=Mock(return_value=0), g_free=Mock())
        with patch.object(native.C, "CDLL", side_effect=[gdk, glib]):
            self.assertEqual(native.monitor(0), {"name": "DP-0", "rect": rectangles[1]})
        gdk.gdk_screen_get_monitor_plug_name.assert_called_once_with(30, 1)
        glib.g_free.assert_called_once()


class LiteralTests(unittest.TestCase):
    def test_complete_literal_is_emitted_only_when_changed(self):
        import contextlib
        import io
        from unittest.mock import patch
        stream = io.StringIO()
        with patch.object(dock, "load_pinned", return_value=[]), \
             patch.object(dock, "current_windows", return_value=[]), \
             contextlib.redirect_stdout(stream):
            first = dock.render([], {}, {"nodes": []})
            same = dock.render([], {}, {"nodes": []}, previous=first)
            self.assertEqual(first, same)
            self.assertEqual(len(stream.getvalue().splitlines()), 1)
            with patch.object(dock, "load_pinned", return_value=[
                    {"name": "Pinned", "match": "pinned", "command": "launch"}]):
                changed = dock.render([], {}, {"nodes": []}, previous=same)
            self.assertNotEqual(first, changed)
        self.assertEqual(len(stream.getvalue().splitlines()), 2)
        self.assertIn(':tooltip "Pinned"', changed)


    def test_left_click_dismissal_uses_shared_lifecycle_boundary(self):
        literal = dock.button("Fixture", dock.LAUNCHER_ICON, "launch", "running")
        self.assertIn("dock-menu.sh dismiss", literal)
        self.assertNotIn("eww update", literal)


class ListenerTests(unittest.TestCase):
    def test_events_reconcile_latest_fullscreen_then_restack_without_rebuilding_literal(self):
        from unittest.mock import Mock, patch
        tree, visible, _ = topology()
        calls = []
        with patch.object(dock, "i3_events", return_value=iter([
                {"change": "focus"}, {"change": "focus"}, {"change": "fullscreen_mode"}])), \
             patch.object(dock, "current_tree", return_value=tree), \
             patch.object(dock, "current_workspaces", return_value=visible), \
             patch.object(dock, "fullscreen_state", side_effect=[False, False, True]), \
             patch.object(dock, "sync_fullscreen", side_effect=lambda action: calls.append(action)), \
             patch.object(dock, "raise_docks", side_effect=lambda: calls.append("raise")), \
             patch.object(dock, "render", return_value="literal") as render:
            dock.listen([], {}, previous="literal")
        self.assertEqual(calls, ["restore", "raise", "restore", "raise", "suppress"])
        self.assertEqual(render.call_count, 3)
        self.assertTrue(all(call.kwargs["previous"] == "literal" for call in render.call_args_list))


    def test_subscription_eof_reconnects_and_cleans_up_children(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        processes = [SimpleNamespace(stdout=io.StringIO(text), terminate=Mock(), wait=Mock(), kill=Mock())
                     for text in ('{"change":"new"}\n', '{"change":"focus"}\n')]
        import select
        with patch.object(dock.subprocess, "Popen", side_effect=processes) as spawn, \
             patch.object(select, "select", return_value=([processes[0].stdout], [], [])), \
             patch.object(dock.time, "sleep") as backoff:
            events = dock.i3_events()
            self.assertEqual(next(events), {"change": "new"})
            self.assertEqual(next(events), {"change": "focus"})
            events.close()
        self.assertEqual(spawn.call_count, 2)
        self.assertGreater(backoff.call_args.args[0], 0)
        self.assertLessEqual(backoff.call_args.args[0], 2)
        for process in processes:
            self.assertTrue(process.stdout.closed)
            self.assertGreater(process.wait.call_args.kwargs["timeout"], 0)


    def test_subscription_accepts_event_without_acknowledgement(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        process = SimpleNamespace(stdout=io.StringIO('{"change":"new"}\n'),
                                  terminate=Mock(), wait=Mock(), kill=Mock())
        with patch.object(dock.subprocess, "Popen", return_value=process):
            events = dock.i3_events()
            self.assertEqual(next(events), {"change": "new"})
            events.close()
        process.terminate.assert_called_once()


    def test_main_listen_uses_physical_reconciliation_and_persistent_listener(self):
        from unittest.mock import patch
        tree, visible, outputs = topology()
        with patch.object(dock.sys, "argv", ["dock-apps.py", "--listen"]), \
             patch.object(dock, "load_pinned", return_value=[]), \
             patch.object(dock, "desktop_files", return_value=[]), \
             patch.object(dock, "gtk_theme_name", return_value="hicolor"), \
             patch.object(dock, "theme_chain", return_value=["hicolor"]), \
             patch.object(dock, "current_tree", return_value=tree), \
             patch.object(dock, "current_windows", return_value=[]), \
             patch.object(dock, "current_workspaces", return_value=visible), \
             patch.object(dock, "current_outputs", return_value=outputs), \
             patch.object(dock, "dock_monitor", return_value={"name": "DP-0"}), \
             patch.object(dock, "sync_fullscreen") as sync, \
             patch.object(dock, "raise_docks") as restack, \
             patch.object(dock, "prefetch_icon_names") as prefetch, \
             patch.object(dock, "render", return_value="literal"), \
             patch.object(dock, "listen") as listen, \
             patch.object(dock.subprocess, "Popen", side_effect=AssertionError("old inline listener")):
            dock.main()
        sync.assert_called_once_with("suppress")
        restack.assert_not_called()
        prefetch.assert_called_once_with([])
        listen.assert_called_once_with([], {}, previous="literal")


    def test_real_isolated_subscription_pipe_reconnects(self):
        import os
        import tempfile
        import textwrap
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
            root = pathlib.Path(directory)
            fake = root / "i3-msg"
            fake.write_text("#!" + dock.sys.executable + "\n" + textwrap.dedent('''\
                import os, pathlib, signal
                marker = pathlib.Path(os.environ["DOCK_TEST_CONNECTED"])
                if not marker.exists():
                    marker.touch()
                else:
                    print('{"change":"new"}', flush=True)
                    print('{"change":"focus"}', flush=True)
                    signal.pause()
                '''))
            fake.chmod(0o755)
            with patch.dict(os.environ, {"PATH": directory, "DOCK_TEST_CONNECTED": str(root / "connected")}):
                events = dock.i3_events()
                try:
                    self.assertEqual(next(events), {"change": "new"})
                    self.assertEqual(next(events), {"change": "focus"})
                finally:
                    events.close()


if __name__ == "__main__":
    unittest.main()
