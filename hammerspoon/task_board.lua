-- Task board: shows ~/.cache/tmux-task-board/board.json (written by
-- tmux/task-board.py) in a draggable, resizable utility window rendered as
-- HTML. Clicking a card selects that tmux window and brings Ghostty forward.
--
-- The window is a real NSWindow (hs.webview): drag it to any screen, resize
-- it, close it with the title-bar button and bring it back with ⌘⇧B. Its
-- frame is remembered across reloads. ⌘⇧D opens this week's todo files in
-- Neovide (so does clicking the "This week" heading).

local M = {}

local HOME = assert(os.getenv("HOME"), "HOME is required")
local STATE_DIR = HOME .. "/.cache/tmux-task-board"
local BOARD_PATH = STATE_DIR .. "/board.json"
local FRAME_SETTING = "taskBoardFrame"
local GAP_SETTING = "taskBoardWeekGap"  -- px between the cards and This week; nil = pinned to the bottom
local TODO_TOOL = HOME .. "/.local/bin/todo-notes"
-- Default placement when no frame is remembered: right half of the first
-- matching screen, else any non-primary screen.
local SCREEN_NAMES = { "T270LG" }
local TOGGLE_HOTKEY = { { "cmd", "shift" }, "b" }
local EDIT_HOTKEY = { { "cmd", "shift" }, "d" }  -- open this week's todo files in Neovide

local neovide = require("neovide_window")
local state = { view = nil, watcher = nil, board = nil, hotkey = nil, frame_timer = nil }

