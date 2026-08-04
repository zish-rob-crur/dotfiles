#!/usr/bin/env python3

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex-window-badges-refresh.sh")
STATE_HELPER = Path(__file__).with_name("assistant_completion_state.py")


def detect(tool: str, pane: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--detect", tool],
        input=pane,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def identify(command: str, title: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--identify", command, title],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


FAKE_TMUX = r"""#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-S" ]]; then
  shift 2
fi
printf '%s\n' "$*" >> "${TMUX_LOG}"

case "${1:-}" in
  display-message)
    case "$*" in
      *'#{pid}'*'#{start_time}'*'#{socket_path}'*)
        printf '%s\t%s\t%s\n' \
          "${FAKE_TMUX_SERVER_PID}" \
          "${FAKE_TMUX_SERVER_START}" \
          "${FAKE_TMUX_SOCKET}"
        ;;
      *socket_path*) printf '%s\n' "${FAKE_TMUX_SOCKET}" ;;
      *codex-badge*)
        if [[ -n "${FAKE_OLD_BADGE+x}" ]]; then
          printf '@1\t%s\n' "${FAKE_OLD_BADGE}"
        else
          printf '@1\t'
          cat "${TMUX_BADGE_FILE}"
          printf '\n'
        fi
        ;;
      *) printf '@1\n' ;;
    esac
    ;;
  list-windows)
    if [[ -n "${TMUX_WINDOWS_FILE:-}" ]]; then
      cat "${TMUX_WINDOWS_FILE}"
    fi
    ;;
  list-panes)
    if [[ "${FAKE_TMUX_LIST_PANES_FAIL:-0}" == "1" ]]; then
      exit 42
    fi
    if [[ "${FAKE_TMUX_LIST_PANES_EMPTY:-0}" == "1" ]]; then
      exit 0
    fi
    if [[ "$*" == *'#{window_id}'* ]] && [[ -n "${TMUX_PANES_FILE:-}" ]]; then
      cat "${TMUX_PANES_FILE}"
    elif [[ -n "${TMUX_PANES_FILE:-}" ]]; then
      cut -f2 "${TMUX_PANES_FILE}" | awk '!seen[$0]++'
    fi
    ;;
  list-clients)
    if [[ "${FAKE_TMUX_LIST_CLIENTS_FAIL:-0}" == "1" ]]; then
      exit 42
    fi
    printf '%s\t%s\n' \
      "${FAKE_CLIENT_PANE:-%1}" \
      "${FAKE_CLIENT_FLAGS:-attached,focused,UTF-8}"
    ;;
  capture-pane)
    if [[ -n "${FAKE_TMUX_CAPTURE_BLOCK_FILE:-}" ]]; then
      while [[ ! -e "${FAKE_TMUX_CAPTURE_BLOCK_FILE}" ]]; do
        sleep 0.02
      done
    fi
    printf '%s' "${FAKE_CAPTURE_CONTENT:-}"
    if [[ -n "${FAKE_TMUX_REPLACE_SOCKET_ON_CAPTURE:-}" ]]; then
      mv "${FAKE_TMUX_REPLACE_SOCKET_ON_CAPTURE}" "${FAKE_TMUX_SOCKET}"
    fi
    ;;
  set-window-option)
    printf '%s' "${!#}" > "${TMUX_BADGE_FILE}"
    ;;
  has-session)
    if [[ -n "${FAKE_TMUX_HAS_SESSION_BLOCK_ONCE_FILE:-}" ]] &&
      [[ ! -e "${FAKE_TMUX_HAS_SESSION_BLOCK_ONCE_FILE}" ]]; then
      : > "${FAKE_TMUX_HAS_SESSION_BLOCK_ONCE_FILE}"
      sleep "${FAKE_TMUX_HAS_SESSION_BLOCK_SECONDS:-2}"
    fi
    ;;
  refresh-client)
    ;;
esac
"""


class FakeTmuxEnvironment:
    def __init__(self, root: Path, socket: str = "/tmp/fake-tmux.sock") -> None:
        self.root = root
        self._socket_paths: dict[str, str] = {}
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.log = root / "tmux.log"
        self.log.write_text("", encoding="utf-8")
        self.badge = root / "badge.txt"
        self.badge.write_text("", encoding="utf-8")
        self.windows = root / "windows.tsv"
        self.windows.write_text("@1\t\n", encoding="utf-8")
        self.panes = root / "panes.tsv"
        self.panes.write_text("@1\t%1\tcodex\tCodex\n", encoding="utf-8")
        tmux = self.bin_dir / "tmux"
        tmux.write_text(FAKE_TMUX, encoding="utf-8")
        tmux.chmod(0o755)
        socket = self.socket_path(socket)
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env['PATH']}",
                "XDG_CACHE_HOME": str(root / "cache"),
                "TMUX_LOG": str(self.log),
                "TMUX_BADGE_FILE": str(self.badge),
                "TMUX_WINDOWS_FILE": str(self.windows),
                "TMUX_PANES_FILE": str(self.panes),
                "FAKE_TMUX_SOCKET": socket,
                "FAKE_TMUX_SERVER_PID": "1234",
                "FAKE_TMUX_SERVER_START": "1700000000",
                "TMUX": f"{socket},1234,0",
                "CODEX_TMUX_BADGE_GC_INTERVAL": "999999",
            }
        )

    def socket_path(self, socket: str) -> str:
        if socket in self._socket_paths:
            return self._socket_paths[socket]
        requested = Path(socket)
        if requested.parent == Path("/tmp") and requested.name.startswith("fake-tmux"):
            actual = self.root / requested.name
        else:
            actual = requested
        if not actual.exists():
            actual.parent.mkdir(parents=True, exist_ok=True)
            actual.write_text("fake tmux socket generation", encoding="utf-8")
        value = str(actual)
        self._socket_paths[socket] = value
        self._socket_paths[value] = value
        return value

    @property
    def state_root(self) -> Path:
        return Path(self.env["XDG_CACHE_HOME"]) / "codex-tmux-status"

    def server_state_dir(self, socket=None) -> Path:
        socket = self.socket_path(socket or self.env["FAKE_TMUX_SOCKET"])
        result = subprocess.run(
            [
                sys.executable,
                str(STATE_HELPER),
                "--state-dir",
                str(self.state_root),
                "--tmux-socket",
                socket,
                "--resolve-state-dir",
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        return Path(result.stdout.strip())

    @property
    def state_dir(self) -> Path:
        return self.server_state_dir()

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()

    def socket_env(self, socket: str) -> dict[str, str]:
        socket = self.socket_path(socket)
        environment = self.env.copy()
        environment.update(
            {
                "FAKE_TMUX_SOCKET": socket,
                "TMUX": f"{socket},5678,0",
            }
        )
        return environment

    def run(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.script_command(*arguments),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )

    def script_command(self, *arguments: str) -> list[str]:
        expanded = list(arguments)
        if expanded[:1] == ["--ack-pane"] and len(expanded) == 2:
            expanded.extend(
                [
                    self.env["FAKE_TMUX_SERVER_PID"],
                    self.env["FAKE_TMUX_SERVER_START"],
                    self.env["FAKE_TMUX_SOCKET"],
                ]
            )
        return ["bash", str(SCRIPT), *expanded]

    def run_for_socket(
        self, socket: str, *arguments: str
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), *arguments],
            env=self.socket_env(socket),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )


