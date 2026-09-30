BeforeAll {
    . (Join-Path $PSScriptRoot '..\scripts\common.ps1')
    $script:Backup = Join-Path $PSScriptRoot '..\scripts\backup.ps1'
    $script:Update = Join-Path $PSScriptRoot '..\scripts\update.ps1'
    $script:Uninstall = Join-Path $PSScriptRoot '..\scripts\uninstall.ps1'

    function New-TestRoot {
        param([string]$Tag = '1.0.0', [string]$Current = '1.0.0', [string]$BackupDir = '')
        $root = Join-Path $TestDrive ('root' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $root 'scripts') -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $root '.env'), "JWT_SECRET_KEY=abc`nIMAGE_TAG=$Tag`n")
        Write-SBState -path (Join-Path $root 'state.json') -state @{ current = $Current; previous = '0.9.0'; last_update = $null }
        $bk = if ($BackupDir) { $BackupDir } else { Join-Path $TestDrive ('bk' + [guid]::NewGuid().ToString('N')) }
        [IO.File]::WriteAllText((Join-Path $root 'settings.json'), (@{ backup_dir = $bk; auto_update = $true } | ConvertTo-Json))
        return $root
    }
}

Describe 'backup' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = New-TestRoot
        $script:Dest = Join-Path $TestDrive ('dest' + [guid]::NewGuid().ToString('N'))
        $global:SBCalls = New-Object System.Collections.ArrayList
        Mock Invoke-SBCompose { [void]$global:SBCalls.Add(($Arguments -join ' ')); @() }
        Mock Compress-Archive {
            New-Item -ItemType Directory -Path (Split-Path -Parent $DestinationPath) -Force | Out-Null
            Set-Content -Path $DestinationPath -Value 'zip'
        }
        Mock Show-SBMessage { }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBCalls -Scope Global -ErrorAction SilentlyContinue
    }

    It 'runs the expected docker sequence and names the zip by timestamp' {
        $zip = New-SBBackup -Destination $script:Dest
        $zip | Should -Match 'servicebills-\d{8}-\d{4}\.zip$'
        (Split-Path -Parent $zip) | Should -Be $script:Dest
        Test-Path $zip | Should -BeTrue
        $calls = @($global:SBCalls)
        $calls.Count | Should -Be 4
        $calls[0] | Should -BeLike 'exec -T db sh -c pg_dump -U servicebills -Fc -f /tmp/sb.dump servicebills'
        $calls[1] | Should -BeLike 'cp db:/tmp/sb.dump *db.dump'
        $calls[2] | Should -Be 'exec -T db rm -f /tmp/sb.dump'
        $calls[3] | Should -BeLike 'cp web:/app/uploads *uploads'
    }

    It 'rotation deletes only backups beyond the newest 14' {
        New-Item -ItemType Directory -Path $script:Dest -Force | Out-Null
        1..16 | ForEach-Object { Set-Content -Path (Join-Path $script:Dest ('servicebills-202601{0:d2}-0130.zip' -f $_)) -Value 'x' }
        Set-Content -Path (Join-Path $script:Dest 'notes.txt') -Value 'keep me'
        [void](New-SBBackup -Destination $script:Dest)
        $zips = @(Get-ChildItem $script:Dest -Filter 'servicebills-*.zip')
        $zips.Count | Should -Be 14
        Test-Path (Join-Path $script:Dest 'servicebills-20260101-0130.zip') | Should -BeFalse
        Test-Path (Join-Path $script:Dest 'servicebills-20260102-0130.zip') | Should -BeFalse
        Test-Path (Join-Path $script:Dest 'servicebills-20260116-0130.zip') | Should -BeTrue
        Test-Path (Join-Path $script:Dest 'notes.txt') | Should -BeTrue
    }

    It 'backup.ps1 uses settings backup_dir, shows a message, and exits 0' {
        $r = & $script:Backup
        $LASTEXITCODE | Should -Be 0
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like 'Backup saved: *' }
        $settings = Get-Content (Join-Path $env:SERVICEBILLS_ROOT 'settings.json') -Raw | ConvertFrom-Json
        @(Get-ChildItem $settings.backup_dir -Filter '*.zip').Count | Should -Be 1
        $r | Should -Match 'servicebills-\d{8}-\d{4}\.zip$'
    }

    It 'backup.ps1 -Quiet shows no message; failure exits 1' {
        & $script:Backup -Quiet | Out-Null
        $LASTEXITCODE | Should -Be 0
        Should -Invoke Show-SBMessage -Times 0 -Exactly
        Mock Invoke-SBCompose { throw 'db is down' }
        & $script:Backup -Quiet | Out-Null
        $LASTEXITCODE | Should -Be 1
        Should -Invoke Show-SBMessage -Times 0 -Exactly
    }

    It 'backup.ps1 failure without -Quiet shows a message and exits 1' {
        Mock Invoke-SBCompose { throw 'db is down' }
        & $script:Backup | Out-Null
        $LASTEXITCODE | Should -Be 1
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like '*Backup failed*' }
    }
}

