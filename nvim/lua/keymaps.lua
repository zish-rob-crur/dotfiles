-- set leader key to space
vim.g.mapleader = ' '

-- nvim-tree leader + e to open file explorer
vim.api.nvim_set_keymap('n', '<leader>e', ':NvimTreeFocus<CR>', { noremap = true, silent = true })

