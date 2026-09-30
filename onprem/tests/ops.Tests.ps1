BeforeAll {
    . (Join-Path $PSScriptRoot '..\scripts\common.ps1')
    $script:Backup = Join-Path $PSScriptRoot '..\scripts\backup.ps1'
    $script:Update = Join-Path $PSScriptRoot '..\scripts\update.ps1'
    $script:Uninstall = Join-Path $PSScriptRoot '..\scripts\uninstall.ps1'
    $script:RestoreScript = Join-Path $PSScriptRoot '..\scripts\restore.ps1'

    function New-TestRoot {
        param([string]$Tag = '1.0.0', [string]$Current = '1.0.0', [string]$BackupDir = '')
        $root = Join-Path $TestDrive ('root' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $root 'scripts') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $root '.env'), "JWT_SECRET_KEY=abc`nFERNET_KEY=fk`nPOSTGRES_PASSWORD=pw`nIMAGE_TAG=$Tag`n")
        Write-SBState -path (Join-Path $root 'state\state.json') -state @{ current = $Current; previous = '0.9.0'; last_update = $null }
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
        $global:SBStaged = @()
        $global:SBKeysText = $null
        Mock Invoke-SBCompose { [void]$global:SBCalls.Add(($Arguments -join ' ')); @() }
        Mock New-SBZip {
            $global:SBStaged = @(Get-ChildItem -Path $SourceDir -Recurse -File | ForEach-Object { $_.Name })
            $kf = Join-Path $SourceDir 'env.keys'
            if (Test-Path $kf) { $global:SBKeysText = [IO.File]::ReadAllText($kf) }
            New-Item -ItemType Directory -Path (Split-Path -Parent $ZipPath) -Force | Out-Null
            Set-Content -Path $ZipPath -Value 'zip'
        }
        Mock Show-SBMessage { }
        Mock Wait-SBDockerEngine { }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBCalls, SBStaged, SBKeysText -Scope Global -ErrorAction SilentlyContinue
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

    It 'stores env.keys with only FERNET_KEY and JWT_SECRET_KEY' {
        [void](New-SBBackup -Destination $script:Dest)
        $global:SBStaged | Should -Contain 'env.keys'
        $m = ConvertFrom-SBEnvText $global:SBKeysText
        $m['FERNET_KEY'] | Should -Be 'fk'
        $m['JWT_SECRET_KEY'] | Should -Be 'abc'
        $m.Contains('POSTGRES_PASSWORD') | Should -BeFalse
        $m.Contains('IMAGE_TAG') | Should -BeFalse
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

    It 'backup.ps1 makes sure Docker is up before backing up' {
        & $script:Backup -Quiet | Out-Null
        $LASTEXITCODE | Should -Be 0
        Should -Invoke Wait-SBDockerEngine -Times 1 -Exactly
    }

    It 'backup.ps1 fails (exit 1) without touching containers when Docker never comes up' {
        Mock Wait-SBDockerEngine { throw 'Docker did not become ready within 180 seconds.' }
        & $script:Backup -Quiet | Out-Null
        $LASTEXITCODE | Should -Be 1
        $global:SBCalls.Count | Should -Be 0
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
        $global:SBFailOn = $null
        $global:SBBackupKeys = $null
        Mock Invoke-SBCompose {
            $line = ($Arguments -join ' ')
            [void]$global:SBCalls.Add($line)
            if ($global:SBFailOn -and $line -like $global:SBFailOn) { throw "compose failed: $line" }
            @()
        }
        Mock Expand-SBZip {
            New-Item -ItemType Directory -Path (Join-Path $Destination 'uploads') -Force | Out-Null
            Set-Content -Path (Join-Path $Destination 'db.dump') -Value 'd'
            if ($global:SBBackupKeys) { [IO.File]::WriteAllText((Join-Path $Destination 'env.keys'), $global:SBBackupKeys) }
        }
        Mock Wait-SBHealth { $true }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBCalls, SBFailOn, SBBackupKeys -Scope Global -ErrorAction SilentlyContinue
    }

    It 'stops app services, recreates the db, restores, replaces uploads, restarts and waits for health' {
        (Restore-SBBackup -Zip 'C:\x\servicebills-20260101-0130.zip') | Should -BeTrue
        $calls = @($global:SBCalls)
        $calls[0] | Should -Be 'stop web scheduler'
        $iCp = [array]::FindIndex($calls, [Predicate[string]] { param($c) $c -like 'cp *db.dump db:/tmp/sb.dump' })
        $iDrop = [array]::FindIndex($calls, [Predicate[string]] { param($c) $c -eq 'exec -T db sh -c dropdb -U servicebills --force --if-exists servicebills && createdb -U servicebills servicebills' })
        $iRestore = [array]::FindIndex($calls, [Predicate[string]] { param($c) $c -eq 'exec -T db sh -c pg_restore -U servicebills -d servicebills --no-owner --exit-on-error /tmp/sb.dump' })
        $iCp | Should -BeGreaterThan -1
        $iDrop | Should -BeGreaterThan $iCp
        $iRestore | Should -BeGreaterThan $iDrop
        ($calls | Where-Object { $_ -like '*--clean*' }) | Should -BeNullOrEmpty
        ($calls | Where-Object { $_ -like 'run --rm --no-deps*rm -rf*' }) | Should -Not -BeNullOrEmpty
        ($calls | Where-Object { $_ -like 'cp *uploads?. web:/app/uploads' }) | Should -Not -BeNullOrEmpty
        $calls[-1] | Should -Be 'up -d'
        Should -Invoke Wait-SBHealth -Times 1 -Exactly
    }

    It 'still starts web and scheduler (finally) when pg_restore fails, and surfaces the failure' {
        $global:SBFailOn = '*pg_restore*'
        { Restore-SBBackup -Zip 'C:\x\servicebills-20260101-0130.zip' } | Should -Throw '*pg_restore*'
        @($global:SBCalls)[-1] | Should -Be 'up -d'
        Should -Invoke Wait-SBHealth -Times 0 -Exactly
    }

    It 'the original failure wins when starting the containers also fails' {
        $global:SBFailOn = '*pg_restore*'
        Mock Invoke-SBCompose {
            $line = ($Arguments -join ' ')
            [void]$global:SBCalls.Add($line)
            if ($line -like '*pg_restore*') { throw 'pg_restore exploded' }
            if ($line -eq 'up -d') { throw 'up failed too' }
            @()
        }
        { Restore-SBBackup -Zip 'C:\x\a.zip' } | Should -Throw '*pg_restore exploded*'
    }

    It 'does not touch .env or recreate containers without -RestoreKeys' {
        $global:SBBackupKeys = "FERNET_KEY=other`nJWT_SECRET_KEY=otherjwt`n"
        [void](Restore-SBBackup -Zip 'C:\x\a.zip')
        $m = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $env:SERVICEBILLS_ROOT '.env')))
        $m['FERNET_KEY'] | Should -Be 'fk'
        @($global:SBCalls)[-1] | Should -Be 'up -d'
    }

    It '-RestoreKeys merges the backup keys into .env (other keys kept) and recreates web/scheduler' {
        $global:SBBackupKeys = "FERNET_KEY=other`nJWT_SECRET_KEY=otherjwt`n"
        [void](Restore-SBBackup -Zip 'C:\x\a.zip' -RestoreKeys)
        $m = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $env:SERVICEBILLS_ROOT '.env')))
        $m['FERNET_KEY'] | Should -Be 'other'
        $m['JWT_SECRET_KEY'] | Should -Be 'otherjwt'
        $m['POSTGRES_PASSWORD'] | Should -Be 'pw'
        $m['IMAGE_TAG'] | Should -Be '1.0.0'
        @($global:SBCalls)[-1] | Should -Be 'up -d --force-recreate web scheduler'
    }

    It '-RestoreKeys with an old backup that has no env.keys leaves .env alone' {
        [void](Restore-SBBackup -Zip 'C:\x\a.zip' -RestoreKeys)
        $m = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $env:SERVICEBILLS_ROOT '.env')))
        $m['FERNET_KEY'] | Should -Be 'fk'
        @($global:SBCalls)[-1] | Should -Be 'up -d'
    }
}

