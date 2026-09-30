# Stops the ServiceBills containers (data is kept).
. (Join-Path $PSScriptRoot 'common.ps1')

try {
    Write-SBLog -name 'stop' -message 'Stopping ServiceBills'
    [void](Invoke-SBCompose -Arguments @('stop'))
    Write-SBLog -name 'stop' -message 'Stopped'
    [void](Show-SBMessage -Text 'ServiceBills stopped.')
    exit 0
}
catch {
    try { Write-SBLog -name 'stop' -message "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    [void](Show-SBMessage -Text 'ServiceBills could not be stopped. See the log folder for details.')
    exit 1
}
