#!/usr/bin/env python3

from __future__ import annotations

import ctypes
import ctypes.util
import json
import math
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from assistant_completion_state import server_state_dir
from assistant_restore_args import (
    build_resume_words,
    is_uuid,
    resolve_executable,
    resume_id_from_words,
)


STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "codex-tmux-status"
CLAUDE_STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "claude-tmux-status"
PROCESS_START_GRANULARITY_SECONDS = 1.0
SPINNER_PREFIXES = tuple(f"{char} " for char in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠐")


def tmux(args: list[str]) -> str:
    return subprocess.check_output(["tmux", *args], text=True, stderr=subprocess.DEVNULL).rstrip("\n")


def shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def command_name(command: str) -> str:
    words = shell_words(command)
    return Path(words[0]).name if words else ""


def assistant_tool(command: str) -> str:
    name = command_name(command)
    if name == "codex" or name.startswith("codex-"):
        return "codex"
    if name == "claude" or name.startswith("claude-"):
        return "claude"
    if "/claude/versions/" in command and is_version_name(name):
        return "claude"
    return ""


def is_version_name(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def pane_tool(pane: dict[str, str]) -> str:
    tool = assistant_tool(pane.get("command", ""))
    if tool:
        return tool
    command = pane.get("command", "")
    title = pane.get("title", "")
    if is_version_name(command) and (
        title.startswith("✳ ") or title.startswith(SPINNER_PREFIXES)
    ):
        return "claude"
    return ""


def state_root(tool: str) -> Path:
    return CLAUDE_STATE_DIR if tool == "claude" else STATE_DIR


def state_path_for_pane(pane_id: str, tool: str, tmux_socket: str = "") -> Path:
    root = state_root(tool)
    namespaced = server_state_dir(root, tmux_socket)
    if namespaced is not None:
        root = namespaced
    return root / f"pane-{pane_id.lstrip('%')}.json"


def state_path_candidates(pane: dict[str, str], tool: str) -> list[tuple[Path, Path, bool]]:
    roots = [state_root(tool)]
    if tool == "claude":
        roots.append(STATE_DIR)
    namespaced = []
    for root in roots:
        state_tool = "claude" if root == CLAUDE_STATE_DIR else "codex"
        namespaced.append(
            (state_path_for_pane(pane["pane_id"], state_tool, pane["socket_path"]), root, False)
        )
    if any(path.exists() for path, _root, _legacy in namespaced):
        return namespaced
    return namespaced + [
        (root / f"pane-{pane['pane_id'].lstrip('%')}.json", root, True)
        for root in roots
    ]


def ambiguous_legacy_state(
    root: Path, state: dict[str, object], tmux_socket: str
) -> bool:
    del root, tmux_socket
    # Socketless legacy state is ambiguous across tmux servers even when a
    # custom socket happens to be named "default".
    return not str(state.get("tmux_socket", state.get("socket_path", ""))).strip()


def load_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


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


def children_by_parent(
    rows: list[tuple[str, str, str]],
) -> dict[str, list[tuple[str, str]]]:
    children: dict[str, list[tuple[str, str]]] = {}
    for pid, ppid, command in rows:
        children.setdefault(ppid, []).append((pid, command))
    return children


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
    if pane_tool(pane) != tool:
        return "", []

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
            assistant_tool(lossy_command) == tool
            or (tool == "claude" and is_version_name(lossy_name))
        ):
            # An apparent assistant whose argv cannot be read prevents proving
            # that the remaining exact candidate is unique.
            return "", []

    return candidates[0] if len(candidates) == 1 else ("", [])


def direct_assistant_child_start_epoch(pane_pid: str, tool: str) -> Optional[float]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    matching_pids: list[str] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, ppid, command = parts
        name = command_name(command)
        matches = assistant_tool(command) == tool or (
            tool == "claude" and is_version_name(name)
        )
        if ppid == pane_pid and matches:
            matching_pids.append(pid)
    if len(matching_pids) != 1:
        return None
    return process_lstart_epoch(matching_pids[0])


def unescape_saved_dir(value: str) -> str:
    return value[1:].replace("\\ ", " ") if value.startswith(":") else ""


def valid_pane_parts(parts: list[str]) -> bool:
    return (
        len(parts) >= 11
        and parts[0] == "pane"
        and bool(parts[1])
        and parts[2].isdigit()
        and parts[5].isdigit()
        and parts[7].startswith(":")
        and parts[8] in {"0", "1"}
        and parts[10].startswith(":")
    )


def tmux_target(session_name: str, window_number: str, pane_index: str) -> str:
    return f"{session_name}:{window_number}.{pane_index}"


def pane_info_for_target(target: str) -> dict[str, str]:
    fields = tmux(
        [
            "display-message",
            "-p",
            "-t",
            target,
            (
                "#{pane_id}\t#{pane_pid}\t#{window_id}\t#{pane_current_path}"
                "\t#{socket_path}\t#{pane_current_command}\t#{pane_title}"
            ),
        ]
    ).split("\t", 6)
    if len(fields) != 7 or not fields[0] or not fields[1].isdigit() or not fields[2]:
        return {}
    return dict(
        zip(
            (
                "pane_id",
                "pane_pid",
                "window_id",
                "path",
                "socket_path",
                "command",
                "title",
            ),
            fields,
        )
    )


def state_matches_pane(
    state: dict[str, object],
    tool: str,
    pane: dict[str, str],
    saved_dir: str,
    process_start_epoch: Optional[float],
) -> bool:
    if process_start_epoch is None:
        return False
    if str(state.get("pane_id", "")).strip() != pane["pane_id"]:
        return False
    if str(state.get("window_id", "")).strip() != pane["window_id"]:
        return False

    state_cwd = str(state.get("cwd", "")).strip()
    if not state_cwd or not saved_dir:
        return False
    if os.path.realpath(state_cwd) != os.path.realpath(saved_dir):
        return False
    if pane.get("path") and os.path.realpath(state_cwd) != os.path.realpath(pane["path"]):
        return False

    state_tool = str(state.get("tool", "")).strip()
    state_source = str(state.get("source", "")).strip()
    if state_tool and state_tool != tool:
        return False
    if state_source and state_source != tool:
        return False

    state_socket = str(state.get("tmux_socket", state.get("socket_path", ""))).strip()
    pane_socket = pane.get("socket_path", "").strip()
    if not pane_socket:
        return False
    if state_socket and os.path.realpath(state_socket) != os.path.realpath(pane_socket):
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


def matching_state(
    pane: dict[str, str], tool: str, saved_dir: str, process_start_epoch: Optional[float]
) -> tuple[dict[str, object], str]:
    for path, root, legacy in state_path_candidates(pane, tool):
        state = load_json(path)
        if legacy and ambiguous_legacy_state(root, state, pane["socket_path"]):
            continue
        if root == STATE_DIR and tool == "claude":
            if str(state.get("source", "")).strip() != "claude":
                continue
        if not state_matches_pane(state, tool, pane, saved_dir, process_start_epoch):
            continue
        session_id = state_resume_id(state, tool)
        if session_id:
            return state, session_id
    return {}, ""


def fallback_shell_command() -> str:
    shell = os.environ.get("SHELL", "")
    if not shell or not Path(shell).is_file():
        shell = next(
            (
                path
                for path in ("/bin/zsh", "/bin/bash", "/bin/sh")
                if Path(path).is_file()
            ),
            "/bin/sh",
        )
    return f"exec {shlex.quote(shell)} -l"


def rewrite_pane_line(parts: list[str]) -> Optional[str]:
    if not valid_pane_parts(parts):
        return None

    updated = parts.copy()
    session_name = parts[1]
    window_number = parts[2]
    pane_index = parts[5]
    pane_title = parts[6]
    saved_dir = unescape_saved_dir(parts[7])
    pane_current_command = parts[9]
    saved_command = parts[10][1:]
    tool = pane_tool(
        {"command": pane_current_command, "title": pane_title}
    ) or assistant_tool(saved_command)
    if not tool:
        return None

    target = tmux_target(session_name, window_number, pane_index)
    try:
        pane = pane_info_for_target(target)
    except (OSError, subprocess.CalledProcessError):
        pane = {}

    session_id = ""
    verified_words = [tool]
    if pane and pane_tool(pane) == tool:
        children = children_by_parent(process_rows())
        process_pid, exact_words = current_assistant_process(pane, tool, children)
        process_start: Optional[float] = None
        if process_pid:
            process_start = process_lstart_epoch(process_pid)
            current_id = resume_id_from_words(exact_words, tool)
            if current_id:
                session_id = current_id
                verified_words = exact_words
        elif words_tool(process_argv(pane["pane_pid"])) != tool:
            # Exact argv may be unavailable on a supported platform. A fresh
            # state file may still verify one unique direct assistant child,
            # but the lossy snapshot command must not become the restore source.
            process_start = direct_assistant_child_start_epoch(pane["pane_pid"], tool)

        if not session_id and process_start is not None:
            _state, session_id = matching_state(
                pane, tool, saved_dir, process_start
            )
            if process_pid:
                verified_words = exact_words

    if session_id:
        resume_words = build_resume_words(verified_words, tool, session_id)
        executable = resolve_executable(
            resume_words[0], pane.get("path", "") or saved_dir, tool
        )
        if executable:
            resume_words[0] = executable
            canonical = shlex.join(resume_words)
        else:
            canonical = fallback_shell_command()
    else:
        # Restoring an unverified assistant command can open a picker or start a new
        # session with stale permission flags. Restore a normal login shell instead.
        canonical = fallback_shell_command()

    if canonical == saved_command:
        return None
    updated[10] = f":{canonical}"
    return "\t".join(updated)


def atomic_write(path: Path, text: str, mode: int) -> None:
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_name = handle.name
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def rewrite_file(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return

    changed = False
    rewritten: list[str] = []
    for line in original.splitlines():
        replacement = rewrite_pane_line(line.split("\t"))
        if replacement is None:
            rewritten.append(line)
        else:
            rewritten.append(replacement)
            changed = True

    if changed or mode != 0o600:
        if changed:
            suffix = "\n" if original.endswith("\n") else ""
            contents = "\n".join(rewritten) + suffix
        else:
            contents = original
        atomic_write(path, contents, 0o600)


def main() -> int:
    if len(sys.argv) != 2:
        return 0
    rewrite_file(Path(sys.argv[1]).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
