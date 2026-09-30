# ServiceBills on-prem helper library. Dot-source this file; it only defines functions.
# PowerShell 5.1 compatible. Pure functions first, side-effecting wrappers at the bottom.

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = 'Internal helpers; no interactive confirmation wanted.')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseSingularNouns', '', Justification = 'Function names are a fixed contract with other scripts.')]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:SBScriptDir = $PSScriptRoot

# Windows PowerShell 5.1 may default to old TLS versions; OR in TLS 1.2 without dropping what is enabled.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}
catch { $null = $_ }

function Get-SBRoot {
    if ($env:SERVICEBILLS_ROOT) { return $env:SERVICEBILLS_ROOT }
    return 'C:\ProgramData\ServiceBills'
}

function Get-SBStatePath {
    # The state file lives in its own folder so compose can bind-mount the directory (not a single file).
    return (Join-Path (Join-Path (Get-SBRoot) 'state') 'state.json')
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
    $dir = Join-Path (Get-SBRoot) 'scripts'
    if (Test-Path variable:script:SBScriptDir) { $dir = $script:SBScriptDir }
    $path = Join-Path $dir 'timezones.json'
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

function Get-SBPrivateIPv4 {
    # Returns the IPv4 address when it is in a private range, otherwise $null.
    param([string]$ip)
    if ($ip -notmatch '^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$') { return $null }
    $o1 = [int]$Matches[1]
    $o2 = [int]$Matches[2]
    if ($o1 -eq 10 -or ($o1 -eq 192 -and $o2 -eq 168) -or ($o1 -eq 172 -and $o2 -ge 16 -and $o2 -le 31)) {
        return $ip
    }
    return $null
}

function Get-SBLanIp {
    # Prefers the private IPv4 of the default-route interface (when PreferredInterfaceIndex is given),
    # then the first private address on a non-virtual adapter, then 'localhost'.
    param([AllowNull()][object[]]$addresses, [int]$PreferredInterfaceIndex = 0)
    $skip = 'vEthernet|WSL|Docker|Loopback|VirtualBox|VMware'
    if ($PreferredInterfaceIndex -gt 0) {
        foreach ($a in @($addresses)) {
            if ($null -eq $a) { continue }
            $idx = $a.PSObject.Properties['InterfaceIndex']
            if (-not $idx -or [int]$idx.Value -ne $PreferredInterfaceIndex) { continue }
            $ip = Get-SBPrivateIPv4 ([string]$a.IPAddress)
            if ($ip) { return $ip }
        }
    }
    foreach ($a in @($addresses)) {
        if ($null -eq $a) { continue }
        if ($a.InterfaceAlias -match $skip) { continue }
        $ip = Get-SBPrivateIPv4 ([string]$a.IPAddress)
        if ($ip) { return $ip }
    }
    return 'localhost'
}

function Get-SBDefaultRouteInterfaceIndex {
    try {
        $route = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop | Sort-Object RouteMetric) | Select-Object -First 1
        if ($route) { return [int]$route.InterfaceIndex }
    }
    catch { $null = $_ }
    return 0
}

function Get-SBLanIpLive {
    return Get-SBLanIp -addresses @(Get-NetIPAddress -AddressFamily IPv4) -PreferredInterfaceIndex (Get-SBDefaultRouteInterfaceIndex)
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

# ---- Installer / launcher wrappers (Task 6) -------------------------------

function Invoke-SBDockerStdin {
    # Runs docker with $InputText piped to stdin (used for `docker login --password-stdin`).
    # The text never appears on the command line.
    param([string[]]$Arguments, [string]$InputText)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $InputText | & docker @Arguments 2>&1 | ForEach-Object { "$_" }
    }
    finally { $ErrorActionPreference = $prev }
}

function Get-SBComposeArguments {
    param([string[]]$Arguments)
    $root = Get-SBRoot
    return @('compose', '-f', (Join-Path $root 'compose.yml'), '--env-file', (Join-Path $root '.env')) + @($Arguments)
}

function Invoke-SBCompose {
    param([string[]]$Arguments)
    return Invoke-SBDocker -Arguments (Get-SBComposeArguments -Arguments $Arguments)
}

function Start-SBDockerDesktop {
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue) -and (Test-Path $exe)) {
        Start-Process -FilePath $exe -WindowStyle Hidden
    }
}

