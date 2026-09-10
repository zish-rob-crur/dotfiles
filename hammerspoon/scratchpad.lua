-- ⌘⇧M: a Neovide window on today's note, ~/scratchpad/YYYY-MM-DD.md; pressing
-- it again focuses the window that is already open (a new day gets a new file
-- once that window is closed). Raycast/shell: open "hammerspoon://scratchpad".
local neovide = require("neovide_window")
local M = {}

local HOTKEY = { { "cmd", "shift" }, "m" }
local DIR = assert(os.getenv("HOME"), "HOME is required") .. "/scratchpad"
local state = { hotkey = nil }

function M.open()
  hs.fs.mkdir(DIR)
  local file = DIR .. "/" .. os.date("%Y-%m-%d") .. ".md"
  if not neovide.open("scratch", { file }, nil, { w = 900, h = 640 }) then
    hs.alert.show("Neovide not found: brew install --cask neovide")
  end
end

function M.start()
  state.hotkey = hs.hotkey.bind(HOTKEY[1], HOTKEY[2], M.open)
  hs.urlevent.bind("scratchpad", M.open)
  return M
end

function M.stop()
  if state.hotkey then state.hotkey:delete() end
  hs.urlevent.bind("scratchpad", nil)
  state.hotkey = nil
end

return M
