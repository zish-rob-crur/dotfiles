#!/usr/bin/env python3

"""Shared, fail-closed Codex notification classification helpers."""

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple


CODEX_STATE_DB_PATTERN = "state_*.sqlite"
CLASS_ROOT = "root"
CLASS_SUBAGENT = "subagent"
CLASS_UNKNOWN = "unknown"
_KNOWN_ROOT_THREAD_SOURCES = {"user", "automation"}
_KNOWN_ROOT_SESSION_SOURCES = {"cli", "vscode", "exec", "mcp"}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SQLITE_RETRIES = 3
_SQLITE_RETRY_SECONDS = 0.03


def clean_text(value: object) -> str:
    """Collapse whitespace after removing terminal control characters."""

    text = _CONTROL_CHARACTERS.sub(" ", str(value))
    return re.sub(r"\s+", " ", text).strip()


def source_is_subagent(source: object) -> bool:
    if not isinstance(source, str):
        return False
    normalized = source.strip().lower()
    if normalized == "subagent" or normalized.startswith("subagent_"):
        return True
    try:
        parsed = json.loads(source)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and any(
        str(key).strip().lower() == "subagent" for key in parsed
    )


def _decode_toml_string(value: str) -> Optional[str]:
    value = value.strip()
    if len(value) < 2:
        return None
    if value[0] == '"' and value[-1] == '"':
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, str) else None
    if value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return None


def configured_sqlite_home(codex_home: Path) -> Optional[Path]:
    """Read the top-level sqlite_home without requiring Python 3.11 tomllib."""

    config_path = codex_home / "config.toml"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    in_top_level = True
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            in_top_level = False
            continue
        if not in_top_level:
            continue
        match = re.match(r"^sqlite_home\s*=\s*((?:\"(?:\\.|[^\"])*\")|(?:'[^']*'))\s*(?:#.*)?$", stripped)
        if not match:
            continue
        decoded = _decode_toml_string(match.group(1))
        if decoded:
            configured = Path(os.path.expandvars(decoded)).expanduser()
            # Config-layer relative paths are rooted at the directory that
            # contains config.toml, not at the notify process's project cwd.
            return configured if configured.is_absolute() else codex_home / configured
    return None


def resolve_codex_locations(
    codex_home: Optional[Path] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> Tuple[Path, Path]:
    env = os.environ if environment is None else environment
    if codex_home is None:
        home_value = env.get("CODEX_HOME", "")
        codex_home = Path(home_value).expanduser() if home_value else Path.home() / ".codex"

    configured_home = configured_sqlite_home(codex_home)
    sqlite_override = env.get("CODEX_SQLITE_HOME", "").strip()
    if configured_home is not None:
        # Codex applies the config key after the environment default.
        sqlite_home = configured_home
    elif sqlite_override:
        sqlite_home = Path(os.path.expandvars(sqlite_override)).expanduser()
    else:
        sqlite_home = codex_home
    return codex_home, sqlite_home


def codex_state_databases(sqlite_home: Path) -> List[Path]:
    if sqlite_home.is_file():
        return [sqlite_home]
    try:
        candidates = list(sqlite_home.glob(CODEX_STATE_DB_PATTERN))
        return sorted(
            candidates,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_columns(connection: sqlite3.Connection, table: str) -> List[str]:
    quoted = _quote_identifier(table)
    return [str(row[1]) for row in connection.execute("PRAGMA table_info(%s)" % quoted)]


def _classify_in_snapshot(connection: sqlite3.Connection, thread_id: str) -> Optional[str]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    }
    edge_schema_unknown = False
    if "thread_spawn_edges" in tables:
        edge_columns = _table_columns(connection, "thread_spawn_edges")
        if "child_thread_id" in edge_columns:
            edge = connection.execute(
                "SELECT 1 FROM thread_spawn_edges WHERE child_thread_id = ? LIMIT 1",
                (thread_id,),
            ).fetchone()
            if edge is not None:
                return CLASS_SUBAGENT
        else:
            edge_schema_unknown = True

    if "threads" not in tables:
        return CLASS_UNKNOWN

    thread_columns = _table_columns(connection, "threads")
    if "id" not in thread_columns:
        return CLASS_UNKNOWN

    selected = [column for column in ("thread_source", "source") if column in thread_columns]
    if not selected:
        return CLASS_UNKNOWN
    column_sql = ", ".join(_quote_identifier(column) for column in selected)
    row = connection.execute(
        "SELECT %s FROM threads WHERE id = ? LIMIT 1" % column_sql,
        (thread_id,),
    ).fetchone()
    if row is None:
        # The thread may live in an older state_N database.
        return CLASS_UNKNOWN if edge_schema_unknown else None

    values: Dict[str, object] = dict(zip(selected, row))
    marker = values.get("thread_source")
    source = values.get("source")
    if isinstance(marker, str) and marker.strip().lower() == CLASS_SUBAGENT:
        return CLASS_SUBAGENT
    if source_is_subagent(source):
        return CLASS_SUBAGENT

    if edge_schema_unknown:
        return CLASS_UNKNOWN

    # Root is an allow-list, not the absence of child evidence. This keeps new
    # schema values, SessionSource::Unknown, internal sessions, and malformed
    # provenance fail-closed until their semantics are deliberately reviewed.
    if isinstance(marker, str):
        normalized_marker = marker.strip().lower()
        if normalized_marker in _KNOWN_ROOT_THREAD_SOURCES:
            return CLASS_ROOT
        # Only SQL NULL means this row predates thread_source and may fall
        # back to the legacy source column. Empty/blank strings are malformed
        # modern provenance and must fail closed.
        return CLASS_UNKNOWN
    elif marker is not None:
        return CLASS_UNKNOWN

    if isinstance(source, str) and source.strip().lower() in _KNOWN_ROOT_SESSION_SOURCES:
        return CLASS_ROOT
    return CLASS_UNKNOWN


def _classify_database(database_path: Path, thread_id: str) -> Optional[str]:
    database_uri = database_path.resolve().as_uri() + "?mode=ro"
    last_error = None
    for attempt in range(_SQLITE_RETRIES):
        try:
            with closing(sqlite3.connect(database_uri, uri=True, timeout=0.05)) as connection:
                connection.execute("PRAGMA query_only = ON")
                connection.execute("BEGIN")
                try:
                    result = _classify_in_snapshot(connection, thread_id)
                finally:
                    connection.rollback()
                return result
        except (OSError, sqlite3.Error) as error:
            last_error = error
            if attempt + 1 < _SQLITE_RETRIES:
                time.sleep(_SQLITE_RETRY_SECONDS)
    if last_error is not None:
        return CLASS_UNKNOWN
    return None


def classify_thread(
    notification: Mapping[str, object],
    codex_home: Optional[Path] = None,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    thread_id = clean_text(notification.get("thread-id", ""))
    if not thread_id:
        return CLASS_UNKNOWN

    _, sqlite_home = resolve_codex_locations(codex_home, environment)
    databases = codex_state_databases(sqlite_home)
    if not databases:
        return CLASS_UNKNOWN

    saw_root = False
    saw_unknown = False
    for database_path in databases:
        result = _classify_database(database_path, thread_id)
        if result == CLASS_SUBAGENT:
            return CLASS_SUBAGENT
        if result == CLASS_ROOT:
            saw_root = True
        elif result == CLASS_UNKNOWN:
            saw_unknown = True
    return CLASS_ROOT if saw_root and not saw_unknown else CLASS_UNKNOWN
