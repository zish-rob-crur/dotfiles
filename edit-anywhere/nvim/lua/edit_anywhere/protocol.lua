local uv = vim.uv or vim.loop

local M = {
  VERSION = 1,
  FAILED_CONTEXT_SENTINEL = "__NVIM_EXTERNAL_CONTEXT_FAILED__",
}

local decision_identity_keys = {
  "protocol_version",
  "session_id",
  "nonce",
  "outcome",
  "reason",
  "fallback_allowed",
  "writer",
  "server_uuid",
  "generation",
  "config_fingerprint",
}

local result_identity_keys = {
  "protocol_version",
  "session_id",
  "nonce",
  "status",
  "reason",
  "server_uuid",
  "generation",
  "output_sha256",
}

local function fail(code, message)
  return nil, { code = code, message = message or code }
end

local function is_null(value)
  return value == nil or value == vim.NIL
end

local function null_if_nil(value)
  if value == nil then return vim.NIL end
  return value
end

local function scalar_equal(left, right)
  if is_null(left) and is_null(right) then return true end
  return left == right
end

local function exact_keys(value, required, optional)
  if type(value) ~= "table" or vim.islist(value) then return false, "must be an object" end
  local allowed = {}
  for _, key in ipairs(required or {}) do
    allowed[key] = true
    if value[key] == nil then return false, "missing field: " .. key end
  end
  for _, key in ipairs(optional or {}) do
    allowed[key] = true
  end
  for key in pairs(value) do
    if not allowed[key] then return false, "unknown field: " .. tostring(key) end
  end
  return true
end

local function integer(value)
  return type(value) == "number" and value == math.floor(value)
end

local function current_uid()
  if uv.getuid then return uv.getuid() end
  if uv.os_get_passwd then
    local passwd = uv.os_get_passwd()
    if passwd then return passwd.uid end
  end
end

local function path_join(...)
  return table.concat({ ... }, "/"):gsub("/+", "/")
end

local function dirname(path)
  return vim.fs.dirname(path)
end

local function ensure_directory(path, mode)
  local stat = uv.fs_lstat(path)
  if stat then
    if stat.type ~= "directory" then return fail("NOT_DIRECTORY", path) end
    local uid = current_uid()
    if uid and stat.uid ~= uid then return fail("WRONG_OWNER", path) end
    return true
  end
  local parent = dirname(path)
  if parent and parent ~= path then
    local parent_stat = uv.fs_lstat(parent)
    if not parent_stat then
      local ok, err = ensure_directory(parent, mode)
      if not ok then return nil, err end
    elseif parent_stat.type ~= "directory" then
      return fail("NOT_DIRECTORY", parent)
    end
  end
  local ok, err = uv.fs_mkdir(path, mode or 448)
  if not ok and not tostring(err):match("EEXIST") then return fail("MKDIR_FAILED", tostring(err)) end
  stat = uv.fs_lstat(path)
  if not stat or stat.type ~= "directory" then return fail("NOT_DIRECTORY", path) end
  local uid = current_uid()
  if uid and stat.uid ~= uid then return fail("WRONG_OWNER", path) end
  pcall(uv.fs_chmod, path, mode or 448)
  return true
end

local function secure_existing_file(path)
  local stat = uv.fs_lstat(path)
  if not stat then return fail("MISSING_FILE", path) end
  if stat.type ~= "file" then return fail("INVALID_FILE_TYPE", path) end
  local uid = current_uid()
  if uid and stat.uid ~= uid then return fail("WRONG_OWNER", path) end
  return stat
end

local function random_suffix()
  local seed = table.concat({ tostring(uv.hrtime()), tostring(uv.os_getpid()), tostring({}) }, ":")
  if vim.fn.exists("*sha256") == 1 then return vim.fn.sha256(seed):sub(1, 20) end
  return seed:gsub("[^%w]", ""):sub(-20)
end

function M.now_unix_ms()
  local now = uv.gettimeofday()
  if type(now) == "table" then return now.sec * 1000 + math.floor((now.usec or 0) / 1000) end
  local sec, usec = uv.gettimeofday()
  return sec * 1000 + math.floor((usec or 0) / 1000)
end

function M.is_session_id(value)
  return type(value) == "string"
    and value:match("^%d%d%d%d%d%d%d%d%-%d%d%d%d%d%d%-%x%x%x%x%x%x%x%x$") ~= nil
end

function M.is_nonce(value)
  return type(value) == "string" and #value >= 22 and #value <= 256 and value:match("^[%w_-]+$") ~= nil
end

function M.cache_root()
  return vim.env.EDIT_ANYWHERE_CACHE_ROOT or vim.fn.expand("~/.cache/edit-anywhere")
end

