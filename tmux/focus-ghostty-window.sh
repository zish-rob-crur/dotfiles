#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--split" ]]; then
  osascript - "${2:-}" >/dev/null 2>&1 <<'APPLESCRIPT' || true
on run argv
  set dir to item 1 of argv
  if dir is "" then return
  tell application "Ghostty"
    if (count of windows) is 0 then return
    set w1 to front window
    set t1 to focused terminal of selected tab of w1
    set id1 to id of t1
    perform action ("goto_split:" & dir) on t1
    set id2 to id of (focused terminal of selected tab of front window)
    if id2 is not id1 then return
    if (count of windows) < 2 then return
  end tell

  set bestTitle to ""
  tell application "System Events"
    if not (exists process "Ghostty") then return
    tell process "Ghostty"
      if (count of windows) < 2 then return
      set frontWin to window 1
      set {fx, fy} to position of frontWin
      set {fw, fh} to size of frontWin
      set fcx to fx + (fw / 2)
      set fcy to fy + (fh / 2)

      set bestWin to missing value
      set bestDist to 999999999

      repeat with i from 2 to (count of windows)
        set w to window i
        try
          set {wx, wy} to position of w
          set {ww, wh} to size of w
          set wcx to wx + (ww / 2)
          set wcy to wy + (wh / 2)
          set dx to wcx - fcx
          set dy to wcy - fcy

          set matchesDir to false
          if dir is "up" and dy < -50 then set matchesDir to true
          if dir is "down" and dy > 50 then set matchesDir to true
          if dir is "left" and dx < -50 then set matchesDir to true
          if dir is "right" and dx > 50 then set matchesDir to true

          if matchesDir then
            set dist to (dx * dx) + (dy * dy)
            if dist < bestDist then
              set bestDist to dist
              set bestWin to w
              set bestTitle to (name of w) as text
            end if
          end if
        end try
      end repeat

      if bestWin is missing value then return
      try
        perform action "AXRaise" of bestWin
        set value of attribute "AXMain" of bestWin to true
        set value of attribute "AXFocused" of bestWin to true
      end try
    end tell
  end tell

  if bestTitle is not "" then
    tell application "Ghostty"
      repeat with gw in windows
        if (name of gw as text) is bestTitle then
          activate window gw
          try
            focus (focused terminal of selected tab of gw)
          end try
          exit repeat
        end if
      end repeat
    end tell
  end if
end run
APPLESCRIPT
  exit 0
fi

session_name=""
client_name=""
client_tty=""
window_id=""
pane_id=""

usage() {
  cat <<'EOF'
Usage: focus-ghostty-window.sh --session <session> --client <client> --tty <tty> --window-id <window-id> --pane-id <pane-id>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      session_name="${2:-}"
      shift 2
      ;;
    --client)
      client_name="${2:-}"
      shift 2
      ;;
    --tty)
      client_tty="${2:-}"
      shift 2
      ;;
    --window-id)
      window_id="${2:-}"
      shift 2
      ;;
    --pane-id)
      pane_id="${2:-}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$session_name" ]]; then
  usage >&2
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  exit 1
fi

