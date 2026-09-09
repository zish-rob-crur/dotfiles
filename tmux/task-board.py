#!/usr/bin/env python3
"""Semantic task board for tmux windows.

Every round collects one row per tmux window: the window name, tool icon, the
badge state computed by codex-window-badges-refresh.sh (waiting / running /
done / error), repo and branch, last activity, and this week's todos. One
batched `codex exec` call turns the last lines of every pane into one task
per assistant pane (two short Chinese lines: what is being done, what happens
next, plus what changed since last time) and, in the same call, names windows
that window_namer.py has not named yet.

Calls are throttled so the board stays cheap:
- a window is re-summarised only when its scrollback changed;
- a badge state change (running -> waiting/done/error) gets a fast lane, at
  most once a minute;
- otherwise summaries happen at most every LLM_MIN_GAP seconds, for windows
  that have been quiet for SETTLE_SECONDS, busy windows every RUNNING_REFRESH;
- with nobody at the keyboard for UNATTENDED_AFTER seconds only state changes
  are summarised, at most every UNATTENDED_GAP, so agents working overnight
  still show up but do not burn quota every few minutes.

The result is written to $XDG_CACHE_HOME/tmux-task-board/board.json for
hammerspoon/task_board.lua.

    task-board.py            one round, write board.json
    task-board.py --print    one round, print the board as text
    task-board.py --daemon   poll until the tmux server exits
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_batch  # noqa: E402
import todo_notes  # noqa: E402
import window_namer  # noqa: E402
from daemon_lock import run_daemon  # noqa: E402
from tmux_panes import clean_command, clean_icon, clean_title, git_info, tmux, tool_of, user_idle_seconds  # noqa: E402

STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-task-board"
BOARD_PATH = STATE_DIR / "board.json"
CACHE_PATH = STATE_DIR / "summaries.json"
LOG_PATH = STATE_DIR / "board.log"
INTERVAL = float(os.environ.get("TMUX_TASK_BOARD_INTERVAL", "10"))
LLM_MIN_GAP = 300  # normal cadence between codex calls
FAST_GAP = 60  # cadence when a badge state changed
FAST_WINDOW_COOLDOWN = 300  # one fast-lane call per window per this many seconds (badges can flap)
SETTLE_SECONDS = 60  # a window must be quiet this long before it is re-summarised
RUNNING_REFRESH = 600  # a busy window is re-summarised at most this often
UNATTENDED_AFTER = 1800  # no keyboard/mouse for this long -> unattended mode
UNATTENDED_GAP = 900
SCROLLBACK_LINES = 40
FIELD_SEP = "\x1f"

# Glyphs rendered into @codex-badge by codex-window-badges-refresh.sh.
STATE_GLYPHS = (("×", "error"), ("◆", "waiting"), ("●", "running"), ("󰄬", "done"))
REVIEW_HINTS = ("已完成", "待查看", "待你", "已生成", "待确认")
NEEDS_DECISION = re.compile(r"等你|待你(决定|确认|回复|回答|批准|选择|拍板)")

log = logging.getLogger("task-board")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "next": {"type": "string"},
                                "change": {"type": "string"},
                                "panes": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["summary", "next", "change", "panes"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "name", "tasks"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PROMPT = """下面是几个 tmux 窗口，每个窗口里有一个或多个 pane：可能是 AI 编程助手（codex 或
claude）的会话，也可能是普通 shell、测试或编辑器。附带会话标题和终端最后几十行输出。

每个 codex 或 claude 的 pane 就是一条任务，一一对应，不要把多个助手 pane 合并成一条；
普通 shell、测试或编辑器的 pane 是它旁边助手任务的辅助信息，不单独成条，但可以用来判断
进展。panes 里写这条任务对应的 pane id。每条任务用中文写：

- summary：在推进什么、目前进展到哪一步。不超过 40 个字。
- next：接下来会发生什么，或者正在等什么。如果在等用户批准、回答问题或确认，必须明确
  写出“等你……”；如果已经完成，写“已完成，待查看结果”之类。不超过 30 个字。

