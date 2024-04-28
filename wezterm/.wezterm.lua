local wezterm = require 'wezterm'
local mux = wezterm.mux
local config = wezterm.config_builder()

local DarkTheme = "Tokyo Night (Gogh)"

local LightTheme = "Catppuccin Latte"

function get_colordir()
  local path_sep = package.config:sub(1, 1)
  local color_dir = os.getenv("HOME") or os.getenv("USERPROFILE")
  color_dir = color_dir ..
      path_sep .. "GitHubRepos" .. path_sep .. "dotfiles" .. path_sep .. "wezterm" .. path_sep .. "colors"
  return color_dir
end

function get_appearance()
  if wezterm.gui then
    return wezterm.gui.get_appearance()
  end
  return 'Dark'
end

function scheme_for_appearance(appearance)
  if appearance:find 'Dark' then
    return DarkTheme
  else
    return LightTheme
  end
end

wezterm.on("update-right-status", function(window, pane)
  local overrides = {}
  if wezterm.gui.get_appearance() == "Light" then
    overrides.theme = LightTheme
  else
    overrides.theme = DarkTheme
  end
  window:set_config_overrides(overrides)
end
)

config.font = wezterm.font('JetBrains Mono')
config.color_scheme_dirs = { get_colordir() }
config.color_scheme = scheme_for_appearance(get_appearance())
config.window_decorations = "RESIZE"
config.font_size = 16.0
config.show_new_tab_button_in_tab_bar = false
config.hide_tab_bar_if_only_one_tab = true
config.hide_mouse_cursor_when_typing = true

config.window_background_opacity = 0.97

config.use_fancy_tab_bar = false
config.tab_max_width = 50

config.tab_bar_at_bottom = false
config.macos_window_background_blur = 50
config.max_fps = 120
config.keys = { {
  key = "LeftArrow",
  mods = "ALT",
  action = wezterm.action {
    SendKey = {
      key = "LeftArrow",
      mods = "CTRL"
    }
  }
}, {
  key = "RightArrow",
  mods = "ALT",
  action = wezterm.action {
    SendKey = {
      key = "RightArrow",
      mods = "CTRL"
    }
  }
},
  {
    key = 'w',
    mods = 'CMD',
    action = wezterm.action.CloseCurrentPane { confirm = false },
  },

}
config.window_padding = {
  left = 5,
  right = 10,
  top = 12,
  bottom = 7
}


-- This function returns the suggested title for a tab.
-- It prefers the title that was set via `tab:set_title()`
-- or `wezterm cli set-tab-title`, but falls back to the
-- title of the active pane in that tab.
function tab_title(tab_info)
  local title = tab_info.tab_title
  local tab_index = tab_info.tab_index + 1
  if not title or title == "" then
    title = tab_info.active_pane.title
  end
  -- if the tab title is explicitly set, take that
  if title and #title > 0 then
    return  string.format("[%d] %s ", tab_index, title)
  end
  -- Otherwise, use the title from the active pane
  -- in that tab
  return tab_info.active_pane.title
end

wezterm.on(
  'format-tab-title',
  function(tab, tabs, panes, config, hover, max_width)
    local title = tab_title(tab)

    if tab.is_active then
      return {
        { Text = "<" .. title .. ">" }
      }
    else
      return {
        { Text = " " .. title .. " " }
      }
    end
  end
)
return config
