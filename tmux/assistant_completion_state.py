#!/usr/bin/env python3

"""Atomic completion/ack state shared by Codex, Claude, and tmux hooks."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Tuple


SocketIdentity = Tuple[
    str,
    Optional[int],
    Optional[int],
    Optional[int],
    str,
]
TmuxServerIdentity = Tuple[int, int, str]


def collapse(value: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(path), 0o700)


def canonical_socket(tmux_socket: str) -> str:
    return os.path.realpath(tmux_socket.strip()) if tmux_socket.strip() else ""


def socket_identity(tmux_socket: str) -> SocketIdentity:
    canonical = canonical_socket(tmux_socket)
    if not canonical:
        return "", None, None, None, ""
    try:
        socket_stat = os.stat(canonical)
    except OSError:
        # Unit-test sockets and an explicitly supplied not-yet-created socket
        # retain the historical path-only namespace.
        return canonical, None, None, None, ""
    birthtime = getattr(socket_stat, "st_birthtime", None)
    if birthtime is not None:
        generation_ns = int(float(birthtime) * 1_000_000_000)
        generation_source = "birthtime"
    else:
        generation_ns = int(
            getattr(
                socket_stat,
                "st_ctime_ns",
                int(float(socket_stat.st_ctime) * 1_000_000_000),
            )
        )
        generation_source = "ctime"
    return (
        canonical,
        int(socket_stat.st_dev),
        int(socket_stat.st_ino),
        generation_ns,
        generation_source,
    )


def _server_state_dir(
    state_root: Path,
    identity: SocketIdentity,
) -> Optional[Path]:
    canonical, device, inode, generation_ns, generation_source = identity
    if not canonical:
        return None
    key_material = canonical
    if device is not None and inode is not None and generation_ns is not None:
        key_material += "\0%d:%d:%s:%d" % (
            device,
            inode,
            generation_source,
            generation_ns,
        )
    server_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:16]
    return state_root / "servers" / server_key


def server_state_dir(state_root: Path, tmux_socket: str) -> Optional[Path]:
    return _server_state_dir(state_root, socket_identity(tmux_socket))


def ensure_server_state_dir_for_identity(
    state_root: Path,
    identity: SocketIdentity,
    tmux_server: Optional[TmuxServerIdentity] = None,
) -> Optional[Path]:
    """Create a namespace from an already-frozen socket identity."""

    directory = _server_state_dir(state_root, identity)
    if directory is None:
        return None
    private_directory(state_root)
    private_directory(state_root / "servers")
    private_directory(directory)
    metadata_path = directory / "server.json"
    metadata = read_json(metadata_path)
    canonical, device, inode, generation_ns, generation_source = identity
    identity_metadata: Dict[str, object] = {
        "kind": (
            "inode-generation"
            if device is not None and inode is not None and generation_ns is not None
            else "path"
        ),
        "canonical_path": canonical,
    }
    if device is not None and inode is not None and generation_ns is not None:
        identity_metadata.update(
            {
                "device": device,
                "inode": inode,
                "generation": {
                    "source": generation_source,
                    "timestamp_ns": generation_ns,
                },
            }
        )
    expected_metadata = {
        "tmux_socket": canonical,
        "socket_identity": identity_metadata,
    }
    if tmux_server is not None:
        server_pid, server_start_time, server_socket = tmux_server
        expected_metadata["tmux_server"] = {
            "pid": server_pid,
            "start_time": server_start_time,
            "socket_path": server_socket,
        }
    elif isinstance(metadata.get("tmux_server"), dict):
        # Generic badge/restore resolution must not erase identity recorded by
        # a verified completion writer.
        expected_metadata["tmux_server"] = metadata["tmux_server"]
    if metadata != expected_metadata:
        atomic_write_json(metadata_path, expected_metadata)
    return directory


def ensure_server_state_dir(state_root: Path, tmux_socket: str) -> Optional[Path]:
    return ensure_server_state_dir_for_identity(state_root, socket_identity(tmux_socket))


def validate_expected_state_dir(
    state_root: Path,
    expected_state_dir: Path,
) -> Optional[Path]:
    """Accept only a canonical existing server namespace below state_root."""

    candidate = expected_state_dir.expanduser()
    if not candidate.is_absolute():
        return None
    try:
        resolved_candidate = candidate.resolve(strict=True)
        resolved_servers_root = (state_root.expanduser() / "servers").resolve(
            strict=True
        )
    except (OSError, RuntimeError):
        return None
    if (
        candidate != resolved_candidate
        or resolved_candidate.parent != resolved_servers_root
        or not re.match(r"^[0-9a-f]{16}$", resolved_candidate.name)
        or not resolved_candidate.is_dir()
    ):
        return None
    return resolved_candidate


def read_json(path: Path) -> Dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_path), str(path))
        os.chmod(str(path), 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


@contextmanager
def pane_lock(state_dir: Path, pane_number: str) -> Iterator[None]:
    private_directory(state_dir)
    lock_path = state_dir / ("pane-%s.state.lock" % pane_number)
    descriptor = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def iso_timestamp(timestamp_ns: Optional[int] = None) -> str:
    seconds = (time.time_ns() if timestamp_ns is None else timestamp_ns) / 1_000_000_000
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_tmux_meta(raw: str) -> Dict[str, str]:
    parts = raw.split("\t")
    if len(parts) != 8:
        return {}
    (
        session_name,
        window_id,
        window_index,
        window_name,
        pane_id,
        tmux_socket,
        server_pid,
        server_start_time,
    ) = parts
    if (
        not session_name
        or not re.match(r"^@[0-9]+$", window_id)
        or not re.match(r"^%[0-9]+$", pane_id)
        or not re.match(r"^[1-9][0-9]*$", server_pid)
        or not re.match(r"^[1-9][0-9]*$", server_start_time)
    ):
        return {}
    tmux_socket = canonical_socket(tmux_socket)
    if not tmux_socket:
        return {}
    return {
        "session_name": session_name,
        "window_id": window_id,
        "window_index": window_index,
        "window_name": window_name,
        "pane_id": pane_id,
        "tmux_socket": tmux_socket,
        "tmux_server_pid": server_pid,
        "tmux_server_start_time": server_start_time,
    }


def query_tmux_server_identity(tmux_socket: str) -> Optional[TmuxServerIdentity]:
    canonical = canonical_socket(tmux_socket)
    if not canonical:
        return None
    command = [
        "tmux",
        "-S",
        canonical,
        "display-message",
        "-p",
        "#{pid}\t#{start_time}\t#{socket_path}",
    ]
    try:
        output = subprocess.check_output(
            command,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        ).rstrip("\n")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    parts = output.split("\t")
    if (
        len(parts) != 3
        or not re.match(r"^[1-9][0-9]*$", parts[0])
        or not re.match(r"^[1-9][0-9]*$", parts[1])
    ):
        return None
    live_socket = canonical_socket(parts[2])
    if not live_socket:
        return None
    return int(parts[0]), int(parts[1]), live_socket


def verified_completion_identity(
    tmux_meta: Mapping[str, str],
) -> Optional[Tuple[SocketIdentity, TmuxServerIdentity]]:
    """Bind captured pane metadata to one stable tmux server generation."""

    tmux_socket = tmux_meta.get("tmux_socket", "")
    frozen_identity = socket_identity(tmux_socket)
    canonical, device, inode, generation_ns, _generation_source = frozen_identity
    if (
        not canonical
        or device is None
        or inode is None
        or generation_ns is None
    ):
        return None
    try:
        captured_server = (
            int(tmux_meta.get("tmux_server_pid", "")),
            int(tmux_meta.get("tmux_server_start_time", "")),
            canonical,
        )
    except (TypeError, ValueError):
        return None
    live_server = query_tmux_server_identity(canonical)
    if live_server != captured_server:
        return None
    if socket_identity(canonical) != frozen_identity:
        return None
    return frozen_identity, captured_server


def focused_pane_ids(tmux_socket: str) -> List[str]:
    command = ["tmux"]
    canonical = canonical_socket(tmux_socket)
    if canonical:
        command.extend(["-S", canonical])
    command.extend(["list-clients", "-F", "#{pane_id}\t#{client_flags}"])
    try:
        output = subprocess.check_output(
            command,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    focused = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2 or not parts[0].startswith("%"):
            continue
        flags = {flag.strip() for flag in parts[1].split(",")}
        if "focused" in flags:
            focused.append(parts[0])
    return list(dict.fromkeys(focused))


def event_timestamp_ns(environment: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if environment is None else environment
    try:
        value = int(env.get("CODEX_NOTIFY_RECEIVED_NS", ""))
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else time.time_ns()


def write_completion(
    state_root: Path,
    tmux_meta_raw: str,
    notification: Mapping[str, object],
    source: str,
    event_ns: Optional[int] = None,
) -> bool:
    write_started_ns = time.time_ns()
    tmux_meta = parse_tmux_meta(tmux_meta_raw)
    if not tmux_meta:
        return False
    verified_identity = verified_completion_identity(tmux_meta)
    if verified_identity is None:
        return False
    frozen_identity, tmux_server = verified_identity
    state_dir = ensure_server_state_dir_for_identity(
        state_root,
        frozen_identity,
        tmux_server,
    )
    if state_dir is None:
        return False
    pane_id = tmux_meta["pane_id"]
    pane_number = pane_id.lstrip("%")
    state_path = state_dir / ("pane-%s.json" % pane_number)
    ack_path = state_dir / ("pane-%s.ack" % pane_number)
    completed_ns = event_timestamp_ns() if event_ns is None else event_ns

    with pane_lock(state_dir, pane_number):
        existing = read_json(state_path)
        try:
            existing_completed_ns = int(existing.get("completed_at_ns", 0))
        except (TypeError, ValueError):
            existing_completed_ns = 0
        thread_id = collapse(
            notification.get("thread-id", "")
            if source == "codex"
            else notification.get("session_id", "")
        )
        if not thread_id:
            return False
        existing_id = collapse(
            existing.get("session_id", "")
            if source == "claude"
            else existing.get("thread_id", "")
        )
        if completed_ns < existing_completed_ns:
            return False
        if completed_ns == existing_completed_ns and existing:
            return existing.get("source") == source and existing_id == thread_id

        ack = read_json(ack_path)
        try:
            acked_ns = int(ack.get("acked_at_ns", 0))
        except (TypeError, ValueError):
            acked_ns = 0
        focused = pane_id in focused_pane_ids(tmux_meta["tmux_socket"])
        unread = not focused and acked_ns < completed_ns

        payload = dict(tmux_meta)
        payload.update(
            {
                "source": source,
                "completed_at": iso_timestamp(completed_ns),
                "completed_at_ns": completed_ns,
                "updated_at": iso_timestamp(),
                "cwd": collapse(notification.get("cwd", "")),
                "thread_id": thread_id,
                "unread": unread,
            }
        )
        if source == "claude":
            payload["session_id"] = thread_id
        if not unread:
            payload["acked_at"] = iso_timestamp(max(acked_ns, completed_ns))
            payload["acked_at_ns"] = max(acked_ns, completed_ns)
        atomic_write_json(state_path, payload)

        # The tmux focus hook may have acknowledged this pane while the
        # completion payload was being built. Reconcile its atomic marker
        # after our first replace so a delayed writer cannot resurrect green.
        final_ack = read_json(ack_path)
        try:
            final_acked_ns = int(final_ack.get("acked_at_ns", 0))
        except (TypeError, ValueError):
            final_acked_ns = 0
        if payload["unread"] and (
            final_acked_ns >= write_started_ns
            or pane_id in focused_pane_ids(tmux_meta["tmux_socket"])
        ):
            payload["unread"] = False
            payload["acked_at_ns"] = max(final_acked_ns, completed_ns)
            payload["acked_at"] = iso_timestamp(int(payload["acked_at_ns"]))
            payload["updated_at"] = iso_timestamp()
            atomic_write_json(state_path, payload)
    return True


def acknowledge(
    state_root: Path,
    pane_id: str,
    tmux_socket: str,
    acknowledged_ns: Optional[int] = None,
    expected_state_dir: Optional[Path] = None,
    require_focused: bool = False,
) -> Optional[bool]:
    if not re.match(r"^%?[0-9]+$", pane_id):
        return None
    pane_number = pane_id.lstrip("%")
    canonical_pane_id = "%" + pane_number

    frozen_identity: Optional[SocketIdentity] = None
    if expected_state_dir is not None:
        expected = validate_expected_state_dir(state_root, expected_state_dir)
        if expected is None:
            return False
        frozen_identity = socket_identity(tmux_socket)
        canonical, device, inode, generation_ns, _generation_source = frozen_identity
        if (
            not canonical
            or device is None
            or inode is None
            or generation_ns is None
        ):
            return False
        calculated_state_dir = _server_state_dir(state_root, frozen_identity)
        if calculated_state_dir is None:
            return False
        try:
            calculated_state_dir = calculated_state_dir.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        if calculated_state_dir != expected:
            return False
        state_dir = expected
    else:
        # Focus hooks must bind their event to the namespace they resolved
        # before this helper started. Direct non-hook callers retain the
        # historical API for tests and explicit maintenance commands.
        if require_focused:
            return False
        state_dir = ensure_server_state_dir(state_root, tmux_socket)
        if state_dir is None:
            return None

    if require_focused and canonical_pane_id not in focused_pane_ids(tmux_socket):
        return False
    if frozen_identity is not None and socket_identity(tmux_socket) != frozen_identity:
        return False

    state_path = state_dir / ("pane-%s.json" % pane_number)
    ack_path = state_dir / ("pane-%s.ack" % pane_number)
    ack_ns = time.time_ns() if acknowledged_ns is None else acknowledged_ns
    with pane_lock(state_dir, pane_number):
        previous_marker = read_json(ack_path)
        state = read_json(state_path)
        prior_values = [ack_ns]
        for value in (
            previous_marker.get("acked_at_ns", 0),
            state.get("acked_at_ns", 0),
        ):
            try:
                prior_values.append(int(value))
            except (TypeError, ValueError):
                pass
        effective_ack_ns = max(prior_values)
        marker = {
            "pane_id": canonical_pane_id,
            "acked_at": iso_timestamp(effective_ack_ns),
            "acked_at_ns": effective_ack_ns,
        }
        try:
            completed_ns = int(state.get("completed_at_ns", 0))
        except (TypeError, ValueError):
            completed_ns = 0
        can_acknowledge_state = completed_ns <= 0 or effective_ack_ns >= completed_ns
        changed = state.get("unread") is True and can_acknowledge_state
        if state and can_acknowledge_state:
            marker["acked_completed_at_ns"] = state.get("completed_at_ns", 0)
            state["unread"] = False
            state["acked_at"] = marker["acked_at"]
            state["acked_at_ns"] = effective_ack_ns
            state["updated_at"] = marker["acked_at"]
            atomic_write_json(state_path, state)
        atomic_write_json(ack_path, marker)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--tmux-socket", default="")
    parser.add_argument("--resolve-state-dir", action="store_true")
    parser.add_argument("--tmux-meta", default=os.environ.get("TMUX_META", ""))
    parser.add_argument("--write-codex", action="store_true")
    parser.add_argument("--write-claude", action="store_true")
    parser.add_argument("--ack-pane")
    parser.add_argument("--require-focused", action="store_true")
    parser.add_argument("--expected-state-dir")
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args()
    state_root = Path(args.state_dir).expanduser()
    if args.resolve_state_dir:
        directory = ensure_server_state_dir(state_root, args.tmux_socket)
        if directory is None:
            return 1
        print(directory.resolve(strict=True))
        return 0
    if args.ack_pane:
        acknowledged_ns = time.time_ns()
        changed = acknowledge(
            state_root,
            args.ack_pane,
            args.tmux_socket,
            acknowledged_ns,
            (
                Path(args.expected_state_dir).expanduser()
                if args.expected_state_dir
                else None
            ),
            args.require_focused,
        )
        if changed is None:
            return 1
        print("changed" if changed else "unchanged")
        return 0
    if not (args.write_codex or args.write_claude) or not args.payload:
        return 2
    try:
        notification = json.loads(args.payload)
    except (TypeError, ValueError):
        return 2
    if not isinstance(notification, dict):
        return 2
    source = "codex" if args.write_codex else "claude"
    if source == "codex" and notification.get("type") != "agent-turn-complete":
        return 0
    return 0 if write_completion(state_root, args.tmux_meta, notification, source) else 0


if __name__ == "__main__":
    raise SystemExit(main())
