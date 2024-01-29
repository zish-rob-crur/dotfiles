local wezterm = require 'wezterm'

function getColordir()
    wezterm.log_error('Config Dir ' .. wezterm.config_dir)
    local color_dir = "~/GitHubRepos/dotfiles/wezterm/colors"
    wezterm.log_error('Color Dir ' .. color_dir)
    return color_dir
end

local config = {}
config.font = wezterm.font 'JetBrains Mono'
config.color_scheme_dirs = {getColordir()}
config.color_scheme = "Flexoki Dark"
config.window_decorations = "RESIZE"
config.font_size = 16.0
config.tab_max_width = 25
config.show_new_tab_button_in_tab_bar = false
config.hide_tab_bar_if_only_one_tab = true
config.window_background_opacity = 0.97
config.inactive_pane_hsb = {
    hue = 1.0,
    saturation = 1.0,
    brightness = 1.0
}
config.max_fps = 120
config.keys = {{
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
