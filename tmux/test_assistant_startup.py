import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant_startup as startup
from test_assistant_restore import RESURRECT, RESTART, verified_hook_process, saved_pane, pane, SESSION_ID


class StartupTests(unittest.TestCase):
    def test_real_interactive_zsh_expands_alias_and_function_after_delay(self):
        if not Path('/bin/zsh').exists():
            self.skipTest('zsh unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recorder = root / 'record.py'
            recorder.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
            output = root / 'args.json'
            invoke = shlex.join([sys.executable, str(recorder), str(output)])
            (root / '.zshrc').write_text(f'alias codex={shlex.quote(invoke + " alias")}\nclaude() {{ {invoke} function "$@"; }}\n')
            env = dict(os.environ, HOME=tmp, ZDOTDIR=tmp, SHELL='/bin/zsh')
            for tool, marker in [('codex', 'alias'), ('claude', 'function')]:
                words = ['/old/absolute/path', 'resume', SESSION_ID, 'literal ; $(false) with spaces']
                with patch.dict(os.environ, env):
                    command = startup.shell_command(words, tmp, tool, delay=.1)
                before = time.monotonic()
                subprocess.run(['/bin/sh', '-c', command], env=env, input='', text=True,
                               capture_output=True, check=True, timeout=10)
                self.assertGreaterEqual(time.monotonic() - before, .09)
                self.assertEqual(json.loads(output.read_text()), [marker, *words[1:]])

    def test_saved_stagger_is_stable_when_rewritten_again(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, TMUX_ASSISTANT_START_INTERVAL='3'):
            path = Path(tmp) / 'snapshot'
            records = [saved_pane('codex resume ' + SESSION_ID) for _ in range(3)]
            path.write_text('\n'.join('\t'.join(row) for row in records) + '\n')
            with verified_hook_process(['codex', 'resume', SESSION_ID]):
                RESURRECT.rewrite_file(path)
                first = path.read_text()
                RESURRECT.rewrite_file(path)
            self.assertEqual(path.read_text(), first)
            commands = [line.split('\t')[10][1:] for line in first.splitlines()]
            self.assertTrue(commands[0].startswith('exec '))
            self.assertTrue(commands[1].startswith('sleep 3; exec '))
            self.assertTrue(commands[2].startswith('sleep 6; exec '))

    def test_batch_waits_before_revalidating_next_pane_and_dry_run_never_waits(self):
        for dry in (False, True):
            events = []
            with (patch.object(RESTART, 'list_panes', return_value=[pane(), pane()]),
                  patch.object(RESTART, 'process_rows', return_value=[]),
                  patch.object(RESTART, 'pane_tool', return_value='codex'),
                  patch.object(RESTART, 'current_assistant_process', return_value=('200', ['codex', 'resume', SESSION_ID])),
                  patch.object(RESTART, 'process_lstart_epoch', return_value=200),
                  patch.object(RESTART, 'resolve_executable', return_value='/bin/codex'),
                  patch.object(RESTART, 'start_interval', return_value=3),
                  patch.object(RESTART.time, 'monotonic', return_value=10),
                  patch.object(RESTART.time, 'sleep', side_effect=lambda seconds: events.append(('wait', seconds))),
                  patch.object(RESTART, 'verified_respawn', side_effect=lambda *args: events.append(('recheck-respawn',)) or ''),
                  patch.object(RESTART.subprocess, 'run')):
                RESTART.restart_panes(dry, 'all')
            self.assertEqual(events, [] if dry else [('recheck-respawn',), ('wait', 3), ('recheck-respawn',)])

    def test_invalid_intervals_fall_back_and_zero_disables_delay(self):
        for value in ('nan', 'inf', '-1', 'oops'):
            with patch.dict(os.environ, TMUX_ASSISTANT_START_INTERVAL=value):
                self.assertEqual(startup.start_interval(), 3)
        with patch.dict(os.environ, TMUX_ASSISTANT_START_INTERVAL='0'):
            self.assertEqual(startup.start_interval(), 0)


if __name__ == '__main__':
    unittest.main()