每个 codex 或 claude 的 pane 都必须有自己的一条任务，不能遗漏。只根据给出的内容判断，不要臆测。

每个窗口可能带 previous：上一次生成的任务列表。它只用来保持措辞稳定，不是任务清单；
当前 pane 里有而 previous 里没有的任务照样要列出来。对比规则：
- 任务没有实质变化时，summary 和 next 沿用 previous 的原话，change 必须是空字符串；
- 有实质变化时（比如从运行中变为等待批准、从等待变为已完成、出现了新任务、报错），更新 summary
  和 next，并在 change 里用一句话说明变化，例如“从等待批准变为已完成”。不超过 20 个字；
- previous 里的任务已经不存在了就不要再列。

标了 name_needed 的窗口还要起一个窗口名 name：不超过 {max_name} 个字符，只用小写 ascii
字母、数字和连字符，描述这个窗口的任务或主题而不是仓库，且不能和 taken_names 里的重复。
其他窗口 name 留空字符串。

输出严格按 schema。

taken_names: {taken}

窗口：
{windows}
"""


@dataclass
class Pane:
    id: str
    active: bool
    command: str
    path: str
    title: str
    tool: str
    repo: str = ""
    branch: str = ""


@dataclass
class Window:
    id: str
    index: int
    session: str
    name: str
    icon: str
    badge: str
    activity: int
    panes: list[Pane] = field(default_factory=list)

    @property
    def state(self) -> str:
        return classify(self.badge)

    @property
    def assistant_panes(self) -> list[Pane]:
        return [p for p in self.panes if p.tool]

    @property
    def git_pane(self) -> Pane | None:
        """The active pane when it is inside a repository, else the first pane that is."""
        return next((p for p in sorted(self.panes, key=lambda p: not p.active) if p.repo), None)

    def settled(self, now: float, changed_at: float | None = None) -> bool:
        """Quiet for SETTLE_SECONDS. Content-change time is preferred over tmux's
        window_activity, which ticks on every redraw."""
        since = changed_at if changed_at is not None else self.activity
        return self.state != "running" and now - since >= SETTLE_SECONDS


def classify(badge: str) -> str:
    for glyph, state in STATE_GLYPHS:
        if glyph in badge:
            return state
    return "idle"


def group_of(state: str, tasks: list[dict]) -> str:
    """attention: blocked on the user; review: finished, look at the result; working; parked."""
    nexts = [t.get("next", "") for t in tasks]
    if state in ("error", "waiting") or any(NEEDS_DECISION.search(n) for n in nexts):
        return "attention"
    if state == "running":
        return "working"
    if state == "done" or any(any(h in n for h in REVIEW_HINTS) for n in nexts):
        return "review"
    return "parked"


def list_windows() -> list[Window]:
    fields = [
        "#{session_name}", "#{window_id}", "#{window_index}", "#{window_name}", "#{@codex-badge}",
        "#{@pane-window-icon}", "#{window_activity}", "#{pane_id}", "#{pane_active}",
        "#{pane_current_command}", "#{pane_current_path}",
        "#{?#{@codex-session-title},#{@codex-session-title},#{pane_title}}",
    ]
    git_info.cache_clear()  # branches change between rounds
    windows: dict[str, Window] = {}
    for line in tmux(["list-panes", "-a", "-F", FIELD_SEP.join(fields)]).splitlines():
        parts = line.split(FIELD_SEP)
        if len(parts) != len(fields):
            continue
        window = windows.setdefault(parts[1], Window(
            id=parts[1], index=int(parts[2] or 0), session=parts[0], name=parts[3].strip(),
            icon=clean_icon(parts[5]), badge=parts[4], activity=int(parts[6] or 0),
        ))
        repo, branch = git_info(parts[10])
        window.panes.append(Pane(
            id=parts[7], active=parts[8] == "1", command=clean_command(parts[9]), path=parts[10],
            title=clean_title(parts[11], parts[10]), tool=tool_of(parts[9], parts[11]), repo=repo, branch=branch,
        ))
    return list(windows.values())


def capture(pane_id: str) -> str:
    text = tmux(["capture-pane", "-p", "-J", "-S", f"-{SCROLLBACK_LINES}", "-t", pane_id])
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def context_of(window: Window) -> tuple[str, dict]:
    """Return (hash, payload) describing the window's panes for the model. The hash covers only live content."""
    panes = []
    for pane in window.panes:
        panes.append({"id": pane.id, "tool": pane.tool or pane.command, "title": pane.title, "tail": capture(pane.id)})
    payload = {"id": window.id, "window": window.name, "panes": panes}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return digest, payload


