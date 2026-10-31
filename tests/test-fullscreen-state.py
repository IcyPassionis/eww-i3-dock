#!/usr/bin/env python3
import importlib.util
import os
import pathlib
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dock_apps", ROOT / "dock-apps.py")
dock_apps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dock_apps)


def output(name, *children):
    return {
        "type": "output",
        "name": name,
        "fullscreen_mode": 0,
        "nodes": list(children),
        "floating_nodes": [],
    }


def workspace(workspace_id=456, *children, fullscreen_mode=0):
    return {
        "type": "workspace",
        "id": workspace_id,
        "fullscreen_mode": fullscreen_mode,
        "nodes": list(children),
        "floating_nodes": [],
    }


def window(fullscreen_mode=0):
    return {
        "type": "con",
        "window": 123,
        "fullscreen_mode": fullscreen_mode,
        "nodes": [],
        "floating_nodes": [],
    }


class FullscreenStateTests(unittest.TestCase):
    def test_detects_fullscreen_nested_on_dock_output(self):
        visible = [{"id": 456, "output": "DP-1", "visible": True}]
        tree = {
            "type": "root",
            "nodes": [
                output("DP-1", workspace(456, window(fullscreen_mode=1))),
                output("HDMI-1", workspace(789, window())),
            ],
        }

        self.assertTrue(dock_apps.fullscreen_on_output(tree, 0, visible))

    def test_ignores_fullscreen_on_another_output(self):
        visible = [{"id": 456, "output": "DP-1", "visible": True}]
        tree = {
            "type": "root",
            "nodes": [
                output("DP-1", workspace(456, window())),
                output("HDMI-1", workspace(789, window(fullscreen_mode=1))),
            ],
        }

        self.assertFalse(dock_apps.fullscreen_on_output(tree, 0, visible))

    def test_detects_global_fullscreen_on_any_output(self):
        tree = {
            "type": "root",
            "nodes": [
                output("DP-1", workspace(456, window())),
                output("HDMI-1", workspace(789, window(fullscreen_mode=2))),
            ],
        }

        self.assertTrue(dock_apps.fullscreen_on_output(tree, 0))

    def test_ignores_workspace_fullscreen_marker_without_fullscreen_window(self):
        tree = {
            "type": "root",
            "nodes": [output("DP-1", workspace(456, window(), fullscreen_mode=1))],
        }

        self.assertFalse(dock_apps.fullscreen_on_output(
            tree, 0, [{"id": 456, "output": "DP-1", "visible": True}]))

    def test_missing_output_is_not_fullscreen(self):
        tree = {"type": "root", "nodes": [output("DP-1", workspace(456, window()))]}

        self.assertFalse(dock_apps.fullscreen_on_output(
            tree, 1, [{"id": 456, "output": "DP-1", "visible": True}]))

    def test_ignores_fullscreen_on_hidden_workspace_same_output(self):
        visible = [{"id": 456, "output": "DP-1", "visible": True}]
        tree = {
            "type": "root",
            "nodes": [output(
                "DP-1",
                workspace(456, window()),
                workspace(999, window(fullscreen_mode=1)),
            )],
        }

        self.assertFalse(dock_apps.fullscreen_on_output(tree, 0, visible))

    def test_fullscreen_transition_enters_suppression(self):
        self.assertEqual(dock_apps.fullscreen_transition(False, True), "suppress")

    def test_fullscreen_transition_restores_after_exit(self):
        self.assertEqual(dock_apps.fullscreen_transition(True, False), "restore")

    def test_fullscreen_transition_ignores_unchanged_or_unknown_state(self):
        self.assertIsNone(dock_apps.fullscreen_transition(False, False))
        self.assertIsNone(dock_apps.fullscreen_transition(True, None))

    def test_sync_fullscreen_invokes_lifecycle_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = pathlib.Path(directory) / "helper.sh"
            received = pathlib.Path(directory) / "received"
            helper.write_text(
                "#!/bin/sh\nprintf '%s' \"$1\" > \"$SYNC_TARGET\"\n",
                encoding="utf-8",
            )
            helper.chmod(0o755)
            old_script = os.environ.get("DOCK_FULLSCREEN_SCRIPT")
            old_target = os.environ.get("SYNC_TARGET")
            os.environ["DOCK_FULLSCREEN_SCRIPT"] = str(helper)
            os.environ["SYNC_TARGET"] = str(received)
            try:
                dock_apps.sync_fullscreen("suppress")
                for _ in range(20):
                    if received.exists():
                        break
                    time.sleep(0.01)
                self.assertEqual(received.read_text(encoding="utf-8"), "suppress")
            finally:
                if old_script is None:
                    os.environ.pop("DOCK_FULLSCREEN_SCRIPT", None)
                else:
                    os.environ["DOCK_FULLSCREEN_SCRIPT"] = old_script
                if old_target is None:
                    os.environ.pop("SYNC_TARGET", None)
                else:
                    os.environ["SYNC_TARGET"] = old_target


if __name__ == "__main__":
    unittest.main()
