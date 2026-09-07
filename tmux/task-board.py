#!/usr/bin/env python3
"""Semantic task board for tmux windows.

Every round collects one row per tmux window: the window-namer name, tool
icon, the badge state computed by codex-window-badges-refresh.sh (waiting /
running / done / error), last activity, and the assistant panes inside. For
windows that hold an assistant pane and have settled (not running, no activity
for a few seconds) the last lines of each assistant pane are summarised by one
batched `codex exec` call into two short Chinese lines: what the window is
doing and what happens next. Summaries are cached by scrollback hash so an
unchanged window never costs another call.

The result is written to $XDG_CACHE_HOME/tmux-task-board/board.json for
hammerspoon/task_board.lua to draw on a dedicated screen.

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
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daemon_lock import run_daemon  # noqa: E402

STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tmux-task-board"
BOARD_PATH = STATE_DIR / "board.json"
CACHE_PATH = STATE_DIR / "summaries.json"
LOG_PATH = STATE_DIR / "board.log"
INTERVAL = float(os.environ.get("TMUX_TASK_BOARD_INTERVAL", "10"))
LLM_MIN_GAP = float(os.environ.get("TMUX_TASK_BOARD_LLM_GAP", "90"))  # at most one codex call per this many seconds
SETTLE_SECONDS = 15
RUNNING_REFRESH_SECONDS = 300  # a busy window is re-summarised at most this often
SCROLLBACK_LINES = 40
CODEX_MODEL = os.environ.get("TMUX_TASK_BOARD_MODEL", "gpt-5.4-mini")
CODEX_TIMEOUT = 120
FIELD_SEP = "\x1f"

# Glyphs rendered into @codex-badge by codex-window-badges-refresh.sh.
STATE_GLYPHS = (("×", "error"), ("◆", "waiting"), ("●", "running"), ("󰄬", "done"))
STATE_ORDER = {"error": 0, "waiting": 1, "done": 2, "running": 3, "idle": 4}
# Board groups, derived from the badge state plus what the model says comes next.
GROUP_ORDER = {"attention": 0, "review": 1, "working": 2, "parked": 3}
REVIEW_HINTS = ("已完成", "待查看", "待你", "已生成", "待确认")
NEEDS_DECISION = re.compile(r"等你|待你(决定|确认|回复|回答|批准|选择|拍板)")


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
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "next": {"type": "string"},
                                "panes": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["summary", "next", "panes"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "tasks"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PROMPT = """下面是几个 tmux 窗口，每个窗口里有一个或多个 pane：可能是 AI 编程助手（codex 或
claude）的会话，也可能是普通 shell、测试或编辑器。附带会话标题和终端最后几十行输出。
一个窗口里可能同时推进多个互不相关的任务，也可能几个 pane 在配合做同一件事（比如助手
会话加一个跑测试的 shell）。请先按任务拆分：同一任务的 pane 合并成一条，不相关的任务各写
一条，并在 panes 里列出每条任务对应的 pane id。每条任务用中文写：

- summary：在推进什么、目前进展到哪一步。不超过 40 个字。
- next：接下来会发生什么，或者正在等什么。如果在等用户批准、回答问题或确认，必须明确
  写出“等你……”；如果已经完成，写“已完成，待查看结果”之类。不超过 30 个字。

空闲的 shell 提示符、没有实质内容的 pane 不要单独成条。只根据给出的内容判断，不要臆测。
输出严格按 schema。

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

    def settled(self, now: float) -> bool:
        return self.state != "running" and now - self.activity >= SETTLE_SECONDS

    def wants_summary(self, now: float, cached: dict | None) -> bool:
        """Settled windows refresh right away; busy ones only every RUNNING_REFRESH_SECONDS."""
        if self.settled(now):
            return True
        return not cached or now - cached.get("at", 0) >= RUNNING_REFRESH_SECONDS


def tmux(args: list[str]) -> str:
    return subprocess.run(["tmux", *args], capture_output=True, text=True).stdout


def classify(badge: str) -> str:
    for glyph, state in STATE_GLYPHS:
        if glyph in badge:
            return state
    return "idle"


