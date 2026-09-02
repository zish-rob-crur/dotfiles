local uv = vim.uv or vim.loop
local protocol = require "edit_anywhere.protocol"
local context = require "edit_anywhere.context"

local M = {}
local Session = {}
Session.__index = Session

M.transitions = {
  PREPARING = { WAITING_UI = true, FAILED = true },
  WAITING_UI = { EDITING = true, FAILED = true },
  EDITING = { COMMIT_PENDING = true, CANCEL_PENDING = true, SUSPENDED = true, FAILED = true },
  COMMIT_PENDING = { DETACHING = true, EDITING = true, SUSPENDED = true, RECOVERY_REQUIRED = true },
  CANCEL_PENDING = { DETACHING = true, EDITING = true, SUSPENDED = true },
  DETACHING = { COMMITTED = true, CANCELLED = true, SUSPENDED = true, RECOVERY_REQUIRED = true },
  SUSPENDED = { WAITING_UI = true, FAILED = true },
  RECOVERY_REQUIRED = { WAITING_UI = true, FAILED = true },
  COMMITTED = {},
  CANCELLED = {},
  FAILED = {},
  DEGRADED = {},
}

local terminal = { COMMITTED = true, CANCELLED = true, FAILED = true }

local function path_join(left, right)
  return left .. "/" .. right
end

local function set_modified(bufnr, value)
  if vim.api.nvim_buf_is_valid(bufnr) then vim.bo[bufnr].modified = value end
end

local function close_timer(timer)
  if timer and not timer:is_closing() then
    timer:stop()
    timer:close()
  end
end

local function remove_attempt(directory)
  if not directory then return end
  pcall(uv.fs_unlink, directory .. "/meta.json")
  pcall(uv.fs_unlink, directory .. "/commit.md")
  pcall(uv.fs_rmdir, directory)
end

local function ui_is_attached(channel)
  for _, ui in ipairs(vim.api.nvim_list_uis()) do
    if tonumber(ui.chan) == tonumber(channel) then return true end
  end
  return false
end

function M.can_transition(from, to)
  return M.transitions[from] and M.transitions[from][to] == true
end

function M.insert_command(cursor, start_insert)
  if not start_insert then return nil end
  return cursor == "end" and "startinsert!" or "startinsert"
end

function Session:set_state(state, detail)
  self.state, self.state_detail = state, detail
  self:publish_state()
  if self.on_state then pcall(self.on_state, self, state) end
end

function Session:transition(next_state, detail)
  if self.state == next_state then return true end
  if not M.can_transition(self.state, next_state) then
    return nil, ("invalid session transition %s -> %s"):format(self.state, next_state)
  end
  self:set_state(next_state, detail)
  return true
end

function Session:publish_state()
  local payload = {
    protocol_version = protocol.VERSION,
    session_id = self.request.session_id,
    nonce = self.request.nonce,
    server_uuid = self.identity.server_uuid,
    generation = self.identity.generation,
    status = self.state:lower(),
    reason = self.state_detail or vim.NIL,
    updated_at_unix_ms = protocol.now_unix_ms(),
  }
  return protocol.atomic_write_json(path_join(self.directory, "state.json"), payload)
end

function Session:serialize()
  if not vim.api.nvim_buf_is_valid(self.bufnr) then return nil, "buffer is invalid" end
  local lines = vim.api.nvim_buf_get_lines(self.bufnr, 0, -1, false)
  local text = table.concat(lines, "\n")
  if self.trailing_newline then text = text .. "\n" end
  return text
end

function Session:write_recovery()
  if self.cleaned or not vim.api.nvim_buf_is_valid(self.bufnr) then return true end
  local text, err = self:serialize()
  if not text then return nil, err end
  return protocol.atomic_write(path_join(self.directory, "recovery.md"), text)
end

function Session:schedule_recovery()
  if self.cleaned then return end
  if not self.recovery_timer then self.recovery_timer = uv.new_timer() end
  self.recovery_timer:stop()
  self.recovery_timer:start(400, 0, vim.schedule_wrap(function()
    if self.cleaned then return end
    self:write_recovery()
  end))
end

