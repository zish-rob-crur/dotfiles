local map = vim.api.nvim_set_keymap
local opts = { noremap = true, silent = true }

map('n', '<leader>ff', '<cmd>Telescope find_files<CR>', opts)
map('n', '<leader>fr', '<cmd>Telescope oldfiles<CR>', opts)
map('n', '<leader>fg', '<cmd>Telescope live_grep<CR>', opts)
map('n', '<leader>fb', '<cmd>Telescope buffers<CR>', opts)
map('n', '<leader>fh', '<cmd>Telescope help_tags<CR>', opts)
