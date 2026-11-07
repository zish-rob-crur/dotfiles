-- Run the real supervisor against a Lua model of Hammerspoon's C registry roots.
-- No processes, UI, files, or live Hammerspoon state are changed.
-- A task's exit releases selfRef, but callback refs survive until task_gc;
-- stopping a timer does not release its callback ref until timer_gc.
local source = assert(arg[1], 'task_board.lua path required')
local registry, nextRef = {}, 0
local weakTasks, weakTimers = setmetatable({}, {__mode='v'}), setmetatable({}, {__mode='v'})
local taskSerial, timerSerial = 0, 0
local function ref(value)
  if value == nil then return nil end
  nextRef = nextRef + 1; registry[nextRef] = value; return nextRef
end
local function unref(key) if key then registry[key] = nil end end
local function count(values) local n = 0; for _ in pairs(values) do n = n + 1 end; return n end
local function collect() for _ = 1, 4 do collectgarbage('collect') end end
local Task = {}; Task.__index = Task
function Task:__gc() unref(self.callbackRef); unref(self.streamRef); unref(self.selfRef) end
function Task:setCallback(fn) unref(self.callbackRef); self.callbackRef = ref(fn); return self end
function Task:setStreamingCallback(fn) unref(self.streamRef); self.streamRef = ref(fn); return self end
function Task:start()
  if self.failStart then return nil end
  self.running = true; self.selfRef = ref(self); return self
end
function Task:isRunning() return self.running end
function Task:setInput(_) return self end
function Task:setEnvironment(_) return self end
function Task:closeInput() self.closed = true; return self end
function Task:emit(chunk)
  if self.readStopped then return false end
  local callback = registry[self.streamRef]
  local keepReading = callback and callback(self, chunk or '', '')
  if keepReading ~= true then self.readStopped = true; return false end
  self.drained = (self.drained or 0) + #(chunk or '')
  return true
end
function Task:finish(code, out)
  self.running = false; unref(self.selfRef); self.selfRef = nil
  local callback = registry[self.callbackRef]
  if callback then callback(code or 0, out or '', '') end
end
local function task(path, callback, stream, args)
  taskSerial = taskSerial + 1
  local value = setmetatable({path=path,args=args or stream}, Task)
  value:setCallback(callback):setStreamingCallback(type(stream) == 'function' and stream or nil)
  weakTasks[taskSerial] = value
  return value
end
local Timer = {}; Timer.__index = Timer
function Timer:__gc() unref(self.callbackRef) end
function Timer:stop() self.stopped = true; return self end
function Timer:fire()
  if not self.stopped then
    local callback = registry[self.callbackRef]
    if self.once then self.stopped = true end
    if callback then callback() end
  end
end
local function timer(callback, once)
  timerSerial = timerSerial + 1
  local value = setmetatable({callbackRef=ref(callback),once=once}, Timer)
  weakTimers[timerSerial] = value
  return value
end
local snapshot = {windows={},todos={}}
local function decode(value)
  if value == 'board' then return snapshot end
  if value == 'ack' then return {event='ack'} end
  if value == 'ready' then return {event='message',body={ready=true}} end
  if value:sub(1, 8) == 'payload:' and value:sub(-1) == '!' then
    return {tasks={{id='test-task',text=value}},vaults={{name='work'}},errors={},week='2026-W45'}
  end
  error('incomplete JSON model')
end
local originalOpen = io.open
io.open = function(path, mode)
  if path:match('/tmux%-task%-board/board.json$') then
    return {read=function() return 'board' end,close=function() end}
  end
  return originalOpen(path, mode)