class PaneStatusDetectionTest(unittest.TestCase):
    def test_identifies_claude_while_its_version_is_the_process_name(self) -> None:
        self.assertEqual(identify("2.1.220", "✳ Task title"), "claude")
        self.assertEqual(identify("2.1.220", "⠋ Task title"), "claude")

    def test_codex_working(self) -> None:
        self.assertEqual(
            detect("codex", "\n• Working (8s • esc to interrupt)\n› "),
            "working",
        )

    def test_codex_waiting(self) -> None:
        self.assertEqual(
            detect("codex", "Run this command?\n› 1. Yes\n  2. No"),
            "waiting",
        )

    def test_codex_error_takes_priority(self) -> None:
        self.assertEqual(
            detect(
                "codex",
                "› 1. Try again\n■ Unexpected error while contacting the server",
            ),
            "errored",
        )

    def test_latest_working_line_beats_stale_dialog_text(self) -> None:
        self.assertEqual(
            detect(
                "codex",
                "› 1. Old dialog\n■ Old error\n• Working (8s • esc to interrupt)",
            ),
            "working",
        )

    def test_permission_dialog_below_working_line_is_waiting(self) -> None:
        self.assertEqual(
            detect(
                "codex",
                "• Working (8s • esc to interrupt)\nRun this command?\n› 1. Yes\n  2. No",
            ),
            "waiting",
        )

    def test_claude_working(self) -> None:
        self.assertEqual(
            detect("claude", "✳ Drizzling… (6s · esc to interrupt)\n❯ "),
            "working",
        )

    def test_claude_waiting_accepts_nonbreaking_space(self) -> None:
        self.assertEqual(
            detect("claude", "Allow this action?\n❯\u00a01. Yes\n  2. No"),
            "waiting",
        )

    def test_claude_error(self) -> None:
        self.assertEqual(detect("claude", "Error: request failed\n❯ "), "errored")

    def test_finished_or_idle_text_has_no_live_status(self) -> None:
        self.assertEqual(detect("codex", "• 已完成。\n\n› "), "")
        self.assertEqual(detect("claude", "✻ Baked for 15s\n\n❯ "), "")

    def test_old_control_text_outside_tail_is_ignored(self) -> None:
        pane = "Error: old failure\n" + "\n".join(f"line {i}" for i in range(20))
        self.assertEqual(detect("claude", pane), "")


