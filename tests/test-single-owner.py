#!/usr/bin/env python3
"""Focused Stage A ownership regressions; all Eww processes are local fakes."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class SingleOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dock-owner-")
        self.base = Path(self.tmp.name)
        self.config = self.base / "config"
        self.runtime = self.base / "runtime"
        self.bin = self.base / "bin"
        for directory in (self.config, self.runtime, self.bin):
            directory.mkdir(mode=0o700)
        self.socket_path = self.runtime / "fake-eww.sock"
        self.argv_log = self.base / "argv.jsonl"
        self.pid_file = self.base / "daemon.pid"
        self.fake_eww = self.bin / "eww"
        self.fake_server = self.bin / "fake-daemon.py"
        self.fake_server.write_text(
            """import json, os, signal, socket, sys
args = sys.argv[1:]
with open(os.environ['FAKE_ARGV_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')
if args[-1:] == ['daemon']:
    with open(os.environ['FAKE_PID_FILE'], 'w') as f: f.write(str(os.getpid()))
    with open(os.environ['FAKE_FD_FILE'], 'w') as f:
        f.write(json.dumps({str(n): os.readlink('/proc/self/fd/' + str(n)) for n in (7,8,9) if os.path.exists('/proc/self/fd/' + str(n))}))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(os.environ['FAKE_SOCKET']); s.listen(4)
    signal.signal(signal.SIGTERM, signal.SIG_IGN if os.environ.get('FAKE_IGNORE_TERM') else lambda *_: sys.exit(0))
    while True:
        conn, _ = s.accept(); conn.close()
if '--no-daemonize' not in args or '--config' not in args: sys.exit(41)
print('')
""",
            encoding="utf-8",
        )
        self.fake_eww.write_text(
            "#!/bin/bash\nexec -a eww python3 \"$FAKE_DAEMON_SCRIPT\" \"$@\"\n",
            encoding="utf-8",
        )
        self.fake_eww.chmod(0o700)
        self.env = os.environ.copy()
        self.env.update(
            DOCK_CONFIG_DIR=str(self.config),
            DOCK_RUNTIME_DIR=str(self.runtime),
            DOCK_EWW_BIN=str(self.fake_eww),
            DISPLAY=":77",
            FAKE_SOCKET=str(self.socket_path),
            FAKE_ARGV_LOG=str(self.argv_log),
            FAKE_PID_FILE=str(self.pid_file),
            FAKE_FD_FILE=str(self.base / "fds.json"),
            FAKE_DAEMON_SCRIPT=str(self.fake_server),
        )

    def tearDown(self):
        if self.pid_file.exists():
            pid = int(self.pid_file.read_text())
            try:
                args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
                if str(self.fake_server) in args:
                    os.kill(pid, signal.SIGKILL if self.env.get("FAKE_IGNORE_TERM") else signal.SIGTERM)
                    wait_for(lambda: not Path(f"/proc/{pid}").exists())
            except (OSError, ValueError):
                pass
        self.tmp.cleanup()

    def test_supervisor_reuses_one_foreground_owner_without_lock_fd_inheritance(self):
        supervisor = ROOT / "dock-daemon.sh"
        starts = [subprocess.Popen([str(supervisor), "ensure"], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
        for process in starts:
            self.addCleanup(self._reap, process)
        results = [p.wait(timeout=7) for p in starts]
        self.assertTrue(all(code == 0 for code in results), results)
        self.assertTrue(wait_for(lambda: self.pid_file.exists()))
        self.assertTrue(wait_for(lambda: self._owner() is not None))
        owner = self._owner()
        self.assertEqual(owner["config_dir"], str(self.config.resolve()))
        self.assertEqual(owner["display"], ":77")
        self.assertEqual(owner["pid"], int(self.pid_file.read_text()))
        self.assertIn("start_time", owner)
        self.assertEqual(owner["socket_path"], str(self.socket_path))
        self.assertEqual(json.loads((self.base / "fds.json").read_text()), {})
        daemon_starts = [json.loads(line) for line in self.argv_log.read_text().splitlines() if json.loads(line)[-1:] == ["daemon"]]
        self.assertEqual(len(daemon_starts), 1)

    def test_shared_client_boundary_is_explicit_and_non_spawning(self):
        command = '. "$1/dock-common.sh"; dock_eww get menu-open'
        result = subprocess.run(["sh", "-c", command, "sh", str(ROOT)], env=self.env, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.argv_log.read_text().splitlines()[-1])
        self.assertEqual(args[:3], ["--config", str(self.config.resolve()), "--no-daemonize"])
        self.assertEqual(args[3:], ["get", "menu-open"])

    def test_supervisor_shutdown_is_bounded_when_daemon_ignores_term(self):
        self.env["FAKE_IGNORE_TERM"] = "1"
        self.env["DOCK_OWNER_STOP_TIMEOUT"] = "0.1"
        process = subprocess.Popen([str(ROOT / "dock-daemon.sh"), "run"], env=self.env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._reap, process)
        self.assertTrue(wait_for(lambda: self._owner() is not None))
        process.terminate()
        process.wait(timeout=1.5)
        self.assertIsNone(self._owner())

    def test_held_unhealthy_owner_lock_fails_bounded_without_launch(self):
        self.env["DOCK_OWNER_WAIT_ATTEMPTS"] = "12"
        self.env["DOCK_OWNER_WAIT_DELAY"] = "0.05"
        paths = subprocess.check_output(["python3", str(ROOT / "dock-owner.py"), "--config-dir", str(self.config), "--runtime-dir", str(self.runtime), "paths"], text=True)
        lock_path = json.loads(paths)["lock_path"]
        (self.base / "lock-holder-ready").touch()
        holder = subprocess.Popen(["flock", lock_path, "sleep", "10"], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertTrue(wait_for(lambda: holder.poll() is None))
        time.sleep(0.05)
        supervisor = ROOT / "dock-daemon.sh"
        started = time.monotonic()
        result = subprocess.run([str(supervisor), "ensure"], env=self.env, capture_output=True, text=True, timeout=2)
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 2)
        self.assertFalse(self.pid_file.exists())
        holder.terminate()
        holder.wait(timeout=2)

    def _owner(self):
        path = ROOT / "dock-owner.py"
        spec = importlib.util.spec_from_file_location("dock_owner_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.dict(os.environ, DISPLAY=self.env["DISPLAY"]):
            return module.verified_owner(config_dir=str(self.config), runtime_dir=str(self.runtime))

    @staticmethod
    def _reap(process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
