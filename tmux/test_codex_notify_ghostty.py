#!/usr/bin/env python3

import importlib.util
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import shutil
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest import mock

import codex_notify_common as COMMON


SCRIPT_DIR = Path(__file__).resolve().parent
NOTIFIER_PATH = SCRIPT_DIR / "codex-notify-ghostty.py"
ROUTER_PATH = SCRIPT_DIR / "codex-notify-router.py"
SIDEBAR_PATH = SCRIPT_DIR / "codex-sidebar-bin.sh"
STATE_HELPER_PATH = SCRIPT_DIR / "assistant_completion_state.py"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOTIFIER = load_script("codex_notify_ghostty", NOTIFIER_PATH)
ROUTER = load_script("codex_notify_router", ROUTER_PATH)
STATE = load_script("assistant_completion_state", STATE_HELPER_PATH)


class StateDatabaseMixin:
    temporary_directory: tempfile.TemporaryDirectory
    codex_home: Path
    sqlite_home: Path
    database_path: Path

    def make_modern_database(self, directory: Optional[Path] = None) -> Path:
        target = self.sqlite_home if directory is None else directory
        target.mkdir(parents=True, exist_ok=True)
        database_path = target / "state_5.sqlite"
        with closing(sqlite3.connect(database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    thread_source TEXT,
                    source TEXT
                );
                CREATE TABLE thread_spawn_edges (
                    parent_thread_id TEXT NOT NULL,
                    child_thread_id TEXT NOT NULL PRIMARY KEY,
                    status TEXT NOT NULL
                );
                """
            )
        self.database_path = database_path
        return database_path

    def insert_thread(
        self,
        thread_id: str,
        thread_source: Optional[str],
        source: str,
        database_path: Optional[Path] = None,
    ) -> None:
        path = self.database_path if database_path is None else database_path
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "INSERT INTO threads (id, thread_source, source) VALUES (?, ?, ?)",
                (thread_id, thread_source, source),
            )
            connection.commit()

    @staticmethod
    def notification(thread_id: str) -> dict:
        return {"type": "agent-turn-complete", "thread-id": thread_id}


class SubagentDetectionTests(unittest.TestCase, StateDatabaseMixin):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.codex_home = Path(self.temporary_directory.name) / "codex"
        self.sqlite_home = self.codex_home
        self.make_modern_database()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def classify(self, thread_id: str) -> str:
        return COMMON.classify_thread(
            self.notification(thread_id),
            self.codex_home,
            {},
        )

    def test_modern_schema_classifies_root_and_all_child_markers(self) -> None:
        self.insert_thread("root", "user", "cli")
        self.insert_thread("marker-child", "subagent", "cli")
        self.insert_thread(
            "json-child",
            None,
            json.dumps({"subagent": {"other": "agent_job:123"}}),
        )
        self.insert_thread("edge-child", None, "cli")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                ("root", "edge-child", "completed"),
            )
            connection.commit()

        self.assertEqual(self.classify("root"), COMMON.CLASS_ROOT)
        self.assertEqual(self.classify("marker-child"), COMMON.CLASS_SUBAGENT)
        self.assertEqual(self.classify("json-child"), COMMON.CLASS_SUBAGENT)
        self.assertEqual(self.classify("edge-child"), COMMON.CLASS_SUBAGENT)

    def test_old_schema_classifies_root_and_child_without_edge_table(self) -> None:
        self.database_path.unlink()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT);
                INSERT INTO threads VALUES ('root', 'cli');
                INSERT INTO threads VALUES ('child', '{"subagent":{"other":"legacy"}}');
                """
            )

        self.assertEqual(self.classify("root"), COMMON.CLASS_ROOT)
        self.assertEqual(self.classify("child"), COMMON.CLASS_SUBAGENT)

    def test_spawn_edge_classifies_child_even_if_thread_row_is_absent(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                ("root", "edge-only-child", "completed"),
            )
            connection.commit()
        self.assertEqual(self.classify("edge-only-child"), COMMON.CLASS_SUBAGENT)

    def test_unknown_thread_and_broken_schema_fail_closed(self) -> None:
        self.assertEqual(self.classify("missing"), COMMON.CLASS_UNKNOWN)
        self.database_path.unlink()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("CREATE TABLE unrelated (id TEXT)")
        self.assertEqual(self.classify("root"), COMMON.CLASS_UNKNOWN)

    def test_unknown_and_unreviewed_source_values_fail_closed(self) -> None:
        self.insert_thread("session-unknown", None, "unknown")
        self.insert_thread("custom-session", None, "future-client")
        self.insert_thread("future-thread-source", "future-feature", "cli")
        self.insert_thread("internal", "memory_consolidation", "cli")
        self.insert_thread("empty-thread-source", "", "cli")
        self.insert_thread("blank-thread-source", "   ", "cli")

        for thread_id in (
            "session-unknown",
            "custom-session",
            "future-thread-source",
            "internal",
            "empty-thread-source",
            "blank-thread-source",
        ):
            with self.subTest(thread_id=thread_id):
                self.assertEqual(self.classify(thread_id), COMMON.CLASS_UNKNOWN)

    def test_known_user_automation_and_legacy_sources_are_root(self) -> None:
        self.insert_thread("user", "user", "cli")
        self.insert_thread("automation", "automation", "vscode")
        self.insert_thread("legacy-exec", None, "exec")
        self.insert_thread("legacy-mcp", None, "mcp")

        for thread_id in ("user", "automation", "legacy-exec", "legacy-mcp"):
            with self.subTest(thread_id=thread_id):
                self.assertEqual(self.classify(thread_id), COMMON.CLASS_ROOT)

    def test_code_sqlite_home_environment_has_precedence(self) -> None:
        relocated = Path(self.temporary_directory.name) / "relocated"
        database = self.make_modern_database(relocated)
        self.insert_thread("root", "user", "cli", database)
        classification = COMMON.classify_thread(
            self.notification("root"),
            self.codex_home,
            {"CODEX_SQLITE_HOME": str(relocated)},
        )
        self.assertEqual(classification, COMMON.CLASS_ROOT)

    def test_config_sqlite_home_is_honored_on_python_39(self) -> None:
        original_database = self.database_path
        relocated = Path(self.temporary_directory.name) / "configured-state"
        database = self.make_modern_database(relocated)
        self.insert_thread("root", "user", "cli", database)
        self.codex_home.mkdir(exist_ok=True)
        (self.codex_home / "config.toml").write_text(
            "sqlite_home = %s\n[features]\nfoo = true\n" % json.dumps(str(relocated)),
            encoding="utf-8",
        )
        original_database.unlink()

        self.assertEqual(self.classify("root"), COMMON.CLASS_ROOT)

    def test_relative_config_sqlite_home_is_rooted_at_codex_home(self) -> None:
        original_database = self.database_path
        relocated = self.codex_home / "relative-state"
        database = self.make_modern_database(relocated)
        self.insert_thread("root", "user", "cli", database)
        self.codex_home.mkdir(exist_ok=True)
        (self.codex_home / "config.toml").write_text(
            'sqlite_home = "relative-state"\n',
            encoding="utf-8",
        )
        original_database.unlink()

        outside_cwd = Path(self.temporary_directory.name) / "project"
        outside_cwd.mkdir()
        previous_cwd = Path.cwd()
        try:
            os.chdir(outside_cwd)
            self.assertEqual(self.classify("root"), COMMON.CLASS_ROOT)
        finally:
            os.chdir(previous_cwd)

    def test_config_sqlite_home_takes_precedence_over_environment(self) -> None:
        configured = Path(self.temporary_directory.name) / "configured-state"
        configured_database = self.make_modern_database(configured)
        self.insert_thread("configured-root", "user", "cli", configured_database)
        environment_home = Path(self.temporary_directory.name) / "environment-state"
        environment_database = self.make_modern_database(environment_home)
        self.insert_thread("environment-root", "user", "cli", environment_database)
        self.codex_home.mkdir(exist_ok=True)
        (self.codex_home / "config.toml").write_text(
            "sqlite_home = %s\n" % json.dumps(str(configured)),
            encoding="utf-8",
        )

        environment = {"CODEX_SQLITE_HOME": str(environment_home)}
        self.assertEqual(
            COMMON.classify_thread(
                self.notification("configured-root"), self.codex_home, environment
            ),
            COMMON.CLASS_ROOT,
        )
        self.assertEqual(
            COMMON.classify_thread(
                self.notification("environment-root"), self.codex_home, environment
            ),
            COMMON.CLASS_UNKNOWN,
        )

    def test_one_connection_provides_the_consistent_snapshot(self) -> None:
        self.insert_thread("root", "user", "cli")
        real_connect = sqlite3.connect
        calls = []

        def tracked_connect(*args, **kwargs):
            calls.append(args[0])
            return real_connect(*args, **kwargs)

        with mock.patch.object(COMMON.sqlite3, "connect", side_effect=tracked_connect):
            self.assertEqual(self.classify("root"), COMMON.CLASS_ROOT)
        self.assertEqual(len(calls), 1)

    def test_subagent_evidence_in_older_database_beats_newer_root_row(self) -> None:
        thread_id = "conflicting-thread"
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                ("parent", thread_id, "completed"),
            )
            connection.commit()

        newer = self.sqlite_home / "state_6.sqlite"
        with closing(sqlite3.connect(newer)) as connection:
            connection.executescript(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    thread_source TEXT,
                    source TEXT
                );
                CREATE TABLE thread_spawn_edges (
                    parent_thread_id TEXT NOT NULL,
                    child_thread_id TEXT NOT NULL PRIMARY KEY,
                    status TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?)",
                (thread_id, "user", "cli"),
            )
            connection.commit()
        os.utime(newer, (self.database_path.stat().st_mtime + 10,) * 2)

        self.assertEqual(self.classify(thread_id), COMMON.CLASS_SUBAGENT)

    def test_locked_database_gets_a_short_retry(self) -> None:
        self.insert_thread("root", "user", "cli")
        real_connect = sqlite3.connect
        attempts = 0

        def flaky_connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sqlite3.OperationalError("database is locked")
            return real_connect(*args, **kwargs)

        with mock.patch.object(COMMON.sqlite3, "connect", side_effect=flaky_connect):
            self.assertEqual(self.classify("root"), COMMON.CLASS_ROOT)
        self.assertEqual(attempts, 2)


