# ServiceBills updater. Checks for a newer release, backs up, updates the image tag, and rolls back on failure.
# Exit 0 = updated / nothing to do / skipped, 1 = failed.
param(
    [switch]$Quiet,
    [switch]$Force
)

if (-not (Get-Command -Name Write-SBLog -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'common.ps1')
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-UpdateLog {
    param([string]$Message)
    Write-SBLog -name 'update' -message $Message
}

function Save-Result {
    param([string]$Status, [string]$Target, [string]$Message, [object]$Current, [object]$Previous)
    $state = @{
        current     = $Current
        previous    = $Previous
        last_update = (New-SBUpdateResult -status $Status -target $Target -message $Message)
    }
    Write-SBState -path $script:StatePath -state $state
    Write-UpdateLog ("{0}: {1}" -f $Status, $Message)
}

function Get-Prop {
    param([object]$Obj, [string]$Name)
    if ($null -eq $Obj) { return $null }
    $p = $Obj.PSObject.Properties[$Name]
    if ($p) { return $p.Value }
    return $null
}

function Write-Env {
    param([string]$Tag)
    $existing = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($script:EnvPath))
    $merged = Merge-SBEnv -existing $existing -generated ([ordered]@{ IMAGE_TAG = $Tag })
    [IO.File]::WriteAllText($script:EnvPath, (ConvertTo-SBEnvText -Values $merged), (New-Object Text.UTF8Encoding $false))
}

function Complete-Update {
    param([int]$Code, [string]$Message)
    if (-not $Quiet) { [void](Show-SBMessage -Text $Message) }
    exit $Code
}

try {
    $root = Get-SBRoot
    $script:StatePath = Join-Path $root 'state.json'
    $script:EnvPath = Join-Path $root '.env'
    $state = Read-SBState $script:StatePath
    $envMap = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($script:EnvPath))
    $oldTag = if ($envMap.Contains('IMAGE_TAG')) { [string]$envMap['IMAGE_TAG'] } else { 'latest' }
    $stateCurrent = $state['current']
    $statePrevious = $state['previous']

    # License gate: ask the running app. If it is down we carry on, since the update may fix it.
    $info = $null
    try { $info = Invoke-RestMethod -Uri 'http://localhost:8000/api/system/info' -TimeoutSec 15 }
    catch { Write-UpdateLog "system/info unavailable: $($_.Exception.Message)" }
    $license = Get-Prop $info 'license'
    if ((Get-Prop $license 'state') -eq 'readonly' -and @('expired', 'revoked', 'version_not_covered') -contains (Get-Prop $license 'reason')) {
        Save-Result -Status 'skipped' -Target '' -Message 'License not valid for updates' -Current $stateCurrent -Previous $statePrevious
        Complete-Update -Code 0 -Message 'License not valid for updates.'
    }

    # Ask the update server.
    $server = 'https://servicebills.onrender.com'
    if ($envMap.Contains('LICENSE_SERVER_URL') -and $envMap['LICENSE_SERVER_URL']) { $server = ([string]$envMap['LICENSE_SERVER_URL']).TrimEnd('/') }
    $latestInfo = $null
    try { $latestInfo = Invoke-RestMethod -Uri "$server/api/updates/latest" -TimeoutSec 30 }
    catch { Write-UpdateLog "update server error: $($_.Exception.Message)" }
    $latest = [string](Get-Prop $latestInfo 'version')
    if (-not $latest) {
        Save-Result -Status 'skipped' -Target '' -Message 'Could not reach update server' -Current $stateCurrent -Previous $statePrevious
        Complete-Update -Code 0 -Message 'Could not reach the update server. Try again later.'
    }

    # Current version.
    $cur = [string]$stateCurrent
    if (-not $cur -or $cur -eq 'latest') {
        $appVersion = [string](Get-Prop $info 'app_version')
        $cur = '0.0.0'
        if ($appVersion -match '^\d+\.\d+\.\d+$') { $cur = $appVersion }
    }

    # Anything to do?
    if ((Compare-SBVersion $latest $cur) -le 0 -and -not $Force) {
        Write-UpdateLog "Already up to date ($cur)"
        Complete-Update -Code 0 -Message "ServiceBills is up to date (version $cur)."
    }
    $minFrom = [string](Get-Prop $latestInfo 'min_upgrade_from')
    if ($minFrom -and (Compare-SBVersion $cur $minFrom) -lt 0) {
        Save-Result -Status 'skipped' -Target $latest -Message 'Please reinstall with the latest ServiceBills Setup' -Current $stateCurrent -Previous $statePrevious
        Complete-Update -Code 0 -Message 'Please reinstall with the latest ServiceBills Setup.'
    }

    # Pre-update backup.
    Write-UpdateLog "Updating $cur -> $latest"
    $zip = $null
    try {
        Wait-SBDockerEngine -Seconds 60
        $zip = New-SBBackup
    }
    catch {
        Write-UpdateLog "Backup error: $($_.Exception.Message)"
        Save-Result -Status 'failed' -Target $latest -Message 'Backup failed, update not attempted' -Current $stateCurrent -Previous $statePrevious
        Complete-Update -Code 1 -Message 'Backup failed, update not attempted.'
    }

    # Switch image tag, pull, start, wait.
    $failure = $null
    try {
        Write-Env -Tag $latest
        [void](Invoke-SBCompose -Arguments @('pull', 'web', 'scheduler'))
        [void](Invoke-SBCompose -Arguments @('up', '-d'))
        if (-not (Wait-SBHealth -Seconds 300)) { throw 'ServiceBills did not pass its health check after the update.' }
    }
    catch { $failure = ($_.Exception.Message -split "\r?\n")[0] }

    if (-not $failure) {
        Save-Result -Status 'ok' -Target $latest -Message "Updated from $cur to $latest" -Current $latest -Previous $cur
        Complete-Update -Code 0 -Message "ServiceBills was updated to version $latest."
    }

    # Roll back: old image tag + pre-update data.
    Write-UpdateLog "Update failed ($failure); rolling back to $oldTag"
    $rollbackNote = ''
    try {
        Write-Env -Tag $oldTag
        $healthy = Restore-SBBackup -Zip $zip
        if (-not $healthy) { $rollbackNote = ' (rollback did not pass its health check)' }
    }
    catch { $rollbackNote = " (rollback error: $(($_.Exception.Message -split "\r?\n")[0]))" }
    Save-Result -Status 'failed' -Target $latest -Message "$failure$rollbackNote" -Current $stateCurrent -Previous $statePrevious
    Complete-Update -Code 1 -Message "The update to $latest failed and ServiceBills was rolled back. $failure"
}
catch {
    $msg = ($_.Exception.Message -split "\r?\n")[0]
    try { Write-UpdateLog "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    if (-not $Quiet) { [void](Show-SBMessage -Text "Update failed: $msg") }
    exit 1
}
