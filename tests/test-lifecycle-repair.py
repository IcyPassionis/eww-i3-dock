#!/usr/bin/env python3
"""Isolated lifecycle regressions. No live Eww/i3 invocation."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LifecycleRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="luna-lifecycle-", dir=os.environ.get("TMPDIR"))
        self.base = Path(self.tmp.name)
        self.state = self.base / "state"
        self.runtime = self.base / "runtime"
        self.state.mkdir()
        self.runtime.mkdir()
        (self.state / "ready").touch()
        (self.state / "windows").write_text("dock_open_area\n")
        self.app = self.base / "apps.py"
        self.app.write_text("import sys\nprint('false') if '--fullscreen' in sys.argv else None\n")
        self.env = dict(os.environ, DOCK_RUNTIME_DIR=str(self.runtime), DOCK_STATE_DIR=str(self.state),
                        DOCK_EWW_BIN=str(ROOT / "tests/fake-eww.sh"), DOCK_APP_SCRIPT=str(self.app),
                        DOCK_SHOW_DETACHED="1", DOCK_HIDE_DETACHED="1", DOCK_FULLSCREEN_DETACHED="1",
                        DOCK_CTX_DETACHED="1", DOCK_MENU_DETACHED="1", DOCK_MOVE_DETACHED="1",
                        DOCK_CONFIRM_ATTEMPTS="2", DOCK_CONFIRM_DELAY="0.01", DOCK_HIDE_DELAY="0.01",
                        DOCK_IPC_TIMEOUT="0.1", DOCK_IPC_KILL_AFTER="0.05", DOCK_LOCK_TIMEOUT="0.2")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, script, *args, **kw):
        return subprocess.run([str(ROOT / script), *args], env=self.env, timeout=4, capture_output=True, **kw)

    def wrapper(self, prefix):
        path = self.base / "eww-wrapper.sh"
        path.write_text('#!/bin/sh\n' + prefix + '\nexec "' + str(ROOT / 'tests/fake-eww.sh') + '" "$@"\n')
        path.chmod(0o755)
        self.env['DOCK_EWW_BIN'] = str(path)

    def isolated_script(self, name):
        # Legacy hardcoded /tmp paths are redirected away from the live dock.
        legacy = self.base / 'legacy'
        legacy.mkdir(exist_ok=True)
        path = self.base / name
        path.write_text((ROOT / name).read_text().replace('/tmp/', str(legacy) + '/'))
        path.chmod(0o755)
        helper = self.base / 'dock-common.sh'
        if not helper.exists():
            helper.symlink_to(ROOT / 'dock-common.sh')
        return path

    def test_pending_fullscreen_intent_wins_inflight_stale_query(self):
        self.env['DOCK_IPC_TIMEOUT'] = '1'
        self.app.write_text("""import os, sys, time
from pathlib import Path
state = Path(os.environ['DOCK_STATE_DIR'])
if '--fullscreen' in sys.argv:
    (state / 'query-entered').touch()
    for _ in range(100):
        if (state / 'release-query').exists():
            break
        time.sleep(0.01)
    print('false')
