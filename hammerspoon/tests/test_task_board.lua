-- Exercise the real supervisor with fake process/OS APIs (no live todo edits).
local source = assert(arg[1], "task_board.lua path required")
local encoded, sequence = {}, 0
local function encode(value)
  sequence = sequence + 1
  local key = "json-" .. sequence
  encoded[key] = value
  return key
end
local function decode(key) return assert(encoded[key], "invalid json") end
local settings, tasks, calls, hotkeys, urls = {}, {}, {}, {}, {}
local originalShutdown = function() calls.shutdown = (calls.shutdown or 0) + 1 end
local snapshot = { windows = {}, todos = {} }
local open = io.open
io.open = function(path, mode)
  if path:match("/tmux%-task%-board/board.json$") then
    return { read = function() return encode(snapshot) end, close = function() end }
  end
  return open(path, mode)
end
package.preload.neovide_window = function()
  return { find = function() return false end,
    open = function(_, args) calls.edit = args; return true end }
end
local screen = { name = function() return "test" end,
  frame = function() return { x = 0, y = 0, w = 1400, h = 900 } end,
  fullFrame = function() return {} end }
hs = {
  shutdownCallback = originalShutdown,
  json = { encode = encode, decode = decode },
  settings = { get = function(k) return settings[k] end, set = function(k, v) settings[k] = v end },
  fs = { attributes = function() return {} end },
  screen = { allScreens = function() return { screen } end, primaryScreen = function() return screen end },
  geometry = { point = function() return { inside = function() return true end } end },
  printf = function() end,
  timer = {
    doEvery = function(_, fn) calls.pollFocus = fn; return { stop = function() end } end,
    doAfter = function(_, fn)
      local timer = { callback = fn, stopped = false }
      function timer:stop() self.stopped = true end
      calls.finishTimer = timer
      return timer
    end,
  },
  execute = function(cmd) calls.execute = cmd; return "/tmp/todos.md\n" end,
  application = { launchOrFocus = function(app) calls.app = app end },
  hotkey = { bind = function(_, key, fn)
    hotkeys[key] = fn
    return { delete = function() hotkeys[key] = nil end }
  end },
  urlevent = { bind = function(name, fn) urls[name] = fn end, openURL = function(url) calls.url = url end },
  pathwatcher = { new = function(_, fn)
    calls.changed = fn
    return { start = function(self) return self end, stop = function() end }
  end },
  task = { new = function(path, complete, stream, args)
    local t = { path = path, callback = complete, streamCallback = type(stream) == "function" and stream or nil, args = args or stream, writes = {} }
    -- The test dispatchers stay callable after the native callbacks are detached.
    function t.complete(code, out, err)
      t.running = false
      if t.callback then t.callback(code, out, err) end
    end
    function t.stream(...) if t.streamCallback then return t.streamCallback(...) end; return false end
    function t:setCallback(fn) self.callback = fn; return self end
    function t:setStreamingCallback(fn) self.streamCallback = fn; return self end
    function t:start()
      if calls.failNextStart then calls.failNextStart = nil; return nil end
      self.running = true; return self
    end
    function t:isRunning() return self.running end
    function t:setInput(line) self.writes[#self.writes + 1] = decode(line:sub(1, -2)); return self end
    function t:setEnvironment() return self end
    function t:closeInput() self.closed = true; return self end
    tasks[#tasks + 1] = t
    return t
  end },
}
local board = assert(loadfile(source))()
board.start()
assert(hs.shutdownCallback ~= originalShutdown)
local task = tasks[1]
local function event(value, split)
  local line = encode(value) .. "\n"
  if split then
    task.stream(task, line:sub(1, 4), "")
    task.stream(task, line:sub(5), "")
  else task.stream(task, line, "") end
end
local function message(body) event({ event = "message", body = body }, true) end
assert(task.writes[1].command == "load")
board.toggle(); board.toggle(); board.inspect()
assert(#task.writes == 1, "must not overwrite pending stdin")
for _, command in ipairs({ "toggle", "toggle", "inspect" }) do
  event({ event = "ack" })
  assert(task.writes[#task.writes].command == command, "commands must remain ordered")
end
event({ event = "ack" })
message({ ready = true })
assert(task.writes[#task.writes].command == "update")
event({ event = "ack" })
event({ event = "rendered" })
assert(board.status().rendered)
message({ todo = { vault = "中文", line = 3.0 } })
assert(table.concat(tasks[#tasks].args, ":") == "toggle:中文:3")
message({ dismiss = { vault = "v", line = 4 } })
assert(table.concat(tasks[#tasks].args, ":") == "dismiss:v:4")
message({ snooze = { vault = "v", line = 5 } })
assert(table.concat(tasks[#tasks].args, ":") == "snooze:v:5")
message({ gap = 42 }); assert(settings.taskBoardWeekGap == 42)
message({ gap = false }); assert(settings.taskBoardWeekGap == nil)
message({ edit = true }); assert(calls.edit[1] == "/tmp/todos.md")
message({ url = "https://example.com" }); assert(calls.url == "https://example.com")
event({ event = "ack" })
local focusTask = { id = "focus-1", text = "完成设计 & 评审? #1", done = false, pinned = true, vault = "work", file = "/tmp/Weekly.md", sessions = {}, minutes = 0, pomodoros = 0 }
local focusData = { tasks = { focusTask }, week = "2026-W44", vaults = { { name = "work" } }, errors = {} }
local streamed = encode({tasks = {}, vaults = {}, errors = {}})
tasks[2].stream(tasks[2], streamed:sub(1, 3), "")
tasks[2].complete(0, streamed:sub(4), "")
event({ event = "ack" })
message({ focus = { action = "create", text = focusTask.text, vault = "work", section = "CALLE 中文", pin = true, request_id = "r1" } })
local createJob = tasks[#tasks]
assert(createJob.args[2] == "create" and createJob.args[#createJob.args] == "--pin")
assert(createJob.args[9] == "--section" and createJob.args[10] == "CALLE 中文", "creation forwards the selected group as one literal argument")
focusData.result = focusTask
createJob.complete(0, encode(focusData), "")
assert(not createJob.callback and not createJob.streamCallback, "finished jobs must release both registry callbacks")
assert(calls.url == "https://example.com", "creating must not start a timer")
event({ event = "ack" })
assert(task.writes[#task.writes].board.todos[1].id == "focus-1", "weekly view uses the same canonical task")
message({ focus = { action = "start", id = "focus-1", minutes = 50, request_id = "r2" } })
local startJob = tasks[#tasks]
assert(startJob.args[2] == "start")
assert(calls.url == "https://example.com", "record request before opening Raycast")
focusData.result = { url = "raycast://focus/start?goal=encoded&duration=3000" }
startJob.complete(0, encode(focusData), "")
assert(calls.url == "raycast://focus/start?goal=encoded&duration=3000")
assert(settings.taskBoardFocus.minutes == 50)
event({ event = "ack" })
local saved = settings.taskBoardFocus
message({ focus = { action = "start", id = "focus-1", minutes = -1, request_id = "invalid" } })
assert(settings.taskBoardFocus == saved)
message({ focus = { action = "open", id = "focus-1", request_id = "r3" } })
focusData.result = focusTask
tasks[#tasks].complete(0, encode(focusData), "")
assert(calls.edit[1] == "/tmp/Weekly.md")
event({ event = "ack" })
message({ focus = { action = "pin", id = "focus-1", request_id = "r4" } })
assert(tasks[#tasks].args[2] == "pin")
tasks[#tasks].complete(1, encode({error = "Editor conflict"}), "")
event({ event = "ack" })
assert(task.writes[#task.writes].board.focus_ack.error == "Editor conflict")
event({ event = "ack" })
message({ focus = { action = "comment", id = "focus-1", text = "第一行\n第二行", request_id = "comment-1" } })
local commentJob = tasks[#tasks]
assert(commentJob.args[2] == "comment" and commentJob.args[6] == "第一行\n第二行")
commentJob.complete(0, encode(focusData), "")
event({ event = "ack" })
assert(task.writes[#task.writes].board.focus_ack.request_id == "comment-1")
assert(calls.url == "raycast://focus/start?goal=encoded&duration=3000", "comments never affect the timer")
event({ event = "ack" })
message({ focus = { action = "move", id = "focus-1", section = "DM 中文", request_id = "move-1" } })
local moveJob = tasks[#tasks]
assert(table.concat(moveJob.args, ":", 2) == "move:--id:focus-1:--section:DM 中文", "move keeps task ID and group as literal arguments")
focusData.result = { id = "focus-1", section = "DM 中文" }
moveJob.complete(0, encode(focusData), "")
event({ event = "ack" })
assert(task.writes[#task.writes].board.focus_ack.request_id == "move-1")
assert(task.writes[#task.writes].board.focus_notice == "Task moved to DM 中文.")
assert(calls.url == "raycast://focus/start?goal=encoded&duration=3000", "moving never starts a timer")
event({ event = "ack" })
message({ focus = { action = "move", id = "focus-1", section = "CALLE", request_id = "move-refresh" } })
local refreshWarning = "Task moved, but board refresh failed: duplicate task ID"
tasks[#tasks].complete(0, encode({ result = { id = "focus-1", section = "CALLE", line = 8 }, refresh_error = refreshWarning }), "")
event({ event = "ack" })
local partial = task.writes[#task.writes].board
assert(partial.focus_ack.request_id == "move-refresh" and not partial.focus_ack.error, "a committed move must be acknowledged as successful")
assert(#partial.todos == 1 and partial.todos[1].id == "focus-1" and partial.todos[1].section == "CALLE", "failed refresh retains the board with the moved task")
assert(partial.todos[1].line == 8 and board.status().focus_error == refreshWarning)
event({ event = "ack" })
calls.pollFocus()
tasks[#tasks].complete(0, encode(focusData), "")
event({ event = "ack" })
assert(not board.status().focus_error, "a successful refresh clears the warning")
event({ event = "ack" })
message({ id = "@1", session = "work" }); assert(calls.app == "Ghostty")
assert(calls.execute:find("select-window -t @1", 1, true))
local frame = { x = 10, y = 20, w = 600, h = 700 }
event({ event = "frame", frame = frame }); assert(settings.taskBoardFrame == frame)
calls.changed({ "/tmp/board.json" })
assert(task.writes[#task.writes].command == "update")
event({ event = "ack" })
urls["task-board"](nil, { action = "toggle" })
assert(task.writes[#task.writes].command == "toggle")
local stoppedComplete, stoppedStream = task.callback, task.streamCallback
board.stop()
assert(hs.shutdownCallback == originalShutdown, "stop must restore the previous shutdown hook")
assert(not task.callback and type(task.streamCallback) == "function", "closing a running helper retains only an output drainer")
assert(debug.getinfo(task.streamCallback, "u").nups == 0, "the drainer must not capture the helper or module state")
assert(task.closed and not hotkeys.b and not hotkeys.d and not urls["task-board"])
board.start()
local replacement = tasks[#tasks - 1]
assert(replacement.writes[1].frame == frame, "frame must survive reload")
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
replacement.stream(replacement, encode({ event = "message", body = { ready = true } }) .. "\n", "")
assert(replacement.writes[#replacement.writes].board.focus.minutes == saved.minutes, "duration must survive reload")
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
local lastURL = calls.url
-- Completion changes the Markdown task status, without stopping Raycast.
replacement.stream(replacement, encode({ event = "message", body = { focus = { action = "complete", id = "focus-1", minutes = 50, request_id = "r5" } } }) .. "\n", "")
local readJob = tasks[#tasks]
readJob.complete(0, encode(focusData), "")
local completeJob = tasks[#tasks]
assert(completeJob.args[2] == "complete")
focusTask.done = true
-- Hammerspoon may deliver buffered stdout after its termination callback.
encoded["late-response-ABC"] = focusData
completeJob.stream(completeJob, "late-response-", "")
completeJob.complete(0, "C", "")
assert(not board.status().focus_error, "process exit must wait for late stdout before reporting failure")
local pendingTimer = calls.finishTimer
assert(completeJob.callback and completeJob.streamCallback, "retain callbacks until late stdout is fully drained")
local lateStream = completeJob.streamCallback
local jobCount = #tasks
calls.pollFocus()
assert(#tasks == jobCount, "do not replace a job awaiting its last output chunk")
completeJob.stream(nil, "AB", "")
assert(pendingTimer.stopped, "a complete response cancels the drain timeout")
assert(not completeJob.callback and not completeJob.streamCallback, "successful late stdout releases job callbacks")
assert(board.status().focus_error == nil)
assert(replacement.writes[#replacement.writes].board.focus_ack.request_id == "r5")
assert(replacement.writes[#replacement.writes].board.todos[1].done)
local writes = #replacement.writes
pendingTimer.callback()
lateStream(nil, "", "")
assert(#replacement.writes == writes, "late callbacks must acknowledge a request only once")
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
-- A truly truncated response must fail visibly and allow the next request.
calls.pollFocus()
local brokenJob = tasks[#tasks]
brokenJob.complete(0, "truncated", "")
local timeout = calls.finishTimer
assert(not timeout.stopped)
timeout.callback()
assert(not brokenJob.callback and not brokenJob.streamCallback, "timeout releases job callbacks")
assert(board.status().focus_error:find("Incomplete", 1, true))
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
calls.pollFocus()
local recovery = tasks[#tasks]
recovery.complete(0, "", "")
recovery.stream(nil, encode(focusData), "")
assert(not board.status().focus_error)
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
assert(calls.url == lastURL)
calls.failNextStart = true
calls.pollFocus()
local failedReader = tasks[#tasks]
assert(not failedReader.callback and not failedReader.streamCallback, "failed reader start clears both registry callbacks")
assert(board.status().focus_error:find("Could not start", 1, true))
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
calls.pollFocus()
tasks[#tasks].complete(0, encode(focusData), "")
replacement.stream(replacement, encode({ event = "ack" }) .. "\n", "")
assert(not board.status().focus_error)
stoppedComplete(0, "", "")
assert(board.status().running, "old completion must not clear new process")
assert(stoppedStream(task, encode({ event = "ack" }) .. "\n", "") == false)
-- Stopping must detach an in-flight reader without killing a possible Markdown write.
calls.pollFocus()
local inFlight = tasks[#tasks]
local inFlightComplete = inFlight.callback
board.stop()
assert(replacement.closed)
assert(not replacement.callback and type(replacement.streamCallback) == "function")
assert(not inFlight.callback and type(inFlight.streamCallback) == "function", "stop replaces in-flight callbacks with an output drainer")
assert(debug.getinfo(inFlight.streamCallback, "u").nups == 0, "the drainer must not capture the writer or module state")
local drained = 0
for _ = 1, 32 do
  local chunk = string.rep("output", 8192)
  assert(inFlight.stream(inFlight, chunk, chunk) == true, "a stopped writer must keep reading beyond a pipe buffer")
  drained = drained + #chunk * 2
end
assert(drained > 1024 * 1024)
assert(inFlight.running, "shutdown must let an authorized write finish")
inFlightComplete(0, encode(focusData), "")
assert(not board.status().running, "late completion after stop must not resurrect the board")
assert(hs.shutdownCallback == originalShutdown)
-- Start failure must not leak a callback cycle, and retry may recover normally.
local beforeFailure = #tasks
calls.failNextStart = true
board.start()
local failedWindow = tasks[beforeFailure + 1]
assert(not failedWindow.callback and not failedWindow.streamCallback, "failed start clears helper callbacks")
board.stop()
assert(hs.shutdownCallback == originalShutdown)
print("PASS: ordered IPC, fragmented events, all actions, frame persistence, reload cleanup")
