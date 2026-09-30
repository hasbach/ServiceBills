# ServiceBills on-prem helper library. Dot-source this file; it only defines functions.
# PowerShell 5.1 compatible. Pure functions first, side-effecting wrappers at the bottom.

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = 'Internal helpers; no interactive confirmation wanted.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseSingularNouns', '', Justification = 'Function names are a fixed contract with other scripts.')]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:SBScriptDir = $PSScriptRoot

function Get-SBRoot {
    if ($env:SERVICEBILLS_ROOT) { return $env:SERVICEBILLS_ROOT }
    return 'C:\ProgramData\ServiceBills'
}

function Compare-SBVersion {
    param([string]$a, [string]$b)
    $pattern = '^\d+\.\d+\.\d+$'
    foreach ($v in @($a, $b)) {
        if ($v -notmatch $pattern) { throw "Invalid version '$v' (expected MAJOR.MINOR.PATCH)" }
    }
    $pa = $a.Split('.') | ForEach-Object { [long]$_ }
    $pb = $b.Split('.') | ForEach-Object { [long]$_ }
    for ($i = 0; $i -lt 3; $i++) {
        if ($pa[$i] -lt $pb[$i]) { return -1 }
        if ($pa[$i] -gt $pb[$i]) { return 1 }
    }
    return 0
}

function Get-SBMachineId {
    param([string]$machineGuid)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($machineGuid))
    }
    finally { $sha.Dispose() }
    return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Read-SBMachineGuid {
    return [string](Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name 'MachineGuid').MachineGuid
}

function Get-SBRandomBytes {
    param([int]$Count)
    $bytes = New-Object byte[] $Count
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) }
    finally { $rng.Dispose() }
    return , $bytes
}

function New-SBSecret {
    param([int]$bytes = 32)
    $b64 = [Convert]::ToBase64String((Get-SBRandomBytes -Count $bytes))
    return $b64.TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-SBFernetKey {
    $b64 = [Convert]::ToBase64String((Get-SBRandomBytes -Count 32))
    return $b64.Replace('+', '-').Replace('/', '_')
}

function ConvertTo-SBIanaTimeZone {
    param([string]$windowsId)
    $path = Join-Path $script:SBScriptDir 'timezones.json'
    if ($windowsId -and (Test-Path $path)) {
        $map = Get-Content -Path $path -Raw | ConvertFrom-Json
        $prop = $map.PSObject.Properties[$windowsId]
        if ($prop) { return [string]$prop.Value }
    }
    return 'UTC'
}

function Merge-SBEnv {
    param(
        [AllowNull()][System.Collections.IDictionary]$existing,
        [AllowNull()][System.Collections.IDictionary]$generated
    )
    $refreshable = @('APP_BASE_URL', 'TZ', 'IMAGE_TAG')
    $result = [ordered]@{}
    if ($existing) {
        foreach ($k in $existing.Keys) { $result[[string]$k] = $existing[$k] }
    }
    if ($generated) {
        foreach ($k in $generated.Keys) {
            $key = [string]$k
            if (-not $result.Contains($key) -or ($refreshable -contains $key)) {
                $result[$key] = $generated[$k]
            }
        }
    }
    return $result
}

function ConvertFrom-SBEnvText {
    param([AllowEmptyString()][string]$text)
    $result = [ordered]@{}
    if (-not $text) { return $result }
    foreach ($line in ($text -split "\r?\n")) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $idx = $t.IndexOf('=')
        if ($idx -lt 1) { continue }
        $result[$t.Substring(0, $idx).Trim()] = $t.Substring($idx + 1)
    }
    return $result
}

function ConvertTo-SBEnvText {
    param([System.Collections.IDictionary]$Values)
    $keys = @($Values.Keys | ForEach-Object { [string]$_ })
    if (-not ($Values -is [System.Collections.Specialized.OrderedDictionary])) {
        $keys = @($keys | Sort-Object)
    }
    $sb = New-Object Text.StringBuilder
    foreach ($k in $keys) {
        [void]$sb.Append($k).Append('=').Append([string]$Values[$k]).Append("`n")
    }
    return $sb.ToString()
}