function Wait-SBDockerEngine {
    param([int]$Seconds = 180)
    $attempts = [Math]::Max(1, [int][Math]::Ceiling($Seconds / 5.0))
    for ($i = 0; $i -lt $attempts; $i++) {
        if (Test-SBDockerEngine) { return }
        if ($i -eq 0) { Start-SBDockerDesktop }
        if ($i -lt ($attempts - 1)) { Start-Sleep -Seconds 5 }
    }
    throw "Docker did not become ready within $Seconds seconds."
}

function Add-SBFirewallRule {
    param([string]$DisplayName = 'ServiceBills (TCP 8000)', [int]$Port = 8000)
    if (Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue) { return }
    New-NetFirewallRule -DisplayName $DisplayName -Direction Inbound -Protocol TCP -LocalPort $Port -Profile Private -Action Allow | Out-Null
}

function Set-SBScheduledTask {
    # Daily task, current user, only when logged on, highest privileges. Replaces an existing task.
    param([string]$Name, [string]$At, [string]$ScriptName, [string]$ScriptArguments = '')
    $vbs = Join-Path (Join-Path (Get-SBRoot) 'scripts') 'run-hidden.vbs'
    $arg = ('"{0}" {1} {2}' -f $vbs, $ScriptName, $ScriptArguments).Trim()
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    # Laptops: run when missed (asleep at 01:30), on battery, and allow up to 2 hours.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Remove-SBScheduledTask {
    param([string]$Name)
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
}

function Show-SBMessage {
    # Returns $true when the user answered Yes (only meaningful with -YesNo).
    param([string]$Text, [string]$Title = 'ServiceBills', [switch]$YesNo)
    Add-Type -AssemblyName System.Windows.Forms
    $buttons = if ($YesNo) { [Windows.Forms.MessageBoxButtons]::YesNo } else { [Windows.Forms.MessageBoxButtons]::OK }
    $r = [Windows.Forms.MessageBox]::Show($Text, $Title, $buttons, [Windows.Forms.MessageBoxIcon]::Information)
    return ($r -eq [Windows.Forms.DialogResult]::Yes)
}

# ---- Backup / restore / update / uninstall (Task 7) ------------------------

function Remove-SBFirewallRule {
    param([string]$DisplayName = 'ServiceBills (TCP 8000)')
    if (Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -DisplayName $DisplayName
    }
}

function Get-SBDefaultBackupDir {
    return (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ServiceBills Backups')
}

function Get-SBSettings {
    $path = Join-Path (Get-SBRoot) 'settings.json'
    $s = @{ backup_dir = ''; auto_update = $true }
    if (Test-Path $path) {
        $raw = [IO.File]::ReadAllText($path)
        if ($raw.Trim()) {
            $obj = $raw | ConvertFrom-Json
            foreach ($p in $obj.PSObject.Properties) { $s[$p.Name] = $p.Value }
        }
    }
    return $s
}

function Resolve-SBBackupDir {
    # Configured backup folder, or the default when none was chosen at install time.
    $dir = [string](Get-SBSettings)['backup_dir']
    if (-not $dir) { $dir = Get-SBDefaultBackupDir }
    return $dir
}

function Get-SBBackupDate {
    # 'servicebills-20260101-0130.zip' -> [datetime], or $null when the name does not match.
    param([string]$Name)
    if ($Name -match '^servicebills-(\d{8})-(\d{4})\.zip$') {
        $stamp = $Matches[1] + $Matches[2]
        return [datetime]::ParseExact($stamp, 'yyyyMMddHHmm', [Globalization.CultureInfo]::InvariantCulture)
    }
    return $null
}

function New-SBZip {
    param([string]$SourceDir, [string]$ZipPath)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $ZipPath) { Remove-Item -Path $ZipPath -Force }
    [IO.Compression.ZipFile]::CreateFromDirectory($SourceDir, $ZipPath)
}

function Expand-SBZip {
    param([string]$ZipPath, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $Destination)
}

function Get-SBKeyNames {
    return @('FERNET_KEY', 'JWT_SECRET_KEY')
}

function Select-SBKeys {
    # Only the two encryption/signing keys out of an env map.
    param([AllowNull()][System.Collections.IDictionary]$EnvMap)
    $keys = [ordered]@{}
    if (-not $EnvMap) { return $keys }
    foreach ($k in (Get-SBKeyNames)) {
        if ($EnvMap.Contains($k) -and $EnvMap[$k]) { $keys[$k] = $EnvMap[$k] }
    }
    return $keys
}

