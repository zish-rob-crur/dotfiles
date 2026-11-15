import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import goto_entries as ge  # noqa: E402


class PaneTextTests(unittest.TestCase):
    def test_board_task_wins(self):
        self.assertEqual(ge.pane_text("codex", "title", "", "/x", "doing it"), "doing it")

    def test_titles_without_information_are_dropped(self):
        self.assertEqual(ge.pane_text("zsh", "zsh", "", "/x/repo", ""), "")
        self.assertEqual(ge.pane_text("zsh", "repo", "", "/x/repo/services/api", ""), "")  # a path component
        self.assertEqual(ge.pane_text("claude", "✳ Fix routing", "", "/x", ""), "Fix routing")

    def test_editor_titles_keep_only_the_file(self):
        self.assertEqual(ge.pane_text("nvim", "keymaps.md (~/a/docs) - Nvim", "", "/a", ""), "keymaps.md")
        self.assertEqual(ge.pane_text("nvim", "[Scratch] - (~/a) - Nvim", "", "/a", ""), "[Scratch]")
        self.assertEqual(ge.pane_text("nvim", "<规范.md (~/a/Team) - Nvim", "", "/a", ""), "规范.md")

    def test_session_title_is_preferred(self):
        self.assertEqual(ge.pane_text("codex", "codex", "Review MR 202", "/x", ""), "Review MR 202")


class BranchTests(unittest.TestCase):
    def test_branch_is_hidden_when_the_worktree_directory_spells_it(self):
        self.assertEqual(ge.branch_label("fix/agentic/v2-prompt-forward-compat", "mono.fix-agentic-v2-prompt-forward-compat"), "")
        self.assertEqual(ge.branch_label("codex/web-ui-main-ci", "mono.codex-web-ui-main-ci"), "")

    def test_branch_is_shown_otherwise(self):
        self.assertEqual(ge.branch_label("main", "dotfiles"), "main")
        self.assertEqual(ge.branch_label("feat/agentic/goal-specs-main", "mono.feat-agentic-goal-specs"), "feat/agentic/goal-specs-main")
        self.assertEqual(ge.branch_label("", "dotfiles"), "")


class WindowOrderTests(unittest.TestCase):
    def test_done_pin_does_not_outrank_running_or_waiting(self):
        def row(wid, index, badge='', pin=''):
            return [wid, str(index), 'task', badge, '%1', '/work', pin]
        rows = [row('@1',1,'󰄬','1'),row('@2',2,'●'),row('@3',3,'◆'),row('@4',4)]
        ordered = sorted(rows, key=lambda r: ge.window_sort_key(r, {}, {}, ''))
        self.assertEqual([r[0] for r in ordered], ['@3','@2','@4','@1'])

    def test_acknowledged_completed_tasks_remain_below_active_work(self):
        board = {'@1':{'group':'review'}}
        done = ['@1','1','done','','%1','/x','1']
        active = ['@2','2','active','●','%2','/y','']
        self.assertGreater(ge.window_sort_key(done,board,{},''), ge.window_sort_key(active,board,{},''))
        done[3] = '●'  # A new live turn supersedes an older completed summary.
        self.assertLess(ge.window_sort_key(done,board,{},''), ge.window_sort_key(active,board,{},''))


class ViewTests(unittest.TestCase):
    def test_pinned_completed_overlap_preserves_whole_window(self):
        from goto_search import view_source
        def row(kind, wid):
            return '\t'.join([kind, wid, 'task', 'NORMAL', wid, '%1', 'main', '-', '-', '1', '-', ''])
        rows = [row('W', '@1'), row('P', '@1'), row('W', '@2')]
        source = '\n'.join(rows)
        metadata = {'@1': {'pinned': True, 'completed': True}, '@2': {'pinned': False, 'completed': False}}
        self.assertEqual(view_source(source, metadata, 'all'), source)
        for view in ('pinned', 'completed'):
            self.assertEqual(view_source(source, metadata, view).splitlines(), rows[:2])
        self.assertEqual(view_source(source, {}, 'pinned'), '')

    def test_view_persists_across_queries_and_pin_reload(self):
        import json
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            def row(wid):
                return '\t'.join(['W', wid, 'handbook', 'NORMAL', wid, '%1', 'main', '-', '-', wid[1:], '-', ''])
            (path/'entries').write_text(row('@1')+'\n'+row('@2')+'\n')
            metadata = {'@1': {'pinned': True}, '@2': {'pinned': False}}
            (path/'window-meta.json').write_text(json.dumps(metadata))
            command = [sys.executable, str(Path(ge.__file__).with_name('goto_search.py')), '--state-dir', directory]
            def run(*args):
                return subprocess.check_output([*command, *args], text=True)
            self.assertIn('@1', run('--cycle-view'))
            self.assertEqual((path/'view').read_text(), 'pinned')
            self.assertEqual(run('--cycle-view'), '')
            self.assertEqual((path/'view').read_text(), 'completed')
            self.assertIn('@2', run('--cycle-view'))
            self.assertEqual((path/'view').read_text(), 'all')
            self.assertIn('@1', run('--cycle-view'))
            self.assertNotIn('@2', run('--query', 'handbook'))
            metadata['@1']['pinned'] = False
            (path/'window-meta.json').write_text(json.dumps(metadata))
            self.assertEqual(run('--query', 'handbook'), '')
            self.assertEqual((path/'windows').read_text(), '')
            self.assertIn('@2', run('--view', 'all', '--query', 'handbook'))