function Get-SBLanIp {
    param([AllowNull()][object[]]$addresses)
    $skip = 'vEthernet|WSL|Docker|Loopback|VirtualBox|VMware'
    foreach ($a in @($addresses)) {
        if ($null -eq $a) { continue }
        if ($a.InterfaceAlias -match $skip) { continue }
        $ip = [string]$a.IPAddress
        if ($ip -notmatch '^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$') { continue }
        $o1 = [int]$Matches[1]
        $o2 = [int]$Matches[2]
        if ($o1 -eq 10 -or ($o1 -eq 192 -and $o2 -eq 168) -or ($o1 -eq 172 -and $o2 -ge 16 -and $o2 -le 31)) {
            return $ip
        }
    }
    return 'localhost'
}

function Get-SBLanIpLive {
    return Get-SBLanIp @(Get-NetIPAddress -AddressFamily IPv4)
}

function Get-SBBackupsToDelete {
    param([string[]]$names, [int]$keep = 14)
    $matching = @($names | Where-Object { $_ -match '^servicebills-\d{8}-\d{4}\.zip$' })
    $sorted = @($matching | Sort-Object -Descending)
    if ($sorted.Count -le $keep) { return @() }
    return @($sorted | Select-Object -Skip $keep)
}

function Read-SBState {
    param([string]$path)
    $state = @{ current = $null; previous = $null; last_update = $null }
    if (-not (Test-Path $path)) { return $state }
    $raw = [IO.File]::ReadAllText($path)
    if (-not $raw.Trim()) { return $state }
    $obj = $raw | ConvertFrom-Json
    foreach ($name in @('current', 'previous')) {
        $prop = $obj.PSObject.Properties[$name]
        if ($prop) { $state[$name] = $prop.Value }
    }
    $lu = $obj.PSObject.Properties['last_update']
    if ($lu -and $null -ne $lu.Value) {
        $h = @{}
        foreach ($p in $lu.Value.PSObject.Properties) { $h[$p.Name] = $p.Value }
        $state['last_update'] = $h
    }
    return $state
}

function Write-SBState {
    param([string]$path, [hashtable]$state)
    $dir = Split-Path -Parent $path
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $json = $state | ConvertTo-Json -Depth 5
    $tmp = "$path.tmp"
    [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding $false))
    Move-Item -Path $tmp -Destination $path -Force
}

function New-SBUpdateResult {
    param([string]$status, [string]$target, [string]$message)
    return @{
        status  = $status
        target  = $target
        at      = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
        message = $message
    }
}

# ---- Side-effecting helpers -------------------------------------------------

function Write-SBLog {
    param([string]$name, [string]$message)
    $dir = Join-Path (Get-SBRoot) 'logs'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $file = Join-Path $dir ('{0}-{1}.log' -f $name, (Get-Date).ToString('yyyyMMdd'))
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
    [IO.File]::AppendAllText($file, "[$stamp] $message`r`n", (New-Object Text.UTF8Encoding $false))
}

function Test-SBVirtualization {
    $cpu = @(Get-CimInstance Win32_Processor | Where-Object { $_.VirtualizationFirmwareEnabled })
    if ($cpu.Count -gt 0) { return $true }
    $cs = Get-CimInstance Win32_ComputerSystem
    return [bool]$cs.HypervisorPresent
}

function Invoke-SBDockerRaw {
    param([string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker @Arguments 2>&1 | ForEach-Object { "$_" }
    }
    finally { $ErrorActionPreference = $prev }
}

function Invoke-SBDocker {
    param([string[]]$Arguments)
    $out = @(Invoke-SBDockerRaw -Arguments $Arguments)
    if ($global:LASTEXITCODE -ne 0) {
        throw ("docker {0} failed (exit {1}): {2}" -f ($Arguments -join ' '), $global:LASTEXITCODE, ($out -join "`n"))
    }
    return $out
}

function Test-SBDockerEngine {
    try {
        [void](Invoke-SBDockerRaw -Arguments @('info'))
        return ($global:LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Test-SBHealthOnce {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    }
    catch { return $false }
}

function Wait-SBHealth {
    param([int]$Seconds = 300, [string]$Url = 'http://localhost:8000/api/health')
    $attempts = [Math]::Max(1, [int][Math]::Ceiling($Seconds / 5.0))
    for ($i = 0; $i -lt $attempts; $i++) {
        if (Test-SBHealthOnce -Url $Url) { return $true }
        if ($i -lt ($attempts - 1)) { Start-Sleep -Seconds 5 }
    }
    return $false
}
