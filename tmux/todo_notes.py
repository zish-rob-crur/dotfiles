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

    - [ ] 确认 MR 213 的合并策略 @proj #群:agentic-dev 📅 2026-09-09
    - [ ] ⏫ 跟进客户反馈的限流问题 @proj:goal-fix #会议
    - [x] 重跑导出 @notes ✅ 2026-09-08

`@tag` links the item to a task-board window (full name or its project prefix),
`#tag` records where it came from. A `## Heading` above items groups them (the
board shows the groups); unfinished items roll into the next week's file under
the same heading, marked "(from W36)".

Links are written reference-style so an item stays one short line; the URLs sit
in a block at the end of the file, and a Feishu message id is kept as the link
title, which is what deduplication matches:

    - [ ] 排查丢失问题 @window #来源 [飞书原消息][fs-9c1e70]

    [fs-9c1e70]: https://applink.feishu.cn/client/chat/open?... "feishu:om_<message id>"

`add` and `tidy` convert inline links (`[text](url) <!-- feishu:<id> -->`) into
that form, so writers may keep producing inline links.

    todo_notes.py ensure               create this week's files, carrying unfinished items over
    todo_notes.py nvim-args            nvim arguments opening one tab per vault (used by Hammerspoon)
    todo_notes.py tidy [vault]         move inline links of this week's file(s) into the reference block
    todo_notes.py list                 print this week's items
    todo_notes.py add <vault> <text>   append an item to this week's file
    todo_notes.py toggle <vault> <n>   flip the checkbox on line n of this week's file
    todo_notes.py edit [vault]         open this week's file(s) in Neovide, one tab per vault
