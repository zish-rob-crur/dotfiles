; Option + w/b to skip words
!w::Send, {Ctrl Down}{Right}{Ctrl Up}
!b::Send, {Ctrl Down}{Left}{Ctrl Up}
; Left Ctrl + n/m to HOME/END
^n::Send, {Home}
^m::Send, {End}
; Left Ctrl + hjkl to arrow keys (Vim-style navigation)
^h::Send, {Left}
^j::Send, {Down}
^k::Send, {Up}
^l::Send, {Right}
; Change CapsLock to Control + Space when pressed alone and to Control when pressed with other keys
*CapsLock::
KeyWait, CapsLock
If (A_PriorKey = "CapsLock") {
    Send, {LCtrl Down}{Space}{LCtrl Up}
} else {
    Send, {LCtrl Down}
}
KeyWait, CapsLock
Send, {LCtrl Up}
return
CapsLock Up::return ; Disable CapsLock toggling