function Read-SBBackupKeys {
    # Returns the env.keys map stored in a backup zip, or $null when the zip has none.
    param([string]$Zip)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $entry = $archive.Entries | Where-Object { $_.FullName -eq 'env.keys' } | Select-Object -First 1
        if (-not $entry) { return $null }
        $reader = New-Object IO.StreamReader($entry.Open(), (New-Object Text.UTF8Encoding $false))
        try { $text = $reader.ReadToEnd() }
        finally { $reader.Dispose() }
        return (Select-SBKeys (ConvertFrom-SBEnvText $text))
    }
    finally { $archive.Dispose() }
}

function Test-SBKeysDiffer {
    # True when the backup carries keys that differ from the ones currently in .env.
    param([AllowNull()][System.Collections.IDictionary]$BackupKeys, [AllowNull()][System.Collections.IDictionary]$CurrentEnv)
    if (-not $BackupKeys -or $BackupKeys.Count -eq 0) { return $false }
    foreach ($k in $BackupKeys.Keys) {
        $cur = $null
        if ($CurrentEnv -and $CurrentEnv.Contains($k)) { $cur = [string]$CurrentEnv[$k] }
        if ($cur -ne [string]$BackupKeys[$k]) { return $true }
    }
    return $false
}

function Set-SBEnvKeys {
    # Merges keys into .env in place (other entries preserved; the file's ACL is kept).
    param([System.Collections.IDictionary]$Keys)
    $envPath = Join-Path (Get-SBRoot) '.env'
    $existing = [ordered]@{}
    if (Test-Path $envPath) { $existing = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($envPath)) }
    foreach ($k in $Keys.Keys) { $existing[[string]$k] = $Keys[$k] }
    [IO.File]::WriteAllText($envPath, (ConvertTo-SBEnvText -Values $existing), (New-Object Text.UTF8Encoding $false))
}

