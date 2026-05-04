param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"

$configPath = Join-Path $PSScriptRoot "kanata.kbd"
$cacheDir = Join-Path $env:LOCALAPPDATA "kanata"
$cachedExe = Join-Path $cacheDir "kanata_windows_tty_winIOv2_x64.exe"

function Find-Kanata {
    $cmd = Get-Command kanata -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $cmd = Get-Command kanata.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $knownPaths = @(
        "C:\ProgramData\chocolatey\bin\kanata.exe",
        "C:\ProgramData\scoop\shims\kanata.exe",
        "$env:USERPROFILE\scoop\shims\kanata.exe",
        $cachedExe
    )

    foreach ($path in $knownPaths) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }

    $zipPaths = @(
        "C:\ProgramData\chocolatey\lib\kanata\tools\windows-binaries-x64.zip",
        "C:\ProgramData\scoop\apps\kanata\current\windows-binaries-x64.zip",
        "$env:USERPROFILE\scoop\apps\kanata\current\windows-binaries-x64.zip"
    )

    foreach ($zipPath in $zipPaths) {
        if (Test-Path -LiteralPath $zipPath) {
            New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
            Expand-Archive -LiteralPath $zipPath -DestinationPath $cacheDir -Force

            if (Test-Path -LiteralPath $cachedExe) {
                return $cachedExe
            }
        }
    }

    throw "kanata.exe was not found. Install Kanata with Chocolatey or Scoop, then run this script again."
}

if (!(Test-Path -LiteralPath $configPath)) {
    throw "Kanata config not found: $configPath"
}

$kanataExe = Find-Kanata
Write-Host "Using Kanata: $kanataExe"
Write-Host "Using config: $configPath"

if ($Check) {
    & $kanataExe --check --cfg $configPath
    exit $LASTEXITCODE
}

Write-Host "Keep this window open while Kanata is active. Run PowerShell as Administrator if input capture fails."
& $kanataExe --cfg $configPath
