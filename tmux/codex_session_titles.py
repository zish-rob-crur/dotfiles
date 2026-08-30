#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Optional

from codex_notify_common import (
    clean_text,
    codex_state_databases,
    resolve_codex_locations,
)


SCRIPT_DIR = Path(__file__).resolve().parent
MAX_TITLE_LENGTH = 120
NEW_SESSION_MATCH_SECONDS = 30
RESUME_PICKER_MATCH_SECONDS = 10 * 60
SQLITE_RETRIES = 3
SQLITE_RETRY_SECONDS = 0.03
REFRESH_INTERVAL = float(os.environ.get("CODEX_SESSION_TITLE_REFRESH_INTERVAL", "10"))
REFRESH_STAMP = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "tmux-pane-layout-status"
    / ".codex-session-title-refresh-stamp"
)


def load_restart_module() -> ModuleType:
    path = SCRIPT_DIR / "restart-assistant-panes.py"
    spec = importlib.util.spec_from_file_location(
        "restart_assistant_panes_for_titles", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RESTART = load_restart_module()


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: str
    cwd: str
    name: str
    title: str
    created_at: Optional[float]
    recency_at: Optional[float]


def bounded_title(value: object) -> str:
    title = clean_text(value)
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return title[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


def load_session_index_titles(codex_home: Path) -> dict[str, str]:
    """Read the append-only resume-picker index; the last record wins."""

    titles: dict[str, str] = {}
    path = codex_home / "session_index.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return titles

    for line in lines:
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        thread_id = str(payload.get("id", "")).strip()
        if not RESTART.is_uuid(thread_id):
            continue
        title = bounded_title(payload.get("thread_name", ""))
        if title:
            titles[thread_id] = title
        else:
            titles.pop(thread_id, None)
    return titles


def _epoch_from_columns(values: dict[str, object], *names: str) -> Optional[float]:
    for name in names:
        value = values.get(name)
        if name.endswith("_ms") and isinstance(value, (int, float)):
            epoch = RESTART.parse_epoch(float(value) / 1000.0)
        else:
            epoch = RESTART.parse_epoch(value)
        if epoch is not None:
            return epoch
    return None


def _records_from_database(
    database: Path, cwd_values: Optional[set[str]] = None
) -> dict[str, ThreadRecord]:
    uri = database.resolve().as_uri() + "?mode=ro"
    for attempt in range(SQLITE_RETRIES):
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                connection.execute("PRAGMA query_only = ON")
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(threads)")
                }
                required = {"id", "cwd", "title"}
                if not required.issubset(columns):
                    return {}
                selected = [
                    column
                    for column in (
                        "id",
                        "cwd",
                        "name",
                        "title",
                        "created_at_ms",
                        "created_at",
                        "recency_at_ms",
                        "recency_at",
                        "updated_at_ms",
                        "updated_at",
                    )
                    if column in columns
                ]
                names = ", ".join('"%s"' % column for column in selected)
                records: dict[str, ThreadRecord] = {}
                query = f"SELECT {names} FROM threads"
                parameters: tuple[str, ...] = ()
                if cwd_values:
                    parameters = tuple(sorted(cwd_values))
                    placeholders = ", ".join("?" for _value in parameters)
                    query += f" WHERE cwd IN ({placeholders})"
                for row in connection.execute(query, parameters):
                    values = dict(zip(selected, row))
                    thread_id = str(values.get("id", "")).strip()
                    if not RESTART.is_uuid(thread_id):
                        continue
                    records[thread_id] = ThreadRecord(
                        thread_id=thread_id,
                        cwd=str(values.get("cwd", "") or ""),
                        # Clean only the handful of live titles that will be
                        # rendered; a state database can contain thousands of
                        # historical rows.
                        name=str(values.get("name", "") or ""),
                        title=str(values.get("title", "") or ""),
                        created_at=_epoch_from_columns(
                            values, "created_at_ms", "created_at"
                        ),
                        recency_at=_epoch_from_columns(
                            values,
                            "recency_at_ms",
                            "recency_at",
                            "updated_at_ms",
                            "updated_at",
                        ),
                    )
                return records
        except (OSError, sqlite3.Error):
            if attempt + 1 < SQLITE_RETRIES:
                time.sleep(SQLITE_RETRY_SECONDS)
    return {}


def load_thread_records(
    sqlite_home: Path, cwd_values: Optional[set[str]] = None
) -> dict[str, ThreadRecord]:
    records: dict[str, ThreadRecord] = {}
    # Databases are newest first. Keep the first copy of a thread so an older
    # state_N snapshot cannot overwrite a rename from the current database.
    for database in codex_state_databases(sqlite_home):
        for thread_id, record in _records_from_database(
            database, cwd_values
        ).items():
            records.setdefault(thread_id, record)
    return records


def display_titles(
    records: dict[str, ThreadRecord],
    picker_titles: dict[str, str],
    thread_ids: Optional[set[str]] = None,
) -> dict[str, str]:
    selected_ids = thread_ids if thread_ids is not None else set(records) | set(picker_titles)
    rendered: dict[str, str] = {}
    for thread_id in selected_ids:
        record = records.get(thread_id)
        title = ""
        if record is not None:
            title = record.name
        if not title:
            title = picker_titles.get(thread_id, "")
        if not title and record is not None:
            title = record.title
        if title:
            rendered[thread_id] = bounded_title(title)
    return rendered


def _same_cwd(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(os.path.expanduser(left))) == os.path.normcase(
        os.path.abspath(os.path.expanduser(right))
    )


def _unique_time_match(
    pane: dict[str, str],
    process_start: float,
    records: dict[str, ThreadRecord],
    used_ids: set[str],
    resumed_from_picker: bool,
) -> str:
    candidates: list[str] = []
    for thread_id, record in records.items():
        if thread_id in used_ids or not _same_cwd(record.cwd, pane.get("path", "")):
            continue
        timestamp = record.recency_at if resumed_from_picker else record.created_at
        if timestamp is None:
            continue
        delta = timestamp - process_start
        limit = (
            RESUME_PICKER_MATCH_SECONDS
            if resumed_from_picker
            else NEW_SESSION_MATCH_SECONDS
        )
        if -5 <= delta <= limit:
            candidates.append(thread_id)
    return candidates[0] if len(candidates) == 1 else ""


def process_start_times() -> dict[str, float]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,lstart="],
            text=True,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.CalledProcessError):
        return {}

    starts: dict[str, float] = {}
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        try:
            starts[fields[0]] = datetime.strptime(
                fields[1], "%a %b %d %H:%M:%S %Y"
            ).timestamp()
        except ValueError:
            continue
    return starts


