#!/usr/bin/env python3
"""Narrow Stage B hover/fullscreen race fixtures; never touches live Eww."""
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HoverIntent(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dock-hover-")
        self.base = Path(self.temp.name)
        self.state = self.base / "state"
        self.runtime = self.base / "runtime"
        self.bin = self.base / "bin"
        self.state.mkdir()
        self.runtime.mkdir()
        self.runtime.chmod(0o700)
        self.bin.mkdir()
        (self.state / "ready").touch()
        (self.state / "windows").write_text("dock\n")
        self.eww = self.bin / "eww"
        self.eww.write_text("#!/bin/sh\n"
                            "while [ $# -gt 0 ]; do\n"
                            "  case $1 in active-windows|open|close|update) break ;; esac\n"
                            "  shift\n"
                            "done\n"
                            f"exec \"{ROOT / 'tests/fake-eww.sh'}\" \"$@\"\n")
        self.eww.chmod(0o755)
        self.app = self.base / "apps.py"
        self.app.write_text("import sys\nprint('false') if '--fullscreen' in sys.argv else None\n")
        self.env = dict(os.environ, DISPLAY="", DOCK_STATE_DIR=str(self.state),
                        DOCK_RUNTIME_DIR=str(self.runtime), DOCK_CONFIG_DIR=str(ROOT),
                        DOCK_EWW_BIN=str(self.eww),
                        DOCK_APP_SCRIPT=str(self.app), DOCK_SHOW_DETACHED="1",
                        DOCK_HIDE_DELAY="0.02", DOCK_CONFIRM_ATTEMPTS="2",
                        DOCK_CONFIRM_DELAY="0.005", DOCK_IPC_TIMEOUT="0.5",
                        DOCK_IPC_KILL_AFTER="0.1", DOCK_LOCK_TIMEOUT="0.3")

    def tearDown(self):
        self.temp.cleanup()

    def script(self, name, *args, **kwargs):
        return subprocess.run([str(ROOT / name), *args], env=self.env,
                              timeout=3, capture_output=True, **kwargs)

    def wait_file(self, path):
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.005)
        self.fail("fixture did not reach barrier: " + str(path))

    def wait_windows(self, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (self.state / "windows").read_text().splitlines() == expected:
                return
            time.sleep(0.01)
        self.assertEqual((self.state / "windows").read_text().splitlines(), expected)

    def delay_setsid(self):
        shim = self.bin / "setsid"
        shim.write_text("#!/bin/sh\n"
                        "touch \"$DOCK_STATE_DIR/setsid-entered\"\n"
                        "while [ ! -e \"$DOCK_STATE_DIR/release-setsid\" ]; do sleep 0.005; done\n"
                        "exec \"$@\"\n")
        shim.chmod(0o755)
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]

    def test_delayed_hide_worker_uses_leave_token_captured_before_detach(self):
        self.delay_setsid()
        process = subprocess.Popen([str(ROOT / "dock-schedule-hide.sh")], env=self.env)
        try:
            self.wait_file(self.state / "setsid-entered")
            self.script("dock-cancel-hide.sh")
            (self.state / "release-setsid").touch()
            time.sleep(0.18)
            self.wait_windows(["dock"])
            self.assertFalse((self.state / "open-count-dock_open_area").exists(),
                             "stale leave worker created the trigger after re-entry")
        finally:
            (self.state / "release-setsid").touch()
            process.wait(timeout=2)

    def test_show_published_during_lock_contention_is_handed_off(self):
        (self.state / "windows").write_text("dock_open_area\n")
        lock_path = self.runtime / "eww-dock-show.lock"
        lock_path.touch()
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen([str(ROOT / "dock-show.sh")], env=self.env)
            time.sleep(0.04)
            fcntl.flock(lock, fcntl.LOCK_UN)
            result = process.wait(timeout=2)
        self.assertEqual(result, 0)
        self.wait_windows(["dock"])

    def test_restore_during_pending_hide_does_not_cancel_hover_intent(self):
        self.env["DOCK_SHOW_DETACHED"] = "1"
        self.env["DOCK_FULLSCREEN_DETACHED"] = "1"
        self.env["DOCK_HIDE_DELAY"] = "0.15"
        (self.state / "eww-dock-fullscreen-request").write_text("1 suppress\n")
        hide = subprocess.Popen([str(ROOT / "dock-schedule-hide.sh")], env=self.env)
        try:
            time.sleep(0.03)
            result = self.script("dock-fullscreen.sh", "restore")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            hide.wait(timeout=2)
        finally:
            if hide.poll() is None:
                hide.kill()
                hide.wait()
        self.wait_windows(["dock_open_area"])

    def delay_mutation(self, command, window):
        self.eww.write_text("#!/bin/sh\n"
                            "while [ $# -gt 0 ]; do\n"
                            "  case $1 in active-windows|open|close|update) break ;; esac\n"
                            "  shift\n"
                            "done\n"
                            f'if [ "$1:$2" = "{command}:{window}" ]; then\n'
                            '  touch "$DOCK_STATE_DIR/mutation-entered"\n'
                            '  while [ ! -e "$DOCK_STATE_DIR/release-mutation" ]; do sleep 0.005; done\n'
                            'fi\n'
                            f'exec "{ROOT / "tests/fake-eww.sh"}" "$@"\n')

    def test_reentry_after_final_hide_check_restores_latest_hover(self):
        self.env["DOCK_HIDE_DETACHED"] = "1"
        self.delay_mutation("close", "dock")
        process = subprocess.Popen([str(ROOT / "dock-schedule-hide.sh")], env=self.env)
        try:
            self.wait_file(self.state / "mutation-entered")
            self.assertEqual(self.script("dock-cancel-hide.sh").returncode, 0)
        finally:
            (self.state / "release-mutation").touch()
            process.wait(timeout=2)
        self.wait_windows(["dock"])

    def test_trigger_leave_during_pending_open_does_not_leave_stale_dock(self):
        (self.state / "windows").write_text("dock_open_area\n")
        self.delay_mutation("open", "dock")
        process = subprocess.Popen([str(ROOT / "dock-show.sh")], env=self.env)
        try:
            self.wait_file(self.state / "mutation-entered")
            self.assertEqual(self.script("dock-cancel-hide.sh", "trigger-left").returncode, 0)
        finally:
            (self.state / "release-mutation").touch()
            process.wait(timeout=2)
        self.wait_windows(["dock_open_area"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
