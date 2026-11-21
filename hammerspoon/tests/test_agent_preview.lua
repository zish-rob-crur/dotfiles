-- Exercise agent_preview.lua HUD lifecycle, non-activating behavior, and modal controls.
local source = assert(arg[1], "agent_preview.lua path required")
local HOME = assert(os.getenv("HOME"), "HOME is required")
local STATE_ROOT = HOME .. "/.cache/codex-tmux-status"

local json_seq, encoded = 0, {}
local function encode(val)
  json_seq = json_seq + 1
  local k = "json-" .. json_seq
  encoded[k] = val
  return k
end
local function decode(raw)
  local stripped = type(raw) == "string" and (raw:gsub("\n$", "")) or raw
  return encoded[stripped] or raw
end

local dirs, hotkeys, urls, tasks, timers = {}, {}, {}, {}, {}
local modal_instance, webview_instance, watcher_cb
local originalShutdown = function() end

local screen = {
  frame = function() return { x = 0, y = 0, w = 1600, h = 1000 } end,
}

hs = {
  shutdownCallback = originalShutdown,
  json = { encode = encode, decode = decode },
  fs = {
    attributes = function(p) return dirs[p] and { mode = "directory" } or nil end,
    mkdir = function(p) dirs[p] = true; return true end,
  },
  screen = { primaryScreen = function() return screen end, mainScreen = function() return screen end },
  window = { focusedWindow = function() return { screen = function() return screen end } end },
  mouse = { getCurrentScreen = function() return screen end },
  alert = { show = function() end },
  printf = function() end,
  drawing = { windowLevels = { floating = 3 } },
  hotkey = {
    bind = function(_, key, fn)
      hotkeys[key] = fn
      return { delete = function() hotkeys[key] = nil end }
    end,
    modal = {
      new = function()
        local m = { entered = false, deleted = false, bindings = {} }
        function m:bind(_, key, fn) self.bindings[key] = fn; return self end
        function m:enter() self.entered = true; return self end
        function m:exit() self.entered = false; return self end
        function m:delete() self.deleted = true end
        modal_instance = m
        return m
      end,
    },
  },
  urlevent = {
    bind = function(name, fn) urls[name] = fn end,
    openURL = function(u) urls.lastOpened = u end,
  },
  pasteboard = {
    setContents = function(txt) urls.copied = txt end,
  },
  pathwatcher = {
    new = function(_, fn)
      watcher_cb = fn
      return {
        start = function(self) self.running = true; return self end,
        stop = function(self) self.running = false end,
      }
    end,
  },
  timer = {
    doAfter = function(delay, fn)
      local t = { delay = delay, fn = fn, stopped = false }
      function t:stop() self.stopped = true end
      timers[#timers + 1] = t
      return t
    end,
  },
  webview = {
    usercontent = {
      new = function()
        local u = {}
        function u:setCallback(fn) self.cb = fn; return self end
        return u
      end,
    },
    new = function(rect, _, ucc)
      local w = { rect = rect, ucc = ucc, visible = false, evals = {} }
      function w:windowStyle(s) self.style = s; return self end
      function w:level(l) self.lvl = l; return self end
      function w:behaviorAsLabels(b) self.behavior = b; return self end
      function w:allowTextEntry(v) self.textEntry = v; return self end
      function w:transparent(v) self.trans = v; return self end
      function w:shadow(v) self.sh = v; return self end
      function w:deleteOnClose(v) self.delClose = v; return self end
      function w:navigationCallback(fn) self.navCb = fn; return self end
      function w:html(h) self.htmlContent = h; return self end
      function w:frame(r) if r then self.rect = r; return self end return self.rect end
      function w:show() self.visible = true; return self end
      function w:hide() self.visible = false; return self end
      function w:delete() self.deleted = true end
      function w:evaluateJavaScript(js) self.evals[#self.evals + 1] = js; return self end
      webview_instance = w
      return w
    end,
  },
  task = {
    new = function(path, complete, stream, args)
      local is_stream = type(stream) == "function"
      local actual_args = is_stream and args or stream
      local t = {
        path = path,
        cb = complete,
        streamCb = is_stream and stream or nil,
        args = actual_args,
        running = false,
      }
      function t:setCallback(fn) self.cb = fn; return self end
      function t:setStreamingCallback(fn) self.streamCb = fn; return self end
      function t:isRunning() return self.running end
      function t:setEnvironment(e) self.env = e; return self end
      function t:start() self.running = true; return self end
      function t.stream(out, err)
        if t.streamCb then return t.streamCb(t, out, err) end
        return false
      end
      function t.complete(code, out, err, add_nl)
        t.running = false
        local payload = add_nl and (out .. "\n") or out
        if t.cb then t.cb(code, payload, err) end
      end
      tasks[#tasks + 1] = t
      return t
    end,
  },
}

local preview = assert(loadfile(source))()
preview.start()
assert(hs.shutdownCallback ~= originalShutdown)
assert(#tasks == 1 and tasks[1].args[2] == "snapshot")

-- Seed snapshot populates seen_ns WITHOUT auto-popping HUD on reload.
tasks[1].complete(0, encode({
  unread = {
    { key = "/tmp/sock:%1", tmux_socket = "/tmp/sock", session_name = "main", window_id = "@1", pane_id = "%1", unread = true, completed_at_ns = 100, compact_html = "old" },
  },
  recent = {},
}), "", true)
assert(preview.status().visible == false)

-- Pathwatcher ignores unrelated files, reacts to pane-2.json completion.
watcher_cb({ STATE_ROOT .. "/servers/abc/refresh-render.lock" })
assert(#timers == 0)
watcher_cb({ STATE_ROOT .. "/servers/abc/pane-2.json" })
assert(#timers == 1)
timers[1].fn()
assert(#tasks == 2)

tasks[2].complete(0, encode({
  unread = {
    { key = "/tmp/sock:%2", tmux_socket = "/tmp/sock", session_name = "main", window_id = "@2", pane_id = "%2", cwd = "/tmp/proj", unread = true, completed_at_ns = 200, compact_html = "new-2", full_html = "full-2", plain_text = "plain-2" },
    { key = "/tmp/sock:%1", tmux_socket = "/tmp/sock", session_name = "main", window_id = "@1", pane_id = "%1", cwd = "/tmp/proj", unread = true, completed_at_ns = 100, compact_html = "old", full_html = "full-1", plain_text = "plain-1" },
  },
  recent = {},
}), "", true)

local st = preview.status()
assert(st.visible == true and st.expanded == false and st.interactive == false)
assert(st.count == 2 and st.index == 1)
assert(modal_instance.entered == false)
assert(webview_instance.textEntry == false)
assert(webview_instance.style[2] == "nonactivating")
assert(webview_instance.visible == true)

-- Simulate webview DOMContentLoaded ready callback.
webview_instance.ucc.cb({ body = { action = "ready" } })
assert(#webview_instance.evals == 1)

-- Passive toast auto-rotates across the 2 unread items every 8s (2 passes = 4 ticks) before hiding.
assert(timers[#timers].delay == 8)
timers[#timers].fn() -- tick 1: 1/2 -> 2/2
assert(preview.status().visible == true and preview.status().index == 2)
timers[#timers].fn() -- tick 2: 2/2 -> 1/2
assert(preview.status().visible == true and preview.status().index == 1)

-- Pressing hotkey promotes compact toast -> interactive modal (keeps compact 430x290 size by default).
hotkeys["u"]()
st = preview.status()
assert(st.expanded == false and st.interactive == true and modal_instance.entered == true)
assert(webview_instance:frame().w == 430 and webview_instance:frame().h == 290)

-- Pressing 'e' expands and collapses between 580x450 and 430x290.
modal_instance.bindings["e"]()
st = preview.status()
assert(st.expanded == true and webview_instance:frame().w == 580 and webview_instance:frame().h == 450)
modal_instance.bindings["e"]()
st = preview.status()
assert(st.expanded == false and webview_instance:frame().w == 430 and webview_instance:frame().h == 290)

-- Pressing 'y' copies plain_text to clipboard; clicking URL/path links routes properly.
modal_instance.bindings["y"]()
assert(urls.copied == "plain-2")
webview_instance.ucc.cb({ body = { action = "open-url", url = "https://example.com/report" } })
assert(urls.lastOpened == "https://example.com/report")
webview_instance.ucc.cb({ body = { action = "open-path", path = "artifacts/report.html" } })
assert(tasks[#tasks].args[2] == "open-path" and tasks[#tasks].args[6] == "artifacts/report.html")

-- Press 'r' (Mark Read) -> sends ack, switches cleanly to remaining unread item %1.
modal_instance.bindings["r"]()
assert(tasks[#tasks].args[2] == "ack" and tasks[#tasks].args[6] == "%2")
assert(preview.status().count == 1 and preview.status().visible == true)

-- Press 'r' again -> clears last unread item, hides HUD, and exits modal.
modal_instance.bindings["r"]()
assert(tasks[#tasks].args[2] == "ack" and tasks[#tasks].args[6] == "%1")
assert(preview.status().visible == false and modal_instance.entered == false)

-- Toggle while hidden triggers fresh interactive snapshot; chunked streaming reassembles before decoding.
hotkeys["u"]()
assert(tasks[#tasks].args[2] == "snapshot")
encoded["chunked-snapshot"] = {
  unread = {},
  recent = {
    { key = "/tmp/sock:%9", tmux_socket = "/tmp/sock", session_name = "main", window_id = "@9", pane_id = "%9", unread = false, completed_at_ns = 50, full_html = "recent" },
  },
}
tasks[#tasks].stream("chunked-", "")
assert(preview.status().visible == false)
tasks[#tasks].complete(0, "snapshot", "", true)
assert(preview.status().visible == true and preview.status().mode == "recent" and modal_instance.entered == true)
modal_instance.bindings["escape"]()
assert(preview.status().visible == false and modal_instance.entered == false)

preview.stop()
assert(webview_instance.deleted == true and modal_instance.deleted == true and hs.shutdownCallback == originalShutdown)
print("agent_preview.lua tests passed")