class BadgeStateAndRefreshTest(unittest.TestCase):
    def test_missing_tmux_environment_exits_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            environment.pop("TMUX", None)
            environment.update(
                {
                    "PATH": "/usr/bin:/bin",
                    "XDG_CACHE_HOME": temporary,
                }
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), "--force"],
                env=environment,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ack_is_atomic_private_and_preserves_resume_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.badge.write_text("done", encoding="utf-8")
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            state_path = fake.state_dir / "pane-1.json"
            state_path.write_text(
                json.dumps(
                    {
                        "thread_id": "019cafe0-0000-7000-8000-000000000001",
                        "cwd": "/tmp/project",
                        "unread": True,
                    }
                ),
                encoding="utf-8",
            )

            fake.run("--ack-pane", "%1")
            first_calls = fake.calls()
            fake.run("--ack-pane", "%1")

            state = json.loads(state_path.read_text(encoding="utf-8"))
            marker_path = fake.state_dir / "pane-1.ack"
            state_lock_path = fake.state_dir / "pane-1.state.lock"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(
                state["thread_id"], "019cafe0-0000-7000-8000-000000000001"
            )
            self.assertFalse(state["unread"])
            self.assertEqual(marker["pane_id"], "%1")
            self.assertGreater(marker["acked_at_ns"], 0)
            self.assertEqual(stat.S_IMODE(fake.state_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(marker_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(state_lock_path.stat().st_mode), 0o600)

            all_calls = fake.calls()
            self.assertEqual(
                sum(call.startswith("capture-pane ") for call in all_calls), 2
            )
            self.assertEqual(
                sum(call.startswith("list-windows ") for call in all_calls), 0
            )
            self.assertEqual(
                sum(call.startswith("list-panes ") for call in all_calls), 2
            )
            new_calls = all_calls[len(first_calls) :]
            self.assertEqual(
                new_calls[0],
                "display-message -p #{pid}\t#{start_time}\t#{socket_path}",
            )
            self.assertEqual(sum(call.startswith("capture-pane ") for call in new_calls), 1)
            self.assertFalse(any(call.startswith("list-windows ") for call in new_calls))
            self.assertEqual(fake.badge.read_text(encoding="utf-8"), "")

    def test_ack_writes_watermark_even_before_completion_state_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.run("--ack-pane", "%1")

            marker = json.loads(
                (fake.state_dir / "pane-1.ack").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["pane_id"], "%1")
            self.assertGreater(marker["acked_at_ns"], 0)
            calls = fake.calls()
            self.assertEqual(
                calls[0],
                "display-message -p #{pid}\t#{start_time}\t#{socket_path}",
            )
            self.assertEqual(
                sum(" list-clients " in f" {call} " for call in calls), 2
            )

    def test_ack_waits_for_old_renderer_then_clears_its_done_badge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = FakeTmuxEnvironment(root)
            release_capture = root / "release-capture"
            fake.env["FAKE_TMUX_CAPTURE_BLOCK_FILE"] = str(release_capture)
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            state_path = fake.state_dir / "pane-1.json"
            state_path.write_text(
                json.dumps(
                    {
                        "pane_id": "%1",
                        "thread_id": "root",
                        "completed_at_ns": 1,
                        "unread": True,
                    }
                ),
                encoding="utf-8",
            )

            renderer = subprocess.Popen(
                ["bash", str(SCRIPT), "--force"],
                env=fake.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ack = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not any(
                    call.startswith("capture-pane ") for call in fake.calls()
                ):
                    time.sleep(0.02)
                self.assertTrue(any(call.startswith("capture-pane ") for call in fake.calls()))

                ack = subprocess.Popen(
                    fake.script_command("--ack-pane", "%1"),
                    env=fake.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.1)
                self.assertIsNone(ack.poll())
                release_capture.touch()
                renderer.communicate(timeout=5)
                ack.communicate(timeout=5)
                self.assertEqual(renderer.returncode, 0, renderer.stderr)
                self.assertEqual(ack.returncode, 0, ack.stderr)
            finally:
                release_capture.touch(exist_ok=True)
                if renderer.poll() is None:
                    renderer.terminate()
                    renderer.communicate(timeout=5)
                if ack is not None and ack.poll() is None:
                    ack.terminate()
                    ack.communicate(timeout=5)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(state["unread"])
            self.assertEqual(fake.badge.read_text(encoding="utf-8"), "")
            set_calls = [
                call for call in fake.calls() if call.startswith("set-window-option ")
            ]
            self.assertTrue(any("󰄬" in call for call in set_calls))
            self.assertNotIn("󰄬", set_calls[-1])
            self.assertEqual(
                stat.S_IMODE((fake.state_dir / "refresh-render.lock").stat().st_mode),
                0o600,
            )

    def test_locked_renderer_aborts_if_socket_generation_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "tmux.sock"
            socket_path.write_text("generation-one", encoding="utf-8")
            fake = FakeTmuxEnvironment(root, str(socket_path))
            expected_state_dir = fake.state_dir

            replacement = root / "replacement.sock"
            replacement.write_text("generation-two", encoding="utf-8")
            os.replace(replacement, socket_path)
            current_state_dir = fake.server_state_dir(str(socket_path))
            self.assertNotEqual(expected_state_dir, current_state_dir)

            environment = fake.env.copy()
            environment.update(
                {
                    "CODEX_TMUX_RENDER_LOCK_HELD": "1",
                    "CODEX_TMUX_EXPECTED_SERVER_STATE_DIR": str(expected_state_dir),
                }
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), "--render-force-locked"],
                env=environment,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(call.startswith("list-windows ") for call in fake.calls()))
            self.assertFalse(any(call.startswith("capture-pane ") for call in fake.calls()))

    def test_renderer_aborts_before_write_if_socket_rebinds_during_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "tmux.sock"
            socket_path.write_text("generation-one", encoding="utf-8")
            fake = FakeTmuxEnvironment(root, str(socket_path))
            original_state_dir = fake.state_dir
            replacement = root / "replacement.sock"
            replacement.write_text("generation-two", encoding="utf-8")
            fake.env.update(
                {
                    "FAKE_CAPTURE_CONTENT": "• Working (esc to interrupt)",
                    "FAKE_TMUX_REPLACE_SOCKET_ON_CAPTURE": str(replacement),
                }
            )

            fake.run("--force")

            self.assertNotEqual(original_state_dir, fake.server_state_dir())
            self.assertTrue(any(call.startswith("capture-pane ") for call in fake.calls()))
            self.assertFalse(
                any(call.startswith("set-window-option ") for call in fake.calls())
            )

    def test_delayed_focus_hook_does_not_ack_after_client_switched_away(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.env["FAKE_CLIENT_PANE"] = "%2"
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            state_path = fake.state_dir / "pane-1.json"
            state_path.write_text(
                json.dumps(
                    {
                        "thread_id": "root",
                        "completed_at_ns": 1,
                        "unread": True,
                    }
                ),
                encoding="utf-8",
            )

            fake.run("--ack-pane", "%1")

            self.assertTrue(json.loads(state_path.read_text())["unread"])
            self.assertFalse((fake.state_dir / "pane-1.ack").exists())

    def test_failed_focus_query_does_not_write_ack_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.env["FAKE_TMUX_LIST_CLIENTS_FAIL"] = "1"
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            state_path = fake.state_dir / "pane-1.json"
            state_path.write_text(
                json.dumps(
                    {
                        "thread_id": "root",
                        "completed_at_ns": 1,
                        "unread": True,
                    }
                ),
                encoding="utf-8",
            )

            fake.run("--ack-pane", "%1")

            self.assertTrue(json.loads(state_path.read_text())["unread"])
            self.assertFalse((fake.state_dir / "pane-1.ack").exists())

    def test_ack_lock_preserves_a_concurrent_new_resume_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            state_path = fake.state_dir / "pane-1.json"
            state_path.write_text(
                json.dumps({"thread_id": "old", "unread": True}), encoding="utf-8"
            )
            lock_path = fake.state_dir / "pane-1.state.lock"
            writer_code = r"""
import fcntl, json, os, sys, tempfile
from pathlib import Path
state_path, lock_path = map(Path, sys.argv[1:])
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
print("ready", flush=True)
sys.stdin.read(1)
fd, temporary = tempfile.mkstemp(dir=state_path.parent)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump({"thread_id": "new", "unread": True}, handle)
os.replace(temporary, state_path)
fcntl.flock(lock_fd, fcntl.LOCK_UN)
os.close(lock_fd)
"""
            writer = subprocess.Popen(
                [sys.executable, "-c", writer_code, str(state_path), str(lock_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ack = None
            try:
                self.assertEqual(writer.stdout.readline().strip(), "ready")
                ack = subprocess.Popen(
                    fake.script_command("--ack-pane", "%1"),
                    env=fake.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.05)
                self.assertIsNone(ack.poll())
                writer.stdin.write("x")
                writer.stdin.flush()
                writer.communicate(timeout=5)
                ack.communicate(timeout=5)
                self.assertEqual(writer.returncode, 0)
                self.assertEqual(ack.returncode, 0)
            finally:
                if writer.poll() is None:
                    writer.terminate()
                    writer.communicate(timeout=5)
                if ack is not None and ack.poll() is None:
                    ack.terminate()
                    ack.communicate(timeout=5)

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["thread_id"], "new")
            self.assertFalse(state["unread"])

    def test_grouped_sessions_capture_each_unique_pane_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.windows.write_text("@1\t\n@1\t\n", encoding="utf-8")
            fake.panes.write_text(
                "@1\t%1\tcodex\tCodex\n@1\t%1\tcodex\tCodex\n",
                encoding="utf-8",
            )
            trace_path = Path(temporary) / "badge.trace"
            fake.env["CODEX_TMUX_BADGE_TRACE_FILE"] = str(trace_path)
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            (fake.state_dir / "pane-1.json").write_text(
                json.dumps({"thread_id": "root", "unread": True}),
                encoding="utf-8",
            )

            fake.run("--force")

            calls = fake.calls()
            self.assertEqual(sum(call.startswith("capture-pane ") for call in calls), 1)
            self.assertEqual(
                sum(call.startswith("set-window-option ") for call in calls), 1
            )
            self.assertTrue(
                any("󰄬" in call for call in calls if call.startswith("set-window-option "))
            )
            trace = trace_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(line.startswith("window\t") for line in trace), 1)
            self.assertEqual(sum(line.startswith("capture\t") for line in trace), 1)

    def test_done_badge_requires_explicit_unread_true(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.state_dir.mkdir(parents=True, exist_ok=True)
            state_path = fake.state_dir / "pane-1.json"

            for value in (False, None):
                payload = {"thread_id": "durable"}
                if value is not None:
                    payload["unread"] = value
                state_path.write_text(json.dumps(payload), encoding="utf-8")
                fake.log.write_text("", encoding="utf-8")
                fake.run("--force")
                self.assertFalse(any("󰄬" in call for call in fake.calls()))


class MultiServerStateIsolationTest(unittest.TestCase):
    SOCKET_A = "/tmp/fake-tmux-a.sock"
    SOCKET_B = "/tmp/fake-tmux-b.sock"

    def write_state(
        self,
        directory: Path,
        pane_number: int,
        thread_id: str,
        unread: bool,
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"pane-{pane_number}.json"
        path.write_text(
            json.dumps(
                {
                    "pane_id": f"%{pane_number}",
                    "thread_id": thread_id,
                    "unread": unread,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_same_pane_id_reads_only_the_current_server_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary), self.SOCKET_A)
            state_a = fake.server_state_dir(self.SOCKET_A)
            state_b = fake.server_state_dir(self.SOCKET_B)
            path_a = self.write_state(state_a, 1, "thread-a", False)
            self.write_state(state_b, 1, "thread-b", True)
            # A legacy/shared root state must not leak into either server.
            self.write_state(fake.state_root, 1, "legacy", True)

            fake.run("--force")
            self.assertFalse(any("󰄬" in call for call in fake.calls()))
            self.assertEqual(json.loads(path_a.read_text())["thread_id"], "thread-a")

            fake.log.write_text("", encoding="utf-8")
            fake.run_for_socket(self.SOCKET_B, "--force")
            self.assertTrue(any("󰄬" in call for call in fake.calls()))

    def test_ack_for_same_pane_id_does_not_touch_other_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary), self.SOCKET_A)
            state_a = fake.server_state_dir(self.SOCKET_A)
            state_b = fake.server_state_dir(self.SOCKET_B)
            path_a = self.write_state(state_a, 1, "thread-a", True)
            path_b = self.write_state(state_b, 1, "thread-b", True)
            legacy_path = self.write_state(fake.state_root, 1, "legacy", True)

            fake.run("--ack-pane", "%1")

            payload_a = json.loads(path_a.read_text(encoding="utf-8"))
            payload_b = json.loads(path_b.read_text(encoding="utf-8"))
            self.assertFalse(payload_a["unread"])
            self.assertEqual(payload_a["thread_id"], "thread-a")
            self.assertTrue(payload_b["unread"])
            self.assertEqual(payload_b["thread_id"], "thread-b")
            self.assertTrue(json.loads(legacy_path.read_text())["unread"])
            self.assertTrue((state_a / "pane-1.ack").is_file())
            self.assertFalse((state_b / "pane-1.ack").exists())

    def test_gc_removes_stale_files_only_from_current_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary), self.SOCKET_A)
            fake.env["CODEX_TMUX_BADGE_GC_INTERVAL"] = "0"
            state_a = fake.server_state_dir(self.SOCKET_A)
            state_b = fake.server_state_dir(self.SOCKET_B)
            stale_a = self.write_state(state_a, 2, "thread-a", True)
            stale_b = self.write_state(state_b, 2, "thread-b", True)
            ack_a = state_a / "pane-2.ack"
            ack_b = state_b / "pane-2.ack"
            ack_a.write_text("{}\n", encoding="utf-8")
            ack_b.write_text("{}\n", encoding="utf-8")

            fake.run("--force")

            self.assertFalse(stale_a.exists())
            self.assertFalse(ack_a.exists())
            self.assertTrue(stale_b.exists())
            self.assertTrue(ack_b.exists())

    def test_gc_failure_preserves_all_state_and_retries_next_round(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary), self.SOCKET_A)
            fake.env.update(
                {
                    "CODEX_TMUX_BADGE_GC_INTERVAL": "0",
                    "FAKE_TMUX_LIST_PANES_FAIL": "1",
                }
            )
            state_dir = fake.server_state_dir(self.SOCKET_A)
            state_path = self.write_state(state_dir, 2, "thread-a", True)
            ack_path = state_dir / "pane-2.ack"
            ack_path.write_text("{}\n", encoding="utf-8")

            fake.run("--force")

            self.assertTrue(state_path.exists())
            self.assertTrue(ack_path.exists())
            self.assertFalse((state_dir / ".gc-stamp").exists())

    def test_active_daemon_empty_pane_set_preserves_all_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary), self.SOCKET_A)
            fake.env.update(
                {
                    "CODEX_TMUX_BADGE_GC_INTERVAL": "0",
                    "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "1",
                    "FAKE_TMUX_LIST_PANES_EMPTY": "1",
                }
            )
            state_dir = fake.server_state_dir(self.SOCKET_A)
            state_path = self.write_state(state_dir, 2, "thread-a", True)
            ack_path = state_dir / "pane-2.ack"
            ack_path.write_text("{}\n", encoding="utf-8")

            fake.run("--daemon")

            self.assertTrue(state_path.exists())
            self.assertTrue(ack_path.exists())
            self.assertFalse((state_dir / ".gc-stamp").exists())

    def test_refresh_stamps_are_independent_per_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary), self.SOCKET_A)
            fake.env["CODEX_TMUX_BADGE_REFRESH_INTERVAL"] = "999999"

            fake.run()
            second_env = fake.socket_env(self.SOCKET_B)
            second_env["CODEX_TMUX_BADGE_REFRESH_INTERVAL"] = "999999"
            subprocess.run(
                ["bash", str(SCRIPT)],
                env=second_env,
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertEqual(
                sum(call.startswith("list-windows ") for call in fake.calls()), 2
            )
            self.assertTrue(
                (fake.server_state_dir(self.SOCKET_A) / ".refresh-stamp").is_file()
            )
            self.assertTrue(
                (fake.server_state_dir(self.SOCKET_B) / ".refresh-stamp").is_file()
            )
            self.assertFalse((fake.state_root / ".refresh-stamp").exists())


class BadgeDaemonTest(unittest.TestCase):
    def wait_for_calls(
        self, fake: FakeTmuxEnvironment, prefix: str, count: int, timeout: float = 5
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sum(call.startswith(prefix) for call in fake.calls()) >= count:
                return
            time.sleep(0.02)
        self.fail(f"timed out waiting for {count} {prefix!r} calls")

    def test_concurrent_launches_have_one_loop_owner_per_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.env.update(
                {
                    "CODEX_TMUX_BADGE_DAEMON_INTERVAL": "0.05",
                    "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "3",
                }
            )
            first = subprocess.Popen(
                ["bash", str(SCRIPT), "--daemon"],
                env=fake.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.wait_for_calls(fake, "list-windows ", 1)
                second = subprocess.run(
                    ["bash", str(SCRIPT), "--daemon"],
                    env=fake.env,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
                self.assertEqual(second.returncode, 0)
                first.communicate(timeout=5)
                self.assertEqual(first.returncode, 0)
            finally:
                if first.poll() is None:
                    first.terminate()
                    first.communicate(timeout=5)

            calls = fake.calls()
            self.assertEqual(sum(call.startswith("list-windows ") for call in calls), 6)
            lock_files = list(fake.state_root.glob(".refresh-daemon-*.lock"))
            self.assertEqual(len(lock_files), 1)
            self.assertEqual(
                json.loads(lock_files[0].read_text(encoding="utf-8")), {}
            )

    def test_sigterm_during_blocked_render_hands_lock_to_same_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = FakeTmuxEnvironment(root)
            blocked = root / "release-old-render"
            first_env = fake.env.copy()
            first_env.update(
                {
                    "CODEX_TMUX_BADGE_DAEMON_INTERVAL": "0.05",
                    "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "100",
                    "CODEX_TMUX_BADGE_RENDER_TIMEOUT": "10",
                    "FAKE_TMUX_CAPTURE_BLOCK_FILE": str(blocked),
                }
            )
            first = subprocess.Popen(
                ["bash", str(SCRIPT), "--daemon"],
                env=first_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            second = None
            try:
                self.wait_for_calls(fake, "capture-pane ", 1)
                lock_path = next(fake.state_root.glob(".refresh-daemon-*.lock"))
                old_owner = json.loads(lock_path.read_text(encoding="utf-8"))["pid"]
                self.assertEqual(old_owner, first.pid)

                first.terminate()
                second_env = fake.env.copy()
                second_env.update(
                    {
                        "CODEX_TMUX_BADGE_DAEMON_INTERVAL": "0.05",
                        "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "100",
                    }
                )
                second = subprocess.Popen(
                    ["bash", str(SCRIPT), "--daemon"],
                    env=second_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                deadline = time.monotonic() + 5
                new_metadata = {}
                while time.monotonic() < deadline:
                    try:
                        new_metadata = json.loads(lock_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        new_metadata = {}
                    if new_metadata.get("pid") == second.pid:
                        break
                    time.sleep(0.02)
                self.assertEqual(new_metadata.get("pid"), second.pid)
                self.assertFalse(new_metadata.get("stopping"))
                first.communicate(timeout=5)
                self.assertEqual(first.returncode, 0)
                self.assertIsNone(second.poll())
            finally:
                blocked.touch(exist_ok=True)
                if first.poll() is None:
                    first.terminate()
                    first.communicate(timeout=5)
                if second is not None and second.poll() is None:
                    second.terminate()
                    second.communicate(timeout=5)

    def test_blocked_render_times_out_without_killing_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = FakeTmuxEnvironment(root)
            fake.env.update(
                {
                    "CODEX_TMUX_BADGE_DAEMON_INTERVAL": "0.01",
                    "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "2",
                    "CODEX_TMUX_BADGE_RENDER_TIMEOUT": "0.75",
                    "FAKE_TMUX_CAPTURE_BLOCK_FILE": str(root / "never-release"),
                }
            )

            fake.run("--daemon")

            self.assertEqual(
                sum(call.startswith("capture-pane ") for call in fake.calls()), 2
            )
            lock_path = next(fake.state_root.glob(".refresh-daemon-*.lock"))
            self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8")), {})

    def test_timed_out_has_session_query_keeps_supervisor_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = FakeTmuxEnvironment(root)
            fake.env.update(
                {
                    "CODEX_TMUX_BADGE_DAEMON_INTERVAL": "0.01",
                    "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "1",
                    "FAKE_TMUX_HAS_SESSION_BLOCK_ONCE_FILE": str(
                        root / "has-session-started"
                    ),
                    "FAKE_TMUX_HAS_SESSION_BLOCK_SECONDS": "2",
                }
            )

            fake.run("--daemon")

            self.assertGreaterEqual(
                sum(call == "has-session" for call in fake.calls()), 2
            )
            self.assertEqual(
                sum(call.startswith("capture-pane ") for call in fake.calls()), 1
            )

    def test_different_servers_do_not_share_the_singleton_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_fake = FakeTmuxEnvironment(root, "/tmp/fake-tmux-a.sock")
            first_fake.env["CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS"] = "1"
            second_env = first_fake.env.copy()
            second_env.update(
                {
                    "FAKE_TMUX_SOCKET": "/tmp/fake-tmux-b.sock",
                    "TMUX": "/tmp/fake-tmux-b.sock,5678,0",
                }
            )

            processes = [
                subprocess.Popen(
                    ["bash", str(SCRIPT), "--daemon"],
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for environment in (first_fake.env, second_env)
            ]
            for process in processes:
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)

            self.assertEqual(
                sum(call.startswith("list-windows ") for call in first_fake.calls()), 2
            )
            self.assertEqual(
                len(list(first_fake.state_root.glob(".refresh-daemon-*.lock"))), 2
            )

    def test_locked_file_owned_by_unrelated_process_is_not_killed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeTmuxEnvironment(Path(temporary))
            fake.state_root.mkdir(parents=True)
            key = hashlib.sha256(
                os.path.realpath(fake.env["FAKE_TMUX_SOCKET"]).encode("utf-8")
            ).hexdigest()[:16]
            lock_path = fake.state_root / f".refresh-daemon-{key}.lock"
            holder_code = r"""
import fcntl, json, os, subprocess, sys
lock_path, script, server = sys.argv[1:]
fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
start = subprocess.check_output(
    ["ps", "-p", str(os.getpid()), "-o", "lstart="], text=True
).strip()
metadata = {
    "pid": os.getpid(), "owner_token": "not-a-daemon", "process_start": start,
    "script": script, "digest": "different", "server_id": server,
}
os.write(fd, (json.dumps(metadata) + "\n").encode())
print("ready", flush=True)
sys.stdin.read(1)
"""
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    holder_code,
                    str(lock_path),
                    str(SCRIPT.resolve()),
                    fake.env["FAKE_TMUX_SOCKET"],
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "ready")
                fake.run("--daemon")
                self.assertIsNone(holder.poll())
                self.assertEqual(fake.calls(), ["display-message -p #{socket_path}"])
            finally:
                if holder.poll() is None:
                    holder.stdin.write("x")
                    holder.stdin.flush()
                holder.communicate(timeout=5)

    def test_changed_script_safely_takes_over_existing_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = FakeTmuxEnvironment(root)
            script_copy = root / SCRIPT.name
            shutil.copy2(SCRIPT, script_copy)
            shutil.copy2(STATE_HELPER, root / STATE_HELPER.name)
            first_env = fake.env.copy()
            first_env.update(
                {
                    "CODEX_TMUX_BADGE_DAEMON_INTERVAL": "0.05",
                    "CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS": "100",
                }
            )
            first = subprocess.Popen(
                ["bash", str(script_copy), "--daemon"],
                env=first_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.wait_for_calls(fake, "list-windows ", 1)
                with script_copy.open("a", encoding="utf-8") as handle:
                    handle.write("\n# daemon takeover test revision\n")
                second_env = fake.env.copy()
                second_env["CODEX_TMUX_BADGE_DAEMON_MAX_LOOPS"] = "1"
                subprocess.run(
                    ["bash", str(script_copy), "--daemon"],
                    env=second_env,
                    text=True,
                    capture_output=True,
                    timeout=8,
                    check=True,
                )
                first.communicate(timeout=8)
                self.assertEqual(first.returncode, 0)
            finally:
                if first.poll() is None:
                    first.terminate()
                    first.communicate(timeout=5)

            lock_path = next(fake.state_root.glob(".refresh-daemon-*.lock"))
            self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8")), {})


if __name__ == "__main__":
    unittest.main()
