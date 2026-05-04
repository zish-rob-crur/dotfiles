param(
    [switch]$RunNow,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$taskName = "Kanata HHKB Layer"
$scriptPath = Join-Path $PSScriptRoot "start_kanata.ps1"
$powershellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-SelfElevated {
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )

    if ($RunNow) {
        $argsList += "-RunNow"
    }

    if ($Remove) {
        $argsList += "-Remove"
    }

    $process = Start-Process -FilePath $powershellPath -ArgumentList $argsList -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

if (!(Test-Path -LiteralPath $scriptPath)) {
    throw "Kanata start script not found: $scriptPath"
}

if (!(Test-IsAdmin)) {
    Write-Host "Requesting administrator privileges to manage the Kanata startup task..."
    Invoke-SelfElevated
    exit $LASTEXITCODE
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $taskName"
    exit 0
}

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

$action = New-ScheduledTaskAction `
    -Execute $powershellPath `
    -Argument $actionArgs `
    -WorkingDirectory $PSScriptRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Starts the Kanata HHKB CapsLock layer at user logon." `
    -Force | Out-Null

Write-Host "Registered scheduled task: $taskName"

if ($RunNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started scheduled task: $taskName"
}
