#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import fcntl
import json
import math
import os
import shlex
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from assistant_completion_state import server_state_dir
from assistant_restore_args import (
    build_resume_words,
    is_uuid,
    resolve_executable,
    resume_id_from_words,
)


STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "codex-tmux-status"
CLAUDE_STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "claude-tmux-status"
SPINNER_PREFIXES = tuple(f"{char} " for char in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠐")
PROCESS_START_GRANULARITY_SECONDS = 1.0


def tmux(args: list[str], check: bool = True) -> str:
    result = subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.rstrip("\n")


def command_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_name(command: str) -> str:
    words = command_words(command)
    return Path(words[0]).name if words else ""


def command_tool(command: str) -> str:
    name = command_name(command)
    if name == "codex" or name.startswith("codex-"):
        return "codex"
    if name == "claude" or name.startswith("claude-"):
        return "claude"
    return ""


def is_version_name(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def load_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def state_root(tool: str) -> Path:
    return CLAUDE_STATE_DIR if tool == "claude" else STATE_DIR


def state_path(pane_id: str, tool: str, tmux_socket: str = "") -> Path:
    root = CLAUDE_STATE_DIR if tool == "claude" else STATE_DIR
    namespaced = server_state_dir(root, tmux_socket)
    if namespaced is not None:
        root = namespaced
    return root / f"pane-{pane_id.lstrip('%')}.json"


def state_path_candidates(pane: dict[str, str], tool: str) -> list[tuple[Path, bool]]:
    roots = [state_root(tool)]
    if tool == "claude":
        roots.append(STATE_DIR)
    namespaced = [
        (state_path(pane["pane_id"], "claude" if root == CLAUDE_STATE_DIR else "codex", pane["socket_path"]), False)
        for root in roots
    ]
    if any(path.exists() for path, _legacy in namespaced):
        return namespaced
    return namespaced + [
        (root / f"pane-{pane['pane_id'].lstrip('%')}.json", True) for root in roots
    ]


def ambiguous_legacy_state(
    root: Path, state: dict[str, object], tmux_socket: str
) -> bool:
    del root, tmux_socket
    # A socketless legacy file cannot prove which tmux server owned a reused
    # pane/window ID. Only legacy state carrying an explicit socket may migrate.
    return not str(state.get("tmux_socket", state.get("socket_path", ""))).strip()


def realpath(value: str) -> str:
    return os.path.realpath(os.path.expanduser(value)) if value else ""


def usable_dir(value: str) -> str:
    path = Path(value).expanduser() if value else Path.home()
    return str(path) if path.is_dir() else str(Path.home())


def parse_epoch(value: object) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        epoch = float(value)
        if not math.isfinite(epoch):
            return None
        for _ in range(3):
            if epoch <= 10_000_000_000:
                break
            epoch /= 1000.0
        return epoch if 0 < epoch <= 10_000_000_000 else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return parse_epoch(float(text))
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def state_completed_epoch(state: dict[str, object]) -> Optional[float]:
    for key in ("completed_at_ns", "completed_at"):
        epoch = parse_epoch(state.get(key))
        if epoch is not None:
            return epoch
    return None


def state_matches_pane(
    state: dict[str, object],
    pane: dict[str, str],
    tool: str,
    process_start_epoch: Optional[float],
) -> bool:
    if process_start_epoch is None:
        return False
    if str(state.get("pane_id", "")).strip() != pane["pane_id"]:
        return False
    if str(state.get("window_id", "")).strip() != pane["window_id"]:
        return False

    state_cwd = str(state.get("cwd", "")).strip()
    if not state_cwd or not pane["path"] or realpath(state_cwd) != realpath(pane["path"]):
        return False

    state_tool = str(state.get("tool", "")).strip()
    state_source = str(state.get("source", "")).strip()
    if state_tool and state_tool != tool:
        return False
    if state_source and state_source not in {tool, "codex" if tool == "codex" else "claude"}:
        return False

    state_socket = str(state.get("tmux_socket", state.get("socket_path", ""))).strip()
    pane_socket = pane.get("socket_path", "").strip()
    if not pane_socket:
        return False
    if state_socket and realpath(state_socket) != realpath(pane_socket):
        return False

    completed_epoch = state_completed_epoch(state)
    return (
        completed_epoch is not None
        and completed_epoch >= process_start_epoch + PROCESS_START_GRANULARITY_SECONDS
    )


def state_resume_id(state: dict[str, object], tool: str) -> str:
    key = "session_id" if tool == "claude" else "thread_id"
    value = state.get(key)
    return str(value).strip() if is_uuid(value) else ""


def saved_session_id(
    tool: str,
    pane: dict[str, str],
    process_start_epoch: Optional[float],
) -> tuple[str, str]:
    for path, legacy in state_path_candidates(pane, tool):
        state = load_json(path)
        if legacy and ambiguous_legacy_state(path.parent, state, pane["socket_path"]):
            continue
        if not state_matches_pane(state, pane, tool, process_start_epoch):
            continue
        if tool == "claude" and (
            path.parent == STATE_DIR or path.parent.parent.parent == STATE_DIR
        ):
            if str(state.get("source", "")).strip() != "claude":
                continue
        session_id = state_resume_id(state, tool)
        if session_id:
            return session_id, str(state.get("cwd", "")).strip()
    return "", ""


def process_rows() -> list[tuple[str, str, str]]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []

    rows: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def children_by_parent(rows: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str]]]:
    children: dict[str, list[tuple[str, str]]] = {}
    for pid, ppid, command in rows:
        children.setdefault(ppid, []).append((pid, command))
    return children


