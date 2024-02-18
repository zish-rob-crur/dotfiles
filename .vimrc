" 显示行号
set number
let mapleader = "\<space>"
" group python 
" 自动寻找 typing import 并追加
nnoremap <leader>t /from typing<CR>$a

" group golang

" group Vue
" 自动寻找 <template <script <style 并跳转
nnoremap <leader>vt /<template<CR>
nnoremap <leader>vs /<script<CR>
nnoremap <leader>vy /<style<CR

" 在normal模式下，按下 leader +dgg 删除所有的换行符
nnoremap <leader>dgg :%s/\n//g<CR> 

" 在norm 模式下, 按下 leader + dnn 替换\n 回车
nnoremap <leader>dnn :%s/\\n//g<CR>

" leader + c 关闭当前buffer
nnoremap <leader>c :bd<CR>
