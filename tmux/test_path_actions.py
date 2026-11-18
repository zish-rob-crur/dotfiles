"""path-actions.sh: fastcopy copy, wrapped path recovery, and smart nvim pane/window actions."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / 'path-actions.sh'


class PathActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        self.log = Path(self.tmp.name) / 'calls.log'
        self.panes = '%5\tzsh\n'
        self.pane_Geometry = '%5\t200\t50\n'
        self.captured = ''

        self._write_exe('tmux', """#!/usr/bin/env bash
printf 'tmux %s\\n' "$*" >> "$TEST_LOG"
case "$1" in
  load-buffer) cat >> "$TEST_LOG.buf" ;;
  display-message) printf '@9\\n' ;;
  list-clients) printf '%%5 main /dev/ttys001\\n' ;;
  list-panes)
    if [[ "$*" == *"pane_width"* ]]; then
      printf '%b' "$FAKE_GEOMETRY"
    else
      printf '%b' "$FAKE_PANES"
    fi
    ;;
  capture-pane) printf '%s' "$FAKE_CAPTURED" ;;
esac
""")
        self._write_exe('pbcopy', """#!/usr/bin/env bash
cat > "$TEST_LOG.clip"
""")
        self._write_exe('fzf', """#!/usr/bin/env bash
printf '%s\\n' "$*" > "$TEST_LOG.fzf_args"
cat > "$TEST_LOG.choices"
head -n 1 "$TEST_LOG.choices"
""")
        self._write_exe('nvim', """#!/usr/bin/env bash
exit 0
""")
        self._write_exe('open', """#!/usr/bin/env bash
