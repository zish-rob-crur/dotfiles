local wezterm = require 'wezterm'
local mux = wezterm.mux
local config = wezterm.config_builder()

local DarkTheme = "Tokyo Night (Gogh)"

local LightTheme = "Catppuccin Latte"

function hash_to_color(input)
  local hash = 0
  for i = 1, #input do
    hash = (hash * 31 + input:byte(i)) % 0xFFFFFF
  end
  return string.format("#%06x", hash)
end

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
config.show_new_tab_button_in_tab_bar = true
config.hide_tab_bar_if_only_one_tab = false
config.hide_mouse_cursor_when_typing = true
config.window_background_opacity = 0.97
config.tab_max_width = 40
config.tab_bar_at_bottom = false
config.macos_window_background_blur = 50
config.max_fps = 120
config.use_fancy_tab_bar = true
config.window_frame = {
  font = wezterm.font { family = 'Roboto', weight = 'Bold' },
  font_size = 14.0,
}
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
    return string.format("[%d] %s ", tab_index, title)
  end
  -- Otherwise, use the title from the active pane
  -- in that tab
  return tab_info.active_pane.title
end

wezterm.on(
  'format-tab-title',
  function(tab, tabs, panes, config, hover, max_width)
    local title = tab_title(tab)
    local prefix = ""
    local suffix = ""
    local foreground_color = hash_to_color(title)
    if tab.is_active then
      prefix = "▶️"
      suffix = "◀️"
    else
      prefix = " "
      suffix = " "
    end
    return {
      { Foreground = { Color = foreground_color } },
      { Text = prefix .. title .. suffix }
    }
  end
)
config.window_decorations = "INTEGRATED_BUTTONS|RESIZE"
config.initial_cols = 120
config.initial_rows = 40
config.window_close_confirmation = 'AlwaysPrompt'
window_frame = {
  active_titlebar_bg = '#0F2536',
  inactive_titlebar_bg = '#0F2536',
  -- font = fonts.font,
  -- font_size = fonts.font_size,
}
config.inactive_pane_hsb = { saturation = 1.0, brightness = 1.0 }
return config
