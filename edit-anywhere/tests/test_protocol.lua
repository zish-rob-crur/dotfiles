local source = debug.getinfo(1, "S").source:gsub("^@", "")
local runtime = vim.fs.dirname(vim.fs.dirname(source)) .. "/nvim"
vim.opt.runtimepath:prepend(runtime)

local protocol = require "edit_anywhere.protocol"

local function equal(actual, expected, message)
  assert(vim.deep_equal(actual, expected), (message or "values differ") .. "\nactual: " .. vim.inspect(actual))
end

local root = vim.fn.tempname()
assert(protocol.ensure_directory(root .. "/sessions", 448))
local session_id = "20260901-210000-A1B2C3D4"
local nonce = "0123456789abcdef0123456789abcdef"
local token = "abcdef0123456789abcdef0123456789"
local directory = assert(protocol.session_dir(session_id, root))
assert(protocol.ensure_directory(directory, 448))

local request = {
  protocol_version = 1,
  session_id = session_id,
  nonce = nonce,
  created_at_unix_ms = 1000,
  expires_at_unix_ms = 3000,
  editor = { filetype = "markdown", cursor = "end", start_insert = true },
  context = { source = "window-ocr", token = token, relative_path = "context.txt" },
  source_window = { pid = 123, window_id = 456, bundle_id = "com.example.test" },
}
assert(protocol.validate_request(request, { now_unix_ms = 2000 }))

local extra = vim.deepcopy(request)
extra.shell_command = "never"
local _, extra_error = protocol.validate_request(extra, { now_unix_ms = 2000 })
equal(extra_error.code, "INVALID_REQUEST", "unknown request fields must fail")

local stale = vim.deepcopy(request)
local _, stale_error = protocol.validate_request(stale, { now_unix_ms = 3001 })
equal(stale_error.code, "STALE_REQUEST")

local escaped, escaped_error = protocol.validate_session_path(session_id, directory .. "/../outside", { cache_root = root })
assert(not escaped and escaped_error.code == "PATH_ESCAPE", "parent traversal must fail")

assert(protocol.atomic_write(root .. "/outside.txt", "outside"))
local linked_path = directory .. "/linked.txt"
assert((vim.uv or vim.loop).fs_symlink(root .. "/outside.txt", linked_path))
local linked, linked_error = protocol.validate_session_path(session_id, linked_path, { cache_root = root })
assert(not linked and linked_error.code == "SYMLINK_FORBIDDEN", "symlinked session files must fail")
local _, linked_read_error = protocol.read_file(linked_path)
assert(linked_read_error.code == "INVALID_FILE_TYPE", "read_file must not follow symlinks")

local symlink_root = vim.fn.tempname()
local symlink_target = vim.fn.tempname()
assert(protocol.ensure_directory(symlink_root, 448))
assert(protocol.ensure_directory(symlink_target, 448))
assert((vim.uv or vim.loop).fs_symlink(symlink_target, symlink_root .. "/sessions"))
local _, symlink_directory_error = protocol.ensure_directory(symlink_root .. "/sessions/escaped", 448)
assert(symlink_directory_error.code == "NOT_DIRECTORY", "directory creation must not traverse a symlink")

local decision = protocol.make_decision(request, {
  server_uuid = "server-test",
  generation = 4,
  config_fingerprint = "fingerprint",
}, "accepted")
assert(protocol.validate_decision(decision))
local decision_path = directory .. "/decision.json"
local ok, mode = protocol.publish_decision(decision_path, decision)
assert(ok and mode == "published")
ok, mode = protocol.publish_decision(decision_path, vim.deepcopy(decision))
assert(ok and mode == "idempotent", "identical decision must be idempotent")
local conflict = vim.deepcopy(decision)
conflict.config_fingerprint = "different"
local _, conflict_error = protocol.publish_decision(decision_path, conflict)
equal(conflict_error.code, "TERMINAL_CONFLICT")

local bad_fallback = vim.deepcopy(decision)
bad_fallback.outcome = "rejected"
bad_fallback.reason = "SERVER_UNAVAILABLE"
bad_fallback.fallback_allowed = true
bad_fallback.writer = "supervisor"
bad_fallback.server_uuid = vim.NIL
bad_fallback.generation = vim.NIL
local _, fallback_error = protocol.validate_decision(bad_fallback)
equal(fallback_error.code, "INVALID_DECISION")

local body = "hello\n"
local result = protocol.make_result(request, {
  server_uuid = "server-test",
  generation = 4,
}, "committed", nil, protocol.sha256(body))
assert(protocol.validate_result(result))
local result_path = directory .. "/result.json"
local result_ok, result_mode = protocol.publish_result(result_path, result)
assert(result_ok and result_mode == "published")
result_ok, result_mode = protocol.publish_result(result_path, vim.deepcopy(result))
assert(result_ok and result_mode == "idempotent", "identical result must be at-most-once idempotent")
local conflicting_result = protocol.make_result(request, {
  server_uuid = "server-test",
  generation = 4,
}, "cancelled", nil, nil)
local _, result_conflict_error = protocol.publish_result(result_path, conflicting_result)
assert(result_conflict_error.code == "TERMINAL_CONFLICT", "a second logical result must conflict")

assert(protocol.atomic_write(directory .. "/input.md", body))
assert(protocol.atomic_write_json(directory .. "/request.json", request))
local loaded, loaded_directory = protocol.load_request(session_id, nonce, { cache_root = root, now_unix_ms = 2000 })
equal(loaded.session_id, session_id)
equal(loaded_directory, directory)

vim.fn.delete(root, "rf")
vim.fn.delete(symlink_root, "rf")
vim.fn.delete(symlink_target, "rf")
print "test_protocol: ok"