printf 'open %s\\n' "$*" >> "$TEST_LOG"
""")

    def _write_exe(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def _env(self, **extra):
        env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ.get('PATH', '')}",
            TEST_LOG=str(self.log),
            TMUX='/tmp/tmux,1,0',
            FAKE_PANES=self.panes,
            FAKE_GEOMETRY=self.pane_Geometry,
            FAKE_CAPTURED=self.captured,
        )
        env.update(extra)
        return env

    def test_fastcopy_non_path_only_copies(self):
        subprocess.run(['bash', str(SCRIPT), '--fastcopy'], input='deadbeef1234',
                       text=True, check=True, cwd=self.tmp.name,
                       env=self._env(FASTCOPY_TARGET_PANE_ID='%5'))
        self.assertEqual(Path(f'{self.log}.buf').read_text(), 'deadbeef1234')
        self.assertNotIn('run-shell', self.log.read_text())

    def test_fastcopy_wrapped_path_unwraps_and_schedules_popup(self):
        docs = Path(self.tmp.name) / 'services' / 'calle-evals' / 'docs'
        docs.mkdir(parents=True)
        target = docs / 'barsec-b01-draft.yaml'
        target.write_text('ok: true\n')

        wrapped = f"{docs.parent}/   \n  docs/barsec-b01-draft.yaml"
        subprocess.run(['bash', str(SCRIPT), '--fastcopy'], input=wrapped,
                       text=True, check=True, cwd=self.tmp.name,
                       env=self._env(FASTCOPY_TARGET_PANE_ID='%5'))
        self.assertEqual(Path(f'{self.log}.buf').read_text(), str(target))
        self.assertIn('run-shell -b -d 0.08', self.log.read_text())
        self.assertIn('--open-popup', self.log.read_text())

    def test_fastcopy_recovers_wrapped_tail_from_capture_pane(self):
        docs = Path(self.tmp.name) / 'services' / 'calle-evals' / 'docs'
        docs.mkdir(parents=True)
        target = docs / 'barsec-b01-draft.yaml'
        target.write_text('ok: true\n')

        self.captured = f"直接看 ({docs.parent}/   ⌄\ndocs/barsec-b01-draft.yaml)。\n"
        subprocess.run(['bash', str(SCRIPT), '--fastcopy'], input=str(docs.parent),
                       text=True, check=True, cwd=self.tmp.name,
                       env=self._env(FASTCOPY_TARGET_PANE_ID='%5'))
        self.assertEqual(Path(f'{self.log}.buf').read_text(), str(target))
        self.assertIn(str(target), self.log.read_text())

    def test_default_choice_without_nvim_pane_splits_largest_pane_smartly(self):
        target = Path(self.tmp.name) / 'note.md'
        target.write_text('hello')
        # %5 is narrow (80x50), %6 is larger but taller/narrower (100x50 -> 100 <= 2.5*50 -> -v)
        self.pane_Geometry = '%5\t80\t50\n%6\t100\t50\n'
        subprocess.run(['bash', str(SCRIPT), '--cwd', self.tmp.name, '--pane', '%5', '--', str(target)],
                       text=True, check=True, cwd=self.tmp.name, env=self._env())
        choices = Path(f'{self.log}.choices').read_text().splitlines()
        self.assertEqual(choices[:2], ['new tmux pane + nvim', 'new tmux window + nvim'])
        self.assertIn('--preview=', Path(f'{self.log}.fzf_args').read_text())
        self.assertIn('bat --style=numbers,changes', Path(f'{self.log}.fzf_args').read_text())
        self.assertIn('split-window -v -c', self.log.read_text())
        self.assertIn('-t %6', self.log.read_text())

        # When largest pane is wide (200x50 -> 200 > 2.5*50 -> -h)
        self.log.write_text('')
        self.pane_Geometry = '%5\t80\t50\n%7\t200\t50\n'
        subprocess.run(['bash', str(SCRIPT), '--cwd', self.tmp.name, '--pane', '%5', '--', str(target)],
                       text=True, check=True, cwd=self.tmp.name, env=self._env())
        self.assertIn('split-window -h -c', self.log.read_text())
        self.assertIn('-t %7', self.log.read_text())

    def test_default_choice_with_nvim_pane_replaces_buffer_in_existing_nvim(self):
        target = Path(self.tmp.name) / 'note.md'
        target.write_text('hello')
        self.panes = '%5\tzsh\n%8\tnvim\n'
        subprocess.run(['bash', str(SCRIPT), '--cwd', self.tmp.name, '--pane', '%5', '--', str(target)],
                       text=True, check=True, cwd=self.tmp.name, env=self._env())
        choices = Path(f'{self.log}.choices').read_text().splitlines()
        self.assertEqual(choices[:3], ['replace in current window nvim', 'new tmux pane + nvim', 'new tmux window + nvim'])
        calls = self.log.read_text()
        self.assertIn('send-keys -t %8 -l :if &buftype !=#', calls)
        self.assertIn(str(target.resolve()), calls)
        self.assertIn('select-pane -t %8', calls)

    def test_html_file_puts_open_in_browser_first(self):
        target = Path(self.tmp.name) / 'report.html'
        target.write_text('<h1>Report</h1>')
        subprocess.run(['bash', str(SCRIPT), '--cwd', self.tmp.name, '--pane', '%5', '--', str(target)],
                       text=True, check=True, cwd=self.tmp.name, env=self._env())
        choices = Path(f'{self.log}.choices').read_text().splitlines()
        self.assertEqual(choices[0], 'open in browser')
        self.assertIn(f'open {target.resolve()}', self.log.read_text())

    def test_fastcopy_path_with_spaces_and_space_wrap_unwraps_cleanly(self):
        folder = Path(self.tmp.name) / 'Interview' / '2026 Q3 Agent Engineer' / 'Candidates' / 'Zeng-Qiwen'
        folder.mkdir(parents=True)
        target = folder / 'Interview Details.md'
        target.write_text('# Notes\n')

        wrapped = 'Interview/2026 Q3 Agent Engineer/Candidates/Zeng-Qiwen/Interview\n  Details.md'
        subprocess.run(['bash', str(SCRIPT), '--fastcopy'], input=wrapped,
                       text=True, check=True, cwd=self.tmp.name,
                       env=self._env(FASTCOPY_TARGET_PANE_ID='%5'))
        self.assertEqual(
            Path(f'{self.log}.buf').read_text(),
            'Interview/2026 Q3 Agent Engineer/Candidates/Zeng-Qiwen/Interview Details.md',
        )
        self.assertIn('run-shell -b -d 0.08', self.log.read_text())
        self.assertIn(r'Interview\ Details.md', self.log.read_text())

    def test_http_url_fastcopy_triggers_popup_and_opens_browser(self):
        url = 'http://localhost:5174/assistant/threads/9d8bf715-0d92-4136-8c95-9a29fde569fb'
        subprocess.run(['bash', str(SCRIPT), '--fastcopy'], input=url,
                       text=True, check=True, cwd=self.tmp.name,
                       env=self._env(FASTCOPY_TARGET_PANE_ID='%5'))
        self.assertEqual(Path(f'{self.log}.buf').read_text(), url)
        self.assertIn('run-shell -b -d 0.08', self.log.read_text())

        self.log.write_text('')
        subprocess.run(['bash', str(SCRIPT), '--cwd', self.tmp.name, '--pane', '%5', '--', url],
                       text=True, check=True, cwd=self.tmp.name, env=self._env())
        choices = Path(f'{self.log}.choices').read_text().splitlines()
        self.assertEqual(choices[0], 'open in browser')
        self.assertIn(f'open {url}', self.log.read_text())


if __name__ == '__main__':
    unittest.main()
