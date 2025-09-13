#Requires AutoHotkey v2.0
#SingleInstance Force

; —— 1) 禁用 CapsLock 默认功能，并定时校正 —— 
SetCapsLockState("AlwaysOff")     ; 启动时先关一次
SetTimer(KeepCapsOff, 500)        ; 定时校正，防止被系统/驱动改回
KeepCapsOff(*) {
    SetCapsLockState("AlwaysOff")
}

; —— 2) 全局变量 —— 
capslockTime        := 0
capslockPressed     := false
capslockUsedCombo   := false        ; ★新增：标记这次按压是否用于组合键
timerInterval       := 300          ; 你的原值（只做轮询守护，不触发行为）
SetTimer(CheckCapsLock, timerInterval)

; —— 3) CapsLock 按下：记录时间 + 立刻拉回 OFF —— 
*CapsLock::
{
    global capslockPressed, capslockTime, capslockUsedCombo
    capslockPressed   := true
    capslockUsedCombo := false
    capslockTime      := A_TickCount
    SetCapsLockState("AlwaysOff")   ; 防抖：按下瞬间也关掉
    return
}

; —— 4) CapsLock 松开：仅在“非组合且长按(≥300ms)”时切换输入法 —— 
*CapsLock up::
{
    global capslockPressed, capslockTime, capslockUsedCombo
    if (capslockPressed) {
        pressDuration := A_TickCount - capslockTime
        if (!capslockUsedCombo && pressDuration >= 300) {
            Send("^{Space}")        ; 按你的系统改成对应的输入法快捷键
        }
    }
    capslockPressed := false
    SetCapsLockState("AlwaysOff")   ; 再保险
    return
}

; —— 5) CapsLock 组合键 —— 
CapsLock & h::UseCapsCombo("{Left}")
CapsLock & j::UseCapsCombo("{Down}")
CapsLock & k::UseCapsCombo("{Up}")
CapsLock & l::UseCapsCombo("{Right}")

CapsLock & a::UseCapsCombo("^a")
CapsLock & b::UseCapsCombo("^b")
CapsLock & c::UseCapsCombo("^c")
CapsLock & d::UseCapsCombo("^d")
; ……可继续添加

UseCapsCombo(sendWhat) {
    global capslockUsedCombo
    capslockUsedCombo := true
    Send(sendWhat)
}

; —— 6) 轮询守护：只维持状态，不触发切换 —— 
CheckCapsLock() {
    global capslockPressed
    if (capslockPressed) {
        SetCapsLockState("AlwaysOff")
    }
}
