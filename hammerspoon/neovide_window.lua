-- Open files in a Neovide window that Hammerspoon launches and therefore can
-- find again (by pid: Neovide's title is not distinctive), place and focus.
-- One window per key: task_board uses "todos", scratchpad uses "scratch".
local M = {}

local HOME = assert(os.getenv("HOME"), "HOME is required")
local NEOVIDE = "/opt/homebrew/bin/neovide"
local tasks = {}

function M.available()
  return hs.fs.attributes(NEOVIDE, "mode") == "file"
end

function M.find(key)
  local task = tasks[key]
  local pid = task and task:isRunning() and task:pid()
  if not pid then return nil end
  for _, window in ipairs(hs.window.allWindows()) do
    local app = window:application()
    if app and app:pid() == pid then return window end
  end
  return nil
end

-- Centre the window on the screen under the mouse once Neovide has created it.
local function place(key, size, attempt)
  local window = M.find(key)
  if not window then
    if (attempt or 0) < 80 then hs.timer.doAfter(0.05, function() place(key, size, (attempt or 0) + 1) end) end
    return
  end
  local f = (hs.mouse.getCurrentScreen() or hs.screen.mainScreen()):frame()
  local w, h = math.min(size.w, math.floor(f.w * 0.55)), math.min(size.h, math.floor(f.h * 0.6))
  window:setFrame({ x = f.x + (f.w - w) / 2, y = f.y + (f.h - h) / 2, w = w, h = h }, 0)
  window:focus()
end

-- Focus the window already open for `key`, else launch Neovide on `files`
-- (`args` are extra nvim arguments, e.g. { "-O" }) with the first file's
-- directory as working directory (hs.task would otherwise inherit
-- Hammerspoon's). Returns false when Neovide could not be started.
function M.open(key, files, args, size)
  local existing = M.find(key)
  if existing then existing:focus(); return true end
  if not M.available() then return false end
  local argv = { "--" }
  for _, a in ipairs(args or {}) do argv[#argv + 1] = a end
  for _, f in ipairs(files) do argv[#argv + 1] = f end
  local task = hs.task.new(NEOVIDE, nil, argv)
  task:setEnvironment({ HOME = HOME, PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" })
  local dir = files[1] and files[1]:match("^(.*)/[^/]+$")
  if dir then task:setWorkingDirectory(dir) end
  if not task:start() then return false end
  tasks[key] = task
  place(key, size or { w = 1100, h = 760 })
  return true
end

return M
