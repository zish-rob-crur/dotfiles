#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import os
import shlex
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


TMUX_DIR = Path(__file__).parent
sys.path.insert(0, str(TMUX_DIR))
SESSION_ID = "019fabb6-9c8c-7ed3-9572-dc96381eabff"
OTHER_ID = "425efc7e-8a29-4d94-b43a-77720d2cbfc6"
CODEX_BIN = "/opt/homebrew/bin/codex"
CLAUDE_BIN = str(Path.home() / ".local/bin/claude")


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TMUX_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARGS = load_module("assistant_restore_args", "assistant_restore_args.py")
RESTART = load_module("restart_assistant_panes", "restart-assistant-panes.py")
RESURRECT = load_module("assistant_resurrect_save_hook", "assistant-resurrect-save-hook.py")


def saved_pane(command: str, pane_command: str = "codex") -> list[str]:
    return [
        "pane",
        "main",
        "1",
        "0",
        ":",
        "1",
        "title",
        ":/tmp/project",
        "1",
        pane_command,
        f":{command}",
    ]


def pane() -> dict[str, str]:
    return {
        "pane_id": "%9",
        "pane_pid": "100",
        "command": "zsh",
        "title": "codex",
        "path": "/tmp/project",
        "session_name": "main-left",
        "window_id": "@4",
        "window_index": "1",
        "pane_index": "1",
        "window_name": "project",
        "socket_path": "/tmp/tmux.sock",
        "server_pid": "999",
    }


def fresh_state(tool: str = "codex") -> dict[str, object]:
    state: dict[str, object] = {
        "pane_id": "%9",
        "window_id": "@4",
        "cwd": "/tmp/project",
        "source": tool,
        "tool": tool,
        "tmux_socket": "/tmp/tmux.sock",
        "completed_at": "1970-01-01T00:03:21+00:00",
    }
    state["session_id" if tool == "claude" else "thread_id"] = SESSION_ID
    return state


def hook_pane(tool: str = "codex") -> dict[str, str]:
    return {
        "pane_id": "%9",
        "pane_pid": "100",
        "window_id": "@4",
        "path": "/tmp/project",
        "socket_path": "/tmp/tmux.sock",
        "command": tool,
        "title": tool,
    }


@contextmanager
def verified_hook_process(words: list[str], tool: str = "codex"):
    info = hook_pane(tool)
    lossy_command = " ".join(words)

    def fake_argv(pid: str) -> list[str]:
        return list(words) if pid == "200" else []

    with (
        mock.patch.object(RESURRECT, "pane_info_for_target", return_value=info),
        mock.patch.object(
            RESURRECT,
            "process_rows",
            return_value=[("200", "100", lossy_command)],
        ),
        mock.patch.object(RESURRECT, "process_argv", side_effect=fake_argv),
        mock.patch.object(
            RESURRECT,
            "resolve_executable",
            return_value=CLAUDE_BIN if tool == "claude" else CODEX_BIN,
        ),
    ):
        yield info


