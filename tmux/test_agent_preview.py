import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_preview as ap  # noqa: E402
import assistant_completion_state as acs  # noqa: E402


class TrimFooterTests(unittest.TestCase):
    def test_strips_codex_composer_and_status_while_keeping_markdown_rule(self):
        raw = "\n".join([
            "Done updating config.",
            "---",
            "All 3 checks passed.",
            "",
            "\033[2m────────────────────────────────────────\033[0m",
            "› Explain this codebase",
            "\033[2m────────────────────────────────────────\033[0m",
            "  gpt-5.4 high · 72% context left · ~/GitHubRepos/dotfiles",
            "",
        ])
        trimmed = ap.trim_tui_footer(raw)
        self.assertEqual(trimmed, ["Done updating config.", "---", "All 3 checks passed."])

    def test_strips_claude_composer_and_permissions_footer(self):
        raw = "\n".join([
            "Patched auth handler.",
            "────────────────────────────────────────",
            "❯\xa0",
            "────────────────────────────────────────",
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
            "  ? for shortcuts",
        ])
        trimmed = ap.trim_tui_footer(raw)
        self.assertEqual(trimmed, ["Patched auth handler."])

    def test_strips_box_prompt_and_preserves_short_output(self):
        raw = "\n".join([
            "Done.",
            "╭────────────────────╮",
            "│ >                  │",
            "╰────────────────────╯",
            "",
        ])
        self.assertEqual(ap.trim_tui_footer(raw), ["Done."])

    def test_strips_codex_wrapped_status_and_antigravity_prompt(self):
        codex_raw = "\n".join([
            "• 已推送，已确认远端 origin/main。",
            "",
            "› Ask Codex to do anything",
            "",
            "  GPT-6.2-Astra medium · ~/GitlabRepos/s-eleven-mono/services/seleven-mcp · main · Context",
            "  87% left · weekly 100% left",
        ])
        self.assertEqual(ap.trim_tui_footer(codex_raw), ["• 已推送，已确认远端 origin/main。"])

        agy_raw = "\n".join([
            "• 单元测试全部通过。",
            "",
            "────────────────────────────────────────",
            ">                                       ",
        ])
        self.assertEqual(ap.trim_tui_footer(agy_raw), ["• 单元测试全部通过。"])

        agy_boxed = "\n".join([
            "Done fixing bug.",
            "────────────────────────────────────────",
            "> draft text",
            "────────────────────────────────────────",
        ])
        self.assertEqual(ap.trim_tui_footer(agy_boxed), ["Done fixing bug."])

    def test_falls_back_when_screen_contains_only_chrome(self):
        raw = "───────\n›\n  ? for shortcuts\n"
        self.assertEqual(ap.trim_tui_footer(raw), ["───────", "›", "  ? for shortcuts"])


class AnsiToHtmlTests(unittest.TestCase):
    def test_escapes_html_and_converts_sgr_colors(self):
        lines = [
            "\033[1;32mOK\033[0m <script>&test</script>",
            "\033[38;2;167;192;128mtruecolor\033[38;5;196mx256\033[0m\033[K",
        ]
        rendered = ap.ansi_to_html(lines)
        self.assertIn('<span style="color:#a7c080;font-weight:600">OK</span>', rendered)
        self.assertIn("&lt;script&gt;&amp;test&lt;/script&gt;", rendered)
        self.assertIn('<span style="color:#a7c080">truecolor</span>', rendered)
        self.assertIn('<span style="color:#ff0000">x256</span>', rendered)
        self.assertNotIn("\033[", rendered)

    def test_reflow_duration_window_name_and_linkify(self):
        raw_lines = [
            "  • 原始报告 (\033[32martifacts/voice-bot-event-\033[0m",
            "    \033[32mrecurrence/olaf-2026-11-17/report.html\033[0m) 保留原件。",
            "",
            "  复现 36/88 → 88/88 还缺一份数据。那组静默实验使用了 11 个冻结检查点。Olaf 的分支里",
            "  有重放代码和预期结果，但本地目录里没找到原始模型请求。",
            "  详见 https://example.com/pr/42 与 Go/Python 或 origin/main。",
            "  ```python",
            "  x = 1",
            "  y = 2",
            "  ```",
            "",
            "  Worked for 1m 46s • 11:01",
        ]
        trimmed, duration = ap.extract_turn_duration(raw_lines)
        self.assertEqual(duration, "1m 46s")
        reflowed = ap.reflow_prose(trimmed)
        plain = "\n".join(ap.strip_ansi(ln) for ln in reflowed)
        self.assertIn("artifacts/voice-bot-event-recurrence/olaf-2026-11-17/report.html", plain)
        self.assertIn("Olaf 的分支里有重放代码和预期结果", plain)
        self.assertIn("x = 1\ny = 2", plain)
        self.assertNotIn("Worked for", plain)

        rendered = ap.ansi_to_html(reflowed)
        self.assertIn('data-path="artifacts/voice-bot-event-recurrence/olaf-2026-11-17/report.html"', rendered)
        self.assertIn('data-url="https://example.com/pr/42"', rendered)
        self.assertNotIn('data-path="36/88"', rendered)
        self.assertNotIn('data-path="Go/Python"', rendered)
        self.assertNotIn('data-path="origin/main"', rendered)

        self.assertEqual(ap.clean_window_name(" calle:voice-bot"), "calle:voice-bot")
        self.assertEqual(ap.clean_window_name("dotfiles"), "dotfiles")

    def test_open_local_path_routes_html_and_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "artifacts" / "report.html"
            report.parent.mkdir()
            report.write_text("<h1>ok</h1>", encoding="utf-8")
            script = Path(tmp) / "app.py"
            script.write_text("print(1)\n", encoding="utf-8")

            with patch.object(ap.subprocess, "run") as m_run:
                self.assertTrue(ap.open_local_path(tmp, "artifacts/report.html"))
                m_run.assert_called_once()
                self.assertEqual(m_run.call_args.args[0], ["open", str(report.resolve())])

            with patch.object(ap.shutil, "which", return_value="/opt/homebrew/bin/neovide"), \
                 patch.object(ap.subprocess, "Popen") as m_popen:
                self.assertTrue(ap.open_local_path(tmp, "app.py:12"))
                m_popen.assert_called_once()
                self.assertEqual(m_popen.call_args.args[0], ["/opt/homebrew/bin/neovide", str(script.resolve()), "+12"])


