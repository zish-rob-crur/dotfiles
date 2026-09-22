-- ⌘⇧I: put the clipboard image on the host of the ssh session in the focused
-- terminal and paste its path there. For a terminal with no local tmux, where
-- tmux's prefix+P cannot reach the session.
--
-- The path is what the far side needs: a coding agent (Codex, Claude Code)
-- attaches an image whose path is pasted into its prompt, and a shell command
-- takes it as an argument. A remote program cannot read this machine's
-- clipboard, and a terminal carries text, not images.
--
-- The host is read from the terminal's title, which a remote shell usually sets
-- to "user@host" or "host: ~/dir"; a title naming a host in ~/.ssh/config, or
-- carrying a user@host, is used without asking. Otherwise a picker offers the
-- configured hosts, most recently used first. ⌘⌥⇧I always asks.
local M = {}

local HOTKEY = { { "cmd", "shift" }, "i" }
local PICK_HOTKEY = { { "cmd", "alt", "shift" }, "i" }  -- same, but always ask which host
local HOME = assert(os.getenv("HOME"), "HOME is required")
local SCRIPT = HOME .. "/GitHubRepos/dotfiles/tmux/ssh-image-paste.sh"
local RECENT_SETTING = "remoteImagePasteRecentHosts"
local ENVIRONMENT = { HOME = HOME, PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin" }
local state = { hotkey = nil, pick_hotkey = nil }

local function recent_hosts()
  local value = hs.settings.get(RECENT_SETTING)
  return type(value) == "table" and value or {}
end

local function remember(host)
  local hosts = { host }
  for _, name in ipairs(recent_hosts()) do
    if name ~= host and #hosts < 10 then hosts[#hosts + 1] = name end
  end
  hs.settings.set(RECENT_SETTING, hosts)
end

-- Host aliases from ~/.ssh/config, patterns left out.
local function configured_hosts()
  local hosts, seen = {}, {}
  local file = io.open(HOME .. "/.ssh/config")
  if not file then return hosts end
  for line in file:lines() do
    local name = line:match("^%s*[Hh]ost%s+(.+)$")  -- match, not gmatch: "^" does not anchor in gmatch
    for alias in (name or ""):gmatch("%S+") do
      if not alias:find("[*?]") and not seen[alias] then
        seen[alias] = true
        hosts[#hosts + 1] = alias
      end
    end
  end
  file:close()
  return hosts
end

-- "user@host: ~/dir" and "user@host" are what a remote shell usually sets.
local function host_from_title(title)
  return (title or ""):match("([%w%._%-]+@[%w%._%-]+)") or (title or ""):match("^%s*([%w%._%-]+):")
end

local function sorted_hosts(first)
  local ordered, seen = {}, {}
  local function add(name)
    if name and name ~= "" and not seen[name] then
      seen[name] = true
      ordered[#ordered + 1] = name
    end
  end
  add(first)
  for _, name in ipairs(recent_hosts()) do add(name) end
  for _, name in ipairs(configured_hosts()) do add(name) end
  return ordered
end

-- Copy the clipboard image to `host`, then paste the remote path into whatever
-- window had focus (the clipboard keeps the path; the image is gone from it).
local function paste_to(host, window)
  hs.alert.show("→ " .. host, 1)
  local task = hs.task.new(SCRIPT, function(code, stdout, stderr)
    if code ~= 0 or stdout == "" then
      hs.alert.show((stderr ~= "" and stderr or stdout):gsub("%s+$", ""))
      return
    end
    remember(host)
    hs.pasteboard.setContents((stdout:gsub("%s+$", "")))
    if window then window:focus() end
    hs.timer.doAfter(0.1, function() hs.eventtap.keyStroke({ "cmd" }, "v", 0) end)
  end, { "--host", host })
  task:setEnvironment(ENVIRONMENT)
  if not task:start() then hs.alert.show("无法运行 ssh-image-paste.sh") end
end

-- Is this name one we can use without asking: a user@host, or an alias ssh
-- itself knows? A bare word from a title could be anything.
local function known_host(name)
  if not name or name == "" then return false end
  if name:find("@") then return true end
  for _, alias in ipairs(configured_hosts()) do
    if alias == name then return true end
  end
  return false
end

function M.paste(always_ask)
  if not hs.pasteboard.readImage() then
    hs.alert.show("剪贴板里没有图片")
    return
  end
  local window = hs.window.focusedWindow()
  local guess = host_from_title(window and window:title())
  if not always_ask and known_host(guess) then
    paste_to(guess, window)
    return
  end
  local hosts = sorted_hosts(guess)
  if #hosts == 0 then
    hs.alert.show("~/.ssh/config 里没有可用的主机")
    return
  end
  local choices = {}
  for _, name in ipairs(hosts) do choices[#choices + 1] = { text = name } end
  hs.chooser.new(function(choice)
    if choice then paste_to(choice.text, window) end
  end):placeholderText("把剪贴板图片传到哪台主机"):choices(choices):show()
end

function M.start()
  state.hotkey = hs.hotkey.bind(HOTKEY[1], HOTKEY[2], function() M.paste(false) end)
  state.pick_hotkey = hs.hotkey.bind(PICK_HOTKEY[1], PICK_HOTKEY[2], function() M.paste(true) end)
  hs.urlevent.bind("remote-image-paste", function(_, params) M.paste(params and params.ask == "1") end)
  return M
end

function M.stop()
  if state.hotkey then state.hotkey:delete() end
  if state.pick_hotkey then state.pick_hotkey:delete() end
  hs.urlevent.bind("remote-image-paste", nil)
  state.hotkey, state.pick_hotkey = nil, nil
end

return M
