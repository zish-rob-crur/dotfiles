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


class ThrottleTests(unittest.TestCase):
    def window(self, badge):
        w = tb.Window(id="@1", index=1, session="0", name="w", icon="", badge=badge, activity=0)
        w.panes.append(tb.Pane("%1", True, "codex", "/x", "t", "codex"))
        return w

    def test_state_change_uses_fast_lane_even_when_unattended(self):
        w = self.window("◆")  # waiting now, was running
        cached = {"hash": "old", "at": 0, "state": "running", "tasks": []}
        self.assertEqual(tb.wants_summary(w, cached, "new", 1000, allow_llm=False, allow_fast=True, attended=False), "fast")
        self.assertEqual(tb.wants_summary(w, cached, "new", 1000, allow_llm=False, allow_fast=False, attended=False), "")
        # A flapping badge gets one fast-lane call per window per cooldown.
        recent = {**cached, "fast_at": 900}
        self.assertEqual(tb.wants_summary(w, recent, "new", 1000, allow_llm=False, allow_fast=True, attended=True), "")

    def test_unattended_ignores_plain_content_changes(self):
        w = self.window("")
        cached = {"hash": "old", "at": 0, "state": "idle", "tasks": []}
        self.assertEqual(tb.wants_summary(w, cached, "new", 1000, allow_llm=True, allow_fast=True, attended=False), "")
        self.assertEqual(tb.wants_summary(w, cached, "new", 1000, allow_llm=True, allow_fast=True, attended=True), "full")

    def test_unchanged_content_never_calls(self):
        w = self.window("")
        cached = {"hash": "same", "at": 0, "state": "idle", "tasks": []}
        self.assertEqual(tb.wants_summary(w, cached, "same", 1000, allow_llm=True, allow_fast=True, attended=True), "")

    def test_settle_uses_content_change_time_not_redraws(self):
        w = self.window("")
        w.activity = 1000  # tmux says "just redrawn"
        cached = {"hash": "old", "at": 0, "state": "idle", "tasks": [], "changed_at": 800}
        self.assertEqual(tb.wants_summary(w, cached, "new", 1000, allow_llm=True, allow_fast=True, attended=True), "full")


class GroupTests(unittest.TestCase):
    def test_groups(self):
        self.assertEqual(tb.group_of("waiting", []), "attention")
        self.assertEqual(tb.group_of("idle", [{"next": "已完成，待你决定是否继续"}]), "attention")
        self.assertEqual(tb.group_of("idle", [{"next": "已完成，待查看结果"}]), "review")
        self.assertEqual(tb.group_of("running", [{"next": "等你确认"}]), "attention")
        self.assertEqual(tb.group_of("running", []), "working")
        self.assertEqual(tb.group_of("idle", []), "parked")


