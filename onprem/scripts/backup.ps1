# ServiceBills backup. Writes servicebills-yyyyMMdd-HHmm.zip (db.dump + uploads) and prints its path.
# Exit 0 on success, 1 on failure.
param(
    [switch]$Quiet,
    [string]$Destination = ''
)

# Dot-source the helper library unless it is already loaded (lets Pester mock its functions).
if (-not (Get-Command -Name Write-SBLog -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'common.ps1')
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    Write-SBLog -name 'backup' -message 'Backup started'
    Wait-SBDockerEngine -Seconds 180
    $zip = New-SBBackup -Destination $Destination
    Write-SBLog -name 'backup' -message "Backup saved: $zip"
    if (-not $Quiet) { [void](Show-SBMessage -Text "Backup saved: $zip") }
    Write-Output $zip
    exit 0
}
catch {
    $msg = ($_.Exception.Message -split "\r?\n")[0]
    try { Write-SBLog -name 'backup' -message "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    if (-not $Quiet) { [void](Show-SBMessage -Text "Backup failed: $msg") }
    exit 1
}
