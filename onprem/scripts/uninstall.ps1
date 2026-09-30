# ServiceBills uninstall (called by the Inno Setup uninstaller). Docker Desktop is left alone.
# Without -DeleteData the customer data AND the configuration (.env with its keys, settings.json,
# state\, logs\) are kept, so a reinstall picks the data up and saved passwords still decrypt.
param(
    [switch]$DeleteData
)

if (-not (Get-Command -Name Write-SBLog -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'common.ps1')
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NormalPath {
    param([string]$Path)
    return $Path.TrimEnd('\', '/').ToLowerInvariant()
}

try {
    $root = Get-SBRoot
    Write-SBLog -name 'uninstall' -message "Uninstall started (DeleteData=$DeleteData)"
    $backupDir = Resolve-SBBackupDir

    $volumesFailed = $null
    try {
        if ($DeleteData) { [void](Invoke-SBCompose -Arguments @('down', '-v')) }
        else { [void](Invoke-SBCompose -Arguments @('down')) }
    }
    catch {
        Write-SBLog -name 'uninstall' -message "docker compose down failed: $($_.Exception.Message)"
        if ($DeleteData) { $volumesFailed = $_.Exception.Message }
    }

    Remove-SBScheduledTask -Name 'ServiceBills Backup'
    Remove-SBScheduledTask -Name 'ServiceBills Updater'
    Remove-SBFirewallRule

    if ($null -ne $volumesFailed) {
        # Keep every file so the removal can be repeated once Docker is running.
        Write-SBLog -name 'uninstall' -message 'Data volumes could not be removed; files were left in place.'
        [void](Show-SBMessage -Text ('The ServiceBills data volumes could not be removed because Docker is not running. ' +
                'Start Docker Desktop and run the uninstall again to delete the data. Your files in ' + $root + ' were left in place.'))
        exit 1
    }

    $normBackup = Get-NormalPath $backupDir
    if ($DeleteData) {
        # Everything under the root except logs\ (and the backup folder if it happens to live in here).
        $keep = @('logs')
        foreach ($item in @(Get-ChildItem -Path $root -Force -ErrorAction SilentlyContinue)) {
            if ($keep -contains $item.Name) { continue }
            if ((Get-NormalPath $item.FullName) -eq $normBackup) { continue }
            # scripts\ contains this running script, so a failed delete there is expected and only logged.
            try { Remove-Item -Path $item.FullName -Recurse -Force }
            catch { Write-SBLog -name 'uninstall' -message "Could not remove $($item.FullName): $($_.Exception.Message)" }
        }
    }
    else {
        # Only the program files; .env, settings.json, state\ and logs\ stay.
        foreach ($name in @('compose.yml', 'scripts', 'setup-src')) {
            $path = Join-Path $root $name
            if (-not (Test-Path $path)) { continue }
            try { Remove-Item -Path $path -Recurse -Force }
            catch { Write-SBLog -name 'uninstall' -message "Could not remove ${path}: $($_.Exception.Message)" }
        }
    }

    # Backups are user data: only the default folder is ever deleted, and only with -DeleteData.
    if ($DeleteData -and $normBackup -eq (Get-NormalPath (Get-SBDefaultBackupDir)) -and (Test-Path $backupDir)) {
        Remove-Item -Path $backupDir -Recurse -Force
        Write-SBLog -name 'uninstall' -message "Deleted default backup folder $backupDir"
    }
    Write-SBLog -name 'uninstall' -message 'Uninstall complete'
    exit 0
}
catch {
    try { Write-SBLog -name 'uninstall' -message "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    exit 1
}