""")
        process = subprocess.Popen([str(ROOT / 'dock-show.sh')], env=self.env)
        try:
            self.wait_file(self.state / 'query-entered')
            self.env['DOCK_LOCK_TIMEOUT'] = '0.02'
            self.run_script('dock-fullscreen.sh', 'suppress')
        finally:
            (self.state / 'release-query').touch()
            process.wait(timeout=3)
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock_open_area'])
        self.assertTrue((self.runtime / 'eww-dock-fullscreen-request').read_text().endswith(' suppress\n'))

    def test_failed_active_query_never_recreates_a_window(self):
        self.wrapper('if [ "$1" = active-windows ]; then sleep 0.6; fi')
        for script in ['dock-show.sh', 'dock-schedule-hide.sh']:
            with self.subTest(script=script):
                (self.state / 'windows').write_text('dock\n')
                for counter in self.state.glob('open-count-*'):
                    counter.unlink()
                result = self.run_script(script)
                self.assertNotEqual(result.returncode, 0, 'unavailable state must not claim success')
                self.assertEqual(list(self.state.glob('open-count-*')), [],
                                 'a failed active-window guard was treated as an absent window')
                self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock'])

    def test_hover_recovers_from_stale_suppression_after_fullscreen_exit(self):
        (self.runtime / 'eww-dock-fullscreen-request').write_text('1 suppress\n')
        result = self.run_script('dock-show.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock'],
                         'confirmed fullscreen exit could not recover via hover')

    def test_installer_preserves_runtime_pins_and_installs_helpers(self):
        self.env['HOME'] = str(self.base)
        destination = self.base / '.config' / 'eww'
        destination.mkdir(parents=True)
        pins = destination / 'pinned-apps.json'
        pins.write_text('[{"name":"personal choice"}]\n')
        self.assertEqual(self.run_script('install.sh').returncode, 0)
        self.assertEqual(pins.read_text(), '[{"name":"personal choice"}]\n',
                         'normal installation overwrote personal pins')
        self.assertTrue((destination / 'dock-common.sh').exists())
        if (ROOT / 'dock-x11.py').exists():
            self.assertTrue((destination / 'dock-x11.py').exists())
        self.assertEqual(self.run_script('install.sh', '--replace-pins').returncode, 0)
        self.assertEqual(pins.read_text(), (ROOT / 'pinned-apps.json').read_text())

    def test_submenu_toggle_uses_configured_bounded_eww(self):
        self.wrapper('''if [ "$1" = get ]; then echo true; exit 0; fi
echo "$*" > "$DOCK_STATE_DIR/update"''')
        (self.base / 'eww').symlink_to(ROOT / 'tests/fake-eww.sh')
        self.env['PATH'] = str(self.base) + ':' + self.env['PATH']
        result = self.run_script('dock-move-toggle.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        update = self.state / 'update'
        self.assertTrue(update.exists(), 'toggle ignored DOCK_EWW_BIN')
        self.assertEqual(update.read_text().strip(), 'update menu-move-open=false')

    def test_menu_reads_shared_context_and_uses_configured_eww(self):
        self.wrapper('echo "$*" >> "$DOCK_STATE_DIR/ipc"')
        (self.base / 'eww').symlink_to(self.env['DOCK_EWW_BIN'])
        i3 = self.base / 'i3-msg'
        i3.write_text('#!/bin/sh\necho "$*" > "$DOCK_STATE_DIR/i3-command"\n')
        i3.chmod(0o755)
        self.env['PATH'] = str(self.base) + ':' + self.env['PATH']
        self.env['HOME'] = str(self.base)
        (self.runtime / 'eww-dock-menu.json').write_text(json.dumps({'con': '123'}))
        result = subprocess.run([str(self.isolated_script('dock-menu.sh')), 'moveto', '4'],
                                env=self.env, timeout=2, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        command = self.state / 'i3-command'
        self.assertTrue(command.exists(), 'menu did not read the runtime context')
        self.assertEqual(command.read_text().strip(), '[con_id=123] move container to workspace number 4')
        self.assertIn('menu-open=false', (self.state / 'ipc').read_text())

    def test_context_uses_shared_runtime_and_cancels_before_ipc(self):
        self.wrapper('''if [ "$1" = update ]; then
cat "$DOCK_RUNTIME_DIR/eww-dock-seq" > "$DOCK_STATE_DIR/generation-at-update"
echo "$*" > "$DOCK_STATE_DIR/update"
fi''')
        (self.base / 'eww').symlink_to(self.env['DOCK_EWW_BIN'])
        self.env['PATH'] = str(self.base) + ':' + self.env['PATH']
        (self.runtime / 'eww-dock-seq').write_text('41\n')
        result = subprocess.run([str(self.isolated_script('dock-ctx.sh')), '123', 'kitty',
                                 'kitty', 'kitty --title test', '1', '1'],
                                env=self.env, timeout=2, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = self.runtime / 'eww-dock-menu.json'
        self.assertTrue(context.exists(), 'menu context escaped DOCK_RUNTIME_DIR')
        self.assertEqual(json.loads(context.read_text())['con'], '123')
        self.assertEqual((self.state / 'generation-at-update').read_text().strip(), '42')
        self.assertIn('menu-running=true', (self.state / 'update').read_text())

    def test_successful_transitions_request_bounded_restack(self):
        self.app.write_text("""import os, sys
from pathlib import Path
if '--fullscreen' in sys.argv:
    print('false')
elif '--raise' in sys.argv:
    for fd in (8, 9):
        assert not Path(f'/proc/self/fd/{fd}').exists(), 'inherited lifecycle fd'
    with open(Path(os.environ['DOCK_STATE_DIR']) / 'raised', 'a') as f:
        f.write('raised\\n')