Describe 'restore core' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = New-TestRoot
        $global:SBCalls = New-Object System.Collections.ArrayList
        Mock Invoke-SBCompose { [void]$global:SBCalls.Add(($Arguments -join ' ')); @() }
        Mock Expand-Archive {
            New-Item -ItemType Directory -Path (Join-Path $DestinationPath 'uploads') -Force | Out-Null
            Set-Content -Path (Join-Path $DestinationPath 'db.dump') -Value 'd'
        }
        Mock Wait-SBHealth { $true }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBCalls -Scope Global -ErrorAction SilentlyContinue
    }

    It 'stops app services, restores the db, replaces uploads, restarts and waits for health' {
        (Restore-SBBackup -Zip 'C:\x\servicebills-20260101-0130.zip') | Should -BeTrue
        $calls = @($global:SBCalls)
        $calls[0] | Should -Be 'stop web scheduler'
        ($calls | Where-Object { $_ -like 'cp *db.dump db:/tmp/sb.dump' }) | Should -Not -BeNullOrEmpty
        ($calls | Where-Object { $_ -like 'exec -T db sh -c pg_restore -U servicebills -d servicebills --clean --if-exists --no-owner /tmp/sb.dump' }) | Should -Not -BeNullOrEmpty
        ($calls | Where-Object { $_ -like 'run --rm --no-deps*rm -rf*' }) | Should -Not -BeNullOrEmpty
        ($calls | Where-Object { $_ -like 'cp *uploads?. web:/app/uploads' }) | Should -Not -BeNullOrEmpty
        $calls[-1] | Should -Be 'up -d'
        Should -Invoke Wait-SBHealth -Times 1 -Exactly
    }
}

