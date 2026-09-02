local uv = vim.uv or vim.loop
local protocol = require "edit_anywhere.protocol"
local session_runtime = require "edit_anywhere.session"
local bootstrap = require "edit_anywhere.bootstrap"

local M = {}

local runtime = {
  started = false,
  ready = false,
  state = "STOPPED",
  active = nil,
  keeper_bufnr = nil,
  window = nil,
  restart_pending = false,
  started_at = nil,
  augroup = nil,
}

local function cache_root()
  return protocol.cache_root()
end

local function path_join(...)
  return table.concat({ ... }, "/"):gsub("/+", "/")
end

local function default_fingerprint()
  local version = vim.version()
  return protocol.sha256(table.concat({
    "edit-anywhere-runtime-v1",
    tostring(protocol.VERSION),
    vim.v.progpath,
    table.concat({ version.major, version.minor, version.patch }, "."),
  }, ":"))
end

local function new_identity()
  local generation = tonumber(vim.env.EDIT_ANYWHERE_SERVER_GENERATION) or 1
  local uuid = vim.env.EDIT_ANYWHERE_SERVER_UUID
  if not uuid or uuid == "" then uuid = protocol.random_token() end
  local fingerprint = vim.env.EDIT_ANYWHERE_CONFIG_FINGERPRINT
  if not fingerprint or fingerprint == "" then fingerprint = default_fingerprint() end
  return {
    name = "edit-anywhere",
    protocol_version = protocol.VERSION,
    server_uuid = uuid,
    generation = math.floor(generation),
    config_fingerprint = fingerprint,
  }
end

runtime.identity = new_identity()

local function set_keeper_options(bufnr)
  if not vim.api.nvim_buf_is_valid(bufnr) then return false end
  vim.b[bufnr].edit_anywhere_keeper = true
  vim.bo[bufnr].buftype = "nofile"
  vim.bo[bufnr].bufhidden = "hide"
  vim.bo[bufnr].swapfile = false
  vim.bo[bufnr].undofile = false
  vim.bo[bufnr].modifiable = true
  vim.bo[bufnr].modified = false
  return true
end

