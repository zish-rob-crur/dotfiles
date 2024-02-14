"自动修改rebase
function! RebaseSquash()
    if getline(1) =~ '^pick'
      let l:current_line = 2
      let l:num_lines = line('$')
      while l:current_line <= l:num_lines
        if getline(l:current_line) !~ '^#'
          execute l:current_line . "s/^pick/squash/"
        endif
        let l:current_line += 1
      endwhile
    endif
endfunction