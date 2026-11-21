-- Non-activating floating preview HUD for completed tmux agent panes
-- (Codex, Claude Code, Antigravity). Watches ~/.cache/codex-tmux-status for
-- new unread completions, pops a compact corner card without stealing focus,
-- and provides ⌘⇧U / tmux <prefix>+u interactive Vim-style keyboard preview.

local M = {}

local HOME = assert(os.getenv("HOME"), "HOME is required")
local STATE_ROOT = HOME .. "/.cache/codex-tmux-status"
local SERVERS_DIR = STATE_ROOT .. "/servers"
local BACKEND = HOME .. "/GitHubRepos/dotfiles/tmux/agent_preview.py"
local PYTHON = "/opt/homebrew/bin/python3"
local PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
local HOTKEY = { { "cmd", "shift" }, "u" }
local AUTOHIDE_SECONDS = 18
local ROTATE_SECONDS = 8
local ROTATE_PASSES = 2
local MAX_ROTATE_TICKS = 8
local DEBOUNCE_SECONDS = 0.12
local TOP_MARGIN = 52
local RIGHT_MARGIN = 14
local COMPACT_SIZE = { w = 430, h = 290 }
local EXPANDED_SIZE = { w = 580, h = 450 }

local state = {
  started = false,
  webview = nil,
  usercontent = nil,
  page_ready = false,
  visible = false,
  expanded = false,
  interactive = false,
  hovered = false,
  mode = "unread", -- "unread" | "recent"
  items = {},
  index = 1,
  remaining_ticks = 0,
  progress_seq = 0,
  seen_ns = {},
  active_tasks = {},
  snapshot_job = nil,
  snapshot_queued = nil,
  debounce_timer = nil,
  autohide_timer = nil,
  watcher = nil,
  hotkey = nil,
  modal = nil,
  modal_active = false,
  previous_shutdown = nil,
  shutdown = nil,
  rendered_key = nil,
  rendered_expanded = nil,
}