local function normal_windows()
  local windows = {}
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    local config = vim.api.nvim_win_get_config(win)
    if config.relative == "" then windows[#windows + 1] = win end
  end
  return windows
end

local filetree_filetypes = {
  ["neo-tree"] = true,
  NvimTree = true,
}

local filetree_startup_descriptions = {
  ["Open Neo-tree on the right at startup"] = true,
}

local function suppress_filetree_startup_autocmds()
  local ok, autocmds = pcall(vim.api.nvim_get_autocmds, { event = "VimEnter" })
  if not ok then return end
  for _, autocmd in ipairs(autocmds) do
    if filetree_startup_descriptions[autocmd.desc] then pcall(vim.api.nvim_del_autocmd, autocmd.id) end
  end
end

local function is_filetree_window(win)
  if not vim.api.nvim_win_is_valid(win) then return false end
  local ok, bufnr = pcall(vim.api.nvim_win_get_buf, win)
  return ok and vim.api.nvim_buf_is_valid(bufnr) and filetree_filetypes[vim.bo[bufnr].filetype] == true
end

local function preferred_window()
  if runtime.window and vim.api.nvim_win_is_valid(runtime.window) and not is_filetree_window(runtime.window) then
    return runtime.window
  end
  local windows = normal_windows()
  for _, win in ipairs(windows) do
    if not is_filetree_window(win) then return win end
  end
  return windows[1]
end

local function close_filetree_windows(desired)
  if vim.fn.exists ":Neotree" == 2 then pcall(vim.cmd, "silent! Neotree close") end
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if is_filetree_window(win) then
      if win == runtime.window and desired and vim.api.nvim_buf_is_valid(desired) then
        pcall(vim.api.nvim_win_set_buf, win, desired)
      else
        pcall(vim.api.nvim_win_close, win, true)
      end
    end
  end
end

function M.normalize_layout()
  runtime.window = preferred_window()
  if not runtime.window or not vim.api.nvim_win_is_valid(runtime.window) then return false end
  local desired = runtime.active and runtime.active.bufnr or runtime.keeper_bufnr
  close_filetree_windows(desired)
  for _, win in ipairs(normal_windows()) do
    if win ~= runtime.window then pcall(vim.api.nvim_win_close, win, true) end
  end
  if #vim.api.nvim_list_tabpages() ~= 1 or #normal_windows() ~= 1 then return false end
  if desired and vim.api.nvim_buf_is_valid(desired) then
    pcall(vim.api.nvim_win_set_buf, runtime.window, desired)
  end
  pcall(vim.api.nvim_set_current_win, runtime.window)
  return M.layout_ok()
end

function M.layout_ok()
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if is_filetree_window(win) then return false end
  end
  local windows = normal_windows()
  if #windows ~= 1 or #vim.api.nvim_list_tabpages() ~= 1 then return false end
  local current_config = vim.api.nvim_win_get_config(vim.api.nvim_get_current_win())
  if current_config.relative ~= "" then return false end
  if not runtime.window or windows[1] ~= runtime.window then return false end
  local expected = runtime.active and runtime.active.bufnr or runtime.keeper_bufnr
  return expected == nil or vim.api.nvim_win_get_buf(runtime.window) == expected
end

local function write_identity()
  local server_dir = path_join(cache_root(), "server")
  local ok, err = protocol.ensure_directory(server_dir, 448)
  if not ok then return nil, err end
  local payload = vim.tbl_extend("force", M.identity(), {
    pid = uv.os_getpid(),
    socket = vim.v.servername ~= "" and vim.v.servername or vim.NIL,
    started_at_unix_ms = runtime.started_at,
  })
  return protocol.atomic_write_json(path_join(server_dir, "identity.json"), payload)
end

local function on_terminal(session)
  if runtime.active ~= session then return end
  runtime.active = nil
  runtime.state = runtime.restart_pending and "RESTART_PENDING" or "IDLE"
  M.normalize_layout()
end

local function on_degraded(session)
  if runtime.active ~= session then return end
  runtime.state = "DEGRADED"
end

local function session_options(request, directory)
  return {
    request = request,
    identity = runtime.identity,
    directory = directory,
    cache_root = cache_root(),
    keeper_bufnr = runtime.keeper_bufnr,
    window = runtime.window,
    on_state = function(_, state)
      if state == "WAITING_UI" or state == "EDITING" or state == "SUSPENDED" or state == "RECOVERY_REQUIRED" then
        runtime.state = state
      end
    end,
    on_terminal = on_terminal,
    on_degraded = on_degraded,
    ensure_layout = M.normalize_layout,
  }
end

local function decision_path(directory)
  return path_join(directory, "decision.json")
end

local function result_path(directory)
  return path_join(directory, "result.json")
end

local function publish_rejection(request, directory, reason)
  local decision = protocol.make_decision(request, runtime.identity, "rejected", reason)
  local ok, err = protocol.publish_decision(decision_path(directory), decision)
  if not ok and err and err.code == "TERMINAL_CONFLICT" then runtime.state = "DEGRADED" end
  return ok, err
end

local function existing_outcome(session_id, nonce, directory)
  local result = protocol.read_json(result_path(directory), { allow_missing = true })
  if result then
    local valid = protocol.validate_result(result)
    if valid and result.session_id == session_id and result.nonce ~= nonce then
      return { accepted = false, state = "rejected", reason = "NONCE_MISMATCH" }
    end
    if not valid or result.session_id ~= session_id then
      return { accepted = false, state = "degraded", reason = "TERMINAL_CONFLICT" }
    end
    return { accepted = true, state = result.status, session_id = session_id, result = result }
  end
  local decision = protocol.read_json(decision_path(directory), { allow_missing = true })
  if decision then
    local valid = protocol.validate_decision(decision)
    if valid and decision.session_id == session_id and decision.nonce ~= nonce then
      return { accepted = false, state = "rejected", reason = "NONCE_MISMATCH" }
    end
    if not valid or decision.session_id ~= session_id then
      return { accepted = false, state = "degraded", reason = "TERMINAL_CONFLICT" }
    end
    if decision.outcome == "rejected" then
      return { accepted = false, state = "rejected", reason = decision.reason, session_id = session_id }
    end
    if not runtime.active then
      -- This process cannot impersonate the generation that accepted the
      -- request. The supervisor must first prove that generation dead and
      -- publish its fail-closed terminal result.
      return { accepted = false, state = "recovery_required", reason = "ACCEPTED_SESSION_ORPHANED", session_id = session_id }
    end
  end
end

local function raw_request(session_id, directory)
  local request = protocol.read_json(path_join(directory, "request.json"), { allow_missing = true })
  if type(request) ~= "table" then return nil end
  if request.session_id ~= session_id then return nil end
  return request
end

function M.identity()
  return vim.deepcopy(runtime.identity)
end

function M.health()
  local bootstrap_health = bootstrap.health()
  return {
    state = runtime.state,
    active_session = runtime.active and runtime.active.request.session_id or vim.NIL,
    prewarmed = bootstrap_health.prewarmed == true,
    adapters_ok = bootstrap_health.adapters_ok == true,
    layout_ok = M.layout_ok(),
    restart_pending = runtime.restart_pending,
    started_at = runtime.started_at or vim.NIL,
    protocol_version = protocol.VERSION,
    server_uuid = runtime.identity.server_uuid,
    generation = runtime.identity.generation,
    config_fingerprint = runtime.identity.config_fingerprint,
  }
end

function M.open(session_id, nonce)
  if not runtime.ready then return { accepted = false, state = runtime.state:lower(), reason = "SERVER_NOT_READY" } end
  if not protocol.is_session_id(session_id) then return { accepted = false, state = "rejected", reason = "INVALID_SESSION_ID" } end
  if not protocol.is_nonce(nonce) then return { accepted = false, state = "rejected", reason = "INVALID_NONCE" } end
  if runtime.active then
    local active = runtime.active
    if active.request.session_id == session_id then
      if active.request.nonce ~= nonce then return { accepted = false, state = "rejected", reason = "NONCE_MISMATCH" } end
      return { accepted = true, state = active.state:lower(), session_id = session_id }
    end
  end
  local directory, directory_error = protocol.session_dir(session_id, cache_root())
  if not directory then return { accepted = false, state = "rejected", reason = directory_error.code } end
  local existing = existing_outcome(session_id, nonce, directory)
  if existing then return existing end
  local request, loaded_directory_or_error = protocol.load_request(session_id, nonce, { cache_root = cache_root() })
  if not request then
    local error_value = loaded_directory_or_error or { code = "INVALID_REQUEST" }
    local raw = raw_request(session_id, directory)
    if raw and raw.nonce == nonce and protocol.is_nonce(raw.nonce) then publish_rejection(raw, directory, error_value.code) end
    return { accepted = false, state = "rejected", reason = error_value.code }
  end
  directory = loaded_directory_or_error
  if runtime.active or runtime.state ~= "IDLE" then
    publish_rejection(request, directory, "BUSY")
    return { accepted = false, state = "rejected", reason = "BUSY", session_id = session_id }
  end
  runtime.state = "PREPARING"
  local session, prepare_error = session_runtime.new(session_options(request, directory))
  if not session then
    runtime.state = "IDLE"
    local reason = (prepare_error and prepare_error.code) or "PREPARE_FAILED"
    publish_rejection(request, directory, reason)
    return { accepted = false, state = "rejected", reason = reason, session_id = session_id }
  end
  local decision = protocol.make_decision(request, runtime.identity, "accepted")
  local published, publish_error = protocol.publish_decision(decision_path(directory), decision)
  if not published then
    session:cleanup(false)
    runtime.state = publish_error and publish_error.code == "TERMINAL_CONFLICT" and "DEGRADED" or "IDLE"
    return {
      accepted = false,
      state = runtime.state:lower(),
      reason = (publish_error and publish_error.code) or "DECISION_PUBLISH_FAILED",
    }
  end
  runtime.active = session
  runtime.state = "WAITING_UI"
  session:start_attach_deadline()
  return { accepted = true, state = "waiting_ui", session_id = session_id }
end

function M.resume(session_id, nonce)
  local active = runtime.active
  if not active or active.request.session_id ~= session_id then
    return { accepted = false, state = runtime.state:lower(), reason = "SESSION_NOT_ACTIVE" }
  end
  if active.request.nonce ~= nonce then return { accepted = false, state = "rejected", reason = "NONCE_MISMATCH" } end
  local ok, err = active:resume()
  if not ok then return { accepted = false, state = active.state:lower(), reason = err } end
  runtime.state = "WAITING_UI"
  return { accepted = true, state = "waiting_ui", session_id = session_id }
end

function M.status(session_id)
  if runtime.active and (not session_id or runtime.active.request.session_id == session_id) then
    local active = runtime.active
    return {
      state = active.state:lower(),
      session_id = active.request.session_id,
      nonce = active.request.nonce,
      bufnr = active.bufnr,
      ui_channel = active.ui_channel or vim.NIL,
      server_uuid = runtime.identity.server_uuid,
      generation = runtime.identity.generation,
    }
  end
  if not session_id then return M.health() end
  local directory = protocol.session_dir(session_id, cache_root())
  if not directory then return { state = "unknown", reason = "INVALID_SESSION_ID" } end
  local result = protocol.read_json(result_path(directory), { allow_missing = true })
  if result then return { state = result.status, session_id = session_id, result = result } end
  local decision = protocol.read_json(decision_path(directory), { allow_missing = true })
  if decision then return { state = decision.outcome, session_id = session_id, decision = decision } end
  return { state = "unknown", session_id = session_id }
end

function M.abort(session_id, nonce, reason)
  local active = runtime.active
  if not active or active.request.session_id ~= session_id then return { aborted = false, reason = "SESSION_NOT_ACTIVE" } end
  if active.request.nonce ~= nonce then return { aborted = false, reason = "NONCE_MISMATCH" } end
  active:abort(reason or "ABORTED")
  return { aborted = true, state = runtime.state:lower() }
end

function M.shutdown(options)
  options = options or {}
  if runtime.active and not options.abort_active then return { stopping = false, reason = "BUSY" } end
  if runtime.active then runtime.active:abort(options.reason or "SERVER_SHUTDOWN") end
  runtime.state = "STOPPING"
  vim.defer_fn(function() pcall(vim.cmd, "qa!") end, 20)
  return { stopping = true }
end

local rpc_methods = {
  identity = function() return M.identity() end,
  health = function() return M.health() end,
  open = function(args) return M.open(args.session_id, args.nonce) end,
  resume = function(args) return M.resume(args.session_id, args.nonce) end,
  status = function(args) return M.status(args.session_id) end,
  abort = function(args) return M.abort(args.session_id, args.nonce, args.reason) end,
  shutdown = function(args) return M.shutdown(args) end,
}

function M.rpc_json(method, arguments_json)
  local handler = rpc_methods[method]
  if not handler then return vim.json.encode { ok = false, error = "UNKNOWN_METHOD" } end
  local arguments = {}
  if arguments_json and arguments_json ~= "" then
    local ok, decoded = pcall(vim.json.decode, arguments_json)
    if not ok or type(decoded) ~= "table" then return vim.json.encode { ok = false, error = "INVALID_ARGUMENTS" } end
    arguments = decoded
  end
  local ok, result = pcall(handler, arguments)
  if not ok then return vim.json.encode { ok = false, error = "RPC_FAILED" } end
  return vim.json.encode { ok = true, result = result }
end

local function channel_from_event(args)
  local event = vim.v.event or {}
  return tonumber(event.chan or (args.data and args.data.chan))
end

local function install_autocmds()
  runtime.augroup = vim.api.nvim_create_augroup("EditAnywhereServer", { clear = true })
  vim.api.nvim_create_autocmd("UIEnter", {
    group = runtime.augroup,
    callback = function(args)
      local channel = channel_from_event(args)
      if not channel then return end
      if not runtime.active then
        runtime.state = "DEGRADED"
        return
      end
      if runtime.active.state ~= "WAITING_UI" then
        runtime.active:degrade("UNEXPECTED_UI_ATTACH")
        return
      end
      if not M.normalize_layout() then
        runtime.active:degrade("LAYOUT_INVALID")
        return
      end
      runtime.active:attach(channel)
    end,
    desc = "Attach a matching remote UI to the active Edit Anywhere session",
  })
  vim.api.nvim_create_autocmd("UILeave", {
    group = runtime.augroup,
    callback = function(args)
      local channel = channel_from_event(args)
      if channel and runtime.active then runtime.active:on_ui_leave(channel) end
    end,
    desc = "Finalize or suspend Edit Anywhere only for the matching UI channel",
  })
  vim.api.nvim_create_autocmd({ "WinNew", "TabNew" }, {
    group = runtime.augroup,
    callback = function()
      vim.schedule(function()
        if not runtime.ready or M.normalize_layout() then return end
        if runtime.active then runtime.active:degrade("LAYOUT_INVALID") else runtime.state = "DEGRADED" end
      end)
    end,
    desc = "Maintain the dedicated one-window Edit Anywhere layout",
  })
  vim.api.nvim_create_autocmd("FileType", {
    group = runtime.augroup,
    pattern = { "neo-tree", "NvimTree" },
    callback = function()
      vim.schedule(function()
        if not runtime.started then return end
        if M.normalize_layout() then return end
        if runtime.active then runtime.active:degrade("LAYOUT_INVALID") else runtime.state = "DEGRADED" end
      end)
    end,
    desc = "Suppress file trees in the dedicated Edit Anywhere server",
  })
end

local function finish_start()
  if runtime.ready then return end
  runtime.state = "WARMING"
  pcall(vim.cmd, "silent! argdelete *")
  set_keeper_options(runtime.keeper_bufnr)
  bootstrap.late()
  if not M.normalize_layout() then
    runtime.state = "DEGRADED"
    return
  end
  runtime.ready = true
  runtime.state = "IDLE"
  write_identity()
end

function M.start()
  if runtime.started then return M.health() end
  if vim.g.edit_anywhere_server ~= 1 then error("edit_anywhere.server requires g:edit_anywhere_server=1") end
  runtime.started = true
  vim.g.edit_anywhere_disable_filetree = 1
  suppress_filetree_startup_autocmds()
  runtime.state = "STARTING"
  runtime.started_at = protocol.now_unix_ms()
  runtime.keeper_bufnr = vim.api.nvim_get_current_buf()
  runtime.window = vim.api.nvim_get_current_win()
  set_keeper_options(runtime.keeper_bufnr)
  bootstrap.early()
  install_autocmds()
  _G.edit_anywhere_rpc_json = M.rpc_json
  -- start() is installed with -c, after the host config and file argument have
  -- been processed. Deferring one loop also lets any remaining startup
  -- callbacks settle before the arglist is cleared and layout is normalized.
  vim.schedule(finish_start)
  return M.health()
end

function M._reset_for_test()
  if runtime.active then runtime.active:cleanup(false) end
  if runtime.augroup then pcall(vim.api.nvim_del_augroup_by_id, runtime.augroup) end
  runtime.started = false
  runtime.ready = false
  runtime.state = "STOPPED"
  runtime.active = nil
  runtime.keeper_bufnr = nil
  runtime.window = nil
  runtime.restart_pending = false
  runtime.started_at = nil
  runtime.augroup = nil
  _G.edit_anywhere_rpc_json = nil
end

return M