function M.session_dir(session_id, cache_root)
  if not M.is_session_id(session_id) then return fail("INVALID_SESSION_ID", "invalid session id") end
  return path_join(cache_root or M.cache_root(), "sessions", session_id)
end

function M.ensure_directory(path, mode)
  return ensure_directory(path, mode)
end

function M.validate_session_path(session_id, path, options)
  options = options or {}
  local root, err = M.session_dir(session_id, options.cache_root)
  if not root then return nil, err end
  if type(path) ~= "string" or path == "" or not vim.startswith(path, root .. "/") then
    return fail("PATH_ESCAPE", "path is outside session")
  end
  if path:find("/../", 1, true) or path:sub(-3) == "/.." then
    return fail("PATH_ESCAPE", "parent traversal is forbidden")
  end
  local root_real = uv.fs_realpath(root)
  if root_real then
    local parent_real = uv.fs_realpath(dirname(path))
    if parent_real and parent_real ~= root_real and not vim.startswith(parent_real, root_real .. "/") then
      return fail("PATH_ESCAPE", "canonical parent escaped session")
    end
  end
  local lstat = uv.fs_lstat(path)
  if lstat and lstat.type == "link" then return fail("SYMLINK_FORBIDDEN", path) end
  if lstat and options.owner ~= false then
    local uid = current_uid()
    if uid and lstat.uid ~= uid then return fail("WRONG_OWNER", path) end
  end
  return path
end

function M.read_file(path, options)
  options = options or {}
  local lstat = uv.fs_lstat(path)
  if not lstat and options.allow_missing then return nil end
  local _, secure_err = secure_existing_file(path)
  if secure_err then return nil, secure_err end
  local fd, open_err = uv.fs_open(path, "r", 384)
  if not fd then return fail("OPEN_FAILED", tostring(open_err)) end
  local stat, stat_err = uv.fs_fstat(fd)
  if not stat then
    uv.fs_close(fd)
    return fail("STAT_FAILED", tostring(stat_err))
  end
  local maximum = options.max_bytes or 8 * 1024 * 1024
  if stat.size > maximum then
    uv.fs_close(fd)
    return fail("FILE_TOO_LARGE", path)
  end
  local data, read_err = uv.fs_read(fd, stat.size, 0)
  uv.fs_close(fd)
  if data == nil then return fail("READ_FAILED", tostring(read_err)) end
  return data
end

function M.read_json(path, options)
  local data, err = M.read_file(path, options)
  if data == nil then return nil, err end
  local ok, value = pcall(vim.json.decode, data)
  if not ok then return fail("INVALID_JSON", tostring(value)) end
  if type(value) ~= "table" or vim.islist(value) then return fail("INVALID_JSON", "top level must be an object") end
  return value
end

function M.atomic_write(path, data, options)
  options = options or {}
  if type(data) ~= "string" then return fail("INVALID_DATA", "atomic_write expects a string") end
  local ok, err = ensure_directory(dirname(path), 448)
  if not ok then return nil, err end
  local temporary = path .. ".tmp." .. random_suffix()
  local fd, open_err = uv.fs_open(temporary, "wx", options.mode or 384)
  if not fd then return fail("OPEN_FAILED", tostring(open_err)) end
  local written, write_err = uv.fs_write(fd, data, 0)
  if not written or written ~= #data then
    uv.fs_close(fd)
    uv.fs_unlink(temporary)
    return fail("WRITE_FAILED", tostring(write_err or "short write"))
  end
  pcall(uv.fs_fsync, fd)
  uv.fs_close(fd)
  pcall(uv.fs_chmod, temporary, options.mode or 384)
  local renamed, rename_err = uv.fs_rename(temporary, path)
  if not renamed then
    uv.fs_unlink(temporary)
    return fail("RENAME_FAILED", tostring(rename_err))
  end
  return true
end

function M.atomic_write_json(path, value, options)
  local ok, encoded = pcall(vim.json.encode, value)
  if not ok then return fail("JSON_ENCODE_FAILED", tostring(encoded)) end
  return M.atomic_write(path, encoded .. "\n", options)
end

function M.logical_equal(left, right, keys)
  if type(left) ~= "table" or type(right) ~= "table" then return false end
  for _, key in ipairs(keys) do
    if not scalar_equal(left[key], right[key]) then return false end
  end
  return true
end