Describe 'restore.ps1' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = New-TestRoot
        $script:Work = Join-Path $TestDrive ('rz' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $script:Work 'src') -Force | Out-Null
        Set-Content -Path (Join-Path $script:Work 'src\db.dump') -Value 'd'
        $global:SBKeyAnswer = $true
        $global:SBPromptedKeys = 0
        $global:SBRestoreKeysArg = $null
        Mock Show-SBMessage {
            if ($Text -like '*another installation*') { $global:SBPromptedKeys++; return $global:SBKeyAnswer }
            return $true
        }
        Mock New-SBBackup { 'C:\bk\safety.zip' }
        Mock Restore-SBBackup { $global:SBRestoreKeysArg = [bool]$RestoreKeys; $true }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBKeyAnswer, SBPromptedKeys, SBRestoreKeysArg, SBPicked -Scope Global -ErrorAction SilentlyContinue
    }

    BeforeAll {
        function New-PickedBackup {
            param([string]$Keys)
            $src = Join-Path $script:Work 'src'
            $keyFile = Join-Path $src 'env.keys'
            if ($Keys) { [IO.File]::WriteAllText($keyFile, $Keys) } elseif (Test-Path $keyFile) { Remove-Item $keyFile }
            $zip = Join-Path $script:Work 'servicebills-20260101-0130.zip'
            New-SBZip -SourceDir $src -ZipPath $zip
            return (Get-Item $zip)
        }
    }

    It 'asks about the keys when the backup came from another installation; Yes restores them' {
        $f = New-PickedBackup -Keys "FERNET_KEY=zzz`nJWT_SECRET_KEY=yyy`n"
        $global:SBPicked = $f
        Mock Select-SBBackupFile { $global:SBPicked }
        & $script:RestoreScript
        $LASTEXITCODE | Should -Be 0
        $global:SBPromptedKeys | Should -Be 1
        $global:SBRestoreKeysArg | Should -BeTrue
    }

    It 'No keeps the current keys' {
        $global:SBKeyAnswer = $false
        $f = New-PickedBackup -Keys "FERNET_KEY=zzz`nJWT_SECRET_KEY=yyy`n"
        $global:SBPicked = $f
        Mock Select-SBBackupFile { $global:SBPicked }
        & $script:RestoreScript
        $LASTEXITCODE | Should -Be 0
        $global:SBPromptedKeys | Should -Be 1
        $global:SBRestoreKeysArg | Should -BeFalse
    }

    It 'does not ask when the keys are the same' {
        $f = New-PickedBackup -Keys "FERNET_KEY=fk`nJWT_SECRET_KEY=abc`n"
        $global:SBPicked = $f
        Mock Select-SBBackupFile { $global:SBPicked }
        & $script:RestoreScript
        $global:SBPromptedKeys | Should -Be 0
        $global:SBRestoreKeysArg | Should -BeFalse
    }

    It 'does not ask for an old backup without env.keys' {
        $f = New-PickedBackup
        $global:SBPicked = $f
        Mock Select-SBBackupFile { $global:SBPicked }
        & $script:RestoreScript
        $global:SBPromptedKeys | Should -Be 0
        $global:SBRestoreKeysArg | Should -BeFalse
    }

    It 'takes a safety backup first and exits 1 when the restore fails' {
        $f = New-PickedBackup
        $global:SBPicked = $f
        Mock Select-SBBackupFile { $global:SBPicked }
        Mock Restore-SBBackup { throw 'pg_restore failed' }
        & $script:RestoreScript
        $LASTEXITCODE | Should -Be 1
        Should -Invoke New-SBBackup -Times 1 -Exactly
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like 'Restore failed:*' }
    }
}

