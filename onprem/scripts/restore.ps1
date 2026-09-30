# ServiceBills restore (interactive). Pick a backup, confirm, take a safety backup, then restore.
param()

if (-not (Get-Command -Name Write-SBLog -CommandType Function -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'common.ps1')
}
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Select-BackupFile {
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

try {
    $dir = Resolve-SBBackupDir
    $file = Select-BackupFile -Dir $dir
    if (-not $file) { exit 0 }
    $when = (Get-SBBackupDate $file.Name).ToString('yyyy-MM-dd HH:mm')
    if (-not (Show-SBMessage -YesNo -Text "This replaces all current ServiceBills data with the backup from $when. Continue?")) { exit 0 }
    Write-SBLog -name 'restore' -message "Restoring $($file.FullName)"
    $safety = New-SBBackup -Destination $dir
    Write-SBLog -name 'restore' -message "Safety backup: $safety"
    $healthy = Restore-SBBackup -Zip $file.FullName
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
