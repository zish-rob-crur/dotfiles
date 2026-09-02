local M = {}

local installed = false
local original_statusline

function M.render(bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  if vim.g.edit_anywhere_server ~= 1 or not vim.api.nvim_buf_is_valid(bufnr)
    or type(vim.b[bufnr].edit_anywhere_session_id) ~= "string"
  then return "" end
  local state = require("edit_anywhere.context").status(bufnr)
  local canonical = state.canonical_state
  if canonical == "SCHEDULED" or canonical == "PENDING" then return "󱄽…" end
  if canonical == "READY" or canonical == "LOADED" then return "󱄽" end
  if canonical == "USED" then return "󱄽✓" end
  if canonical == "EMPTY" or canonical == "FAILED" then return "󱄽!" end
  return ""
end

local function evaluate_original()
  local ok, heirline = pcall(require, "heirline")
  if ok and type(heirline.eval_statusline) == "function" then
    local evaluated_ok, value = pcall(heirline.eval_statusline)
    if evaluated_ok and type(value) == "string" then return value end
  end
  if original_statusline and not vim.startswith(original_statusline, "%!") then return original_statusline end
  return ""
end

function M.evaluate()
  local upstream = evaluate_original()
  local segment = M.render()
  if segment == "" then return upstream end
  if upstream == "" then return "%=" .. segment .. " " end
  return upstream .. "%=" .. segment .. " "
end

function M.setup()
  if installed or vim.g.edit_anywhere_server ~= 1 then return end
  installed = true
  original_statusline = vim.o.statusline
  _G.edit_anywhere_statusline_eval = M.evaluate
  vim.o.statusline = "%!v:lua.edit_anywhere_statusline_eval()"
  pcall(vim.api.nvim_del_user_command, "EditAnywhereStatus")
  vim.api.nvim_create_user_command("EditAnywhereStatus", function()
    local bufnr = vim.api.nvim_get_current_buf()
    local status = require("edit_anywhere.context").status(bufnr)
    vim.notify(
      ("Edit Anywhere OCR: state=%s session=%s used=%s"):format(
        status.canonical_state,
        status.session_id or vim.b[bufnr].edit_anywhere_session_id or "none",
        tostring(status.used == true)
      ),
      vim.log.levels.INFO
    )
  end, { desc = "Show Edit Anywhere OCR context status" })
end

return M
