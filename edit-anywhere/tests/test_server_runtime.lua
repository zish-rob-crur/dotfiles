local source = debug.getinfo(1, "S").source:gsub("^@", "")
local runtime_path = vim.fs.dirname(vim.fs.dirname(source)) .. "/nvim"
vim.opt.runtimepath:prepend(runtime_path)

local root = vim.fn.tempname()
vim.env.EDIT_ANYWHERE_CACHE_ROOT = root
vim.g.edit_anywhere_server = 1

local protocol = require "edit_anywhere.protocol"
local bootstrap = require "edit_anywhere.bootstrap"
bootstrap.early()
local startup_tree_autocmd = vim.api.nvim_create_autocmd("VimEnter", {
  desc = "Open Neo-tree on the right at startup",
  callback = function() vim.g.unexpected_edit_anywhere_tree = true end,
})
local server = require "edit_anywhere.server"
server.start()
assert(vim.wait(500, function() return server.health().state == "IDLE" end), "server did not become idle")
for _, autocmd in ipairs(vim.api.nvim_get_autocmds { event = "VimEnter" }) do
  assert(autocmd.id ~= startup_tree_autocmd, "startup file tree autocmd remained installed")
end
assert(server.identity().name == "edit-anywhere")
assert(server.health().layout_ok)
assert(vim.o.more == false, "dedicated server must not open a more prompt")
if vim.fn.exists "+messagesopt" == 1 then
  assert(not vim.o.messagesopt:find("hit%-enter"), "dedicated server must disable hit-enter prompts")
  assert(vim.o.messagesopt:find("wait:0", 1, true), "dedicated server messages must never block for input")
end

local tree_bufnr = vim.api.nvim_create_buf(false, true)
vim.bo[tree_bufnr].filetype = "neo-tree"
vim.cmd "vsplit"
vim.api.nvim_win_set_buf(vim.api.nvim_get_current_win(), tree_bufnr)
assert(vim.wait(500, function() return server.health().layout_ok end), "file tree was not suppressed")
for _, win in ipairs(vim.api.nvim_list_wins()) do
  assert(vim.bo[vim.api.nvim_win_get_buf(win)].filetype ~= "neo-tree", "neo-tree remained visible")
end

local session_id = "20260901-210003-A1B2C3D7"
local nonce = "0123456789abcdef0123456789abcdef"
local token = "abcdef0123456789abcdef0123456789"
local directory = assert(protocol.session_dir(session_id, root))
assert(protocol.ensure_directory(directory, 448))
local now = protocol.now_unix_ms()
local request = {
  protocol_version = 1,
  session_id = session_id,
  nonce = nonce,
  created_at_unix_ms = now,
  expires_at_unix_ms = now + 120000,
  editor = { filetype = "markdown", cursor = "end", start_insert = true },
  context = { source = "window-ocr", token = token, relative_path = "context.txt" },
  source_window = { pid = 123, window_id = 456, bundle_id = "com.example.test" },
}
assert(protocol.atomic_write(directory .. "/input.md", "server body\n"))
assert(protocol.atomic_write_json(directory .. "/request.json", request))

local opened = server.open(session_id, nonce)
assert(opened.accepted and opened.state == "waiting_ui")
local replay = server.open(session_id, nonce)
assert(replay.accepted and replay.state == "waiting_ui", "same nonce replay must be idempotent")
local mismatch = server.open(session_id, "ffffffffffffffffffffffffffffffff")
assert(not mismatch.accepted and mismatch.reason == "NONCE_MISMATCH")

assert(vim.wait(2500, function() return server.health().state == "IDLE" end, 10), "UI attach timeout did not terminate")
local result = assert(protocol.read_json(directory .. "/result.json"))
assert(result.status == "failed" and result.reason == "UI_ATTACH_TIMEOUT")
assert(vim.fn.filereadable(directory .. "/output.md") == 0)
local ended_nonce_mismatch = server.open(session_id, "ffffffffffffffffffffffffffffffff")
assert(not ended_nonce_mismatch.accepted and ended_nonce_mismatch.reason == "NONCE_MISMATCH")

local rpc = vim.json.decode(server.rpc_json("identity", "{}"))
assert(rpc.ok and rpc.result.name == "edit-anywhere")
server._reset_for_test()
vim.fn.delete(root, "rf")
print "test_server_runtime: ok"