function M.publish_json_no_replace(path, value, identity_keys)
  local ok, encoded = pcall(vim.json.encode, value)
  if not ok then return fail("JSON_ENCODE_FAILED", tostring(encoded)) end
  local parent_ok, parent_err = ensure_directory(dirname(path), 448)
  if not parent_ok then return nil, parent_err end
  local temporary = path .. ".tmp." .. random_suffix()
  local fd, open_err = uv.fs_open(temporary, "wx", 384)
  if not fd then return fail("OPEN_FAILED", tostring(open_err)) end
  local data = encoded .. "\n"
  local written, write_err = uv.fs_write(fd, data, 0)
  if not written or written ~= #data then
    uv.fs_close(fd)
    uv.fs_unlink(temporary)
    return fail("WRITE_FAILED", tostring(write_err or "short write"))
  end
  pcall(uv.fs_fsync, fd)
  uv.fs_close(fd)
  pcall(uv.fs_chmod, temporary, 384)
  local linked, link_err = uv.fs_link(temporary, path)
  uv.fs_unlink(temporary)
  if linked then return true, "published" end
  if not tostring(link_err):match("EEXIST") then return fail("PUBLISH_FAILED", tostring(link_err)) end
  local existing, existing_err = M.read_json(path)
  if not existing then return nil, existing_err end
  if M.logical_equal(existing, value, identity_keys) then return true, "idempotent" end
  return fail("TERMINAL_CONFLICT", path)
end

function M.validate_request(request, options)
  options = options or {}
  local ok, key_error = exact_keys(request, {
    "protocol_version",
    "session_id",
    "nonce",
    "created_at_unix_ms",
    "expires_at_unix_ms",
    "editor",
    "context",
    "source_window",
  })
  if not ok then return fail("INVALID_REQUEST", key_error) end
  if request.protocol_version ~= M.VERSION then return fail("PROTOCOL_MISMATCH", "unsupported protocol") end
  if not M.is_session_id(request.session_id) then return fail("INVALID_SESSION_ID") end
  if not M.is_nonce(request.nonce) then return fail("INVALID_NONCE") end
  if not integer(request.created_at_unix_ms) or not integer(request.expires_at_unix_ms) then
    return fail("INVALID_REQUEST", "timestamps must be integers")
  end
  if request.expires_at_unix_ms <= request.created_at_unix_ms then
    return fail("INVALID_REQUEST", "expiry must follow creation")
  end
  if (options.now_unix_ms or M.now_unix_ms()) > request.expires_at_unix_ms then return fail("STALE_REQUEST") end
  ok, key_error = exact_keys(request.editor, { "filetype", "cursor", "start_insert" })
  if not ok then return fail("INVALID_REQUEST", "editor " .. key_error) end
  if request.editor.filetype ~= "markdown" then return fail("INVALID_REQUEST", "only markdown is supported") end
  if request.editor.cursor ~= "end" and request.editor.cursor ~= "start" then
    return fail("INVALID_REQUEST", "invalid cursor")
  end
  if type(request.editor.start_insert) ~= "boolean" then return fail("INVALID_REQUEST", "invalid start_insert") end
  ok, key_error = exact_keys(request.context, { "source", "token", "relative_path" })
  if not ok then return fail("INVALID_REQUEST", "context " .. key_error) end
  if request.context.source ~= "window-ocr" then return fail("INVALID_REQUEST", "invalid context source") end
  if not M.is_nonce(request.context.token) then return fail("INVALID_REQUEST", "invalid context token") end
  if request.context.relative_path ~= "context.txt" then return fail("INVALID_REQUEST", "invalid context path") end
  ok, key_error = exact_keys(request.source_window, { "pid", "window_id", "bundle_id" })
  if not ok then return fail("INVALID_REQUEST", "source_window " .. key_error) end
  if not integer(request.source_window.pid) or request.source_window.pid <= 0 then return fail("INVALID_REQUEST", "invalid pid") end
  if not integer(request.source_window.window_id) or request.source_window.window_id <= 0 then
    return fail("INVALID_REQUEST", "invalid window id")
  end
  if type(request.source_window.bundle_id) ~= "string" or request.source_window.bundle_id == "" then
    return fail("INVALID_REQUEST", "invalid bundle id")
  end
  return request
end

function M.validate_decision(decision)
  local ok, key_error = exact_keys(decision, {
    "protocol_version",
    "session_id",
    "nonce",
    "outcome",
    "reason",
    "fallback_allowed",
    "writer",
    "server_uuid",
    "generation",
    "config_fingerprint",
    "decided_at_unix_ms",
  })
  if not ok then return fail("INVALID_DECISION", key_error) end
  if decision.protocol_version ~= M.VERSION or not M.is_session_id(decision.session_id) or not M.is_nonce(decision.nonce) then
    return fail("INVALID_DECISION")
  end
  if decision.outcome ~= "accepted" and decision.outcome ~= "rejected" then return fail("INVALID_DECISION") end
  if type(decision.fallback_allowed) ~= "boolean" or not integer(decision.decided_at_unix_ms) then
    return fail("INVALID_DECISION")
  end
  if decision.outcome == "accepted" then
    if not is_null(decision.reason) or decision.fallback_allowed or decision.writer ~= "server" then
      return fail("INVALID_DECISION", "invalid accepted decision")
    end
    if type(decision.server_uuid) ~= "string" or not integer(decision.generation) then return fail("INVALID_DECISION") end
  else
    if type(decision.reason) ~= "string" or decision.reason == "" then return fail("INVALID_DECISION") end
    if decision.fallback_allowed then return fail("INVALID_DECISION", "fallback is retired") end
    if decision.writer ~= "server" and not (decision.writer == "supervisor" and decision.reason == "DECISION_LOST") then
      return fail("INVALID_DECISION", "invalid rejection writer")
    end
  end
  return decision