class CoverageTests(unittest.TestCase):
    def test_skipped_assistant_panes_get_a_title_task_in_pane_order(self):
        w = window()
        w.panes.append(tb.Pane("%1", True, "codex", "/x", "first", "codex"))
        w.panes.append(tb.Pane("%2", False, "zsh", "/x", "", ""))
        w.panes.append(tb.Pane("%3", False, "codex", "/x", "third", "codex"))
        tasks = tb.cover_panes(w, [
            {"summary": "s3", "next": "n", "change": "", "panes": ["%3", "%99"]},   # %99 belongs to another window
            {"summary": "foreign", "next": "n", "change": "", "panes": ["%98"]},   # entirely another window's
        ])
        self.assertEqual([(t["summary"], t["panes"]) for t in tasks], [("first", ["%1"]), ("s3", ["%3"])])


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
        self.assertEqual([r["group"] for r in board["windows"]], ["attention", "working", "parked"])  # tmux order, group is a label
        # Without a codex call the cache only records content recency, no summaries.
        self.assertEqual(sorted(cache), ["@1", "@2", "@3"])
        self.assertTrue(all("tasks" not in e and e["changed_at"] == 1000 for e in cache.values()))
        self.assertEqual([r["changed_at"] for r in board["windows"]], [1000, 1000, 1000])

    def test_llm_only_for_settled_changed_windows(self):
        settled = window(id="@1", badge="", activity=0)
        settled.panes.append(tb.Pane("%1", True, "codex", "/x", "t", "codex"))
        busy = window(id="@2", badge="●", activity=0)
        busy.panes.append(tb.Pane("%2", True, "codex", "/x", "t", "codex"))
        with mock.patch.object(tb, "ask_codex", return_value={"@1": {"tasks": [{"summary": "s", "next": "等你确认", "change": "从运行中变为等待确认", "panes": ["%1"]}], "name": ""}}) as ask:
            busy_cache = {"@2": {"hash": "old", "at": 990, "state": "running", "tasks": [{"summary": "b", "next": "n", "panes": []}]}}  # summarised 10 s ago
            board, cache, called = tb.build_board([settled, busy], busy_cache, now=1000, allow_llm=True)
            self.assertTrue(called)
            self.assertEqual([p["id"] for p in ask.call_args[0][0]], ["@1"])  # busy window waits for its 2-minute refresh
        self.assertEqual(cache["@1"]["tasks"][0]["summary"], "s")
        self.assertEqual(cache["@1"]["tasks"][0]["changed_at"], 1000)
        self.assertEqual(cache["@2"]["tasks"][0]["summary"], "b")
        self.assertEqual(board["windows"][0]["id"], "@1")
        self.assertEqual(board["windows"][0]["group"], "attention")  # idle + "等你" -> needs you
        # Changed scrollback, model says nothing changed: the previous summaries were sent and the old note is kept.
        settled.panes[0].title = "t2"
        with mock.patch.object(tb, "capture", side_effect=lambda pane_id: "new tail"):
            with mock.patch.object(tb, "ask_codex", return_value={"@1": {"tasks": [{"summary": "s", "next": "等你确认", "change": "", "panes": ["%1"]}], "name": ""}}) as ask:
                _, cache, _ = tb.build_board([settled], cache, now=3000, allow_llm=True)
                self.assertEqual(ask.call_args[0][0][0]["previous"][0]["summary"], "s")
        self.assertEqual(cache["@1"]["tasks"][0]["change"], "从运行中变为等待确认")
        self.assertEqual(cache["@1"]["tasks"][0]["changed_at"], 1000)
        # Same scrollback again: served from cache, no call.
        with mock.patch.object(tb, "capture", side_effect=lambda pane_id: "new tail"):
            with mock.patch.object(tb, "ask_codex") as ask:
                _, _, called = tb.build_board([settled], cache, now=4000, allow_llm=True)
                self.assertFalse(called)
                ask.assert_not_called()

    def test_failed_call_counts_as_full_and_blocks_the_fast_lane(self):
        settled = window(id="@1", index=1, badge="◆", activity=0)
        settled.panes.append(tb.Pane("%1", True, "codex", "/x", "t", "codex"))
        cache = {"@1": {"hash": "old", "at": 0, "state": "running", "tasks": []}}
        with mock.patch.object(tb, "ask_codex", side_effect=tb.codex_batch.CodexError("model not supported")):
            board, cache, called = tb.build_board([settled], cache, now=1000, allow_llm=False, allow_fast=True)
        self.assertEqual(called, "full")
        self.assertEqual(board["error"], "model not supported")
        self.assertEqual(cache["@1"]["fast_at"], 1000)
        self.assertEqual(cache["@1"]["tasks"], [])  # nothing overwritten
        with mock.patch.object(tb, "ask_codex") as ask:
            _, _, called = tb.build_board([settled], cache, now=1100, allow_llm=False, allow_fast=True)
        ask.assert_not_called()  # same state mismatch, but the window is on cooldown
        self.assertEqual(called, "")


if __name__ == "__main__":
    unittest.main()
