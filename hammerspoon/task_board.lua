-- Task board: shows ~/.cache/tmux-task-board/board.json (written by
-- tmux/task-board.py) in a draggable, resizable standard window rendered as
-- HTML. Clicking a card selects that tmux window and brings Ghostty forward.
--
-- The Swift helper owns a real NSWindow + WKWebView: drag it to any screen, resize
-- it, close it with the title-bar button and bring it back with ⌘⇧B. Its
-- frame is remembered across reloads. ⌘⇧D opens this week's todo files in
-- Neovide (so does clicking the "This week" heading).

local M = {}

local HOME = assert(os.getenv("HOME"), "HOME is required")
local STATE_DIR = HOME .. "/.cache/tmux-task-board"
local BOARD_PATH = STATE_DIR .. "/board.json"
local FRAME_SETTING = "taskBoardFrame"
local GAP_SETTING = "taskBoardWeekGap"  -- px between the cards and This week; nil = pinned to the bottom
local TMUX_COLLAPSED_SETTING = "taskBoardTmuxCollapsed"
local FOCUS_SETTING = "taskBoardFocus"  -- duration only; task data lives in weekly Markdown
local FOCUS_TOOL = HOME .. "/.local/bin/focus-notes"
local TODO_TOOL = HOME .. "/.local/bin/todo-notes"
local WINDOW_TOOL = HOME .. "/.local/share/task-board/Task Board.app/Contents/MacOS/task-board-window"
-- Default placement when no frame is remembered: right half of the first
-- matching screen, else any non-primary screen.
local SCREEN_NAMES = { "T270LG" }
local TOGGLE_HOTKEY = { { "cmd", "shift" }, "b" }
local EDIT_HOTKEY = { { "cmd", "shift" }, "d" }  -- open this week's todo files in Neovide

local neovide = require("neovide_window")
local state = { task = nil, watcher = nil, board = nil, queue = {}, pending = false, output = "" }