class SnapshotAndAckTests(unittest.TestCase):
    def test_snapshot_orders_unread_and_skips_dead_or_working_panes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sock = str(root / "tmux.sock")
            Path(sock).touch()
            sdir = acs.ensure_server_state_dir(root, sock)
            self.assertIsNotNone(sdir)

            records = {
                "pane-1.json": {
                    "pane_id": "%1", "session_name": "main", "source": "claude",
                    "unread": True, "completed_at_ns": 100_000_000_000,
                },
                "pane-2.json": {
                    "pane_id": "%2", "session_name": "main", "source": "codex",
                    "unread": True, "completed_at_ns": 200_000_000_000,
                },
                "pane-3.json": {
                    "pane_id": "%3", "session_name": "main", "source": "codex",
                    "unread": True, "completed_at_ns": 300_000_000_000,  # spinning/working
                },
                "pane-4.json": {
                    "pane_id": "%4", "session_name": "main", "source": "agy",
                    "unread": True, "completed_at_ns": 400_000_000_000,  # dead pane
                },
                "pane-5.json": {
                    "pane_id": "%5", "session_name": "main", "source": "agy",
                    "unread": False, "completed_at_ns": 50_000_000_000,
                },
            }
            for name, payload in records.items():
                (sdir / name).write_text(json.dumps(payload), encoding="utf-8")

            board = root / "board.json"
            board.write_text(json.dumps({
                "windows": [{
                    "id": "@2", "repo": "dotfiles", "branch": "main",
                    "tasks": [{"summary": "Build preview", "next": "Verify HUD", "panes": ["%2"]}],
                }],
            }), encoding="utf-8")

            live_panes = {
                ("main", "%1"): {
                    "session_name": "main", "session_group": "", "window_id": "@1",
                    "window_index": "1", "window_name": "api", "pane_id": "%1",
                    "command": "claude", "title": "✳ api fix", "path": "/tmp/api", "agy_state": "",
                },
                ("main", "%2"): {
                    "session_name": "main", "session_group": "", "window_id": "@2",
                    "window_index": "2", "window_name": "dotfiles", "pane_id": "%2",
                    "command": "codex", "title": "codex", "path": "/tmp/dotfiles", "agy_state": "",
                },
                ("main", "%3"): {
                    "session_name": "main", "session_group": "", "window_id": "@3",
                    "window_index": "3", "window_name": "busy", "pane_id": "%3",
                    "command": "codex", "title": "⠋ working", "path": "/tmp/busy", "agy_state": "",
                },
                ("main", "%5"): {
                    "session_name": "main", "session_group": "", "window_id": "@5",
                    "window_index": "5", "window_name": "agy-win", "pane_id": "%5",
                    "command": "agy", "title": "agy", "path": "/tmp/agy", "agy_state": "",
                },
            }

            def fake_run_tmux(s, *args, **kwargs):
                if args and args[0] == "capture-pane":
                    pane_target = args[-1]
                    return f"Result from {pane_target}\n──────────\n›\n"
                return ""

            with patch.object(ap, "discover_live_servers", return_value=[(acs.canonical_socket(sock), sdir)]), \
                 patch.object(ap, "list_live_panes", return_value=(live_panes, {k[1]: v for k, v in live_panes.items()})), \
                 patch.object(ap, "run_tmux", side_effect=fake_run_tmux):
                snap = ap.build_snapshot(root, board, lines=80)

            self.assertEqual([item["pane_id"] for item in snap["unread"]], ["%2", "%1"])
            self.assertEqual(snap["unread"][0]["source_label"], "Codex")
            self.assertEqual(snap["unread"][0]["repo"], "dotfiles")
            self.assertEqual(snap["unread"][0]["summary"], "Build preview -> Verify HUD")
            self.assertEqual(snap["unread"][0]["compact_html"], "Result from %2")
            self.assertEqual([item["pane_id"] for item in snap["recent"]], ["%5"])
            self.assertEqual(snap["recent"][0]["source_label"], "Antigravity")

    def test_ack_clears_unread_even_when_pane_is_unfocused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sock = str(root / "tmux.sock")
            Path(sock).touch()
            sdir = acs.ensure_server_state_dir(root, sock)
            pane_file = sdir / "pane-12.json"
            pane_file.write_text(json.dumps({
                "pane_id": "%12",
                "unread": True,
                "completed_at_ns": 12345,
            }), encoding="utf-8")

            with patch.object(ap, "refresh_badges") as refresh:
                changed = ap.ack_pane(root, sock, "%12")
            self.assertTrue(changed)
            refresh.assert_called_once_with(sock)
            updated = json.loads(pane_file.read_text(encoding="utf-8"))
            self.assertFalse(updated["unread"])


if __name__ == "__main__":
    unittest.main()