function Session:install_guards()
  local bufnr = self.bufnr
  vim.api.nvim_buf_create_user_command(bufnr, "EditAnywhereCancel", function() self:cancel(bufnr) end, {
    desc = "Cancel this Edit Anywhere session",
  })
  vim.api.nvim_buf_create_user_command(bufnr, "EditAnywhereContext", function() context.show(bufnr) end, {
    desc = "Show the OCR context for this Edit Anywhere session",
  })
  vim.keymap.set("n", "ZZ", function() self:commit(bufnr) end, {
    buffer = bufnr,
    silent = true,
    nowait = true,
    desc = "Commit Edit Anywhere session",
  })
  vim.keymap.set({ "n", "i" }, "<C-c><C-c>", function() self:cancel(bufnr) end, {
    buffer = bufnr,
    silent = true,
    nowait = true,
    desc = "Cancel Edit Anywhere session",
  })
  vim.keymap.set("n", "ZQ", function() self:cancel(bufnr) end, {
    buffer = bufnr,
    silent = true,
    nowait = true,
    desc = "Cancel Edit Anywhere session",
  })
  vim.keymap.set("n", "<Leader>oc", function() context.show(bufnr) end, {
    buffer = bufnr,
    silent = true,
    nowait = true,
    desc = "Show Edit Anywhere OCR context",
  })
  self.augroup = vim.api.nvim_create_augroup("EditAnywhereSession" .. bufnr, { clear = true })
  vim.api.nvim_create_autocmd("BufWriteCmd", {
    group = self.augroup,
    buffer = bufnr,
    callback = function()
      vim.notify("Edit Anywhere: use ZZ to commit or ZQ to cancel", vim.log.levels.WARN)
    end,
    desc = "Block host writes for Edit Anywhere acwrite buffers",
  })
  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
    group = self.augroup,
    buffer = bufnr,
    callback = function() self:schedule_recovery() end,
    desc = "Maintain the Edit Anywhere recovery shadow",
  })
end

function Session:activate_context()
  local context_path = path_join(self.directory, self.request.context.relative_path)
  local valid, err = protocol.validate_session_path(self.request.session_id, context_path, {
    cache_root = self.cache_root,
  })
  if not valid then return nil, err end
  context.activate {
    session_id = self.request.session_id,
    bufnr = self.bufnr,
    token = self.request.context.token,
    generation = self.identity.generation,
    source = self.request.context.source,
    path = context_path,
  }
  return true
end

function Session:prepare_buffer()
  local input_path = path_join(self.directory, "input.md")
  local input, err = protocol.read_file(input_path)
  if input == nil then return nil, err end
  self.trailing_newline = input:sub(-1) == "\n"
  local bufnr = vim.api.nvim_create_buf(false, true)
  self.bufnr = bufnr
  vim.b[bufnr].edit_anywhere_session_id = self.request.session_id
  vim.b[bufnr].edit_anywhere_nonce = self.request.nonce
  vim.b[bufnr].edit_anywhere_generation = self.identity.generation
  vim.b[bufnr].edit_anywhere_context_token = self.request.context.token
  vim.b[bufnr].edit_anywhere_buffer = true
  vim.bo[bufnr].buftype = "acwrite"
  vim.bo[bufnr].bufhidden = "hide"
  vim.bo[bufnr].swapfile = false
  vim.bo[bufnr].undofile = false
  vim.bo[bufnr].modifiable = true
  local lines = vim.split(input, "\n", { plain = true })
  if self.trailing_newline and lines[#lines] == "" then table.remove(lines) end
  if #lines == 0 then lines = { "" } end
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, lines)
  vim.api.nvim_buf_set_name(bufnr, ("edit-anywhere://%s/document.md"):format(self.request.session_id))
  set_modified(bufnr, false)
  self:install_guards()
  vim.bo[bufnr].filetype = self.request.editor.filetype
  set_modified(bufnr, false)
  local context_ok, context_err = self:activate_context()
  if not context_ok then return nil, context_err end
  return true
end

function Session:start_attach_deadline()
  close_timer(self.attach_timer)
  self.attach_timer = uv.new_timer()
  self.attach_timer:start(2000, 0, vim.schedule_wrap(function()
    if self.cleaned or self.ui_channel or self.state ~= "WAITING_UI" then return end
    self:fail("UI_ATTACH_TIMEOUT")
  end))
end

function Session:publish_ui_ready()
  local ready = {
    protocol_version = protocol.VERSION,
    session_id = self.request.session_id,
    nonce = self.request.nonce,
    server_uuid = self.identity.server_uuid,
    generation = self.identity.generation,
    status = "ready",
    ui_channel = self.ui_channel,
    ready_at_unix_ms = protocol.now_unix_ms(),
  }
  local ok, err = protocol.atomic_write_json(path_join(self.directory, "ui-ready.json"), ready)
  if not ok then return nil, err end
  vim.opt.title = true
  vim.opt.titlestring = "EDIT_ANYWHERE_READY:" .. self.request.session_id
  -- titlestring is the PTY/AX readiness sentinel. Flush it in this loop rather
  -- than waiting for the next incidental redraw; the screen itself was fully
  -- prepared by the preceding scheduled loop.
  pcall(vim.cmd, "redrawstatus")
  pcall(vim.cmd, "redraw")
  return true