def pane_tool(pane: dict[str, str]) -> str:
    tool = command_tool(pane["command"])
    if tool:
        return tool
    if is_version_name(pane["command"]) and (
        pane["title"].startswith("✳ ") or pane["title"].startswith(SPINNER_PREFIXES)
    ):
        return "claude"
    return ""


def _darwin_process_argv(pid: int) -> list[str]:
    library_name = ctypes.util.find_library("c")
    if not library_name:
        return []
    try:
        libc = ctypes.CDLL(library_name, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, pid
        size = ctypes.c_size_t(0)
        if (
            libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0
            or size.value <= 4
        ):
            return []
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            return []
    except (AttributeError, OSError, ValueError):
        return []

    raw = buffer.raw[: size.value]
    argc = int.from_bytes(raw[:4], byteorder=sys.byteorder, signed=True)
    if argc <= 0 or argc > 100_000:
        return []
    offset = raw.find(b"\0", 4)
    if offset < 0:
        return []
    offset += 1
    while offset < len(raw) and raw[offset] == 0:
        offset += 1

    argv: list[str] = []
    for _ in range(argc):
        end = raw.find(b"\0", offset)
        if end < 0:
            return []
        argv.append(raw[offset:end].decode("utf-8", errors="surrogateescape"))
        offset = end + 1
    return argv


def process_argv(pid: str) -> list[str]:
    if not pid.isdigit():
        return []
    proc_path = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_path.is_file():
            raw = proc_path.read_bytes()
            return [
                part.decode("utf-8", errors="surrogateescape")
                for part in raw.split(b"\0")
                if part
            ]
    except OSError:
        return []
    if sys.platform == "darwin":
        return _darwin_process_argv(int(pid))
    return []


def words_tool(words: list[str]) -> str:
    if not words:
        return ""
    name = Path(words[0]).name
    if name == "codex" or name.startswith("codex-"):
        return "codex"
    if name == "claude" or name.startswith("claude-"):
        return "claude"
    if is_version_name(name) and "/claude/versions/" in words[0]:
        return "claude"
    return ""


def current_assistant_process(
    pane: dict[str, str],
    tool: str,
    children: dict[str, list[tuple[str, str]]],
) -> tuple[str, list[str]]:
    candidates: list[tuple[str, list[str]]] = []

    root_words = process_argv(pane["pane_pid"])
    if words_tool(root_words) == tool:
        candidates.append((pane["pane_pid"], root_words))

    for pid, lossy_command in children.get(pane["pane_pid"], []):
        exact_words = process_argv(pid)
        if words_tool(exact_words) == tool:
            candidates.append((pid, exact_words))
            continue
        lossy_name = command_name(lossy_command)
        if not exact_words and (
            command_tool(lossy_command) == tool
            or (tool == "claude" and is_version_name(lossy_name))
        ):
            return "", []

    # More than one direct assistant child means at least one can be a
    # background/stopped job. tmux's pane command cannot identify which child
    # owns the foreground terminal reliably enough for destructive respawn.
    return candidates[0] if len(candidates) == 1 else ("", [])


def process_lstart_epoch(pid: str) -> Optional[float]:
    if not pid.isdigit():
        return None
    try:
        text = subprocess.check_output(
            ["ps", "-p", pid, "-o", "lstart="],
            text=True,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        return datetime.strptime(text, "%a %b %d %H:%M:%S %Y").timestamp()
    except ValueError:
        return None


def source_words(tool: str, exact_words: list[str]) -> list[str]:
    if exact_words and words_tool(exact_words) == tool:
        return exact_words
    return [tool]


def shell_command(words: list[str], cwd: str, tool: str) -> str:
    runner = next(
        (
            path
            for path in ("/bin/zsh", "/bin/bash", "/bin/sh")
            if Path(path).is_file()
        ),
        "",
    )
    if not runner:
        raise RuntimeError("no POSIX-compatible shell found")

    login_shell = os.environ.get("SHELL", "")
    if not login_shell or not Path(login_shell).is_file():
        login_shell = runner

    command = " ".join(shlex.quote(word) for word in words)
    workdir = usable_dir(cwd)
    inner = (
        f"cd {shlex.quote(workdir)} 2>/dev/null || cd; "
        f"{command}; "
        "status=$?; "
        f"printf '\\n[{tool} exited with status %s; shell refreshed]\\n' \"$status\"; "
        f"exec {shlex.quote(login_shell)} -l"
    )
    return f"exec {shlex.quote(runner)} -lic {shlex.quote(inner)}"


def list_panes() -> list[dict[str, str]]:
    fmt = "\t".join(
        [
            "#{pane_id}",
            "#{pane_pid}",
            "#{pane_current_command}",
            "#{pane_title}",
            "#{pane_current_path}",
            "#{session_name}",
            "#{window_id}",
            "#{window_index}",
            "#{pane_index}",
            "#{window_name}",
            "#{socket_path}",
            "#{pid}",
        ]
    )
    output = tmux(["list-panes", "-a", "-F", fmt], check=True)
    panes: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        parts = line.split("\t", 11)
        if len(parts) != 12:
            continue
        (
            pane_id,
            pane_pid,
            command,
            title,
            path,
            session_name,
            window_id,
            window_index,
            pane_index,
            window_name,
            socket_path,
            server_pid,
        ) = parts
        if not pane_id or pane_id in seen or not server_pid.isdigit():
            continue
        seen.add(pane_id)
        panes.append(
            {
                "pane_id": pane_id,
                "pane_pid": pane_pid,
                "command": command,
                "title": title,
                "path": path,
                "session_name": session_name,
                "window_id": window_id,
                "window_index": window_index,
                "pane_index": pane_index,
                "window_name": window_name,
                "socket_path": socket_path,
                "server_pid": server_pid,
            }
        )
    return panes


def live_pane_identity(pane_id: str) -> dict[str, str]:
    fmt = "\t".join(
        (
            "#{pane_id}",
            "#{pane_pid}",
            "#{pane_current_command}",
            "#{pane_title}",
            "#{socket_path}",
            "#{pid}",
        )
    )
    try:
        fields = tmux(["display-message", "-p", "-t", pane_id, fmt], check=True).split(
            "\t", 5
        )
    except (OSError, subprocess.CalledProcessError):
        return {}
    if (
        len(fields) != 6
        or fields[0] != pane_id
        or not fields[1].isdigit()
        or not fields[5].isdigit()
    ):
        return {}
    return dict(
        zip(
            (
                "pane_id",
                "pane_pid",
                "command",
                "title",
                "socket_path",
                "server_pid",
            ),
            fields,
        )
    )


def process_identity_matches(
    pane: dict[str, str],
    tool: str,
    process_pid: str,
    process_start_epoch: float,
    exact_words: list[str],
) -> bool:
    live_pane = live_pane_identity(pane["pane_id"])
    if not live_pane or live_pane["pane_pid"] != pane["pane_pid"]:
        return False
    if live_pane["server_pid"] != pane.get("server_pid", ""):
        return False
    if pane_tool(live_pane) != tool:
        return False
    if realpath(live_pane.get("socket_path", "")) != realpath(
        pane.get("socket_path", "")
    ):
        return False

    live_children = children_by_parent(process_rows())
    live_pid, live_words = current_assistant_process(live_pane, tool, live_children)
    if (
        live_pid != process_pid
        or live_words != exact_words
        or words_tool(live_words) != tool
    ):
        return False
    return process_lstart_epoch(live_pid) == process_start_epoch


def respawn_lock_path(pane: dict[str, str]) -> Path:
    server_pid = pane.get("server_pid", "")
    pane_number = pane.get("pane_id", "").lstrip("%")
    namespaced = server_state_dir(STATE_DIR, pane.get("socket_path", ""))
    if namespaced is None or not server_pid.isdigit() or not pane_number.isdigit():
        raise OSError("invalid tmux respawn lock identity")
    return (
        namespaced
        / "restart-locks"
        / f"server-{server_pid}"
        / f"pane-{pane_number}.lock"
    )


@contextmanager
def pane_respawn_lock(pane: dict[str, str]) -> Iterator[None]:
    lock_path = respawn_lock_path(pane)
    for directory in (
        STATE_DIR,
        STATE_DIR / "servers",
        lock_path.parent.parent.parent,
        lock_path.parent.parent,
        lock_path.parent,
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)

    descriptor = os.open(
        str(lock_path),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def verified_respawn(
    pane: dict[str, str],
    tool: str,
    process_pid: str,
    process_start_epoch: float,
    exact_words: list[str],
    cwd: str,
    command: str,
) -> str:
    try:
        with pane_respawn_lock(pane):
            if not process_identity_matches(
                pane, tool, process_pid, process_start_epoch, exact_words
            ):
                return "process changed before respawn"
            try:
                tmux(
                    [
                        "respawn-pane",
                        "-k",
                        "-t",
                        pane["pane_id"],
                        "-c",
                        usable_dir(cwd),
                        command,
                    ]
                )
            except subprocess.CalledProcessError:
                return "respawn failed"
    except OSError:
        return "respawn lock unavailable"
    return ""


def restart_panes(dry_run: bool, tool_filter: str) -> tuple[int, int, int]:
    children = children_by_parent(process_rows())
    restarted = 0
    skipped = 0
    found = 0

    for pane in list_panes():
        tool = pane_tool(pane)
        if not tool or (tool_filter != "all" and tool != tool_filter):
            continue

        found += 1
        process_pid, exact_words = current_assistant_process(pane, tool, children)
        if not process_pid:
            skipped += 1
            print(
                f"skip {pane['pane_id']} {tool}: foreground assistant process is ambiguous",
                file=sys.stderr,
            )
            continue

        process_start = process_lstart_epoch(process_pid)
        if not exact_words or process_start is None:
            skipped += 1
            print(
                f"skip {pane['pane_id']} {tool}: exact process identity is unavailable",
                file=sys.stderr,
            )
            continue

        session_id = resume_id_from_words(exact_words, tool) if exact_words else ""
        cwd = pane["path"]
        source = "current command"
        if not session_id:
            session_id, state_cwd = saved_session_id(tool, pane, process_start)
            cwd = state_cwd or cwd
            source = "fresh pane state"

        if not session_id:
            skipped += 1
            print(
                f"skip {pane['pane_id']} {tool}: no verified current/fresh resume id",
                file=sys.stderr,
            )
            continue

        words = build_resume_words(source_words(tool, exact_words), tool, session_id)
        resolved_executable = resolve_executable(
            words[0] if words else "", cwd, tool
        )
        if not words or not resolved_executable:
            skipped += 1
            print(f"skip {pane['pane_id']} {tool}: executable is unavailable", file=sys.stderr)
            continue
        words[0] = resolved_executable
        try:
            command = shell_command(words, cwd, tool)
        except RuntimeError as error:
            skipped += 1
            print(f"skip {pane['pane_id']} {tool}: {error}", file=sys.stderr)
            continue

        label = f"{pane['session_name']}:{pane['window_index']}.{pane['pane_index']} {tool}"
        if dry_run:
            print(f"would restart {label} ({pane['pane_id']}; id from {source})")
            continue

        respawn_error = verified_respawn(
            pane,
            tool,
            process_pid,
            process_start,
            exact_words,
            cwd,
            command,
        )
        if respawn_error:
            skipped += 1
            print(
                f"skip {pane['pane_id']} {tool}: {respawn_error}",
                file=sys.stderr,
            )
            continue

        restarted += 1
        print(f"restarted {label} ({pane['pane_id']}; id from {source})")

    if not dry_run:
        refresh = Path.home() / "GitHubRepos/dotfiles/tmux/codex-window-badges-refresh.sh"
        if refresh.exists():
            subprocess.run(
                [str(refresh), "--force"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    return found, restarted, skipped


def display_message(message: str) -> None:
    subprocess.run(
        ["tmux", "display-message", message],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart Codex and Claude Code panes with verified resume IDs.")
    parser.add_argument("--dry-run", action="store_true", help="show what would restart without touching panes")
    parser.add_argument("--tool", choices=["all", "codex", "claude"], default="all")
    args = parser.parse_args()

    try:
        found, restarted, skipped = restart_panes(args.dry_run, args.tool)
    except subprocess.CalledProcessError:
        message = "No tmux server found"
        if not args.dry_run:
            display_message(message)
        print(message, file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"found={found} skipped={skipped}")
        return 0

    if found == 0:
        message = "No Codex/Claude panes found"
    elif restarted == 0:
        message = f"No Codex/Claude panes restarted ({skipped} skipped; no verified ID or respawn failed)"
    else:
        message = f"Restarted {restarted} Codex/Claude pane(s)"
        if skipped:
            message += f"; skipped {skipped}"

    display_message(message)
    print(message)
    return 0 if restarted or found == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