Describe 'update.ps1' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = New-TestRoot
        $script:Root = $env:SERVICEBILLS_ROOT
        $script:StatePath = Join-Path $script:Root 'state\state.json'
        $script:EnvFile = Join-Path $script:Root '.env'
        $global:SBSeq = New-Object System.Collections.ArrayList
        $global:SBInfo = [pscustomobject]@{ app_version = '1.0.0'; license = [pscustomobject]@{ state = 'active'; reason = $null } }
        $global:SBLatest = [pscustomobject]@{ version = '1.1.0'; min_upgrade_from = $null }
        $global:SBHealthy = $true
        $global:SBUpdateServerCalled = 0
        $global:SBRestored = $null
        $global:SBRestoreKeysArg = $null
        $global:SBPullFails = $false
        $global:SBRestoreMode = 'ok'
        $global:SBBackupFails = $false
        $global:SBEnvPath = $script:EnvFile
        Mock Invoke-RestMethod {
            if ($Uri -like '*/api/system/info') { return $global:SBInfo }
            $global:SBUpdateServerCalled++
            if ($global:SBLatest -eq 'unreachable') { throw 'no route to host' }
            return $global:SBLatest
        }
        Mock Invoke-SBDocker {
            [void]$global:SBSeq.Add('docker ' + ($Arguments -join ' '))
            if ($global:SBPullFails) { throw 'pull access denied' }
            @()
        }
        Mock Invoke-SBCompose {
            $tag = (ConvertFrom-SBEnvText ([IO.File]::ReadAllText($global:SBEnvPath)))['IMAGE_TAG']
            [void]$global:SBSeq.Add(('compose ' + ($Arguments -join ' ') + ' [tag=' + $tag + ']'))
            @()
        }
        Mock New-SBBackup {
            [void]$global:SBSeq.Add('backup')
            if ($global:SBBackupFails) { throw 'disk full' }
            'C:\bk\servicebills-20260101-0130.zip'
        }
        Mock Restore-SBBackup {
            [void]$global:SBSeq.Add('restore')
            $global:SBRestored = $Zip
            $global:SBRestoreKeysArg = [bool]$RestoreKeys
            if ($global:SBRestoreMode -eq 'throw') { throw 'pg_restore failed: syntax error' }
            return ($global:SBRestoreMode -eq 'ok')
        }
        Mock Wait-SBHealth { $global:SBHealthy }
        Mock Show-SBMessage { }
        Mock Wait-SBDockerEngine { }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        foreach ($v in 'SBSeq', 'SBInfo', 'SBLatest', 'SBHealthy', 'SBUpdateServerCalled', 'SBRestored', 'SBRestoreKeysArg', 'SBPullFails', 'SBRestoreMode', 'SBBackupFails', 'SBEnvPath') {
            Remove-Variable -Name $v -Scope Global -ErrorAction SilentlyContinue
        }
    }

    It 'does nothing when no newer version exists' {
        $global:SBLatest = [pscustomobject]@{ version = '1.0.0'; min_upgrade_from = $null }
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        Should -Invoke New-SBBackup -Times 0 -Exactly
        $global:SBSeq.Count | Should -Be 0
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.0.0'
        $st['last_update'] | Should -BeNullOrEmpty
    }

    It 'makes sure Docker is up first' {
        & $script:Update -Quiet
        Should -Invoke Wait-SBDockerEngine -Times 1 -Exactly
    }

    It 'skips (exit 0, no change) when Docker never comes up' {
        Mock Wait-SBDockerEngine { throw 'Docker did not become ready' }
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        $global:SBSeq.Count | Should -Be 0
        (Read-SBState $script:StatePath)['last_update']['status'] | Should -Be 'skipped'
    }

    It 'updates in the right order: pull, stop, backup, tag switch + up, ok state with previous' {
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        $seq = @($global:SBSeq)
        $seq | Should -Be @(
            'docker pull ghcr.io/hasbach/servicebills:1.1.0',
            'compose stop web scheduler [tag=1.0.0]',
            'backup',
            'compose up -d [tag=1.1.0]'
        )
        $env = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($script:EnvFile))
        $env['IMAGE_TAG'] | Should -Be '1.1.0'
        $env['JWT_SECRET_KEY'] | Should -Be 'abc'
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.1.0'
        $st['previous'] | Should -Be '1.0.0'
        $st['last_update']['status'] | Should -Be 'ok'
        $st['last_update']['target'] | Should -Be '1.1.0'
        Should -Invoke Restore-SBBackup -Times 0 -Exactly
    }

    It 'a failed download changes nothing: no stop, no backup, no restore, tag untouched, status skipped' {
        $global:SBPullFails = $true
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 0
        @($global:SBSeq) | Should -Be @('docker pull ghcr.io/hasbach/servicebills:1.1.0')
        Should -Invoke New-SBBackup -Times 0 -Exactly
        Should -Invoke Restore-SBBackup -Times 0 -Exactly
        (ConvertFrom-SBEnvText ([IO.File]::ReadAllText($script:EnvFile)))['IMAGE_TAG'] | Should -Be '1.0.0'
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.0.0'
        $st['last_update']['status'] | Should -Be 'skipped'
        $st['last_update']['message'] | Should -Be 'Download failed, will retry'
    }

    It 'rolls back (restore + old tag) only when health fails after the update' {
        $global:SBHealthy = $false
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 1
        $env = ConvertFrom-SBEnvText ([IO.File]::ReadAllText($script:EnvFile))
        $env['IMAGE_TAG'] | Should -Be '1.0.0'
        Should -Invoke Restore-SBBackup -Times 1 -Exactly
        $global:SBRestored | Should -Be 'C:\bk\servicebills-20260101-0130.zip'
        $global:SBRestoreKeysArg | Should -BeFalse
        @($global:SBSeq)[-1] | Should -Be 'restore'
        $st = Read-SBState $script:StatePath
        $st['current'] | Should -Be '1.0.0'
        $st['previous'] | Should -Be '0.9.0'
        $st['last_update']['status'] | Should -Be 'failed'
        $st['last_update']['target'] | Should -Be '1.1.0'
        $st['last_update']['message'] | Should -Not -BeLike '*ROLLBACK FAILED*'
    }

    It 'says the rollback failed when the restore throws' {
        $global:SBHealthy = $false
        $global:SBRestoreMode = 'throw'
        & $script:Update
        $LASTEXITCODE | Should -Be 1
        $st = Read-SBState $script:StatePath
        $st['last_update']['status'] | Should -Be 'failed'
        $st['last_update']['message'] | Should -BeLike '*ROLLBACK FAILED*pg_restore failed*'
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like '*rollback ALSO failed*' -and $Text -notlike '*was rolled back*' }
    }

    It 'says the rollback failed when the restored stack is not healthy' {
        $global:SBHealthy = $false
        $global:SBRestoreMode = 'unhealthy'
        & $script:Update
        $LASTEXITCODE | Should -Be 1
        (Read-SBState $script:StatePath)['last_update']['message'] | Should -BeLike '*ROLLBACK FAILED*'
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like '*rollback ALSO failed*' }
    }

    It 'reports a successful rollback as such' {
        $global:SBHealthy = $false
        & $script:Update
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like '*was rolled back*' }
    }

    It 'restarts the app and records failed when the backup fails, without changing the tag' {
        $global:SBBackupFails = $true
        & $script:Update -Quiet
        $LASTEXITCODE | Should -Be 1
        $seq = @($global:SBSeq)
        $seq | Should -Be @(
            'docker pull ghcr.io/hasbach/servicebills:1.1.0',
            'compose stop web scheduler [tag=1.0.0]',
            'backup',
            'compose up -d [tag=1.0.0]'
        )
        Should -Invoke Restore-SBBackup -Times 0 -Exactly
        $st = Read-SBState $script:StatePath
        $st['last_update']['status'] | Should -Be 'failed'
        $st['last_update']['message'] | Should -BeLike '*Backup failed, update not attempted*'
        (ConvertFrom-SBEnvText ([IO.File]::ReadAllText($script:EnvFile)))['IMAGE_TAG'] | Should -Be '1.0.0'
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
        $global:SBComposeFails = $false
        Mock Get-SBDefaultBackupDir { $global:SBDefaultBk }
        Mock Invoke-SBCompose {
            [void]$global:SBCalls.Add(($Arguments -join ' '))
            if ($global:SBComposeFails) { throw 'error during connect: docker daemon is not running' }
            @()
        }
        Mock Remove-SBScheduledTask { }
        Mock Remove-SBFirewallRule { }
        Mock Show-SBMessage { }
    }
    AfterEach {
        $env:SERVICEBILLS_ROOT = $script:OldRoot
        Remove-Variable -Name SBCalls, SBDefaultBk, SBComposeFails -Scope Global -ErrorAction SilentlyContinue
    }

    BeforeAll {
        function New-UninstallRoot {
            param([string]$BackupDir)
            $r = New-TestRoot -BackupDir $BackupDir
            Set-Content (Join-Path $r 'compose.yml') 'name: servicebills'
            New-Item -ItemType Directory -Path (Join-Path $r 'setup-src\scripts') -Force | Out-Null
            Set-Content (Join-Path $r 'scripts\start.ps1') '# x'
            Set-Content (Join-Path $r 'logs\install-20260101.log') 'log'
            return $r
        }
    }

    It 'default keeps .env, settings, state and logs; removes only compose.yml, scripts and setup-src' {
        New-Item -ItemType Directory -Path $script:DefaultBk -Force | Out-Null
        Set-Content (Join-Path $script:DefaultBk 'servicebills-20260101-0130.zip') 'x'
        $env:SERVICEBILLS_ROOT = New-UninstallRoot -BackupDir $script:DefaultBk
        & $script:Uninstall
        $LASTEXITCODE | Should -Be 0
        @($global:SBCalls) | Should -Contain 'down'
        @($global:SBCalls) | Should -Not -Contain 'down -v'
        Should -Invoke Remove-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Backup' }
        Should -Invoke Remove-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Updater' }
        Should -Invoke Remove-SBFirewallRule -Times 1 -Exactly
        $r = $env:SERVICEBILLS_ROOT
        Test-Path (Join-Path $r '.env') | Should -BeTrue
        Test-Path (Join-Path $r 'settings.json') | Should -BeTrue
        Test-Path (Join-Path $r 'state\state.json') | Should -BeTrue
        Test-Path (Join-Path $r 'logs') | Should -BeTrue
        Test-Path (Join-Path $r 'compose.yml') | Should -BeFalse
        Test-Path (Join-Path $r 'scripts') | Should -BeFalse
        Test-Path (Join-Path $r 'setup-src') | Should -BeFalse
        Test-Path (Join-Path $script:DefaultBk 'servicebills-20260101-0130.zip') | Should -BeTrue
        # The kept .env still carries the keys needed to decrypt the kept data.
        (ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $r '.env'))))['FERNET_KEY'] | Should -Be 'fk'
    }

    It '-DeleteData passes -v, removes .env/settings/state, and deletes the default backup dir' {
        New-Item -ItemType Directory -Path $script:DefaultBk -Force | Out-Null
        Set-Content (Join-Path $script:DefaultBk 'servicebills-20260101-0130.zip') 'x'
        $env:SERVICEBILLS_ROOT = New-UninstallRoot -BackupDir $script:DefaultBk
        & $script:Uninstall -DeleteData
        $LASTEXITCODE | Should -Be 0
        @($global:SBCalls) | Should -Contain 'down -v'
        $r = $env:SERVICEBILLS_ROOT
        Test-Path (Join-Path $r '.env') | Should -BeFalse
        Test-Path (Join-Path $r 'settings.json') | Should -BeFalse
        Test-Path (Join-Path $r 'state') | Should -BeFalse
        Test-Path (Join-Path $r 'logs') | Should -BeTrue
        Test-Path $script:DefaultBk | Should -BeFalse
    }

    It '-DeleteData never deletes a user-chosen backup folder' {
        $custom = Join-Path $TestDrive 'OneDrive\my-backups'
        New-Item -ItemType Directory -Path $custom -Force | Out-Null
        Set-Content (Join-Path $custom 'servicebills-20260101-0130.zip') 'x'
        $env:SERVICEBILLS_ROOT = New-UninstallRoot -BackupDir $custom
        & $script:Uninstall -DeleteData
        $LASTEXITCODE | Should -Be 0
        @($global:SBCalls) | Should -Contain 'down -v'
        Test-Path (Join-Path $custom 'servicebills-20260101-0130.zip') | Should -BeTrue
    }

    It '-DeleteData with Docker not running: clear message, exit 1, nothing deleted' {
        $global:SBComposeFails = $true
        $env:SERVICEBILLS_ROOT = New-UninstallRoot -BackupDir $script:DefaultBk
        New-Item -ItemType Directory -Path $script:DefaultBk -Force | Out-Null
        & $script:Uninstall -DeleteData
        $LASTEXITCODE | Should -Be 1
        Should -Invoke Show-SBMessage -Times 1 -Exactly -ParameterFilter { $Text -like '*data volumes could not be removed*Docker*' }
        $r = $env:SERVICEBILLS_ROOT
        Test-Path (Join-Path $r '.env') | Should -BeTrue
        Test-Path $script:DefaultBk | Should -BeTrue
        $logs = Get-ChildItem (Join-Path $r 'logs') -Filter 'uninstall-*.log' | Get-Content -Raw
        $logs | Should -BeLike '*could not be removed*'
    }

    It 'a failed "down" without -DeleteData is only logged' {
        $global:SBComposeFails = $true
        $env:SERVICEBILLS_ROOT = New-UninstallRoot -BackupDir $script:DefaultBk
        & $script:Uninstall
        $LASTEXITCODE | Should -Be 0
        Should -Invoke Show-SBMessage -Times 0 -Exactly
    }
}