function New-SBBackup {
    # Dumps the database and copies uploads (plus the two keys) from the running containers into a zip.
    # Returns the zip path.
    param([string]$Destination)
    if (-not $Destination) { $Destination = Resolve-SBBackupDir }
    if (-not (Test-Path $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('sb-backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    try {
        # Binary data never goes through a pipe: dump inside the container, then `docker compose cp`.
        [void](Invoke-SBCompose -Arguments @('exec', '-T', 'db', 'sh', '-c', 'pg_dump -U servicebills -Fc -f /tmp/sb.dump servicebills'))
        [void](Invoke-SBCompose -Arguments @('cp', 'db:/tmp/sb.dump', (Join-Path $tmp 'db.dump')))
        [void](Invoke-SBCompose -Arguments @('exec', '-T', 'db', 'rm', '-f', '/tmp/sb.dump'))
        [void](Invoke-SBCompose -Arguments @('cp', 'web:/app/uploads', (Join-Path $tmp 'uploads')))
        $envPath = Join-Path (Get-SBRoot) '.env'
        if (Test-Path $envPath) {
            $keys = Select-SBKeys (ConvertFrom-SBEnvText ([IO.File]::ReadAllText($envPath)))
            [IO.File]::WriteAllText((Join-Path $tmp 'env.keys'), (ConvertTo-SBEnvText -Values $keys), (New-Object Text.UTF8Encoding $false))
        }
        $zip = Join-Path $Destination ('servicebills-{0}.zip' -f (Get-Date).ToString('yyyyMMdd-HHmm'))
        New-SBZip -SourceDir $tmp -ZipPath $zip
    }
    finally {
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    $names = @(Get-ChildItem -Path $Destination -Filter 'servicebills-*.zip' | ForEach-Object { $_.Name })
    foreach ($old in @(Get-SBBackupsToDelete -names $names)) {
        Remove-Item -Path (Join-Path $Destination $old) -Force
    }
    return $zip
}

function Restore-SBBackup {
    # Non-interactive restore core shared by restore.ps1 and the update rollback.
    # The app containers are stopped first (they hold DB connections); the db container stays up. The
    # database is dropped and recreated, then pg_restore runs with --exit-on-error so a bad restore is
    # never silent. Uploads are cleared with a one-off `run` of the web image and refilled with `cp`.
    # The app containers are ALWAYS started again (finally), even when a step failed; the failure is
    # still thrown. With -RestoreKeys the backup's FERNET_KEY / JWT_SECRET_KEY are merged into .env and
    # the app containers recreated so they pick them up. Returns $true when the stack is healthy again.
    param([string]$Zip, [switch]$RestoreKeys)
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('sb-restore-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $completed = $false
    $keysRestored = $false
    try {
        Expand-SBZip -ZipPath $Zip -Destination $tmp
        if ($RestoreKeys) {
            $keysFile = Join-Path $tmp 'env.keys'
            if (Test-Path $keysFile) {
                Set-SBEnvKeys -Keys (Select-SBKeys (ConvertFrom-SBEnvText ([IO.File]::ReadAllText($keysFile))))
                $keysRestored = $true
            }
        }
        [void](Invoke-SBCompose -Arguments @('stop', 'web', 'scheduler'))
        [void](Invoke-SBCompose -Arguments @('up', '-d', 'db'))
        [void](Invoke-SBCompose -Arguments @('cp', (Join-Path $tmp 'db.dump'), 'db:/tmp/sb.dump'))
        [void](Invoke-SBCompose -Arguments @('exec', '-T', 'db', 'sh', '-c', 'dropdb -U servicebills --force --if-exists servicebills && createdb -U servicebills servicebills'))
        [void](Invoke-SBCompose -Arguments @('exec', '-T', 'db', 'sh', '-c', 'pg_restore -U servicebills -d servicebills --no-owner --exit-on-error /tmp/sb.dump'))
        [void](Invoke-SBCompose -Arguments @('exec', '-T', 'db', 'rm', '-f', '/tmp/sb.dump'))
        [void](Invoke-SBCompose -Arguments @('run', '--rm', '--no-deps', '--entrypoint', 'sh', 'web', '-c', 'rm -rf /app/uploads/* /app/uploads/.[!.]*'))
        if (Test-Path (Join-Path $tmp 'uploads')) {
            [void](Invoke-SBCompose -Arguments @('cp', ((Join-Path $tmp 'uploads') + '\.'), 'web:/app/uploads'))
        }
        $completed = $true
    }
    finally {
        try {
            if ($keysRestored) { [void](Invoke-SBCompose -Arguments @('up', '-d', '--force-recreate', 'web', 'scheduler')) }
            else { [void](Invoke-SBCompose -Arguments @('up', '-d')) }
        }
        catch {
            # Only surface this when nothing else is already failing.
            if ($completed) { throw }
        }
        Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    return [bool](Wait-SBHealth -Seconds 300)
}

# ---- Final-review additions -------------------------------------------------

function Invoke-SBIcacls {
    # Thin wrapper around icacls.exe so tests can mock it.
    param([string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& icacls.exe @Arguments 2>&1 | ForEach-Object { "$_" }) }
    finally { $ErrorActionPreference = $prev }
    if ($global:LASTEXITCODE -ne 0) {
        throw ("icacls {0} failed (exit {1}): {2}" -f ($Arguments -join ' '), $global:LASTEXITCODE, ($out -join ' '))
    }
}

function Get-SBCurrentUserSid {
    return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}

function Grant-SBRootAccess {
    # The installer runs elevated but the day-to-day scripts run as the interactive user, who must be
    # able to write logs, state and settings under the (ProgramData) root. With UAC elevation the
    # installing user and the interactive user are the same account.
    param([string]$Root)
    $sid = Get-SBCurrentUserSid
    Invoke-SBIcacls -Arguments @($Root, '/grant', ('*{0}:(OI)(CI)M' -f $sid), '/T', '/C')
}

function Protect-SBEnvFile {
    # .env holds secrets: drop inherited access, keep only SYSTEM, Administrators and the installing user.
    param([string]$EnvPath)
    $sid = Get-SBCurrentUserSid
    Invoke-SBIcacls -Arguments @($EnvPath, '/inheritance:r', '/grant:r', '*S-1-5-18:F', '*S-1-5-32-544:F', ('*{0}:M' -f $sid))
}

function Resolve-SBImageTag {
    # Never lower an installed version: a SemVer tag on disk beats an older SemVer or an unknown
    # ('latest') requested tag. Returns the tag to write to .env.
    param([AllowEmptyString()][string]$Existing, [AllowEmptyString()][string]$Requested)
    $semver = '^\d+\.\d+\.\d+$'
    if (-not $Existing) { return $Requested }
    if ($Existing -notmatch $semver) { return $Requested }
    if ($Requested -notmatch $semver) { return $Existing }
    if ((Compare-SBVersion $Existing $Requested) -gt 0) { return $Existing }
    return $Requested
}

function Test-SBWebRunning {
    # True when the web container of an existing install is currently running.
    if (-not (Test-SBDockerEngine)) { return $false }
    try {
        $out = @(Invoke-SBCompose -Arguments @('ps', '--status', 'running', '--services'))
        return ($out -contains 'web')
    }
    catch { return $false }
}

function Set-SBRunKey {
    param([string]$Name, [string]$Value)
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    Set-ItemProperty -Path $key -Name $Name -Value $Value
}

function Set-SBDockerAutoStart {
    # Docker Desktop must start when the user logs in, otherwise ServiceBills is down after a reboot.
    # Newer Docker Desktop keeps settings in settings-store.json, older ones in settings.json (JSON,
    # key AutoStart). When neither file exists yet (Docker never ran) fall back to an HKCU Run entry.
    # Returns which mechanism was used: 'settings-store.json', 'settings.json' or 'run-key'.
    param(
        [string]$AppData = $env:APPDATA,
        [string]$ExePath = ''
    )
    if (-not $ExePath) { $ExePath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe' }
    foreach ($name in @('settings-store.json', 'settings.json')) {
        $path = Join-Path (Join-Path $AppData 'Docker') $name
        if (-not (Test-Path $path)) { continue }
        try {
            $raw = [IO.File]::ReadAllText($path)
            $obj = if ($raw.Trim()) { $raw | ConvertFrom-Json } else { New-Object psobject }
            $prop = $obj.PSObject.Properties | Where-Object { $_.Name -ieq 'AutoStart' } | Select-Object -First 1
            if ($prop) { $prop.Value = $true }
            else { $obj | Add-Member -NotePropertyName 'AutoStart' -NotePropertyValue $true }
            [IO.File]::WriteAllText($path, ($obj | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
            return $name
        }
        catch { continue }
    }
    Set-SBRunKey -Name 'Docker Desktop' -Value ('"{0}"' -f $ExePath)
    return 'run-key'
}

function Select-SBBackupFile {
    # Shows the backup picker; returns the chosen FileInfo or $null.
    param([string]$Dir)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $files = @(Get-ChildItem -Path $Dir -Filter 'servicebills-*.zip' -ErrorAction SilentlyContinue |
            Where-Object { $null -ne (Get-SBBackupDate $_.Name) } | Sort-Object Name -Descending)
    if ($files.Count -eq 0) {
        [void](Show-SBMessage -Text "No backups were found in $Dir")
        return $null
    }
    $form = New-Object Windows.Forms.Form
    $form.Text = 'ServiceBills - Restore'
    $form.StartPosition = 'CenterScreen'
    $form.Size = New-Object Drawing.Size(420, 380)
    $form.TopMost = $true
    $label = New-Object Windows.Forms.Label
    $label.Text = 'Choose the backup to restore:'
    $label.Location = New-Object Drawing.Point(12, 10)
    $label.AutoSize = $true
    $list = New-Object Windows.Forms.ListBox
    $list.Location = New-Object Drawing.Point(12, 34)
    $list.Size = New-Object Drawing.Size(380, 250)
    foreach ($f in $files) {
        [void]$list.Items.Add((Get-SBBackupDate $f.Name).ToString('yyyy-MM-dd HH:mm'))
    }
    $list.SelectedIndex = 0
    $ok = New-Object Windows.Forms.Button
    $ok.Text = 'Restore'
    $ok.Location = New-Object Drawing.Point(226, 300)
    $ok.DialogResult = [Windows.Forms.DialogResult]::OK
    $cancel = New-Object Windows.Forms.Button
    $cancel.Text = 'Cancel'
    $cancel.Location = New-Object Drawing.Point(312, 300)
    $cancel.DialogResult = [Windows.Forms.DialogResult]::Cancel
    $form.AcceptButton = $ok
    $form.CancelButton = $cancel
    $form.Controls.AddRange(@($label, $list, $ok, $cancel))
    $result = $form.ShowDialog()
    $index = $list.SelectedIndex
    $form.Dispose()
    if ($result -ne [Windows.Forms.DialogResult]::OK -or $index -lt 0) { return $null }
    return $files[$index]
}