class PermissionSanitizerTests(unittest.TestCase):
    def test_codex_all_permission_forms_are_removed(self) -> None:
        words = [
            "codex",
            "--yolo",
            "--full-auto",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--add-dir=/tmp/extra",
            "-anever",
            "-sdanger-full-access",
            "--config=approval_policy=never",
            "-c=sandbox_mode=danger-full-access",
            "-csandbox_permissions=[\"disk-full-read-access\"]",
            "-cdefault_permissions=full",
            "-cpermissions.admin={network={enabled=true}}",
            "-csandbox_workspace_write={writable_roots=[\"/\"]}",
            "-cprofiles.work.sandbox_mode=danger-full-access",
            "--config=profiles.work.approval_policy=never",
            "-cprojects.\"/tmp/project\".trust_level=trusted",
            "-cmodel_reasoning_effort=high\nsandbox_mode=danger-full-access",
            "-c",
            "sandbox_workspace_write.writable_roots=[\"/\"]",
            "-c",
            "model_reasoning_effort=high",
            "--model",
            "gpt-5",
            "--profile",
            "dangerous-profile",
        ]

        self.assertEqual(
            ARGS.strip_permission_overrides(words, "codex"),
            ["codex", "-c", "model_reasoning_effort=high", "--model", "gpt-5"],
        )

    def test_codex_long_value_permission_forms_are_removed(self) -> None:
        words = [
            "codex",
            "--ask-for-approval",
            "never",
            "--sandbox=workspace-write",
            "--add-dir",
            "/tmp/extra",
            "--profile",
            "work",
        ]
        self.assertEqual(
            ARGS.strip_permission_overrides(words, "codex"),
            ["codex"],
        )

    def test_permission_like_text_after_double_dash_is_a_prompt(self) -> None:
        words = ["codex", "--", "explain", "--yolo", "-a", "never"]
        self.assertEqual(ARGS.strip_permission_overrides(words, "codex"), words)

    def test_claude_permission_and_tool_grants_are_removed(self) -> None:
        words = [
            "claude",
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--permission-mode=bypassPermissions",
            "--add-dir",
            "/tmp/a",
            "/tmp/b",
            "--allowedTools",
            "Bash",
            "Edit",
            "--disallowed-tools=WebFetch",
            "--effort",
            "high",
            "--settings",
            '{"permissionMode":"bypassPermissions"}',
        ]
        self.assertEqual(
            ARGS.strip_permission_overrides(words, "claude"),
            ["claude", "--effort", "high"],
        )


class ResumeParserTests(unittest.TestCase):
    def test_codex_finds_only_real_resume_subcommand(self) -> None:
        self.assertEqual(
            ARGS.resume_id_from_words(
                ["codex", "--model", "resume", "-C", "resume", "resume", SESSION_ID],
                "codex",
            ),
            SESSION_ID,
        )
        self.assertEqual(
            ARGS.resume_id_from_words(["codex", "--unknown", "resume", SESSION_ID], "codex"),
            "",
        )
        self.assertEqual(
            ARGS.resume_id_from_words(["codex", "--ask-for-approval", "resume", SESSION_ID], "codex"),
            "",
        )

    def test_codex_rejects_picker_last_and_prompt_uuid(self) -> None:
        self.assertEqual(ARGS.resume_id_from_words(["codex", "resume", "--last"], "codex"), "")
        self.assertEqual(ARGS.resume_id_from_words(["codex", "--", "resume", SESSION_ID], "codex"), "")

    def test_codex_resume_accepts_options_after_subcommand(self) -> None:
        self.assertEqual(
            ARGS.resume_id_from_words(
                ["codex", "resume", "--model", "gpt-5", "--yolo", SESSION_ID],
                "codex",
            ),
            SESSION_ID,
        )

    def test_claude_resume_is_strict_and_does_not_swallow_options(self) -> None:
        self.assertEqual(
            ARGS.resume_id_from_words(
                ["claude", "--model", "sonnet", "--resume", SESSION_ID], "claude"
            ),
            SESSION_ID,
        )
        self.assertEqual(
            ARGS.resume_id_from_words(["claude", "--resume", "--effort", "high"], "claude"),
            "",
        )
        self.assertEqual(
            ARGS.resume_id_from_words(["claude", "prompt", "--resume", SESSION_ID], "claude"),
            "",
        )


