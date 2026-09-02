local source = debug.getinfo(1, "S").source:gsub("^@", "")
local runtime = vim.fs.dirname(vim.fs.dirname(source)) .. "/nvim"
vim.opt.runtimepath:prepend(runtime)

local protocol = require "edit_anywhere.protocol"
local session_runtime = require "edit_anywhere.session"

assert(session_runtime.can_transition("PREPARING", "WAITING_UI"))
assert(not session_runtime.can_transition("WAITING_UI", "COMMITTED"))
assert(session_runtime.insert_command("end", true) == "startinsert!", "end cursor must append after the final character")
assert(session_runtime.insert_command("start", true) == "startinsert")
assert(session_runtime.insert_command("end", false) == nil)

local root = vim.fn.tempname()
local session_id = "20260901-210002-A1B2C3D6"
local nonce = "0123456789abcdef0123456789abcdef"
local token = "abcdef0123456789abcdef0123456789"
local directory = assert(protocol.session_dir(session_id, root))
assert(protocol.ensure_directory(directory, 448))
assert(protocol.atomic_write(directory .. "/input.md", "original body\n"))

local request = {
  protocol_version = 1,
  session_id = session_id,
  nonce = nonce,
  created_at_unix_ms = 1000,
  expires_at_unix_ms = 3000,
  editor = { filetype = "markdown", cursor = "end", start_insert = false },
  context = { source = "window-ocr", token = token, relative_path = "context.txt" },
  source_window = { pid = 123, window_id = 456, bundle_id = "com.example.test" },
}

local keeper = vim.api.nvim_get_current_buf()
vim.bo[keeper].buftype = "nofile"
vim.bo[keeper].bufhidden = "hide"
local session = assert(session_runtime.new {
  request = request,
  identity = { server_uuid = "server-test", generation = 9, config_fingerprint = "test" },
  directory = directory,
  cache_root = root,
  keeper_bufnr = keeper,
  window = vim.api.nvim_get_current_win(),
})

assert(vim.bo[session.bufnr].buftype == "acwrite")
assert(session:serialize() == "original body\n", "trailing newline semantics changed")
local context_mapping = vim.iter(vim.api.nvim_buf_get_keymap(session.bufnr, "n")):find(function(mapping)
  return mapping.lhs == "\\oc"
end)
assert(context_mapping and context_mapping.desc == "Show Edit Anywhere OCR context")
local write_ok = pcall(vim.api.nvim_buf_call, session.bufnr, function() vim.cmd "write" end)
assert(write_ok, "ordinary :write must be ignored without entering a hit-enter error prompt")
local unchanged = assert(protocol.read_file(directory .. "/input.md"))
assert(unchanged == "original body\n")
assert(vim.fn.filereadable(directory .. "/output.md") == 0)

-- A matching UI disappearing without a detach intent suspends, never commits.
assert(session:attach(71))
vim.wait(30)
assert(session:on_ui_leave(71) == false)
assert(session.state == "SUSPENDED")
assert(vim.fn.filereadable(directory .. "/result.json") == 0)
assert(session:resume())

assert(session:cleanup(false))
assert(session:cleanup(false), "cleanup must be idempotent")
vim.fn.delete(root, "rf")
print "test_session: ok"
