local M = {}

local HOME = assert(os.getenv("HOME"), "HOME is required")
local PROTOCOL_VERSION = 1
local CACHE_DIR = HOME .. "/.cache/edit-anywhere"
local SESSIONS_DIR = CACHE_DIR .. "/sessions"
local FRONTEND_LOCK_DIR = CACHE_DIR .. "/frontend.lock"
local OWNER_PATH = FRONTEND_LOCK_DIR .. "/owner.json"
local FIFO_PATH = CACHE_DIR .. "/quick-terminal.fifo"
local DISPATCHER_PATH = HOME .. "/.local/bin/edit-anywhere-quick-terminal"
local SERVER_PATH = HOME .. "/.local/bin/edit-anywhere-server"
local SERVER_PID_PATH = CACHE_DIR .. "/server/nvim.pid"
local OCR_BINARY = CACHE_DIR .. "/edit-anywhere-ocr-bin"
local OCR_FALLBACK = HOME .. "/.local/bin/edit-anywhere-ocr"
local QUICK_TERMINAL_TITLE = "__EDIT_ANYWHERE_QUICK_TERMINAL__"
local QUICK_TERMINAL_SESSION_TITLE = "__EDIT_ANYWHERE_SESSION__"
local QUICK_TERMINAL_READY_TITLE = "EDIT_ANYWHERE_READY:"
local LEASE_MS = 3000
local RECLAIM_GRACE_MS = 2000
local WRITEBACK_TIMEOUT_SECONDS = 2
local MAX_JSON_BYTES = 1024 * 1024
local MAX_OUTPUT_BYTES = 8 * 1024 * 1024
local CURRENT_UID = tonumber((hs.execute("/usr/bin/id -u") or ""):match("%d+"))

local VALID_OWNER_STAGES = {
  claimed = true,
  request_published = true,
  accepted = true,
  ui_ready = true,
  suspended = true,
  recovery_required = true,
  terminal = true,
  paste_intent = true,
  pasted = true,
  clipboard_only = true,
}

local VALID_RUNTIME_STATES = {
  preparing = true,
  waiting_ui = true,
  editing = true,
  commit_pending = true,
  cancel_pending = true,
  detaching = true,
  suspended = true,
  recovery_required = true,
  committed = true,
  cancelled = true,
  failed = true,
  degraded = true,
}

local ACTIVE_RUNTIME_STAGES = {
  preparing = "accepted",
  waiting_ui = "accepted",
  editing = "ui_ready",
  commit_pending = "ui_ready",
  cancel_pending = "ui_ready",
  detaching = "ui_ready",
  suspended = "suspended",
  recovery_required = "recovery_required",
}

local state = {
  instance_uuid = hs.host.uuid(),
  sessions = {},
  hotkey = nil,
}

local function now_ms()
  if hs.timer.secondsSinceEpoch then return math.floor(hs.timer.secondsSinceEpoch() * 1000) end
  return os.time() * 1000
end

local function elapsed_ms(started_at)
  if not started_at then return nil end
  return (hs.timer.absoluteTime() - started_at) / 1000000
end

local function shell_quote(value)
  return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end

local function command_ok(command)
  local _, ok = hs.execute(command)
  return ok == true
end

local function ensure_private_dir(path)
  local existing = hs.fs.symlinkAttributes(path)
  if existing and existing.mode ~= "directory" then return false end
  if not existing and not command_ok("/bin/mkdir -p " .. shell_quote(path)) then return false end
  return command_ok("/bin/chmod 700 " .. shell_quote(path))
end

local function private_directory(path)
  local attributes = hs.fs.symlinkAttributes(path)
  return attributes ~= nil and attributes.mode == "directory"
    and CURRENT_UID ~= nil and attributes.uid == CURRENT_UID
    and attributes.permissions == "rwx------"
end

