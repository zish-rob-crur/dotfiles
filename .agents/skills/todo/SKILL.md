---
name: todo
description: Record one item into the user's weekly todo file (Obsidian, via `todo-notes add`) or list this week's items. Use only when the user explicitly invokes /todo; never add todo items on your own initiative.
---

# Todo

The user keeps one Markdown todo file per ISO week in each Obsidian vault
(`work`, `personal`), rendered on the tmux task board. `todo-notes` (in PATH)
knows the vault paths from `~/.config/task-board/config.toml`.

This skill runs only on an explicit `/todo …` from the user. Never record
decisions, follow-ups or "things to review" without being asked.

## Usage

- `/todo 确认 MR 213 的合并策略` — add to the `work` vault
- `/todo personal 整理读书笔记` — add to the `personal` vault (also `个人`)
- `/todo list` — print this week's items

## Steps

1. Pick the vault: `personal` when the text starts with `personal` or `个人`,
   otherwise `work`. Strip that keyword from the text.
2. Keep the user's wording. Preserve any `@窗口名`, `#来源`, `📅 YYYY-MM-DD`
   or `⏫` the user typed.
3. If the user gave no `@tag` and the shell is inside tmux, attach the current
   window so the board can link the item to its card:
   `tmux display -p '#W'`, then drop a leading icon glyph and the space after
   it (names look like `✳ calle:goal-fix`; use `calle:goal-fix`).
   Skip the tag when the item is clearly unrelated to the current window.
4. Run `todo-notes add <vault> "<text>"` and reply with the line as written and
   the file path it went to. For `list`, run `todo-notes list` and show the output.