local HTML = [[<!doctype html>
<meta charset="utf-8">
<style>
  @font-face { font-family: "BoardIcons"; src: local("CodexStatusSymbols-Regular"); unicode-range: U+E00B-U+E010; }
  :root {
    --bg: rgba(30, 35, 38, 0.96);
    --header: rgba(39, 46, 51, 0.98);
    --text: #d3c6aa;
    --muted: #9aa69d;
    --border: #3d484d;
    --green: #a7c080;
    --blue: #7fbbb3;
    --peach: #e69875;
    --yellow: #dbbc7f;
    --btn: rgba(56, 66, 73, 0.65);
    --btn-hover: rgba(75, 86, 92, 0.85);
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden; background: transparent; color: var(--text);
    font: 11px/1.4 "BoardIcons", "Maple Mono NF CN", "JetBrainsMono Nerd Font", Menlo, monospace;
  }
  #card {
    display: flex; flex-direction: column; width: 100%; height: 100%;
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
    overflow: hidden;
  }
  #card.interactive { border-color: var(--green); }
  .header {
    flex: none; padding: 6px 8px 0; background: var(--header);
    border-bottom: 1px solid var(--border); user-select: none;
  }
  .top-row { display: flex; align-items: center; gap: 5px; min-width: 0; }
  .dot { font-weight: 700; flex: none; color: var(--green); }
  .dot.recent { color: var(--muted); }
  .title {
    font-weight: 600; color: #ece4d1; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0;
  }
  .badge {
    flex: none; font-size: 9.5px; padding: 1px 5px; border-radius: 5px;
    background: #2f4145; color: var(--blue);
  }
  .badge.codex { background: #34412f; color: var(--green); }
  .badge.claude { background: #47362e; color: var(--peach); }
  .badge.antigravity { background: #4a4330; color: var(--yellow); }
  .age { flex: none; font-size: 9.5px; color: var(--muted); white-space: nowrap; }
  .nav { display: inline-flex; align-items: center; gap: 1px; flex: none; font-size: 10px; color: var(--text); font-weight: 600; }
  .nav[hidden], .progress-track[hidden], .footer[hidden], [hidden] { display: none !important; }
  .sub-row {
    display: flex; align-items: center; gap: 5px; margin: 4px 0 5px; min-width: 0;
  }
  .meta-inline {
    flex: 1; min-width: 0; font-size: 10px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  button {
    font: inherit; font-size: 10px; line-height: 1.2; color: var(--text);
    background: var(--btn); border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 6px; cursor: pointer; white-space: nowrap; flex: none;
  }
  button:hover { background: var(--btn-hover); }
  button:disabled { opacity: 0.45; cursor: default; }
  button.primary { background: var(--green); border-color: var(--green); color: #1e2326; font-weight: 600; }
  button.primary:hover { background: #b8cf93; }
  button.copied { border-color: var(--green); color: var(--green); }
  button.icon { padding: 1px 5px; border-color: transparent; background: transparent; color: var(--muted); }
  button.icon:hover { background: var(--btn); color: var(--text); }
  .progress-track {
    height: 1.5px; margin: 0 -8px; background: transparent; overflow: hidden;
  }
  .progress-bar {
    height: 100%; width: 100%; background: rgba(167, 192, 128, 0.55);
    transform-origin: left center;
  }
  @keyframes dwell-shrink { from { transform: scaleX(1); } to { transform: scaleX(0); } }
  #card:hover .progress-bar { animation-play-state: paused !important; }
  #output {
    flex: 1; min-height: 0; margin: 0; padding: 6px 9px;
    overflow-y: auto; overflow-x: hidden;
    white-space: pre-wrap; word-break: break-word;
    font: 11px/1.45 "Maple Mono NF CN", "JetBrainsMono Nerd Font", Menlo, monospace;
    color: var(--text);
  }
  #output.scrolled-down {
    -webkit-mask-image: linear-gradient(to bottom, transparent 0px, black 14px);
    mask-image: linear-gradient(to bottom, transparent 0px, black 14px);
  }
  a.out-link {
    color: inherit; text-decoration: underline;
    text-decoration-style: dotted; text-underline-offset: 2px; cursor: pointer;
  }
  a.out-link:hover { text-decoration-style: solid; filter: brightness(1.18); }
  #output::-webkit-scrollbar { width: 6px; }
  #output::-webkit-scrollbar-track { background: transparent; }
  #output::-webkit-scrollbar-thumb { background: #4f585e; border-radius: 3px; }
  .footer {
    flex: none; padding: 3px 8px; font-size: 9.5px; color: var(--muted);
    border-top: 1px solid var(--border); background: var(--header);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; user-select: none;
  }
</style>
<div id="card">
  <div class="header">
    <div class="top-row">
      <span id="dot" class="dot">✓</span>
      <span id="title" class="title"></span>
      <span id="source" class="badge"></span>
      <span id="age" class="age"></span>
      <span id="nav" class="nav" hidden>
        <button type="button" class="icon" data-action="prev" title="Previous ([)">‹</button>
        <span id="counter"></span>
        <button type="button" class="icon" data-action="next" title="Next (])">›</button>
      </span>
      <button type="button" data-action="hide" class="icon" title="Hide (keep unread badge)">×</button>
    </div>
    <div class="sub-row">
      <span id="meta" class="meta-inline"></span>
      <button type="button" data-action="copy" id="btn-copy" title="Copy response (y)">Copy</button>
      <button type="button" data-action="toggle-expand" id="btn-expand">Expand</button>
      <button type="button" data-action="ack" id="btn-ack">Mark Read</button>
      <button type="button" data-action="open" id="btn-open" class="primary">Open</button>
    </div>
    <div id="progress-track" class="progress-track" hidden><div id="progress-bar" class="progress-bar"></div></div>
  </div>
  <pre id="output"></pre>
  <div id="footer" class="footer" hidden>j/k scroll · e expand · y copy · [/] switch · r read · Enter open · Esc hide</div>
</div>
<script>
  const post = (msg) => {
    try { window.webkit.messageHandlers.agentPreview.postMessage(msg); } catch (_) {}
  };
  const card = document.getElementById("card");
  const dot = document.getElementById("dot");
  const titleEl = document.getElementById("title");
  const sourceEl = document.getElementById("source");
  const ageEl = document.getElementById("age");
  const navEl = document.getElementById("nav");
  const counterEl = document.getElementById("counter");
  const metaEl = document.getElementById("meta");
  const copyBtn = document.getElementById("btn-copy");
  const expandBtn = document.getElementById("btn-expand");
  const ackBtn = document.getElementById("btn-ack");
  const progressTrack = document.getElementById("progress-track");
  const progressBar = document.getElementById("progress-bar");
  const outputEl = document.getElementById("output");
  const footerEl = document.getElementById("footer");

  const syncScrollMask = () => {
    outputEl.classList.toggle("scrolled-down", outputEl.scrollTop > 4);
  };
  outputEl.addEventListener("scroll", syncScrollMask, { passive: true });

  document.addEventListener("click", (ev) => {
    const link = ev.target.closest("a.out-link");
    if (link) {
      ev.preventDefault();
      if (link.dataset.url) post({ action: "open-url", url: link.dataset.url });
      else if (link.dataset.path) post({ action: "open-path", path: link.dataset.path });
      return;
    }
    const btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    post({ action: btn.dataset.action });
  });
  card.addEventListener("mouseenter", () => post({ action: "hover", inside: true }));
  card.addEventListener("mouseleave", () => post({ action: "hover", inside: false }));

  let copyTimer = null;
  window.flashCopied = () => {
    copyBtn.textContent = "Copied ✓";
    copyBtn.classList.add("copied");
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      copyBtn.textContent = "Copy";
      copyBtn.classList.remove("copied");
    }, 1200);
  };

  let lastHtml = "";
  let lastProgressSeq = -1;
  window.updateHud = (data) => {
    if (!data || !data.item) return;
    const item = data.item;
    const expanded = Boolean(data.expanded);
    const interactive = Boolean(data.interactive);
    card.classList.toggle("interactive", interactive);
    dot.textContent = item.unread ? "✓" : "•";
    dot.classList.toggle("recent", !item.unread);

    const widx = item.window_index ? item.window_index + " " : "";
    const wtitle = widx + (item.window_name || item.session_name || item.pane_id || "");
    titleEl.textContent = wtitle;
    titleEl.title = wtitle;

    const src = item.source_label || "Agent";
    sourceEl.textContent = src;
    sourceEl.className = "badge " + src.toLowerCase();
    ageEl.textContent = [item.duration, item.age].filter(Boolean).join(" · ");

    const total = Number(data.total || 1);
    navEl.hidden = total <= 1;
    counterEl.textContent = (data.index || 1) + "/" + total;

    const repoBranch = [item.repo, item.branch].filter(Boolean).join(":");
    const summaryText = item.summary || repoBranch || "";
    metaEl.textContent = summaryText;
    metaEl.title = [repoBranch, item.summary].filter(Boolean).join(" · ");

    expandBtn.textContent = expanded ? "Collapse" : "Expand";
    ackBtn.hidden = !item.unread;
    footerEl.hidden = !interactive;

    const dwell = (!interactive && Number(data.dwell_seconds) > 0) ? Number(data.dwell_seconds) : 0;
    progressTrack.hidden = dwell <= 0;
    if (dwell > 0 && data.progress_seq !== lastProgressSeq) {
      lastProgressSeq = data.progress_seq;
      progressBar.style.animation = "none";
      void progressBar.offsetWidth;
      progressBar.style.animation = "dwell-shrink " + dwell + "s linear forwards";
    }

    const nextHtml = (expanded || interactive)
      ? (item.full_html || item.compact_html || "(no terminal output)")
      : (item.compact_html || "(no terminal output)");
    const prevHeight = outputEl.scrollHeight;
    const nearBottom = (outputEl.scrollTop + outputEl.clientHeight) >= (prevHeight - 24);
    if (nextHtml !== lastHtml || data.reset_scroll) {
      outputEl.innerHTML = nextHtml;
      lastHtml = nextHtml;
    }
    if (data.reset_scroll || nearBottom) {
      outputEl.scrollTop = outputEl.scrollHeight;
    }
    syncScrollMask();
  };

  window.scrollPreview = (dy) => {
    if (dy === "top") outputEl.scrollTop = 0;
    else if (dy === "bottom") outputEl.scrollTop = outputEl.scrollHeight;
    else outputEl.scrollBy(0, Number(dy) || 0);
  };

  window.addEventListener("DOMContentLoaded", () => post({ action: "ready" }));
</script>
]]

local function drain_task_output() return true end
local function clear_task_callbacks(task)
  if not task then return end
  if task.setCallback then task:setCallback(nil) end
  if task.setStreamingCallback then
    local running = task.isRunning and task:isRunning()
    task:setStreamingCallback(running and drain_task_output or nil)
  end
end

local function ensure_private_dirs()
  if not hs.fs.attributes(STATE_ROOT) then hs.fs.mkdir(STATE_ROOT) end
  if not hs.fs.attributes(SERVERS_DIR) then hs.fs.mkdir(SERVERS_DIR) end
end

local function stop_autohide()
  if state.autohide_timer then
    state.autohide_timer:stop()
    state.autohide_timer = nil
  end
end

local function exit_modal()
  if state.modal and state.modal_active then
    state.modal:exit()
    state.modal_active = false
  end
  state.interactive = false
end

local function enter_modal()
  if state.modal and not state.modal_active then
    state.modal:enter()
    state.modal_active = true
  end
  state.interactive = true
  stop_autohide()
end

local function active_screen()
  local win = hs.window.focusedWindow and hs.window.focusedWindow() or nil
  local scr = win and win:screen() or nil
  if not scr and hs.mouse and hs.mouse.getCurrentScreen then
    scr = hs.mouse.getCurrentScreen()
  end
  return scr or (hs.screen.mainScreen and hs.screen.mainScreen()) or hs.screen.primaryScreen()
end

local function target_frame(expanded)
  local sf = active_screen():frame()
  local w, h
  if expanded then
    w = math.min(EXPANDED_SIZE.w, math.floor(sf.w * 0.42))
    h = math.min(EXPANDED_SIZE.h, math.floor(sf.h * 0.55))
  else
    w = COMPACT_SIZE.w
    h = COMPACT_SIZE.h
  end
  w = math.max(280, math.min(w, sf.w - RIGHT_MARGIN * 2))
  h = math.max(150, math.min(h, sf.h - TOP_MARGIN - 24))
  return {
    x = sf.x + sf.w - w - RIGHT_MARGIN,
    y = sf.y + TOP_MARGIN,
    w = w,
    h = h,
  }
end

local function current_item()
  if #state.items == 0 then return nil end
  if state.index < 1 then state.index = 1 end
  if state.index > #state.items then state.index = #state.items end
  return state.items[state.index]
end

local function push_view(reset_scroll)
  local item = current_item()
  if not item or not state.webview then return end
  if state.webview.frame then
    state.webview:frame(target_frame(state.expanded))
  end
  if not state.page_ready then return end
  local should_reset = reset_scroll
    or (state.rendered_key ~= item.key)
    or (state.rendered_expanded ~= state.expanded)
  state.rendered_key = item.key
  state.rendered_expanded = state.expanded
  local dwell = 0
  if not state.interactive and state.visible then
    dwell = (#state.items > 1) and ROTATE_SECONDS or AUTOHIDE_SECONDS
  end
  local payload = {
    item = item,
    index = state.index,
    total = #state.items,
    expanded = state.expanded,
    interactive = state.interactive,
    dwell_seconds = dwell,
    progress_seq = state.progress_seq,
    reset_scroll = should_reset,
  }
  state.webview:evaluateJavaScript("window.updateHud(" .. hs.json.encode(payload) .. ")")
end

local function reset_dwell()
  if #state.items > 1 then
    state.remaining_ticks = math.min(MAX_ROTATE_TICKS, #state.items * ROTATE_PASSES)
  else
    state.remaining_ticks = 1
  end
end

local function schedule_autohide()
  stop_autohide()
  if not state.visible or state.interactive or state.hovered then return end
  if (state.remaining_ticks or 0) <= 0 then reset_dwell() end
  state.progress_seq = (state.progress_seq or 0) + 1
  local delay = (#state.items > 1) and ROTATE_SECONDS or AUTOHIDE_SECONDS
  state.autohide_timer = hs.timer.doAfter(delay, function()
    state.autohide_timer = nil
    if not state.visible or state.interactive or state.hovered then return end
    state.remaining_ticks = (state.remaining_ticks or 1) - 1
    if state.remaining_ticks <= 0 or #state.items <= 1 then
      M.hide()
    else
      state.index = (state.index % #state.items) + 1
      schedule_autohide()
      push_view(true)
    end
  end)
end

local function scroll_preview(dy)
  if not state.visible or not state.webview or not state.page_ready then return end
  local arg = type(dy) == "string" and hs.json.encode(dy) or tostring(dy)
  state.webview:evaluateJavaScript("window.scrollPreview(" .. arg .. ")")
end

local function handle_message(msg)
  local body = (type(msg) == "table" and type(msg.body) == "table") and msg.body or msg
  if type(body) ~= "table" then return end
  local action = body.action
  if action == "ready" then
    state.page_ready = true
    if state.visible then push_view(true) end
  elseif action == "hover" then
    state.hovered = body.inside == true
    if state.hovered then
      stop_autohide()
    else
      schedule_autohide()
      push_view(false)
    end
  elseif action == "copy" then
    M.copy_current()
  elseif action == "open-url" then
    if type(body.url) == "string" and body.url:match("^https?://") then
      hs.urlevent.openURL(body.url)
    end
  elseif action == "open-path" then
    M.open_path(body.path)
  elseif action == "toggle-expand" then
    M.toggle_expand()
  elseif action == "ack" then
    M.mark_read()
  elseif action == "open" then
    M.open_current()
  elseif action == "hide" then
    M.hide()
  elseif action == "prev" then
    M.prev_item()
  elseif action == "next" then
    M.next_item()
  end
end

local function ensure_webview()
  if state.webview then return state.webview end
  local ucc = hs.webview.usercontent.new("agentPreview")
  ucc:setCallback(handle_message)
  local wv = hs.webview.new(target_frame(false), { developerExtrasEnabled = false }, ucc)
  wv:windowStyle({ "borderless", "nonactivating" })
  wv:level(hs.drawing.windowLevels.floating)
  wv:behaviorAsLabels({ "canJoinAllSpaces", "fullScreenAuxiliary" })
  if wv.allowTextEntry then wv:allowTextEntry(false) end
  if wv.transparent then wv:transparent(true) end
  if wv.shadow then wv:shadow(true) end
  if wv.deleteOnClose then wv:deleteOnClose(false) end
  if wv.navigationCallback then
    wv:navigationCallback(function(ev)
      if ev == "didFinish" then
        state.page_ready = true
        if state.visible then push_view(true) end
      end
    end)
  end
  state.page_ready = false
  wv:html(HTML)
  state.usercontent = ucc
  state.webview = wv
  return wv
end

local function show_hud(interactive, expanded)
  if #state.items == 0 then return end
  state.visible = true
  state.expanded = expanded == true
  if interactive then
    enter_modal()
  else
    exit_modal()
  end
  local wv = ensure_webview()
  if not state.interactive then
    reset_dwell()
    schedule_autohide()
  end
  push_view(true)
  wv:show()
end

function M.hide()
  stop_autohide()
  exit_modal()
  state.visible = false
  state.hovered = false
  state.remaining_ticks = 0
  if state.webview then state.webview:hide() end
end

local function run_backend(args, on_done, expect_json)
  local arguments = { BACKEND }
  for _, v in ipairs(args) do arguments[#arguments + 1] = v end
  local job, exit_code, finish_timer
  local finished = false
  local output, errors, final_output, final_errors = "", "", "", ""
  local function cleanup()
    if finish_timer then finish_timer:stop(); finish_timer = nil end
    local done_task = job
    job = nil
    if done_task then
      state.active_tasks[done_task] = nil
      clear_task_callbacks(done_task)
    end
    output, errors, final_output, final_errors = "", "", "", ""
  end
  local function finish(timed_out)
    if finished or not job or exit_code == nil then return end
    local out, err = output .. final_output, errors .. final_errors
    local parsed = nil
    if expect_json and exit_code == 0 and not timed_out then
      if out == "" or out:sub(-1) ~= "\n" then return end
      local ok, data = pcall(hs.json.decode, out)
      if not (ok and type(data) == "table") then return end
      parsed = data
    end
    finished = true
    local code = exit_code
    cleanup()
    if on_done then on_done(code, out, err, parsed) end
  end
  job = hs.task.new(PYTHON, function(code, out, err)
    if finished or not job then return end
    exit_code, final_output, final_errors = code, out or "", err or ""
    finish(false)
    if not finished and job then
      finish_timer = hs.timer.doAfter(1, function() finish(true) end)
    end
  end, function(_, out, err)
    if finished or not job then return false end
    output, errors = output .. (out or ""), errors .. (err or "")
    finish(false)
    return true
  end, arguments)
  if not job then
    if on_done then on_done(1, "", "could not create hs.task", nil) end
    return nil
  end
  state.active_tasks[job] = cleanup
  if job.setEnvironment then job:setEnvironment({ HOME = HOME, PATH = PATH_ENV }) end
  if not job:start() then
    finished = true
    cleanup()
    if on_done then on_done(1, "", "could not start agent_preview.py", nil) end
    return nil
  end
  return job
end

local function index_of_key(items, key)
  if not key then return nil end
  for i, item in ipairs(items) do
    if item.key == key then return i end
  end
  return nil
end

local function apply_snapshot(data, mode)
  local unread = type(data.unread) == "table" and data.unread or {}
  local recent = type(data.recent) == "table" and data.recent or {}

  local newest_new = nil
  for _, item in ipairs(unread) do
    local ns = tonumber(item.completed_at_ns) or 0
    local prev = state.seen_ns[item.key] or 0
    if ns > prev and not newest_new then
      newest_new = item
    end
  end
  for _, item in ipairs(unread) do
    local ns = tonumber(item.completed_at_ns) or 0
    if ns > (state.seen_ns[item.key] or 0) then
      state.seen_ns[item.key] = ns
    end
  end

  if mode == "seed" then
    return
  end

  if mode == "toggle" then
    if #unread > 0 then
      state.mode = "unread"
      state.items = unread
      state.index = 1
      show_hud(true, false)
    elseif #recent > 0 then
      state.mode = "recent"
      state.items = recent
      state.index = 1
      show_hud(true, false)
    else
      hs.alert.show("No completed agent output", 1.2)
    end
    return
  end

  -- Watcher-triggered snapshot
  if state.visible then
    local prev_item = current_item()
    local prev_key = prev_item and prev_item.key or nil
    if state.mode == "unread" then
      if #unread == 0 then
        if not state.interactive then
          M.hide()
        else
          state.items = recent
          state.mode = #recent > 0 and "recent" or "unread"
          state.index = 1
          if #state.items == 0 then M.hide() else push_view(false) end
        end
        return
      end
      state.items = unread
      if newest_new then
        state.index = index_of_key(unread, newest_new.key) or 1
        reset_dwell()
        schedule_autohide()
        push_view(true)
      else
        state.index = index_of_key(unread, prev_key) or math.min(state.index, #unread)
        push_view(false)
      end
    end
    return
  end

  if newest_new and #unread > 0 then
    state.mode = "unread"
    state.items = unread
    state.index = index_of_key(unread, newest_new.key) or 1
    show_hud(false, false)
  end
end

local function fetch_snapshot(mode)
  if state.snapshot_job then
    -- Interactive toggle always takes precedence over a background poll.
    if mode == "toggle" or not state.snapshot_queued then
      state.snapshot_queued = mode
    end
    return
  end
  local started_job = run_backend({ "snapshot" }, function(code, out, err, parsed)
    state.snapshot_job = nil
    if code == 0 then
      local data = parsed
      if type(data) ~= "table" and out ~= "" then
        local ok, decoded = pcall(hs.json.decode, out)
        if ok and type(decoded) == "table" then data = decoded end
      end
      if type(data) == "table" then
        apply_snapshot(data, mode)
      end
    elseif err ~= "" then
      hs.printf("agent_preview snapshot error: %s", err)
    end
    local queued = state.snapshot_queued
    state.snapshot_queued = nil
    if queued then fetch_snapshot(queued) end
  end, true)
  if started_job then state.snapshot_job = started_job end
end

local function trigger_debounce()
  if not state.started then return end
  if state.debounce_timer then state.debounce_timer:stop() end
  state.debounce_timer = hs.timer.doAfter(DEBOUNCE_SECONDS, function()
    state.debounce_timer = nil
    if state.started then fetch_snapshot("watch") end
  end)
end

function M.toggle_interactive()
  if state.visible and state.interactive then
    M.hide()
    return
  end
  if state.visible and not state.interactive then
    enter_modal()
    push_view(true)
    return
  end
  fetch_snapshot("toggle")
end

function M.toggle_expand()
  if not state.visible then return end
  state.expanded = not state.expanded
  if not state.interactive then reset_dwell(); schedule_autohide() end
  push_view(true)
end

function M.copy_current()
  local item = current_item()
  if not item then return end
  local text = item.plain_text or ""
  if text ~= "" and hs.pasteboard and hs.pasteboard.setContents then
    hs.pasteboard.setContents(text)
  end
  if state.visible and state.webview and state.page_ready then
    state.webview:evaluateJavaScript("window.flashCopied && window.flashCopied()")
  end
  if not state.interactive then reset_dwell(); schedule_autohide(); push_view(false) end
end

function M.prev_item()
  if #state.items <= 1 then return end
  state.index = ((state.index - 2) % #state.items) + 1
  if not state.interactive then reset_dwell(); schedule_autohide() end
  push_view(true)
end

function M.next_item()
  if #state.items <= 1 then return end
  state.index = (state.index % #state.items) + 1
  if not state.interactive then reset_dwell(); schedule_autohide() end
  push_view(true)
end

function M.mark_read()
  local item = current_item()
  if not item then
    M.hide()
    return
  end
  local sock, pid = item.tmux_socket, item.pane_id
  if item.unread then
    table.remove(state.items, state.index)
    if state.index > #state.items then state.index = #state.items end
    run_backend({ "ack", "--tmux-socket", sock, "--pane-id", pid })
  end
  if #state.items == 0 then
    M.hide()
  else
    if not state.interactive then reset_dwell(); schedule_autohide() end
    push_view(true)
  end
end

function M.open_path(raw_path)
  local item = current_item()
  if item and type(raw_path) == "string" and raw_path ~= "" then
    run_backend({ "open-path", "--cwd", item.cwd or "", "--path", raw_path })
  end
end

function M.open_current()
  local item = current_item()
  if not item then
    M.hide()
    return
  end
  local sock, sess, wid, pid = item.tmux_socket, item.session_name, item.window_id, item.pane_id
  if item.unread then
    table.remove(state.items, state.index)
    if state.index > #state.items then state.index = #state.items end
  end
  M.hide()
  run_backend({
    "open",
    "--tmux-socket", sock,
    "--session", sess,
    "--window-id", wid,
    "--pane-id", pid,
  })
end

local function build_modal()
  local modal = hs.hotkey.modal.new()
  modal:bind({}, "j", function() scroll_preview(72) end, nil, function() scroll_preview(72) end)
  modal:bind({}, "down", function() scroll_preview(72) end, nil, function() scroll_preview(72) end)
  modal:bind({}, "k", function() scroll_preview(-72) end, nil, function() scroll_preview(-72) end)
  modal:bind({}, "up", function() scroll_preview(-72) end, nil, function() scroll_preview(-72) end)
  modal:bind({}, "d", function() scroll_preview(280) end, nil, function() scroll_preview(280) end)
  modal:bind({ "ctrl" }, "d", function() scroll_preview(280) end, nil, function() scroll_preview(280) end)
  modal:bind({}, "u", function() scroll_preview(-280) end, nil, function() scroll_preview(-280) end)
  modal:bind({ "ctrl" }, "u", function() scroll_preview(-280) end, nil, function() scroll_preview(-280) end)
  modal:bind({}, "g", function() scroll_preview("top") end)
  modal:bind({ "shift" }, "g", function() scroll_preview("bottom") end)
  modal:bind({}, "e", M.toggle_expand)
  modal:bind({}, "space", M.toggle_expand)
  modal:bind({}, "y", M.copy_current)
  modal:bind({}, "]", M.next_item)
  modal:bind({}, "tab", M.next_item)
  modal:bind({}, "[", M.prev_item)
  modal:bind({ "shift" }, "tab", M.prev_item)
  modal:bind({}, "r", M.mark_read)
  modal:bind({}, "return", M.open_current)
  modal:bind({}, "o", M.open_current)
  modal:bind({}, "escape", M.hide)
  modal:bind({}, "q", M.hide)
  return modal
end

function M.status()
  return {
    started = state.started,
    visible = state.visible,
    expanded = state.expanded,
    interactive = state.interactive,
    mode = state.mode,
    count = #state.items,
    index = state.index,
  }
end

function M.start()
  if state.started then return M end
  state.started = true
  ensure_private_dirs()
  state.modal = build_modal()
  state.hotkey = hs.hotkey.bind(HOTKEY[1], HOTKEY[2], M.toggle_interactive)
  state.watcher = hs.pathwatcher.new(STATE_ROOT, function(paths)
    if type(paths) ~= "table" then return end
    for _, path in ipairs(paths) do
      if type(path) == "string" and (path:match("pane%-%d+%.json$") or path:match("pane%-%d+%.ack$")) then
        trigger_debounce()
        return
      end
    end
  end):start()
  hs.urlevent.bind("agent-preview", function(_, params)
    local action = params and params.action or "toggle"
    if action == "toggle" then M.toggle_interactive()
    elseif action == "hide" then M.hide()
    elseif action == "ack" then M.mark_read()
    elseif action == "open" then M.open_current()
    elseif action == "next" then M.next_item()
    elseif action == "prev" then M.prev_item()
    end
  end)
  local previous = hs.shutdownCallback
  state.previous_shutdown = previous
  state.shutdown = function()
    M.stop()
    if previous then previous() end
  end
  hs.shutdownCallback = state.shutdown
  fetch_snapshot("seed")
  return M
end

function M.stop()
  state.started = false
  stop_autohide()
  if state.debounce_timer then state.debounce_timer:stop(); state.debounce_timer = nil end
  exit_modal()
  if state.modal then state.modal:delete(); state.modal = nil end
  if state.hotkey then state.hotkey:delete(); state.hotkey = nil end
  if state.watcher then state.watcher:stop(); state.watcher = nil end
  hs.urlevent.bind("agent-preview", nil)
  for _, cleanup in pairs(state.active_tasks) do cleanup() end
  state.active_tasks = {}
  state.snapshot_job = nil
  state.snapshot_queued = nil
  if state.usercontent and state.usercontent.setCallback then
    state.usercontent:setCallback(nil)
  end
  if state.webview then
    if state.webview.navigationCallback then state.webview:navigationCallback(nil) end
    state.webview:delete()
    state.webview = nil
  end
  state.usercontent = nil
  state.page_ready = false
  state.visible = false
  state.items = {}
  if hs.shutdownCallback == state.shutdown then hs.shutdownCallback = state.previous_shutdown end
  state.shutdown, state.previous_shutdown = nil, nil
end

return M