local function private_cache_parent(path)
  local prefix = CACHE_DIR .. "/"
  if type(path) ~= "string" or path:sub(1, #prefix) ~= prefix then return false end
  if not private_directory(CACHE_DIR) then return false end
  local relative = path:sub(#prefix + 1)
  local components = {}
  for component in relative:gmatch("[^/]+") do
    if component == "." or component == ".." then return false end
    table.insert(components, component)
  end
  if #components == 0 then return false end
  local current = CACHE_DIR
  for index = 1, #components - 1 do
    current = current .. "/" .. components[index]
    if not private_directory(current) then return false end
  end
  return true
end

local function chmod_private(path)
  command_ok("/bin/chmod 600 " .. shell_quote(path))
end

local function read_file(path, maximum_bytes)
  if not private_cache_parent(path) then return nil end
  local attributes = hs.fs.symlinkAttributes(path)
  if not attributes or attributes.mode ~= "file" then return nil end
  if CURRENT_UID == nil or attributes.uid ~= CURRENT_UID then return nil end
  if attributes.permissions ~= "rw-------" then return nil end
  if attributes.size and attributes.size > (maximum_bytes or MAX_JSON_BYTES) then return nil end
  local file = io.open(path, "rb")
  if not file then return nil end
  local contents = file:read("*a")
  file:close()
  if type(contents) ~= "string" or #contents > (maximum_bytes or MAX_JSON_BYTES) then return nil end
  return contents
end

local function write_file(path, contents)
  if not private_cache_parent(path) then return false, "unsafe parent directory" end
  local file, err = io.open(path, "wb")
  if not file then return false, err end
  local ok, write_err = file:write(contents)
  file:close()
  if not ok then return false, write_err end
  chmod_private(path)
  return true
end

local function atomic_write(path, contents)
  local temporary = path .. ".tmp." .. hs.host.uuid()
  local ok, err = write_file(temporary, contents)
  if ok then ok, err = os.rename(temporary, path) end
  if not ok then os.remove(temporary) end
  return ok, err
end

local function encode_json(value)
  local ok, result = pcall(hs.json.encode, value, true)
  if not ok then return nil, result end
  return result
end

local function atomic_write_json(path, value)
  local encoded, err = encode_json(value)
  if not encoded then return false, err end
  return atomic_write(path, encoded .. "\n")
end

local function read_json(path)
  local contents = read_file(path, MAX_JSON_BYTES)
  if not contents then return nil end
  local ok, result = pcall(hs.json.decode, contents)
  if not ok or type(result) ~= "table" then return nil end
  return result
end

local function is_integer(value)
  return type(value) == "number" and value == math.floor(value)
end

local function has_only_keys(value, names)
  if type(value) ~= "table" then return false end
  local allowed = {}
  for _, name in ipairs(names) do allowed[name] = true end
  for name in pairs(value) do if not allowed[name] then return false end end
  return true
end

local function valid_session_id(value)
  return type(value) == "string" and value:match("^%d%d%d%d%d%d%d%d%-%d%d%d%d%d%d%-%x%x%x%x%x%x%x%x$") ~= nil
end

local function valid_token(value)
  return type(value) == "string" and #value >= 22 and #value <= 256 and value:match("^[%w_%-]+$") ~= nil
end

local function valid_digest(value)
  return type(value) == "string" and #value == 64 and value:match("^[0-9a-fA-F]+$") ~= nil
end

local function valid_identity_string(value)
  return type(value) == "string" and #value >= 1 and #value <= 256
    and value:match("^[%w_%-]+$") ~= nil
end

local function valid_owner(owner)
  return has_only_keys(owner, {
    "protocol_version", "hammerspoon_instance_uuid", "session_id", "nonce", "stage",
    "created_at_unix_ms", "lease_expires_at_unix_ms",
  })
    and owner.protocol_version == PROTOCOL_VERSION
    and valid_identity_string(owner.hammerspoon_instance_uuid)
    and valid_session_id(owner.session_id)
    and valid_token(owner.nonce)
    and VALID_OWNER_STAGES[owner.stage] == true
    and is_integer(owner.created_at_unix_ms) and owner.created_at_unix_ms > 0
    and is_integer(owner.lease_expires_at_unix_ms)
    and owner.lease_expires_at_unix_ms >= owner.created_at_unix_ms
end

local function valid_request(request, owner)
  if not has_only_keys(request, {
      "protocol_version", "session_id", "nonce", "created_at_unix_ms", "expires_at_unix_ms",
      "editor", "context", "source_window",
    }) or request.protocol_version ~= PROTOCOL_VERSION
    or request.session_id ~= owner.session_id or request.nonce ~= owner.nonce
    or not is_integer(request.created_at_unix_ms) or not is_integer(request.expires_at_unix_ms)
    or request.created_at_unix_ms ~= owner.created_at_unix_ms
    or request.expires_at_unix_ms <= request.created_at_unix_ms then return false end
  local editor, context, source = request.editor, request.context, request.source_window
  return has_only_keys(editor, { "filetype", "cursor", "start_insert" }) and editor.filetype == "markdown"
    and (editor.cursor == "start" or editor.cursor == "end") and type(editor.start_insert) == "boolean"
    and has_only_keys(context, { "source", "token", "relative_path" }) and context.source == "window-ocr"
    and valid_token(context.token) and context.relative_path == "context.txt"
    and has_only_keys(source, { "pid", "window_id", "bundle_id" }) and is_integer(source.pid) and source.pid > 0
    and is_integer(source.window_id) and source.window_id > 0
    and type(source.bundle_id) == "string" and source.bundle_id ~= "" and #source.bundle_id <= 512
end

local function valid_decision(decision, owner)
  if not has_only_keys(decision, {
      "protocol_version", "session_id", "nonce", "outcome", "reason", "fallback_allowed", "writer",
      "server_uuid", "generation", "config_fingerprint", "decided_at_unix_ms",
    }) or decision.protocol_version ~= PROTOCOL_VERSION
    or decision.session_id ~= owner.session_id or decision.nonce ~= owner.nonce
    or not is_integer(decision.decided_at_unix_ms) or decision.decided_at_unix_ms < owner.created_at_unix_ms
    or type(decision.fallback_allowed) ~= "boolean" then return false end
  if decision.outcome == "accepted" then
    return decision.reason == nil and decision.writer == "server" and decision.fallback_allowed == false
      and valid_identity_string(decision.server_uuid) and is_integer(decision.generation) and decision.generation >= 0
      and valid_digest(decision.config_fingerprint)
  end
  if decision.outcome ~= "rejected" or type(decision.reason) ~= "string" or decision.reason == "" then return false end
  if decision.fallback_allowed then return false end
  if decision.writer == "server" then
    return valid_identity_string(decision.server_uuid) and is_integer(decision.generation)
      and decision.generation >= 0 and valid_digest(decision.config_fingerprint)
  end
  return decision.writer == "supervisor" and decision.reason == "DECISION_LOST"
    and decision.server_uuid == nil and decision.generation == nil
    and (decision.config_fingerprint == nil or valid_digest(decision.config_fingerprint))
end

local function valid_result(result, owner, accepted_decision)
  if not has_only_keys(result, {
      "protocol_version", "session_id", "nonce", "server_uuid", "generation", "status", "reason",
      "output_sha256", "published_at_unix_ms",
    }) or result.protocol_version ~= PROTOCOL_VERSION
    or result.session_id ~= owner.session_id or result.nonce ~= owner.nonce
    or not valid_identity_string(result.server_uuid) or not is_integer(result.generation) or result.generation < 0
    or not is_integer(result.published_at_unix_ms)
    or result.published_at_unix_ms < owner.created_at_unix_ms then return false end
  if accepted_decision and (result.server_uuid ~= accepted_decision.server_uuid
    or result.generation ~= accepted_decision.generation) then return false end
  if result.status == "committed" then
    return result.reason == nil and valid_digest(result.output_sha256)
  end
  if result.output_sha256 ~= nil then return false end
  return (result.status == "cancelled" and result.reason == nil)
    or (result.status == "failed" and type(result.reason) == "string" and result.reason ~= "")
end

local function valid_delivery(delivery, owner)
  if not has_only_keys(delivery, {
      "protocol_version", "session_id", "nonce", "status", "reason",
      "hammerspoon_instance_uuid", "published_at_unix_ms",
    }) or delivery.protocol_version ~= PROTOCOL_VERSION
    or delivery.session_id ~= owner.session_id or delivery.nonce ~= owner.nonce
    or not valid_identity_string(delivery.hammerspoon_instance_uuid)
    or not is_integer(delivery.published_at_unix_ms)
    or delivery.published_at_unix_ms < owner.created_at_unix_ms then return false end
  if delivery.status == "paste_intent" or delivery.status == "pasted" then return delivery.reason == nil end
  return delivery.status == "clipboard_only" and type(delivery.reason) == "string" and delivery.reason ~= ""
end

local function valid_ui_ready(ready, owner, accepted_decision)
  return has_only_keys(ready, {
    "protocol_version", "session_id", "nonce", "server_uuid", "generation", "status",
    "ui_channel", "ready_at_unix_ms",
  }) and ready.protocol_version == PROTOCOL_VERSION
    and ready.session_id == owner.session_id and ready.nonce == owner.nonce
    and ready.server_uuid == accepted_decision.server_uuid
    and ready.generation == accepted_decision.generation
    and ready.status == "ready" and is_integer(ready.ui_channel) and ready.ui_channel > 0
    and is_integer(ready.ready_at_unix_ms) and ready.ready_at_unix_ms >= accepted_decision.decided_at_unix_ms
end

local function valid_runtime_state(runtime_state, owner, accepted_decision)
  return has_only_keys(runtime_state, {
    "protocol_version", "session_id", "nonce", "server_uuid", "generation", "status", "reason",
    "updated_at_unix_ms",
  }) and runtime_state.protocol_version == PROTOCOL_VERSION
    and runtime_state.session_id == owner.session_id and runtime_state.nonce == owner.nonce
    and runtime_state.server_uuid == accepted_decision.server_uuid
    and runtime_state.generation == accepted_decision.generation
    and type(runtime_state.status) == "string" and VALID_RUNTIME_STATES[runtime_state.status:lower()] == true
    and (runtime_state.reason == nil or (type(runtime_state.reason) == "string" and runtime_state.reason ~= ""))
    and is_integer(runtime_state.updated_at_unix_ms)
    and runtime_state.updated_at_unix_ms >= accepted_decision.decided_at_unix_ms
end

local function valid_dispatcher_metrics(metrics, session)
  return has_only_keys(metrics, {
    "protocol_version", "session_id", "dispatch_ack_at_unix_ms", "dispatcher_pid",
  }) and metrics.protocol_version == PROTOCOL_VERSION and metrics.session_id == session.id
    and is_integer(metrics.dispatch_ack_at_unix_ms) and metrics.dispatch_ack_at_unix_ms >= session.created_at_ms
    and is_integer(metrics.dispatcher_pid) and metrics.dispatcher_pid > 0
end

local function server_json(command, ...)
  if command ~= "health" and command ~= "status" and command ~= "open" then return nil end
  if hs.fs.attributes(SERVER_PATH, "mode") ~= "file" then return nil end
  local arguments = { ... }
  local parts = { shell_quote(SERVER_PATH), shell_quote(command) }
  for _, argument in ipairs(arguments) do table.insert(parts, shell_quote(argument)) end
  local output, ok = hs.execute(table.concat(parts, " "))
  if not ok or type(output) ~= "string" or output == "" then return nil end
  local decoded_ok, decoded = pcall(hs.json.decode, output)
  if not decoded_ok or type(decoded) ~= "table" then return nil end
  return decoded
end

local function alert(message, session)
  local screen = session and session.source_screen or hs.screen.mainScreen()
  hs.alert.show(message, { textSize = 16 }, screen, 2)
end

local function restore_clipboard(contents)
  if contents and next(contents) ~= nil then hs.pasteboard.writeAllData(contents) else hs.pasteboard.clearContents() end
end

local function random_token()
  return (hs.host.uuid() .. hs.host.uuid()):gsub("%-", "")
end

local function new_session_id()
  return os.date("%Y%m%d-%H%M%S") .. "-" .. hs.host.uuid():gsub("%-", ""):sub(1, 8):upper()
end

local function paths_for(id)
  local dir = SESSIONS_DIR .. "/" .. id
  return {
    dir = dir,
    input = dir .. "/input.md",
    request = dir .. "/request.json",
    decision = dir .. "/decision.json",
    state = dir .. "/state.json",
    ui_ready = dir .. "/ui-ready.json",
    result = dir .. "/result.json",
    output = dir .. "/output.md",
    delivery = dir .. "/delivery.json",
    recovery = dir .. "/recovery.md",
    context = dir .. "/context.txt",
    screenshot = dir .. "/source.png",
    metrics_dir = dir .. "/metrics",
    metrics = dir .. "/metrics/hammerspoon.json",
    dispatcher_metrics = dir .. "/metrics/dispatcher.json",
  }
end

local function owner_matches(owner, session)
  return valid_owner(owner)
    and owner.session_id == session.id and owner.nonce == session.nonce
    and owner.hammerspoon_instance_uuid == (session.owner_instance_uuid or state.instance_uuid)
end

local function owner_payload(session, stage)
  return {
    protocol_version = PROTOCOL_VERSION,
    hammerspoon_instance_uuid = state.instance_uuid,
    session_id = session.id,
    nonce = session.nonce,
    stage = stage,
    created_at_unix_ms = session.created_at_ms,
    lease_expires_at_unix_ms = now_ms() + LEASE_MS,
  }
end

local function update_owner(session, stage)
  local owner = read_json(OWNER_PATH)
  if not owner_matches(owner, session) then return false end
  local next_stage = stage or session.stage
  if not VALID_OWNER_STAGES[next_stage] then return false end
  local ok = atomic_write_json(OWNER_PATH, owner_payload(session, next_stage))
  if ok then
    session.stage = next_stage
    session.owner_instance_uuid = state.instance_uuid
  end
  return ok
end

local function stop_session_timer(session, name)
  local timer = session[name]
  if not timer then return end
  pcall(function() timer:stop() end)
  session[name] = nil
end

local function schedule_session_timer(session, name, delay, callback)
  stop_session_timer(session, name)
  session[name] = hs.timer.doAfter(delay, function()
    session[name] = nil
    if state.sessions[session.id] ~= session then return end
    callback()
  end)
  return session[name]
end

local function stop_session_activity(session)
  for _, name in ipairs({
    "monitor", "dispatch_timeout", "window_timer", "lease_timer", "ocr_delay_timer",
    "writeback_focus_timer", "writeback_select_timer", "writeback_ack_timer", "writeback_watchdog",
  }) do
    stop_session_timer(session, name)
  end
  for _, name in ipairs({ "dispatch_task", "ocr_task" }) do
    local task = session[name]
    if task then pcall(function() task:terminate() end); session[name] = nil end
  end
  session.dispatch_pending = false
end

local function release_owner(session)
  if session.lease_timer then session.lease_timer:stop(); session.lease_timer = nil end
  local owner = read_json(OWNER_PATH)
  if not owner_matches(owner, session) then return false end
  local removed = os.remove(OWNER_PATH)
  if not removed then return false end
  return hs.fs.rmdir(FRONTEND_LOCK_DIR) == true
end

local function begin_lease(session)
  if session.lease_timer then return end
  session.lease_timer = hs.timer.doEvery(1, function()
    if state.sessions[session.id] then update_owner(session, session.stage) end
  end)
end

local function acquire_owner(session)
  if not ensure_private_dir(CACHE_DIR) or not ensure_private_dir(SESSIONS_DIR) then return false, "cache unavailable" end
  if not command_ok("/bin/mkdir " .. shell_quote(FRONTEND_LOCK_DIR)) then return false, "busy" end
  command_ok("/bin/chmod 700 " .. shell_quote(FRONTEND_LOCK_DIR))
  session.stage = "claimed"
  local ok, err = atomic_write_json(OWNER_PATH, owner_payload(session, session.stage))
  if not ok then
    os.remove(OWNER_PATH)
    hs.fs.rmdir(FRONTEND_LOCK_DIR)
    return false, err
  end
  session.owner_instance_uuid = state.instance_uuid
  begin_lease(session)
  return true
end

local function write_metrics(session)
  if not session.paths or not session.paths.metrics then return end
  atomic_write_json(session.paths.metrics, {
    protocol_version = PROTOCOL_VERSION,
    session_id = session.id,
    process = "hammerspoon",
    hotkey_to_source_ready_ms = session.source_ready_ms,
    hotkey_to_dispatch_ms = session.dispatch_ms,
    hotkey_to_qt_focused_ms = session.qt_focused_ms,
    hotkey_to_ui_ready_ms = session.ui_ready_ms,
    end_to_end_ms = session.end_to_end_ms,
    ocr_capture_start_ms = session.ocr_capture_start_ms,
    ocr_ready_ms = session.ocr_ready_ms,
    status = session.metric_status or session.stage,
    updated_at_unix_ms = now_ms(),
  })
end

local function toggle_quick_terminal()
  hs.eventtap.keyStroke({ "ctrl" }, "`", 0)
end

local function title_matches(window, session)
  local ok, title = pcall(function() return window:title() end)
  if not ok or type(title) ~= "string" then return false end
  if title:find(QUICK_TERMINAL_TITLE, 1, true) then return true end
  if title:find(session.id, 1, true) and title:find(QUICK_TERMINAL_SESSION_TITLE, 1, true) then return true end
  return title:find(session.id, 1, true) and title:find(QUICK_TERMINAL_READY_TITLE, 1, true)
end

local function find_quick_terminal(session)
  if session.quick_terminal_window_id then
    local known = hs.window.get(session.quick_terminal_window_id)
    if known then return known end
  end
  for _, window in ipairs(hs.window.allWindows()) do
    if title_matches(window, session) then
      session.quick_terminal_window_id = window:id()
      return window
    end
  end
  return nil
end

local function show_quick_terminal(session)
  local terminal = find_quick_terminal(session)
  if terminal then
    local ok, visible = pcall(function() return terminal:isVisible() end)
    if ok and visible then
      local app = terminal:application()
      if app then app:activate(true) end
      pcall(function() terminal:focus() end)
      return
    end
  end
  toggle_quick_terminal()
end

local function clamp(value, low, high)
  return math.max(low, math.min(value, high))
end

local function place_quick_terminal(session, terminal)
  if session.quick_terminal_placed or not session.source_frame or not session.source_screen then return end
  local visible, frame, source = session.source_screen:frame(), terminal:frame(), session.source_frame
  local inset, gap = 12, 8
  frame.w = math.min(frame.w, visible.w * 0.46, 1180)
  frame.h = math.min(frame.h, visible.h * 0.44, 680)
  local min_x, max_x = visible.x + inset, visible.x + visible.w - frame.w - inset
  local min_y, max_y = visible.y + inset, visible.y + visible.h - frame.h - inset
  local aligned_x = clamp(source.x + source.w - frame.w, min_x, max_x)
  local aligned_y = clamp(source.y + math.min(48, source.h * 0.06), min_y, max_y)
  local candidates = {
    { x = source.x + source.w + gap, y = aligned_y },
    { x = source.x - frame.w - gap, y = aligned_y },
    { x = aligned_x, y = source.y + source.h + gap },
    { x = aligned_x, y = source.y - frame.h - gap },
  }
  local chosen
  for _, candidate in ipairs(candidates) do
    if candidate.x >= visible.x and candidate.y >= visible.y
      and candidate.x + frame.w <= visible.x + visible.w
      and candidate.y + frame.h <= visible.y + visible.h then
      chosen = candidate
      break
    end
  end
  if not chosen then
    chosen = {
      x = aligned_x,
      y = aligned_y,
    }
  end
  frame.x, frame.y = chosen.x, chosen.y
  pcall(function() terminal:setFrame(frame, 0) end)
  session.quick_terminal_placed = true
end

local function observe_quick_terminal(session)
  if session.window_timer then return end
  local attempts = 0
  session.window_timer = hs.timer.doEvery(0.025, function()
    if not state.sessions[session.id] then return end
    attempts = attempts + 1
    local terminal = find_quick_terminal(session)
    if terminal then
      place_quick_terminal(session, terminal)
      local focused = hs.window.focusedWindow()
      if focused and focused:id() == terminal:id() and not session.qt_focused_ms then
        session.qt_focused_ms = elapsed_ms(session.hotkey_started_at)
        if session.ui_ready_ms then
          session.end_to_end_ms = math.max(session.ui_ready_ms, session.qt_focused_ms)
          hs.settings.set("editAnywhereLastWindowVisibleMs", session.end_to_end_ms)
          hs.settings.set("editAnywhereLastMetricSession", session.id)
          hs.settings.set("editAnywhereLastMetricAt", os.time())
        end
        write_metrics(session)
      end
    end
    if attempts >= 160 or (session.qt_focused_ms and session.ui_ready_seen) then
      session.window_timer:stop()
      session.window_timer = nil
    end
  end)
end

local function hide_quick_terminal(session)
  local terminal = find_quick_terminal(session)
  if terminal then
    local ok, visible = pcall(function() return terminal:isVisible() end)
    if ok and visible then toggle_quick_terminal() end
  end
end

local function source_window(session)
  if not session.source_window_id then return nil end
  local window = hs.window.get(session.source_window_id)
  if not window then return nil end
  local app = window:application()
  if not app or app:pid() ~= session.source_pid or app:bundleID() ~= session.source_bundle_id then return nil end
  return window
end

local function writeback_session_is_current(session, expected_stage)
  if state.sessions[session.id] ~= session then return false end
  if expected_stage and session.stage ~= expected_stage then return false end
  return owner_matches(read_json(OWNER_PATH), session)
end

local function focus_source(session, success, failure)
  local attempts = 0
  local function attempt()
    if not writeback_session_is_current(session, "terminal") then return end
    local window = source_window(session)
    if not window then failure(); return end
    local app = window:application()
    if app then app:activate(true) end
    pcall(function() window:focus() end)
    schedule_session_timer(session, "writeback_focus_timer", 0.04, function()
      if not writeback_session_is_current(session, "terminal") then return end
      local focused = hs.window.focusedWindow()
      if focused and focused:id() == session.source_window_id then success(); return end
      attempts = attempts + 1
      if attempts < 15 then attempt() else failure() end
    end)
  end
  schedule_session_timer(session, "writeback_focus_timer", 0.03, attempt)
end

local function finish_session(session)
  stop_session_activity(session)
  if session.paths and session.paths.screenshot then os.remove(session.paths.screenshot) end
  release_owner(session)
  state.sessions[session.id] = nil
end

local function preserve_for_recovery(session, message)
  stop_session_activity(session)
  if session.paths and session.paths.screenshot then os.remove(session.paths.screenshot) end
  state.sessions[session.id] = nil
  alert(message .. "；已保留恢复锁", session)
end

local function write_delivery(session, status, extra)
  local payload = {
    protocol_version = PROTOCOL_VERSION,
    session_id = session.id,
    nonce = session.nonce,
    status = status,
    hammerspoon_instance_uuid = state.instance_uuid,
    published_at_unix_ms = now_ms(),
  }
  for key, value in pairs(extra or {}) do payload[key] = value end
  return atomic_write_json(session.paths.delivery, payload)
end

local function fallback_to_clipboard(session, reason, message)
  if not writeback_session_is_current(session) then return false end
  stop_session_timer(session, "writeback_watchdog")
  stop_session_timer(session, "writeback_focus_timer")
  stop_session_timer(session, "writeback_select_timer")
  stop_session_timer(session, "writeback_ack_timer")
  if not update_owner(session, "clipboard_only")
    or not write_delivery(session, "clipboard_only", { reason = reason }) then
    preserve_for_recovery(session, message .. "，且无法记录 clipboard-only；结果已保留在剪贴板")
    return false
  end
  session.metric_status = "clipboard_only"
  write_metrics(session)
  alert(message .. "；结果已保留在剪贴板", session)
  finish_session(session)
  return true
end

local function finish_without_writeback(session, message)
  hide_quick_terminal(session)
  if session.clipboard_before then restore_clipboard(session.clipboard_before) end
  local window = source_window(session)
  if window then
    local app = window:application()
    if app then app:activate(true) end
    hs.timer.doAfter(0.04, function() pcall(function() window:focus() end) end)
  end
  session.metric_status = session.metric_status or "finished_without_writeback"
  write_metrics(session)
  alert(message, session)
  finish_session(session)
end

local function validate_identity(document, session)
  if type(document) ~= "table" then return false end
  if document.protocol_version ~= PROTOCOL_VERSION or document.session_id ~= session.id or document.nonce ~= session.nonce then return false end
  if session.server_uuid and document.server_uuid ~= session.server_uuid then return false end
  if session.generation and document.generation ~= session.generation then return false end
  return true
end

local function commit_result(session, result)
  local contents = read_file(session.paths.output, MAX_OUTPUT_BYTES)
  if contents == nil then preserve_for_recovery(session, "读取编辑结果失败，未写回"); return end
  local digest = hs.hash.SHA256(contents)
  if type(result.output_sha256) ~= "string" or digest:lower() ~= result.output_sha256:lower() then
    preserve_for_recovery(session, "编辑结果校验失败，未写回")
    return
  end
  hide_quick_terminal(session)
  hs.pasteboard.setContents(contents)
  if session.adopted or not source_window(session) then
    fallback_to_clipboard(session, "SOURCE_WINDOW_UNAVAILABLE", "未自动写回")
    return
  end
  focus_source(session, function()
    local owner_updated = update_owner(session, "paste_intent")
    local delivery_written = owner_updated and write_delivery(session, "paste_intent")
    if not owner_updated or not delivery_written then
      preserve_for_recovery(session, "无法记录写回状态；结果已保留在剪贴板")
      return
    end
    schedule_session_timer(session, "writeback_watchdog", WRITEBACK_TIMEOUT_SECONDS, function()
      if writeback_session_is_current(session, "paste_intent") then
        fallback_to_clipboard(session, "WRITEBACK_TIMEOUT", "自动写回超时，无法确认是否已粘贴")
      end
    end)
    local selected = pcall(hs.eventtap.keyStroke, { "cmd" }, "a", 0)
    if not selected then
      fallback_to_clipboard(session, "SELECT_FAILED", "无法选择原输入框内容，未自动写回")
      return
    end
    schedule_session_timer(session, "writeback_select_timer", 0.04, function()
      if not writeback_session_is_current(session, "paste_intent") then return end
      local focused = hs.window.focusedWindow()
      if not focused or focused:id() ~= session.source_window_id then
        fallback_to_clipboard(session, "FOCUS_LOST", "原窗口失去焦点，未自动写回")
        return
      end
      local pasted = pcall(hs.eventtap.keyStroke, { "cmd" }, "v", 0)
      if not pasted then
        fallback_to_clipboard(session, "PASTE_FAILED", "无法发送粘贴快捷键")
        return
      end
      schedule_session_timer(session, "writeback_ack_timer", 0.25, function()
        if not writeback_session_is_current(session, "paste_intent") then return end
        stop_session_timer(session, "writeback_watchdog")
        if not write_delivery(session, "pasted") or not update_owner(session, "pasted") then
          preserve_for_recovery(session, "写回已发送，但无法确认 delivery 状态")
          return
        end
        if session.clipboard_before then restore_clipboard(session.clipboard_before) end
        session.metric_status = "pasted"
        write_metrics(session)
        finish_session(session)
      end)
    end)
  end, function()
    fallback_to_clipboard(session, "SOURCE_WINDOW_UNAVAILABLE", "无法聚焦原窗口，未自动写回")
  end)
end

local function handle_result(session, result)
  if session.result_handled then return end
  session.result_handled = true
  local owner, decision = read_json(OWNER_PATH), read_json(session.paths.decision)
  if not validate_identity(result, session) or not owner_matches(owner, session)
    or not valid_decision(decision, owner) or decision.outcome ~= "accepted"
    or not valid_result(result, owner, decision) then
    preserve_for_recovery(session, "编辑结果身份校验失败，未写回")
    return
  end
  if hs.fs.symlinkAttributes(session.paths.delivery) ~= nil then
    preserve_for_recovery(session, "终态前已存在 delivery，未写回")
    return
  end
  if not update_owner(session, "terminal") then
    preserve_for_recovery(session, "无法确认终态 owner，未写回")
    return
  end
  if result.status == "committed" then
    commit_result(session, result)
  elseif result.status == "cancelled" then
    finish_without_writeback(session, "已取消，原文本未修改")
  else
    finish_without_writeback(session, "编辑会话失败，原文本未修改")
  end
end

local function publish_context(session, contents)
  if not state.sessions[session.id] or session.result_handled then return false end
  return atomic_write(session.paths.context, contents)
end

local function start_ocr(session)
  if session.ocr_started or session.adopted or session.result_handled then return end
  session.ocr_started = true
  session.ocr_capture_started_at = hs.timer.absoluteTime()
  session.ocr_capture_start_ms = elapsed_ms(session.hotkey_started_at)
  local window = source_window(session)
  if not window or not hs.screenRecordingState() then
    publish_context(session, "__NVIM_EXTERNAL_CONTEXT_FAILED__\n")
    write_metrics(session)
    return
  end
  local snapshot = window:snapshot(false)
  if not snapshot or not snapshot:saveToFile(session.paths.screenshot, true, "PNG") then
    publish_context(session, "__NVIM_EXTERNAL_CONTEXT_FAILED__\n")
    write_metrics(session)
    return
  end
  chmod_private(session.paths.screenshot)
  local tool = hs.fs.attributes(OCR_BINARY) and OCR_BINARY or OCR_FALLBACK
  if not hs.fs.attributes(tool) then
    publish_context(session, "__NVIM_EXTERNAL_CONTEXT_FAILED__\n")
    os.remove(session.paths.screenshot)
    return
  end
  session.ocr_task = hs.task.new(tool, function(exit_code, stdout, stderr)
    os.remove(session.paths.screenshot)
    if not state.sessions[session.id] or session.result_handled then return end
    if exit_code == 0 then
      publish_context(session, stdout or "")
    else
      hs.printf("Edit Anywhere OCR failed for %s: %s", session.id, stderr or "unknown error")
      publish_context(session, "__NVIM_EXTERNAL_CONTEXT_FAILED__\n")
    end
    session.ocr_ready_ms = elapsed_ms(session.ocr_capture_started_at)
    write_metrics(session)
  end, { session.paths.screenshot })
  if not session.ocr_task or not session.ocr_task:start() then
    os.remove(session.paths.screenshot)
    publish_context(session, "__NVIM_EXTERNAL_CONTEXT_FAILED__\n")
  end
end

local function maybe_start_ocr(session)
  if session.ui_ready_ms and session.dispatch_plus_100_ready then start_ocr(session) end
end

local function observe_dispatch_ack(session, dispatcher_metrics)
  if session.dispatch_ack_seen or not valid_dispatcher_metrics(dispatcher_metrics, session) then return end
  session.dispatch_ack_seen = true
  session.ocr_delay_timer = hs.timer.doAfter(0.1, function()
    session.ocr_delay_timer = nil
    if not state.sessions[session.id] then return end
    session.dispatch_plus_100_ready = true
    maybe_start_ocr(session)
  end)
end

local function observe_decision(session, decision)
  if session.decision_seen then return end
  session.decision_seen = true
  local owner = read_json(OWNER_PATH)
  if not validate_identity(decision, session) or not owner_matches(owner, session)
    or not valid_decision(decision, owner) then
    preserve_for_recovery(session, "Server 决策身份校验失败")
    return
  end
  if decision.outcome ~= "accepted" then
    session.metric_status = "rejected:" .. tostring(decision.reason or "UNKNOWN")
    finish_without_writeback(session, "Edit Anywhere 暂不可用：" .. tostring(decision.reason or "请求被拒绝"))
    return
  end
  session.server_uuid = decision.server_uuid
  session.generation = decision.generation
  session.config_fingerprint = decision.config_fingerprint
  if not update_owner(session, "accepted") then
    preserve_for_recovery(session, "无法确认 accepted owner")
    return
  end
  write_metrics(session)
end

local function observe_ui_ready(session, ready)
  if session.ui_ready_seen or not session.decision_seen or not validate_identity(ready, session) then return end
  local owner, decision = read_json(OWNER_PATH), read_json(session.paths.decision)
  if not owner_matches(owner, session) or not valid_decision(decision, owner)
    or decision.outcome ~= "accepted" or not valid_ui_ready(ready, owner, decision) then return end
  session.ui_ready_seen = true
  if session.stage == "accepted" and not update_owner(session, "ui_ready") then
    preserve_for_recovery(session, "无法确认 ui-ready owner")
    return
  end
  if session.adopted then return end
  session.ui_ready_ms = elapsed_ms(session.hotkey_started_at)
  session.end_to_end_ms = math.max(session.ui_ready_ms or 0, session.qt_focused_ms or 0)
  session.metric_status = "ui_ready"
  hs.settings.set("editAnywhereLastUiReadyMs", session.ui_ready_ms)
  hs.settings.set("editAnywhereLastWindowVisibleMs", session.end_to_end_ms)
  hs.settings.set("editAnywhereLastMetricSession", session.id)
  hs.settings.set("editAnywhereLastMetricAt", os.time())
  hs.printf("Edit Anywhere metric: hotkey -> Neovim UI ready %.1f ms (end-to-end %.1f ms)", session.ui_ready_ms, session.end_to_end_ms)
  write_metrics(session)
  maybe_start_ocr(session)
end

local function start_monitor(session)
  if session.monitor then return end
  session.monitor = hs.timer.doEvery(0.025, function()
    if not state.sessions[session.id] then return end
    if not session.dispatch_ack_seen then observe_dispatch_ack(session, read_json(session.paths.dispatcher_metrics)) end
    if not session.decision_seen then
      local decision = read_json(session.paths.decision)
      if decision then observe_decision(session, decision) end
    end
    if not state.sessions[session.id] then return end
    if not session.ui_ready_seen then
      local ready = read_json(session.paths.ui_ready)
      if ready then observe_ui_ready(session, ready) end
    end
    if not state.sessions[session.id] then return end
    local result = read_json(session.paths.result)
    if result then handle_result(session, result); return end
    if session.stage ~= "suspended" and session.stage ~= "recovery_required" then
      local runtime_state = read_json(session.paths.state)
      local owner, decision = read_json(OWNER_PATH), read_json(session.paths.decision)
      local status = owner_matches(owner, session) and valid_decision(decision, owner)
        and valid_runtime_state(runtime_state, owner, decision) and runtime_state.status:lower() or ""
      if status == "suspended" and not update_owner(session, "suspended") then
        preserve_for_recovery(session, "无法确认 suspended owner")
      end
      if status == "recovery_required" and not update_owner(session, "recovery_required") then
        preserve_for_recovery(session, "无法确认 recovery owner")
      end
    end
  end)
end

local function ensure_fifo()
  ensure_private_dir(CACHE_DIR)
  local mode = hs.fs.symlinkAttributes(FIFO_PATH, "mode")
  if mode == "named pipe" then return true end
  if mode ~= nil then return false end
  if not command_ok("/usr/bin/mkfifo " .. shell_quote(FIFO_PATH)) then return false end
  chmod_private(FIFO_PATH)
  return true
end

local function dispatch(session)
  if session.dispatch_pending then
    show_quick_terminal(session)
    return true
  end
  session.dispatch_pending = true
  if not ensure_fifo() then finish_without_writeback(session, "无法准备 Ghostty Quick Terminal"); return end
  session.dispatch_task = hs.task.new("/bin/sh", function(exit_code, _, stderr)
    if not state.sessions[session.id] then return end
    session.dispatch_task = nil
    if exit_code ~= 0 then
      session.dispatch_pending = false
      hs.printf("Edit Anywhere dispatch failed for %s: %s", session.id, stderr or "unknown error")
      finish_without_writeback(session, "无法连接 Ghostty Quick Terminal")
      return
    end
    session.dispatch_ms = elapsed_ms(session.hotkey_started_at)
    hs.settings.set("editAnywhereLastLaunchRequestMs", session.dispatch_ms)
    write_metrics(session)
  end, {
    "-c", 'printf "%s\\n" "$1" > "$2"', "edit-anywhere-dispatch", session.id, FIFO_PATH,
  })
  if not session.dispatch_task or not session.dispatch_task:start() then
    session.dispatch_pending = false
    finish_without_writeback(session, "无法投递 Quick Terminal 编辑任务")
    return
  end
  show_quick_terminal(session)
  observe_quick_terminal(session)
  session.dispatch_timeout = hs.timer.doAfter(4, function()
    session.dispatch_timeout = nil
    if not state.sessions[session.id] then return end
    session.dispatch_pending = false
    if session.decision_seen then return end
    finish_without_writeback(session, "Quick Terminal 或 Neovim Server 启动超时")
  end)
  return true
end

local function publish_request(session, contents)
  if not ensure_private_dir(session.paths.dir) or not ensure_private_dir(session.paths.metrics_dir) then
    return false, "session directory unavailable"
  end
  local ok, err = atomic_write(session.paths.input, contents)
  if not ok then return false, err end
  ok, err = atomic_write_json(session.paths.request, {
    protocol_version = PROTOCOL_VERSION,
    session_id = session.id,
    nonce = session.nonce,
    created_at_unix_ms = session.created_at_ms,
    expires_at_unix_ms = session.created_at_ms + 120000,
    editor = { filetype = "markdown", cursor = "end", start_insert = true },
    context = { source = "window-ocr", token = session.context_token, relative_path = "context.txt" },
    source_window = { pid = session.source_pid, window_id = session.source_window_id, bundle_id = session.source_bundle_id },
  })
  if not ok then return false, err end
  session.source_ready_ms = elapsed_ms(session.hotkey_started_at)
  if not update_owner(session, "request_published") then return false, "owner update failed" end
  write_metrics(session)
  return true
end

local function reconcile_terminal_owner(owner)
  if not valid_owner(owner) then return false end
  local paths = paths_for(owner.session_id)
  local decision, result = read_json(paths.decision), read_json(paths.result)
  if not valid_decision(decision, owner) or decision.outcome ~= "accepted"
    or not valid_result(result, owner, decision) then return false end
  local session = {
    id = owner.session_id, nonce = owner.nonce, created_at_ms = owner.created_at_unix_ms,
    hotkey_started_at = hs.timer.absoluteTime(), paths = paths, adopted = true, stage = owner.stage,
    owner_instance_uuid = owner.hammerspoon_instance_uuid,
  }
  state.sessions[session.id] = session
  local delivery = read_json(paths.delivery)
  local delivery_exists = hs.fs.symlinkAttributes(paths.delivery) ~= nil
  if delivery_exists and not valid_delivery(delivery, owner) then
    state.sessions[session.id] = nil
    return false
  end
  if result.status == "committed" then
    if not delivery or delivery.status == "paste_intent" then
      local contents = read_file(paths.output, MAX_OUTPUT_BYTES)
      if not contents or hs.hash.SHA256(contents):lower() ~= result.output_sha256:lower() then
        state.sessions[session.id] = nil
        return false
      end
      hs.pasteboard.setContents(contents)
      if not write_delivery(session, "clipboard_only", { reason = "HAMMERSPOON_RELOAD" }) then
        state.sessions[session.id] = nil
        return false
      end
      alert("已恢复编辑结果到剪贴板；为避免重复写入，未自动粘贴", session)
    end
  elseif delivery_exists then
    state.sessions[session.id] = nil
    return false
  end
  if not release_owner(session) then state.sessions[session.id] = nil; return false end
  state.sessions[session.id] = nil
  return true
end

local function reconcile_rejected_owner(owner)
  if not valid_owner(owner) then return false end
  local paths = paths_for(owner.session_id)
  local decision = read_json(paths.decision)
  if not valid_decision(decision, owner) or decision.outcome ~= "rejected" then return false end
  if hs.fs.symlinkAttributes(paths.result) ~= nil or hs.fs.symlinkAttributes(paths.delivery) ~= nil then return false end
  local session = {
    id = owner.session_id, nonce = owner.nonce, created_at_ms = owner.created_at_unix_ms,
    hotkey_started_at = hs.timer.absoluteTime(), paths = paths, adopted = true, stage = "terminal",
    owner_instance_uuid = owner.hammerspoon_instance_uuid,
  }
  state.sessions[session.id] = session
  alert("上一个 Edit Anywhere 请求已结束：" .. tostring(decision.reason or "请求被拒绝"), session)
  if not release_owner(session) then state.sessions[session.id] = nil; return false end
  state.sessions[session.id] = nil
  return true
end

local function valid_server_health(health)
  if not has_only_keys(health, {
      "state", "active_session", "prewarmed", "adapters_ok", "layout_ok", "restart_pending",
      "started_at", "protocol_version", "server_uuid", "generation", "config_fingerprint",
    }) or health.protocol_version ~= PROTOCOL_VERSION or not valid_identity_string(health.server_uuid)
    or not is_integer(health.generation) or health.generation < 0 or not valid_digest(health.config_fingerprint)
    or type(health.state) ~= "string" or health.state == ""
    or type(health.prewarmed) ~= "boolean" or type(health.adapters_ok) ~= "boolean"
    or type(health.layout_ok) ~= "boolean" or type(health.restart_pending) ~= "boolean"
    or (health.active_session ~= nil and not valid_session_id(health.active_session))
    or (health.started_at ~= nil and (not is_integer(health.started_at) or health.started_at <= 0)) then return false end
  return true
end

local function active_status_matches(status, owner, decision)
  if not has_only_keys(status, {
      "state", "session_id", "nonce", "bufnr", "ui_channel", "server_uuid", "generation",
    }) or status.session_id ~= owner.session_id or status.nonce ~= owner.nonce
    or status.server_uuid ~= decision.server_uuid or status.generation ~= decision.generation
    or not is_integer(status.bufnr) or status.bufnr <= 0
    or (status.ui_channel ~= nil and (not is_integer(status.ui_channel) or status.ui_channel <= 0))
    or type(status.state) ~= "string" then return false end
  local value = status.state:lower()
  return ACTIVE_RUNTIME_STAGES[value] ~= nil
end

local function adopt_active_owner(owner, request, decision, health, resume_requested)
  if owner.stage ~= "request_published" and owner.stage ~= "accepted" and owner.stage ~= "ui_ready"
    and owner.stage ~= "suspended" and owner.stage ~= "recovery_required" then return false end
  if not valid_server_health(health) or health.server_uuid ~= decision.server_uuid
    or health.generation ~= decision.generation or health.config_fingerprint ~= decision.config_fingerprint
    or health.active_session ~= owner.session_id then return false end
  local server_status = server_json("status", owner.session_id)
  if not active_status_matches(server_status, owner, decision) then return false end
  local active_state = server_status.state:lower()
  local stage = ACTIVE_RUNTIME_STAGES[active_state]
  local session = {
    id = owner.session_id,
    nonce = owner.nonce,
    context_token = request.context.token,
    created_at_ms = owner.created_at_unix_ms,
    hotkey_started_at = hs.timer.absoluteTime(),
    paths = paths_for(owner.session_id),
    adopted = true,
    stage = stage,
    decision_seen = true,
    dispatch_ack_seen = true,
    ui_ready_seen = true,
    owner_instance_uuid = owner.hammerspoon_instance_uuid,
    server_uuid = decision.server_uuid,
    generation = decision.generation,
    config_fingerprint = decision.config_fingerprint,
  }
  state.sessions[session.id] = session
  if not update_owner(session, stage) then state.sessions[session.id] = nil; return false end
  begin_lease(session)
  start_monitor(session)
  if resume_requested and (active_state == "suspended" or active_state == "recovery_required") then
    dispatch(session)
  end
  return true
end

local function reclaim_unpublished_claim(owner, health)
  if owner.stage ~= "claimed" or now_ms() < owner.lease_expires_at_unix_ms + RECLAIM_GRACE_MS
    or not valid_server_health(health) or health.active_session ~= nil then return false end
  local paths = paths_for(owner.session_id)
  for _, path in ipairs({ paths.request, paths.decision, paths.result, paths.delivery }) do
    if hs.fs.symlinkAttributes(path) ~= nil then return false end
  end
  local session = {
    id = owner.session_id, nonce = owner.nonce, created_at_ms = owner.created_at_unix_ms,
    hotkey_started_at = hs.timer.absoluteTime(), paths = paths, adopted = true, stage = "claimed",
    owner_instance_uuid = owner.hammerspoon_instance_uuid,
  }
  state.sessions[session.id] = session
  local released = release_owner(session)
  state.sessions[session.id] = nil
  if released then alert("已安全回收未发布的 Edit Anywhere 请求", session) end
  return released
end

local function directory_is_empty(path)
  local ok, iterator, directory = pcall(hs.fs.dir, path)
  if not ok or type(iterator) ~= "function" then return false end
  for name in iterator, directory do
    if name ~= "." and name ~= ".." then return false end
  end
  return true
end

local function reclaim_empty_frontend_lock()
  local attributes = hs.fs.symlinkAttributes(FRONTEND_LOCK_DIR)
  if not attributes or not private_directory(FRONTEND_LOCK_DIR)
    or not directory_is_empty(FRONTEND_LOCK_DIR)
    or type(attributes.modification) ~= "number"
    or now_ms() < attributes.modification * 1000 + RECLAIM_GRACE_MS then return false end
  local health = server_json "health"
  if not valid_server_health(health) or health.active_session ~= nil then return false end
  return hs.fs.rmdir(FRONTEND_LOCK_DIR) == true
end

local function reclaim_orphaned_session(session, owner, request, decision, health)
  if not valid_owner(owner) or not valid_request(request, owner) or not valid_decision(decision, owner)
    or decision.outcome ~= "accepted" or not valid_server_health(health) then return false end
  local same_generation = valid_server_health(health)
    and health.server_uuid == decision.server_uuid
    and health.generation == decision.generation
    and health.config_fingerprint == decision.config_fingerprint
  local superseded_generation = valid_server_health(health) and health.generation > decision.generation
  if (not same_generation and not superseded_generation)
    or health.active_session ~= nil or health.state:lower() ~= "idle" then return false end
  local synthesized = session == nil
  session = session or {
    id = owner.session_id,
    nonce = owner.nonce,
    created_at_ms = owner.created_at_unix_ms,
    hotkey_started_at = hs.timer.absoluteTime(),
    paths = paths_for(owner.session_id),
    adopted = true,
    stage = owner.stage,
    owner_instance_uuid = owner.hammerspoon_instance_uuid,
  }
  if not owner_matches(owner, session) then return false end
  if synthesized then state.sessions[session.id] = session end
  local result = read_json(session.paths.result)
  if result then
    handle_result(session, result)
    return true
  end
  if hs.fs.symlinkAttributes(session.paths.delivery) ~= nil then
    if synthesized then state.sessions[session.id] = nil end
    return false
  end
  stop_session_activity(session)
  hide_quick_terminal(session)
  if not release_owner(session) then
    if synthesized then state.sessions[session.id] = nil end
    return false
  end
  state.sessions[session.id] = nil
  alert("旧的 Edit Anywhere 会话已失效；恢复文本仍保留，请重新按快捷键", session)
  return true
end

local function reconcile_owner(owner, resume_requested)
  if not valid_owner(owner) then return false end
  local live = state.sessions[owner.session_id]
  if live then
    if not owner_matches(owner, live) then return false end
    local runtime_state, decision = read_json(live.paths.state), read_json(live.paths.decision)
    local request = read_json(live.paths.request)
    if reclaim_orphaned_session(live, owner, request, decision, server_json "health") then return true end
    local value = valid_decision(decision, owner) and valid_runtime_state(runtime_state, owner, decision)
      and runtime_state.status:lower() or ""
    if resume_requested and (value == "suspended" or value == "recovery_required") then
      dispatch(live)
    elseif resume_requested then
      show_quick_terminal(live)
      alert("已切回现有 Edit Anywhere 会话；用 ZQ 或连续两次 Ctrl-C 取消", live)
    end
    return true
  end
  if reconcile_terminal_owner(owner) or reconcile_rejected_owner(owner) then return true end
  local paths = paths_for(owner.session_id)
  local request = read_json(paths.request)
  local health = server_json("health")
  if not request then return reclaim_unpublished_claim(owner, health) end
  if not valid_request(request, owner) then return false end
  local decision = read_json(paths.decision)
  if decision then
    if not valid_decision(decision, owner) then return false end
    if decision.outcome == "rejected" then return reconcile_rejected_owner(owner) end
    if reclaim_orphaned_session(nil, owner, request, decision, health) then return true end
    return adopt_active_owner(owner, request, decision, health, resume_requested)
  end
  if not valid_server_health(health) then return false end
  server_json("open", owner.session_id, owner.nonce)
  decision = read_json(paths.decision)
  if not decision or not valid_decision(decision, owner) then return false end
  if decision.outcome == "rejected" then return reconcile_rejected_owner(owner) end
  return adopt_active_owner(owner, request, decision, server_json("health"), resume_requested)
end

local function handle_existing_owner(owner)
  if reconcile_owner(owner, true) then return end
  alert("已有一个无法安全接管的 Edit Anywhere 会话；已保留恢复锁")
end

local editable_ax_roles = {
  AXTextArea = true,
  AXTextField = true,
  AXComboBox = true,
}

local function focused_input_text(app)
  if not app then return nil end
  local ok, value = pcall(function()
    local application = hs.axuielement.applicationElement(app)
    local element = application and application:attributeValue "AXFocusedUIElement"
    if not element then return nil end
    local role = element:attributeValue "AXRole"
    if not editable_ax_roles[role] or element:attributeValue "AXEnabled" == false then return nil end
    local contents = element:attributeValue "AXValue"
    if type(contents) == "string" then return contents end
  end)
  return ok and value or nil
end

local function begin_edit()
  local hotkey_started_at = hs.timer.absoluteTime()
  local existing_owner = read_json(OWNER_PATH)
  if existing_owner then handle_existing_owner(existing_owner); return end
  if hs.fs.attributes(FRONTEND_LOCK_DIR) and not reclaim_empty_frontend_lock() then
    alert("Edit Anywhere 前端状态需要恢复；未读取当前输入框")
    return
  end
  if hs.eventtap.isSecureInputEnabled() then alert("当前是安全输入框，无法读取文本"); return end
  if not hs.fs.attributes(DISPATCHER_PATH) then alert("缺少 Edit Anywhere Quick Terminal 脚本"); return end
  local app, window = hs.application.frontmostApplication(), hs.window.focusedWindow()
  if not app or not window or not window:id() or not app:bundleID() then alert("当前窗口无法用于 Edit Anywhere"); return end
  local session = {
    id = new_session_id(),
    nonce = random_token(),
    context_token = random_token(),
    created_at_ms = now_ms(),
    hotkey_started_at = hotkey_started_at,
    source_pid = app:pid(),
    source_bundle_id = app:bundleID(),
    source_window_id = window:id(),
    source_frame = window:frame(),
    source_screen = window:screen(),
    clipboard_before = hs.pasteboard.readAllData(),
  }
  session.paths = paths_for(session.id)
  local acquired, acquire_error = acquire_owner(session)
  if not acquired then
    if acquire_error == "busy" then alert("已有一个 Edit Anywhere 会话") else alert("无法创建 Edit Anywhere 会话") end
    return
  end
  state.sessions[session.id] = session
  local direct_contents = focused_input_text(app)
  if direct_contents ~= nil then
    local ok, err = publish_request(session, direct_contents)
    if not ok then
      hs.printf("Edit Anywhere request publish failed for %s: %s", session.id, err or "unknown error")
      finish_without_writeback(session, "创建 Edit Anywhere 请求失败")
      return
    end
    start_monitor(session)
    dispatch(session)
    return
  end
  local marker = "__EDIT_ANYWHERE_EMPTY_" .. random_token() .. "__"
  hs.pasteboard.setContents(marker)
  hs.eventtap.keyStroke({ "cmd" }, "a", 0)
  hs.timer.doAfter(0.015, function()
    if not state.sessions[session.id] then return end
    hs.pasteboard.callbackWhenChanged(0.3, function(changed)
      if not state.sessions[session.id] then return end
      local contents = hs.pasteboard.getContents()
      restore_clipboard(session.clipboard_before)
      if not changed or contents == nil or contents == marker then
        finish_without_writeback(session, "未能读取当前输入框；没有打开编辑窗口")
        return
      end
      local ok, err = publish_request(session, contents)
      if not ok then
        hs.printf("Edit Anywhere request publish failed for %s: %s", session.id, err or "unknown error")
        finish_without_writeback(session, "创建 Edit Anywhere 请求失败")
        return
      end
      start_monitor(session)
      dispatch(session)
    end)
    hs.eventtap.keyStroke({ "cmd" }, "c", 0)
  end)
end

function M.start()
  if state.hotkey then state.hotkey:delete() end
  ensure_private_dir(CACHE_DIR)
  ensure_private_dir(SESSIONS_DIR)
  state.hotkey = hs.hotkey.bind({ "cmd", "shift" }, "e", begin_edit)
  hs.timer.doAfter(0, function()
    local owner = read_json(OWNER_PATH)
    if owner then reconcile_owner(owner, false) else reclaim_empty_frontend_lock() end
  end)
  return M
end

function M.stop()
  if state.hotkey then state.hotkey:delete(); state.hotkey = nil end
  for _, session in pairs(state.sessions) do stop_session_activity(session) end
end

local function server_process_alive()
  local contents = read_file(SERVER_PID_PATH, 64)
  local pid = contents and tonumber(contents:match("^%s*(%d+)%s*$"))
  if not pid or pid < 1 then return false end
  local _, ok = hs.execute("/bin/kill -0 " .. tostring(math.floor(pid)))
  return ok == true
end

function M.recoverStuckSession()
  local owner = read_json(OWNER_PATH)
  if not owner then return true, "NO_SESSION" end
  if not valid_owner(owner) then return false, "INVALID_OWNER" end
  if server_process_alive() then return false, "SERVER_STILL_RUNNING" end
  local session = state.sessions[owner.session_id] or {
    id = owner.session_id,
    nonce = owner.nonce,
    owner_instance_uuid = owner.hammerspoon_instance_uuid,
    created_at_ms = owner.created_at_unix_ms,
    paths = paths_for(owner.session_id),
  }
  stop_session_activity(session)
  local retained_path = read_file(session.paths.recovery, MAX_OUTPUT_BYTES) and session.paths.recovery or session.paths.input
  if not release_owner(session) then return false, "OWNER_RELEASE_FAILED" end
  state.sessions[session.id] = nil
  alert("已解除卡住的 Edit Anywhere 会话；文本仍保留在会话目录", session)
  return true, retained_path
end

M._state = state

if os.getenv("EDIT_ANYWHERE_TEST") == "1" then
  M._test = {
    owner_matches = owner_matches,
    valid_owner = valid_owner,
    valid_request = valid_request,
    valid_decision = valid_decision,
    valid_result = valid_result,
    valid_delivery = valid_delivery,
    directory_is_empty = directory_is_empty,
  }
end

return M
