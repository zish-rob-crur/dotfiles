#!/usr/bin/env python3

from __future__ import annotations

import json
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TMUX_DIR = Path(__file__).parent
sys.path.insert(0, str(TMUX_DIR))

import codex_session_titles as TITLES  # noqa: E402


def load_layout_module():
    path = TMUX_DIR / "pane-layout-icons-refresh.py"
    spec = importlib.util.spec_from_file_location("pane_layout_icons_refresh", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LAYOUT = load_layout_module()


SESSION_ID = "019fabb6-9c8c-7ed3-9572-dc96381eabff"
OTHER_ID = "019fabb6-9c8c-7ed3-9572-dc96381eac00"


def pane() -> dict[str, str]:
    return {
        "pane_id": "%9",
        "pane_pid": "100",
        "command": "codex",
        "title": "project",
        "path": "/tmp/project",
        "session_name": "main",
        "window_id": "@4",
        "window_index": "2",
        "pane_index": "1",
        "window_name": "project",
        "socket_path": "/tmp/tmux.sock",
        "server_pid": "999",
    }


class SessionIndexTests(unittest.TestCase):
    def test_last_picker_title_wins_and_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = (
                {"id": SESSION_ID, "thread_name": "old title"},
                {"id": SESSION_ID, "thread_name": "  renamed\tthread\n"},
            )
            (root / "session_index.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            self.assertEqual(
                TITLES.load_session_index_titles(root),
                {SESSION_ID: "renamed thread"},
            )


class DatabaseTests(unittest.TestCase):
    def test_explicit_name_precedes_picker_and_legacy_title(self) -> None:
        record = TITLES.ThreadRecord(
            SESSION_ID,
            "/tmp/project",
            "my session",
            "legacy title",
            100.0,
            200.0,
        )
        self.assertEqual(
            TITLES.display_titles(
                {SESSION_ID: record}, {SESSION_ID: "picker title"}
            )[SESSION_ID],
            "my session",
        )

    def test_current_sqlite_schema_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                """CREATE TABLE threads (
                    id TEXT PRIMARY KEY, cwd TEXT, name TEXT, title TEXT,
                    created_at INTEGER, created_at_ms INTEGER,
                    updated_at INTEGER, updated_at_ms INTEGER,
                    recency_at INTEGER, recency_at_ms INTEGER
                )"""
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    SESSION_ID,
                    "/tmp/project",
                    "named thread",
                    "generated title",
                    100,
                    100_500,
                    200,
                    200_500,
                    300,
                    300_500,
                ),
            )
            connection.commit()
            connection.close()

            records = TITLES.load_thread_records(root)
            self.assertEqual(records[SESSION_ID].name, "named thread")
            self.assertEqual(records[SESSION_ID].created_at, 100.5)
            self.assertEqual(records[SESSION_ID].recency_at, 300.5)


class ResolutionTests(unittest.TestCase):
    def test_verified_resume_id_is_used(self) -> None:
        current = pane()
        with (
            mock.patch.object(TITLES.RESTART, "process_rows", return_value=[]),
            mock.patch.object(TITLES.RESTART, "children_by_parent", return_value={}),
            mock.patch.object(TITLES, "process_start_times", return_value={"200": 100.0}),
            mock.patch.object(TITLES.RESTART, "pane_tool", return_value="codex"),
            mock.patch.object(
                TITLES.RESTART,
                "current_assistant_process",
                return_value=("200", ["codex", "resume", SESSION_ID]),
            ),
        ):
            self.assertEqual(
                TITLES.resolve_live_thread_ids([current], {}),
                {"%9": SESSION_ID},
            )

    def test_picker_resume_uses_only_unique_recent_cwd_match(self) -> None:
        current = pane()
        record = TITLES.ThreadRecord(
            SESSION_ID,
            "/tmp/project",
            "named thread",
            "",
            10.0,
            150.0,
        )
        with (
            mock.patch.object(TITLES.RESTART, "process_rows", return_value=[]),
            mock.patch.object(TITLES.RESTART, "children_by_parent", return_value={}),
            mock.patch.object(TITLES, "process_start_times", return_value={"200": 100.0}),
            mock.patch.object(TITLES.RESTART, "pane_tool", return_value="codex"),
            mock.patch.object(
                TITLES.RESTART,
                "current_assistant_process",
                return_value=("200", ["codex", "resume"]),
            ),
            mock.patch.object(
                TITLES.RESTART, "saved_session_id", return_value=("", "")
            ),
        ):
            self.assertEqual(
                TITLES.resolve_live_thread_ids([current], {SESSION_ID: record}),
                {"%9": SESSION_ID},
            )

    def test_picker_resume_rejects_ambiguous_cwd_matches(self) -> None:
        current = pane()
        records = {
            thread_id: TITLES.ThreadRecord(
                thread_id,
                "/tmp/project",
                title,
                "",
                10.0,
                recency,
            )
            for thread_id, title, recency in (
                (SESSION_ID, "first", 150.0),
                (OTHER_ID, "second", 160.0),
            )
        }
        with (
            mock.patch.object(TITLES.RESTART, "process_rows", return_value=[]),
            mock.patch.object(TITLES.RESTART, "children_by_parent", return_value={}),
            mock.patch.object(TITLES, "process_start_times", return_value={"200": 100.0}),
            mock.patch.object(TITLES.RESTART, "pane_tool", return_value="codex"),
            mock.patch.object(
                TITLES.RESTART,
                "current_assistant_process",
                return_value=("200", ["codex", "resume"]),
            ),
            mock.patch.object(
                TITLES.RESTART, "saved_session_id", return_value=("", "")
            ),
        ):
            self.assertEqual(TITLES.resolve_live_thread_ids([current], records), {})


class PaneLabelTests(unittest.TestCase):
    def test_codex_session_title_replaces_project_title_in_label(self) -> None:
        current = LAYOUT.Pane(
            window_id="@4",
            pane_id="%9",
            active=True,
            left=0,
            top=0,
            width=100,
            height=30,
            command="codex",
            title="project",
            session_title="Fix editor race",
            path="/tmp/project",
        )

        active, inactive = LAYOUT.render_pane_labels(current)
        self.assertIn("Fix editor race", active)
        self.assertIn("Fix editor race", inactive)
        self.assertNotIn("project · project", active)


if __name__ == "__main__":
    unittest.main()
