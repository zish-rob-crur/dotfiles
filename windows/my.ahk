#Requires AutoHotkey v2.0

; 禁用 CapsLock 默认功能
SetCapsLockState("AlwaysOff")  ; 禁用 CapsLock 的默认行为

; 定义全局变量
capslockTime := 0
capslockPressed := false

; 设置检测时间间隔
timerInterval := 300  ; 300ms

; 如果单独按下 CapsLock 进行输入法切换
SetTimer(CheckCapsLock, timerInterval)

; CapsLock 按下
CapsLock::
{
    global capslockPressed, capslockTime  ; 确保访问全局变量
    capslockPressed := true
    capslockTime := A_TickCount  ; 记录按下时间
    return
}

; CapsLock 松开
CapsLock up::
{
    global capslockPressed  ; 确保访问全局变量
    if (capslockPressed) {
        ; 计算按下时间
        pressDuration := A_TickCount - capslockTime

        if (pressDuration < 300) {
            ; 如果按下时间小于300ms，则认为是组合键，交给其它键处理
            return
        } else {
            ; 如果按下时间超过300ms，则切换输入法
            Send("^{Space}")  ; 这里以 Ctrl+Space 为输入法切换快捷键，具体根据你的系统来调整
        }
    }
    capslockPressed := false
    return
}

; CapsLock + h/j/k/l 当作方向键
CapsLock & h::Send("{Left}")
CapsLock & j::Send("{Down}")
CapsLock & k::Send("{Up}")
CapsLock & l::Send("{Right}")

; 其他按键作为 Ctrl（如 CapsLock + a 等）
CapsLock & a::Send("^a")
CapsLock & b::Send("^b")
CapsLock & c::Send("^c")
CapsLock & d::Send("^d")
; 可以继续添加更多按键的 Ctrl 功能，类似

CheckCapsLock()
{
    global capslockPressed, capslockTime  ; 确保访问全局变量
    ; 定时检查 CapsLock 按下的时长
    if (capslockPressed) {
        pressDuration := A_TickCount - capslockTime
        if (pressDuration >= 80) {
            ; 如果 CapsLock 按下超过80ms，认为是单独按下，执行输入法切换
            Send("^{Space}")
            capslockPressed := false  ; 只执行一次
        }
    }
}
