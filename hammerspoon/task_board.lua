-- Task board: shows ~/.cache/tmux-task-board/board.json (written by
-- tmux/task-board.py) in a draggable, resizable utility window rendered as
-- HTML. Clicking a card selects that tmux window and brings Ghostty forward.
--
-- The window is a real NSWindow (hs.webview): drag it to any screen, resize
-- it, close it with the title-bar button and bring it back with ⌘⇧B. Its
-- frame is remembered across reloads.

local M = {}

local HOME = assert(os.getenv("HOME"), "HOME is required")
local STATE_DIR = HOME .. "/.cache/tmux-task-board"
local BOARD_PATH = STATE_DIR .. "/board.json"
local FRAME_SETTING = "taskBoardFrame"
-- Default placement when no frame is remembered: right half of the first
-- matching screen, else any non-primary screen.
local SCREEN_NAMES = { "T270LG" }
local TOGGLE_HOTKEY = { { "cmd", "shift" }, "b" }

local state = { view = nil, watcher = nil, board = nil, hotkey = nil, frame_timer = nil }
M._state = state -- for `hs -c` inspection

local PAGE = [[
<!doctype html>
<meta charset="utf-8">
<style>
  @font-face { font-family: "BoardIcons"; src: local("CodexStatusSymbols-Regular"); unicode-range: U+E00B-E00D; }
  :root {
    --bg: #1e2326; --card: #272e33; --card-hover: #2e373c; --text: #d3c6aa; --muted: #859289; --tag: #a7b0a8;
    --title: #dbbc7f; --attention: #dbbc7f; --error: #e67e80; --review: #a7c080; --working: #7fbbb3; --parked: #4f585e;
  }
  html, body { margin: 0; background: var(--bg); color: var(--text);
    font: 13px/1.5 "BoardIcons", "Maple Mono NF CN", "JetBrainsMono Nerd Font", "PingFang SC", monospace; }
  header { position: sticky; top: 0; background: var(--bg); padding: 10px 14px 6px; display: flex; align-items: baseline; gap: 12px;
    border-bottom: 1px solid #2e373c; z-index: 1; }
  header h1 { font-size: 15px; margin: 0; color: var(--title); font-weight: 600; }
  header .counts { display: flex; gap: 10px; font-size: 12px; }
  header .counts span::before { content: "●"; margin-right: 4px; }
  header .updated { margin-left: auto; color: var(--muted); font-size: 11px; }
  main { padding: 4px 10px 12px; }
  h2 { font-size: 11px; font-weight: 600; letter-spacing: .08em; margin: 10px 2px 4px; text-transform: uppercase; }
  h2 .n { color: var(--muted); font-weight: 400; margin-left: 6px; }
  /* Wider windows get more columns automatically. */
  .group { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 5px; }
  .card { background: var(--card); border-left: 3px solid var(--parked); border-radius: 5px; padding: 5px 9px; cursor: pointer; }
  .card:hover { background: var(--card-hover); }
  .meta { display: flex; align-items: baseline; gap: 6px; font-size: 11px; color: var(--tag); margin-bottom: 1px; }
  .meta .idx { color: var(--muted); }
  .meta .git { color: var(--muted); }
  .meta .git::before { content: "·"; margin-right: 6px; }
  .meta .age { margin-left: auto; color: var(--muted); white-space: nowrap; }
  .task + .task { margin-top: 3px; padding-top: 3px; border-top: 1px dashed #3a444a; }
  .summary { color: var(--text); }
  .next { color: var(--muted); }
  .next::before { content: "→ "; }
  /* One line per task: "summary → next"; the next step wraps under it only when the card is narrow. */
  .task { display: flex; gap: 8px; flex-wrap: wrap; }
  .next { white-space: nowrap; }
  .attention .next { color: var(--attention); } .error .next { color: var(--error); }
  .review .next { color: var(--review); } .working .next { color: var(--working); }
  .attention { border-left-color: var(--attention); } .error { border-left-color: var(--error); }
  .review { border-left-color: var(--review); } .working { border-left-color: var(--working); }
  .parked .summary { color: var(--muted); font-size: 13px; } .parked .next { display: none; }
  .stale .summary, .stale .next { opacity: .6; }
  h2.attention, .counts .attention { color: var(--attention); } h2.review, .counts .review { color: var(--review); }
  h2.working, .counts .working { color: var(--working); } h2.parked, .counts .parked { color: var(--muted); }
  .titles { color: var(--muted); }
</style>
<header><h1>Task Board</h1><div class="counts" id="counts"></div><div class="updated" id="updated"></div></header>
<main id="main"></main>
<script>
  const GROUPS = ["attention", "review", "working", "parked"];
  const LABEL = { attention: "Needs you", review: "Review", working: "Working", parked: "Parked" };
  const STATE = { error: "error", waiting: "waiting", done: "done", running: "running", idle: "idle" };
  let board = { windows: [] };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const age = epoch => {
    if (!epoch) return "";
    const s = Math.floor(Date.now() / 1000 - epoch);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " d ago";
  };
  const stripIcon = name => { const m = /^(\S+)\s+(.*)$/.exec(name || ""); return m && !/\w/.test(m[1]) ? m[2] : (name || ""); };
  const shorten = (s, n) => (s && s.length > n) ? s.slice(0, n - 1) + "…" : (s || "");
  function card(w, multi) {
    const tasks = (w.tasks || []).map(t => `<div class="task"><div class="summary">${esc(t.summary)}</div><div class="next">${esc(t.next)}</div></div>`);
    const titles = (!tasks.length && w.panes && w.panes.length)
      ? `<div class="titles">${esc(w.panes.map(p => p.title).filter(Boolean).join("  ·  "))}</div>` : "";
    const idx = (multi ? shorten(w.session, 12) + ":" : "") + w.index;
    const cls = [w.group, w.state === "error" ? "error" : "", w.summary_stale ? "stale" : ""].join(" ");
    return `<div class="card ${cls}" data-id="${esc(w.id)}" data-session="${esc(w.session)}">
      <div class="meta"><span class="idx">${esc(idx)}</span><span class="icon">${esc(w.icon)}</span><span class="name">${esc(stripIcon(w.name))}</span>
        ${w.repo ? `<span class="git">${esc(w.repo)}${w.branch ? " @ " + esc(w.branch) : ""}</span>` : ""}
        <span class="age">${age(w.activity_at)}</span></div>
      ${w.group === "parked" ? (tasks[0] || titles) : (tasks.join("") || titles)}
    </div>`;
  }
  function render() {
    const wins = board.windows || [];
    const multi = new Set(wins.map(w => w.session)).size > 1;
    const counts = {};
    wins.forEach(w => counts[w.group] = (counts[w.group] || 0) + 1);
    document.getElementById("counts").innerHTML = GROUPS.filter(g => counts[g]).map(g => `<span class="${g}">${LABEL[g]} ${counts[g]}</span>`).join("");
    document.getElementById("updated").textContent = board.generated_at ? "Updated " + new Date(board.generated_at * 1000).toTimeString().slice(0, 5) : "";
    document.getElementById("main").innerHTML = GROUPS.filter(g => counts[g]).map(g =>
      `<h2 class="${g}">${LABEL[g]}<span class="n">${counts[g]}</span></h2><div class="group">` + wins.filter(w => w.group === g).map(w => card(w, multi)).join("") + `</div>`).join("");
  }
  window.update = b => { board = b; render(); };
  document.addEventListener("click", e => {
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

local function push_board()
  if not state.view or not state.board then return end
  state.view:evaluateJavaScript("window.update(" .. hs.json.encode(state.board) .. ")")
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
    if type(body) == "table" and body.id then jump_to(body) end
  end)
  state.view = hs.webview.new(frame, { developerExtrasEnabled = false }, controller)
  local masks = hs.webview.windowMasks
  state.view:windowStyle(masks.titled | masks.closable | masks.resizable | masks.utility | masks.nonactivating)
  state.view:windowTitle("Task Board")
  state.view:level(hs.drawing.windowLevels.normal)
  state.view:behaviorAsLabels({ "canJoinAllSpaces", "stationary" })
  state.view:allowTextEntry(false)
  state.view:deleteOnClose(false)
  state.view:html(PAGE)
  -- The page needs a moment to load before window.update exists.
  hs.timer.doAfter(0.3, push_board)
  state.view:show()
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
  build_view()
  reload_board()
  state.watcher = hs.pathwatcher.new(STATE_DIR, function(paths)
    for _, path in ipairs(paths) do
      if path:sub(-#"board.json") == "board.json" then reload_board(); return end
    end
  end):start()
  state.frame_timer = hs.timer.doEvery(30, remember_frame)
  state.hotkey = hs.hotkey.bind(TOGGLE_HOTKEY[1], TOGGLE_HOTKEY[2], M.toggle)
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
  if state.view then state.view:delete() end
  state.view, state.watcher, state.frame_timer, state.hotkey = nil, nil, nil, nil
end

return M