"""

from __future__ import annotations

import datetime as dt
import hashlib
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
DEF_RE = re.compile(r'^\[([^\]]+)\]:\s*([a-z][\w+.-]*://\S+)(?:\s+"([^"]*)")?\s*$')  # [label]: url "title"; urls only, so "[注意]: 明天" stays text
INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
FEISHU_ID_RE = re.compile(r"\s*<!--\s*feishu:([\w-]+)\s*-->")
FEISHU_MESSAGE_URL = "applink.feishu.cn/client/"
REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[([^\]]+)\]")


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


@dataclass
class LinkDef:
    url: str
    title: str = ""

    def line(self, label: str) -> str:
        return f'[{label}]: {self.url}' + (f' "{self.title}"' if self.title else "")


def split_link_defs(lines: list[str]) -> tuple[list[str], dict[str, LinkDef]]:
    """(lines without link definitions, label -> definition)."""
    body, defs = [], {}
    for line in lines:
        match = DEF_RE.match(line)
        if match and not ITEM_RE.match(line):
            defs[match.group(1)] = LinkDef(match.group(2), match.group(3) or "")
        else:
            body.append(line)
    return body, defs


def _label_for(url: str, feishu_id: str, defs: dict[str, LinkDef]) -> str:
    for label, known in defs.items():
        if known.url == url:
            return label
    base = f"fs-{feishu_id}" if feishu_id else "l-" + hashlib.sha1(url.encode()).hexdigest()
    prefix = base[:3] if feishu_id else base[:2]
    tail = base[len(prefix):]
    for size in range(6, len(tail) + 1):  # short labels hide little text when rendered; grow on collision
        label = prefix + (tail[-size:] if feishu_id else tail[:size])
        if label not in defs:
            return label
    return base


def tidy_lines(lines: list[str]) -> list[str]:
    """Inline links -> reference links, with one definition block at the end holding
    the definitions still in use, in order of first use. Idempotent."""
    body, defs = split_link_defs(lines)

    def convert(line: str) -> str:
        links = list(INLINE_LINK_RE.finditer(line))
        if not links:
            return line
        # A hidden id belongs to the Feishu message link before it, which is not
        # always the link right before it ("[消息](…) · [文档](…) <!-- feishu:id -->").
        ids: dict[int, str] = {}
        consumed = []
        for comment in FEISHU_ID_RE.finditer(line):
            before = [n for n, link in enumerate(links) if link.end() <= comment.start() and n not in ids]
            messages = [n for n in before if FEISHU_MESSAGE_URL in links[n].group(2)]
            target = (messages or before or [None])[-1]
            if target is not None:
                ids[target] = comment.group(1)
                consumed.append(comment.span())
        out, pos = [], 0
        spans = sorted([(link.start(), link.end(), n) for n, link in enumerate(links)] + [(a, b, None) for a, b in consumed])
        for start, end, n in spans:
            out.append(line[pos:start])
            if n is not None:
                text, url, feishu_id = links[n].group(1), links[n].group(2), ids.get(n, "")
                label = _label_for(url, feishu_id, defs)
                known = defs.setdefault(label, LinkDef(url))
                if feishu_id and not known.title:
                    known.title = f"feishu:{feishu_id}"
                out.append(f"[{text}][{label}]")
            pos = end
        return "".join(out) + line[pos:]

    body = [convert(line) for line in body]
    used: list[str] = []
    for line in body:
        for label in REF_LINK_RE.findall(line):
            if label[1] in defs and label[1] not in used:
                used.append(label[1])
    while body and not body[-1].strip():
        body.pop()
    if not used:
        return body
    return body + [""] + [defs[label].line(label) for label in used]


def resolve_links(text: str, defs: dict[str, LinkDef]) -> str:
    """[text][label] -> [text](url) for renderers that only know inline links."""
    return REF_LINK_RE.sub(lambda m: f"[{m.group(1)}]({defs[m.group(2)].url})" if m.group(2) in defs else m.group(0), text)


def parse_item(vault: str, file: Path, number: int, line: str, defs: dict[str, LinkDef] | None = None) -> Item | None:
    match = ITEM_RE.match(line)
    if not match:
        return None
    body = resolve_links(match.group(4), defs or {})
    done = match.group(2).lower() == "x"
    tags = re.findall(r"(?<!\S)@(\S+)", body)
    sources = re.findall(r"(?<!\S)#(\S+)", body)
    due_match = re.search(r"📅 (\d{4}-\d{2}-\d{2})", body)
    from_match = FROM_RE.search(body)
    text = re.sub(r"<!--.*?-->", "", body)  # hidden ids such as <!-- feishu:om_… -->; the links stay for the board to render
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
    _, defs = split_link_defs(lines)
    for number, line in enumerate(lines, start=1):
        heading = HEADING_RE.match(line)
        if heading:
            section = "" if len(heading.group(1)) == 1 else heading.group(2)  # the H1 is the week title
            continue
        item = parse_item(vault, file, number, line, defs)
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
    try:
        _, defs = split_link_defs(previous.read_text(encoding="utf-8").splitlines())
    except OSError:
        defs = {}
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
    header = [f"# {week}  {monday:%m-%d} ~ {sunday:%m-%d}", ""]
    carried += [definition.line(label) for label, definition in defs.items()]  # tidy keeps only those still used
    lines = tidy_lines(header + "\n".join(carried).split("\n")) if carried else header
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def tidy_week(vault: Vault) -> Path:
    target = ensure_week(vault)
    lines = target.read_text(encoding="utf-8").splitlines()
    tidied = tidy_lines(lines)
    if tidied != lines:
        target.write_text("\n".join(tidied) + "\n", encoding="utf-8")
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
    body, defs = split_link_defs(target.read_text(encoding="utf-8").splitlines())
    while body and not body[-1].strip():
        body.pop()
    lines = tidy_lines(body + [line] + [definition.line(label) for label, definition in defs.items()])
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
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


def _vim_escape(path: str) -> str:
    """fnameescape() for a path used inside an Ex command."""
    return re.sub(r'([ \\%#|"])', r"\\\1", path)


def nvim_args(vaults: list[Vault]) -> list[str]:
    """nvim arguments opening this week's file of each vault in its own tab page,
    with the tab's working directory at the vault root. Tabs keep the vaults
    apart in one editor: AstroNvim scopes the buffer list per tab, and a Codex
    side panel opened in a tab stays in that tab. The first file is a plain
    argument and the rest use :tabnew after startup (with `nvim -p`, AstroNvim
    would list every buffer under the first tab)."""
    files = [str(ensure_week(vault)) for vault in vaults]
    args = [files[0], "-c", f"tcd {_vim_escape(str(vaults[0].path))}"]
    for vault, file in zip(vaults[1:], files[1:]):
        args += ["-c", f"tabnew {_vim_escape(file)} | tcd {_vim_escape(str(vault.path))}"]
    if len(vaults) > 1:
        args += ["-c", "tabfirst"]
    return args


def edit_week(vaults: list[Vault]) -> None:
    """Open this week's files in Neovide (one tab per vault), or bring the existing Neovide to the front.
    Falls back to nvim in the terminal when Neovide is not installed."""
    args = nvim_args(vaults)
    neovide = shutil.which("neovide")
    if not neovide:
        os.execvp("nvim", ["nvim", *args])
    running = subprocess.run(["pgrep", "-f", f"neovide.*{week_id()}\\.md"], capture_output=True, text=True)
    if running.returncode == 0:
        subprocess.run(["osascript", "-e", 'tell application "Neovide" to activate'], capture_output=True)
        return
    # --size is in physical pixels; 2200x1500 is about 1100x750 points on a Retina display.
    subprocess.Popen([neovide, "--size", "2200x1500", "--", *args], start_new_session=True,
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
    elif command == "tidy":
        for vault in [find_vault(vaults, argv[2])] if len(argv) > 2 else vaults:
            print(tidy_week(vault))
    elif command == "nvim-args":  # one argument per line, for the Hammerspoon launcher
        print("\n".join(nvim_args(vaults)))
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
