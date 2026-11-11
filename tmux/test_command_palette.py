import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('palette', Path(__file__).with_name('command-palette.py'))
palette = importlib.util.module_from_spec(spec)
spec.loader.exec_module(palette)


class PaletteTests(unittest.TestCase):
    @patch.object(palette.subprocess, 'run')
    def test_restarts_only_request_confirmation(self, run):
        for tool in ('all', 'codex', 'claude'):
            palette.dispatch('restart-' + tool, '/dev/test', '@12')
            args = run.call_args.args[0]
            self.assertEqual(args[:5], ['tmux', 'confirm-before', '-t', '/dev/test', '-p'])
            self.assertIn('ALL sessions', args[5])
            self.assertIn('--tool ' + tool, args[6])
            self.assertEqual(run.call_count, ('all', 'codex', 'claude').index(tool) + 1)

    @patch.object(palette.subprocess, 'run')
    def test_pin_uses_captured_window_and_client(self, run):
        palette.dispatch('pin', '/dev/test', '@12')
        self.assertEqual(run.call_args.args[0][-4:], ['--target', '@12', '--client', '/dev/test'])

    @patch.object(palette.subprocess, 'run')
    def test_unknown_action_never_executes(self, run):
        with self.assertRaises(ValueError):
            palette.dispatch('restart-all; bad-command', '/dev/test', '@12')
        run.assert_not_called()

    @patch.object(palette, 'dispatch')
    @patch.object(palette.subprocess, 'run')
    def test_cancel_does_nothing(self, run, dispatch):
        run.return_value = subprocess.CompletedProcess([], 130, '', '')
        with patch('sys.argv', ['palette', '--client', '/dev/test', '--window', '@12']):
            palette.main()
        dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
