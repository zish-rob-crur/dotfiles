#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


TMUX_DIR = Path(__file__).parent
sys.path.insert(0, str(TMUX_DIR))
SESSION_ID = "019fabb6-9c8c-7ed3-9572-dc96381eabff"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "codex_idle_parker", TMUX_DIR / "codex-idle-parker.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PARKER = load_module()


def pane() -> dict[str, str]:
    return {
        "pane_id": "%9",
        "pane_pid": "100",
        "command": "codex",
        "title": "Codex",
        "path": "/tmp/project",
        "session_name": "main",
        "window_id": "@4",
        "window_index": "2",
        "pane_index": "1",
        "window_name": "project",
        "socket_path": "/tmp/tmux.sock",
        "server_pid": "999",
    }


def candidate() -> object:
    return PARKER.Candidate(
        pane=pane(),
        process_pid="200",
        process_start=100.0,
        exact_words=["codex", "resume", SESSION_ID],
        session_id=SESSION_ID,
        cwd="/tmp/project",
        source="current command",
        last_activity=200.0,
    )


class ComposerTests(unittest.TestCase):
    def test_empty_composer_is_idle(self) -> None:
        self.assertTrue(
            PARKER.idle_composer_visible("completed output\n\n› \n  gpt-5.6 high")
        )

    def test_work_approval_error_and_draft_are_not_idle(self) -> None:
        panes = (
            "• Working (8s • esc to interrupt)\n› ",
            "Run this command?\n› 1. Yes\n  2. No",
            "■ Error: connection lost\n› ",
            "› keep this unsent draft\n  gpt-5.6 high",
            "› \n2 background terminals",
        )
        for content in panes:
            with self.subTest(content=content):
                self.assertFalse(PARKER.idle_composer_visible(content))


class ActivityDatabaseTests(unittest.TestCase):
    def test_newest_thread_timestamp_wins_across_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            databases = []
            for index, timestamp in enumerate((1_700_000_000, 1_700_000_100)):
                database = root / f"state_{index}.sqlite"
                connection = sqlite3.connect(database)
                connection.execute(
                    "CREATE TABLE threads (id TEXT PRIMARY KEY, updated_at INTEGER, updated_at_ms INTEGER)"
                )
                connection.execute(
                    "INSERT INTO threads VALUES (?, ?, ?)",
                    (SESSION_ID, timestamp, timestamp * 1000),
                )
                connection.commit()
                connection.close()
                databases.append(database)

            with (
                mock.patch.object(
                    PARKER, "resolve_codex_locations", return_value=(root, root)
                ),
                mock.patch.object(
                    PARKER, "codex_state_databases", return_value=databases
                ),
            ):
                self.assertEqual(
                    PARKER.thread_activity_epoch(SESSION_ID), 1_700_000_100
                )


class CandidateTests(unittest.TestCase):
    def test_old_verified_session_becomes_candidate(self) -> None:
        current_pane = pane()
        with (
            mock.patch.object(
                PARKER.RESTART,
                "current_assistant_process",
                return_value=("200", ["codex", "resume", SESSION_ID]),
            ),
            mock.patch.object(
                PARKER.RESTART, "process_lstart_epoch", return_value=100.0
            ),
            mock.patch.object(PARKER, "thread_activity_epoch", return_value=200.0),
        ):
            resolved = PARKER.resolve_candidate(
                current_pane, {"100": [("200", "codex")]}, 1000.0, 300
            )
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.session_id, SESSION_ID)

    def test_recent_session_is_not_candidate(self) -> None:
        with (
            mock.patch.object(
                PARKER.RESTART,
                "current_assistant_process",
                return_value=("200", ["codex", "resume", SESSION_ID]),
            ),
            mock.patch.object(
                PARKER.RESTART, "process_lstart_epoch", return_value=900.0
            ),
            mock.patch.object(PARKER, "thread_activity_epoch", return_value=950.0),
        ):
            resolved = PARKER.resolve_candidate(
                pane(), {"100": [("200", "codex")]}, 1000.0, 300
            )
        self.assertIsNone(resolved)

    def test_new_session_requires_fresh_pane_state(self) -> None:
        with (
            mock.patch.object(
                PARKER.RESTART,
                "current_assistant_process",
                return_value=("200", ["codex", "--yolo"]),
            ),
            mock.patch.object(
                PARKER.RESTART, "process_lstart_epoch", return_value=100.0
            ),
            mock.patch.object(
                PARKER.RESTART, "saved_session_id", return_value=("", "")
            ),
        ):
            self.assertIsNone(
                PARKER.resolve_candidate(pane(), {}, 1000.0, 300)
            )


class ScanAndParkTests(unittest.TestCase):
    def test_focused_pane_is_skipped_before_resolution(self) -> None:
        with (
            mock.patch.object(PARKER, "focused_panes", return_value={"%9"}),
            mock.patch.object(PARKER.RESTART, "process_rows", return_value=[]),
            mock.patch.object(PARKER.RESTART, "list_panes", return_value=[pane()]),
            mock.patch.object(PARKER, "resolve_candidate") as resolve,
        ):
            result = PARKER.scan_once(300, apply=True, now=1000.0)
        self.assertEqual(result, PARKER.ScanResult(1, 0, 0, 1))
        resolve.assert_not_called()

    def test_dry_run_lists_candidate_without_respawning(self) -> None:
        selected = candidate()
        output = StringIO()
        with (
            mock.patch.object(PARKER, "focused_panes", return_value=set()),
            mock.patch.object(PARKER, "pane_is_idle", return_value=True),
            mock.patch.object(PARKER.RESTART, "process_rows", return_value=[]),
            mock.patch.object(PARKER.RESTART, "list_panes", return_value=[pane()]),
            mock.patch.object(PARKER, "resolve_candidate", return_value=selected),
            mock.patch.object(PARKER, "park_candidate") as park,
            redirect_stdout(output),
        ):
            result = PARKER.scan_once(300, apply=False, now=1000.0)
        self.assertEqual(result, PARKER.ScanResult(1, 1, 0, 0))
        self.assertIn("would park main:2.1", output.getvalue())
        park.assert_not_called()

    def test_apply_revalidates_and_respawns_to_resume_card(self) -> None:
        selected = candidate()
        with (
            mock.patch.object(
                PARKER.RESTART, "pane_respawn_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                PARKER.RESTART, "usable_dir", return_value="/tmp/project"
            ),
            mock.patch.object(PARKER, "candidate_still_safe", return_value=""),
            mock.patch.object(PARKER.RESTART, "tmux", return_value="") as tmux,
        ):
            self.assertEqual(PARKER.park_candidate(selected, 1000.0, 300), "")

        args = tmux.call_args.args[0]
        self.assertEqual(args[:6], ["respawn-pane", "-k", "-t", "%9", "-c", "/tmp/project"])
        self.assertIn(f"codex resume {SESSION_ID}", args[-1])
        self.assertIn(f"cr {SESSION_ID}", args[-1])

    def test_final_recheck_blocks_changed_thread(self) -> None:
        selected = candidate()
        with (
            mock.patch.object(PARKER, "focused_panes", return_value=set()),
            mock.patch.object(PARKER, "pane_is_idle", return_value=True),
            mock.patch.object(PARKER, "thread_activity_epoch", return_value=950.0),
            mock.patch.object(
                PARKER.RESTART, "process_identity_matches", return_value=True
            ),
        ):
            self.assertEqual(
                PARKER.candidate_still_safe(selected, 1000.0, 300),
                "thread became active",
            )


if __name__ == "__main__":
    unittest.main()
