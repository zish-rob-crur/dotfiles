#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("task_board", Path(__file__).with_name("task-board.py"))
assert spec and spec.loader
tb = importlib.util.module_from_spec(spec)
sys.modules["task_board"] = tb
spec.loader.exec_module(tb)


def window(**overrides):
    fields = {"id": "@1", "index": 1, "session": "0", "name": "w", "icon": "", "badge": "", "activity": 0}
    fields.update(overrides)
    return tb.Window(**fields)


class ClassifyTests(unittest.TestCase):
    def test_badge_glyphs(self):
        self.assertEqual(tb.classify(" #[fg=x,bold]◆#[pop-default]"), "waiting")
        self.assertEqual(tb.classify("󰄬"), "done")
        self.assertEqual(tb.classify("×"), "error")
        self.assertEqual(tb.classify("●"), "running")
        self.assertEqual(tb.classify(""), "idle")

    def test_tool_detection(self):
        self.assertEqual(tb.tool_of("codex", ""), "codex")
        self.assertEqual(tb.tool_of("2.1.260", "✳ Fix it"), "claude")
        self.assertEqual(tb.tool_of("zsh", "host"), "")


class GroupTests(unittest.TestCase):
    def test_groups(self):
        self.assertEqual(tb.group_of("waiting", []), "attention")
        self.assertEqual(tb.group_of("idle", [{"next": "已完成，待你决定是否继续"}]), "attention")
        self.assertEqual(tb.group_of("idle", [{"next": "已完成，待查看结果"}]), "review")
        self.assertEqual(tb.group_of("running", [{"next": "等你确认"}]), "attention")
        self.assertEqual(tb.group_of("running", []), "working")
        self.assertEqual(tb.group_of("idle", []), "parked")


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.capture = mock.patch.object(tb, "capture", side_effect=lambda pane_id: f"tail of {pane_id}")
        self.capture.start()

    def tearDown(self):
        self.capture.stop()

    def test_sorting_and_cache_reuse_without_llm(self):
        waiting = window(id="@1", index=1, badge="◆", activity=100)
        waiting.panes.append(tb.Pane("%1", True, "codex", "/x", "t", "codex"))
        running = window(id="@2", index=2, badge="●", activity=200)
        running.panes.append(tb.Pane("%2", True, "codex", "/x", "t", "codex"))
        shell = window(id="@3", index=3, activity=300)
        shell.panes.append(tb.Pane("%3", True, "zsh", "/x", "", ""))
        board, cache, called = tb.build_board([shell, running, waiting], {}, now=1000, allow_llm=False)
        self.assertFalse(called)
        self.assertEqual([r["id"] for r in board["windows"]], ["@1", "@2", "@3"])
        self.assertEqual([r["group"] for r in board["windows"]], ["attention", "working", "parked"])
        self.assertEqual(cache, {})

    def test_llm_only_for_settled_changed_windows(self):
        settled = window(id="@1", badge="", activity=0)
        settled.panes.append(tb.Pane("%1", True, "codex", "/x", "t", "codex"))
        busy = window(id="@2", badge="●", activity=0)
        busy.panes.append(tb.Pane("%2", True, "codex", "/x", "t", "codex"))
        with mock.patch.object(tb, "ask_codex", return_value={"@1": {"tasks": [{"summary": "s", "next": "等你确认", "panes": ["%1"]}]}}) as ask:
            busy_cache = {"@2": {"hash": "old", "at": 990, "tasks": [{"summary": "b", "next": "n", "panes": []}]}}  # summarised 10 s ago
            board, cache, called = tb.build_board([settled, busy], busy_cache, now=1000, allow_llm=True)
            self.assertTrue(called)
            self.assertEqual([p["id"] for p in ask.call_args[0][0]], ["@1"])  # busy window waits for its 2-minute refresh
        self.assertEqual(cache["@1"]["tasks"][0]["summary"], "s")
        self.assertEqual(cache["@2"]["tasks"][0]["summary"], "b")
        self.assertEqual(board["windows"][0]["id"], "@1")
        self.assertEqual(board["windows"][0]["group"], "attention")  # idle + "等你" -> needs you
        # Same scrollback again: served from cache, no call.
        with mock.patch.object(tb, "ask_codex") as ask:
            _, _, called = tb.build_board([settled], cache, now=2000, allow_llm=True)
            self.assertFalse(called)
            ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