""")
        for script, args, windows in [('start-dock.sh', [], 'dock\n'),
                                      ('dock-show.sh', [], 'dock_open_area\n'),
                                      ('dock-fullscreen.sh', ['restore'], ''),
                                      ('dock-schedule-hide.sh', [], 'dock\n')]:
            with self.subTest(script=script):
                (self.state / 'windows').write_text(windows)
                raised = self.state / 'raised'
                raised.unlink(missing_ok=True)
                result = self.run_script(script, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(raised.exists(), script + ' did not request restacking')

    def test_show_owner_reconciles_pending_fullscreen_request(self):
        self.env['DOCK_IPC_TIMEOUT'] = '1'
        self.wrapper('''if [ "$1:$2" = open:dock ]; then
touch "$DOCK_STATE_DIR/open-entered"
for n in $(seq 1 100); do
 [ ! -e "$DOCK_STATE_DIR/release-open" ] || break
 sleep 0.01
done
fi''')
        process = subprocess.Popen([str(ROOT / 'dock-show.sh')], env=self.env)
        try:
            self.wait_file(self.state / 'open-entered')
            self.env['DOCK_LOCK_TIMEOUT'] = '0.02'
            result = self.run_script('dock-fullscreen.sh', 'suppress')
            self.assertNotEqual(result.returncode, 0, 'busy request must not claim application')
        finally:
            (self.state / 'release-open').touch()
            process.wait(timeout=3)
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock_open_area'])
        self.assertEqual((self.runtime / 'eww-dock-fullscreen-applied').read_text(),
                         (self.runtime / 'eww-dock-fullscreen-request').read_text())

    def test_latest_fullscreen_restore_wins_slow_suppression(self):
        (self.state / 'windows').write_text('dock\n')
        self.env['DOCK_IPC_TIMEOUT'] = '1'
        self.wrapper('''if [ "$1:$2" = open:dock_open_area ]; then
touch "$DOCK_STATE_DIR/open-entered"
for n in $(seq 1 100); do
 [ ! -e "$DOCK_STATE_DIR/release-open" ] || break
 sleep 0.01
done
fi''')
        process = subprocess.Popen([str(ROOT / 'dock-fullscreen.sh'), 'suppress'], env=self.env)
        try:
            self.wait_file(self.state / 'open-entered')
            self.env['DOCK_LOCK_TIMEOUT'] = '0.02'
            self.run_script('dock-fullscreen.sh', 'restore')
        finally:
            (self.state / 'release-open').touch()
            process.wait(timeout=3)
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock'],
                         'slow suppression overwrote the latest restore request')

    def test_fullscreen_retries_temporary_transition_contention(self):
        (self.state / 'windows').write_text('dock\n')
        with (self.runtime / 'eww-dock-show.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen([str(ROOT / 'dock-fullscreen.sh'), 'suppress'], env=self.env)
            time.sleep(0.07)
            fcntl.flock(lock, fcntl.LOCK_UN)
            self.assertEqual(process.wait(timeout=3), 0)
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock_open_area'],
                         'fullscreen request was dropped on lock contention')

    def test_cancel_uses_runtime_generation_without_ipc(self):
        self.wrapper('echo "$*" >> "$DOCK_STATE_DIR/ipc"')
        (self.runtime / 'eww-dock-seq').write_text('41\n')
        result = subprocess.run([str(self.isolated_script('dock-cancel-hide.sh'))],
                                env=self.env, timeout=2, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.runtime / 'eww-dock-seq').read_text().strip(), '42')
        self.assertFalse((self.state / 'ipc').exists(), 'hover cancellation must not use IPC')

    def wait_file(self, path):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.005)
        self.fail('fixture did not reach barrier: ' + str(path))

    def test_cancel_during_trigger_creation_preserves_dock(self):
        (self.state / 'windows').write_text('dock\n')
        (self.runtime / 'eww-dock-seq').write_text('1\n')
        self.env['DOCK_IPC_TIMEOUT'] = '1'
        self.wrapper('''if [ "$1:$2" = open:dock_open_area ]; then
touch "$DOCK_STATE_DIR/open-entered"
for n in $(seq 1 100); do
 [ ! -e "$DOCK_STATE_DIR/release-open" ] || break
 sleep 0.01
done
fi''')
        process = subprocess.Popen([str(ROOT / 'dock-schedule-hide.sh')], env=self.env)
        try:
            self.wait_file(self.state / 'open-entered')
            (self.runtime / 'eww-dock-seq').write_text('2\n')
        finally:
            (self.state / 'release-open').touch()
            process.wait(timeout=3)
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock'],
                         'a late cancelled hide closed the usable dock')

    def test_startup_bounds_every_confirmation_query(self):
        self.wrapper('if [ "$1" = active-windows ]; then sleep 0.6; exit 1; fi')
        before = time.monotonic()
        result = self.run_script('start-dock.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - before, 0.7, 'startup waited on unbounded confirmation IPC')

    def test_all_eww_calls_close_lifecycle_descriptors(self):
        self.wrapper('for fd in 8 9; do\n [ ! -e "/proc/$$/fd/$fd" ] || echo "$1:$fd" >> "$DOCK_STATE_DIR/leaked"\ndone')
        self.run_script('dock-show.sh')
        self.assertFalse((self.state / 'leaked').exists(), 'Eww inherited a lifecycle flock descriptor')
        self.assertEqual((self.state / 'windows').read_text().splitlines(), ['dock'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
