BeforeAll {
    . (Join-Path $PSScriptRoot '..\scripts\common.ps1')
    $script:Install = Join-Path $PSScriptRoot '..\scripts\install.ps1'
}

Describe 'install.ps1' {
    BeforeEach {
        $script:OldRoot = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = Join-Path $TestDrive ('root' + [guid]::NewGuid().ToString('N'))
        $script:Root = $env:SERVICEBILLS_ROOT
        $script:Src = Join-Path $TestDrive ('src' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $script:Src 'scripts') -Force | Out-Null
        Set-Content -Path (Join-Path $script:Src 'docker-compose.yml') -Value 'name: servicebills'
        Set-Content -Path (Join-Path $script:Src 'scripts\backup.ps1') -Value '# backup'
        $script:Token = 'ghp_SECRETTOKEN123'
        $global:SBTestIp = '192.168.1.50'

        Mock Read-SBMachineGuid { '0a1b2c3d-1111-2222-3333-444455556666' }
        Mock Get-SBLanIpLive { $global:SBTestIp }
        Mock Wait-SBDockerEngine { }
        Mock Invoke-SBDockerStdin { $global:LASTEXITCODE = 0 }
        Mock Invoke-SBDocker { @() }
        Mock Wait-SBHealth { $true }
        Mock Add-SBFirewallRule { }
        Mock Set-SBScheduledTask { }
        Mock Remove-SBScheduledTask { }
    }
    AfterEach { $env:SERVICEBILLS_ROOT = $script:OldRoot; Remove-Variable -Name SBTestIp -Scope Global -ErrorAction SilentlyContinue }

    It 'fresh install writes every .env key and the right MACHINE_ID' {
        & $script:Install -SourceDir $script:Src -BackupDir 'D:\bk' -GhcrUser u -GhcrToken $script:Token
        $LASTEXITCODE | Should -Be 0
        $envMap = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $script:Root '.env')))
        foreach ($k in 'JWT_SECRET_KEY', 'FERNET_KEY', 'POSTGRES_PASSWORD', 'MACHINE_ID', 'TZ', 'APP_BASE_URL', 'IMAGE_TAG') {
            $envMap.Contains($k) | Should -BeTrue -Because $k
        }
        $envMap['MACHINE_ID'] | Should -Be (Get-SBMachineId '0a1b2c3d-1111-2222-3333-444455556666')
        $envMap['APP_BASE_URL'] | Should -Be 'http://192.168.1.50:8000'
        $envMap['IMAGE_TAG'] | Should -Be 'latest'
        Test-Path (Join-Path $script:Root 'compose.yml') | Should -BeTrue
        Test-Path (Join-Path $script:Root 'scripts\backup.ps1') | Should -BeTrue
        Test-Path (Join-Path $script:Root 'logs') | Should -BeTrue
        (Read-SBState (Join-Path $script:Root 'state.json'))['current'] | Should -Be 'latest'
        $s = Get-Content (Join-Path $script:Root 'settings.json') -Raw | ConvertFrom-Json
        $s.backup_dir | Should -Be 'D:\bk'
        $s.auto_update | Should -BeTrue
    }

    It 'reinstall keeps secrets but refreshes APP_BASE_URL' {
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token -NoStart
        $first = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $script:Root '.env')))
        $global:SBTestIp = '10.0.0.9'
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token -NoStart
        $second = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $script:Root '.env')))
        foreach ($k in 'JWT_SECRET_KEY', 'FERNET_KEY', 'POSTGRES_PASSWORD', 'MACHINE_ID') {
            $second[$k] | Should -Be $first[$k]
        }
        $second['APP_BASE_URL'] | Should -Be 'http://10.0.0.9:8000'
    }

    It 'registers backup and updater tasks when auto-update is on' {
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token -NoStart
        Should -Invoke Set-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Backup' -and $At -eq '01:30' }
        Should -Invoke Set-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Updater' -and $At -eq '03:30' }
        Should -Invoke Remove-SBScheduledTask -Times 0 -Exactly
    }

    It '-AutoUpdate $false unregisters the updater and registers backup' {
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token -NoStart -AutoUpdate $false
        Should -Invoke Set-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Backup' }
        Should -Invoke Set-SBScheduledTask -Times 0 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Updater' }
        Should -Invoke Remove-SBScheduledTask -Times 1 -Exactly -ParameterFilter { $Name -eq 'ServiceBills Updater' }
        (Get-Content (Join-Path $script:Root 'settings.json') -Raw | ConvertFrom-Json).auto_update | Should -BeFalse
    }

    It 'passes the GHCR token via stdin only' {
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token
        Should -Invoke Invoke-SBDockerStdin -Times 1 -Exactly -ParameterFilter {
            $InputText -eq $script:Token -and ($Arguments -join ' ') -like '*--password-stdin*' -and ($Arguments -join ' ') -notlike "*$($script:Token)*"
        }
        Should -Invoke Invoke-SBDocker -Times 0 -Exactly -ParameterFilter { ($Arguments -join ' ') -like "*$($script:Token)*" }
        $logs = Get-ChildItem (Join-Path $script:Root 'logs') -Filter *.log | Get-Content -Raw
        $logs | Should -Not -BeLike "*$($script:Token)*"
    }

    It 'pulls and starts the stack and waits for health' {
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token
        Should -Invoke Invoke-SBDocker -Times 1 -Exactly -ParameterFilter { $Arguments -contains 'pull' }
        Should -Invoke Invoke-SBDocker -Times 1 -Exactly -ParameterFilter { $Arguments -contains 'up' }
        Should -Invoke Wait-SBHealth -Times 1 -Exactly
        Should -Invoke Add-SBFirewallRule -Times 1 -Exactly
    }

    It 'exits 1 and logs the error when health never comes up' {
        Mock Wait-SBHealth { $false }
        & $script:Install -SourceDir $script:Src -GhcrUser u -GhcrToken $script:Token
        $LASTEXITCODE | Should -Be 1
        $logs = Get-ChildItem (Join-Path $script:Root 'logs') -Filter install-*.log | Get-Content -Raw
        $logs | Should -BeLike '*ERROR*health*'
    }
}
