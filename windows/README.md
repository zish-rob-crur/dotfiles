# Windows dotfiles

## Kanata HHKB layer

CapsLock mappings are handled by Kanata instead of AutoHotkey. This avoids the
unstable AHK behavior around using CapsLock as both a lock key and a prefix key.

The current setup:

- `windows/kanata.kbd`: Kanata config
- `windows/start_kanata.ps1`: starts Kanata with the repo config
- `windows/install_kanata_startup.ps1`: installs/removes the Windows startup task
- `windows/my.ahk`: intentionally no longer owns CapsLock

Validate the config:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start_kanata.ps1 -Check
```

Start Kanata:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start_kanata.ps1
```

Keep the Kanata window open while using the mapping. Run the PowerShell window
as Administrator if mappings do not apply inside elevated applications.

### Installation

Install Kanata first. Chocolatey provides the newer package on this machine:

```powershell
choco install kanata -y
```

Scoop is also usable if preferred:

```powershell
scoop install kanata
```

The Chocolatey package may leave the Windows binaries inside
`C:\ProgramData\chocolatey\lib\kanata\tools\windows-binaries-x64.zip` instead
of putting `kanata.exe` on `PATH`. `start_kanata.ps1` handles that case by
expanding the x64 binary to:

```text
%LOCALAPPDATA%\kanata\kanata_windows_tty_winIOv2_x64.exe
```

### Startup Task

Install startup task with administrator privileges:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\install_kanata_startup.ps1 -RunNow
```

This registers a Windows Scheduled Task named `Kanata HHKB Layer` that starts
Kanata at user logon with highest privileges. The command relaunches itself via
UAC if it is not already running as Administrator.

Remove the startup task:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\install_kanata_startup.ps1 -Remove
```

Useful checks:

```powershell
Get-ScheduledTask -TaskName 'Kanata HHKB Layer'
Get-ScheduledTaskInfo -TaskName 'Kanata HHKB Layer'
Get-Process -Name kanata* -ErrorAction SilentlyContinue
```

Restart Kanata after changing `kanata.kbd`:

```powershell
Stop-ScheduledTask -TaskName 'Kanata HHKB Layer' -ErrorAction SilentlyContinue
Get-Process -Name kanata* -ErrorAction SilentlyContinue | Stop-Process
Start-ScheduledTask -TaskName 'Kanata HHKB Layer'
```

### Mappings

- Tap `CapsLock`: `Shift`, for input method toggle
- `CapsLock+h/j/k/l`: left/down/up/right
- `CapsLock+c`: `Ctrl+c`
- Other letters, digits, `Tab`, `Backspace`, and `Delete` under `CapsLock` map
  to the corresponding `Ctrl+...` shortcut
- `F13` and `LCtrl` have the same behavior for machines that already remap
  CapsLock before Kanata sees it

Do not run the old AutoHotkey CapsLock mappings at the same time.

### Troubleshooting

If the scheduled task is `Running` but mappings do not work, first test whether
Kanata sees CapsLock as `caps`, `f13`, or `lctl`. This config maps all three to
the same HHKB layer because this machine previously had CapsLock remapped before
Kanata saw the input.

If mappings work in normal apps but not elevated apps, reinstall the startup task
or run Kanata from an Administrator PowerShell.

`Get-ScheduledTaskInfo` may show `LastTaskResult` as `267009`. That is Windows
Task Scheduler's running state (`0x41301`), not a failure.
