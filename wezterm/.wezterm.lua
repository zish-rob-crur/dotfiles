local wezterm = require 'wezterm'
local mux = wezterm.mux
local config = wezterm.config_builder()

local DarkTheme = "Flexoki Dark"

function get_colordir()
  local path_sep = package.config:sub(1,1) 
  local color_dir = os.getenv("HOME") or os.getenv("USERPROFILE") 
  color_dir = color_dir .. path_sep .. "GitHubRepos" .. path_sep .. "dotfiles" .. path_sep .. "wezterm" .. path_sep .. "colors"
  return color_dir
end

function get_theme()
  return DarkTheme
end

config.font = wezterm.font('JetBrains Mono')
config.color_scheme_dirs = { get_colordir() }
config.color_scheme = get_theme()
config.window_decorations = "RESIZE"
config.font_size = 16.0
config.show_new_tab_button_in_tab_bar = false
config.hide_tab_bar_if_only_one_tab = true 
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

return config
