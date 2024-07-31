local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not (vim.uv or vim.loop).fs_stat(lazypath) then
    print("lazy.nvim not found")
    vim.fn.system({
        "git",
        "clone",
        "--filter=blob:none",
        "https://github.com/folke/lazy.nvim.git",
        "--branch=stable", -- latest stable release
        lazypath,
    })
end
-- disable netrw at the very start of your init.lua
vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1

-- optionally enable 24-bit colour
vim.opt.termguicolors = true

vim.opt.rtp:prepend(lazypath)

vim.g.mapleader = " "
vim.wo.relativenumber = true

require("lazy-init")
if vim.g.vscode then
    require("vscode-init")
else
    require("mason-lsp")
    require("lsp-clients.lua-ls")
    require("lsp-clients.pyright")
    require("cmp-config")
    require('barbar-config')
    require("catppuccin-config")
end

-- 使用系统剪切板
vim.opt.clipboard = "unnamedplus"