Describe 'update.ps1' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = New-TestRoot
        $script:Root = $env:SERVICEBILLS_ROOT
        $script:StatePath = Join-Path $script:Root 'state.json'
        $global:SBCalls = New-Object System.Collections.ArrayList
        $global:SBInfo = [pscustomobject]@{ app_version = '1.0.0'; license = [pscustomobject]@{ state = 'active'; reason = $null } }
        $global:SBLatest = [pscustomobject]@{ version = '1.1.0'; min_upgrade_from = $null }
        $global:SBHealthy = $true
        $global:SBUpdateServerCalled = 0
        $global:SBRestored = $null
        Mock Invoke-RestMethod {
            if ($Uri -like '*/api/system/info') { return $global:SBInfo }
            $global:SBUpdateServerCalled++
            if ($global:SBLatest -eq 'unreachable') { throw 'no route to host' }
            return $global:SBLatest
        }
        Mock Invoke-SBCompose { [void]$global:SBCalls.Add(($Arguments -join ' ')); @() }
        Mock New-SBBackup { 'C:\bk\servicebills-20260101-0130.zip' }
        Mock Restore-SBBackup { $global:SBRestored = $Zip; $true }
        Mock Wait-SBHealth { $global:SBHealthy }
        Mock Show-SBMessage { }
        Mock Wait-SBDockerEngine { }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        foreach ($v in 'SBCalls', 'SBInfo', 'SBLatest', 'SBHealthy', 'SBUpdateServerCalled', 'SBRestored') {
            Remove-Variable -Name $v -Scope Global -ErrorAction SilentlyContinue
        }
    }

    It 'does nothing when no newer version exists' {
        $global:SBLatest = [pscustomobject]@{ version = '1.0.0'; min_upgrade_from = $null }
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        Should -Invoke New-SBBackup -Times 0 -Exactly
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.0.0'
        $st['last_update'] | Should -BeNullOrEmpty
    }

    It 'updates: backup, pull, up, ok state with previous' {
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        Should -Invoke New-SBBackup -Times 1 -Exactly
        $calls = @($global:SBCalls)
        $calls | Should -Contain 'pull web scheduler'
        $calls | Should -Contain 'up -d'
        $env = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $script:Root '.env')))
        $env['IMAGE_TAG'] | Should -Be '1.1.0'
        $env['JWT_SECRET_KEY'] | Should -Be 'abc'
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.1.0'
        $st['previous'] | Should -Be '1.0.0'
        $st['last_update']['status'] | Should -Be 'ok'
        $st['last_update']['target'] | Should -Be '1.1.0'
    }

    It 'rolls back when health fails after the update' {
        $global:SBHealthy = $false
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 1
        $env = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $script:Root '.env')))
        $env['IMAGE_TAG'] | Should -Be '1.0.0'
        Should -Invoke Restore-SBBackup -Times 1 -Exactly
        $global:SBRestored | Should -Be 'C:\bk\servicebills-20260101-0130.zip'
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.0.0'
        $st['previous'] | Should -Be '0.9.0'
        $st['last_update']['status'] | Should -Be 'failed'
        $st['last_update']['target'] | Should -Be '1.1.0'
    }

    It 'fails without touching anything when the backup fails' {
        Mock New-SBBackup { throw 'disk full' }
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 1
        $st = Read-SBState $script:StatePath
        $st['last_update']['status'] | Should -Be 'failed'
        $st['last_update']['message'] | Should -BeLike '*Backup failed, update not attempted*'
        $global:SBCalls.Count | Should -Be 0
        $env = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $script:Root '.env')))
        $env['IMAGE_TAG'] | Should -Be '1.0.0'
    }

    It 'skips without calling the update server when the license is expired' {
        $global:SBInfo.license.state = 'readonly'
        $global:SBInfo.license.reason = 'expired'
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        $global:SBUpdateServerCalled | Should -Be 0
        $st = Read-SBState $script:StatePath
        $st['last_update']['status'] | Should -Be 'skipped'
        $st['last_update']['message'] | Should -BeLike '*License not valid for updates*'
    }

    It 'skips when the current version is below min_upgrade_from' {
        $global:SBLatest = [pscustomobject]@{ version = '2.0.0'; min_upgrade_from = '1.5.0' }
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        Should -Invoke New-SBBackup -Times 0 -Exactly
        $st = Read-SBState $script:StatePath
        $st['last_update']['status'] | Should -Be 'skipped'
        $st['last_update']['message'] | Should -BeLike '*reinstall with the latest ServiceBills Setup*'
    }

    It 'skips when the update server is unreachable' {
        $global:SBLatest = 'unreachable'
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        Should -Invoke New-SBBackup -Times 0 -Exactly
        $st = Read-SBState $script:StatePath
        $st['last_update']['status'] | Should -Be 'skipped'
        $st['last_update']['message'] | Should -BeLike '*Could not reach update server*'
    }

    It 'treats a "latest" current tag as the reported app_version' {
        Write-SBState -path $script:StatePath -state @{ current = 'latest'; previous = $null; last_update = $null }
        $global:SBLatest = [pscustomobject]@{ version = '1.0.0'; min_upgrade_from = $null }
        & $script:Update -Quiet
        Should -Invoke New-SBBackup -Times 0 -Exactly
    }

    It '-Force reinstalls the same version; non-quiet shows a message' {
        $global:SBLatest = [pscustomobject]@{ version = '1.0.0'; min_upgrade_from = $null }
        & $script:Update -Force
        $LASTEXITCODE | Should -Be 0
        Should -Invoke New-SBBackup -Times 1 -Exactly
        Should -Invoke Show-SBMessage -Times 1 -Exactly
    }
}