end

function M.validate_result(result)
  local ok, key_error = exact_keys(result, {
    "protocol_version",
    "session_id",
    "nonce",
    "server_uuid",
    "generation",
    "status",
    "reason",
    "output_sha256",
    "published_at_unix_ms",
  })
  if not ok then return fail("INVALID_RESULT", key_error) end
  if result.protocol_version ~= M.VERSION or not M.is_session_id(result.session_id) or not M.is_nonce(result.nonce) then
    return fail("INVALID_RESULT")
  end
  if type(result.server_uuid) ~= "string" or not integer(result.generation) or not integer(result.published_at_unix_ms) then
    return fail("INVALID_RESULT")
  end
  if result.status ~= "committed" and result.status ~= "cancelled" and result.status ~= "failed" then
    return fail("INVALID_RESULT")
  end
  if result.status == "committed" then
    if not is_null(result.reason) or type(result.output_sha256) ~= "string" or #result.output_sha256 ~= 64 then
      return fail("INVALID_RESULT", "invalid committed result")
    end
  elseif not is_null(result.output_sha256) then
    return fail("INVALID_RESULT", "non-committed result has an output digest")
  end
  return result
end

function M.load_request(session_id, nonce, options)
  options = options or {}
  local directory, dir_err = M.session_dir(session_id, options.cache_root)
  if not directory then return nil, dir_err end
  local stat = uv.fs_lstat(directory)
  if not stat or stat.type ~= "directory" then return fail("INVALID_SESSION_PATH") end
  local uid = current_uid()
  if uid and stat.uid ~= uid then return fail("WRONG_OWNER") end
  local request_path = path_join(directory, "request.json")
  local valid_path, path_err = M.validate_session_path(session_id, request_path, options)
  if not valid_path then return nil, path_err end
  local request, read_err = M.read_json(request_path)
  if not request then return nil, read_err end
  local validated, validation_err = M.validate_request(request, options)
  if not validated then return nil, validation_err end
  if request.session_id ~= session_id then return fail("SESSION_MISMATCH") end
  if request.nonce ~= nonce then return fail("NONCE_MISMATCH") end
  local input_path = path_join(directory, "input.md")
  local input_valid, input_err = M.validate_session_path(session_id, input_path, options)
  if not input_valid then return nil, input_err end
  local _, file_err = secure_existing_file(input_path)
  if file_err then return nil, file_err end
  return request, directory
end

function M.make_decision(request, identity, outcome, reason)
  return {
    protocol_version = M.VERSION,
    session_id = request.session_id,
    nonce = request.nonce,
    outcome = outcome,
    reason = null_if_nil(reason),
    fallback_allowed = false,
    writer = "server",
    server_uuid = null_if_nil(identity and identity.server_uuid),
    generation = null_if_nil(identity and identity.generation),
    config_fingerprint = null_if_nil(identity and identity.config_fingerprint),
    decided_at_unix_ms = M.now_unix_ms(),
  }
end

function M.make_result(request, identity, status, reason, digest)
  return {
    protocol_version = M.VERSION,
    session_id = request.session_id,
    nonce = request.nonce,
    server_uuid = identity.server_uuid,
    generation = identity.generation,
    status = status,
    reason = null_if_nil(reason),
    output_sha256 = null_if_nil(digest),
    published_at_unix_ms = M.now_unix_ms(),
  }
end

function M.publish_decision(path, decision)
  local valid, err = M.validate_decision(decision)
  if not valid then return nil, err end
  return M.publish_json_no_replace(path, decision, decision_identity_keys)
end

function M.publish_result(path, result)
  local valid, err = M.validate_result(result)
  if not valid then return nil, err end
  return M.publish_json_no_replace(path, result, result_identity_keys)
end

function M.sha256(text)
  if vim.fn.exists("*sha256") ~= 1 then error("sha256() is required") end
  return vim.fn.sha256(text)
end

function M.random_token()
  local first = random_suffix()
  local second = random_suffix()
  return (first .. second):sub(1, 32)
end

return M
