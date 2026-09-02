local M = {
  adapters_ok = false,
  prewarmed = false,
}

local early_done = false
local late_done = false

local source_path = debug.getinfo(1, "S").source:gsub("^@", "")
local runtime_root = vim.fs.dirname(vim.fs.dirname(vim.fs.dirname(source_path)))

local function ensure_runtime_path()
  local lua_root = runtime_root .. "/lua"
  local first = lua_root .. "/?.lua"
  local second = lua_root .. "/?/init.lua"
  if not package.path:find(first, 1, true) then package.path = first .. ";" .. second .. ";" .. package.path end
  if not vim.tbl_contains(vim.opt.runtimepath:get(), runtime_root) then vim.opt.runtimepath:prepend(runtime_root) end
end

local function dedicated()
  return vim.g.edit_anywhere_server == 1 or vim.env.NVIM_EDIT_ANYWHERE_SERVER == "1"
end

local function load_home_env(name)
  if vim.env[name] and vim.env[name] ~= "" then return true end
  local path = vim.fn.expand "~/.env"
  if vim.fn.filereadable(path) ~= 1 then return false end
  local ok, lines = pcall(vim.fn.readfile, path)
  if not ok then return false end
  for _, line in ipairs(lines) do
    local key, value = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
    if key == name and value and value ~= "" then
      value = value:gsub("%s+#.*$", ""):gsub("^['\"]", ""):gsub("['\"]$", "")
      vim.env[name] = value
      return true
    end
  end
  return false
end

local function install_compatibility_module(name)
  package.loaded[name] = nil
  package.preload[name] = function() return require "edit_anywhere.context" end
end

local function configure_nonblocking_messages()
  vim.o.more = false
  if vim.fn.exists "+messagesopt" == 1 then
    vim.o.messagesopt = "history:500,progress:c,wait:0"
  end
end

function M.early()
  if early_done or not dedicated() then return false end
  early_done = true
  ensure_runtime_path()
  vim.g.edit_anywhere_server = 1
  vim.env.NVIM_EDIT_ANYWHERE_SERVER = "1"
  vim.env.DOTAGENT_EDITOR_PROMPT = "1"
  vim.env.DOTAGENT_AGENT = vim.env.DOTAGENT_AGENT or "codex"
  -- Keeper-as-an-argument prevents host configs from classifying this as an
  -- argument-less daily session. These additional flags cover common session
  -- plugins without changing the host repository.
  vim.g.resession_enabled = false
  vim.g.auto_session_enabled = false
  vim.g.edit_anywhere_disable_session_restore = true
  configure_nonblocking_messages()
  install_compatibility_module "user.external_context"
  install_compatibility_module "user.shell_context"
  vim.api.nvim_create_autocmd("VimEnter", {
    group = vim.api.nvim_create_augroup("EditAnywhereBootstrap", { clear = true }),
    once = true,
    callback = ensure_runtime_path,
    desc = "Keep the dotfiles Edit Anywhere runtime available after host startup",
  })
  return true
end

local function load_plugins()
  local ok, lazy = pcall(require, "lazy")
  if not ok or type(lazy.load) ~= "function" then return false end
  pcall(lazy.load, {
    plugins = {
      "plenary.nvim",
      "blink.cmp",
      "minuet-ai.nvim",
      "heirline.nvim",
      "nvim-treesitter",
    },
    wait = true,
  })
  return true
end