Describe 'uninstall.ps1' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $script:DefaultBk = Join-Path $TestDrive ('docs' + [guid]::NewGuid().ToString('N') + '\ServiceBills Backups')
        $global:SBDefaultBk = $script:DefaultBk
        $global:SBCalls = New-Object System.Collections.ArrayList
        Mock Get-SBDefaultBackupDir { $global:SBDefaultBk }
        Mock Invoke-SBCompose { [void]$global:SBCalls.Add(($Arguments -join ' ')); @() }
        Mock Remove-SBScheduledTask { }
        Mock Remove-SBFirewallRule { }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBCalls, SBDefaultBk -Scope Global -ErrorAction SilentlyContinue
    }

    It 'default keeps volumes, logs and backups; removes tasks, firewall rule and other files' {
        New-Item -ItemType Directory -Path $script:DefaultBk -Force | Out-Null
        Set-Content (Join-Path $script:DefaultBk 'servicebills-20260101-0130.zip') 'x'
        $env:SERVICEBILLS_ROOT = New-TestRoot -BackupDir $script:DefaultBk
        & $script:Uninstall
        $LASTEXITCODE | Should -Be 0
        @($global:SBCalls) | Should -Contain 'down'
        @($global:SBCalls) | Should -Not -Contain 'down -v'
        Should -Invoke Remove-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Backup' }
        Should -Invoke Remove-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Updater' }
        Should -Invoke Remove-SBFirewallRule -Times 1 -Exactly
        Test-Path (Join-Path $env:SERVICEBILLS_ROOT '.env') | Should -BeFalse
        Test-Path (Join-Path $env:SERVICEBILLS_ROOT 'state.json') | Should -BeFalse
        Test-Path (Join-Path $env:SERVICEBILLS_ROOT 'logs') | Should -BeTrue
        Test-Path (Join-Path $script:DefaultBk 'servicebills-20260101-0130.zip') | Should -BeTrue
    }

    It '-DeleteData passes -v and deletes the default backup dir' {
        New-Item -ItemType Directory -Path $script:DefaultBk -Force | Out-Null
        Set-Content (Join-Path $script:DefaultBk 'servicebills-20260101-0130.zip') 'x'
        $env:SERVICEBILLS_ROOT = New-TestRoot -BackupDir $script:DefaultBk
        & $script:Uninstall -DeleteData
        $LASTEXITCODE | Should -Be 0
        @($global:SBCalls) | Should -Contain 'down -v'
        Test-Path $script:DefaultBk | Should -BeFalse
    }

    It '-DeleteData never deletes a user-chosen backup folder' {
        $custom = Join-Path $TestDrive 'OneDrive\my-backups'
        New-Item -ItemType Directory -Path $custom -Force | Out-Null
        Set-Content (Join-Path $custom 'servicebills-20260101-0130.zip') 'x'
        $env:SERVICEBILLS_ROOT = New-TestRoot -BackupDir $custom
        & $script:Uninstall -DeleteData
        $LASTEXITCODE | Should -Be 0
        @($global:SBCalls) | Should -Contain 'down -v'
        Test-Path (Join-Path $custom 'servicebills-20260101-0130.zip') | Should -BeTrue
    }
}
