---
name: todo
description: Add or list weekly Markdown tasks, and read or append comments on existing Task Board tasks. Use only when the user explicitly invokes /todo; never add todo items on your own initiative.
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
- `/todo comment <task title or ID> -- <comment>` — append a comment to an existing task
- `/todo comments <task title or ID>` — read that task's comments

For `comment` or `comments`, use **Task comments** below instead of adding a task.

## Add or list tasks

1. Pick the vault: `personal` when the text starts with `personal` or `个人`,
   otherwise `work`. Strip that keyword from the text.
2. Keep the user's wording. Preserve any `@窗口名`, `#来源`, `📅 YYYY-MM-DD`
   or `⏫` the user typed.
3. If the user gave no `@tag` and the shell is inside tmux, attach the current
   window so the board can link the item to its card:
   `tmux display -p '#W'`, then drop a leading icon glyph and the space after
   it (names look like `✳ proj:goal-fix`; use `proj:goal-fix`).
   Skip the tag when the item is clearly unrelated to the current window.
4. Run `todo-notes add <vault> "<text>"` and reply with the line as written and
   the file path it went to. For `list`, run `todo-notes list` and show the output.

## Task comments

Use `focus-notes` (installed with Task Board) for comments. Both the board and
this CLI use the same task IDs and Markdown files. A comment never creates or
completes a task, changes its pin, or starts a Focus timer.

1. Read `focus-notes snapshot` and resolve the requested task by stable `id`,
   title (`text`), and vault. The snapshot includes completed tasks from this
   week. Filter the JSON locally; show only relevant candidates, not the whole
   task/session dump. If several tasks match, ask the user to choose; never
   guess by line number or silently create a new task.
2. To read, run `focus-notes comments --id <id>`. It returns the task identity,
   comment file path and comments with `id`, `created` (timezone included), and
   `text`. Summarize the returned comments or quote them as requested.
3. To append, preserve the user's text and line breaks. Write the exact text
   to a temporary UTF-8 file, then invoke with an argument array (for example
   Python `subprocess.run`):
   `focus-notes comment --id <id> --text-file <path> --request-id <uuid>`.
   Read `result.comment` and `result.file` from the JSON response. Do not
   interpolate comment text into a shell command. A comment must contain
   1–4000 characters after trimming surrounding whitespace.
4. Reuse the same request ID for a retry of the same write, so it cannot append
   twice. On a conflict or malformed Markdown error, keep the text and report
   the error; do not overwrite the file or claim the comment was saved.
5. Reply with the target task, saved comment and file path. Delete only the
   temporary input file you created.

Comments live under each vault's configured `todo_folder`, in
`comments/<task-id>.md`. They remain attached across task renames and weekly
rollover. Files contain a `task_id` property, timestamped comment headings with
hidden comment IDs, and blockquoted bodies. Preserve these IDs and timestamps
when editing Markdown; quote every body line (including blank lines) so task
checkboxes and headings inside a comment remain ordinary comment content.
Use the CLI to append instead of hand-generating this format. This skill does
not post comments to Feishu, GitHub, or any other service.
