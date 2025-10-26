
#Requires AutoHotkey v2.0
#SingleInstance Force
#UseHook
if !A_IsAdmin {
    try Run('*RunAs "' A_ScriptFullPath '"')
    ExitApp
}

; ===== 单击 F13（原 CapsLock）→ 切换微信输入法中/英（发送 Shift）=====
; 想把“单击改为长按≥300ms才切换”，把下面 KeyWait 的超时改成 "T0.30" 并加判断即可
$F13::
{
    ; 轻点立即切换（不搞复杂的长按判定）
    KeyWait("F13")             ; 等抬起，避免和组合键竞争
    SendEvent("{Shift}")       ; 依赖“微信输入法：中英切换=Shift”
    return
}

; ===== 可选：F13 作为前缀的组合键（保留你常用的）=====
F13 & h::Send("{Left}")
F13 & j::Send("{Down}")
F13 & k::Send("{Up}")
F13 & l::Send("{Right}")

F13 & a::Send("^a")
F13 & b::Send("^b")
F13 & c::Send("^c")
F13 & d::Send("^d")