def tool_of(command: str, title: str) -> str:
    if command.startswith("codex"):
        return "codex"
    if command.startswith("claude") or (re.fullmatch(r"\d+\.\d+\.\d+", command) and title.startswith(("✳", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"))):
        return "claude"
    return ""


def clean_title(title: str) -> str:
    return re.sub(r"^[\s✳⠁-⣿]+", "", title).strip()


@lru_cache(maxsize=None)
def git_info(path: str) -> tuple[str, str]:
    """(repo name, branch) for a path; empty strings outside a repository."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "", ""
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        return "", ""
    branch = "" if lines[0] == "HEAD" else lines[0]
    repo = os.path.basename(lines[1]).split(".", 1)[0]  # worktree dirs look like repo.branch
    return repo, branch


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
            icon=re.split(r"[×│/\d]", parts[5], maxsplit=1)[0].strip(), badge=parts[4],
            activity=int(parts[6] or 0),
        ))
        title = clean_title(parts[11])
        repo, branch = git_info(parts[10])
        window.panes.append(Pane(
            id=parts[7], active=parts[8] == "1", command=parts[9], path=parts[10], title=title,
            tool=tool_of(parts[9], parts[11]), repo=repo, branch=branch,
        ))
    return list(windows.values())


def capture(pane_id: str) -> str:
    text = tmux(["capture-pane", "-p", "-J", "-S", f"-{SCROLLBACK_LINES}", "-t", pane_id])
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def context_of(window: Window) -> tuple[str, dict]:
    """Return (hash, payload) describing the window's assistant panes for the model."""
    panes = []
    for pane in window.panes:
        panes.append({"id": pane.id, "tool": pane.tool or pane.command, "title": pane.title, "tail": capture(pane.id)})
    payload = {"id": window.id, "window": window.name, "panes": panes}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return digest, payload


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


def ask_codex(payloads: list[dict]) -> dict[str, dict]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=STATE_DIR) as tmp:
        schema = Path(tmp) / "schema.json"
        schema.write_text(json.dumps(OUTPUT_SCHEMA))
        output = Path(tmp) / "out.json"
        stderr = Path(tmp) / "stderr.txt"
        prompt = PROMPT.format(windows=json.dumps(payloads, ensure_ascii=False, indent=1))
        log.info("codex: summarising %s", [p["id"] for p in payloads])
        started = time.monotonic()
        try:
            subprocess.run(
                [
                    "codex", "exec",
                    "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
                    "-s", "read-only", "--color", "never",
                    "-C", tmp,
                    "-m", CODEX_MODEL, "-c", "model_reasoning_effort=low",
                    "--output-schema", str(schema), "-o", str(output),
                    prompt,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr.open("w"),
                timeout=CODEX_TIMEOUT,
            )
        except FileNotFoundError:
            log.error("codex CLI not found on PATH")
            return {}
        except subprocess.TimeoutExpired:
            log.error("codex exec timed out after %ss\n%s", CODEX_TIMEOUT, stderr.read_text(errors="replace")[-800:])
            return {}
        if not output.exists():
            log.error("codex returned nothing\n%s", stderr.read_text(errors="replace")[-800:])
            return {}
        raw = output.read_text()
        log.info("codex replied in %.1fs: %s", time.monotonic() - started, raw.strip())
        try:
            items = json.loads(raw).get("items", [])
        except (json.JSONDecodeError, AttributeError):
            log.error("codex reply is not the expected JSON")
            return {}
    result: dict[str, dict] = {}
    for item in items:
        if not item.get("id"):
            continue
        tasks = []
        for task in item.get("tasks", []):
            summary = str(task.get("summary", "")).strip()
            if summary:
                tasks.append({
                    "summary": summary,
                    "next": str(task.get("next", "")).strip(),
                    "panes": [str(p) for p in task.get("panes", [])],
                })
        result[str(item["id"])] = {"tasks": tasks}
    return result


def build_board(windows: list[Window], cache: dict, now: float, allow_llm: bool) -> tuple[dict, dict, bool]:
    """Return (board, updated cache, whether codex was called)."""
    pending: list[dict] = []
    contexts: dict[str, str] = {}
    for window in windows:
        digest, payload = context_of(window)
        contexts[window.id] = digest
        cached = cache.get(window.id)
        if cached and cached.get("hash") == digest:
            continue
        if not any(p["tail"] for p in payload["panes"]):
            continue  # nothing on screen yet
        if allow_llm and window.wants_summary(now, cached):
            pending.append(payload)

    called = False
    if pending:
        called = True
        for window_id, summary in ask_codex(pending).items():
            cache[window_id] = {"hash": contexts.get(window_id, ""), "at": int(now), **summary}

    rows = []
    for window in windows:
        cached = cache.get(window.id, {})
        state = window.state
        tasks = cached.get("tasks", [])
        rows.append({
            "id": window.id,
            "index": window.index,
            "session": window.session,
            "name": window.name,
            "icon": window.icon,
            "state": state,
            "group": group_of(state, tasks),
            "repo": window.git_pane.repo if window.git_pane else "",
            "branch": window.git_pane.branch if window.git_pane else "",
            "activity_at": window.activity,
            "tasks": tasks,
            "summary_stale": bool(cached) and cached.get("hash") != contexts.get(window.id, cached.get("hash")),
            "panes": [{"tool": p.tool, "title": p.title} for p in window.assistant_panes],
        })
    # Stable within a state group: session then window index, so cards only
    # move when their state changes, not on every keystroke.
    rows.sort(key=lambda r: (GROUP_ORDER[r["group"]], STATE_ORDER[r["state"]], r["session"], r["index"]))
    live = {w.id for w in windows}
    for stale in [k for k in cache if k not in live]:
        cache.pop(stale, None)
    return {"generated_at": int(now), "windows": rows}, cache, called


class Board:
    def __init__(self) -> None:
        self.last_llm_at = 0.0

    def round(self, print_only: bool = False) -> dict:
        now = time.time()
        cache = load_cache()
        allow_llm = now - self.last_llm_at >= LLM_MIN_GAP
        board, cache, called = build_board(list_windows(), cache, now, allow_llm)
        if called:
            self.last_llm_at = now
        write_json(CACHE_PATH, cache)
        write_json(BOARD_PATH, board)
        if print_only:
            for row in board["windows"]:
                age = int((now - row["activity_at"]) / 60)
                print(f"{row['index']:>3} {row['state']:<8} {age:>4}m  {row['name']}")
                for task in row["tasks"]:
                    print(f"      {task['summary']}  ->  {task['next']}  {task['panes']}")
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
    board = Board()
    if args.daemon:
        return run_daemon(STATE_DIR / ".daemon.lock", Path(__file__).resolve(), INTERVAL, lambda: board.round() and None, log)
    board.round(print_only=args.print_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
