require("options")
require("plugins")
require("keymaps")
local cmd = vim.cmd

local git_vim_path = vim.api.nvim_get_runtime_file("vim/git.vim", false)[1]
if git_vim_path then
    cmd("source " .. git_vim_path)
end

local vimrc_path = "~/.vimrc"
if vim.fn.filereadable(vimrc_path) == 1 then
    cmd("source " .. vimrc_path)
end

vim.cmd.colorscheme "catppuccin"