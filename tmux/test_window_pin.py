"""Integration coverage using a disposable tmux server, never the user's server."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(shutil.which('tmux'), 'tmux required')
class WindowPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.socket = str(self.path / 'socket')
        binary = shutil.which('tmux')
        wrapper = self.path / 'tmux'
        import shlex
        wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(binary)} -f /dev/null -S {shlex.quote(self.socket)} "$@"\n')
        wrapper.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.path}:{os.environ["PATH"]}', TMUX='', XDG_CACHE_HOME=str(self.path / 'cache'))
        self.run_tmux('new-session', '-d', '-s', 'test', '-n', 'first')
        self.first = self.run_tmux('display-message', '-p', '-t', 'test:0', '#{window_id}').strip()
        self.second = self.run_tmux('new-window', '-d', '-P', '-F', '#{window_id}', '-t', 'test', '-n', 'second').strip()
        self.state = self.path / 'state'
        self.state.mkdir()

    def tearDown(self):
        self.run_tmux('kill-server')
        self.temp.cleanup()

    def run_tmux(self, *args):
        return subprocess.check_output(['tmux', *args], env=self.env, text=True)

    def entries(self, *args):
        output = subprocess.check_output([sys.executable, str(ROOT / 'goto_entries.py'),
            '--session', 'test', '--window-id', self.first, '--state-dir', str(self.state), *args], env=self.env, text=True)
        return [row.split('\t') for row in output.splitlines() if row]

    def pin(self, window):
        return self.run_tmux('show-options', '-wqv', '-t', window, '@focus-pin').strip()

    def test_pin_unpin_and_shared_window_identity(self):
        rows = self.entries('--toggle-pin', self.second)
        self.assertEqual(rows[0][4], self.second)
        self.assertIn('󰐃', rows[0][2])
        self.run_tmux('rename-window', '-t', self.second, 'renamed')
        self.run_tmux('new-session', '-d', '-s', 'linked', '-t', 'test')
        self.assertEqual(self.pin(f'linked:{self.second}'), '1')
        rows = self.entries('--toggle-pin', self.second)
        self.assertEqual(rows[0][4], self.first)
        self.assertNotIn('󰐃', rows[0][2])
        self.assertNotIn('@focus-pin', self.run_tmux('show-options', '-w', '-t', self.second))

    def test_pin_without_target_uses_current_window(self):
        # run-shell (tmux-fzf's menu) has no TMUX_PANE.
        env = dict(self.env, TMUX_PANE='')
        subprocess.run([sys.executable, str(ROOT / 'window_pin.py')], env=env, check=True)
        current = self.run_tmux('display-message', '-p', '#{window_id}').strip()
        self.assertEqual(self.pin(current), '1')

    def test_save_and_restore_carry_pins_across_a_new_server(self):
        script = [sys.executable, str(ROOT / 'window_pin.py')]
        env = dict(self.env, XDG_DATA_HOME=str(self.path / 'data'))
        subprocess.run(script + ['--target', self.second], env=env, check=True)
        subprocess.run(script + ['--save'], env=env, check=True)
        self.run_tmux('kill-server')
        self.run_tmux('new-session', '-d', '-s', 'test', '-n', 'first')
        self.run_tmux('new-window', '-d', '-t', 'test', '-n', 'second')
        subprocess.run(script + ['--restore'], env=env, check=True)
        self.assertEqual(self.pin('test:1'), '1')
        self.assertEqual(self.pin('test:0'), '')

    def test_pane_target_and_pin_reload_keep_cursor(self):
        pane = self.run_tmux('display-message', '-p', '-t', self.second, '#{pane_id}').strip()
        subprocess.run([sys.executable, str(ROOT / 'window_pin.py'), '--target', pane], env=self.env, check=True)
        rows = self.entries()
        self.assertEqual(rows[0][4], self.second)
        command = ['bash', str(ROOT / 'goto_cursor.sh'), 'renamed', str(self.state / 'start'), str(self.state / 'windows')]
        self.assertEqual(subprocess.check_output(command, text=True, env=dict(os.environ, FZF_KEY='d')), 'best\n')
        self.assertEqual(subprocess.check_output(command, text=True, env=dict(os.environ, FZF_KEY='alt-p')), '')


if __name__ == '__main__':
    unittest.main()