def with_previous(payload: dict, cached: dict | None) -> dict:
    """Attach the last summaries so the model can keep wording stable and name what changed."""
    if not cached or not cached.get("tasks"):
        return payload
    previous = [{"summary": t.get("summary", ""), "next": t.get("next", ""), "panes": t.get("panes", [])} for t in cached["tasks"]]
    return {**payload, "previous": previous, "previous_at": time.strftime("%H:%M", time.localtime(cached.get("at", 0)))}


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, value) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    tmp.replace(path)


def ask_codex(payloads: list[dict], taken: list[str] | None = None) -> dict[str, dict]:
    """Returns {window id: {"tasks": [...], "name": "..."}} for the windows in payloads."""
    prompt = PROMPT.format(
        max_name=window_namer.MAX_NAME_LENGTH,
        taken=", ".join(sorted(taken or [])) or "(none)",
        windows=json.dumps(payloads, ensure_ascii=False, indent=1),
    )
    reply = codex_batch.ask(prompt, OUTPUT_SCHEMA, state_dir=STATE_DIR, log=log,
                            label=f"summarising {[p['id'] for p in payloads]}")
    result: dict[str, dict] = {}
    for item in reply.get("items", []):
        if not item.get("id"):
            continue
        tasks = []
        for task in item.get("tasks", []):
            summary = str(task.get("summary", "")).strip()
            change = str(task.get("change", "")).strip()
            if re.search(r"沿用|无变化|没有变化|不变", change):
                change = ""  # the model sometimes narrates "unchanged" instead of leaving it empty
            if summary:
                tasks.append({
                    "summary": summary,
                    "next": str(task.get("next", "")).strip(),
                    "change": change,
                    "panes": [str(p) for p in task.get("panes", [])],
                })
        result[str(item["id"])] = {"tasks": tasks, "name": str(item.get("name", "")).strip()}
    return result


def cover_panes(window: Window, tasks: list[dict]) -> list[dict]:
    """Every assistant pane must show up exactly under its own window.

    In a batched call the model sometimes attributes another window's pane ids
    to this window, so foreign ids are stripped (tasks left without a pane are
    dropped), then a title-only task is added for any assistant pane the model
    skipped, and tasks are ordered the way their panes sit in the window."""
    own = {p.id for p in window.panes}
    kept = []
    for task in tasks:
        panes = [p for p in task.get("panes", []) if p in own]
        if panes:
            kept.append({**task, "panes": panes})
    tasks = kept
    covered = {p for t in tasks for p in t.get("panes", [])}
    for pane in window.assistant_panes:
        if pane.id not in covered and pane.title:
            tasks.append({"summary": pane.title, "next": "", "change": "", "panes": [pane.id]})
    order = {p.id: i for i, p in enumerate(window.panes)}
    return sorted(tasks, key=lambda t: min((order.get(p, 99) for p in t.get("panes", [])), default=99))


def window_todo_count(name: str, todos: list[dict]) -> int:
    """Open todos tagged @<window name> (icon stripped)."""
    bare = re.sub(r"^\S+\s+", "", name) if re.match(r"^[^\w\s]+\s", name) else name
    return sum(1 for t in todos if not t["done"] and bare in t["tags"])


