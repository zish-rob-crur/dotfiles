local wezterm = require 'wezterm'
local mux = wezterm.mux
local config = wezterm.config_builder()

local LightTheme = "Flexoki Light"
local DarkTheme = "Flexoki Dark"

function get_colordir()
  local color_dir = os.getenv("HOME") .. "/GitHubRepos/dotfiles/wezterm/colors"
  wezterm.log_error('Color Dir ' .. color_dir)
  return color_dir
end

function get_theme()
  if wezterm.gui.get_appearance() == "Light" then
    return LightTheme
  end
  return DarkTheme
end

config.font = wezterm.font('JetBrains Mono')
config.color_scheme_dirs = { get_colordir() }
config.color_scheme = get_theme()
config.window_decorations = "RESIZE"
config.font_size = 16.0
config.tab_max_width = 25
config.show_new_tab_button_in_tab_bar = false
config.hide_tab_bar_if_only_one_tab = false
config.window_background_opacity = 0.97
config.use_fancy_tab_bar = true
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
wezterm.plugin.require("https://github.com/nekowinston/wezterm-bar").apply_to_config(config, {
  position = "top",
  max_width = 32,
  dividers = false,
  indicator = {
    leader = {
      enabled = true,
      off = " ",
      on = " ",
    },
    mode = {
      enabled = true,
      names = {
        resize_mode = "RESIZE",
        copy_mode = "VISUAL",
        search_mode = "SEARCH",
      },
    },
  },
  tabs = {
    numerals = "arabic",
    pane_count = false,
    brackets = {
      active = { "(", ")" },
      inactive = { "[", "]" },
    },
  },
  clock = {
    enabled = false,
    format = "%Y-%m-%d %H:%M:%S",
  },
})

return config