local PAGE = [[
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Task Board</title>
<style>
  @font-face { font-family: "BoardIcons"; src: local("CodexStatusSymbols-Regular"); unicode-range: U+E00B-E00D; }
  :root {
    --bg: #1e2326; --card: #272e33; --card-hover: #2e373c; --text: #d3c6aa; --muted: #9aa69d; --tag: #a7b0a8;
    --title: #dbbc7f; --attention: #dbbc7f; --error: #e67e80; --review: #a7c080; --working: #7fbbb3; --parked: #4f585e;
    --change: #e69875; --claude: #e69875; --codex: #83c092; --line: #384249;
    --p0: #e67e80; --p1: #e69875; --p2: #dbbc7f; --p3: #a7c080; --p4: #83c092; --p5: #7fbbb3; --p6: #d699b6; --p7: #a7b0a8;
  }
  * { box-sizing: border-box; }
  html { color-scheme: dark; }
  html, body { margin: 0; height: 100%; overflow: hidden; background: var(--bg); color: var(--text);
    font: 13px/1.6 "BoardIcons", "Maple Mono NF CN", "JetBrainsMono Nerd Font", "PingFang SC", monospace; }
  body { display: flex; flex-direction: column; }
  [hidden] { display: none !important; }
  ::selection { background: #455448; color: #f1e8d5; }
  ::-webkit-scrollbar { width: 7px; height: 7px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #4f585e; border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: #65726a; }
  button, select, input, textarea { font: inherit; color: inherit; }
  button, select { background: transparent; border: 1px solid var(--line); border-radius: 5px; padding: 5px 9px; font-size: 12px; }
  button { cursor: pointer; }
  button:hover, select:hover { background: var(--card-hover); }
  button:disabled { opacity: .5; cursor: default; }
  :focus-visible { outline: 2px solid var(--title); outline-offset: 2px; }
  input, textarea { caret-color: var(--review); }
  input::placeholder, textarea::placeholder { color: var(--muted); }
  input[type=checkbox] { width: 15px; height: 15px; margin: 4px 0 0; accent-color: var(--review); cursor: pointer; flex: none; }
  .primary { background: #a7c080; border-color: #a7c080; color: #202820; font-weight: 600; }
  .primary:hover { background: #bbd098; }
  .quiet { border-color: transparent; color: var(--muted); }
  .board-top { flex: none; }
  .toolbar { display: flex; align-items: center; gap: 16px; padding: 14px 20px; border-bottom: 1px solid var(--line); }
  h1 { font-size: 16px; font-weight: 600; white-space: nowrap; margin: 0; }
  #board-week { font-size: 11px; color: var(--muted); white-space: nowrap; }
  .task-search { display: flex; align-items: center; gap: 4px; margin-left: auto; width: min(38vw, 420px); min-width: 0; }
  #task-search { width: 100%; min-width: 0; background: var(--card); border: 1px solid var(--line); border-radius: 5px; padding: 6px 10px; font-size: 12px; }
  .focus { display: flex; align-items: flex-start; gap: 18px; padding: 10px 20px; border-bottom: 1px solid var(--line); }
  .focus h2 { font-size: 12px; color: var(--review); margin: 6px 0 0; font-weight: 600; }
  #focus-rows { flex: 1; min-width: 0; max-height: 18vh; overflow-y: auto; }
  .focus-row { display: flex; align-items: baseline; gap: 10px; min-width: 0; padding: 3px 0; }
  .focus-row .task-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; flex: 1; }
  .focus-project { color: var(--muted); font-size: 11px; flex: none; max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .focus-row button[data-action=start] { border: 0; color: var(--review); padding: 3px 6px; white-space: nowrap; }
  #focus-minutes { margin-top: 2px; flex: none; }
  .focus-help, .focus-status { color: var(--muted); font-size: 11px; margin: 4px 0; }
  .focus-status { margin: 0; padding: 0 20px; max-height: 60px; overflow: auto; }
  .focus-status:empty { display: none; }
  .focus-status.error, .comment-status.error { color: var(--error); }
  #focus-form { padding: 12px 20px; border-bottom: 1px solid var(--line); }
  .new-task-fields { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .new-task-fields label { display: flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%; color: var(--muted); font-size: 11px; }
  .new-task-fields select { min-width: 0; max-width: 220px; color: var(--text); }
  #focus-task { flex: 1 1 220px; min-width: 0; background: var(--card); border: 1px solid var(--line); border-radius: 5px; padding: 6px 9px; }
  main { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; padding: 0 20px; }
  .week-block { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; }
  .week-toolbar { display: flex; align-items: center; gap: 12px; padding: 12px 0 8px; flex: none; }
  .week-toolbar #week { font-weight: 600; padding: 0; border: 0; }
  #task-search-status { color: var(--muted); font-size: 11px; }
  [data-show-completed] { margin-left: auto; white-space: nowrap; }
  .todos { display: flex; flex: 1; align-items: stretch; min-height: 0; gap: 24px; overflow-x: auto; overflow-y: hidden; padding-bottom: 8px; }
  .todo-group { flex: 0 0 min(100%, 360px); min-width: 0; overflow-y: auto; overscroll-behavior: contain; padding-right: 7px; }
  .todo-group h3 { position: sticky; top: 0; background: var(--bg); z-index: 1; font-size: 13px; line-height: 1.5;
    display: flex; align-items: baseline; gap: 8px; padding: 8px 2px 12px; margin: 0; border-bottom: 1px solid var(--line); }
  .todo-group .vault { color: var(--muted); font-size: 10px; font-weight: 400; }
  .group-count { margin-left: auto; font-size: 11px; color: var(--muted); font-weight: 400; }
  .todo { padding: 14px 2px; border-bottom: 1px solid #30393e; }
  .todo:hover, .todo.selected { background: #252d30; }
  .task-line { display: flex; align-items: flex-start; gap: 10px; }
  .task-title { min-width: 0; text-align: left; padding: 0; border: 0; border-radius: 2px; color: var(--text); line-height: 1.65; font-size: 13px; overflow-wrap: anywhere; }
  .task-title:hover { background: transparent; color: #eee4ce; }
  .todo.done .task-title { color: var(--muted); text-decoration: line-through; }
  .row-meta { display: flex; align-items: baseline; flex-wrap: wrap; gap: 2px 10px; margin: 6px 0 0 25px; color: var(--muted); font-size: 10px; }
  .row-meta button { padding: 0; font-size: inherit; color: inherit; border: 0; }
  .row-meta button:hover { color: var(--text); text-decoration: underline; }
  .row-meta .overdue { color: var(--error); }
  .row-meta .due-today { color: var(--attention); }
  .row-meta .pinned { color: var(--review); }
  .row-meta .priority { color: var(--attention); }
  .empty-state { align-self: flex-start; color: var(--muted); padding: 20px 0; }
  .tmux-block { flex: none; border-top: 1px solid var(--line); }
  .tmux-toolbar { display: flex; align-items: center; gap: 14px; padding: 8px 0; }
  .tmux-toolbar h2 { font-size: 11px; font-weight: 500; margin: 0; white-space: nowrap; }
  .tmux-counts { display: flex; flex-wrap: wrap; gap: 10px; font-size: 10px; color: var(--muted); }
  .tmux-toolbar button { margin-left: auto; }
  #updated { color: var(--muted); font-size: 10px; }
  #tmux-cards { max-height: 28vh; overflow: auto; padding-bottom: 6px; }
  #tmux-cards h2 { font-size: 11px; color: var(--muted); }
  .pill { font-size: 10px; padding: 0 6px; border-radius: 8px; background: #3a444a; color: var(--text); letter-spacing: .04em; }
  .pill.attention { background: #4a4330; color: var(--attention); } .pill.error { background: #4a3336; color: var(--error); }
  .pill.review { background: #34412f; color: var(--review); } .pill.working { background: #2f4145; color: var(--working); }
  .pill.parked { color: var(--muted); }
  /* Masonry-style columns: cards keep their natural height; a second column only appears past ~1060px. */
  .group { column-width: 520px; column-gap: 8px; }
  .card { background: var(--card); border-left: 1px solid var(--parked); border-radius: 5px; padding: 5px 9px; cursor: pointer;
    break-inside: avoid; margin-bottom: 5px; }
  .card:hover { background: var(--card-hover); }
  /* The meta line never wraps: the branch is the only part allowed to shrink, with an ellipsis. */
  .meta { display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: var(--tag); margin-bottom: 1px; white-space: nowrap; }
  .meta .idx { color: var(--muted); }
  .meta .git { color: var(--muted); flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .meta .git::before { content: "·"; margin-right: 6px; }
  .meta .age { margin-left: auto; color: var(--muted); padding-left: 8px; }
  .task + .task { margin-top: 3px; padding-top: 3px; border-top: 1px dashed #3a444a; }
  .summary { color: var(--text); }
  .next { color: var(--muted); }
  .next::before { content: "→ "; }
  /* One line per task: "summary → next"; the next step wraps under it only when the card is narrow. */
  .task { display: flex; gap: 8px; flex-wrap: wrap; }
  .next { white-space: nowrap; }
  .change { color: var(--change); white-space: nowrap; }
  .change::before { content: "Δ "; }
  .card.attention .next { color: var(--attention); } .card.error .next { color: var(--error); }
  .card.review .next { color: var(--review); } .card.working .next { color: var(--working); }
  .card.attention { border-left-color: var(--attention); background: #2c2e2b; } .card.error { border-left-color: var(--error); background: #2f2a2b; }
  .card.review { border-left-color: var(--review); background: #272f2b; } .card.working { border-left-color: var(--working); background: #262f31; }
  .card.attention:hover { background: #33352f; } .card.review:hover { background: #2d372f; } .card.working:hover { background: #2b3739; }
  .icon.claude { color: var(--claude); } .icon.codex { color: var(--codex); }
  .name { color: var(--text); }
  .repo-0 .git { color: var(--p0); } .repo-1 .git { color: var(--p1); } .repo-2 .git { color: var(--p2); } .repo-3 .git { color: var(--p3); }
  .repo-4 .git { color: var(--p4); } .repo-5 .git { color: var(--p5); } .repo-6 .git { color: var(--p6); } .repo-7 .git { color: var(--p7); }
  .git .branch { opacity: .75; }
  .parked .summary { color: var(--muted); font-size: 13px; } .parked .next { display: none; }
  .stale .summary, .stale .next { opacity: .6; }
  .error-bar { margin: 0 0 12px; padding: 6px 12px; border-radius: 6px; background: #4a3336; color: var(--error); font-size: 12px; }
  /* Recency: the longer a window has been silent, the dimmer its card. "Needs you" never dims. */
  .card.age-1 { opacity: .8; } .card.age-2 { opacity: .6; } .card.age-3 { opacity: .4; }
  .card.age-1:hover, .card.age-2:hover, .card.age-3:hover { opacity: 1; }
  .titles { color: var(--muted); }

  .error-bar { flex: none; margin: 0; padding: 6px 20px; max-height: 70px; overflow: auto; }
  .sec-0 { color: var(--p0); } .sec-1 { color: var(--p1); } .sec-2 { color: var(--p2); } .sec-3 { color: var(--p3); }
  .sec-4 { color: var(--p4); } .sec-5 { color: var(--p5); } .sec-6 { color: var(--p6); } .sec-7 { color: var(--p7); }
  #task-drawer { position: fixed; top: 0; bottom: 0; right: 0; z-index: 5; width: 440px; max-width: calc(100% - 24px);
    background: var(--card); box-shadow: -12px 0 36px #10171980; display: flex; flex-direction: column; }
  .drawer-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 20px; flex: none; }
  .drawer-toolbar span { color: var(--muted); font-size: 12px; }
  .drawer-body { overflow-y: auto; min-height: 0; padding: 0 24px 24px; overscroll-behavior: contain; }
  #drawer-title { font-size: 17px; line-height: 1.65; font-weight: 500; margin: 10px 0 16px; overflow-wrap: anywhere; }
  .drawer-meta { color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
  .drawer-actions { display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0 0; }
  #drawer-group { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
  #drawer-group label { color: var(--muted); font-size: 12px; }
  #task-group { flex: 1; min-width: 0; }
  #task-group-status { flex-basis: 100%; color: var(--muted); font-size: 11px; }
  #task-group-status.error { color: var(--error); }
  .drawer-section { margin-top: 28px; }
  .drawer-section summary { cursor: pointer; font-size: 12px; font-weight: 600; }
  .drawer-section details[open] summary { margin-bottom: 12px; }
  .drawer-section h3 { font-size: 12px; margin: 0 0 10px; font-weight: 600; }
  .drawer-section p { font-size: 11px; margin: 6px 0; }
  a { color: var(--working); text-decoration-color: #7fbbb380; text-underline-offset: 3px; overflow-wrap: anywhere; }
  a:hover { color: var(--text); }
  .source-list { margin: 0; padding: 0; list-style: none; }
  .source-list li { margin: 0 0 10px; }
  .source-list a { font-size: 12px; display: block; }
  .source-list small { display: block; color: var(--muted); font-size: 10px; overflow-wrap: anywhere; }
  .comment-history { list-style: none; padding: 0; margin: 0 0 14px; }
  .comment-history li { margin-bottom: 14px; }
  .comment-history time { color: var(--muted); display: block; font-size: 10px; }
  .comment-body { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
  .task-comments textarea { width: 100%; min-height: 100px; resize: vertical; padding: 10px; background: var(--bg); border: 1px solid #56625c; border-radius: 5px; font-size: 12px; }
  .comment-controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
  .comment-status { font-size: 10px; color: var(--muted); }
  .session-list { color: var(--muted); }
  @media (min-width: 1600px) { .todo-group { flex-basis: 400px; } }
  @media (max-width: 700px) {
    .toolbar { flex-wrap: wrap; gap: 8px 12px; padding: 10px 14px; }
    .task-search { order: 3; flex: 1 0 100%; width: 100%; }
    .toolbar > [data-add] { margin-left: auto; }
    .focus { gap: 10px; padding: 8px 14px; }
    .focus-project { display: none; }
    .focus-row { gap: 4px; }
    .focus-row button[data-action=start] { font-size: 10px; }
    main { padding: 0 14px; }
    .week-toolbar { flex-wrap: wrap; gap: 4px 10px; }
    #task-search-status { order: 3; flex-basis: 100%; }
    #updated { display: none; }
    .tmux-counts { gap: 6px; }
    .tmux-toolbar { gap: 8px; }
  }
</style>
<div class="board-top">
  <header class="toolbar"><h1>Task Board</h1><span id="board-week"></span>
    <div class="task-search" role="search" aria-label="Task search">
      <input id="task-search" type="search" aria-label="Search tasks" autocomplete="off" placeholder="Search tasks, groups, comments…">
      <button type="button" id="task-search-clear" class="quiet" hidden>Clear</button>
    </div><button type="button" class="primary" data-add="week">Add task</button>
  </header>
  <section class="focus" aria-label="Pinned weekly tasks"><h2>Focus</h2><div id="focus-rows"></div>
    <select id="focus-minutes" aria-label="Raycast Focus duration"><option value="25">25 min</option><option value="50">50 min</option><option value="90">90 min</option></select>
  </section>
  <form id="focus-form" hidden><div class="new-task-fields">
    <input id="focus-task" aria-label="New weekly task" type="text" maxlength="240" autocomplete="off" placeholder="What do you want to move forward?" required>
    <label for="focus-vault">Vault <select id="focus-vault" aria-label="Task vault"><option value="work">work</option></select></label>
    <label for="focus-section">Group <select id="focus-section" aria-label="Task group"><option value="Inbox">Inbox</option></select></label>
    <button type="submit" id="focus-save" class="primary">Add task</button><button type="button" id="focus-cancel">Cancel</button>
  </div><p class="focus-help" id="focus-destination" role="status">Saved to this week's Markdown.</p></form>
  <div class="focus-status" id="focus-status" role="status" aria-live="polite"></div>
</div>
<div class="error-bar" id="error" hidden></div>
<main id="main"></main>
<aside id="task-drawer" role="dialog" aria-labelledby="drawer-title" hidden>
  <div class="drawer-toolbar"><span>Task details</span><button type="button" id="drawer-close">Close</button></div>
  <div class="drawer-body">
    <section id="drawer-summary"></section>
    <form id="drawer-group" aria-label="Move task to a group">
      <label for="task-group">Group</label><select id="task-group" aria-label="Task group"></select>
      <button type="submit" id="task-group-save">Move</button>
      <span id="task-group-status" role="status"></span>
    </form>
    <section id="drawer-sources" class="drawer-section" aria-label="Task sources"></section>
    <section id="drawer-comments" class="drawer-section task-comments" aria-label="Task comments"></section>
    <section id="drawer-history" class="drawer-section" aria-label="Focus history"></section>
  </div>
</aside>
<script>
  const GROUPS = ["attention", "review", "working", "parked"];
  const LABEL = { attention: "Needs you", review: "Review", working: "Working", parked: "Parked" };
  let board = { windows: [] };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // Escaped text with markdown links and bare urls turned into anchors (opened by Hammerspoon).
  const linkify = s => esc(s)
    .replace(/\[([^\x5d]+)\x5d\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${url}">${label}</a>`)  // \x5d is a closing bracket: two of them in a row would end the Lua long string
    .replace(/(^|[^"=>])(https?:\/\/[^\s<)]+)/g, (_, before, url) => `${before}<a href="${url}">${url.replace(/^https?:\/\//, "").slice(0, 40)}</a>`);
  const age = epoch => {
    if (!epoch) return "";
    const s = Math.floor(Date.now() / 1000 - epoch);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " d ago";
  };
  const ageClass = (epoch, group) => {
    if (group === "attention" || !epoch) return "";
    const h = (Date.now() / 1000 - epoch) / 3600;
    return h < 0.5 ? "" : h < 3 ? "age-1" : h < 24 ? "age-2" : "age-3";
  };
  const stripIcon = name => { const m = /^(\S+)\s+(.*)$/.exec(name || ""); return m && !/\w/.test(m[1]) ? m[2] : (name || ""); };
  const repoClass = repo => { let h = 0; for (const c of repo || "") h = (h * 31 + c.charCodeAt(0)) >>> 0; return "repo-" + (h % 7); };
  const toolClass = icon => icon === "\u{2733}" ? "claude" : icon === "\u{E00B}" ? "codex" : "";
  function card(w) {
    const recent = t => t.change && t.changed_at && (Date.now() / 1000 - t.changed_at) < 1800;
    const tasks = (w.tasks || []).map(t => `<div class="task"><div class="summary">${esc(t.summary)}</div><div class="next">${esc(t.next)}</div>${recent(t) ? `<div class="change">${esc(t.change)}</div>` : ""}</div>`);
    const titles = (!tasks.length && w.panes && w.panes.length)
      ? `<div class="titles">${esc(w.panes.map(p => p.title).filter(Boolean).join("  ·  "))}</div>` : "";
    const idx = w.index;
    const cls = [w.group, w.state === "error" ? "error" : "", w.summary_stale ? "stale" : "", repoClass(w.repo), ageClass(w.changed_at || w.activity_at, w.group)].join(" ");
    return `<div class="card ${cls}" data-id="${esc(w.id)}" data-session="${esc(w.session)}">
      <div class="meta"><span class="idx">${esc(idx)}</span><span class="icon ${toolClass(w.icon)}">${esc(w.icon)}</span><span class="name">${esc(stripIcon(w.name))}</span>
        <span class="pill ${w.state === "error" ? "error" : w.group}">${LABEL[w.group] || w.group}</span>
        ${w.todo_count ? `<span class="todos-n">☐ ${w.todo_count}</span>` : ""}
        ${w.repo ? `<span class="git">${esc(w.repo)}${w.branch ? ` <span class="branch">@ ${esc(w.branch)}</span>` : ""}</span>` : ""}
        <span class="age">${age(w.changed_at || w.activity_at)}</span></div>
      ${w.group === "parked" ? (tasks[0] || titles) : (tasks.join("") || titles)}
    </div>`;
  }
  const today = () => {
    const date = new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  };
  function compareTaskDue(a, b) {
    if (!!a.done !== !!b.done) return a.done ? 1 : -1;
    if (a.done) return 0;
    if (!a.due || !b.due) return a.due ? -1 : b.due ? 1 : 0;
    return a.due < b.due ? -1 : a.due > b.due ? 1 : 0;
  }
  // Section colours: hash picks a preferred slot, collisions move to the next
  // free one, so sections on screen never share a colour (7 colours; slot 7
  // of the palette is grey and reads as "no colour").
  const hash7 = name => Array.from(name.toLowerCase()).reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0) % 7;
  let sectionSlots = {};
  function assignSectionColours(todos) {
    sectionSlots = {};
    const used = new Set();
    for (const name of [...new Set(todos.map(t => t.section).filter(Boolean))]) {
      let slot = hash7(name);
      for (let i = 0; i < 7 && used.has(slot); i++) slot = (slot + 1) % 7;
      used.add(slot); sectionSlots[name] = slot;
    }
  }
  const sectionClass = name => name && sectionSlots[name] !== undefined ? "sec-" + sectionSlots[name] : "";
  // Presentation only: keep every source URL, including repeated labels. Preserve
  // semantic labels and intervening prose; only the generic provenance label goes away.
  function taskPresentation(text) {
    text = String(text || '');
    const sources = [];
    let body = '', cursor = 0;
    const pattern = /\[([^\x5d\n]+)\x5d\((https?:\/\/)/g;
    let match;
    while ((match = pattern.exec(text))) {
      if (['!', '\\'].includes(text[match.index - 1])) continue;
      const start = match.index + match[0].length - match[2].length;
      let end = start, depth = 1;
      for (; end < text.length; end++) {
        if (text[end] === '\\') { end++; continue; }
        if (text[end] === '(') depth++;
        if (text[end] === ')' && --depth === 0) break;
      }
      if (depth !== 0) continue;
      const url = text.slice(start, end);
      if (/\s/.test(url)) continue;
      body += text.slice(cursor, match.index) + (match[1] === '飞书原消息' ? '' : match[1]);
      sources.push({label: match[1], url});
      cursor = end + 1; pattern.lastIndex = cursor;
    }
    body += String(text || '').slice(cursor);
    body = body.replace(/(^|[\s（(])(https?:\/\/[^\s<>]+)/g, (token, before, url, offset) => {
      if (before === '(' && body[offset - 1] === ']') return token;
      let clean = url.replace(/[.,;，。；）]+$/, '');
      while (clean.endsWith(')') && (clean.match(/\)/g) || []).length > (clean.match(/\(/g) || []).length) clean = clean.slice(0, -1);
      sources.push({label: 'Link', url: clean});
      return before + url.slice(clean.length);
    }).replace(/\s+/g, ' ').trim();
    return {title: body || sources[0]?.label || 'Untitled task', sources};
  }
  const renderedHTML = new WeakMap();
  const setHTML = (element, html) => {
    if (renderedHTML.get(element) !== html) { element.innerHTML = html; renderedHTML.set(element, html); }
  };
  function commentsPanel(t) {
    return `<h3>Comments</h3><div class="comment-content"></div>
      <form data-comment-form="${esc(t.id)}">
        <textarea data-comment="${esc(t.id)}" aria-label="Add a comment" placeholder="Add context, progress, or a question…" maxlength="4000">${esc(commentDrafts.get(t.id) || '')}</textarea>
        <div class="comment-controls"><button type="submit" data-comment-save>Post comment</button>
        <span class="comment-status" role="status"></span></div>
      </form>`;
  }
  function todoRow(t) {
    const {title, sources} = taskPresentation(t.text), busy = !t.id || pendingTasks.has(t.id);
    const day = today(), overdue = !t.done && t.due && t.due < day, dueToday = !t.done && t.due === day;
    return `<article class="todo${t.done ? ' done' : ''}${selectedTask === t.id ? ' selected' : ''}" data-task="${esc(t.id)}">
      <div class="task-line"><input type="checkbox" data-complete aria-label="${esc(t.done ? 'Reopen ' : 'Complete ')}${esc(title)}" ${t.done ? 'checked' : ''} ${busy ? 'disabled' : ''}>
        <button type="button" class="task-title" data-detail="${esc(t.id)}" aria-haspopup="dialog">${esc(title)}</button></div>
      <div class="row-meta">
        ${t.priority ? '<span class="priority">Priority</span>' : ''}
        ${t.pinned ? '<span class="pinned">Pinned</span>' : ''}
        ${t.due ? `<span class="${overdue ? 'overdue' : dueToday ? 'due-today' : ''}">${dueToday ? 'Due today' : `${overdue ? 'Overdue · ' : 'Due '}${esc(t.due.slice(5))}`}</span>` : ''}
        ${t.suspect && !t.done ? '<span>Review completion</span>' : ''}
        ${sources.length ? `<button type="button" data-detail="${esc(t.id)}" data-section="sources">Sources ${sources.length}</button>` : ''}
        ${t.comments?.length || t.comments_error || (commentDrafts.get(t.id) || '').trim() ? `<button type="button" data-detail="${esc(t.id)}" data-section="comments">${t.comments_error ? 'Comments unavailable' : `Comments ${t.comments?.length || 0}`}${(commentDrafts.get(t.id) || '').trim() ? ' · draft' : ''}</button>` : ''}
      </div></article>`;
  }
  function focusRow(t) {
    const {title} = taskPresentation(t.text);
    return `<div class="focus-row" data-task="${esc(t.id)}"><span class="focus-project">${esc(t.section || t.vault)}</span>
      <button type="button" class="task-title" data-detail="${esc(t.id)}" aria-haspopup="dialog" title="${esc(title)}">${esc(title)}</button>
      <button type="button" data-action="start" title="Start Focus in Raycast" ${pendingTasks.has(t.id) ? 'disabled' : ''}>Start Focus</button></div>`;
  }
  function saveDrawerDraft() {
    const field = document.querySelector('#drawer-comments textarea');
    if (field) {
      if (field.value) commentDrafts.set(field.dataset.comment, field.value);
      else commentDrafts.delete(field.dataset.comment);
    }
  }
  function openDrawer(id, section, opener) {
    if (!(board.todos || []).some(t => t.id === id)) return;
    saveDrawerDraft();
    const changed = selectedTask !== id;
    selectedTask = id;
    drawerOpener = {id, section: opener?.dataset.section || '', focus: !!opener?.closest('.focus-row')};
    renderDrawer();
    for (const row of document.querySelectorAll('.todo')) row.classList.toggle('selected', row.dataset.task === id);
    const body = document.querySelector('.drawer-body');
    if (changed) body.scrollTop = 0;
    if (section === 'sources') document.querySelector('#drawer-sources details').open = true;
    if (section) document.getElementById('drawer-' + section)?.scrollIntoView({block: 'start'});
    if (section === 'comments') document.querySelector('#drawer-comments textarea')?.focus();
    else document.getElementById('drawer-close').focus();
  }
  function closeDrawer(restoreFocus = true) {
    saveDrawerDraft();
    selectedTask = null;
    document.getElementById('task-drawer').hidden = true;
    for (const row of document.querySelectorAll('.todo.selected')) row.classList.remove('selected');
    if (!restoreFocus) return;
    const candidates = [...document.querySelectorAll('[data-detail]')].filter(el => el.dataset.detail === drawerOpener?.id);
    const opener = candidates.find(el => (el.dataset.section || '') === drawerOpener.section && !!el.closest('.focus-row') === drawerOpener.focus) || candidates[0];
    (opener || searchInput).focus();
  }
  function renderDrawer() {
    const drawer = document.getElementById('task-drawer');
    drawer.hidden = !selectedTask;
    if (!selectedTask) return;
    const active = document.activeElement;
    const hadFocus = drawer.contains(active);
    const activeAction = hadFocus ? active?.dataset.action : null;
    const task = (board.todos || []).find(t => t.id === selectedTask);
    const comments = document.getElementById('drawer-comments');
    drawer.dataset.task = selectedTask;
    if (!task) {
      saveDrawerDraft();
      setHTML(document.getElementById('drawer-summary'), '<h2 id="drawer-title">Task unavailable</h2><p class="focus-help">This task is no longer in this week. Any comment draft is kept until you close the board.</p>');
      for (const id of ['drawer-group', 'drawer-sources', 'drawer-comments', 'drawer-history']) document.getElementById(id).hidden = true;
      if (hadFocus && active.id !== 'drawer-close') document.getElementById('drawer-close').focus();
      return;
    }
    for (const id of ['drawer-group', 'drawer-sources', 'drawer-comments', 'drawer-history']) document.getElementById(id).hidden = false;
    const {title, sources} = taskPresentation(task.text), busy = pendingTasks.has(task.id);
    setHTML(document.getElementById('drawer-summary'), `<div class="drawer-meta">${esc(task.section || 'Ungrouped')} · ${esc(task.vault)}${task.done ? ' · Completed' : ''}</div>
      <h2 id="drawer-title">${esc(title)}</h2>
      <div class="drawer-meta">${[...(task.tags || []).map(t => '@' + t), ...(task.sources || []).map(t => '#' + t), task.due ? 'Due ' + task.due : '', task.from_week ? 'Carried from ' + task.from_week : ''].filter(Boolean).map(esc).join(' · ')}</div>
      <div class="drawer-actions"><button type="button" class="primary" data-action="start" ${busy || task.done ? 'disabled' : ''}>Start Focus · ${esc(focusMinutes.value)} min</button>
        <button type="button" data-action="${task.pinned ? 'unpin' : 'pin'}" ${busy ? 'disabled' : ''}>${task.pinned ? 'Unpin' : 'Pin to Focus'}</button>
        <button type="button" data-action="open" ${busy ? 'disabled' : ''}>Edit Markdown</button></div>
      <p class="focus-help">Focus and website blocking run in Raycast.</p>
      ${task.suspect && !task.done ? `<p class="focus-help">Possibly completed · <a href="${esc(task.suspect)}">View evidence</a> <button type="button" data-action="dismiss" ${busy ? 'disabled' : ''}>Dismiss hint</button></p>` : ''}
      ${task.stale && !task.done ? `<p class="focus-help">Carried for ${esc(task.stale)} <button type="button" data-action="snooze" ${busy ? 'disabled' : ''}>Snooze reminder</button></p>` : ''}`);
    syncTaskGroup(task);
    const sourcesEl = document.getElementById('drawer-sources');
    const sourcesOpen = sourcesEl.dataset.task === task.id && sourcesEl.querySelector('details')?.open;
    setHTML(sourcesEl, `<details><summary>Sources · ${sources.length}</summary>${sources.length ? `<ul class="source-list">${sources.map(source => `<li><a href="${esc(source.url)}">${esc(source.label)}</a><small>${esc(source.url)}</small></li>`).join('')}</ul>` : '<p class="focus-help">No source links.</p>'}</details>`);
    sourcesEl.dataset.task = task.id;
    sourcesEl.querySelector('details').open = !!sourcesOpen;
    // The editor is owned by the drawer, not by the periodically rebuilt board.
    // Only changing tasks replaces it; refreshes update history and controls in place.
    if (comments.dataset.task !== task.id) {
      comments.innerHTML = commentsPanel(task); comments.dataset.task = task.id;
    }
    const history = task.comments || [];
    setHTML(comments.querySelector('.comment-content'), history.length ? `<ol class="comment-history">${history.map(c => `<li><time datetime="${esc(c.created)}">${focusTime(c.created)}</time><div class="comment-body">${linkify(c.text)}</div></li>`).join('')}</ol>` : '<p class="focus-help">No comments yet.</p>');
    comments.querySelector('textarea').disabled = !!task.comments_error;
    syncCommentControls();
    setHTML(document.getElementById('drawer-history'), `<h3>Focus history</h3>
      <p>${task.stats_error ? 'Statistics unavailable — check the log errors.' : `${task.pomodoros || 0} confirmed sessions · ${task.minutes || 0} min`}</p>
      <p class="focus-help">${task.unconfirmed || 0} unconfirmed requests · excluded from totals</p>
      <p class="focus-help">First start: ${focusTime(task.started)}<br>Task completed: ${focusTime(task.completed_at)}</p>
      <div class="session-list">${(task.sessions || []).slice().reverse().map(session => `<p>${focusTime(session.requested || session.started)} · ${esc(session.result)}${['completed', 'partial'].includes(session.result) ? ` · ${esc(session.minutes)} min<br>${focusTime(session.started)} → ${focusTime(session.finished)}` : ' · time not counted'}</p>`).join('')}</div>
      <button type="button" data-action="log" ${busy ? 'disabled' : ''}>Open log</button>`);
    if (hadFocus && !active.isConnected) {
      const replacement = activeAction
        ? [...drawer.querySelectorAll('[data-action]')].find(el => el.dataset.action === activeAction && !el.disabled)
        : active.matches('summary') ? drawer.querySelector('summary')
        : active.matches('a[href]') ? [...drawer.querySelectorAll('a[href]')].find(el => el.href === active.href) : null;
      (replacement || document.getElementById('drawer-close')).focus({preventScroll: true});
    }
  }
  function renderTodos() {
    const todos = board.todos || [];
    const eligible = todos.filter(t => showCompleted || !t.done), visible = eligible.filter(matchesTask);
    const groups = new Map();
    for (const t of visible) {
      const key = JSON.stringify([t.vault, t.section || '']);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(t);
    }
    const html = [...groups].map(([key, tasks]) => {
      const first = tasks[0];
      return `<section class="todo-group" data-group="${esc(key)}" tabindex="0" aria-label="${esc(first.section || 'Ungrouped')} · ${esc(first.vault)}">
        <h3 class="section ${sectionClass(first.section)}">${esc(first.section || 'Ungrouped')}<span class="vault">${esc(first.vault)}</span><span class="group-count">${tasks.length}</span></h3>
        ${tasks.slice().sort(compareTaskDue).map(todoRow).join('')}</section>`;
    }).join('');
    document.getElementById('task-search-clear').hidden = !taskQuery;
    const status = taskQuery ? `${visible.length} of ${eligible.length} tasks · ${groups.size} groups` : `${todos.filter(t => !t.done).length} open · ${todos.filter(t => t.done).length} done · ${groups.size} groups`;
    return `<section class="week-block"><div class="week-toolbar"><button type="button" id="week" title="Open weekly Markdown in Neovide">This week</button><span id="task-search-status" role="status" aria-live="polite">${status}</span>
      <button type="button" class="quiet" data-show-completed aria-pressed="${showCompleted}">${showCompleted ? 'Hide' : 'Show'} completed</button></div>
      <div class="todos" tabindex="0" aria-label="Weekly task groups — scroll sideways for more">${html || `<p class="empty-state">${taskQuery ? 'No matching tasks. Try another search or show completed tasks.' : 'No open tasks this week.'}</p>`}</div></section>`;
  }
  function render() {
    const previousColumns = document.querySelector('.todos');
    const left = resetTaskScroll ? 0 : (previousColumns?.scrollLeft || 0);
    const tops = new Map([...document.querySelectorAll('.todo-group')].map(el => [el.dataset.group, resetTaskScroll ? 0 : el.scrollTop]));
    const tmuxTop = document.getElementById('tmux-cards')?.scrollTop || 0;
    const focusTop = document.getElementById('focus-rows').scrollTop;
    const active = document.activeElement;
    const focusKey = active?.closest?.('#main, #focus-rows') ? {
      task: active.closest('[data-task]')?.dataset.task, detail: active.dataset.detail, section: active.dataset.section,
      action: active.dataset.action, complete: active.matches('input[data-complete]'), id: active.id,
      group: active.classList.contains('todo-group') ? active.dataset.group : null,
      columns: active.classList.contains('todos'), completed: active.hasAttribute('data-show-completed'),
      pinned: !!active.closest('.focus-row')
    } : null;
    resetTaskScroll = false;
    assignSectionColours(board.todos || []);
    const pinned = (board.todos || []).filter(t => t.pinned && !t.done && matchesTask(t));
    setHTML(document.getElementById('focus-rows'), pinned.map(focusRow).join('') || `<p class="focus-help">${taskQuery ? 'No pinned tasks match.' : 'Open a task and pin it here to keep it in focus.'}</p>`);
    document.getElementById('focus-rows').scrollTop = focusTop;
    document.getElementById('board-week').textContent = board.week || '';
    const wins = board.windows || [], multi = new Set(wins.map(w => w.session)).size > 1, counts = {};
    wins.forEach(w => counts[w.group] = (counts[w.group] || 0) + 1);
    const sessions = [...new Set(wins.map(w => w.session))];
    setHTML(document.getElementById('main'), renderTodos() + `<section class="tmux-block${tmuxCollapsed ? ' collapsed' : ''}" aria-label="Tmux windows">
      <div class="tmux-toolbar"><h2>Tmux · ${wins.length}</h2><div class="tmux-counts">${GROUPS.filter(g => counts[g]).map(g => `<span>${LABEL[g]} ${counts[g]}</span>`).join('')}</div><span id="updated"></span>
        <button type="button" class="quiet" id="tmux-toggle" aria-expanded="${!tmuxCollapsed}" aria-controls="tmux-cards">${tmuxCollapsed ? 'Expand' : 'Collapse'}</button></div>
      <div class="cards" id="tmux-cards" ${tmuxCollapsed ? 'hidden' : ''}>${sessions.map(s => (multi ? `<h2>${esc(s)}</h2>` : '') + `<div class="group">${wins.filter(w => w.session === s).map(w => card(w)).join('')}</div>`).join('')}</div></section>`);
    const columns = document.querySelector('.todos');
    if (columns) columns.scrollLeft = left;
    for (const column of document.querySelectorAll('.todo-group')) column.scrollTop = tops.get(column.dataset.group) || 0;
    document.getElementById('tmux-cards').scrollTop = tmuxTop;
    if (focusKey && !active.isConnected) {
      let replacement;
      if (focusKey.task) {
        const rows = [...document.querySelectorAll('[data-task]')].filter(el => el.dataset.task === focusKey.task && el.classList.contains(focusKey.pinned ? 'focus-row' : 'todo'));
        const controls = rows.flatMap(row => [...row.querySelectorAll('button, input')]);
        replacement = controls.find(el => focusKey.complete ? el.matches('input[data-complete]') : focusKey.detail ? el.dataset.detail === focusKey.detail && el.dataset.section === focusKey.section : el.dataset.action === focusKey.action);
      } else if (focusKey.id) replacement = document.getElementById(focusKey.id);
      else if (focusKey.group) replacement = [...document.querySelectorAll('.todo-group')].find(el => el.dataset.group === focusKey.group);
      else if (focusKey.columns) replacement = columns;
      else if (focusKey.completed) replacement = document.querySelector('[data-show-completed]');
      (replacement || searchInput).focus({preventScroll: true});
    }
    renderHeaderTime(); renderDrawer();
  }
  // "⟳ 10:22 · next in 48s": the countdown ticks every second, the rest only on data changes.
  function renderHeaderTime() {
    const hhmm = t => new Date(t * 1000).toTimeString().slice(0, 5);
    let text = board.generated_at ? "⟳ " + hhmm(board.generated_at) : "";
    if (board.next_refresh_at) {
      const left = Math.round(board.next_refresh_at - Date.now() / 1000);
      text += " · summaries " + (left > 60 ? "in " + Math.ceil(left / 60) + " min" : left > 0 ? "in " + left + "s" : "checking");
    }
    const updated = document.getElementById("updated");
    if (updated) updated.textContent = text;
    const err = document.getElementById("error");
    err.hidden = !board.error;
    if (board.error) err.textContent = "Summaries paused: codex calls failing since " + hhmm(board.error_since || board.generated_at) + " — " + board.error;
  }
  setInterval(renderHeaderTime, 1000);
  const focusInput = document.getElementById("focus-task"), focusMinutes = document.getElementById("focus-minutes");
  const focusSection = document.getElementById("focus-section");
  const focusVault = document.getElementById("focus-vault"), focusForm = document.getElementById("focus-form");
  const taskGroupForm = document.getElementById('drawer-group'), taskGroup = document.getElementById('task-group');
  let focusComposing = false, pendingCreate = null, focusRequest = 0, showCompleted = false;
  let durationInitialized = false, createVaultInitialized = false, tmuxCollapsed = null;
  const searchInput = document.getElementById('task-search');
  let taskQuery = '', searchComposing = false, resetTaskScroll = false;
  const searchText = text => String(text || '').normalize('NFKC').toLocaleLowerCase();
  function matchesTask(task) {
    const terms = searchText(taskQuery).split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    const text = searchText([task.text, task.section, task.vault, task.due,
      ...(task.tags || []).map(t => '@' + t), ...(task.sources || []).map(t => '#' + t),
      ...(task.comments || []).map(c => c.text)].join(' '));
    return terms.every(term => text.includes(term));
  }
  function applySearch() {
    if (searchComposing) return;
    taskQuery = searchInput.value.trim();
    resetTaskScroll = true;
    render();
  }
  searchInput.addEventListener('input', applySearch);
  searchInput.addEventListener('search', applySearch);
  searchInput.addEventListener('compositionstart', () => { searchComposing = true; });
  searchInput.addEventListener('compositionend', () => { searchComposing = false; applySearch(); });
  searchInput.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !searchComposing && !e.isComposing) { e.stopPropagation(); searchInput.value = ''; applySearch(); }
  });
  document.getElementById('task-search-clear').addEventListener('click', () => {
    searchInput.value = ''; applySearch(); searchInput.focus();
  });
  let selectedTask = null, drawerOpener = null;
  const pendingTasks = new Map(), commentDrafts = new Map(), pendingComments = new Map(), commentErrors = new Map();
  const groupDrafts = new Map();
  const composingComments = new Set();
  const rowTask = row => (board.todos || []).find(t => t.id === row?.dataset.task);
  const focusTime = value => value ? esc(value.length === 10 ? value : new Date(value).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"})) : "—";
  const requestID = () => `${Date.now()}-${Math.random().toString(36).slice(2, 10)}-${++focusRequest}`;
  const postFocus = body => {
    if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({focus: body});
  };
  function taskAction(task, action, section) {
    if (!task?.id || pendingTasks.has(task.id)) return;
    if (action === 'start' && task.done) return;
    if (action === 'move' && (section === task.section || !taskSections(task.vault).includes(section))) return;
    const request_id = requestID();
    pendingTasks.set(task.id, request_id);
    if (action === 'move') {
      groupDrafts.set(task.id, {section, request_id});
      postFocus({action, id: task.id, section, request_id});
    } else postFocus({action, id: task.id, minutes: Number(focusMinutes.value), request_id});
    render();
  }
  function syncCommentControls() {
    for (const form of document.querySelectorAll('[data-comment-form]')) {
      const id = form.dataset.commentForm, pending = pendingComments.has(id);
      const task = (board.todos || []).find(t => t.id === id);
      const error = task?.comments_error || commentErrors.get(id) || '';
      const button = form.querySelector('[data-comment-save]');
      button.disabled = pending || !(commentDrafts.get(id) || '').trim() || !!task?.comments_error;
      button.textContent = pending ? 'Saving…' : 'Post comment';
      const status = form.querySelector('.comment-status');
      status.textContent = error || (pending ? 'Saving to Markdown…' : 'Post to save in Markdown.');
      status.className = 'comment-status' + (error ? ' error' : '');
    }
  }
  document.addEventListener('input', e => {
    const field = e.target.closest('textarea[data-comment]');
    if (!field) return;
    commentDrafts.set(field.dataset.comment, field.value);
    syncCommentControls();
  });
  document.addEventListener('focusin', e => {
    // A non-modal overlay must not hide the keyboard's next background target.
    if (selectedTask && !e.target.closest('#task-drawer')) closeDrawer(false);
    const field = e.target.closest('textarea[data-comment]');
    if (field) field.value = commentDrafts.get(field.dataset.comment) || '';
  });
  document.addEventListener('compositionstart', e => {
    if (e.target.dataset.comment) composingComments.add(e.target.dataset.comment);
  });
  document.addEventListener('compositionend', e => {
    if (e.target.dataset.comment) {
      composingComments.delete(e.target.dataset.comment);
      commentDrafts.set(e.target.dataset.comment, e.target.value);
      syncFocus(); renderDrawer();
    }
  });
  document.addEventListener('submit', e => {
    const form = e.target.closest('[data-comment-form]');
    if (!form) return;
    e.preventDefault();
    const id = form.dataset.commentForm, text = (commentDrafts.get(id) || '').trim();
    if (!text || pendingComments.has(id) || composingComments.has(id)) return;
    const request_id = requestID();
    pendingComments.set(id, {text, request_id});
    commentErrors.delete(id);
    postFocus({action: 'comment', id, text, request_id});
    syncCommentControls();
  });
  function taskSections(vault) {
    const config = board.focus_notes?.vaults?.find(v => v.name === vault);
    // Older snapshots have only tasks; new snapshots also expose empty headings.
    const sections = Array.isArray(config?.sections) ? config.sections
      : (board.todos || []).filter(t => t.vault === vault).map(t => t.section);
    return [...new Set(['Inbox', ...sections.filter(section => typeof section === 'string' && section)])];
  }
  function syncTaskGroup(task) {
    const draft = groupDrafts.get(task.id), section = draft?.section ?? task.section ?? '';
    const sections = taskSections(task.vault), available = sections.includes(section);
    const busy = pendingTasks.has(task.id), save = document.getElementById('task-group-save');
    const active = document.activeElement;
    taskGroupForm.dataset.task = task.id;
    setHTML(taskGroup, sections.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join('')
      + (available ? '' : `<option value="${esc(section)}">${section ? esc(section) + ' (unavailable)' : 'Ungrouped'}</option>`));
    taskGroup.value = section;
    taskGroup.disabled = busy;
    save.disabled = busy || !available || section === task.section;
    save.textContent = draft?.request_id ? 'Moving…' : 'Move';
    const error = draft?.error || (!available && section !== (task.section || '') ? 'Group unavailable. Choose another group.' : '');
    const status = document.getElementById('task-group-status');
    status.textContent = error || (draft?.request_id ? 'Moving in Markdown…' : `Move within ${task.vault}.`);
    status.className = error ? 'error' : '';
    if ((active === taskGroup || active === save) && active.disabled) document.getElementById('drawer-close').focus({preventScroll: true});
  }
  function createDestinationAvailable() {
    return (board.focus_notes?.vaults || []).some(v => v.name === focusVault.value)
      && taskSections(focusVault.value).includes(focusSection.value);
  }
  function syncCreateControls(resetGroup = false) {
    const section = resetGroup ? 'Inbox' : (focusSection.value || 'Inbox');
    const sections = taskSections(focusVault.value);
    const available = sections.includes(section);
    setHTML(focusSection, sections.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join('')
      + (available ? '' : `<option value="${esc(section)}">${esc(section)} (unavailable)</option>`));
    focusSection.value = section;
    const valid = createDestinationAvailable();
    document.getElementById('focus-save').disabled = !!pendingCreate || !valid;
    document.getElementById('focus-destination').textContent = valid
      ? `Save to ${focusVault.value} · ${section} in this week's Markdown.`
      : 'This vault or group is no longer available. Choose another destination.';
  }
  const syncFocus = () => {
    const ack = board.focus_ack;
    for (const [id, request] of pendingComments) {
      const result = request.ack || (ack?.request_id === request.request_id ? ack : null);
      if (!result) continue;
      // Another action can be acknowledged while the IME is still composing.
      // Keep this result locally; only delay changing the editor's contents.
      if (composingComments.has(id)) { request.ack = result; continue; }
      pendingComments.delete(id);
      if (result.error) commentErrors.set(id, result.error); else {
        commentErrors.delete(id);
        if ((commentDrafts.get(id) || '').trim() === request.text) {
          commentDrafts.delete(id);
          for (const field of document.querySelectorAll('textarea[data-comment]')) if (field.dataset.comment === id) field.value = '';
        }
      }
    }
    syncCommentControls();
    if (pendingCreate && ack?.request_id === pendingCreate.request_id) {
      if (!ack.error && !focusComposing && focusInput.value.trim() === pendingCreate.text
          && focusVault.value === pendingCreate.vault && focusSection.value === pendingCreate.section) {
        focusInput.value = ""; focusForm.hidden = true;
      }
      pendingCreate = null;
    }
    for (const [id, request] of pendingTasks) {
      if (ack?.request_id !== request) continue;
      pendingTasks.delete(id);
      const draft = groupDrafts.get(id);
      if (draft?.request_id === request) {
        if (ack.error) groupDrafts.set(id, {section: draft.section, error: ack.error});
        else groupDrafts.delete(id);
      }
    }
    let vault = focusVault.value;
    const vaults = board.focus_notes?.vaults || [];
    if (!createVaultInitialized && vaults.length) {
      if (!vaults.some(v => v.name === vault)) vault = vaults[0].name;
      createVaultInitialized = true;
    }
    setHTML(focusVault, vaults.map(v => `<option value="${esc(v.name)}">${esc(v.name)}</option>`).join('')
      + (vault && !vaults.some(v => v.name === vault) ? `<option value="${esc(vault)}">${esc(vault)} (unavailable)</option>` : ''));
    if (vault) focusVault.value = vault;
    syncCreateControls();
    if (!durationInitialized) { focusMinutes.value = String(board.focus?.minutes || 25); durationInitialized = true; }
    const error = board.focus_notes?.error || (board.focus_notes?.errors || []).join(" · ") || ack?.error;
    const status = document.getElementById("focus-status");
    status.textContent = error || board.focus_notice || "";
    status.className = 'focus-status' + (error ? ' error' : '');
  };
  focusInput.addEventListener("compositionstart", () => { focusComposing = true; });
  focusInput.addEventListener("compositionend", () => { focusComposing = false; });
  focusVault.addEventListener('change', () => syncCreateControls(true));
  focusSection.addEventListener('change', () => syncCreateControls());
  taskGroup.addEventListener('change', () => {
    const task = rowTask(taskGroupForm);
    if (!task || pendingTasks.has(task.id)) return;
    if (taskGroup.value === (task.section || '')) groupDrafts.delete(task.id);
    else groupDrafts.set(task.id, {section: taskGroup.value});
    syncTaskGroup(task);
  });
  taskGroupForm.addEventListener('submit', e => {
    e.preventDefault();
    taskAction(rowTask(taskGroupForm), 'move', taskGroup.value);
  });
  focusForm.addEventListener("submit", e => {
    e.preventDefault();
    const text = focusInput.value.trim();
    if (!text || focusComposing || pendingCreate || !createDestinationAvailable()) return;
    pendingCreate = {text, vault: focusVault.value, section: focusSection.value, request_id: requestID()};
    document.getElementById("focus-save").disabled = true;
    postFocus({action: 'create', ...pendingCreate, pin: false});
  });
  document.getElementById("focus-cancel").addEventListener("click", () => { focusForm.hidden = true; });
  focusMinutes.addEventListener("change", () => { postFocus({action: 'duration', minutes: Number(focusMinutes.value)}); renderDrawer(); });
  window.update = b => { board = b; if (tmuxCollapsed === null) tmuxCollapsed = b.tmux_collapsed === true; syncFocus(); render(); };
  document.getElementById('drawer-close').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && selectedTask && !e.isComposing && !composingComments.size && !focusComposing && !searchComposing) {
      e.preventDefault(); closeDrawer();
    }
  });
  if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ ready: true });
  document.addEventListener("click", e => {
    const detail = e.target.closest('[data-detail]');
    if (detail) { openDrawer(detail.dataset.detail, detail.dataset.section, detail); return; }
    if (e.target.closest('#tmux-toggle')) {
      tmuxCollapsed = !tmuxCollapsed;
      if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({tmux_collapsed: tmuxCollapsed});
      render();
      document.getElementById('tmux-toggle').focus();
      return;
    }
    const add = e.target.closest('[data-add]');
    if (add) { focusForm.hidden = false; focusInput.focus(); return; }
    if (e.target.closest('[data-show-completed]')) { showCompleted = !showCompleted; render(); return; }
    const action = e.target.closest('[data-action]');
    if (action) {
      const row = action.closest('[data-task]'), t = rowTask(row);
      if (!action.disabled) taskAction(t, action.dataset.action);
      return;
    }
    const link = e.target.closest("a[href]");
    if (link) {
      e.preventDefault();  // External links open through the native bridge.
      if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ url: link.href });
      return;
    }
    if (e.target.closest("#week")) {
      if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ edit: true });
      return;
    }
    const box = e.target.closest("input[data-complete]");
    if (box) {
      const row = box.closest("[data-task]"), t = rowTask(row);
      if (t && !box.disabled) taskAction(t, box.checked ? 'complete' : 'reopen');
      return;
    }
    const el = e.target.closest(".card");
    if (el && window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ id: el.dataset.id, session: el.dataset.session });
  });
  setInterval(render, 60000);
</script>
]]

local function read_board()
  local file = io.open(BOARD_PATH, "rb")
  if not file then return nil end
  local contents = file:read("*a")
  file:close()
  local ok, decoded = pcall(hs.json.decode, contents)
  if ok and type(decoded) == "table" and type(decoded.windows) == "table" then return decoded end
  return nil
end

-- Open this week's todo files in Neovide, one tab per vault (todo-notes builds
-- the nvim arguments), or focus the window already open.
local function edit_todos()
  if neovide.find("todos") then neovide.open("todos", {}); return end
  local args = {}
  for line in (hs.execute(TODO_TOOL .. " nvim-args", true) or ""):gmatch("[^\n]+") do args[#args + 1] = line end
  if #args == 0 then hs.printf("task board: no todo files (is ~/.config/task-board/config.toml set?)"); return end
  if not neovide.open("todos", args) then  -- the first argument is a file: Neovide starts in its directory
    hs.task.new(TODO_TOOL, nil, { "edit" }):start()  -- terminal fallback inside todo-notes
  end
end
M.edit = edit_todos -- also used by raycast/todo-week.sh


local function run_todo_tool(args, what)
  local task = hs.task.new(TODO_TOOL, function(code, _, err)
    if code ~= 0 then hs.printf("task board: todo %s failed (%s): %s", what, tostring(code), err or "") end
  end, args)
  task:setEnvironment({ HOME = HOME, PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" })
  if not task:start() then hs.printf("task board: could not start %s", TODO_TOOL) end
end

local function todo_line(todo) return tostring(math.tointeger(tonumber(todo.line)) or todo.line) end

local function toggle_todo(todo)
  run_todo_tool({ "toggle", tostring(todo.vault), todo_line(todo) }, "toggle")
end

local function jump_to(row)
  hs.execute(string.format("/opt/homebrew/bin/tmux switch-client -t %s \\; select-window -t %s", row.session or "", row.id))
  hs.application.launchOrFocus("Ghostty")
end

local function preferred_screen()
  for _, name in ipairs(SCREEN_NAMES) do
    for _, screen in ipairs(hs.screen.allScreens()) do
      if screen:name() == name then return screen end
    end
  end
  for _, screen in ipairs(hs.screen.allScreens()) do
    if screen ~= hs.screen.primaryScreen() then return screen end
  end
  return hs.screen.primaryScreen()
end

local function default_frame()
  local f = preferred_screen():frame()
  local w = math.min(f.w * 0.5, 720)
  return { x = f.x + f.w - w - 16, y = f.y + 16, w = w, h = f.h - 32 }
end

local function frame_on_some_screen(frame)
  if type(frame) ~= "table" or not frame.w or frame.w < 200 then return false end
  local center = hs.geometry.point(frame.x + frame.w / 2, frame.y + frame.h / 2)
  for _, screen in ipairs(hs.screen.allScreens()) do
    if center:inside(screen:fullFrame()) then return true end
  end
  return false
end

-- hs.task:setInput replaces unsent input. Keep one record in flight until
-- the helper acknowledges it, including during startup and rapid toggles.
local function flush()
  if state.pending or not state.task or not state.task:isRunning() or #state.queue == 0 then return end
  state.pending = true
  state.task:setInput(table.remove(state.queue, 1))
end

local function send(message)
  state.queue[#state.queue + 1] = hs.json.encode(message) .. "\n"
  flush()
end

local function push_board()
  if not state.task then return end
  local board = state.board or { windows = {} }
  board.week_gap = hs.settings.get(GAP_SETTING)
  board.tmux_collapsed = hs.settings.get(TMUX_COLLAPSED_SETTING) == true
  board.focus = hs.settings.get(FOCUS_SETTING) or { minutes = 25 }
  board.focus_notice = state.focus_error or state.focus_notice
  board.focus_notes = state.focus_notes or { tasks = {}, vaults = {} }
  if state.focus_notes then
    board.todos, board.week = state.focus_notes.tasks, state.focus_notes.week
  end
  board.focus_ack = state.focus_ack
  send({ command = "update", board = board })
end

local function reload_board()
  state.board = read_board()
  push_board()
end

local pump_focus
-- hs.task keeps callbacks in the Lua registry until its userdata is collected.
-- A callback that closes over its task prevents that collection unless detached.
local function drain_task_output() return true end
local function clear_task_callbacks(task)
  if not task then return end
  task:setCallback(nil)
  -- An active writer must still drain stdout/stderr or a full pipe can block its
  -- exit. This callback captures nothing, so the task remains collectible.
  task:setStreamingCallback(task:isRunning() and drain_task_output or nil)
end

local function run_focus(args, callback, on_error)
  state.focus_queue = state.focus_queue or {}
  state.focus_queue[#state.focus_queue + 1] = { args = args, callback = callback, on_error = on_error }
  pump_focus()
end

pump_focus = function()
  if state.focus_job or not state.focus_queue or #state.focus_queue == 0 then return end
  local request = table.remove(state.focus_queue, 1)
  local arguments = { FOCUS_TOOL }
  for _, arg in ipairs(request.args) do arguments[#arguments + 1] = arg end
  local job, exit_code, finish_timer
  local output, errors, final_output, final_errors = "", "", "", ""
  local function cleanup()
    if finish_timer then finish_timer:stop(); finish_timer = nil end
    clear_task_callbacks(job)
    job = nil
    output, errors, final_output, final_errors = "", "", "", ""
  end
  local function finish(timed_out)
    if not job or state.focus_job ~= job or exit_code == nil then return end
    -- Pending read notifications can arrive AFTER the process exits. The final
    -- callback's unread tail belongs after those chunks, not before them.
    local out, err = output .. final_output, errors .. final_errors
    local ok, data = pcall(hs.json.decode, out)
    if not (ok and type(data) == "table") and not timed_out then return end
    state.focus_job, state.focus_cleanup = nil, nil
    cleanup()
    if exit_code == 0 and ok and type(data) == "table" and not data.error then
      local changed = out ~= state.focus_output
      local recovered = state.focus_error ~= nil
      state.focus_error = nil
      if data.refresh_error then
        -- The move committed, but an independent Markdown problem prevented a
        -- fresh snapshot. Acknowledge the move without replacing the cached board.
        state.focus_error = data.refresh_error
        for _, task in ipairs(state.focus_notes and state.focus_notes.tasks or {}) do
          if data.result and task.id == data.result.id then
            task.section, task.line = data.result.section, data.result.line
          end
        end
      else
        state.focus_notes, state.focus_output = data, out
      end
      if request.callback then request.callback(data) end
      if changed or recovered or request.callback then push_board() end
    else
      state.focus_error = ok and type(data) == "table" and data.error
        or (exit_code == 0 and "Incomplete task response. Refresh and try again."
          or "Task command failed. Check the Hammerspoon console and try again.")
      if request.on_error then request.on_error(state.focus_error) end
      hs.printf("focus notes: %s", err ~= "" and err or state.focus_error)
      push_board()
    end
    pump_focus()
  end
  job = hs.task.new("/opt/homebrew/bin/python3", function(code, out, err)
    if not job or state.focus_job ~= job then return end
    exit_code, final_output, final_errors = code, out or "", err or ""
    finish(false)
    if job and state.focus_job == job then
      -- Usually the pending stdout notification completes this immediately;
      -- bound the wait so a malformed response cannot lock all task controls.
      finish_timer = hs.timer.doAfter(1, function() finish(true) end)
    end
  end, function(_, out, err)
    if not job or state.focus_job ~= job then return false end
    output, errors = output .. (out or ""), errors .. (err or "")
    finish(false)
    return true
  end, arguments)
  state.focus_job, state.focus_cleanup = job, cleanup
  if not job or not job:start() then
    state.focus_job, state.focus_cleanup = nil, nil
    cleanup()
    state.focus_error = "Could not start focus-notes. Run the Task Board installer."
    if request.on_error then request.on_error(state.focus_error) end
    -- Let the periodic read retry after installation or a transient launch failure.
    state.focus_queue = {}
    push_board()
  end
end

local function reload_focus()
  if not state.focus_job and (not state.focus_queue or #state.focus_queue == 0) then run_focus({ "snapshot" }) end
end

local function focus_action(focus)
  if type(focus) ~= "table" then return end
  local minutes = tonumber(focus.minutes)
  if focus.action == "duration" then
    if minutes == 25 or minutes == 50 or minutes == 90 then
      hs.settings.set(FOCUS_SETTING, { minutes = minutes })
    end
    return
  end
  if type(focus.request_id) ~= "string" then return end
  local args
  if focus.action == "create" then
    if type(focus.text) ~= "string" or type(focus.vault) ~= "string" then return end
    args = { "create", "--vault", focus.vault, "--title", focus.text, "--request-id", focus.request_id }
    if focus.section ~= nil then
      if type(focus.section) ~= "string" then return end
      args[#args + 1] = "--section"; args[#args + 1] = focus.section
    end
    if focus.pin then args[#args + 1] = "--pin" end
  else
    if type(focus.id) ~= "string" then return end
    local allowed = { start=true, complete=true, reopen=true, pin=true, unpin=true, open=true, log=true, snooze=true, dismiss=true, comment=true, move=true }
    if not allowed[focus.action] then return end
    args = { focus.action, "--id", focus.id }
    if focus.action == "move" then
      if type(focus.section) ~= "string" then return end
      args[#args + 1] = "--section"; args[#args + 1] = focus.section
    elseif focus.action == "comment" then
      if type(focus.text) ~= "string" then return end
      args[#args + 1] = "--text"; args[#args + 1] = focus.text
      args[#args + 1] = "--request-id"; args[#args + 1] = focus.request_id
    elseif focus.action == "start" then
      if minutes ~= 25 and minutes ~= 50 and minutes ~= 90 then return end
      args[#args + 1] = "--minutes"; args[#args + 1] = tostring(minutes)
      args[#args + 1] = "--request-id"; args[#args + 1] = focus.request_id
      hs.settings.set(FOCUS_SETTING, { minutes = minutes })
    end
  end
  run_focus(args, function(data)
    state.focus_ack = { request_id = focus.request_id }
    local result = data.result or {}
    if focus.action == "start" then
      if result.url and result.url ~= "" then
        local opened = hs.urlevent.openURL(result.url)
        state.focus_notice = opened == false and "Could not open Raycast. Request remains unconfirmed in the log."
          or "Request sent to Raycast. Check its timer; no focus time counted yet."
      end
    elseif focus.action == "comment" then
      state.focus_notice = "Comment saved to Markdown."
    elseif focus.action == "move" then
      state.focus_notice = "Task moved to " .. tostring(result.section or focus.section) .. "."
    elseif focus.action == "open" or focus.action == "log" then
      if result.file then neovide.open("task-board:" .. result.file, { result.file }, { "+" .. tostring(result.line or 1) }) end
      state.focus_notice = "Opened Markdown."
    else
      state.focus_notice = "Saved to weekly Markdown."
    end
  end, function(error)
    state.focus_ack = { request_id = focus.request_id, error = error }
  end)
end

local function handle_event(event)
  if event.event == "ack" then
    state.pending = false
    flush()
  elseif event.event == "frame" then
    if frame_on_some_screen(event.frame) then hs.settings.set(FRAME_SETTING, event.frame) end
  elseif event.event == "rendered" then
    state.rendered = true
  elseif event.event == "status" then
    state.status = event
  elseif event.event == "error" then
    hs.printf("task board window: %s", tostring(event.message))
  elseif event.event == "message" then
    local body = event.body
    if type(body) ~= "table" then return end
    if body.ready then push_board()
    elseif body.focus then focus_action(body.focus)
    elseif body.edit then edit_todos()
    elseif body.todo then toggle_todo(body.todo)
    elseif body.dismiss then run_todo_tool({ "dismiss", tostring(body.dismiss.vault), todo_line(body.dismiss) }, "dismiss")
    elseif body.snooze then run_todo_tool({ "snooze", tostring(body.snooze.vault), todo_line(body.snooze) }, "snooze")
    elseif type(body.tmux_collapsed) == "boolean" then hs.settings.set(TMUX_COLLAPSED_SETTING, body.tmux_collapsed)
    elseif body.gap ~= nil then hs.settings.set(GAP_SETTING, body.gap or nil)
    elseif body.url then hs.urlevent.openURL(body.url)
    elseif body.id then jump_to(body) end
  end
end

local function build_view()
  if state.task and state.task:isRunning() then return true end
  if not hs.fs.attributes(WINDOW_TOOL) then
    hs.printf("task board: run dotfiles/scripts/install_task_board.sh first")
    return false
  end
  state.queue, state.pending, state.output = {}, false, ""
  state.rendered, state.status = false, nil
  local task
  local function cleanup()
    clear_task_callbacks(task)
    task = nil
  end
  task = hs.task.new(WINDOW_TOOL, function(code, _, err)
    local exited = task
    cleanup()
    if not exited or state.task ~= exited then return end
    state.task, state.window_cleanup, state.queue, state.pending = nil, nil, {}, false
    if code ~= 0 then hs.printf("task board window exited (%s): %s", tostring(code), err or "") end
  end, function(_, out, err)
    if not task or state.task ~= task then return false end
    if err and err ~= "" then hs.printf("task board window: %s", err) end
    state.output = state.output .. (out or "")
    while true do
      local boundary = state.output:find("\n", 1, true)
      if not boundary then break end
      local line = state.output:sub(1, boundary - 1)
      state.output = state.output:sub(boundary + 1)
      local ok, event = pcall(hs.json.decode, line)
      if ok and type(event) == "table" then handle_event(event) end
    end
    return true
  end)
  state.task, state.window_cleanup = task, cleanup
  if not task or not task:start() then
    state.task, state.window_cleanup = nil, nil
    cleanup()
    hs.printf("task board: could not start %s", WINDOW_TOOL)
    return false
  end
  local saved = hs.settings.get(FRAME_SETTING)
  local frame = frame_on_some_screen(saved) and saved or default_frame()
  send({ command = "load", html = PAGE, frame = frame })
  return true
end

-- ⌘⇧B: hidden/minimized -> show and focus; visible -> hide.
function M.toggle()
  if not state.task or not state.task:isRunning() then
    if build_view() then reload_board() end
  else
    send({ command = "toggle" })
  end
end

-- Diagnostics use the helper's own protocol, without focusing the window.
function M.inspect()
  if state.task then send({ command = "inspect" }) end
end
function M.status()
  return { running = state.task ~= nil and state.task:isRunning(), rendered = state.rendered,
    window = state.status, pending = state.pending, queued = #state.queue,
    focus_tasks = #((state.focus_notes or {}).tasks or {}), focus_error = state.focus_error }
end

function M.start()
  if state.hotkey then return M end
  state.board = read_board()
  build_view()
  reload_focus()
  state.focus_timer = hs.timer.doEvery(5, reload_focus)
  state.watcher = hs.pathwatcher.new(STATE_DIR, function(paths)
    for _, path in ipairs(paths) do
      if path:sub(-#"board.json") == "board.json" then reload_board(); return end
    end
  end):start()
  state.hotkey = hs.hotkey.bind(TOGGLE_HOTKEY[1], TOGGLE_HOTKEY[2], M.toggle)
  state.edit_hotkey = hs.hotkey.bind(EDIT_HOTKEY[1], EDIT_HOTKEY[2], edit_todos)
  -- External entry points (Raycast, shell): open "hammerspoon://task-board?action=edit|toggle".
  hs.urlevent.bind("task-board", function(_, params)
    local action = params and params.action
    if action == "edit" then edit_todos() elseif action == "toggle" then M.toggle() end
  end)
  local previous = hs.shutdownCallback
  state.previous_shutdown = previous
  state.shutdown = function()
    M.stop()
    if previous then previous() end
  end
  hs.shutdownCallback = state.shutdown
  return M
end

function M.stop()
  if state.focus_timer then state.focus_timer:stop() end
  -- Detach callbacks while allowing any in-progress atomic Markdown write to
  -- finish. hs.task's own self reference lives until the subprocess exits.
  if state.focus_cleanup then state.focus_cleanup() end
  state.focus_cleanup = nil
  state.focus_timer, state.focus_job, state.focus_queue = nil, nil, {}
  if state.watcher then state.watcher:stop() end
  if state.hotkey then state.hotkey:delete() end
  if state.edit_hotkey then state.edit_hotkey:delete() end
  hs.urlevent.bind("task-board", nil)
  -- Closing stdin exits the helper on reload, even if it is hidden.
  if state.task and state.task:isRunning() then state.task:closeInput() end
  if state.window_cleanup then state.window_cleanup() end
  state.window_cleanup = nil
  state.task, state.watcher, state.hotkey, state.edit_hotkey = nil, nil, nil, nil
  state.queue, state.pending = {}, false
  if hs.shutdownCallback == state.shutdown then hs.shutdownCallback = state.previous_shutdown end
  state.shutdown, state.previous_shutdown = nil, nil
end

return M
