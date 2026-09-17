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


FS = "https://applink.feishu.cn/client/chat/open?openChatId=oc_1&position=34"
DOC = "https://x.feishu.cn/wiki/Abc"


class LinkTests(unittest.TestCase):
    def test_inline_links_move_to_a_reference_block(self):
        lines = [
            "# 2026-W38",
            "",
            "## CALLE",
            f"- [ ] 排查丢失 @calle #飞书 [飞书原消息]({FS}) <!-- feishu:om_x100b651ebdb6 --> · [复现 case]({DOC}) (from 2026-W37)",
            "- [ ] 没有链接的事项",
            "[注意]: 这不是链接定义",
            "",
        ]
        tidied = tn.tidy_lines(lines)
        self.assertEqual(tidied[3], "- [ ] 排查丢失 @calle #飞书 [飞书原消息][fs-1ebdb6] · [复现 case][l-" + tidied[3].split("[l-")[1])
        self.assertIn("[注意]: 这不是链接定义", tidied)
        self.assertEqual(tidied[-2], f'[fs-1ebdb6]: {FS} "feishu:om_x100b651ebdb6"')
        self.assertTrue(tidied[-1].startswith("[l-") and tidied[-1].endswith(DOC))
        self.assertEqual(tn.tidy_lines(tidied), tidied)  # idempotent

    def test_hidden_id_goes_to_the_feishu_message_link_before_it(self):
        line = f"- [ ] x [飞书原消息]({FS}) · [复现 case]({DOC}) <!-- feishu:om_1111111 --> [飞书原消息]({FS}2) <!-- feishu:om_2222222 -->"
        tidied = tn.tidy_lines([line])
        self.assertTrue(tidied[0].startswith("- [ ] x [飞书原消息][fs-111111] · [复现 case][l-"), tidied[0])
        self.assertTrue(tidied[0].endswith("[飞书原消息][fs-222222]"), tidied[0])
        self.assertIn(f'[fs-111111]: {FS} "feishu:om_1111111"', tidied)
        self.assertIn(f'[fs-222222]: {FS}2 "feishu:om_2222222"', tidied)

    def test_same_url_reuses_its_label_and_unused_definitions_are_dropped(self):
        lines = [f"- [ ] a [飞书原消息]({FS}) <!-- feishu:om_1abcdef -->", f"- [ ] b [飞书原消息]({FS})",
                 "", f"[old]: {DOC}"]
        tidied = tn.tidy_lines(lines)
        self.assertEqual(tidied[:2], ["- [ ] a [飞书原消息][fs-abcdef]", "- [ ] b [飞书原消息][fs-abcdef]"])
        self.assertEqual(tidied[2:], ["", f'[fs-abcdef]: {FS} "feishu:om_1abcdef"'])

    def test_label_grows_on_collision(self):
        lines = [f"- [ ] a [m](https://a.example) <!-- feishu:om_1aaaaaa -->",
                 f"- [ ] b [m](https://b.example) <!-- feishu:om_2aaaaaa -->"]
        tidied = tn.tidy_lines(lines)
        self.assertIn("[m][fs-aaaaaa]", tidied[0])
        self.assertIn("[m][fs-2aaaaaa]", tidied[1])

    def test_parsed_text_resolves_references_for_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "w.md"
            file.write_text(f"- [ ] 排查 [飞书原消息][fs-1] #飞书\n\n[fs-1]: {FS} \"feishu:om_1\"\n", encoding="utf-8")
            (item,) = tn.parse_file("work", file)
            self.assertEqual(item.text, f"排查 [飞书原消息]({FS})")

    def test_rollover_carries_the_definitions_of_unfinished_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = tn.Vault("work", Path(tmp))
            previous = vault.week_file("2026-W37")
            previous.parent.mkdir(parents=True)
            previous.write_text("\n".join([
                "# 2026-W37", "", "## CALLE", "- [ ] 未完成 [飞书原消息][fs-aaaaaa]", "- [x] 已完成 [飞书原消息][fs-bbbbbb] ✅ 2026-09-10",
                "", f'[fs-aaaaaa]: {FS} "feishu:om_aaaaaa"', f"[fs-bbbbbb]: {DOC}",
            ]) + "\n", encoding="utf-8")
            text = tn.ensure_week(vault, "2026-W38").read_text(encoding="utf-8")
            self.assertIn("- [ ] 未完成 [飞书原消息][fs-aaaaaa] (from 2026-W37)", text)
            self.assertIn(f'[fs-aaaaaa]: {FS} "feishu:om_aaaaaa"', text)
            self.assertNotIn(DOC, text)

    def test_add_keeps_the_block_at_the_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = tn.Vault("work", Path(tmp))
            tn.add_item(vault, f"第一件 [飞书原消息]({FS}) <!-- feishu:om_aaaaaa -->")
            target = tn.add_item(vault, f"第二件 [文档]({DOC})")
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[-5:-3], ["- [ ] 第一件 [飞书原消息][fs-aaaaaa]", lines[-4]])
            self.assertTrue(lines[-4].startswith("- [ ] 第二件 [文档][l-"))
            self.assertEqual(lines[-3], "")
            self.assertTrue(lines[-2].startswith("[fs-aaaaaa]: ") and lines[-1].startswith("[l-"))


class NvimArgsTests(unittest.TestCase):
    def test_one_tab_per_vault_with_tab_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = tn.Vault(name="work", path=Path(tmp) / "work vault", todo_folder="Inbox/Todo")
            personal = tn.Vault(name="personal", path=Path(tmp) / "me", todo_folder="Inbox/Todo")
            args = tn.nvim_args([work, personal])
            week = tn.week_id()
            self.assertEqual(args[0], str(work.path / "Inbox/Todo" / f"{week}.md"))
            self.assertEqual(args[1:3], ["-c", f"tcd {tmp}/work\\ vault"])  # spaces escaped for Ex
            self.assertEqual(args[3:5], ["-c", f"tabnew {personal.path}/Inbox/Todo/{week}.md | tcd {personal.path}"])
            self.assertEqual(args[5:], ["-c", "tabfirst"])
            self.assertEqual(tn.nvim_args([personal])[1:], ["-c", f"tcd {personal.path}"])


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