local function configure_minuet()
  local ok, minuet = pcall(require, "minuet")
  if not ok then return false end
  if not minuet.config then
    local setup_ok = pcall(minuet.setup, {})
    if not setup_ok then return false end
  end
  local context = require "edit_anywhere.context"
  local previous = minuet.config or {}
  local previous_deepseek = previous.provider_options and previous.provider_options.openai_fim_compatible or {}
  local deepseek = {
    api_key = "DEEPSEEK_API_KEY",
    name = "deepseek",
    end_point = "https://api.deepseek.com/beta/completions",
    model = "deepseek-v4-flash",
    stream = true,
    template = {
      prompt = context.fim_prompt,
      suffix = context.fim_suffix,
    },
    optional = {
      max_tokens = 128,
      temperature = 0.2,
    },
    transform = type(previous_deepseek.transform) == "table" and vim.deepcopy(previous_deepseek.transform) or {},
    get_text_fn = type(previous_deepseek.get_text_fn) == "table" and vim.deepcopy(previous_deepseek.get_text_fn) or {},
  }
  previous.provider = "openai_fim_compatible"
  previous.provider_options = { openai_fim_compatible = deepseek }
  previous.request_timeout = previous.request_timeout or 3
  previous.throttle = previous.throttle or 300
  previous.debounce = previous.debounce or 150
  previous.context_window = previous.context_window or 6000
  previous.n_completions = 1
  minuet.config = previous
  vim.g.zish_minuet_provider = "deepseek"
  vim.env.MINUET_PROVIDER = "deepseek"
  local key_available = load_home_env "DEEPSEEK_API_KEY"
  if previous.virtualtext then
    previous.virtualtext.auto_trigger_ft = key_available and { "markdown" } or {}
  end
  local virtualtext_ok = pcall(function() require("minuet.virtualtext").setup() end)
  local probe_ok, probe = pcall(context.fim_prompt, "edit-anywhere-adapter-probe", "", {})
  return minuet.config.provider == "openai_fim_compatible"
    and minuet.config.provider_options.openai_fim_compatible.template.prompt == context.fim_prompt
    and minuet.config.provider_options.openai_fim_compatible.template.suffix == context.fim_suffix
    and type(minuet.config.provider_options.openai_fim_compatible.get_text_fn) == "table"
    and virtualtext_ok
    and probe_ok
    and type(probe) == "string"
    and probe:find("edit-anywhere-adapter-probe", 1, true) ~= nil
end

local function configure_context_completion_refresh()
  local group = vim.api.nvim_create_augroup("EditAnywhereContextCompletion", { clear = true })
  vim.api.nvim_create_autocmd("User", {
    group = group,
    pattern = "ZishExternalContextChanged",
    desc = "Refresh inline completion when asynchronous OCR becomes ready",
    callback = function(event)
      local data = event.data or {}
      local bufnr = tonumber(data.bufnr)
      if data.state ~= "READY" or not bufnr or bufnr ~= vim.api.nvim_get_current_buf() then return end
      vim.schedule(function()
        if not vim.api.nvim_buf_is_valid(bufnr) or bufnr ~= vim.api.nvim_get_current_buf() then return end
        if not vim.fn.mode():match "^[iR]" then return end
        local ok, virtualtext = pcall(require, "minuet.virtualtext")
        if not ok then return end
        virtualtext.action.dismiss()
        virtualtext.action.next()
      end)
    end,
  })
  return true
end

local function prewarm_markdown()
  local current_window = vim.api.nvim_get_current_win()
  local current_buffer = vim.api.nvim_win_get_buf(current_window)
  local buffer = vim.api.nvim_create_buf(false, true)
  vim.b[buffer].edit_anywhere_prewarm = true
  vim.bo[buffer].buftype = "acwrite"
  vim.bo[buffer].bufhidden = "wipe"
  vim.bo[buffer].swapfile = false
  vim.bo[buffer].undofile = false
  vim.api.nvim_buf_set_lines(buffer, 0, -1, false, { "Edit Anywhere prewarm" })
  vim.api.nvim_win_set_buf(current_window, buffer)
  vim.bo[buffer].filetype = "markdown"
  -- nvim_win_set_buf emitted BufEnter and the filetype assignment emitted one
  -- FileType. Let callbacks scheduled by those events drain without emitting
  -- either event a second time.
  vim.wait(20, function() return false end, 1)
  vim.bo[buffer].modified = false
  vim.api.nvim_win_set_buf(current_window, current_buffer)
  pcall(vim.api.nvim_buf_delete, buffer, { force = true })
  return true
end

function M.late()
  if late_done then return M.adapters_ok, M.prewarmed end
  if not dedicated() then return false, false end
  late_done = true
  load_plugins()
  configure_nonblocking_messages()
  local minuet_ok = configure_minuet()
  local context_refresh_ok = pcall(configure_context_completion_refresh)
  local status_ok = pcall(function() require("edit_anywhere.status").setup() end)
  local prewarm_ok = pcall(prewarm_markdown)
  M.adapters_ok = minuet_ok and context_refresh_ok and status_ok
  M.prewarmed = prewarm_ok
  return M.adapters_ok, M.prewarmed
end

function M.health()
  return {
    adapters_ok = M.adapters_ok,
    prewarmed = M.prewarmed,
  }
end

return M
