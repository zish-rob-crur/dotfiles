#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import todo_notes as tn  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_item_fields(self):
        item = tn.parse_item("work", Path("w.md"), 3, "- [ ] ⏫ 确认 MR 213 的合并策略 @calle:goal-fix #群:agentic 📅 2026-09-09 (from 2026-W36)")
        assert item
        self.assertEqual(item.text, "确认 MR 213 的合并策略")
        self.assertEqual(item.tags, ["calle:goal-fix"])
        self.assertEqual(item.sources, ["群:agentic"])
        self.assertEqual(item.due, "2026-09-09")
        self.assertTrue(item.priority)
        self.assertEqual(item.from_week, "2026-W36")
        self.assertFalse(item.done)

    def test_done_and_non_items(self):
        done = tn.parse_item("p", Path("w.md"), 1, "- [x] 重跑导出 @airudder ✅ 2026-09-08")
        assert done
        self.assertTrue(done.done)
        self.assertEqual(done.text, "重跑导出")
        self.assertIsNone(tn.parse_item("p", Path("w.md"), 2, "# heading"))

    def test_hidden_comments_are_dropped_but_links_stay(self):
        item = tn.parse_item("w", Path("w.md"), 4, "- [ ] 排查丢失 [飞书原消息](https://x.feishu.cn/a?b=1&c=2) <!-- feishu:om_1 --> #飞书")
        self.assertEqual(item.text, "排查丢失 [飞书原消息](https://x.feishu.cn/a?b=1&c=2)")
        self.assertEqual(item.sources, ["飞书"])
        self.assertIsNone(tn.parse_item("p", Path("w.md"), 3, "- plain bullet"))


class SectionTests(unittest.TestCase):
    def test_items_take_the_nearest_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "w.md"
            f.write_text("# 2026-W37\n\n- [ ] top\n\n## CALLE\n- [ ] one\n### sub\n- [ ] two\n", encoding="utf-8")
            items = tn.parse_file("work", f)
            self.assertEqual([(i.text, i.section) for i in items], [("top", ""), ("one", "CALLE"), ("two", "sub")])


class WeekTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = tn.Vault("work", Path(self.tmp.name), "Todo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_week_ids(self):
        self.assertEqual(tn.week_id(dt.date(2026, 9, 8)), "2026-W37")
        self.assertEqual(tn.previous_week("2026-W37"), "2026-W36")
        self.assertEqual(tn.week_range("2026-W37"), (dt.date(2026, 9, 7), dt.date(2026, 9, 13)))

    def test_rollover_carries_unfinished_items_once(self):
        last = self.vault.week_file("2026-W36")
        last.parent.mkdir(parents=True)
        last.write_text("# 2026-W36\n\n- [ ] 未完成 @calle\n- [x] 已完成 ✅ 2026-09-05\n\n## CALLE\n- [ ] 更早的 (from 2026-W35)\n", encoding="utf-8")
        target = tn.ensure_week(self.vault, "2026-W37")
        text = target.read_text(encoding="utf-8")
        self.assertIn("# 2026-W37  09-07 ~ 09-13", text)
        self.assertIn("- [ ] 未完成 @calle (from 2026-W36)", text)
        self.assertIn("## CALLE\n- [ ] 更早的 (from 2026-W35)", text)
        self.assertNotIn("已完成", text)
        self.assertEqual([i.section for i in tn.parse_file("work", target)], ["", "CALLE"])
        # Existing file is left alone.
        target.write_text("custom", encoding="utf-8")
        self.assertEqual(tn.ensure_week(self.vault, "2026-W37").read_text(), "custom")

    def test_add_and_toggle(self):
        week = tn.week_id()
        tn.add_item(self.vault, "写周报 @dotfiles")
        items = tn.parse_file("work", self.vault.week_file(week))
        self.assertEqual([i.text for i in items], ["写周报"])
        self.assertTrue(tn.toggle_item(self.vault, items[0].line, today=dt.date(2026, 9, 8)))
        self.assertIn("- [x] 写周报 @dotfiles ✅ 2026-09-08", self.vault.week_file(week).read_text())
        self.assertFalse(tn.toggle_item(self.vault, items[0].line))
        self.assertIn("- [ ] 写周报 @dotfiles\n", self.vault.week_file(week).read_text())


if __name__ == "__main__":
    unittest.main()