class GlyphTests(unittest.TestCase):
    def test_badge_glyphs(self):
        self.assertIn("◆", ge.glyph(" #[fg=#BF8700,bold]◆#[pop-default]"))
        self.assertIn("✓", ge.glyph(" #[fg=#1A7F37,bold]󰄬"))
        self.assertEqual(ge.glyph(""), " ")



class SearchTests(unittest.TestCase):
    def test_hidden_metadata_matches_without_polluting_display(self):
        from goto_search import filter_rows
        fields = ['P','%1','codex  配置方案已更新','NORMAL','@1','%1','main','-','-','3','1',
                  ge.search_suffix('设计 Calling 配置覆盖方案','','/work/call-e','feat/runtime-overrides','overwrite 配置覆盖')]
        parent = ['W','@1','3 calle:overrides','NORMAL','@1','%1','main','-','-','3','-','/work/call-e']
        source = '\t'.join(parent)+'\n'+'\t'.join(fields)+'\n'
        for query in ['call-e overwrite', '配置覆盖', 'runtime-overrides', '3']:
            rows = filter_rows(source, query)
            self.assertEqual(len(rows), 2, query)
            self.assertNotIn('overwrite', rows[1].split('\t')[2])
            self.assertNotIn('/work', rows[1].split('\t')[2])
        self.assertEqual(filter_rows(source, 'no-such-task'), [])
        self.assertEqual(filter_rows(source, ''), source.splitlines())

    def test_ansi_colored_rows_survive_fzf_filter(self):
        from goto_search import filter_rows
        parent = ['W','@1','\033[1mdotfiles\033[0m','NORMAL','@1','%1','main','-','-','17','-','/work/dotfiles']
        pane = ['P','%1','\033[2mnvim handbook.md\033[0m','NORMAL','@1','%1','main','-','-','17','1','handbook /work/dotfiles']
        original = ['\t'.join(parent), '\t'.join(pane)]
        source = '\n'.join(original) + '\n'
        for query in ['handbook', 'dotfile']:
            self.assertEqual(filter_rows(source, query), original)

    def test_project_match_ranks_first_even_when_another_window_is_pinned(self):
        from goto_search import filter_rows
        def row(kind, ident, wid, display, metadata):
            return '\t'.join([kind,ident,display,'NORMAL',wid,'%1','main','-','-','17','1',metadata])
        source_rows = [
            row('W','@1','@1','pinned other-project','docs of the files'),
            row('P','%1','@1','codex mentioning dotfile incidentally','dotfile'),
            row('W','@2','@2','\033[1mdotf:search dotfiles\033[0m','/work/dotfiles'),
            row('P','%2','@2','nvim config.py','/work/dotfiles'),
        ]
        source = '\n'.join(source_rows)+'\n'
        for query in ['dotfil', 'dotfile', 'dotfiles']:
            found = filter_rows(source, query)
            self.assertEqual(found[0].split('\t')[4], '@2')
            self.assertEqual(found[1].split('\t')[4], '@2')
        self.assertEqual(filter_rows(source, ''), source_rows)

    def test_default_shell_title_does_not_add_a_row(self):
        self.assertEqual(ge.pane_text('zsh','user@Mac:/work/call-e','','/work/call-e',''), '')


if __name__ == "__main__":
    unittest.main()
