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
vim.opt.rtp:prepend(lazypath)

vim.g.mapleader = " "
vim.wo.relativenumber = true

require("lazy-init")
if vim.g.vscode then
    require("vscode-init")

end

-- 使用系统剪切板
vim.opt.clipboard = "unnamedplus"