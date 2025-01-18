#NoEnv
SendMode Input
SetWorkingDir %A_ScriptDir%

; 1. 确保 CapsLock 始终关闭
SetCapsLockState, AlwaysOff

; 2. 每次按下 CapsLock 时，执行此操作
CapsLock::
    SetCapsLockState, AlwaysOff
    Send ^{Esc} ; 将 CapsLock 映射为 Ctrl
return

; 3. Ctrl + h/j/k/l 分别作为方向键
^h::Send {Left}
^j::Send {Down}
^k::Send {Up}
^l::Send {Right}
