[CmdletBinding()]
param(
    [string]$EnvPath
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        throw "Please run this script from an elevated PowerShell session."
    }
}

function Read-DotEnv {
    param([string]$Path)

    $result = @{}
    if (-not (Test-Path $Path)) {
        return $result
    }

    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $pair = $trimmed -split "=", 2
        if ($pair.Count -lt 2) {
            continue
        }

        $key = $pair[0].Trim()
        $value = $pair[1].Trim().Trim("'`"")
        if ($key) {
            $result[$key] = $value
        }
    }

    return $result
}

Assert-Admin

if (-not $EnvPath) {
    $EnvPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
}

$envVars = Read-DotEnv -Path $EnvPath

$openwrtGateway = $envVars["OPENWRT_GATEWAY"]
if (-not $openwrtGateway) {
    $openwrtGateway = "192.168.31.3"
}

$directGateway = $envVars["DIRECT_GATEWAY"]
if (-not $directGateway) {
    $directGateway = "192.168.31.1"
}

$openwrtDnsRaw = $envVars["OPENWRT_DNS"]
if (-not $openwrtDnsRaw) {
    $openwrtDnsRaw = $openwrtGateway
}
$openwrtDnsList = $openwrtDnsRaw -split "[,;]" | ForEach-Object { $_.Trim() } | Where-Object { $_ }

$directDnsRaw = $envVars["DIRECT_DNS"]
if (-not $directDnsRaw) {
    $directDnsRaw = $directGateway
}
$directDnsList = $directDnsRaw -split "[,;]" | ForEach-Object { $_.Trim() } | Where-Object { $_ }

$interfaceAlias = $envVars["GATEWAY_INTERFACE"]

function Get-PrimaryInterface {
    param([string]$Alias)

    if ($Alias) {
        $chosen = Get-NetIPConfiguration | Where-Object { $_.InterfaceAlias -eq $Alias }
        if (-not $chosen) {
            throw "Interface '$Alias' not found or missing IPv4 configuration."
        }
        if (-not $chosen.IPv4DefaultGateway) {
            throw "Interface '$Alias' has no IPv4 default gateway. Connect it or choose another interface."
        }
        return $chosen
    }

    $withGatewayUp = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq "Up" } |
        Sort-Object -Property InterfaceMetric |
        Select-Object -First 1

    if ($withGatewayUp) {
        return $withGatewayUp
    }

    $fallback = Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -ne $null } |
        Sort-Object -Property InterfaceMetric |
        Select-Object -First 1

    if (-not $fallback) {
        throw "No interface with an IPv4 default gateway found. Set GATEWAY_INTERFACE in the .env file."
    }

    return $fallback
}

try {
    Write-Host "--- ipconfig (before) ---"
    ipconfig

    $interfaceConfig = Get-PrimaryInterface -Alias $interfaceAlias
    $ifIndex = $interfaceConfig.InterfaceIndex
    $ipInterface = Get-NetIPInterface -InterfaceIndex $ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
    $interfaceMetric = $ipInterface.InterfaceMetric

    $currentGateway = ($interfaceConfig.IPv4DefaultGateway | Select-Object -First 1).NextHop
    if (-not $currentGateway) {
        throw "Interface $($interfaceConfig.InterfaceAlias) has no IPv4 default gateway."
    }

    $displayMetric = $interfaceMetric
    if (-not $displayMetric) { $displayMetric = "N/A" }
    Write-Host "Using interface '$($interfaceConfig.InterfaceAlias)' (metric $displayMetric) with current gateway $currentGateway."

    $targetGateway = $null
    $targetLabel = $null
    $targetDns = @()
    if ($currentGateway -eq $openwrtGateway) {
        $targetGateway = $directGateway
        $targetLabel = "direct"
        $targetDns = $directDnsList
    } elseif ($currentGateway -eq $directGateway) {
        $targetGateway = $openwrtGateway
        $targetLabel = "openwrt"
        $targetDns = $openwrtDnsList
    } else {
        Write-Host "Current gateway $currentGateway does not match OPENWRT_GATEWAY ($openwrtGateway) or DIRECT_GATEWAY ($directGateway). No change made."
        return
    }

    $existingRoutes = Get-NetRoute -InterfaceIndex $ifIndex -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 -ErrorAction SilentlyContinue
    foreach ($route in $existingRoutes) {
        Remove-NetRoute -InterfaceIndex $route.InterfaceIndex -DestinationPrefix $route.DestinationPrefix -NextHop $route.NextHop -RouteMetric $route.RouteMetric -Confirm:$false -ErrorAction SilentlyContinue
        route delete 0.0.0.0 mask 0.0.0.0 $route.NextHop if $ifIndex | Out-Null
    }

    $routeMetric = ($existingRoutes | Sort-Object -Property RouteMetric | Select-Object -First 1).RouteMetric
    if (-not $routeMetric) {
        $routeMetric = $interfaceMetric
    }
    if (-not $routeMetric) {
        $routeMetric = 10
    }

    # Use route.exe for persistent route to avoid PolicyStore issues on some adapters
    route -p add 0.0.0.0 mask 0.0.0.0 $targetGateway metric $routeMetric if $ifIndex | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to add persistent route to $targetGateway (ifIndex $ifIndex, metric $routeMetric)."
    }

    if ($targetDns.Count -gt 0) {
        Set-DnsClientServerAddress -InterfaceIndex $ifIndex -ServerAddresses $targetDns -ErrorAction Stop
        Write-Host "DNS set to: $($targetDns -join ', ')."
    }

    Write-Host "Switched $($interfaceConfig.InterfaceAlias) gateway from $currentGateway to $targetGateway ($targetLabel) with metric $routeMetric."

    Write-Host "--- ipconfig (after) ---"
    ipconfig
}
finally {
    Write-Host ""
    Write-Host "Press Enter to exit..."
    [void][System.Console]::ReadLine()
}
