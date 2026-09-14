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


class GlyphTests(unittest.TestCase):
    def test_badge_glyphs(self):
        self.assertIn("◆", ge.glyph(" #[fg=#BF8700,bold]◆#[pop-default]"))
        self.assertIn("✓", ge.glyph(" #[fg=#1A7F37,bold]󰄬"))
        self.assertEqual(ge.glyph(""), " ")


if __name__ == "__main__":
    unittest.main()
