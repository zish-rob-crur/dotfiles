#!/usr/bin/env python3
"""Weekly todo files in Obsidian vaults, read by the task board.

Vaults come from ~/.config/task-board/config.toml (paths differ per machine):

    [[vaults]]
    name = "work"
    path = "~/Documents/obsidian/<work vault>"
    todo_folder = "Inbox/Todo"

    [[vaults]]
    name = "personal"
    path = "~/Documents/obsidian/<personal vault>"
    todo_folder = "Inbox/Todo"

One Markdown file per ISO week, e.g. Inbox/Todo/2026-W37.md, in Obsidian Tasks
style so the vault's own plugins understand it:

    - [ ] 确认 MR 213 的合并策略 @calle #群:agentic-dev 📅 2026-09-09
    - [ ] ⏫ 跟进客户反馈的限流问题 @calle:goal-fix #会议
    - [x] 给 airudder 重跑导出 @airudder ✅ 2026-09-08

`@tag` links the item to a task-board window (full name or its project prefix),
`#tag` records where it came from. A `## Heading` above items groups them (the
board shows the groups); unfinished items roll into the next week's file under
the same heading, marked "(from W36)".

    todo_notes.py ensure               create this week's files, carrying unfinished items over
    todo_notes.py list                 print this week's items
    todo_notes.py add <vault> <text>   append an item to this week's file
    todo_notes.py toggle <vault> <n>   flip the checkbox on line n of this week's file
    todo_notes.py edit [vault]         open this week's file(s) in Neovide, side by side
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TASK_BOARD_CONFIG", str(Path.home() / ".config/task-board/config.toml")))
ITEM_RE = re.compile(r"^(\s*- \[)( |x|X)(\] )(.*)$")
DONE_STAMP_RE = re.compile(r"\s*✅ \d{4}-\d{2}-\d{2}")
FROM_RE = re.compile(r"\(from (\d{4}-W\d{2})\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


@dataclass
class Vault:
    name: str
    path: Path
    todo_folder: str = "Inbox/Todo"

    def week_file(self, week: str) -> Path:
        return self.path / self.todo_folder / f"{week}.md"


@dataclass
class Item:
    vault: str
    file: str
    line: int  # 1-based line number in the file
    text: str  # description without the checkbox and metadata
    raw: str
    done: bool
    tags: list[str] = field(default_factory=list)  # @window tags
    sources: list[str] = field(default_factory=list)  # #source tags
    due: str = ""
    priority: bool = False
    from_week: str = ""
    section: str = ""  # nearest "## Heading" above the item

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in self.__dict__.items()}


def load_vaults(config_path: Path = CONFIG_PATH) -> list[Vault]:
    try:
        data = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    vaults = []
    for entry in data.get("vaults", []):
        path = Path(os.path.expanduser(entry.get("path", ""))).resolve()
        if entry.get("name") and path.is_dir():
            vaults.append(Vault(entry["name"], path, entry.get("todo_folder", "Inbox/Todo")))
    return vaults


def week_id(day: dt.date | None = None) -> str:
    day = day or dt.date.today()
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_range(week: str) -> tuple[dt.date, dt.date]:
    year, number = week.split("-W")
    monday = dt.date.fromisocalendar(int(year), int(number), 1)
    return monday, monday + dt.timedelta(days=6)


def previous_week(week: str) -> str:
    monday, _ = week_range(week)
    return week_id(monday - dt.timedelta(days=1))


def parse_item(vault: str, file: Path, number: int, line: str) -> Item | None:
    match = ITEM_RE.match(line)
    if not match:
        return None
    body = match.group(4)
    done = match.group(2).lower() == "x"
    tags = re.findall(r"(?<!\S)@(\S+)", body)
    sources = re.findall(r"(?<!\S)#(\S+)", body)
    due_match = re.search(r"📅 (\d{4}-\d{2}-\d{2})", body)
    from_match = FROM_RE.search(body)
    text = body
    for pattern in (r"(?<!\S)[@#]\S+", r"📅 \d{4}-\d{2}-\d{2}", r"✅ \d{4}-\d{2}-\d{2}", r"\(from \d{4}-W\d{2}\)"):
        text = re.sub(pattern, "", text)
    priority = bool(re.search(r"⏫|🔺", body)) or text.strip().startswith("!")
    text = re.sub(r"⏫|🔺|🔼|🔽|⏬", "", text).strip().lstrip("!").strip()
    return Item(
        vault=vault, file=str(file), line=number, text=text, raw=line.rstrip("\n"), done=done,
        tags=tags, sources=sources, due=due_match.group(1) if due_match else "",
        priority=priority, from_week=from_match.group(1) if from_match else "",
    )


def parse_file(vault: str, file: Path) -> list[Item]:
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items = []
    section = ""
    for number, line in enumerate(lines, start=1):
        heading = HEADING_RE.match(line)
        if heading:
            section = "" if len(heading.group(1)) == 1 else heading.group(2)  # the H1 is the week title
            continue
        item = parse_item(vault, file, number, line)
        if item:
            item.section = section
            items.append(item)
    return items


def ensure_week(vault: Vault, week: str | None = None) -> Path:
    """Create this week's file if missing, carrying unfinished items from the previous week."""
    week = week or week_id()
    target = vault.week_file(week)
    if target.exists():
        return target
    monday, sunday = week_range(week)
    carried: list[str] = []
    section = ""
    previous = vault.week_file(previous_week(week))
    for item in parse_file(vault.name, previous):
        if item.done:
            continue
        if item.section != section:
            section = item.section
            if section:
                carried.append(f"\n## {section}")
        origin = item.from_week or previous_week(week)
        raw = FROM_RE.sub("", item.raw).rstrip()
        carried.append(f"{raw} (from {origin})")
    target.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {week}  {monday:%m-%d} ~ {sunday:%m-%d}\n\n"
    target.write_text(header + "\n".join(carried) + ("\n" if carried else ""), encoding="utf-8")
    return target