end
package.preload.neovide_window = function() return {find=function() return false end,open=function() return true end} end
local screen = {name=function() return 'test' end,frame=function() return {x=0,y=0,w=1200,h=800} end,fullFrame=function() return {} end}
local originalShutdown = function() end
local settings = {}
hs = {
  shutdownCallback=originalShutdown,
  json={encode=function() return '{}' end,decode=decode},
  settings={get=function(k) return settings[k] end,set=function(k,v) settings[k]=v end},
  fs={attributes=function() return {} end},
  screen={allScreens=function() return {screen} end,primaryScreen=function() return screen end},
  geometry={point=function() return {inside=function() return true end} end},
  task={new=task}, timer={doEvery=function(_,fn) return timer(fn,false) end,doAfter=function(_,fn) return timer(fn,true) end},
  printf=function() end,execute=function() return '' end,
  application={launchOrFocus=function() end},
  hotkey={bind=function() return {delete=function() end} end},
  urlevent={bind=function() end,openURL=function() return true end},
  pathwatcher={new=function() return {start=function(self) return self end,stop=function() end} end},
}
local function latestReader() return assert(weakTasks[taskSerial], 'missing reader') end
local function ack(window) window:emit('ack\n') end
local function payload(i) return 'payload:' .. tostring(i) .. string.rep(string.char(65+i%26), 64*1024) .. '!' end
local function roundTrip(window, reader, i)
  local value = payload(i)
  if i % 3 == 0 then
    -- Exit precedes final output, so the drain timer is created and cancelled.
    reader:emit(value:sub(1, 16))
    reader:finish(0, '!')
    reader:emit(value:sub(17, -2))
  elseif i % 5 == 0 then
    reader:finish(0, 'truncated')
    assert(weakTimers[timerSerial]):fire()
  else reader:finish(0, value) end
  ack(window)
end
collect()
local baseline = collectgarbage('count')
for cycle = 1, 10 do
  local board = assert(loadfile(source))()
  local initialTask = taskSerial
  board.start()
  local window = assert(weakTasks[initialTask+1])
  local pollTimer = assert(weakTimers[timerSerial])
  ack(window); window:emit('ready\n'); ack(window)
  roundTrip(window, latestReader(), cycle*100)
  for n = 1, 40 do
    pollTimer:fire()
    roundTrip(window, latestReader(), cycle*100+n)
  end
  -- Completed jobs/timers must already be collectible while the module runs.
  collect()
  assert(count(weakTasks) == 1, 'completed readers remain rooted: ' .. count(weakTasks))
  assert(count(weakTimers) == 1, 'stopped drain timers remain rooted: ' .. count(weakTimers))
  -- A stop may race an active writer; detachment must not kill it.
  pollTimer:fire()
  local inFlight = latestReader()
  board.stop()
  assert(inFlight:isRunning(), 'stop killed an authorized write')
  assert(inFlight.callbackRef == nil and type(registry[inFlight.streamRef]) == 'function', 'stopped writer needs an output drainer')
  assert(debug.getinfo(registry[inFlight.streamRef], 'u').nups == 0, 'drainer captures task/module state')
  -- Model continued read re-arming: output exceeds any ordinary pipe buffer.
  -- A missing/false streaming callback stops reads and prevents this writer exiting.
  local chunk = string.rep('late output', 8192)
  for _ = 1, 16 do assert(inFlight:emit(chunk), 'stopped writer blocked while draining a large snapshot') end
  assert(inFlight.drained > 1024 * 1024)
  assert(window:emit(chunk), 'closing helper still needs its final output drained')
  inFlight:finish(0, '')
  window:finish(0, '')
  assert(hs.shutdownCallback == originalShutdown, 'reload retained shutdown wrapper')
  board, window, pollTimer, inFlight = nil, nil, nil, nil
  collect()
  assert(count(weakTasks) == 0, 'reload retained task userdata')
  assert(count(weakTimers) == 0, 'reload retained timer userdata')
  assert(count(registry) == 0, 'reload retained C registry roots')
end
collect()
local growth = collectgarbage('count') - baseline
assert(growth < 1024, ('heap grew %.1f KiB after completed polls/reloads'):format(growth))
print(('PASS: 410 polls and 10 reloads release task/timer registry roots; Lua heap delta %.1f KiB'):format(growth))