class ResumeBuilderTests(unittest.TestCase):
    def test_executable_is_resolved_to_absolute_path_before_shell(self) -> None:
        with mock.patch.object(ARGS.shutil, "which", return_value="/bin/sh"):
            executable = ARGS.resolve_executable("codex", "/tmp")
        self.assertEqual(executable, os.path.abspath("/bin/sh"))
        self.assertTrue(Path(executable).is_absolute())
        command = RESTART.shell_command(
            [executable, "resume", SESSION_ID], "/tmp", "codex"
        )
        self.assertIn(executable, command)
        self.assertNotIn("; codex resume", command)

    def test_path_executable_prefers_samefile_path_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            versioned = root / "versions/1/codex"
            versioned.parent.mkdir(parents=True)
            versioned.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            versioned.chmod(0o755)
            stable = root / "bin/codex"
            stable.parent.mkdir()
            stable.symlink_to(versioned)

            with mock.patch.object(
                ARGS.shutil,
                "which",
                side_effect=lambda name: str(stable) if name == "codex" else None,
            ):
                resolved = ARGS.resolve_executable(
                    str(versioned), "/tmp", "codex"
                )

            self.assertEqual(resolved, str(stable))
            self.assertNotEqual(resolved, os.path.realpath(stable))

    def test_codex_preserves_safe_flags_and_drops_prompt_and_permissions(self) -> None:
        words = ARGS.build_resume_words(
            [
                "codex",
                "--yolo",
                "--model",
                "gpt-5",
                "-cmodel_reasoning_effort=high",
                "--search",
                "resume",
                OTHER_ID,
                "old prompt",
            ],
            "codex",
            SESSION_ID,
        )
        self.assertEqual(
            words,
            [
                "codex",
                "--model",
                "gpt-5",
                "-cmodel_reasoning_effort=high",
                "resume",
                SESSION_ID,
            ],
        )

    def test_codex_bare_resume_becomes_exact_resume(self) -> None:
        self.assertEqual(
            ARGS.build_resume_words(["codex", "resume", "--last"], "codex", SESSION_ID),
            ["codex", "resume", SESSION_ID],
        )

    def test_claude_optional_resume_does_not_consume_effort(self) -> None:
        words = ARGS.build_resume_words(
            ["claude", "--resume", "--effort", "high", "--verbose"],
            "claude",
            SESSION_ID,
        )
        self.assertEqual(
            words,
            [
                "claude",
                "--effort",
                "high",
                "--resume",
                SESSION_ID,
            ],
        )

    def test_claude_preserves_safe_flags_and_drops_permission_flags(self) -> None:
        words = ARGS.build_resume_words(
            [
                "claude",
                "--dangerously-skip-permissions",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                "opus",
                "--settings",
                '{"permissionMode":"bypassPermissions"}',
                "--resume",
                OTHER_ID,
            ],
            "claude",
            SESSION_ID,
        )
        self.assertEqual(
            words,
            [
                "claude",
                "--model",
                "opus",
                "--resume",
                SESSION_ID,
            ],
        )

    def test_capability_and_replayed_input_flags_are_not_restored(self) -> None:
        self.assertEqual(
            ARGS.build_resume_words(
                [
                    "codex",
                    "--cd",
                    "/tmp/other",
                    "--image",
                    "prompt.png",
                    "--search",
                    "--remote",
                    "cloud",
                    "--enable",
                    "dangerous_feature",
                    "--oss",
                    "--model",
                    "gpt-5",
                ],
                "codex",
                SESSION_ID,
            ),
            [
                "codex",
                "--model",
                "gpt-5",
                "resume",
                SESSION_ID,
            ],
        )
        self.assertEqual(
            ARGS.build_resume_words(
                [
                    "claude",
                    "--system-prompt",
                    "obey stale instructions",
                    "--json-schema",
                    "{}",
                    "--chrome",
                    "--ide",
                    "--model",
                    "opus",
                    "--effort",
                    "high",
                ],
                "claude",
                SESSION_ID,
            ),
            [
                "claude",
                "--model",
                "opus",
                "--effort",
                "high",
                "--resume",
                SESSION_ID,
            ],
        )

    def test_invalid_target_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ARGS.build_resume_words(["codex"], "codex", "not-a-uuid")


