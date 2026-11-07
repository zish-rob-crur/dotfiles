#!/usr/bin/env python3
"""Weekly tasks, pinned Focus views and honest session logs in Markdown."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib
import unicodedata
import uuid
import hashlib
from urllib.parse import urlencode, quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import todo_notes as todo
from contextlib import contextmanager

CONFIG = Path(os.environ.get("TASK_BOARD_CONFIG", str(Path.home() / ".config/task-board/config.toml")))
HEADER = "| Session | Started | Finished | Focus minutes | Result | Source |"
RULE = "| --- | --- | --- | ---: | --- | --- |"
RESULTS = {"completed", "partial", "cancelled", "pending"}


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def vaults() -> list[dict]:
    config = tomllib.loads(CONFIG.read_text())
    result = []
    for item in config.get("vaults", []):
        root = Path(item["path"]).expanduser().resolve()
        folder = root / item.get("focus_folder", str(Path(item.get("todo_folder", "Inbox/Todo")).parent / "Focus"))
        if not folder.resolve().is_relative_to(root):
            raise ValueError("focus_folder must stay inside its vault")
        if root.is_dir():
            result.append({"name": item["name"], "folder": folder})
    return result


def timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Session timestamps must include a timezone")
    return parsed


def frontmatter(text: str) -> tuple[dict, list[str], str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("Expected YAML frontmatter")
    raw, body = text[4:].split("\n---\n", 1)
    fields = {}
    for line in raw.splitlines():
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        fields[key] = value
    return fields, raw.splitlines(), body


def parse(path: Path, vault: str) -> dict:
    text = path.read_text()
    fields, _, body = frontmatter(text)
    ident, title = fields.get("focus_id", ""), fields.get("title", "")
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", ident) or not title:
        raise ValueError("focus_id and title are required")
    status = fields.get("status", "active")
    if status not in {"active", "completed"}:
        raise ValueError("status must be active or completed")
    sessions = []
    in_table = False
    for line in body.splitlines():
        if line == HEADER:
            in_table = True
            continue
        if not in_table or line == RULE:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            raise ValueError("Invalid focus session row")
        session_id, started, finished, minutes, result, source = cells
        if result not in RESULTS or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", session_id):
            raise ValueError("Invalid session ID or result")
        start = timestamp(started)
        end = timestamp(finished) if finished != "—" else None
        duration = float(minutes)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("Focus minutes must be a finite nonnegative number")
        if end and (end < start or duration * 60 > (end - start).total_seconds() + 1):
            raise ValueError("Focus minutes cannot exceed the elapsed session time")
        if result in {"completed", "partial"} and (not end or not source or source == "—"):
            raise ValueError("Counted sessions need an end time and source")
        if any(s["id"] == session_id for s in sessions):
            raise ValueError("Duplicate session ID")
        sessions.append({"id": session_id, "started": started, "finished": finished if end else "",
                         "minutes": duration, "result": result, "source": source})
    counted = [s for s in sessions if s["result"] in {"completed", "partial"}]
    return {"id": ident, "title": title, "status": status, "vault": vault, "file": str(path),
            "created": fields.get("created", ""), "completed": fields.get("completed", ""),
            "started": min((s["started"] for s in sessions), key=timestamp, default=""),
            "minutes": round(sum(s["minutes"] for s in counted), 2),
            "pomodoros": sum(s["result"] == "completed" for s in sessions),
            "sessions": sessions}


@contextmanager
def locked():
    # Serialise our writers, including simultaneous UI requests. Editor writes
    # are protected separately by comparing the original text before replace.
    root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-task-board"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "focus.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def replace(path: Path, original: str, text: str):
    if path.read_text() != original:
        raise ValueError("Markdown file changed in your editor; reload and try again")
    fd, temp = tempfile.mkstemp(prefix=".focus-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, path.stat().st_mode & 0o777)
        if path.read_text() != original:
            raise ValueError("Markdown file changed in your editor; reload and try again")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


TASK_ID = re.compile(r"<!-- task:([a-zA-Z0-9-]{1,80}) -->")
PIN = "<!-- focus -->"
LOG_HEADER = "| Request | Task ID | Task | Requested | Planned min | Started | Finished | Focus min | Result | Source |"
LOG_RULE = "| --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |"


def configured():
    # Unlike the older CLI, invalid configuration must not look like an empty board.
    tomllib.loads(CONFIG.read_text())
    result = todo.load_vaults(CONFIG)
    if not result:
        raise ValueError("No task vaults configured")
    for v in result:
        if not v.week_file(todo.week_id()).resolve().is_relative_to(v.path):
            raise ValueError("todo_folder must stay inside its vault")
    return result


def backup(path, original):
    # One pre-migration copy, including all prose, for each existing weekly file.
    destination = path.with_suffix(path.suffix + ".before-unified-focus")
    try:
        with destination.open("x") as handle:
            handle.write(original)
    except FileExistsError:
        pass


def identify(vault):
    path = todo.ensure_week(vault)
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    for item in todo.parse_file(vault.name, path):
        ids = TASK_ID.findall(item.raw)
        if len(ids) > 1:
            raise ValueError("A task has multiple IDs; keep one unique task ID")
        if not ids:
            line = lines[item.line - 1]
            lines[item.line - 1] = line.rstrip("\n") + f" <!-- task:{uuid.uuid4().hex} -->\n"
    text = "".join(lines)
    if text != original:
        backup(path, original)
        replace(path, original, text)
    return path


def validate_section(section):
    if (not isinstance(section, str) or not section.strip() or "<!--" in section
            or any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in section)):
        raise ValueError("Choose a task group on one line, without control characters or metadata comments")
    return section.strip()


def sections_for(vault):
    path = vault.week_file(todo.week_id())
    lines = path.read_text().splitlines() if path.exists() else []
    # Use the same heading semantics as parse_file, including child headings.
    names = [m.group(2) for line in lines if (m := todo.HEADING_RE.match(line))
             and len(m.group(1)) > 1 and m.group(2)]
    return list(dict.fromkeys([*names, "Inbox"]))


def insert_task_row(lines, row, section):
    # Repeated headings merge in the board; insert in the first matching block.
    start = next((i + 1 for i, line in enumerate(lines)
                  if (m := todo.HEADING_RE.match(line)) and len(m.group(1)) > 1 and m.group(2) == section), None)
    if start is None:
        if section != "Inbox":
            raise ValueError("Task group is no longer in this vault; refresh and choose another group")
        lines.append(("\n" if lines and not lines[-1].endswith("\n") else "") + "\n## Inbox\n\n")
        start = len(lines)
    while start < len(lines) and not todo.HEADING_RE.match(lines[start]):
        start += 1
    separator = "\n" if start and not lines[start - 1].endswith("\n") else ""
    lines.insert(start, separator + row + ("" if row.endswith("\n") else "\n"))


def append_task(vault, title, ident, pinned=False, done=False, section="Inbox"):
    section = validate_section(section)
    path = identify(vault)
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    stamp = " ✅ " + dt.date.today().isoformat() if done else ""
    row = f"- [{'x' if done else ' '}] {title}{stamp} <!-- task:{ident} -->" + (" " + PIN if pinned else "")
    insert_task_row(lines, row, section)
    replace(path, original, "".join(lines))


def legacy_tasks():
    tasks, errors = [], []
    for v in vaults():
        for path in sorted(v["folder"].glob("*.md")):
            if path.name.startswith("_") or re.fullmatch(r"\d{4}-\d{2}\.md", path.name):
                continue
            try:
                if "focus_id:" in path.read_text():
                    tasks.append(parse(path, v["name"]))
            except (OSError, ValueError) as error:
                errors.append(f"{path.name}: {error}")
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate legacy focus_id; resolve before migrating")
    return tasks, errors


def migrate(vs, legacy):
    for v in vs:
        identify(v)
        known = set()
        for file in (v.path / v.todo_folder).glob("????-W??.md"):
            known.update(TASK_ID.findall(file.read_text()))
        for task in legacy:
            if task["vault"] != v.name or task["id"] in known:
                continue
            # Keep the original note byte-for-byte, linking it from the weekly task.
            relative = os.path.relpath(task["file"], v.week_file(todo.week_id()).parent)
            title = task["title"] + f" [Notes]({quote(relative)})"
            append_task(v, title, task["id"], pinned=task["status"] == "active", done=task["status"] == "completed")
            known.add(task["id"])


def journal(vault_name):
    v = next(v for v in vaults() if v["name"] == vault_name)
    return v["folder"] / (dt.date.today().strftime("%Y-%m") + ".md")


def ensure_journal(vault_name):
    path = journal(vault_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("x") as handle:
            handle.write("# Focus sessions · " + path.stem + "\n\n"
                         "Requests are unconfirmed. Only verified completed/partial rows count.\n\n" + LOG_HEADER + "\n" + LOG_RULE + "\n")
    return path


def read_logs():
    records, errors, seen = {}, [], set()
    for v in vaults():
        for path in sorted(v["folder"].glob("????-??.md")):
            active = False
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line == LOG_HEADER:
                    active = True
                    continue
                if not active or line == LOG_RULE:
                    continue
                if not line.startswith("|"):
                    active = False
                    continue
                try:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) != 10:
                        raise ValueError("Expected ten session columns")
                    rid, ident, title, requested, planned, started, finished, minutes, result, source = cells
                    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", rid) or rid in seen:
                        raise ValueError("Invalid or duplicate request ID")
                    seen.add(rid)
                    timestamp(requested)
                    value = float(minutes)
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("Focus minutes must be finite and nonnegative")
                    if result not in {"requested", "failed", "cancelled", "completed", "partial"}:
                        raise ValueError("Invalid session result")
                    if result in {"completed", "partial"}:
                        start, end = timestamp(started), timestamp(finished)
                        if end < start or value * 60 > (end - start).total_seconds() + 1:
                            raise ValueError("Focus minutes cannot exceed elapsed time")
                        if source in {"", "—", "raycast-link"}:
                            raise ValueError("Confirmed sessions require an explicit evidence source, e.g. manual")
                    records.setdefault((v["name"], ident), []).append({
                        "id": rid, "requested": requested, "started": "" if started == "—" else started,
                        "finished": "" if finished == "—" else finished, "minutes": value,
                        "result": result, "source": source, "file": str(path)})
                except ValueError as error:
                    errors.append(f"{path.name}:{number}: {error}")
    # A duplicate or invalid row makes totals uncertain: fail closed, retain the tasks.
    return ({} if errors else records), errors


COMMENT_HEADING = re.compile(r"^## (\S+) <!-- comment:([a-zA-Z0-9-]{1,80}) -->$")


def comments_path(vault_name, ident):
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", ident):
        raise ValueError("Invalid task ID")
    v = next((v for v in configured() if v.name == vault_name), None)
    if not v:
        raise ValueError("Unknown task vault")
    path = v.path / v.todo_folder / "comments" / (ident + ".md")
    if not path.resolve().is_relative_to(v.path):
        raise ValueError("Comments must stay inside their vault")
    return path


def parse_comments(text, ident):
    fields, _, body = frontmatter(text)
    if fields.get("task_id") != ident:
        raise ValueError("Comment file task_id does not match this task")
    comments, seen = [], set()
    lines = body.splitlines()
    for i, line in enumerate(lines):
        match = COMMENT_HEADING.fullmatch(line)
        if not match:
            if line.startswith("## ") and "<!-- comment:" in line:
                raise ValueError("Invalid comment heading; keep its timestamp and ID")
            continue
        created, cid = match.groups()
        timestamp(created)
        if cid in seen:
            raise ValueError("Duplicate comment ID")
        seen.add(cid)
        content = []
        pos = i + 1
        # Each body is quoted so headings, task checkboxes and metadata in the
        # user's text remain content, not new records or weekly tasks.
        while pos < len(lines) and (lines[pos] == ">" or lines[pos].startswith("> ")):
            content.append(lines[pos][2:] if lines[pos].startswith("> ") else "")
            pos += 1
        if not content or not "\n".join(content).strip():
            raise ValueError("Comment body is missing; use Markdown blockquote lines")
        comments.append({"id": cid, "created": created, "text": "\n".join(content)})
    return comments


def read_comments(vault_name, ident):
    path = comments_path(vault_name, ident)
    return parse_comments(path.read_text(), ident) if path.exists() else []


def add_comment(ident, text, request_id=None):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or len(text) > 4000 or "\x00" in text:
        raise ValueError("Enter a comment of 1–4000 characters")
    cid = request_id or uuid.uuid4().hex
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", cid):
        raise ValueError("Invalid comment request ID")
    task = find(ident)
    path = comments_path(task["vault"], ident)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text() if path.exists() else None
    existing = parse_comments(original, ident) if original is not None else []
    previous = next((c for c in existing if c["id"] == cid), None)
    if previous:
        if previous["text"] != text:
            raise ValueError("This comment request ID was already used for different text")
        return {"id": ident, "file": str(path), "comment": previous}
    created = now()
    base = original if original is not None else f"---\ntask_id: {ident}\n---\n\n# Task comments\n"
    record = f"\n## {created} <!-- comment:{cid} -->\n" + "\n".join("> " + line for line in text.split("\n")) + "\n"
    updated = base + ("\n" if not base.endswith("\n") else "") + record
    if original is None:
        with path.open("x") as handle:
            handle.write(updated)
    else:
        replace(path, original, updated)
    return {"id": ident, "file": str(path), "comment": {"id": cid, "created": created, "text": text}}


def snapshot(read_only=False):
    vs = configured()
    legacy, errors = legacy_tasks()
    if not read_only:
        migrate(vs, legacy)
    logs, log_errors = read_logs()
    errors += log_errors
    tasks, seen = [], set()
    if read_only:
        items = (item for v in vs for item in todo.parse_file(v.name, v.week_file(todo.week_id())))
    else:
        items = todo.this_week_items(vs)
    for item in items:
        if read_only:
            item.stale = todo.staleness(item, todo.week_id())
        ids = TASK_ID.findall(item.raw)
        if len(ids) != 1 or ids[0] in seen:
            raise ValueError("Missing or duplicate task ID in this week; keep each ID unique")
        ident = ids[0]
        seen.add(ident)
        task = item.as_dict()
        sessions = logs.get((item.vault, ident), []) + [
            s for old in legacy if old["id"] == ident and old["vault"] == item.vault for s in old["sessions"]]
        counted = [s for s in sessions if s["result"] in {"completed", "partial"}]
        completed = re.search(r"<!-- completed:([^ >]+) -->", item.raw)
        date_done = re.search(r"✅ (\d{4}-\d{2}-\d{2})", item.raw)
        task.update(id=ident, pinned=PIN in item.raw,
                    completed_at=(completed.group(1) if completed else date_done.group(1) if date_done else "") if item.done else "",
                    minutes=round(sum(s["minutes"] for s in counted), 2),
                    pomodoros=sum(s["result"] == "completed" for s in counted),
                    started=min((s["started"] for s in counted), key=timestamp, default=""),
                    unconfirmed=sum(s["result"] in {"requested", "pending"} for s in sessions),
                    sessions=sessions, log_file=str(journal(item.vault)), stats_error=bool(log_errors))
        try:
            task["comments"] = read_comments(item.vault, ident)
            task["comments_error"] = ""
        except (OSError, ValueError) as error:
            task["comments"], task["comments_error"] = [], str(error)
            errors.append(f"Comments for {ident}: {error}")
        task["comments_file"] = str(comments_path(item.vault, ident))
        tasks.append(task)
    return {"tasks": tasks, "week": todo.week_id(),
            "vaults": [{"name": v.name, "sections": sections_for(v)} for v in vs], "errors": errors}


def find(ident):
    matches = [t for t in snapshot()["tasks"] if t["id"] == ident]
    if len(matches) != 1:
        raise ValueError("Task is no longer in this week; refresh and try again")
    return matches[0]


def create(vault_name, title, pinned=False, request_id=None, section="Inbox"):
    title = title.strip()
    if not title or len(title) > 240 or any(c in title for c in "\n\r\x00") or "<!--" in title:
        raise ValueError("Enter a task title of 1–240 characters on one line, without metadata comments")
    section = validate_section(section)
    vs = configured()
    v = next((v for v in vs if v.name == vault_name), None)
    if not v:
        raise ValueError("Unknown task vault")
    # Retried creation uses the same stable ID; identical titles remain independent.
    ident = hashlib.sha256(("create:" + request_id).encode()).hexdigest()[:32] if request_id else uuid.uuid4().hex
    if section not in sections_for(v):
        # A completed request remains idempotent even if its group was renamed.
        if request_id and any(ident in TASK_ID.findall(item.raw)
                              for vault in vs for item in todo.parse_file(vault.name, vault.week_file(todo.week_id()))):
            return find(ident)
        # Validate before snapshot() can initialize IDs or migrate existing notes.
        raise ValueError("Task group is no longer in this vault; refresh and choose another group")
    data = snapshot()
    if any(t["id"] == ident for t in data["tasks"]):
        return find(ident)
    append_task(v, title, ident, pinned, section=section)
    return find(ident)


def check_standalone_task(lines, index):
    # The weekly parser treats each checkbox line independently. Refuse ambiguous
    # list trees/continuations rather than leaving children or prose behind.
    if lines[index][:1].isspace():
        raise ValueError("This task is nested; use Edit Markdown to move it with its surrounding list")
    for line in lines[index + 1:]:
        if not line.strip():
            continue
        if todo.HEADING_RE.match(line) or todo.DEF_RE.match(line):
            return
        if todo.ITEM_RE.match(line) and not line[:1].isspace():
            return
        raise ValueError("This task has attached or ambiguous text; use Edit Markdown to move the complete task")


def move(ident, section):
    section = validate_section(section)
    if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", ident):
        raise ValueError("Invalid task ID")
    # Read the actual current-week files without snapshot()/identify() side effects.
    # Resolve by ID against the same original text that the final CAS will check.
    matches = []
    for vault in configured():
        path = vault.week_file(todo.week_id())
        if not path.exists():
            continue
        original = path.read_text()
        lines = original.splitlines(keepends=True)
        current = ""
        for index, line in enumerate(lines):
            if heading := todo.HEADING_RE.match(line):
                current = heading.group(2) if len(heading.group(1)) > 1 else ""
            if ident in TASK_ID.findall(line):
                matches.append((vault, path, original, lines, index, current))
    if len(matches) != 1:
        raise ValueError("Task is missing or its ID is duplicated in this week; refresh and check Markdown")
    vault, path, original, lines, index, current = matches[0]
    if not todo.ITEM_RE.match(lines[index]) or len(TASK_ID.findall(lines[index])) != 1:
        raise ValueError("Task changed in your editor; refresh and check Markdown")
    result = {"id": ident, "vault": vault.name, "file": str(path), "line": index + 1, "section": section}
    if current == section:
        return result
    check_standalone_task(lines, index)
    row = lines.pop(index)
    insert_task_row(lines, row, section)
    updated = "".join(lines)
    replace(path, original, updated)
    result["line"] = next(i for i, line in enumerate(updated.splitlines(), 1) if ident in TASK_ID.findall(line))
    return result


def change(ident, action):
    task = find(ident)
    path = Path(task["file"])
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    # Resolve by ID again against the exact text being written, never a stale line number.
    matches = [i for i, line in enumerate(lines) if ident in TASK_ID.findall(line)]
    if len(matches) != 1:
        raise ValueError("Task changed in your editor; refresh and try again")
    i = matches[0]
    row = lines[i].rstrip("\n")
    m = todo.ITEM_RE.match(row)
    if not m:
        raise ValueError("Task is no longer a checkbox")
    if action in {"pin", "unpin"}:
        row = row.replace(" " + PIN, "").replace(PIN, "")
        if action == "pin":
            row += " " + PIN
    elif action in {"complete", "reopen"}:
        done = action == "complete"
        if (m.group(2).lower() == "x") == done:
            return task
        body = re.sub(r"\s*<!-- completed:[^>]+ -->", "", todo.DONE_STAMP_RE.sub("", m.group(4)))
        body = todo.SUSPECT_MARK_RE.sub("", body)
        row = m.group(1) + ("x" if done else " ") + m.group(3) + body
        if done:
            row += f" ✅ {dt.date.today().isoformat()} <!-- completed:{now()} -->"
    elif action == "dismiss":
        row = todo.SUSPECT_MARK_RE.sub("", row)
    elif action == "snooze":
        row = todo.SCHEDULED_RE.sub("", row)
        row += " ⏳ " + (dt.date.today() + dt.timedelta(days=todo.SNOOZE_DAYS)).isoformat()
    else:
        raise ValueError("Unknown task action")
    lines[i] = row + "\n"
    replace(path, original, "".join(lines))
    return find(ident)


def start(ident, minutes, request_id):
    if minutes not in {25, 50, 90} or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", request_id):
        raise ValueError("Invalid duration or request ID")
    task = find(ident)
    if task["done"]:
        raise ValueError("Reopen this task before starting Focus")
    logs, errors = read_logs()
    if errors:
        raise ValueError("Fix the session log errors before starting another recorded session")
    if any(s["id"] == request_id for sessions in logs.values() for s in sessions):
        return {"id": ident, "url": ""}  # retry acknowledged, do not launch twice
    change(ident, "pin")
    path = ensure_journal(task["vault"])
    original = path.read_text()
    title = task["text"].replace("|", "&#124;").replace("\n", " ")
    row = f"| {request_id} | {ident} | {title} | {now()} | {minutes} | — | — | 0 | requested | raycast-link |\n"
    # Keep rows in the table even when the user has appended their own notes.
    lines = original.splitlines(keepends=True)
    try:
        pos = next(i + 2 for i, line in enumerate(lines) if line.rstrip("\n") == LOG_HEADER)
    except StopIteration:
        raise ValueError("Focus log table header is missing")
    while pos < len(lines) and lines[pos].startswith("|"):
        pos += 1
    lines.insert(pos, row)
    replace(path, original, "".join(lines))
    goal = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", task["text"])
    return {"id": ident, "url": "raycast://focus/start?" + urlencode({"goal": goal, "duration": minutes * 60}, quote_via=quote)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["snapshot", "create", "move", "complete", "reopen", "pin", "unpin", "dismiss", "snooze", "start", "open", "log", "comment", "comments"])
    parser.add_argument("--vault", default="work")
    parser.add_argument("--title", default="")
    parser.add_argument("--section", default="Inbox")
    parser.add_argument("--id", default="")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--minutes", type=int, default=25)
    parser.add_argument("--pin", action="store_true")
    comment_input = parser.add_mutually_exclusive_group()
    comment_input.add_argument("--text", default="")
    comment_input.add_argument("--text-file", type=Path)
    args = parser.parse_args()
    try:
        with locked():
            result = None
            if args.command == "create":
                result = create(args.vault, args.title, args.pin, args.request_id or None, section=args.section)
            elif args.command == "move":
                result = move(args.id, args.section)
            elif args.command == "comment":
                text = args.text_file.read_text(encoding="utf-8") if args.text_file else args.text
                result = add_comment(args.id, text, args.request_id or None)
            elif args.command == "comments":
                task = find(args.id)
                print(json.dumps({"id": task["id"], "text": task["text"], "vault": task["vault"],
                                  "file": task["comments_file"], "comments": read_comments(task["vault"], task["id"])}))
                return 0
            elif args.command == "start":
                result = start(args.id, args.minutes, args.request_id)
            elif args.command in {"complete", "reopen", "pin", "unpin", "dismiss", "snooze"}:
                result = change(args.id, args.command)
            elif args.command in {"open", "log"}:
                result = find(args.id)
                if args.command == "log":
                    result = dict(result, file=str(ensure_journal(result["vault"])), line=1)
            if args.command == "move":
                try:
                    data = snapshot(read_only=True)
                except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
                    # The atomic move has already committed. A failed refresh
                    # must not report that write as failed or run migrations.
                    data = {"refresh_error": "Task moved, but board refresh failed: " + str(error)}
            else:
                data = snapshot()
            if result:
                data["result"] = result
            # ASCII transport avoids hs.task splitting a UTF-8 codepoint across chunks.
            print(json.dumps(data))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
