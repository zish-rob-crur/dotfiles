#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Optional

from assistant_completion_state import server_state_dir
from codex_notify_common import codex_state_databases, resolve_codex_locations


SCRIPT_DIR = Path(__file__).resolve().parent
STATE_ROOT = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "codex-tmux-status"
)
DEFAULT_IDLE_SECONDS = 3 * 24 * 60 * 60
DEFAULT_INTERVAL_SECONDS = 60 * 60
MIN_INTERVAL_SECONDS = 60
SQLITE_RETRIES = 3
SQLITE_RETRY_SECONDS = 0.03


def load_restart_module() -> ModuleType:
    path = SCRIPT_DIR / "restart-assistant-panes.py"
    spec = importlib.util.spec_from_file_location("restart_assistant_panes_for_parker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RESTART = load_restart_module()


@dataclass(frozen=True)
class Candidate:
    pane: dict[str, str]
    process_pid: str
    process_start: float
    exact_words: list[str]
    session_id: str
    cwd: str
    source: str
    last_activity: float


@dataclass(frozen=True)
class ScanResult:
    found: int
    eligible: int
    parked: int
    skipped: int


def parse_database_epoch(value: object) -> Optional[float]:
    return RESTART.parse_epoch(value)


def thread_activity_epoch(session_id: str) -> Optional[float]:
    """Return the newest persisted update time for one exact Codex thread."""

    _codex_home, sqlite_home = resolve_codex_locations()
    newest: Optional[float] = None
    for database in codex_state_databases(sqlite_home):
        uri = database.resolve().as_uri() + "?mode=ro"
        for attempt in range(SQLITE_RETRIES):
            try:
                with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                    connection.execute("PRAGMA query_only = ON")
                    columns = {
                        str(row[1])
                        for row in connection.execute("PRAGMA table_info(threads)")
                    }
                    if "id" not in columns:
                        break
                    selected = [
                        column
                        for column in ("updated_at_ms", "updated_at")
                        if column in columns
                    ]
                    if not selected:
                        break
                    names = ", ".join(f'"{column}"' for column in selected)
                    row = connection.execute(
                        f"SELECT {names} FROM threads WHERE id = ? LIMIT 1",
                        (session_id,),
                    ).fetchone()
                    if row is not None:
                        for value in row:
                            epoch = parse_database_epoch(value)
                            if epoch is not None and (newest is None or epoch > newest):
                                newest = epoch
                    break
            except (OSError, sqlite3.Error):
                if attempt + 1 < SQLITE_RETRIES:
                    time.sleep(SQLITE_RETRY_SECONDS)
                    continue
                break
    return newest


def focused_panes() -> Optional[set[str]]:
    try:
        output = RESTART.tmux(
            ["list-clients", "-F", "#{pane_id}\t#{client_flags}"], check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    focused: set[str] = set()
    for line in output.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2:
            continue
        pane_id, flags = fields
        if "focused" in {flag.strip() for flag in flags.split(",")}:
            focused.add(pane_id)
    return focused


def idle_composer_visible(content: str) -> bool:
    """Accept only an empty Codex composer with no active control beneath it."""

    lines = content.splitlines()[-16:]
    for line in lines:
        if "You've hit your usage limit" in line:
            return False
        if re.search(r"^\s*■.*error", line, re.IGNORECASE):
            return False
        if re.search(r"^\s*•.*esc\s+to\s+interrupt", line):
            return False
        if re.search(r"^\s*›\s+[0-9]+\.", line):
            return False
        if re.search(r"Press\s+enter\s+to\s+(?:confirm|continue)", line, re.IGNORECASE):
            return False
        if re.search(r"enter\s+to\s+submit\s+answer", line, re.IGNORECASE):
            return False
        if re.search(r"[1-9][0-9]*\s+background\s+terminals?", line, re.IGNORECASE):
            return False

    # Requiring the bare prompt preserves any abandoned draft in the composer.
    return any(re.fullmatch(r"\s*›\s*", line) for line in lines[-8:])


def pane_is_idle(pane_id: str) -> bool:
    try:
        content = RESTART.tmux(
            ["capture-pane", "-p", "-J", "-t", pane_id, "-S", "-40"],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return idle_composer_visible(content)


def resolve_candidate(
    pane: dict[str, str],
    children: dict[str, list[tuple[str, str]]],
    now: float,
    idle_seconds: int,
) -> Optional[Candidate]:
    if RESTART.pane_tool(pane) != "codex":
        return None

    process_pid, exact_words = RESTART.current_assistant_process(
        pane, "codex", children
    )
    if not process_pid or not exact_words:
        return None
    process_start = RESTART.process_lstart_epoch(process_pid)
    if process_start is None:
        return None

    session_id = RESTART.resume_id_from_words(exact_words, "codex")
    cwd = pane["path"]
    source = "current command"
    if not session_id:
        session_id, state_cwd = RESTART.saved_session_id(
            "codex", pane, process_start
        )
        cwd = state_cwd or cwd
        source = "fresh pane state"
    if not session_id:
        return None

    persisted_activity = thread_activity_epoch(session_id)
    if persisted_activity is None or persisted_activity > now + 60:
        return None
    last_activity = max(process_start, persisted_activity)
    if now - last_activity < idle_seconds:
        return None

    return Candidate(
        pane=pane,
        process_pid=process_pid,
        process_start=process_start,
        exact_words=exact_words,
        session_id=session_id,
        cwd=cwd,
        source=source,
        last_activity=last_activity,
    )


def duration_label(seconds: float) -> str:
    days = max(1, int(seconds // (24 * 60 * 60)))
    return f"{days}d"


def parked_shell_command(candidate: Candidate, now: float) -> str:
    runner = next(
        (path for path in ("/bin/zsh", "/bin/bash", "/bin/sh") if Path(path).is_file()),
        "",
    )
    if not runner:
        raise RuntimeError("no POSIX-compatible shell found")
    login_shell = os.environ.get("SHELL", "")
    if not login_shell or not Path(login_shell).is_file():
        login_shell = runner

    lines = [
        "",
        f"[Codex auto-parked after {duration_label(now - candidate.last_activity)} idle]",
        f"cwd: {RESTART.usable_dir(candidate.cwd)}",
        f"session: {candidate.session_id}",
        f"resume: codex resume {candidate.session_id}",
        f"shortcut: cr {candidate.session_id}",
        "",
    ]
    print_command = "printf '%s\\n' " + " ".join(shlex.quote(line) for line in lines)
    inner = f"{print_command}; exec {shlex.quote(login_shell)} -l"
    return f"exec {shlex.quote(runner)} -lic {shlex.quote(inner)}"


def candidate_still_safe(candidate: Candidate, now: float, idle_seconds: int) -> str:
    focused = focused_panes()
    if focused is None:
        return "client focus is unavailable"
    if candidate.pane["pane_id"] in focused:
        return "pane became focused"
    if not pane_is_idle(candidate.pane["pane_id"]):
        return "Codex is no longer at an empty composer"

    activity = thread_activity_epoch(candidate.session_id)
    if activity is None or activity > now + 60:
        return "thread activity is unavailable"
    if now - max(candidate.process_start, activity) < idle_seconds:
        return "thread became active"
    if not RESTART.process_identity_matches(
        candidate.pane,
        "codex",
        candidate.process_pid,
        candidate.process_start,
        candidate.exact_words,
    ):
        return "process changed before parking"
    return ""


def park_candidate(candidate: Candidate, now: float, idle_seconds: int) -> str:
    try:
        command = parked_shell_command(candidate, now)
    except RuntimeError as error:
        return str(error)

    try:
        with RESTART.pane_respawn_lock(candidate.pane):
            reason = candidate_still_safe(candidate, now, idle_seconds)
            if reason:
                return reason
            try:
                RESTART.tmux(
                    [
                        "respawn-pane",
                        "-k",
                        "-t",
                        candidate.pane["pane_id"],
                        "-c",
                        RESTART.usable_dir(candidate.cwd),
                        command,
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError:
                return "respawn failed"
    except OSError:
        return "respawn lock unavailable"
    return ""


def scan_once(idle_seconds: int, apply: bool, now: Optional[float] = None) -> ScanResult:
    current_time = time.time() if now is None else now
    focused = focused_panes()
    if focused is None:
        raise RuntimeError("tmux client focus is unavailable")

    children = RESTART.children_by_parent(RESTART.process_rows())
    found = eligible = parked = skipped = 0
    for pane in RESTART.list_panes():
        if RESTART.pane_tool(pane) != "codex":
            continue
        found += 1
        if pane["pane_id"] in focused or not pane_is_idle(pane["pane_id"]):
            skipped += 1
            continue

        candidate = resolve_candidate(pane, children, current_time, idle_seconds)
        if candidate is None:
            skipped += 1
            continue
        eligible += 1

        label = (
            f"{pane['session_name']}:{pane['window_index']}.{pane['pane_index']} "
            f"({pane['pane_id']}; idle "
            f"{duration_label(current_time - candidate.last_activity)})"
        )
        if not apply:
            print(f"would park {label}")
            continue

        reason = park_candidate(candidate, current_time, idle_seconds)
        if reason:
            skipped += 1
            print(f"skip {pane['pane_id']}: {reason}", file=sys.stderr)
            continue
        parked += 1
        print(f"parked {label}")

    if parked:
        refresh = SCRIPT_DIR / "codex-window-badges-refresh.sh"
        if refresh.exists():
            subprocess.run(
                [str(refresh), "--force"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    return ScanResult(found=found, eligible=eligible, parked=parked, skipped=skipped)


def tmux_integer_option(name: str, default: int, minimum: int) -> int:
    try:
        raw = RESTART.tmux(["show-options", "-gqv", name], check=True).strip()
        value = int(raw) if raw else default
    except (OSError, ValueError, subprocess.CalledProcessError):
        return default
    return value if value >= minimum else default


def server_identity() -> Optional[tuple[str, str, str]]:
    try:
        fields = RESTART.tmux(
            ["display-message", "-p", "#{pid}\t#{start_time}\t#{socket_path}"],
            check=True,
        ).split("\t", 2)
    except (OSError, subprocess.CalledProcessError):
        return None
    if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit() or not fields[2]:
        return None
    return fields[0], fields[1], fields[2]


def daemon_lock(identity: tuple[str, str, str]) -> Optional[tuple[int, Path]]:
    server_pid, server_start, socket_path = identity
    namespace = server_state_dir(STATE_ROOT, socket_path)
    if namespace is None:
        return None
    lock_dir = namespace / "idle-parker"
    for directory in (STATE_ROOT, STATE_ROOT / "servers", namespace, lock_dir):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    lock_path = lock_dir / f"server-{server_pid}-{server_start}.lock"
    descriptor = os.open(
        str(lock_path),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor, lock_path


def lock_still_current(descriptor: int, lock_path: Path) -> bool:
    """False once the lock file was unlinked or replaced; the flock then guards nothing."""
    try:
        return os.fstat(descriptor).st_ino == lock_path.stat().st_ino
    except OSError:
        return False


def run_daemon() -> int:
    identity = server_identity()
    if identity is None:
        return 1
    lock = daemon_lock(identity)
    if lock is None:
        return 0
    descriptor, lock_path = lock

    stopped = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    max_loops = int(os.environ.get("CODEX_TMUX_IDLE_PARK_MAX_LOOPS", "0"))
    loops = 0
    try:
        while not stopped.is_set() and server_identity() == identity and lock_still_current(descriptor, lock_path):
            idle_seconds = tmux_integer_option(
                "@codex-idle-park-seconds", DEFAULT_IDLE_SECONDS, 60
            )
            try:
                scan_once(idle_seconds, apply=True)
            except (RuntimeError, subprocess.CalledProcessError):
                pass
            loops += 1
            if max_loops > 0 and loops >= max_loops:
                break
            interval = tmux_integer_option(
                "@codex-idle-park-interval", DEFAULT_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS
            )
            stopped.wait(interval)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Park verified, idle Codex tmux panes and leave a resume command."
    )
    parser.add_argument("--daemon", action="store_true", help="scan periodically until the tmux server exits")
    parser.add_argument("--apply", action="store_true", help="park eligible panes; default is a dry run")
    parser.add_argument("--idle-seconds", type=int, help="override the tmux idle threshold for this scan")
    args = parser.parse_args()

    if args.daemon:
        return run_daemon()

    idle_seconds = (
        args.idle_seconds
        if args.idle_seconds is not None and args.idle_seconds >= 1
        else tmux_integer_option("@codex-idle-park-seconds", DEFAULT_IDLE_SECONDS, 60)
    )
    try:
        result = scan_once(idle_seconds, apply=args.apply)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"codex-idle-parker: {error}", file=sys.stderr)
        return 1

    action = "parked" if args.apply else "eligible"
    count = result.parked if args.apply else result.eligible
    print(
        f"found={result.found} {action}={count} skipped={result.skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