local PAGE = [[
<!doctype html>
<meta charset="utf-8">
<style>
  @font-face { font-family: "BoardIcons"; src: local("CodexStatusSymbols-Regular"); unicode-range: U+E00B-E00D; }
  :root {
    --bg: #1e2326; --card: #272e33; --card-hover: #2e373c; --text: #d3c6aa; --muted: #859289; --tag: #a7b0a8;
    --title: #dbbc7f; --attention: #dbbc7f; --error: #e67e80; --review: #a7c080; --working: #7fbbb3; --parked: #4f585e;
    --change: #e69875; --claude: #e69875; --codex: #83c092;
    /* repo colours, picked by a hash of the repo name */
    --p0: #e67e80; --p1: #e69875; --p2: #dbbc7f; --p3: #a7c080; --p4: #83c092; --p5: #7fbbb3; --p6: #d699b6; --p7: #a7b0a8;
  }
  html { color-scheme: dark; }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #3a444a; border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: #4f585e; }
  html, body { margin: 0; background: var(--bg); color: var(--text);
    font: 13px/1.5 "BoardIcons", "Maple Mono NF CN", "JetBrainsMono Nerd Font", "PingFang SC", monospace; }
  header { position: sticky; top: 0; background: var(--bg); padding: 10px 14px 6px; display: flex; align-items: baseline; gap: 12px;
    border-bottom: 1px solid #2e373c; z-index: 1; white-space: nowrap; overflow: hidden; }
  header h1 { font-size: 15px; margin: 0; color: var(--title); font-weight: 600; }
  header .counts { display: flex; gap: 10px; font-size: 12px; }
  header .counts span::before { content: "●"; margin-right: 4px; }
  header .updated { margin-left: auto; color: var(--muted); font-size: 11px; flex-shrink: 0; }
  /* The window is a flex column: cards at the top, This week pinned to the bottom. */
  body { min-height: 100vh; display: flex; flex-direction: column; }
  main { padding: 4px 10px 12px; flex: 1 1 auto; display: flex; flex-direction: column; }
  main > .cards { flex: 0 0 auto; }
  main > .week-block { margin-top: var(--week-gap, auto); }  /* auto = pinned to the bottom; the divider drag sets a fixed gap */
  h2 { font-size: 11px; font-weight: 600; letter-spacing: .08em; margin: 10px 2px 4px; text-transform: uppercase; color: var(--muted); }
  .pill { font-size: 10px; padding: 0 6px; border-radius: 8px; background: #3a444a; color: var(--text); letter-spacing: .04em; }
  .pill.attention { background: #4a4330; color: var(--attention); } .pill.error { background: #4a3336; color: var(--error); }
  .pill.review { background: #34412f; color: var(--review); } .pill.working { background: #2f4145; color: var(--working); }
  .pill.parked { color: var(--muted); }
  /* Masonry-style columns: cards keep their natural height; a second column only appears past ~1060px. */
  .group { column-width: 520px; column-gap: 8px; }
  .card { background: var(--card); border-left: 3px solid var(--parked); border-radius: 5px; padding: 5px 9px; cursor: pointer;
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
  h2.attention, .counts .attention { color: var(--attention); } h2.review, .counts .review { color: var(--review); }
  h2.working, .counts .working { color: var(--working); } h2.parked, .counts .parked { color: var(--muted); }
  .titles { color: var(--muted); }
  /* This week's todos */
  /* Same masonry columns as the cards; a heading and its items stay together. */
  .todos { margin: 4px 0 10px; column-width: 520px; column-gap: 8px; }
  .todo-group { break-inside: avoid; margin-bottom: 8px; }
  .divider { height: 1px; background: #3a444a; margin: 0 0 12px; cursor: row-resize; position: relative; }
  .divider::before { content: ""; position: absolute; inset: -8px 0; }
  .divider:hover, .divider.dragging { background: var(--muted); }
  h2.week { cursor: pointer; } h2.week:hover { color: var(--text); }
  h2.week::after { content: "  open in Neovide"; font-weight: 400; letter-spacing: 0; text-transform: none; opacity: 0; }
  h2.week:hover::after { opacity: .7; }
  .todo { display: flex; gap: 8px; align-items: baseline; padding: 3px 6px; border-radius: 4px; }
  .todo:hover { background: var(--card-hover); }
  .todo input { margin: 0; accent-color: var(--review); cursor: pointer; }
  .todo .vault { font-size: 10px; color: var(--muted); border: 1px solid #3a444a; border-radius: 6px; padding: 0 5px; }
  .todo .text { flex: 1 1 auto; min-width: 0; }
  .todo .tag, .todo .src, .todo .due, .todo .from { flex: none; white-space: nowrap; }
  .todo a { color: var(--working); text-decoration: none; border-bottom: 1px dotted var(--working); }
  .todo a:hover { color: var(--text); border-bottom-style: solid; }
  .todo a::after { content: "↗"; font-size: 10px; margin-left: 2px; }
  .todo.done .text { color: var(--muted); text-decoration: line-through; }
  .todo .tag { color: var(--working); font-size: 12px; }
  .todo .src, .todo .from { color: var(--muted); font-size: 11px; }
  .todo .due { color: var(--attention); font-size: 11px; }
  .todo.priority .text::before { content: "⏫ "; }
  .todo.overdue .due { color: var(--error); }
  .meta .todos-n { color: var(--review); }
  h3.section { font-size: 11px; font-weight: 600; margin: 8px 6px 2px; letter-spacing: .06em; }
  h3.section .vault { font-weight: 400; color: var(--muted); margin-left: 6px; font-size: 10px; }
  .sec-0 { color: var(--p0); } .sec-1 { color: var(--p1); } .sec-2 { color: var(--p2); } .sec-3 { color: var(--p3); }
  .sec-4 { color: var(--p4); } .sec-5 { color: var(--p5); } .sec-6 { color: var(--p6); } .sec-7 { color: var(--p7); }
  .todo.sec-0 { border-left: 3px solid var(--p0); } .todo.sec-1 { border-left: 3px solid var(--p1); }
  .todo.sec-2 { border-left: 3px solid var(--p2); } .todo.sec-3 { border-left: 3px solid var(--p3); }
  .todo.sec-4 { border-left: 3px solid var(--p4); } .todo.sec-5 { border-left: 3px solid var(--p5); }
  .todo.sec-6 { border-left: 3px solid var(--p6); } .todo.sec-7 { border-left: 3px solid var(--p7); }
  .todo { border-left: 3px solid transparent; }
</style>
<header><h1>Task Board</h1><div class="counts" id="counts"></div><div class="updated" id="updated"></div></header>
<div class="error-bar" id="error" hidden></div>
<main id="main"></main>
<script>
  const GROUPS = ["attention", "review", "working", "parked"];
  const LABEL = { attention: "Needs you", review: "Review", working: "Working", parked: "Parked" };
  const STATE = { error: "error", waiting: "waiting", done: "done", running: "running", idle: "idle" };
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
  const shorten = (s, n) => (s && s.length > n) ? s.slice(0, n - 1) + "…" : (s || "");
  const repoClass = repo => { let h = 0; for (const c of repo || "") h = (h * 31 + c.charCodeAt(0)) >>> 0; return "repo-" + (h % 7); };
  const toolClass = icon => icon === "\u{2733}" ? "claude" : icon === "\u{E00B}" ? "codex" : "";
  function card(w, multi) {
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
  const today = () => new Date().toISOString().slice(0, 10);
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
  function todoRow(t, i) {
    const cls = ["todo", t.done ? "done" : "", t.priority ? "priority" : "", (!t.done && t.due && t.due < today()) ? "overdue" : "", sectionClass(t.section)].join(" ");
    return `<label class="${cls}" data-i="${i}"><input type="checkbox" ${t.done ? "checked" : ""}>
      <span class="vault">${esc(t.vault)}</span><span class="text">${linkify(t.text)}</span>
      ${t.tags.map(x => `<span class="tag">@${esc(x)}</span>`).join(" ")}
      ${t.sources.map(x => `<span class="src">#${esc(x)}</span>`).join(" ")}
      ${t.due ? `<span class="due">📅 ${esc(t.due.slice(5))}</span>` : ""}
      ${t.from_week ? `<span class="from">from ${esc(t.from_week.slice(5))}</span>` : ""}</label>`;
  }
  function renderTodos() {
    const todos = board.todos || [];
    assignSectionColours(todos);
    const open = todos.filter(t => !t.done), done = todos.filter(t => t.done);
    if (!todos.length) return "";
    // Group by vault + "## Heading" in file order; ungrouped items come first within a vault.
    let html = "", key = null;
    todos.forEach((t, i) => {
      if (t.done) return;
      const k = t.vault + "\u0000" + (t.section || "");
      if (k !== key) {
        if (key !== null) html += "</div>";
        key = k;
        html += `<div class="todo-group">` + (t.section
          ? `<h3 class="section ${sectionClass(t.section)}">${esc(t.section)}<span class="vault">${esc(t.vault)}</span></h3>`
          : `<h3 class="section"><span class="vault">${esc(t.vault)}</span></h3>`);
      }
      html += todoRow(t, i);
    });
    if (key !== null) html += "</div>";
    return `<div class="week-block"><div class="divider" title="Drag to adjust the gap · double-click to reset"></div><h2 class="week" id="week">This week · ${esc(board.week || "")}<span class="n">· ${open.length} open · ${done.length} done</span></h2>
      <div class="todos">${html}</div></div>`;
  }
  function render() {
    const wins = board.windows || [];
    const multi = new Set(wins.map(w => w.session)).size > 1;
    const counts = {};
    wins.forEach(w => counts[w.group] = (counts[w.group] || 0) + 1);
    document.getElementById("counts").innerHTML = GROUPS.filter(g => counts[g]).map(g => `<span class="${g}">${LABEL[g]} ${counts[g]}</span>`).join("");
    renderHeaderTime();
    // tmux order, one block per session; no regrouping so cards never move.
    const sessions = [...new Set(wins.map(w => w.session))];
    document.getElementById("main").innerHTML = `<div class="cards">` + sessions.map(s =>
      (multi ? `<h2>${esc(s)}</h2>` : "") + `<div class="group">` + wins.filter(w => w.session === s).map(w => card(w, multi)).join("") + `</div>`).join("") + `</div>` + renderTodos();
  }
  // "⟳ 10:22 · next in 48s": the countdown ticks every second, the rest only on data changes.
  function renderHeaderTime() {
    const hhmm = t => new Date(t * 1000).toTimeString().slice(0, 5);
    let text = board.generated_at ? "⟳ " + hhmm(board.generated_at) : "";
    if (board.next_refresh_at) {
      const left = Math.round(board.next_refresh_at - Date.now() / 1000);
      text += " · summaries " + (left > 60 ? "in " + Math.ceil(left / 60) + " min" : left > 0 ? "in " + left + "s" : "checking");
    }
    document.getElementById("updated").textContent = text;
    const err = document.getElementById("error");
    err.hidden = !board.error;
    if (board.error) err.textContent = "Summaries paused: codex calls failing since " + hhmm(board.error_since || board.generated_at) + " — " + board.error;
  }
  setInterval(renderHeaderTime, 1000);
  // Gap between the cards and This week: drag the divider to fix it, double-click to pin
  // This week back to the bottom. Hammerspoon stores it and sends it along with the board.
  let gap = null;
  const applyGap = () => {
    if (gap === null) document.documentElement.style.removeProperty("--week-gap");
    else document.documentElement.style.setProperty("--week-gap", gap + "px");
  };
  const saveGap = () => { if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ gap: gap === null ? false : gap }); };
  window.update = b => { board = b; gap = typeof b.week_gap === "number" ? b.week_gap : null; applyGap(); render(); };
  document.addEventListener("pointerdown", e => {
    const bar = e.target.closest(".divider");
    if (!bar) return;
    e.preventDefault();
    const block = bar.closest(".week-block"), cards = document.querySelector(".cards");
    const startGap = block.getBoundingClientRect().top - cards.getBoundingClientRect().bottom, startY = e.clientY;
    bar.classList.add("dragging");
    const move = ev => { gap = Math.max(0, Math.round(startGap + ev.clientY - startY)); applyGap(); };
    const up = () => {
      bar.classList.remove("dragging");
      document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
      saveGap();
    };
    document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
  });
  document.addEventListener("dblclick", e => {
    if (!e.target.closest(".divider")) return;
    gap = null; applyGap(); saveGap();
  });
  if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ ready: true });
  document.addEventListener("click", e => {
    const link = e.target.closest("a[href]");
    if (link) {
      e.preventDefault();  // no in-page navigation, and no checkbox toggle from the enclosing label
      if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ url: link.href });
      return;
    }
    if (e.target.closest("#week")) {
      if (window.webkit && webkit.messageHandlers.board) webkit.messageHandlers.board.postMessage({ edit: true });
      return;
    }
    const box = e.target.closest(".todo input");
    if (box) {
      const row = box.closest(".todo"), t = (board.todos || [])[Number(row.dataset.i)];
      if (t && window.webkit && webkit.messageHandlers.board) {
        t.done = box.checked; row.classList.toggle("done", t.done);  // optimistic; the daemon re-reads the file
        webkit.messageHandlers.board.postMessage({ todo: { vault: t.vault, line: t.line } });
      }
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

-- Open this week's todo files in Neovide, or focus the one already open.
local function edit_todos()
  if neovide.find("todos") then neovide.open("todos", {}); return end
  local files = {}
  for line in (hs.execute(TODO_TOOL .. " ensure", true) or ""):gmatch("[^\n]+") do files[#files + 1] = line end
  if #files == 0 then hs.printf("task board: no todo files (is ~/.config/task-board/config.toml set?)"); return end
  if not neovide.open("todos", files, { "-O" }) then
    hs.task.new(TODO_TOOL, nil, { "edit" }):start()  -- terminal fallback inside todo-notes
  end
end
M.edit = edit_todos -- also used by raycast/todo-week.sh
function M.view() return state.view end

local function toggle_todo(todo)
  local line = tostring(math.tointeger(tonumber(todo.line)) or todo.line)
  local task = hs.task.new(TODO_TOOL, function(code, out, err)
    if code ~= 0 then hs.printf("task board: todo toggle failed (%s): %s", tostring(code), err or "") end
  end, { "toggle", tostring(todo.vault), line })
  task:setEnvironment({ HOME = HOME, PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" })
  if not task:start() then hs.printf("task board: could not start %s", TODO_TOOL) end
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

local function remember_frame()
  if not state.view then return end
  local f = state.view:frame()
  if f then hs.settings.set(FRAME_SETTING, { x = f.x, y = f.y, w = f.w, h = f.h }) end
end

-- Push board.json into the page. If the WebKit content process died (blank
-- white window after display sleep or a reload), the page has no `update`
-- function any more: reload the HTML once and push again.
local function push_board(retry)
  if not state.view or not state.board then return end
  state.board.week_gap = hs.settings.get(GAP_SETTING)
  local js = "typeof window.update === 'function' && (window.update(" .. hs.json.encode(state.board) .. "), true)"
  state.view:evaluateJavaScript(js, function(result, err)
    if result == true or retry then return end
    local reason = type(err) == "table" and (err.localizedDescription or err.message) or err
    hs.printf("task board: page lost (%s); reloading html", tostring(reason or "no update()"))
    state.view:html(PAGE)  -- the page posts {ready=true} when loaded, which pushes again
  end)
end

local function reload_board()
  state.board = read_board()
  push_board()
end

local function build_view()
  local saved = hs.settings.get(FRAME_SETTING)
  local frame = frame_on_some_screen(saved) and saved or default_frame()
  local controller = hs.webview.usercontent.new("board"):setCallback(function(message)
    local body = message and message.body
    if type(body) ~= "table" then return end
    if body.ready then push_board(true)
    elseif body.edit then edit_todos()
    elseif body.todo then toggle_todo(body.todo)
    elseif body.gap ~= nil then hs.settings.set(GAP_SETTING, body.gap or nil)  -- false clears it
    elseif body.url then hs.urlevent.openURL(body.url)
    elseif body.id then jump_to(body) end
  end)
  state.view = hs.webview.new(frame, { developerExtrasEnabled = false }, controller)
  -- A plain window (no `utility`, no `nonactivating`): panels float above
  -- other apps' windows and stay out of Cmd-Tab and Mission Control, which is
  -- exactly what the board must not do. Clicking it activates Hammerspoon,
  -- whose Dock icon is shown for that reason.
  local masks = hs.webview.windowMasks
  state.view:windowStyle(masks.titled | masks.closable | masks.resizable)
  state.view:windowTitle("Task Board")
  state.view:level(hs.drawing.windowLevels.normal)
  state.view:behaviorAsLabels({ "canJoinAllSpaces" })
  state.view:allowTextEntry(false)
  state.view:deleteOnClose(false)
  state.view:html(PAGE)  -- the page posts {ready=true} once loaded; that triggers the first push
  state.view:show()
  state.view:level(hs.drawing.windowLevels.normal)  -- showing a panel can re-raise its level
end

-- ⌘⇧B: hidden -> show on top; visible -> hide.
function M.toggle()
  if not state.view then build_view(); reload_board(); return end
  local window = state.view:hswindow()
  if window and window:isVisible() then
    remember_frame()
    state.view:hide()
  else
    state.view:show()
    state.view:bringToFront()
    push_board()
  end
end

function M.start()
  hs.dockicon.show()  -- lets the board be reached with Cmd-Tab like any app window
  build_view()
  state.board = read_board()  -- pushed once the page reports ready
  state.watcher = hs.pathwatcher.new(STATE_DIR, function(paths)
    for _, path in ipairs(paths) do
      if path:sub(-#"board.json") == "board.json" then reload_board(); return end
    end
  end):start()
  state.frame_timer = hs.timer.doEvery(30, remember_frame)
  state.hotkey = hs.hotkey.bind(TOGGLE_HOTKEY[1], TOGGLE_HOTKEY[2], M.toggle)
  state.edit_hotkey = hs.hotkey.bind(EDIT_HOTKEY[1], EDIT_HOTKEY[2], edit_todos)
  -- External entry points (Raycast, shell): open "hammerspoon://task-board?action=edit|toggle".
  hs.urlevent.bind("task-board", function(_, params)
    local action = params and params.action
    if action == "edit" then edit_todos() elseif action == "toggle" then M.toggle() end
  end)
  local previous = hs.shutdownCallback
  hs.shutdownCallback = function()
    M.stop()
    if previous then previous() end
  end
  return M
end

function M.stop()
  remember_frame()
  if state.watcher then state.watcher:stop() end
  if state.frame_timer then state.frame_timer:stop() end
  if state.hotkey then state.hotkey:delete() end
  if state.edit_hotkey then state.edit_hotkey:delete() end
  hs.urlevent.bind("task-board", nil)
  if state.view then state.view:delete() end
  state.view, state.watcher, state.frame_timer, state.hotkey = nil, nil, nil, nil
end

return M