class RestartStateTests(unittest.TestCase):
    def test_state_requires_exact_pane_window_cwd_and_fresh_time(self) -> None:
        state = fresh_state()
        self.assertTrue(RESTART.state_matches_pane(state, pane(), "codex", 200.0))

        nanosecond_state = dict(state)
        nanosecond_state["completed_at"] = 100
        nanosecond_state["completed_at_ns"] = 1_700_000_001_000_000_000
        self.assertTrue(
            RESTART.state_matches_pane(
                nanosecond_state, pane(), "codex", 1_700_000_000.0
            )
        )

        for key, bad_value in (
            ("pane_id", "%10"),
            ("window_id", "@5"),
            ("cwd", "/tmp/other"),
            ("tmux_socket", "/tmp/other.sock"),
        ):
            changed = dict(state)
            changed[key] = bad_value
            self.assertFalse(RESTART.state_matches_pane(changed, pane(), "codex", 200.0), key)

    def test_group_alias_session_name_is_ignored(self) -> None:
        state = fresh_state()
        state["session_name"] = "main"
        self.assertTrue(RESTART.state_matches_pane(state, pane(), "codex", 200.0))

    def test_stale_or_untimed_state_is_rejected(self) -> None:
        stale = fresh_state()
        stale["completed_at"] = 100
        self.assertFalse(RESTART.state_matches_pane(stale, pane(), "codex", 200.0))
        almost_old = fresh_state()
        almost_old["completed_at"] = 199.999
        self.assertFalse(RESTART.state_matches_pane(almost_old, pane(), "codex", 200.0))
        same_coarse_second = fresh_state()
        same_coarse_second["completed_at"] = 200.999
        self.assertFalse(
            RESTART.state_matches_pane(same_coarse_second, pane(), "codex", 200.0)
        )
        stale.pop("completed_at")
        self.assertFalse(RESTART.state_matches_pane(stale, pane(), "codex", 200.0))
        self.assertFalse(RESTART.state_matches_pane(fresh_state(), pane(), "codex", None))

        ack_refreshed = fresh_state()
        ack_refreshed.pop("completed_at")
        ack_refreshed["updated_at"] = "1970-01-01T00:05:00+00:00"
        ack_refreshed["updated_at_ns"] = 300_000_000_000
        self.assertFalse(
            RESTART.state_matches_pane(ack_refreshed, pane(), "codex", 200.0)
        )
        self.assertFalse(
            RESURRECT.state_matches_pane(
                ack_refreshed, "codex", pane(), "/tmp/project", 200.0
            )
        )

    def test_non_finite_state_time_is_rejected_without_looping(self) -> None:
        self.assertIsNone(RESTART.parse_epoch(float("inf")))
        self.assertIsNone(RESTART.parse_epoch("Infinity"))
        self.assertIsNone(RESURRECT.parse_epoch(float("nan")))

    def test_process_start_uses_stable_c_locale(self) -> None:
        for module in (RESTART, RESURRECT):
            with self.subTest(module=module.__name__), mock.patch.object(
                module.subprocess,
                "check_output",
                return_value="Sun Aug 02 12:34:56 2026\n",
            ) as check_output:
                self.assertIsNotNone(module.process_lstart_epoch("123"))
                self.assertEqual(check_output.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_state_id_must_be_exact_field_not_summary(self) -> None:
        state = fresh_state()
        state["thread_id"] = ""
        state["summary"] = f"resume this: {SESSION_ID}"
        self.assertEqual(RESTART.state_resume_id(state, "codex"), "")

    def test_socketless_legacy_state_is_never_claimable(self) -> None:
        state = fresh_state()
        state.pop("tmux_socket")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(
                RESTART.ambiguous_legacy_state(
                    root, state, "/private/tmp/tmux-501/default"
                )
            )
            self.assertTrue(
                RESTART.ambiguous_legacy_state(
                    root, state, "/private/tmp/tmux-501/custom"
                )
            )
            self.assertTrue(
                RESURRECT.ambiguous_legacy_state(
                    root, state, "/private/tmp/other/default"
                )
            )
            tagged = dict(state, tmux_socket="/private/tmp/tmux-501/default")
            self.assertFalse(
                RESTART.ambiguous_legacy_state(root, tagged, "/any/socket")
            )

    def test_uncertain_pane_is_skipped_without_respawn(self) -> None:
        assistant_pane = pane()
        assistant_pane["command"] = "codex"
        with (
            mock.patch.object(RESTART, "list_panes", return_value=[assistant_pane]),
            mock.patch.object(RESTART, "process_rows", return_value=[("200", "100", "codex")]),
            mock.patch.object(RESTART, "process_argv", return_value=[]),
            mock.patch.object(RESTART, "process_lstart_epoch", return_value=None),
            mock.patch.object(RESTART, "saved_session_id", return_value=("", "")),
            redirect_stderr(StringIO()),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(RESTART.restart_panes(True, "all"), (1, 0, 1))

    def test_background_assistant_in_idle_shell_is_not_a_restart_target(self) -> None:
        idle_pane = pane()
        idle_pane["command"] = "zsh"
        with (
            mock.patch.object(RESTART, "list_panes", return_value=[idle_pane]),
            mock.patch.object(RESTART, "process_rows", return_value=[("200", "100", f"codex resume {SESSION_ID}")]),
            redirect_stderr(StringIO()),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(RESTART.restart_panes(True, "all"), (0, 0, 0))

    def test_multiple_direct_assistant_children_are_ambiguous(self) -> None:
        assistant_pane = pane()
        assistant_pane["command"] = "codex"
        rows = [
            ("200", "100", f"codex resume {SESSION_ID}"),
            ("201", "100", f"codex resume {OTHER_ID}"),
        ]
        with (
            mock.patch.object(RESTART, "list_panes", return_value=[assistant_pane]),
            mock.patch.object(RESTART, "process_rows", return_value=rows),
            mock.patch.object(RESTART, "process_argv", side_effect=lambda pid: ["codex"]),
            redirect_stderr(StringIO()),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(RESTART.restart_panes(True, "all"), (1, 0, 1))

    def test_process_identity_revalidation_checks_pid_start_argv_and_tool(self) -> None:
        assistant_pane = pane()
        assistant_pane["command"] = "codex"
        words = ["codex", "resume", SESSION_ID]
        live = {
            "pane_id": assistant_pane["pane_id"],
            "pane_pid": assistant_pane["pane_pid"],
            "command": "codex",
            "title": "codex",
            "socket_path": assistant_pane["socket_path"],
            "server_pid": assistant_pane["server_pid"],
        }
        with (
            mock.patch.object(
                RESTART, "live_pane_identity", return_value=live
            ) as live_identity,
            mock.patch.object(RESTART, "process_rows", return_value=[]),
            mock.patch.object(
                RESTART,
                "current_assistant_process",
                return_value=("200", words),
            ) as current_process,
            mock.patch.object(
                RESTART, "process_lstart_epoch", return_value=200.0
            ) as start_epoch,
        ):
            self.assertTrue(
                RESTART.process_identity_matches(
                    assistant_pane, "codex", "200", 200.0, words
                )
            )
            self.assertFalse(
                RESTART.process_identity_matches(
                    assistant_pane,
                    "codex",
                    "200",
                    200.0,
                    ["codex", "resume", OTHER_ID],
                )
            )
            current_process.return_value = ("201", words)
            self.assertFalse(
                RESTART.process_identity_matches(
                    assistant_pane, "codex", "200", 200.0, words
                )
            )
            current_process.return_value = ("200", words)
            start_epoch.return_value = 201.0
            self.assertFalse(
                RESTART.process_identity_matches(
                    assistant_pane, "codex", "200", 200.0, words
                )
            )
            start_epoch.return_value = 200.0
            live_identity.return_value = dict(live, command="zsh")
            self.assertFalse(
                RESTART.process_identity_matches(
                    assistant_pane, "codex", "200", 200.0, words
                )
            )

    def test_changed_process_is_not_killed_by_respawn(self) -> None:
        assistant_pane = pane()
        assistant_pane["command"] = "codex"
        rows = [("200", "100", f"codex resume {SESSION_ID}")]

        def fake_argv(pid: str) -> list[str]:
            return ["codex", "resume", SESSION_ID] if pid == "200" else []

        with (
            mock.patch.object(RESTART, "list_panes", return_value=[assistant_pane]),
            mock.patch.object(RESTART, "process_rows", return_value=rows),
            mock.patch.object(RESTART, "process_argv", side_effect=fake_argv),
            mock.patch.object(RESTART, "process_lstart_epoch", return_value=200.0),
            mock.patch.object(
                RESTART, "resolve_executable", return_value=CODEX_BIN
            ),
            mock.patch.object(RESTART, "process_identity_matches", return_value=False),
            mock.patch.object(
                RESTART, "pane_respawn_lock", return_value=nullcontext()
            ),
            mock.patch.object(RESTART, "tmux") as tmux_mock,
            mock.patch.object(RESTART.subprocess, "run"),
            redirect_stderr(StringIO()),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(RESTART.restart_panes(False, "all"), (1, 0, 1))
        tmux_mock.assert_not_called()

    def test_respawn_lock_covers_final_recheck_and_respawn(self) -> None:
        assistant_pane = pane()
        events: list[str] = []

        @contextmanager
        def fake_lock(_pane: dict[str, str]):
            events.append("lock-enter")
            try:
                yield
            finally:
                events.append("lock-exit")

        def identity_matches(*_args) -> bool:
            events.append("recheck")
            return True

        def fake_tmux(_args: list[str]) -> str:
            events.append("respawn")
            return ""

        with (
            mock.patch.object(RESTART, "pane_respawn_lock", side_effect=fake_lock),
            mock.patch.object(
                RESTART, "process_identity_matches", side_effect=identity_matches
            ),
            mock.patch.object(RESTART, "tmux", side_effect=fake_tmux),
        ):
            error = RESTART.verified_respawn(
                assistant_pane,
                "codex",
                "200",
                200.0,
                ["codex", "resume", SESSION_ID],
                "/tmp/project",
                "safe command",
            )

        self.assertEqual(error, "")
        self.assertEqual(
            events, ["lock-enter", "recheck", "respawn", "lock-exit"]
        )

    def test_respawn_lock_namespace_includes_socket_generation_and_pane(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            RESTART, "STATE_DIR", Path(directory)
        ):
            first = pane()
            next_generation = dict(first, server_pid="1000")
            other_socket = dict(first, socket_path="/tmp/other-tmux.sock")
            other_pane = dict(first, pane_id="%10")

            paths = {
                RESTART.respawn_lock_path(candidate)
                for candidate in (first, next_generation, other_socket, other_pane)
            }
            self.assertEqual(len(paths), 4)

            lock_path = RESTART.respawn_lock_path(first)
            with RESTART.pane_respawn_lock(first):
                self.assertTrue(lock_path.is_file())
                self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)
                self.assertEqual(
                    stat.S_IMODE(lock_path.parent.stat().st_mode), 0o700
                )

    def test_scrollback_uuid_fallback_does_not_exist(self) -> None:
        self.assertFalse(hasattr(RESTART, "capture_resume_id"))


class ResurrectSaveHookTests(unittest.TestCase):
    def test_existing_codex_resume_is_canonicalized_and_sanitized(self) -> None:
        parts = saved_pane(
            f"codex --model gpt-5 --yolo resume {SESSION_ID} old-prompt"
        )
        with verified_hook_process(
            [
                "codex",
                "--model",
                "gpt-5",
                "--yolo",
                "resume",
                SESSION_ID,
                "old-prompt",
            ]
        ):
            rewritten = RESURRECT.rewrite_pane_line(parts)
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10],
            f":{CODEX_BIN} --model gpt-5 resume {SESSION_ID}",
        )

    def test_existing_claude_resume_is_canonicalized_and_sanitized(self) -> None:
        parts = saved_pane(
            f"claude --effort high --resume {SESSION_ID} --dangerously-skip-permissions",
            pane_command="claude",
        )
        with verified_hook_process(
            [
                "claude",
                "--effort",
                "high",
                "--resume",
                SESSION_ID,
                "--dangerously-skip-permissions",
            ],
            "claude",
        ):
            rewritten = RESURRECT.rewrite_pane_line(parts)
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10],
            f":{CLAUDE_BIN} --effort high --resume {SESSION_ID}",
        )

    def test_verified_clean_resume_is_left_unchanged(self) -> None:
        with verified_hook_process(["codex", "resume", SESSION_ID]):
            self.assertIsNone(
                RESURRECT.rewrite_pane_line(
                    saved_pane(f"{CODEX_BIN} resume {SESSION_ID}")
                )
            )

    def test_verified_permission_overrides_are_removed(self) -> None:
        command = (
            "codex --sandbox read-only --ask-for-approval untrusted "
            f"resume {SESSION_ID}"
        )
        with verified_hook_process(shlex.split(command)):
            rewritten = RESURRECT.rewrite_pane_line(saved_pane(command))
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10], f":{CODEX_BIN} resume {SESSION_ID}"
        )

    def test_unverified_bare_resume_restores_a_login_shell(self) -> None:
        with mock.patch.object(RESURRECT, "pane_info_for_target", return_value={}):
            rewritten = RESURRECT.rewrite_pane_line(saved_pane("codex --yolo resume"))
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        command = rewritten.split("\t")[10][1:]
        self.assertTrue(command.startswith("exec /"))
        self.assertNotIn("codex", command)
        self.assertNotIn("yolo", command)

    def test_unresolvable_live_executable_restores_login_shell(self) -> None:
        with (
            verified_hook_process(["codex", "resume", SESSION_ID]),
            mock.patch.object(RESURRECT, "resolve_executable", return_value=""),
        ):
            rewritten = RESURRECT.rewrite_pane_line(
                saved_pane(f"codex resume {SESSION_ID}")
            )
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        command = rewritten.split("\t")[10][1:]
        self.assertTrue(command.startswith("exec /"))
        self.assertNotIn(SESSION_ID, command)

    def test_lossy_snapshot_prompt_uuid_is_not_treated_as_resume_id(self) -> None:
        # tmux-resurrect's default ps strategy flattens the one positional
        # prompt below into the same text as a real `codex resume UUID` argv.
        exact_words = ["codex", f"resume {SESSION_ID}"]
        with (
            verified_hook_process(exact_words),
            mock.patch.object(RESURRECT, "process_lstart_epoch", return_value=200.0),
            mock.patch.object(RESURRECT, "matching_state", return_value=({}, "")),
        ):
            rewritten = RESURRECT.rewrite_pane_line(
                saved_pane(f"codex resume {SESSION_ID}")
            )
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        command = rewritten.split("\t")[10][1:]
        self.assertTrue(command.startswith("exec /"))
        self.assertNotIn(SESSION_ID, command)

    def test_restore_flags_come_from_live_exact_argv_not_snapshot(self) -> None:
        saved = saved_pane(f"codex --model stale resume {SESSION_ID}")
        with verified_hook_process(
            ["codex", "--model", "current", "resume", SESSION_ID]
        ):
            rewritten = RESURRECT.rewrite_pane_line(saved)
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10],
            f":{CODEX_BIN} --model current resume {SESSION_ID}",
        )

    def test_fresh_state_without_exact_argv_builds_minimal_resume(self) -> None:
        info = hook_pane()
        with (
            mock.patch.object(RESURRECT, "pane_info_for_target", return_value=info),
            mock.patch.object(RESURRECT, "process_rows", return_value=[]),
            mock.patch.object(
                RESURRECT, "current_assistant_process", return_value=("", [])
            ),
            mock.patch.object(RESURRECT, "process_argv", return_value=[]),
            mock.patch.object(
                RESURRECT, "direct_assistant_child_start_epoch", return_value=200.0
            ),
            mock.patch.object(
                RESURRECT,
                "matching_state",
                return_value=(fresh_state(), SESSION_ID),
            ),
            mock.patch.object(
                RESURRECT, "resolve_executable", return_value=CODEX_BIN
            ),
        ):
            rewritten = RESURRECT.rewrite_pane_line(
                saved_pane(
                    f"codex --model stale --search --yolo resume {SESSION_ID}"
                )
            )
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10], f":{CODEX_BIN} resume {SESSION_ID}"
        )

    def test_multiple_live_exact_assistants_are_ambiguous(self) -> None:
        info = hook_pane()
        children = {
            "100": [
                ("200", f"codex resume {SESSION_ID}"),
                ("201", f"codex resume {OTHER_ID}"),
            ]
        }

        def fake_argv(pid: str) -> list[str]:
            if pid == "200":
                return ["codex", "resume", SESSION_ID]
            if pid == "201":
                return ["codex", "resume", OTHER_ID]
            return []

        with mock.patch.object(RESURRECT, "process_argv", side_effect=fake_argv):
            self.assertEqual(
                RESURRECT.current_assistant_process(info, "codex", children),
                ("", []),
            )

    def test_fresh_matching_state_injects_id_and_preserves_safe_flags(self) -> None:
        with (
            verified_hook_process(["codex", "--model", "gpt-5"]),
            mock.patch.object(RESURRECT, "direct_assistant_child_start_epoch", return_value=200.0),
            mock.patch.object(RESURRECT, "process_lstart_epoch", return_value=200.0),
            mock.patch.object(RESURRECT, "matching_state", return_value=(fresh_state(), SESSION_ID)),
        ):
            rewritten = RESURRECT.rewrite_pane_line(
                saved_pane("codex --model gpt-5 --profile work --yolo resume")
            )
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10],
            f":{CODEX_BIN} --model gpt-5 resume {SESSION_ID}",
        )

    def test_versioned_claude_binary_is_sanitized(self) -> None:
        command = (
            "/Users/me/.local/share/claude/versions/2.1.220 "
            f"--settings '{{\"permissionMode\":\"bypassPermissions\"}}' "
            f"--dangerously-skip-permissions --resume {SESSION_ID}"
        )
        exact_words = [
            "/Users/me/.local/share/claude/versions/2.1.220",
            "--settings",
            '{"permissionMode":"bypassPermissions"}',
            "--dangerously-skip-permissions",
            "--resume",
            SESSION_ID,
        ]
        with verified_hook_process(exact_words, "claude"):
            rewritten = RESURRECT.rewrite_pane_line(
                saved_pane(command, pane_command="2.1.220")
            )
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(
            rewritten.split("\t")[10],
            f":{CLAUDE_BIN} --resume {SESSION_ID}",
        )

    def test_malformed_resurrect_lines_are_ignored(self) -> None:
        malformed = saved_pane("codex")
        malformed[2] = "not-a-window"
        self.assertIsNone(RESURRECT.rewrite_pane_line(malformed))
        malformed = saved_pane("codex")
        malformed[10] = "codex"
        self.assertIsNone(RESURRECT.rewrite_pane_line(malformed))

    def test_save_rewrite_is_atomic_and_converges_mode_to_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.txt"
            path.write_text("\t".join(saved_pane("codex resume")) + "\n", encoding="utf-8")
            path.chmod(0o640)
            before_inode = path.stat().st_ino
            with mock.patch.object(RESURRECT, "pane_info_for_target", return_value={}):
                RESURRECT.rewrite_file(path)
            self.assertNotEqual(path.stat().st_ino, before_inode)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))
            self.assertNotIn("codex resume", path.read_text(encoding="utf-8"))

    def test_mode_only_snapshot_hardening_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.txt"
            original = b"window\tmain\t1\t:work\r\n\r\n"
            path.write_bytes(original)
            path.chmod(0o644)
            before_inode = path.stat().st_ino

            RESURRECT.rewrite_file(path)

            self.assertNotEqual(path.stat().st_ino, before_inode)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