def resolve_live_thread_ids(
    panes: list[dict[str, str]], records: dict[str, ThreadRecord]
) -> dict[str, str]:
    children = RESTART.children_by_parent(RESTART.process_rows())
    process_starts = process_start_times()
    resolved: dict[str, str] = {}
    used_ids: set[str] = set()
    unresolved: list[tuple[dict[str, str], list[str], float]] = []

    for pane in panes:
        if RESTART.pane_tool(pane) != "codex":
            continue
        process_pid, exact_words = RESTART.current_assistant_process(
            pane, "codex", children
        )
        if not process_pid or not exact_words:
            continue

        thread_id = RESTART.resume_id_from_words(exact_words, "codex")
        process_start = process_starts.get(process_pid)
        if not thread_id and process_start is not None:
            thread_id, _state_cwd = RESTART.saved_session_id(
                "codex", pane, process_start
            )
        if thread_id:
            resolved[pane["pane_id"]] = thread_id
            used_ids.add(thread_id)
        elif process_start is not None:
            unresolved.append((pane, exact_words, process_start))

    for pane, exact_words, process_start in unresolved:
        resumed_from_picker = "resume" in exact_words
        thread_id = _unique_time_match(
            pane, process_start, records, used_ids, resumed_from_picker
        )
        if thread_id:
            resolved[pane["pane_id"]] = thread_id
            used_ids.add(thread_id)

    return resolved


def old_pane_options() -> dict[str, tuple[str, str]]:
    fmt = "\t".join(
        ("#{pane_id}", "#{@codex-session-id}", "#{@codex-session-title}")
    )
    try:
        output = RESTART.tmux(["list-panes", "-a", "-F", fmt], check=True)
    except (OSError, subprocess.CalledProcessError):
        return {}
    options: dict[str, tuple[str, str]] = {}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].startswith("%"):
            options[parts[0]] = (parts[1], parts[2])
    return options


def _set_pane_option(pane_id: str, name: str, value: str) -> None:
    args = ["set-option", "-pq", "-t", pane_id]
    if value:
        args.extend((name, value))
    else:
        args.insert(2, "-u")
        args.append(name)
    try:
        RESTART.tmux(args, check=True)
    except (OSError, subprocess.CalledProcessError):
        pass


def refresh_is_due(force: bool = False) -> bool:
    if force:
        return True
    now = time.time()
    try:
        previous = float(REFRESH_STAMP.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        previous = 0.0
    if now - previous < REFRESH_INTERVAL:
        return False
    try:
        REFRESH_STAMP.parent.mkdir(parents=True, exist_ok=True)
        REFRESH_STAMP.write_text(f"{now}\n", encoding="utf-8")
    except OSError:
        pass
    return True


def refresh_codex_pane_titles(force: bool = False) -> int:
    if not refresh_is_due(force):
        return 0
    try:
        panes = RESTART.list_panes()
    except (OSError, subprocess.CalledProcessError):
        return 0

    codex_home, sqlite_home = resolve_codex_locations()
    codex_cwds = {
        pane.get("path", "")
        for pane in panes
        if RESTART.pane_tool(pane) == "codex" and pane.get("path", "")
    }
    records = load_thread_records(sqlite_home, codex_cwds)
    picker_titles = load_session_index_titles(codex_home)
    live_ids = resolve_live_thread_ids(panes, records)
    titles = display_titles(records, picker_titles, set(live_ids.values()))
    old_options = old_pane_options()
    changed = 0

    for pane in panes:
        pane_id = pane["pane_id"]
        thread_id = live_ids.get(pane_id, "")
        title = titles.get(thread_id, "") if thread_id else ""
        old_id, old_title = old_options.get(pane_id, ("", ""))
        if thread_id != old_id:
            _set_pane_option(pane_id, "@codex-session-id", thread_id)
            changed += 1
        if title != old_title:
            _set_pane_option(pane_id, "@codex-session-title", title)
            changed += 1

    return changed
