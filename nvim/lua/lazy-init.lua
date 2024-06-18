plugins = {
    {
        "folke/tokyonight.nvim",
        lazy = false,
        priority = 1000,
        config = function()
            -- load the colorscheme here
            vim.cmd([[colorscheme tokyonight]])
        end,
        cond = {
            function()
                return not vim.g.vscode
            end
        }
    },
    {
        "unblevable/quick-scope",
        config = function()
            if vim.g.vscode then
                vim.cmd([[highlight QuickScopePrimary guifg='#afff5f' gui=underline ctermfg=155 cterm=underline]])
                vim.cmd([[highlight QuickScopeSecondary guifg='#5fffff' gui=underline ctermfg=81 cterm=underline]])
            end
        end
    },
    {
        'smoka7/hop.nvim',
        version = "*",
        config = function()
            local hop = require('hop')
            hop.setup()
            vim.keymap.set('n', '<leader>s',
                function()
                    hop.hint_patterns()
                end,
                { silent = true }
            )
        end
    },

}

require("lazy").setup(
    plugins, {

    }
)