focus_with_ghostty_applescript() {
  FOCUS_SESSION="$session_name" \
  FOCUS_CLIENT="$client_name" \
  FOCUS_TTY="$client_tty" \
  FOCUS_WINDOW_ID="$window_id" \
  FOCUS_PANE_ID="$pane_id" \
  osascript <<'APPLESCRIPT'
on startsWith(theText, prefixText)
  if prefixText is "" then return false
  if (length of theText) < (length of prefixText) then return false
  return (text 1 thru (length of prefixText) of theText) is prefixText
end startsWith

on titleOf(theObject)
  try
    return (name of theObject) as text
  on error
    return ""
  end try
end titleOf

set sessionName to system attribute "FOCUS_SESSION"
set windowId to system attribute "FOCUS_WINDOW_ID"
set paneId to system attribute "FOCUS_PANE_ID"

set windowPrefix to ""
set panePrefix to ""
set sessionPrefix to "Tmux[" & sessionName & " "

if windowId is not "" then
  set windowPrefix to "Tmux[" & sessionName & " " & windowId & " "
end if
if windowId is not "" and paneId is not "" then
  set panePrefix to "Tmux[" & sessionName & " " & windowId & " " & paneId & "]"
end if

tell application "Ghostty"
  set targetTerminal to missing value
  set targetWindow to missing value
  set sessionWindow to missing value
  set sessionTerminal to missing value

  repeat with w in windows
    set windowTitle to my titleOf(w)

    if panePrefix is not "" and my startsWith(windowTitle, panePrefix) then
      set targetWindow to w
      exit repeat
    end if
    if windowPrefix is not "" and my startsWith(windowTitle, windowPrefix) then
      set targetWindow to w
    end if
    if sessionWindow is missing value and my startsWith(windowTitle, sessionPrefix) then
      set sessionWindow to w
    end if

    repeat with t in terminals of w
      set terminalTitle to my titleOf(t)
      if panePrefix is not "" and my startsWith(terminalTitle, panePrefix) then
        set targetTerminal to t
        exit repeat
      end if
      if targetTerminal is missing value and windowPrefix is not "" and my startsWith(terminalTitle, windowPrefix) then
        set targetTerminal to t
      end if
      if sessionTerminal is missing value and my startsWith(terminalTitle, sessionPrefix) then
        set sessionTerminal to t
      end if
    end repeat

    if targetTerminal is not missing value then exit repeat
  end repeat

  if targetTerminal is not missing value then
    focus targetTerminal
    return
  end if
  if targetWindow is not missing value then
    activate window targetWindow
    return
  end if
  if sessionTerminal is not missing value then
    focus sessionTerminal
    return
  end if
  if sessionWindow is not missing value then
    activate window sessionWindow
    return
  end if
end tell

error "No matching Ghostty window"
APPLESCRIPT
}

focus_with_system_events() {
  FOCUS_SESSION="$session_name" \
  FOCUS_CLIENT="$client_name" \
  FOCUS_TTY="$client_tty" \
  FOCUS_WINDOW_ID="$window_id" \
  FOCUS_PANE_ID="$pane_id" \
  osascript <<'APPLESCRIPT'
on startsWith(theText, prefixText)
  if prefixText is "" then return false
  if (length of theText) < (length of prefixText) then return false
  return (text 1 thru (length of prefixText) of theText) is prefixText
end startsWith

on axTitleOf(theWindow)
  try
    return (value of attribute "AXTitle" of theWindow) as text
  on error
    try
      return (name of theWindow) as text
    on error
      return ""
    end try
  end try
end axTitleOf

set sessionName to system attribute "FOCUS_SESSION"
set windowId to system attribute "FOCUS_WINDOW_ID"
set paneId to system attribute "FOCUS_PANE_ID"
set windowPrefix to ""
set panePrefix to ""
set sessionPrefix to "Tmux[" & sessionName & " "

if windowId is not "" then
  set windowPrefix to "Tmux[" & sessionName & " " & windowId & " "
end if
if windowId is not "" and paneId is not "" then
  set panePrefix to "Tmux[" & sessionName & " " & windowId & " " & paneId & "]"
end if

tell application "Ghostty" to activate
delay 0.05

tell application "System Events"
  if not (exists process "Ghostty") then error "Ghostty process not found"

  tell process "Ghostty"
    set targetWindow to missing value
    set sessionWindow to missing value

    repeat with w in windows
      set windowTitle to my axTitleOf(w)
      if panePrefix is not "" and my startsWith(windowTitle, panePrefix) then
        set targetWindow to w
        exit repeat
      end if
      if windowPrefix is not "" and my startsWith(windowTitle, windowPrefix) then
        set targetWindow to w
      end if
      if sessionWindow is missing value and my startsWith(windowTitle, sessionPrefix) then
        set sessionWindow to w
      end if
    end repeat

    if targetWindow is missing value then set targetWindow to sessionWindow
    if targetWindow is missing value then error "No matching Ghostty window"

    try
      perform action "AXRaise" of targetWindow
    end try
    try
      set value of attribute "AXMain" of targetWindow to true
    end try
    try
      set value of attribute "AXFocused" of targetWindow to true
    end try
  end tell
end tell
APPLESCRIPT
}

focus_with_ghostty_applescript >/dev/null 2>&1 && exit 0
focus_with_system_events >/dev/null 2>&1 && exit 0
exit 1
