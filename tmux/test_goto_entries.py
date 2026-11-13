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