def this_week_items(vaults: list[Vault]) -> list[Item]:
    items = []
    for vault in vaults:
        items.extend(parse_file(vault.name, ensure_week(vault)))
    return items


def add_item(vault: Vault, text: str) -> Path:
    target = ensure_week(vault)
    line = text.strip()
    if not line.startswith("- ["):
        line = f"- [ ] {line}"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return target


def toggle_item(vault: Vault, line_number: int, today: dt.date | None = None) -> bool:
    """Flip the checkbox on a line; done items get an Obsidian Tasks ✅ stamp. Returns the new state."""
    target = ensure_week(vault)
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    if not 1 <= line_number <= len(lines):
        raise IndexError(f"line {line_number} is outside {target}")
    line = lines[line_number - 1]
    match = ITEM_RE.match(line)
    if not match:
        raise ValueError(f"line {line_number} is not a todo item")
    done = match.group(2).lower() == "x"
    body = DONE_STAMP_RE.sub("", match.group(4).rstrip("\n"))
    if done:
        new = f"{match.group(1)} {match.group(3)}{body}\n"
    else:
        stamp = (today or dt.date.today()).isoformat()
        new = f"{match.group(1)}x{match.group(3)}{body} ✅ {stamp}\n"
    lines[line_number - 1] = new
    target.write_text("".join(lines), encoding="utf-8")
    return not done


def edit_week(vaults: list[Vault]) -> None:
    """Open this week's files in Neovide (vertical splits), or bring the existing Neovide to the front.
    Falls back to nvim in the terminal when Neovide is not installed."""
    files = [str(ensure_week(vault)) for vault in vaults]
    neovide = shutil.which("neovide")
    if not neovide:
        os.execvp("nvim", ["nvim", "-O", *files])
    running = subprocess.run(["pgrep", "-f", f"neovide.*{week_id()}\\.md"], capture_output=True, text=True)
    if running.returncode == 0:
        subprocess.run(["osascript", "-e", 'tell application "Neovide" to activate'], capture_output=True)
        return
    # --size is in physical pixels; 2200x1500 is about 1100x750 points on a Retina display.
    subprocess.Popen([neovide, "--size", "2200x1500", "--", "-O", *files], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def find_vault(vaults: list[Vault], name: str) -> Vault:
    for vault in vaults:
        if vault.name == name:
            return vault
    raise SystemExit(f"todo_notes: no vault named {name!r} in {CONFIG_PATH}")


def main(argv: list[str]) -> int:
    vaults = load_vaults()
    if not vaults:
        print(f"todo_notes: no vaults configured in {CONFIG_PATH}", file=sys.stderr)
        return 1
    command = argv[1] if len(argv) > 1 else "list"
    if command == "ensure":
        for vault in vaults:
            print(ensure_week(vault))
    elif command == "list":
        for item in this_week_items(vaults):
            mark = "x" if item.done else " "
            section = f"[{item.section}] " if item.section else ""
            print(f"[{mark}] {item.vault:<9} {section}{item.text}  {' '.join('@' + t for t in item.tags)}")
    elif command == "add" and len(argv) >= 4:
        print(add_item(find_vault(vaults, argv[2]), " ".join(argv[3:])))
    elif command == "toggle" and len(argv) == 4:
        print("done" if toggle_item(find_vault(vaults, argv[2]), int(argv[3])) else "open")
    elif command == "edit":
        edit_week([find_vault(vaults, argv[2])] if len(argv) > 2 else vaults)
    else:
        print(__doc__, file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
