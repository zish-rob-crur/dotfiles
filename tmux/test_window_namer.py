#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import window_namer as wn  # noqa: E402


def window(**overrides):
    fields = {"id": "@1", "name": "dotfiles", "auto_rename": True, "llm_name": "", "llm_key": "", "icon": ""}
    fields.update(overrides)
    return wn.Window(**fields)


class SanitizeTests(unittest.TestCase):
    def test_lowercases_and_strips_junk(self):
        self.assertEqual(wn.sanitize("Bug Handoff!"), "bug-handoff")

    def test_truncates_at_word_boundary(self):
        self.assertEqual(wn.sanitize("goal-review-fix-for-agents-and-more"), "goal-review-fix-for-agents")
        self.assertEqual(wn.sanitize("a" * 40), "a" * wn.MAX_NAME_LENGTH)


class PrefixTests(unittest.TestCase):
    def setUp(self):
        self.original = wn.prefix_overrides
        wn.prefix_overrides = lambda: {"my-long-repo-name": "mono"}

    def tearDown(self):
        wn.prefix_overrides = self.original

    def test_override_wins(self):
        self.assertEqual(wn.project_prefix("my-long-repo-name"), "mono")

    def test_initials_for_multiword_and_head_for_single(self):
        self.assertEqual(wn.project_prefix("my-cool-repo"), "mcr")
        self.assertEqual(wn.project_prefix("dotfiles"), "dotf")
        self.assertEqual(wn.project_prefix(""), "")

    def test_compose(self):
        self.assertEqual(wn.compose("✳", "mono", "goal-fix"), "✳ mono:goal-fix")
        self.assertEqual(wn.compose("", "", "agents-edit"), "agents-edit")


class CleanTests(unittest.TestCase):
    def test_icon_keeps_leading_tool_glyph(self):
        self.assertEqual(wn.clean_icon("✳×2"), "✳")
        self.assertEqual(wn.clean_icon("·"), "")

    def test_title_drops_directory_and_spinner_noise(self):
        self.assertEqual(wn.clean_title("✳ Fix routing", "/x/repo"), "Fix routing")
        self.assertEqual(wn.clean_title("repo", "/x/repo"), "")
        self.assertEqual(wn.clean_title("⠧ repo.feature-polic...", "/x/repo"), "")

    def test_claude_version_becomes_claude(self):
        self.assertEqual(wn.clean_command("2.1.260"), "claude")


class KeyTests(unittest.TestCase):
    def test_key_is_a_digest_that_survives_list_panes_splitting(self):
        w = window()
        w.panes.append(wn.Pane("/a", "zsh", "t", True, branch="b"))
        self.assertNotIn(wn.FIELD_SEP, w.key)  # @llm-key travels through list-panes output split on FIELD_SEP
        self.assertRegex(w.key, r"^[0-9a-f]{16}$")
        same = window()
        same.panes.append(wn.Pane("/a", "zsh", "t", True, branch="b"))
        self.assertEqual(w.key, same.key)
        same.panes[0].title = "other"
        self.assertNotEqual(w.key, same.key)


class OwnershipTests(unittest.TestCase):
    def window_named(self, name, icon="✳", repo="my-cool-repo"):
        w = window(auto_rename=False, llm_name="", name=name, icon=icon)
        w.panes.append(wn.Pane("/x", "zsh", "", True, branch="main", repo=repo))
        return w

    def test_restored_session_keeps_a_name_this_script_would_compose(self):
        # tmux-resurrect restores the name but not @llm-name / @llm-key.
        self.assertTrue(self.window_named("✳ mcr:goal-fix").owned)
        self.assertTrue(self.window_named("mcr:goal-fix", icon="").owned)  # a prefix alone is evidence
        self.assertTrue(self.window_named("✳ goal-fix").owned)  # so is an icon alone
        # The icon and the prefix in the name are the ones from when it was set;
        # the pane mix and the worktree may have moved on since.
        self.assertTrue(self.window_named("\ue00b other:goal-fix").owned)

    def test_a_hand_typed_name_stays_manual(self):
        self.assertFalse(self.window_named("✳ mcr:Goal Fix").owned)  # spaces and capitals
        self.assertFalse(self.window_named("mine", icon="").owned)  # neither icon nor prefix
        self.assertFalse(self.window_named("todo list", icon="").owned)


class PlanTests(unittest.TestCase):
    def test_skips_windows_renamed_by_hand(self):
        w = window(auto_rename=False, llm_name="✳ x:old", name="mine")
        w.panes.append(wn.Pane("/x", "zsh", "", True, branch="main"))
        self.assertEqual(wn.plan([w]), ([], []))

    def test_names_signal_and_releases_stale(self):
        fresh = window(id="@1")
        fresh.panes.append(wn.Pane("/x", "codex", "Do thing", True, branch="main"))
        stale = window(id="@2", auto_rename=False, llm_name="✳ x:old", name="✳ x:old")
        stale.panes.append(wn.Pane("/y", "zsh", "", True))
        quiet = window(id="@3")
        quiet.panes.append(wn.Pane("/z", "zsh", "", True))
        to_name, to_release = wn.plan([fresh, stale, quiet])
        self.assertEqual([w.id for w in to_name], ["@1"])
        self.assertEqual([w.id for w in to_release], ["@2"])

    def test_unchanged_owned_window_is_skipped(self):
        w = window(auto_rename=False, llm_name="✳ x:topic", name="✳ x:topic")
        w.panes.append(wn.Pane("/x", "codex", "Do thing", True, branch="main"))
        w.llm_key = w.key
        self.assertEqual(wn.plan([w]), ([], []))
        self.assertEqual(w.topic, "topic")
        bare = window(auto_rename=False, llm_name="\ue62b agents-edit", name="\ue62b agents-edit")
        self.assertEqual(bare.topic, "agents-edit")


if __name__ == "__main__":
    unittest.main()