end

function Session:attach(channel)
  if self.cleaned then return nil, "session is cleaned" end
  if self.ui_channel and self.ui_channel ~= channel then return nil, "another UI is already attached" end
  if self.state ~= "WAITING_UI" then return nil, "session is not waiting for UI" end
  self.ui_channel = channel
  close_timer(self.attach_timer)
  self.attach_timer = nil
  if not vim.api.nvim_win_is_valid(self.window) then return self:fail("SESSION_WINDOW_INVALID") end
  vim.api.nvim_set_current_win(self.window)
  vim.api.nvim_win_set_buf(self.window, self.bufnr)
  vim.wo[self.window].wrap = true
  vim.wo[self.window].linebreak = true
  if self.request.editor.cursor == "start" then
    vim.api.nvim_win_set_cursor(self.window, { 1, 0 })
  else
    local count = vim.api.nvim_buf_line_count(self.bufnr)
    local last = vim.api.nvim_buf_get_lines(self.bufnr, count - 1, count, false)[1] or ""
    vim.api.nvim_win_set_cursor(self.window, { count, #last })
  end
  self:transition("EDITING")
  vim.schedule(function()
    if self.cleaned or self.ui_channel ~= channel or self.state ~= "EDITING" then return end
    local insert_command = M.insert_command(self.request.editor.cursor, self.request.editor.start_insert)
    if insert_command then pcall(vim.cmd, insert_command) end
    pcall(vim.cmd, "redraw!")
    vim.schedule(function()
      if self.cleaned or self.ui_channel ~= channel or self.state ~= "EDITING" then return end
      if not ui_is_attached(channel) then
        self:on_ui_leave(channel)
        return
      end
      if self.ensure_layout and not self.ensure_layout() then
        self:degrade("LAYOUT_INVALID")
        return
      end
      local ok, err = self:publish_ui_ready()
      if not ok then self:fail("UI_READY_PUBLISH_FAILED:" .. tostring(err and err.code or err)) end
    end)
  end)
  return true
end

function Session:create_attempt(text, original_modified)
  local attempts = path_join(self.directory, "attempts")
  local ok, err = protocol.ensure_directory(attempts, 448)
  if not ok then return nil, err end
  local token = protocol.random_token()
  local temporary = attempts .. "/.tmp-" .. token
  local final = attempts .. "/" .. token
  local made, mkdir_err = uv.fs_mkdir(temporary, 448)
  if not made then return nil, { code = "ATTEMPT_MKDIR_FAILED", message = tostring(mkdir_err) } end
  local digest = protocol.sha256(text)
  local metadata = {
    protocol_version = protocol.VERSION,
    session_id = self.request.session_id,
    nonce = self.request.nonce,
    generation = self.identity.generation,
    attempt_token = token,
    body_sha256 = digest,
    original_modified = original_modified,
    created_at_unix_ms = protocol.now_unix_ms(),
  }
  ok, err = protocol.atomic_write_json(temporary .. "/meta.json", metadata)
  if ok then ok, err = protocol.atomic_write(temporary .. "/commit.md", text) end
  if not ok then
    remove_attempt(temporary)
    return nil, err
  end
  local renamed, rename_err = uv.fs_rename(temporary, final)
  if not renamed then
    remove_attempt(temporary)
    return nil, { code = "ATTEMPT_RENAME_FAILED", message = tostring(rename_err) }
  end
  return { token = token, directory = final, digest = digest, original_modified = original_modified }
end

function Session:restore_after_detach_failure(state)
  close_timer(self.intent_timer)
  self.intent_timer = nil
  self.detach_intent = nil
  if vim.api.nvim_win_is_valid(self.window) and vim.api.nvim_buf_is_valid(self.bufnr) then
    vim.api.nvim_win_set_buf(self.window, self.bufnr)
    set_modified(self.bufnr, self.original_modified)
  end
  self:set_state(state or "EDITING")
end

function Session:begin_detach(kind, attempt)
  if not self.ui_channel then return nil, "no matching UI channel" end
  local original_modified = self.original_modified
  self.detach_intent = {
    kind = kind,
    session_id = self.request.session_id,
    expected_chan = self.ui_channel,
    token = protocol.random_token(),
    attempt = attempt,
    original_modified = original_modified,
    consumed = false,
  }
  self:transition "DETACHING"
  if not vim.api.nvim_win_is_valid(self.window) or not vim.api.nvim_buf_is_valid(self.keeper_bufnr) then
    self:restore_after_detach_failure()
    return nil, "keeper window is invalid"
  end
  set_modified(self.bufnr, false)
  vim.api.nvim_win_set_buf(self.window, self.keeper_bufnr)
  self.intent_timer = uv.new_timer()
  self.intent_timer:start(2000, 0, vim.schedule_wrap(function()
    if self.cleaned or not self.detach_intent or self.detach_intent.consumed then return end
    if attempt then remove_attempt(attempt.directory) end
    self:write_recovery()
    self.detach_intent = nil
    self.ui_channel = nil
    set_modified(self.bufnr, original_modified)
    self:set_state("SUSPENDED", "DETACH_CONFIRM_TIMEOUT")
  end))
  local ok, detach_error = pcall(vim.cmd, "detach")
  if not ok then
    if attempt then remove_attempt(attempt.directory) end
    self:restore_after_detach_failure("EDITING")
    return nil, tostring(detach_error)
  end
  return true
end

function Session:commit(bufnr)
  if bufnr ~= self.bufnr or self.state ~= "EDITING" or not self.ui_channel then return nil, "session is not editable" end
  self.original_modified = vim.bo[self.bufnr].modified
  local text, serialize_error = self:serialize()
  if not text then return nil, serialize_error end
  local attempt, attempt_error = self:create_attempt(text, self.original_modified)
  if not attempt then
    vim.notify("Edit Anywhere commit failed; editor remains open", vim.log.levels.ERROR)
    return nil, attempt_error
  end
  self.attempt = attempt
  self:transition "COMMIT_PENDING"
  local ok, err = self:begin_detach("commit", attempt)
  if not ok then
    self.attempt = nil
    vim.notify("Edit Anywhere detach failed; editor remains open", vim.log.levels.ERROR)
    return nil, err
  end
  return true
end

function Session:cancel(bufnr)
  if bufnr ~= self.bufnr or self.state ~= "EDITING" or not self.ui_channel then return nil, "session is not editable" end
  self.original_modified = vim.bo[self.bufnr].modified
  self:transition "CANCEL_PENDING"
  if self.attempt then
    remove_attempt(self.attempt.directory)
    self.attempt = nil
  end
  local output = path_join(self.directory, "output.md")
  local valid_output = protocol.validate_session_path(self.request.session_id, output, { cache_root = self.cache_root })
  if valid_output and uv.fs_lstat(output) then pcall(uv.fs_unlink, output) end
  local ok, err = self:begin_detach("cancel")
  if not ok then
    vim.notify("Edit Anywhere detach failed; editor remains open", vim.log.levels.ERROR)
    return nil, err
  end
  return true
end

function Session:finalize_commit(intent)
  local attempt = intent.attempt
  if not attempt then return self:recovery_required("ATTEMPT_MISSING") end
  local metadata, metadata_err = protocol.read_json(attempt.directory .. "/meta.json")
  local candidate, candidate_err = protocol.read_file(attempt.directory .. "/commit.md")
  if not metadata or not candidate then
    return self:recovery_required((metadata_err or candidate_err or {}).code or "ATTEMPT_READ_FAILED")
  end
  local digest = protocol.sha256(candidate)
  if metadata.session_id ~= self.request.session_id
    or metadata.nonce ~= self.request.nonce
    or metadata.generation ~= self.identity.generation
    or metadata.attempt_token ~= attempt.token
    or metadata.body_sha256 ~= digest
  then
    return self:degrade("ATTEMPT_CONFLICT")
  end
  local output_path = path_join(self.directory, "output.md")
  local output_ok, output_err = protocol.atomic_write(output_path, candidate)
  if not output_ok then return self:recovery_required(output_err.code or "OUTPUT_PUBLISH_FAILED") end
  local result = protocol.make_result(self.request, self.identity, "committed", nil, digest)
  local result_ok, result_status = protocol.publish_result(path_join(self.directory, "result.json"), result)
  if not result_ok then
    if result_status and result_status.code == "TERMINAL_CONFLICT" then return self:degrade("RESULT_CONFLICT") end
    return self:recovery_required((result_status or {}).code or "RESULT_PUBLISH_FAILED")
  end
  self:transition "COMMITTED"
  self:cleanup(true)
  if self.on_terminal then self.on_terminal(self, result) end
  return true
end

function Session:finalize_cancel()
  local result = protocol.make_result(self.request, self.identity, "cancelled", nil, nil)
  local ok, status = protocol.publish_result(path_join(self.directory, "result.json"), result)
  if not ok then
    if status and status.code == "TERMINAL_CONFLICT" then return self:degrade("RESULT_CONFLICT") end
    return self:recovery_required((status or {}).code or "RESULT_PUBLISH_FAILED")
  end
  self:transition "CANCELLED"
  self:cleanup(true)
  if self.on_terminal then self.on_terminal(self, result) end
  return true
end

function Session:on_ui_leave(channel)
  if self.cleaned or self.ui_channel ~= channel then return false end
  close_timer(self.intent_timer)
  self.intent_timer = nil
  local intent = self.detach_intent
  self.ui_channel = nil
  if not intent or intent.consumed or intent.expected_chan ~= channel or intent.session_id ~= self.request.session_id then
    if self.attempt then remove_attempt(self.attempt.directory) end
    self.attempt = nil
    self.detach_intent = nil
    set_modified(self.bufnr, self.original_modified == true)
    self:write_recovery()
    if self.state ~= "SUSPENDED" then
      self:set_state("SUSPENDED", "UNEXPECTED_UI_LEAVE")
    end
    return false
  end
  intent.consumed = true
  self.detach_intent = nil
  if intent.kind == "commit" then return self:finalize_commit(intent) end
  if intent.kind == "cancel" then return self:finalize_cancel() end
  return self:degrade("INVALID_DETACH_INTENT")
end

function Session:recovery_required(reason)
  self:write_recovery()
  set_modified(self.bufnr, self.original_modified == true)
  self:set_state("RECOVERY_REQUIRED", reason)
  return nil, reason
end

function Session:degrade(reason)
  self:write_recovery()
  set_modified(self.bufnr, self.original_modified == true)
  self:set_state("DEGRADED", reason)
  if self.on_degraded then self.on_degraded(self, reason) end
  return nil, reason
end

function Session:fail(reason)
  if self.cleaned or terminal[self.state] then return true end
  self:write_recovery()
  local result = protocol.make_result(self.request, self.identity, "failed", reason, nil)
  local ok, err = protocol.publish_result(path_join(self.directory, "result.json"), result)
  if not ok then
    if err and err.code == "TERMINAL_CONFLICT" then return self:degrade("RESULT_CONFLICT") end
    return self:recovery_required((err or {}).code or "RESULT_PUBLISH_FAILED")
  end
  self:set_state("FAILED", reason)
  self:cleanup(false)
  if self.on_terminal then self.on_terminal(self, result) end
  return nil, reason
end

function Session:resume()
  if self.state ~= "SUSPENDED" and self.state ~= "RECOVERY_REQUIRED" then return nil, "session is not resumable" end
  self:set_state "WAITING_UI"
  self:start_attach_deadline()
  return true
end

function Session:abort(reason)
  self:write_recovery()
  return self:fail(reason or "ABORTED")
end

function Session:cleanup(remove_recovery)
  if self.cleaned then return true end
  self.cleaned = true
  close_timer(self.attach_timer)
  close_timer(self.intent_timer)
  close_timer(self.recovery_timer)
  self.attach_timer = nil
  self.intent_timer = nil
  self.recovery_timer = nil
  context.cleanup(self.bufnr)
  if self.augroup then pcall(vim.api.nvim_del_augroup_by_id, self.augroup) end
  if remove_recovery then pcall(uv.fs_unlink, path_join(self.directory, "recovery.md")) end
  if vim.api.nvim_buf_is_valid(self.bufnr) then
    set_modified(self.bufnr, false)
    pcall(vim.api.nvim_buf_delete, self.bufnr, { force = true })
  end
  return true
end

function M.new(options)
  assert(type(options) == "table", "session options are required")
  local self = setmetatable({
    request = assert(options.request, "request is required"),
    identity = assert(options.identity, "identity is required"),
    directory = assert(options.directory, "directory is required"),
    cache_root = options.cache_root or protocol.cache_root(),
    keeper_bufnr = assert(options.keeper_bufnr, "keeper buffer is required"),
    window = assert(options.window, "window is required"),
    state = "PREPARING",
    on_state = options.on_state,
    on_terminal = options.on_terminal,
    on_degraded = options.on_degraded,
    ensure_layout = options.ensure_layout,
  }, Session)
  local ok, err = self:prepare_buffer()
  if not ok then
    if self.bufnr and vim.api.nvim_buf_is_valid(self.bufnr) then self:cleanup(false) end
    return nil, err
  end
  self:transition "WAITING_UI"
  return self
end

return M
