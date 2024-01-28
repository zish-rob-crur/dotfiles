" 显示行号
set number
let mapleader = "\<space>"
" group python 
" 自动寻找 typing import 并追加
nnoremap <leader>t /from typing<CR>$a

" group golang
" 自动输入 if err != nil { return err }
nnoremap <leader>e if err != nil { <CR>   return err <CR> }<CR><Esc>  

" group Vue
" 自动寻找 <template <script <style 并跳转
nnoremap <leader>vt /<template<CR>
nnoremap <leader>vs /<script<CR>
nnoremap <leader>vy /<style<CR

" 重载配置 leader + r
nnoremap <leader>r :source $MYVIMRC<CR>

" 在插入模式下，按 hjkl 移动光标
inoremap <C-j> <Down>
inoremap <C-k> <Up>
inoremap <C-h> <Left>
inoremap <C-l> <Right>

" 在normal模式下，按下 leader +dgg 删除所有的换行符
nnoremap <leader>dgg :%s/\n//g<CR> 


" 在norm 模式下, 按下 leader + dnn 替换\n 回车
nnoremap <leader>dnn :%s/\\n//g<CR>