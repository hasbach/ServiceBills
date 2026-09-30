# ServiceBills restore (interactive). Pick a backup, confirm, take a safety backup, then restore.
param()

if (-not (Get-Command -Name Write-SBLog -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'common.ps1')
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $dir = Resolve-SBBackupDir
    $file = Select-SBBackupFile -Dir $dir
    if (-not $file) { exit 0 }
    $when = (Get-SBBackupDate $file.Name).ToString('yyyy-MM-dd HH:mm')
    if (-not (Show-SBMessage -YesNo -Text "This replaces all current ServiceBills data with the backup from $when. Continue?")) { exit 0 }

    # A backup from another installation carries different encryption keys; without them saved
    # passwords in that backup cannot be read. Ask before touching .env.
    $restoreKeys = $false
    $backupKeys = Read-SBBackupKeys -Zip $file.FullName
    $envPath = Join-Path (Get-SBRoot) '.env'
    $currentEnv = $null
    if (Test-Path $envPath) { $currentEnv = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($envPath)) }
    if (Test-SBKeysDiffer -BackupKeys $backupKeys -CurrentEnv $currentEnv) {
        $restoreKeys = [bool](Show-SBMessage -YesNo -Text 'This backup came from another installation. Also restore its encryption keys? (needed to read saved passwords)')
    }

    Write-SBLog -name 'restore' -message "Restoring $($file.FullName) (restore keys: $restoreKeys)"
    $safety = New-SBBackup -Destination $dir
    Write-SBLog -name 'restore' -message "Safety backup: $safety"
    $healthy = Restore-SBBackup -Zip $file.FullName -RestoreKeys:$restoreKeys
    if (-not $healthy) { throw 'ServiceBills did not pass its health check after the restore.' }
    Write-SBLog -name 'restore' -message 'Restore complete'
    [void](Show-SBMessage -Text "Restore complete. Data from $when is now active.")
    exit 0
}
catch {
    $msg = ($_.Exception.Message -split "\r?\n")[0]
    try { Write-SBLog -name 'restore' -message "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    [void](Show-SBMessage -Text "Restore failed: $msg")
    exit 1
}