def wants_summary(window: Window, cached: dict | None, digest: str, now: float,
                  allow_llm: bool, allow_fast: bool, attended: bool) -> str:
    """'' (no), 'fast' (badge state changed) or 'full' (regular cadence)."""
    if cached and cached.get("hash") == digest:
        return ""  # nothing on screen changed since the last summary
    if cached and "state" in cached and cached["state"] != window.state:
        if allow_fast and now - cached.get("fast_at", 0) >= FAST_WINDOW_COOLDOWN:
            return "fast"
    if not attended or not allow_llm:
        return ""  # overnight only state changes are worth a call; otherwise wait for the cadence
    if window.settled(now, cached.get("changed_at") if cached else None):
        return "full"
    return "full" if not cached or now - cached.get("at", 0) >= RUNNING_REFRESH else ""


def build_board(windows: list[Window], cache: dict, now: float, allow_llm: bool,
                todos: list[dict] | None = None, naming: dict[str, window_namer.Window] | None = None,
                allow_fast: bool | None = None, attended: bool = True) -> tuple[dict, dict, str]:
    """Return (board, updated cache, kind of codex call: '' / 'fast' / 'full').

    `naming` maps window ids that still need a name to window_namer windows;
    those ride along in the same codex call and are renamed on reply. A failed
    call counts as a full one (so the regular cadence applies) and is reported
    in board["error"]; the windows it covered skip the fast lane for a while."""
    allow_fast = allow_llm if allow_fast is None else allow_fast
    naming = naming or {}
    pending: list[dict] = []
    reasons: dict[str, str] = {}
    contexts: dict[str, str] = {}
    for window in windows:
        digest, payload = context_of(window)
        contexts[window.id] = digest
        cached = cache.get(window.id)
        # Content recency, independent of summaries: tmux's window_activity
        # ticks on every redraw (resize, display wake), the capture hash only
        # changes when something was actually printed.
        entry = cache.setdefault(window.id, {})
        if entry.get("seen_hash") != digest:
            entry["seen_hash"], entry["changed_at"] = digest, int(now)
        if not any(p["tail"] for p in payload["panes"]):
            continue  # nothing on screen yet
        reason = wants_summary(window, cached, digest, now, allow_llm, allow_fast, attended)
        if window.id in naming and allow_llm:
            reason = reason or "full"
        if reason:
            reasons[window.id] = reason
            payload = with_previous(payload, cached)
            if window.id in naming:
                payload = {**payload, "name_needed": True, "naming": window_namer.describe(naming[window.id])}
            pending.append(payload)

    called = error = ""
    if pending:
        called = "full" if "full" in reasons.values() else "fast"
        by_id = {w.id: w for w in windows}
        taken = [w.name.split(" ")[-1].rsplit(":", 1)[-1] for w in windows if w.id not in naming]
        renames: dict[str, dict] = {}
        try:
            replies = ask_codex(pending, taken)
        except codex_batch.CodexError as exc:
            replies, called, error = {}, "full", str(exc)
            for payload in pending:
                cache.setdefault(payload["id"], {})["fast_at"] = int(now)
        for window_id, reply in replies.items():
            window = by_id.get(window_id)
            if not window:
                continue
            tasks = cover_panes(window, reply.get("tasks", []))
            previous = {t.get("summary"): t for t in cache.get(window_id, {}).get("tasks", [])}
            for task in tasks:
                if task.get("change"):
                    task["changed_at"] = int(now)
                else:
                    old = previous.get(task.get("summary"), {})
                    task["change"], task["changed_at"] = old.get("change", ""), old.get("changed_at", 0)
            entry = {**cache.get(window_id, {}), "hash": contexts.get(window_id, ""), "at": int(now), "state": window.state, "tasks": tasks}
            if reasons.get(window_id) == "fast":
                entry["fast_at"] = int(now)
            cache[window_id] = entry
            if window_id in naming and reply.get("name"):
                renames[window_id] = window_namer.finish(naming[window_id], reply["name"])
        if renames:
            window_namer.apply(renames)
            for window_id, entry in renames.items():
                by_id[window_id].name = entry["name"]

    todos = todos or []
    rows = []
    for window in windows:
        cached = cache.get(window.id, {})
        tasks = cached.get("tasks", [])
        rows.append({
            "id": window.id,
            "index": window.index,
            "session": window.session,
            "name": window.name,
            "icon": window.icon,
            "state": window.state,
            "group": group_of(window.state, tasks),
            "repo": window.git_pane.repo if window.git_pane else "",
            "branch": window.git_pane.branch if window.git_pane else "",
            "todo_count": window_todo_count(window.name, todos),
            "activity_at": window.activity,
            "changed_at": cached.get("changed_at", window.activity),
            "tasks": tasks,
            "summary_stale": bool(cached) and cached.get("hash") != contexts.get(window.id, cached.get("hash")),
            "panes": [{"tool": p.tool, "title": p.title} for p in window.assistant_panes],
        })
    rows.sort(key=lambda r: (r["session"], r["index"]))  # tmux order; the group only colours the card
    live = {w.id for w in windows}
    for stale in [k for k in cache if k not in live]:
        cache.pop(stale, None)
    return {
        "generated_at": int(now), "attended": attended, "error": error,
        "week": todo_notes.week_id(), "todos": todos, "windows": rows,
    }, cache, called


