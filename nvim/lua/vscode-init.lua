local vscode = require('vscode')
local utils = require('map_utils')
local lua_fn = utils.lua_fn
local lua_expr = utils.lua_expr
local key = vim.api.nvim_set_keymap

local noremap = { noremap = true }
local remap = { noremap = false }
local expr = { expr = true }

-- 设置快捷键 space + e 打开文件树
vim.api.nvim_set_keymap('n', '<space>e', lua_fn(function()
    vscode.action('workbench.view.explorer')
end), noremap)

print("vscode nvim config end")