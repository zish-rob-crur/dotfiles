"""goto_step.sh: arrow and tab moves in the goto popup."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

STEP = Path(__file__).resolve().parent / 'goto_step.sh'


class GotoStepTests(unittest.TestCase):
    def setUp(self):
        # Window lines 1, 4 and 7 of a 9-line list; the others are their panes.
        self.map = tempfile.NamedTemporaryFile('w', delete=False)
        self.map.write('1\t1\n4\t4\n7\t7\n')
        self.map.close()
        self.addCleanup(os.unlink, self.map.name)

    def step(self, kind, direction, line, query=''):
        return subprocess.check_output(['bash', str(STEP), kind, direction, query, str(line - 1), self.map.name],
                                       env=dict(os.environ, FZF_TOTAL_COUNT='9'), text=True).strip()

    def test_arrows_step_window_to_window_without_a_query(self):
        self.assertEqual(self.step('window', 'down', 1), 'pos(4)')
        self.assertEqual(self.step('window', 'down', 5), 'pos(7)')
        self.assertEqual(self.step('window', 'up', 6), 'pos(4)')  # a pane goes up to its own window
        self.assertEqual(self.step('window', 'up', 4), 'pos(1)')
        self.assertEqual(self.step('window', 'down', 8), '')
        self.assertEqual(self.step('window', 'up', 1), '')

    def test_arrows_step_matches_with_a_query(self):
        self.assertEqual(self.step('window', 'down', 2, 'dot'), 'down-match')
        self.assertEqual(self.step('window', 'up', 2, 'dot'), 'up-match')

    def test_tab_cycles_within_the_window(self):
        self.assertEqual(self.step('pane', 'next', 4), 'pos(5)')
        self.assertEqual(self.step('pane', 'next', 6), 'pos(4)')
        self.assertEqual(self.step('pane', 'prev', 4), 'pos(6)')
        self.assertEqual(self.step('pane', 'next', 9), 'pos(7)')  # the last window ends with the list
        self.assertEqual(self.step('pane', 'prev', 7), 'pos(9)')


if __name__ == '__main__':
    unittest.main()