class RouterTests(unittest.TestCase):
    def payload(self, notification_type: str = "agent-turn-complete") -> str:
        return json.dumps({"type": notification_type, "thread-id": "thread"})

    def test_root_is_forwarded_exactly_once(self) -> None:
        with (
            mock.patch.object(ROUTER, "classify_thread", return_value=COMMON.CLASS_ROOT),
            mock.patch.object(ROUTER, "forward", return_value=0) as forward,
        ):
            self.assertEqual(ROUTER.main([self.payload()]), 0)
        forward.assert_called_once()

    def test_subagent_and_unknown_never_reach_any_downstream(self) -> None:
        for classification in (COMMON.CLASS_SUBAGENT, COMMON.CLASS_UNKNOWN):
            with self.subTest(classification=classification):
                with (
                    mock.patch.object(ROUTER, "classify_thread", return_value=classification),
                    mock.patch.object(ROUTER, "forward") as forward,
                ):
                    self.assertEqual(ROUTER.main([self.payload()]), 0)
                    forward.assert_not_called()

    def test_approval_requested_is_preserved_without_thread_classification(self) -> None:
        with (
            mock.patch.object(ROUTER, "classify_thread") as classify,
            mock.patch.object(ROUTER, "forward", return_value=0) as forward,
        ):
            self.assertEqual(ROUTER.main([self.payload("approval-requested")]), 0)
        classify.assert_not_called()
        forward.assert_called_once()

    def test_sky_previous_notify_reuses_root_classification(self) -> None:
        command = ROUTER.downstream_command(Path("/sky"), Path("/sidebar"), "{}")
        self.assertEqual(command[:3], ["/sky", "turn-ended", "--previous-notify"])
        self.assertEqual(json.loads(command[3]), ["/sidebar", "notify", "--classified-root"])
        self.assertEqual(command[4], "{}")


