# Starts ServiceBills and opens it in the browser.
. (Join-Path $PSScriptRoot 'common.ps1')

try {
    Write-SBLog -name 'start' -message 'Starting ServiceBills'
    Wait-SBDockerEngine -Seconds 180
    [void](Invoke-SBCompose -Arguments @('up', '-d'))
    if (-not (Wait-SBHealth -Seconds 120)) { throw 'ServiceBills did not pass its health check within 2 minutes.' }
    Write-SBLog -name 'start' -message 'ServiceBills is up'
    Start-Process 'http://localhost:8000'
    exit 0
}
catch {
    try { Write-SBLog -name 'start' -message "ERROR: $($_.Exception.Message)" } catch { $null = $_ }
    if (Show-SBMessage -Text 'ServiceBills could not start. Open the log folder?' -YesNo) {
        Start-Process -FilePath 'explorer.exe' -ArgumentList (Join-Path (Get-SBRoot) 'logs')
    }
    exit 1
}
