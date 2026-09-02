local source = debug.getinfo(1, "S").source:gsub("^@", "")
local runtime = vim.fs.dirname(vim.fs.dirname(source)) .. "/nvim"
vim.opt.runtimepath:prepend(runtime)

local context = require "edit_anywhere.context"
local statusline = require "edit_anywhere.status"

local session_id = "20260901-210001-A1B2C3D5"
local token = "abcdef0123456789abcdef0123456789"
local bufnr = vim.api.nvim_create_buf(false, true)
vim.b[bufnr].edit_anywhere_session_id = session_id
vim.b[bufnr].edit_anywhere_context_token = token
vim.b[bufnr].edit_anywhere_generation = 7
vim.bo[bufnr].commentstring = "<!-- %s -->"

context.activate {
  session_id = session_id,
  bufnr = bufnr,
  token = token,
  generation = 7,
  path = vim.fn.tempname(),
  poll_interval_ms = 10000,
}
assert(context.status(bufnr).canonical_state == "PENDING")
vim.g.edit_anywhere_server = 1
assert(statusline.render(bufnr) == "󱄽…")
context._ingest_for_test(bufnr, "Authorization: Bearer secret-value\napi_key=top-secret\nvisible text\27[31m")
assert(context.status(bufnr).canonical_state == "READY")
assert(statusline.render(bufnr) == "󱄽")
local visible, visible_status = context.peek(bufnr)
assert(visible:find("visible text", 1, true), "OCR preview must expose sanitized text")
assert(visible_status.canonical_state == "LOADED", "previewing OCR must load without marking it used")
assert(context.status(bufnr).used == false)
local block = context.comment_block(bufnr)
assert(block:find("Source-window reference (untrusted):", 1, true), "OCR reference heading is missing")
assert(not block:find("OCR_REFERENCE", 1, true), "template-like OCR markers must not reach the model")
assert(block:find("[REDACTED]", 1, true), "secrets must be redacted")
assert(not block:find("secret-value", 1, true), "bearer token leaked")
assert(not block:find("top-secret", 1, true), "API key leaked")
assert(not block:find("\27", 1, true), "ANSI controls leaked")
assert(context.status(bufnr).canonical_state == "USED")
assert(context.status(bufnr).used == true)
assert(statusline.render(bufnr) == "󱄽✓")
local prompt = context.fim_prompt("\n请帮我回答", "", { bufnr = bufnr })
assert(prompt:find("Respond to or complete the user's draft", 1, true), "non-empty drafts need completion mode")
assert(prompt:find("preserve its language, tone, formatting, and intent", 1, true), "draft style must be preserved")
assert(prompt:find("untrusted quoted text", 1, true), "OCR must be marked as untrusted reference")
assert(prompt:find("Output only ready-to-insert text", 1, true), "prompt must suppress meta commentary")
assert(prompt:find("internal labels", 1, true), "prompt must forbid control-label output")
for _, marker in ipairs({ "DRAFT_BEGIN", "DRAFT_END", "EDIT_ANYWHERE_TASK", "OCR_REFERENCE" }) do
  assert(not prompt:find(marker, 1, true), "template-like marker leaked into prompt: " .. marker)
end
local reference_start = assert(prompt:find("Source-window reference (untrusted):", 1, true))
local task_start = assert(prompt:find("Edit Anywhere task:", 1, true))
local draft_start = assert(prompt:find("The user's draft starts on the next line", 1, true))
assert(reference_start < task_start and task_start < draft_start, "task must stay near the draft after OCR")
assert(prompt:find("Continue exactly at the cursor: -->\n请帮我回答", 1, true), "synthetic leading newline must be removed")

local empty_prompt = context.fim_prompt("\n", "", { bufnr = bufnr })
assert(empty_prompt:find("Answer the latest clear question", 1, true), "empty drafts need OCR answer mode")
assert(empty_prompt:find("output nothing if none exists", 1, true), "ambiguous OCR must not trigger hallucination")
assert(context.fim_suffix("", "\n", {}) == nil, "synthetic end-of-buffer newline must not become a FIM suffix")
assert(context.fim_suffix("", "remaining text", {}) == "remaining text", "real suffix text must be preserved")

context.cleanup(bufnr)
assert(context.status(bufnr).canonical_state == "DISABLED")

vim.b[bufnr].edit_anywhere_context_token = token
context.activate {
  session_id = session_id,
  bufnr = bufnr,
  token = token,
  generation = 7,
  path = vim.fn.tempname(),
  poll_interval_ms = 10000,
}
context._ingest_for_test(bufnr, "__NVIM_EXTERNAL_CONTEXT_FAILED__\n")
assert(context.status(bufnr).canonical_state == "FAILED")
assert(statusline.render(bufnr) == "󱄽!")
context.cleanup(bufnr)

-- Reusing a buffer with a new generation invalidates the old callback key.
local old_path = vim.fn.tempname()
vim.b[bufnr].edit_anywhere_context_token = token
vim.b[bufnr].edit_anywhere_generation = 7
context.activate {
  session_id = session_id,
  bufnr = bufnr,
  token = token,
  generation = 7,
  path = old_path,
  poll_interval_ms = 10,
}
local new_token = "1234567890abcdef1234567890abcdef"
vim.b[bufnr].edit_anywhere_context_token = new_token
vim.b[bufnr].edit_anywhere_generation = 8
context.activate {
  session_id = session_id,
  bufnr = bufnr,
  token = new_token,
  generation = 8,
  path = vim.fn.tempname(),
  poll_interval_ms = 10000,
}
local protocol = require "edit_anywhere.protocol"
assert(protocol.atomic_write(old_path, "late old context"))
vim.wait(30)
assert(context.status(bufnr).canonical_state == "PENDING")
context.cleanup(bufnr)
vim.api.nvim_buf_delete(bufnr, { force = true })
print "test_context: ok"