class RouterIntegrationTests(unittest.TestCase, StateDatabaseMixin):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.codex_home = self.root / "codex"
        self.sqlite_home = self.codex_home
        self.make_modern_database()
        self.sky_log = self.root / "sky.log"
        self.fake_sky = self.root / "sky"
        self.fake_sky.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$SKY_LOG\"\n",
            encoding="utf-8",
        )
        self.fake_sky.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_router(self, thread_id: str, extra_environment: Optional[dict] = None) -> int:
        env = {
            **os.environ,
            "CODEX_HOME": str(self.codex_home),
            "SKY_LOG": str(self.sky_log),
        }
        if extra_environment:
            env.update(extra_environment)
        payload = json.dumps(self.notification(thread_id))
        result = subprocess.run(
            [
                sys.executable,
                str(ROUTER_PATH),
                "--sky-client",
                str(self.fake_sky),
                "--sidebar",
                str(SIDEBAR_PATH),
                payload,
            ],
            env=env,
            check=False,
        )
        return result.returncode

    def downstream_count(self) -> int:
        if not self.sky_log.exists():
            return 0
        return len(self.sky_log.read_text(encoding="utf-8").splitlines())

    def test_modern_root_once_but_child_and_unknown_zero(self) -> None:
        self.insert_thread("root", "user", "cli")
        self.insert_thread("child", "subagent", "cli")
        self.insert_thread("unknown-source", None, "unknown")

        self.assertEqual(self.run_router("child"), 0)
        self.assertEqual(self.run_router("unknown"), 0)
        self.assertEqual(self.run_router("unknown-source"), 0)
        self.assertEqual(self.downstream_count(), 0)
        self.assertEqual(self.run_router("root"), 0)
        self.assertEqual(self.downstream_count(), 1)

    def test_old_schema_child_reaches_zero_downstreams(self) -> None:
        self.database_path.unlink()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE threads (id TEXT PRIMARY KEY, source TEXT);
                INSERT INTO threads VALUES ('child', '{"subagent":{"other":"legacy"}}');
                """
            )
        self.assertEqual(self.run_router("child"), 0)
        self.assertEqual(self.downstream_count(), 0)

    def test_relocated_sqlite_child_reaches_zero_downstreams(self) -> None:
        original_database = self.database_path
        relocated = self.root / "sqlite"
        database = self.make_modern_database(relocated)
        self.insert_thread("child", "subagent", "cli", database)
        original_database.unlink()
        self.assertEqual(
            self.run_router("child", {"CODEX_SQLITE_HOME": str(relocated)}),
            0,
        )
        self.assertEqual(self.downstream_count(), 0)


class NotifierTests(unittest.TestCase):
    def test_osc_message_removes_bel_escape_and_c1_controls(self) -> None:
        self.assertEqual(NOTIFIER.collapse("ok\a\x1b]9;evil\x9b done"), "ok ]9;evil done")

    def test_tmux_label_removes_controls_from_server_names(self) -> None:
        self.assertEqual(
            NOTIFIER.tmux_label(
                {
                    "session_name": "main\a\x1b]9;evil",
                    "window_index": "1",
                    "window_name": "work\x9b",
                    "pane_id": "%2",
                }
            ),
            "main ]9;evil · 1:work · %2",
        )

    def test_tmux_context_rejects_rebound_server_from_inherited_environment(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"TMUX": "/tmp/tmux-test.sock,111,0", "TMUX_PANE": "%2"},
            ),
            mock.patch.object(
                NOTIFIER,
                "shell_output",
                return_value="222\t/tmp/tmux-test.sock",
            ),
        ):
            self.assertEqual(NOTIFIER.tmux_context(), {})

    def test_tmux_context_rechecks_server_after_reading_pane(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {"TMUX": "/tmp/tmux-test.sock,111,0", "TMUX_PANE": "%2"},
            ),
            mock.patch.object(
                NOTIFIER,
                "shell_output",
                side_effect=[
                    "111\t/tmp/tmux-test.sock",
                    "/dev/ttys001|xterm-ghostty|xterm|main|1|work|%2|0",
                    "222\t/tmp/tmux-test.sock",
                ],
            ),
        ):
            self.assertEqual(NOTIFIER.tmux_context(), {})

    def test_unknown_direct_invocation_is_silent(self) -> None:
        payload = json.dumps({"type": "agent-turn-complete", "thread-id": "missing"})
        with (
            mock.patch.object(sys, "argv", [str(NOTIFIER_PATH), payload]),
            mock.patch.object(NOTIFIER, "classify_thread", return_value=COMMON.CLASS_UNKNOWN),
            mock.patch.object(NOTIFIER, "send_terminal_notifier") as send_notification,
            mock.patch.object(NOTIFIER, "send_osc9") as send_osc,
        ):
            self.assertEqual(NOTIFIER.main(), 0)
        send_notification.assert_not_called()
        send_osc.assert_not_called()

    def test_classified_root_is_not_reclassified_and_notifies_once(self) -> None:
        payload = json.dumps(
            {
                "type": "agent-turn-complete",
                "thread-id": "root",
                "cwd": "/tmp/project",
            }
        )
        with (
            mock.patch.object(sys, "argv", [str(NOTIFIER_PATH), "--classified-root", payload]),
            mock.patch.object(NOTIFIER, "classify_thread") as classify,
            mock.patch.object(NOTIFIER, "tmux_context", return_value={}),
            mock.patch.object(NOTIFIER, "direct_terminal_info", return_value=("", "", "")),
            mock.patch.object(NOTIFIER, "send_terminal_notifier", return_value=True) as send,
        ):
            self.assertEqual(NOTIFIER.main(), 0)
        classify.assert_not_called()
        send.assert_called_once()


class CompletionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="notify-state-",
            dir="/tmp",
        )
        self.root = Path(self.temporary_directory.name)
        self.state_dir = self.root / "cache" / "codex-tmux-status"
        self.tmux_socket = str(self.root / "tmux.sock")
        self.server_pid = 4242
        self.server_start_time = 1_700_000_000
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.tmux_socket)
        self.server_query_patcher = mock.patch.object(
            STATE,
            "query_tmux_server_identity",
            return_value=(
                self.server_pid,
                self.server_start_time,
                STATE.canonical_socket(self.tmux_socket),
            ),
        )
        self.server_query_patcher.start()
        self.meta = "main\t@1\t1\twork\t%%42\t%s\t%d\t%d" % (
            self.tmux_socket,
            self.server_pid,
            self.server_start_time,
        )
        self.payload = {
            "type": "agent-turn-complete",
            "thread-id": "root-id",
            "cwd": "/tmp/project",
            "input-messages": ["task"],
            "last-assistant-message": "done",
        }

    def tearDown(self) -> None:
        self.server_query_patcher.stop()
        self.listener.close()
        self.temporary_directory.cleanup()

    def state(self) -> dict:
        server_dir = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert server_dir is not None
        return json.loads((server_dir / "pane-42.json").read_text(encoding="utf-8"))

    def test_socket_identity_prefers_stable_birthtime(self) -> None:
        socket_stat = SimpleNamespace(st_dev=7, st_ino=100, st_birthtime=1.25)
        with mock.patch.object(STATE.os, "stat", return_value=socket_stat):
            identity = STATE.socket_identity(self.tmux_socket)
        self.assertEqual(
            identity,
            (STATE.canonical_socket(self.tmux_socket), 7, 100, 1_250_000_000, "birthtime"),
        )

    def test_socket_identity_falls_back_to_ctime(self) -> None:
        socket_stat = SimpleNamespace(
            st_dev=7,
            st_ino=100,
            st_ctime=1.0,
            st_ctime_ns=1_500_000_001,
        )
        with mock.patch.object(STATE.os, "stat", return_value=socket_stat):
            identity = STATE.socket_identity(self.tmux_socket)
        self.assertEqual(
            identity,
            (STATE.canonical_socket(self.tmux_socket), 7, 100, 1_500_000_001, "ctime"),
        )

    def test_recreated_socket_path_uses_a_fresh_server_namespace(self) -> None:
        canonical = STATE.canonical_socket(self.tmux_socket)
        with mock.patch.object(
            STATE,
            "socket_identity",
            side_effect=[
                (canonical, 7, 100, 1_000, "birthtime"),
                (canonical, 7, 101, 2_000, "birthtime"),
                (canonical, 7, 101, 2_000, "birthtime"),
            ],
        ):
            first = STATE.server_state_dir(self.state_dir, self.tmux_socket)
            second = STATE.server_state_dir(self.state_dir, self.tmux_socket)
            ensured = STATE.ensure_server_state_dir(self.state_dir, self.tmux_socket)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertEqual(ensured, second)
        assert second is not None
        metadata = json.loads((second / "server.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["tmux_socket"], canonical)
        self.assertEqual(
            metadata["socket_identity"],
            {
                "kind": "inode-generation",
                "canonical_path": canonical,
                "device": 7,
                "inode": 101,
                "generation": {
                    "source": "birthtime",
                    "timestamp_ns": 2_000,
                },
            },
        )

    def test_reused_socket_inode_with_new_generation_uses_a_fresh_namespace(self) -> None:
        canonical = STATE.canonical_socket(self.tmux_socket)
        with mock.patch.object(
            STATE,
            "socket_identity",
            side_effect=[
                (canonical, 7, 100, 1_000, "ctime"),
                (canonical, 7, 100, 2_000, "ctime"),
            ],
        ):
            first = STATE.server_state_dir(self.state_dir, self.tmux_socket)
            second = STATE.server_state_dir(self.state_dir, self.tmux_socket)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)

    def test_completion_requires_captured_tmux_server_identity(self) -> None:
        legacy_meta = self.meta.rsplit("\t", 2)[0]
        with mock.patch.object(STATE, "query_tmux_server_identity") as query:
            self.assertFalse(
                STATE.write_completion(
                    self.state_dir,
                    legacy_meta,
                    self.payload,
                    "codex",
                    100,
                )
            )
        query.assert_not_called()
        self.assertEqual(list(self.state_dir.glob("servers/*/pane-*.json")), [])

    def test_completion_fails_closed_when_server_identity_query_fails(self) -> None:
        with mock.patch.object(STATE, "query_tmux_server_identity", return_value=None):
            self.assertFalse(
                STATE.write_completion(
                    self.state_dir,
                    self.meta,
                    self.payload,
                    "codex",
                    100,
                )
            )
        self.assertEqual(list(self.state_dir.glob("servers/*/pane-*.json")), [])

    def test_rebound_socket_before_write_rejects_old_server_metadata(self) -> None:
        os.unlink(self.tmux_socket)
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        replacement.bind(self.tmux_socket)
        try:
            with mock.patch.object(
                STATE,
                "query_tmux_server_identity",
                return_value=(
                    self.server_pid + 1,
                    self.server_start_time + 1,
                    STATE.canonical_socket(self.tmux_socket),
                ),
            ):
                self.assertFalse(
                    STATE.write_completion(
                        self.state_dir,
                        self.meta,
                        self.payload,
                        "codex",
                        100,
                    )
                )
        finally:
            replacement.close()
        self.assertEqual(list(self.state_dir.glob("servers/*/pane-*.json")), [])

    def test_rebind_after_tmux_query_is_caught_by_second_socket_stat(self) -> None:
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def query_then_rebind(_path: str):
            os.unlink(self.tmux_socket)
            replacement.bind(self.tmux_socket)
            return (
                self.server_pid,
                self.server_start_time,
                STATE.canonical_socket(self.tmux_socket),
            )

        try:
            with mock.patch.object(
                STATE,
                "query_tmux_server_identity",
                side_effect=query_then_rebind,
            ):
                self.assertFalse(
                    STATE.write_completion(
                        self.state_dir,
                        self.meta,
                        self.payload,
                        "codex",
                        100,
                    )
                )
        finally:
            replacement.close()
        self.assertEqual(list(self.state_dir.glob("servers/*/pane-*.json")), [])

    def test_verified_writer_uses_frozen_identity_without_a_third_stat(self) -> None:
        frozen = STATE.socket_identity(self.tmux_socket)
        with (
            mock.patch.object(
                STATE,
                "socket_identity",
                side_effect=[frozen, frozen],
            ) as identity_query,
            mock.patch.object(STATE, "focused_pane_ids", return_value=[]),
        ):
            self.assertTrue(
                STATE.write_completion(
                    self.state_dir,
                    self.meta,
                    self.payload,
                    "codex",
                    100,
                )
            )
        self.assertEqual(identity_query.call_count, 2)
        state_dir = STATE._server_state_dir(self.state_dir, frozen)
        assert state_dir is not None
        state = json.loads((state_dir / "pane-42.json").read_text(encoding="utf-8"))
        self.assertEqual(state["tmux_server_pid"], str(self.server_pid))
        self.assertEqual(
            state["tmux_server_start_time"],
            str(self.server_start_time),
        )
        server_metadata = json.loads((state_dir / "server.json").read_text())
        self.assertEqual(
            server_metadata["tmux_server"],
            {
                "pid": self.server_pid,
                "start_time": self.server_start_time,
                "socket_path": STATE.canonical_socket(self.tmux_socket),
            },
        )

    def test_atomic_private_state_preserves_resume_id(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            self.assertTrue(STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100))
        state = self.state()
        server_dir = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert server_dir is not None
        self.assertEqual(state["thread_id"], "root-id")
        self.assertTrue(state["unread"])
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(server_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((server_dir / "pane-42.json").stat().st_mode), 0o600)

        self.assertTrue(STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 200))
        state = self.state()
        self.assertEqual(state["thread_id"], "root-id")
        self.assertFalse(state["unread"])
        self.assertEqual(stat.S_IMODE((server_dir / "pane-42.ack").stat().st_mode), 0o600)
        self.assertFalse(STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 300))

    def test_expected_state_dir_ack_uses_one_stable_generation(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            self.assertTrue(
                STATE.write_completion(
                    self.state_dir,
                    self.meta,
                    self.payload,
                    "codex",
                    100,
                )
            )
        expected = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert expected is not None
        expected = expected.resolve(strict=True)

        with mock.patch.object(STATE, "focused_pane_ids", return_value=["%42"]):
            self.assertTrue(
                STATE.acknowledge(
                    self.state_dir,
                    "%42",
                    self.tmux_socket,
                    200,
                    expected,
                    True,
                )
            )
        self.assertFalse(json.loads((expected / "pane-42.json").read_text())["unread"])

    def test_expected_state_dir_rejects_socket_rebound_before_ack(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
        expected = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert expected is not None
        expected = expected.resolve(strict=True)

        os.unlink(self.tmux_socket)
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        replacement.bind(self.tmux_socket)
        try:
            self.assertFalse(
                STATE.acknowledge(
                    self.state_dir,
                    "%42",
                    self.tmux_socket,
                    200,
                    expected,
                )
            )
        finally:
            replacement.close()

        state = json.loads((expected / "pane-42.json").read_text())
        self.assertTrue(state["unread"])
        self.assertFalse((expected / "pane-42.ack").exists())

    def test_expected_state_dir_rechecks_generation_after_focus_query(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
        expected = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert expected is not None
        expected = expected.resolve(strict=True)
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def focus_then_rebind(_socket_path: str):
            os.unlink(self.tmux_socket)
            replacement.bind(self.tmux_socket)
            return ["%42"]

        try:
            with mock.patch.object(
                STATE,
                "focused_pane_ids",
                side_effect=focus_then_rebind,
            ):
                self.assertFalse(
                    STATE.acknowledge(
                        self.state_dir,
                        "%42",
                        self.tmux_socket,
                        200,
                        expected,
                        True,
                    )
                )
        finally:
            replacement.close()

        state = json.loads((expected / "pane-42.json").read_text())
        self.assertTrue(state["unread"])
        self.assertFalse((expected / "pane-42.ack").exists())

    def test_expected_state_dir_rejects_noncanonical_or_injected_path(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
        expected = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert expected is not None
        expected = expected.resolve(strict=True)
        outside = self.root / "0123456789abcdef"
        outside.mkdir()
        alias = self.root / "state-alias"
        alias.symlink_to(expected, target_is_directory=True)

        for invalid in (outside.resolve(strict=True), alias):
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    STATE.acknowledge(
                        self.state_dir,
                        "%42",
                        self.tmux_socket,
                        200,
                        invalid,
                    )
                )
        state = json.loads((expected / "pane-42.json").read_text())
        self.assertTrue(state["unread"])
        self.assertFalse((expected / "pane-42.ack").exists())

    def test_expected_state_dir_cli_acknowledges_valid_namespace(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
        expected = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        assert expected is not None
        expected = expected.resolve(strict=True)

        result = subprocess.run(
            [
                sys.executable,
                str(STATE_HELPER_PATH),
                "--state-dir",
                str(self.state_dir),
                "--tmux-socket",
                self.tmux_socket,
                "--ack-pane",
                "%42",
                "--expected-state-dir",
                str(expected),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "changed\n")
        self.assertFalse(json.loads((expected / "pane-42.json").read_text())["unread"])

    def test_same_pane_number_is_isolated_between_tmux_servers(self) -> None:
        other_socket = str(self.root / "other-tmux.sock")
        other_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        other_listener.bind(other_socket)
        self.addCleanup(other_listener.close)
        other_pid = 5252
        other_start_time = self.server_start_time + 1
        other_meta = "main\t@9\t1\tother\t%%42\t%s\t%d\t%d" % (
            other_socket,
            other_pid,
            other_start_time,
        )

        def query_server(path: str):
            canonical = STATE.canonical_socket(path)
            if canonical == STATE.canonical_socket(other_socket):
                return other_pid, other_start_time, canonical
            return self.server_pid, self.server_start_time, canonical

        with (
            mock.patch.object(STATE, "query_tmux_server_identity", side_effect=query_server),
            mock.patch.object(STATE, "focused_pane_ids", return_value=[]),
        ):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
            other_payload = dict(self.payload)
            other_payload["thread-id"] = "other-root-id"
            STATE.write_completion(self.state_dir, other_meta, other_payload, "codex", 110)

        first_dir = STATE.server_state_dir(self.state_dir, self.tmux_socket)
        other_dir = STATE.server_state_dir(self.state_dir, other_socket)
        assert first_dir is not None and other_dir is not None
        self.assertNotEqual(first_dir, other_dir)
        self.assertEqual(json.loads((first_dir / "pane-42.json").read_text())["thread_id"], "root-id")
        self.assertEqual(json.loads((other_dir / "pane-42.json").read_text())["thread_id"], "other-root-id")

        STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 120)
        self.assertFalse(json.loads((first_dir / "pane-42.json").read_text())["unread"])
        self.assertTrue(json.loads((other_dir / "pane-42.json").read_text())["unread"])

    def test_completion_without_id_cannot_refresh_an_old_id(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
            missing_id = dict(self.payload)
            missing_id.pop("thread-id")
            self.assertFalse(
                STATE.write_completion(self.state_dir, self.meta, missing_id, "codex", 200)
            )
        state = self.state()
        self.assertEqual(state["thread_id"], "root-id")
        self.assertEqual(state["completed_at_ns"], 100)

    def test_out_of_order_ack_cannot_move_watermark_backwards(self) -> None:
        STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 300)
        STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 200)
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 250)
        state = self.state()
        self.assertEqual(state["acked_at_ns"], 300)
        self.assertFalse(state["unread"])

    def test_ack_older_than_completion_cannot_clear_new_unread(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 300)
        self.assertFalse(
            STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 250)
        )
        state = self.state()
        self.assertEqual(state["completed_at_ns"], 300)
        self.assertTrue(state["unread"])

    def test_delayed_old_completion_cannot_replace_new_id_or_unread(self) -> None:
        newer = dict(self.payload)
        newer["thread-id"] = "new-root-id"
        older = dict(self.payload)
        older["thread-id"] = "old-root-id"
        STATE.acknowledge(self.state_dir, "%42", self.tmux_socket, 250)
        with mock.patch.object(STATE, "focused_pane_ids", return_value=[]):
            self.assertTrue(
                STATE.write_completion(self.state_dir, self.meta, newer, "codex", 300)
            )
            self.assertFalse(
                STATE.write_completion(self.state_dir, self.meta, older, "codex", 200)
            )
        state = self.state()
        self.assertEqual(state["thread_id"], "new-root-id")
        self.assertEqual(state["completed_at_ns"], 300)
        self.assertTrue(state["unread"])

    def test_currently_focused_completion_never_becomes_unread(self) -> None:
        with mock.patch.object(STATE, "focused_pane_ids", return_value=["%42"]):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
        self.assertFalse(self.state()["unread"])

    def test_only_clients_with_focused_flag_count_as_viewed(self) -> None:
        with mock.patch.object(
            STATE.subprocess,
            "check_output",
            return_value="%41\tattached,UTF-8\n%42\tattached,focused,UTF-8\n",
        ) as check_output:
            self.assertEqual(STATE.focused_pane_ids(self.tmux_socket), ["%42"])
        self.assertEqual(
            check_output.call_args.args[0][:3],
            ["tmux", "-S", STATE.canonical_socket(self.tmux_socket)],
        )

    def test_ack_marker_after_write_started_wins_the_race(self) -> None:
        original_read_json = STATE.read_json
        ack_reads = 0

        def racing_read(path: Path):
            nonlocal ack_reads
            if path.suffix == ".ack":
                ack_reads += 1
                return {} if ack_reads == 1 else {"acked_at_ns": 2**63}
            return original_read_json(path)

        with (
            mock.patch.object(STATE, "read_json", side_effect=racing_read),
            mock.patch.object(STATE, "focused_pane_ids", return_value=[]),
        ):
            STATE.write_completion(self.state_dir, self.meta, self.payload, "codex", 100)
        self.assertFalse(self.state()["unread"])


class SidebarIntegrationTests(unittest.TestCase, StateDatabaseMixin):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="notify-sidebar-",
            dir="/tmp",
        )
        root = Path(self.temporary_directory.name)
        self.codex_home = root / "codex"
        self.sqlite_home = self.codex_home
        self.make_modern_database()
        self.fake_bin = root / "bin"
        self.fake_bin.mkdir()
        self.cache_home = root / "cache"
        self.log_path = root / "python.log"
        self.tmux_socket = str(root / "tmux.sock")
        self.tmux_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.tmux_listener.bind(self.tmux_socket)
        fake_tmux = self.fake_bin / "tmux"
        fake_tmux.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = -S ]; then shift 2; fi\n"
            "if [ \"$1\" = display-message ]; then\n"
            "  case \"$*\" in\n"
            "    *'#{session_name}'*) printf 'main\\t@1\\t1\\twork\\t%%42\\t%s\\t%s\\t%s\\n' \"$FAKE_TMUX_SOCKET\" \"${FAKE_CAPTURE_PID:-4242}\" \"${FAKE_CAPTURE_START:-1700000000}\";;\n"
            "    *'#{pid}'*) printf '%s\\t%s\\t%s\\n' \"${FAKE_LIVE_PID:-4242}\" \"${FAKE_LIVE_START:-1700000000}\" \"$FAKE_TMUX_SOCKET\";;\n"
            "  esac\n"
            "fi\n"
            "if [ \"$1\" = list-clients ]; then printf '%%9\\n'; fi\n",
            encoding="utf-8",
        )
        fake_tmux.chmod(0o755)
        fake_notifier = self.fake_bin / "terminal-notifier"
        fake_notifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_notifier.chmod(0o755)

    def tearDown(self) -> None:
        self.tmux_listener.close()
        self.temporary_directory.cleanup()

    def environment(self) -> dict:
        return {
            **os.environ,
            "CODEX_HOME": str(self.codex_home),
            "PATH": "%s:%s" % (self.fake_bin, os.environ.get("PATH", "")),
            "TMUX_PANE": "%42",
            "TMUX": "%s,4242,0" % self.tmux_socket,
            "FAKE_TMUX_SOCKET": self.tmux_socket,
            "XDG_CACHE_HOME": str(self.cache_home),
            "TERM": "xterm-256color",
            "TERM_PROGRAM": "test",
        }

    def test_direct_unknown_does_not_write_state(self) -> None:
        payload = json.dumps(self.notification("unknown"))
        result = subprocess.run(
            ["bash", str(SIDEBAR_PATH), "notify", payload],
            env=self.environment(),
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            list((self.cache_home / "codex-tmux-status").glob("servers/*/pane-*.json")),
            [],
        )

    def test_classified_root_writes_state_without_another_db_lookup(self) -> None:
        payload = json.dumps(self.notification("root"))
        # No matching DB row: --classified-root must still use the router result.
        result = subprocess.run(
            ["bash", str(SIDEBAR_PATH), "notify", "--classified-root", payload],
            env=self.environment(),
            check=False,
        )
        state_root = self.cache_home / "codex-tmux-status"
        state_dir = STATE.server_state_dir(state_root, self.tmux_socket)
        assert state_dir is not None
        state_path = state_dir / "pane-42.json"
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(state_path.read_text())["thread_id"], "root")

    def test_rebound_fake_tmux_server_cannot_receive_old_completion(self) -> None:
        payload = json.dumps(self.notification("root"))
        env = self.environment()
        env["FAKE_LIVE_PID"] = "5252"
        result = subprocess.run(
            ["bash", str(SIDEBAR_PATH), "notify", "--classified-root", payload],
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            list((self.cache_home / "codex-tmux-status").glob("servers/*/pane-*.json")),
            [],
        )

    def test_new_server_consistent_with_itself_still_rejects_old_tmux_environment(self) -> None:
        payload = json.dumps(self.notification("root"))
        env = self.environment()
        env.update({"FAKE_CAPTURE_PID": "5252", "FAKE_LIVE_PID": "5252"})
        result = subprocess.run(
            ["bash", str(SIDEBAR_PATH), "notify", "--classified-root", payload],
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            list((self.cache_home / "codex-tmux-status").glob("servers/*/pane-*.json")),
            [],
        )

    def test_state_failure_does_not_swallow_desktop_notifier(self) -> None:
        real_python = sys.executable
        wrapper = self.fake_bin / "python3"
        wrapper.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$PYTHON_LOG\"\n"
            "case \"$1\" in *assistant_completion_state.py) exit 1;; esac\n"
            "exec \"$REAL_PYTHON\" \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        env = self.environment()
        env.update({"PYTHON_LOG": str(self.log_path), "REAL_PYTHON": real_python})
        payload = json.dumps(self.notification("root"))

        result = subprocess.run(
            ["bash", str(SIDEBAR_PATH), "notify", "--classified-root", payload],
            env=env,
            check=False,
        )
        log = self.log_path.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertIn("assistant_completion_state.py", log)
        self.assertIn("codex-notify-ghostty.py --classified-root", log)


class ClaudeHookIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="notify-claude-",
            dir="/tmp",
        )
        self.root = Path(self.temporary_directory.name)
        self.cache_home = self.root / "cache"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.script_dir = self.root / "scripts"
        self.script_dir.mkdir()
        self.hook_path = self.script_dir / "claude-code-stop-notify.sh"
        shutil.copy2(SCRIPT_DIR / "claude-code-stop-notify.sh", self.hook_path)
        shutil.copy2(STATE_HELPER_PATH, self.script_dir / STATE_HELPER_PATH.name)
        self.list_file = self.root / "panes.txt"
        self.tmux_socket = str(self.root / "tmux.sock")
        self.server_pid = 4242
        self.server_start_time = 1_700_000_000
        self.tmux_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.tmux_listener.bind(self.tmux_socket)
        fake_tmux = self.fake_bin / "tmux"
        fake_tmux.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = -S ]; then shift 2; fi\n"
            "case \"$1\" in\n"
            "  list-panes) cat \"$TMUX_LIST_FILE\";;\n"
            "  list-clients) :;;\n"
            "  display-message)\n"
            "    if [ \"${TMUX_DISPLAY_FAIL:-}\" = 1 ]; then exit 1; fi\n"
            "    case \"$*\" in\n"
            "      *'#{session_name}'*) printf 'main\\t@1\\t1\\twork\\t%%42\\t%s\\t%s\\t%s\\n' \"$FAKE_TMUX_SOCKET\" \"${FAKE_CAPTURE_PID:-4242}\" \"${FAKE_CAPTURE_START:-1700000000}\";;\n"
            "      *'#{pid}'*) printf '%s\\t%s\\t%s\\n' \"${FAKE_LIVE_PID:-4242}\" \"${FAKE_LIVE_START:-1700000000}\" \"$FAKE_TMUX_SOCKET\";;\n"
            "    esac;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_tmux.chmod(0o755)

    def tearDown(self) -> None:
        self.tmux_listener.close()
        self.temporary_directory.cleanup()

    def environment(self) -> dict:
        env = {
            **os.environ,
            "PATH": "%s:%s" % (self.fake_bin, os.environ.get("PATH", "")),
            "XDG_CACHE_HOME": str(self.cache_home),
            "TMUX_LIST_FILE": str(self.list_file),
            "TMUX": "%s,%s,0" % (self.tmux_socket, self.server_pid),
            "FAKE_TMUX_SOCKET": self.tmux_socket,
        }
        env.pop("TMUX_PANE", None)
        return env

    def run_hook(
        self,
        payload: dict,
        extra_environment: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        environment = self.environment()
        if extra_environment:
            environment.update(extra_environment)
        return subprocess.run(
            ["bash", str(self.hook_path)],
            input=json.dumps(payload),
            text=True,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def state_files(self):
        state_dir = self.cache_home / "codex-tmux-status"
        return list(state_dir.glob("servers/*/pane-*.json")) if state_dir.exists() else []

    def pane_line(
        self,
        session: str,
        window_id: str,
        window_name: str,
        pane_id: str,
        command: str,
        title: str,
        cwd: str,
    ) -> str:
        return (
            f"{session}\t{window_id}\t1\t{window_name}\t{pane_id}\t"
            f"{command}\t{title}\t{cwd}\t{self.server_pid}\t"
            f"{self.server_start_time}\t{self.tmux_socket}\n"
        )

    def test_no_tmux_pane_with_two_unique_same_cwd_matches_writes_nothing(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@1", "one", "%10", "claude", "Claude", "/tmp/project")
            + self.pane_line("main", "@2", "two", "%11", "claude", "Claude", "/tmp/project"),
            encoding="utf-8",
        )
        result = self.run_hook({"cwd": "/tmp/project", "session_id": "session"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertEqual(self.state_files(), [])

    def test_no_tmux_identity_never_probes_the_default_server(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@1", "one", "%10", "claude", "Claude", "/tmp/project"),
            encoding="utf-8",
        )
        env = self.environment()
        env.pop("TMUX", None)
        result = subprocess.run(
            ["bash", str(self.hook_path)],
            input=json.dumps({"cwd": "/tmp/project", "session_id": "session"}),
            text=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertEqual(self.state_files(), [])

    def test_failed_authoritative_tmux_pane_never_falls_back_to_cwd(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@2", "other", "%11", "claude", "Other Claude", "/tmp/project"),
            encoding="utf-8",
        )
        env = self.environment()
        env.update({"TMUX_PANE": "%99", "TMUX_DISPLAY_FAIL": "1"})
        result = subprocess.run(
            ["bash", str(self.hook_path)],
            input=json.dumps({"cwd": "/tmp/project", "session_id": "session"}),
            text=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertEqual(self.state_files(), [])

    def test_hook_runner_plus_other_claude_at_same_cwd_writes_nothing(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@1", "one", "%10", "bash", "Hook runner", "/tmp/project")
            + self.pane_line("main", "@2", "two", "%11", "claude", "Other Claude", "/tmp/project"),
            encoding="utf-8",
        )
        result = self.run_hook({"cwd": "/tmp/project", "session_id": "session"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertEqual(self.state_files(), [])

    def test_rebound_fake_server_rejects_cwd_matched_completion(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@1", "one", "%10", "claude", "Claude", "/tmp/project"),
            encoding="utf-8",
        )
        result = self.run_hook(
            {"cwd": "/tmp/project", "session_id": "session"},
            {"FAKE_LIVE_PID": str(self.server_pid + 1)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertEqual(self.state_files(), [])

    def test_new_server_consistent_with_itself_rejects_old_tmux_environment(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@1", "one", "%10", "claude", "Claude", "/tmp/project"),
            encoding="utf-8",
        )
        result = self.run_hook(
            {"cwd": "/tmp/project", "session_id": "session"},
            {
                "FAKE_CAPTURE_PID": str(self.server_pid + 1),
                "FAKE_LIVE_PID": str(self.server_pid + 1),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "{}\n")
        self.assertEqual(self.state_files(), [])

    def test_session_group_duplicates_count_as_one_physical_match(self) -> None:
        self.list_file.write_text(
            self.pane_line("main", "@1", "one", "%10", "claude", "Claude", "/tmp/project")
            + self.pane_line("main-left", "@1", "one", "%10", "claude", "Claude", "/tmp/project"),
            encoding="utf-8",
        )
        transcript = self.root / "large-transcript.jsonl"
        transcript.write_bytes(b"not-json\n" + b"x" * (1024 * 1024))
        result = self.run_hook(
            {
                "cwd": "/tmp/project",
                "session_id": "claude-session",
                "transcript_path": str(transcript),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        files = self.state_files()
        self.assertEqual(len(files), 1)
        state = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(state["thread_id"], "claude-session")
        self.assertEqual(state["tmux_socket"], STATE.canonical_socket(self.tmux_socket))
        self.assertEqual(state["tmux_server_pid"], str(self.server_pid))
        self.assertEqual(
            state["tmux_server_start_time"],
            str(self.server_start_time),
        )
        self.assertNotIn("assistant", state)
        self.assertNotIn("transcript_path", state)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