class Board:
    def __init__(self) -> None:
        self.last_llm_at = 0.0  # last regular-cadence call
        self.last_fast_at = 0.0  # last fast-lane call; does not delay the regular cadence
        self.failures = 0  # consecutive failed calls; each doubles the regular gap (capped)
        self.error = ""  # reason of the last failed call, shown on the board until a call succeeds
        self.error_since = 0

    def gap(self, attended: bool) -> float:
        return (LLM_MIN_GAP if attended else UNATTENDED_GAP) * 2 ** min(self.failures, 3)

    def round(self, print_only: bool = False) -> dict:
        now = time.time()
        cache = load_cache()
        attended = user_idle_seconds() < UNATTENDED_AFTER
        gap = self.gap(attended)
        allow_llm = now - self.last_llm_at >= gap
        allow_fast = now - self.last_fast_at >= (FAST_GAP if attended else UNATTENDED_GAP)
        todos = [item.as_dict() for item in todo_notes.this_week_items(todo_notes.load_vaults())]

        # Windows window_namer has not named yet ride along in the same codex call.
        to_name, to_release = window_namer.plan(window_namer.list_windows())
        for window in to_release:
            window_namer.release(window)
        naming = {w.id: w for w in to_name}

        board, cache, called = build_board(list_windows(), cache, now, allow_llm, todos, naming, allow_fast, attended)
        if called == "full":
            self.last_llm_at = self.last_fast_at = now
        elif called == "fast":
            self.last_fast_at = now
        if called:
            self.failures = self.failures + 1 if board["error"] else 0
            self.error = board["error"]
            self.error_since = (self.error_since or int(now)) if board["error"] else 0
        board["error"], board["error_since"] = self.error, self.error_since
        board["next_refresh_at"] = int(max(self.last_llm_at + self.gap(attended), now + INTERVAL))
        write_json(CACHE_PATH, cache)
        write_json(BOARD_PATH, board)
        if print_only:
            print(f"attended={attended} called={called} error={board['error']!r}")
            for row in board["windows"]:
                age = int((now - row["activity_at"]) / 60)
                print(f"{row['index']:>3} {row['group']:<10} {age:>4}m  {row['name']}")
                for task in row["tasks"]:
                    change = f"  [{task['change']}]" if task.get("change") else ""
                    print(f"      {task['summary']}  ->  {task['next']}{change}  {task['panes']}")
        return board


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write a semantic task board for tmux windows.")
    parser.add_argument("--daemon", action="store_true", help="poll until the tmux server exits")
    parser.add_argument("--print", action="store_true", dest="print_only", help="print the board after one round")
    args = parser.parse_args(argv[1:])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    window_namer.setup_logging()
    board = Board()
    if args.daemon:
        here = Path(__file__).resolve()
        sources = [here] + [here.with_name(n) for n in ("todo_notes.py", "daemon_lock.py", "tmux_panes.py", "codex_batch.py", "window_namer.py")]
        return run_daemon(STATE_DIR / ".daemon.lock", here, INTERVAL, lambda: board.round() and None, log, sources=sources)
    board.round(print_only=args.print_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
