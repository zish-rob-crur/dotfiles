require("hs.ipc")

local edit_anywhere = require("edit_anywhere")
edit_anywhere.start()
require("task_board").start()
require("scratchpad").start()

hs.autoLaunch(true)

return edit_anywhere
