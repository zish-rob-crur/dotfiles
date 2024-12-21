#NoEnv               ; 推荐：禁止在变量查找中使用环境变量
SendMode Input       ; 推荐：提高发送命令的可靠度和速度
SetWorkingDir %A_ScriptDir%

; 1. 关闭系统的CapsLock
SetCapsLockState, AlwaysOff

; 2. 将CapsLock映射为Ctrl
;    这样一来，当你按下物理CapsLock键时，系统将识别为Ctrl键
CapsLock::Ctrl

; 3. Ctrl + h/j/k/l 分别作为方向键
^h::Send {Left}
^j::Send {Down}
^k::Send {Up}
^l::Send {Right}
