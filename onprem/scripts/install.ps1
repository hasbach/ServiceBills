# ServiceBills installer (run by Setup). Exit 0 on success, 1 on failure with a one-line message on stderr.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', 'GhcrToken', Justification = 'Passed by Setup; forwarded to docker only via stdin.')]
param(
    [string]$BackupDir = '',
    [bool]$AutoUpdate = $true,
    [string]$GhcrUser = '',
    [string]$GhcrToken = '',
    [string]$ImageTag = 'latest',
    [string]$SourceDir = '',
    [switch]$NoStart
)

# Dot-source the helper library unless it is already loaded (lets Pester mock its functions).
if (-not (Get-Command -Name Write-SBLog -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'common.ps1')
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-InstallLog {
    param([string]$Message)
    Write-SBLog -name 'install' -message $Message
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

try {
    if (-not $SourceDir) { $SourceDir = Split-Path -Parent $PSScriptRoot }
    $root = Get-SBRoot
    $envPath = Join-Path $root '.env'
    $statePath = Get-SBStatePath

    # Re-running over an existing install: protect the data first. A failed backup aborts the install.
    if ((Test-Path $envPath) -and (Test-SBWebRunning)) {
        Write-InstallLog 'Existing install is running; taking a backup first'
        try { $preZip = New-SBBackup }
        catch {
            $why = $_.Exception.Message.Split([char]10)[0].Trim()
            throw "Could not back up the existing ServiceBills data before upgrading, so nothing was changed. $why"
        }
        Write-InstallLog "Pre-upgrade backup: $preZip"
    }

    Write-InstallLog "Step 1: creating $root and copying files from $SourceDir"
    foreach ($d in @($root, (Join-Path $root 'logs'), (Join-Path $root 'scripts'), (Split-Path -Parent $statePath))) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
    # Let the interactive user write logs, state and settings (the installer itself runs elevated).
    Grant-SBRootAccess -Root $root
    Copy-Item -Path (Join-Path $SourceDir 'docker-compose.yml') -Destination (Join-Path $root 'compose.yml') -Force
    Copy-Item -Path (Join-Path (Join-Path $SourceDir 'scripts') '*') -Destination (Join-Path $root 'scripts') -Recurse -Force

    Write-InstallLog 'Step 2: building .env'
    $existing = $null
    if (Test-Path $envPath) { $existing = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($envPath)) }
    $existingTag = ''
    if ($existing -and $existing.Contains('IMAGE_TAG')) { $existingTag = [string]$existing['IMAGE_TAG'] }
    # Never lower an installed version (an older Setup, or 'latest', must not downgrade the app).
    $effectiveTag = Resolve-SBImageTag -Existing $existingTag -Requested $ImageTag
    $generated = [ordered]@{
        JWT_SECRET_KEY    = New-SBSecret
        FERNET_KEY        = New-SBFernetKey
        POSTGRES_PASSWORD = New-SBSecret 24
        MACHINE_ID        = Get-SBMachineId (Read-SBMachineGuid)
        TZ                = ConvertTo-SBIanaTimeZone (Get-TimeZone).Id
        APP_BASE_URL      = 'http://{0}:8000' -f (Get-SBLanIpLive)
        IMAGE_TAG         = $effectiveTag
    }
    $merged = Merge-SBEnv -existing $existing -generated $generated
    Write-Utf8NoBom -Path $envPath -Text (ConvertTo-SBEnvText -Values $merged)
    Protect-SBEnvFile -EnvPath $envPath

    Write-InstallLog 'Step 3: state\state.json'
    if (-not (Test-Path $statePath)) {
        Write-SBState -path $statePath -state @{ current = $effectiveTag; previous = $null; last_update = $null }
    }
    else {
        $state = Read-SBState $statePath
        if ([string]$state['current'] -ne $effectiveTag) {
            $prevTag = $state['previous']
            if ($state['current']) { $prevTag = $state['current'] }
            Write-SBState -path $statePath -state @{ current = $effectiveTag; previous = $prevTag; last_update = $state['last_update'] }
        }
    }

    Write-InstallLog 'Step 4: settings.json'
    $settings = [ordered]@{ backup_dir = $BackupDir; auto_update = $AutoUpdate }
    Write-Utf8NoBom -Path (Join-Path $root 'settings.json') -Text ($settings | ConvertTo-Json)

    if (-not $NoStart) {
        Write-InstallLog 'Step 5: starting ServiceBills'
        try { Wait-SBDockerEngine -Seconds 180 }
        catch { throw 'Docker is still starting or needs a restart. Restart the PC and run ServiceBills Setup again.' }
        if ($GhcrToken) {
            Write-InstallLog "docker login ghcr.io as $GhcrUser"
            [void](Invoke-SBDockerStdin -Arguments @('login', 'ghcr.io', '-u', $GhcrUser, '--password-stdin') -InputText $GhcrToken)
            if ($global:LASTEXITCODE -ne 0) { throw 'docker login to ghcr.io failed. Check the registry user and token.' }
        }
        [void](Invoke-SBCompose -Arguments @('pull'))
        [void](Invoke-SBCompose -Arguments @('up', '-d'))
        if (-not (Wait-SBHealth -Seconds 300)) {
            throw 'ServiceBills did not pass its health check within 5 minutes.'
        }
    }

    Write-InstallLog 'Step 6: firewall rule'
    Add-SBFirewallRule

    Write-InstallLog 'Step 7: scheduled tasks'
    Set-SBScheduledTask -Name 'ServiceBills Backup' -At '01:30' -ScriptName 'backup.ps1' -ScriptArguments '-Quiet'
    if ($AutoUpdate) {
        Set-SBScheduledTask -Name 'ServiceBills Updater' -At '03:30' -ScriptName 'update.ps1' -ScriptArguments '-Quiet'
    }
    else {
        Remove-SBScheduledTask -Name 'ServiceBills Updater'
    }

    Write-InstallLog 'Step 8: Docker Desktop starts on login'
    try {
        $how = Set-SBDockerAutoStart
        Write-InstallLog "Docker Desktop auto-start configured via $how"
    }
    catch { Write-InstallLog "Could not configure Docker Desktop auto-start: $($_.Exception.Message)" }

    Write-InstallLog 'Install complete.'
    exit 0
}
catch {
    $msg = ($_.Exception.Message -split "\r?\n")[0]
    try { Write-InstallLog "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    [Console]::Error.WriteLine("ServiceBills setup failed: $msg")
    exit 1
}
