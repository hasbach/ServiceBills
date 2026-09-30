BeforeAll {
    . (Join-Path $PSScriptRoot '..\scripts\common.ps1')
}

Describe 'Get-SBRoot' {
    It 'defaults to ProgramData\ServiceBills' {
        $old = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = $null
        try { Get-SBRoot | Should -Be 'C:\ProgramData\ServiceBills' }
        finally { $env:SERVICEBILLS_ROOT = $old }
    }
    It 'honours SERVICEBILLS_ROOT' {
        $old = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = 'D:\x'
        try { Get-SBRoot | Should -Be 'D:\x' }
        finally { $env:SERVICEBILLS_ROOT = $old }
    }
}

Describe 'Compare-SBVersion' {
    It 'orders numerically' { Compare-SBVersion '1.10.0' '1.9.9' | Should -Be 1 }
    It 'orders lower' { Compare-SBVersion '1.2.3' '1.2.4' | Should -Be -1 }
    It 'equal' { Compare-SBVersion '2.0.1' '2.0.1' | Should -Be 0 }
    It 'throws on bad input' { { Compare-SBVersion '1.2' '1.2.3' } | Should -Throw }
    It 'throws on bad second arg' { { Compare-SBVersion '1.2.3' 'v1.2.3' } | Should -Throw }
}

Describe 'Get-SBMachineId' {
    It 'is sha256 hex of the GUID as read' {
        Get-SBMachineId '0a1b2c3d-1111-2222-3333-444455556666' |
            Should -Be 'fcd0af9c440f1e9c79c8c05f596f64b4715704498ea061449e16174fe95d0aaf'
    }
    It 'does not normalise case' {
        Get-SBMachineId 'ABC' | Should -Not -Be (Get-SBMachineId 'abc')
    }
}

Describe 'secrets' {
    It 'New-SBSecret is urlsafe without padding' {
        $s = New-SBSecret
        $s | Should -Not -Match '[+/=]'
        $s.Length | Should -Be 43
    }
    It 'New-SBSecret honours byte count and is random' {
        (New-SBSecret 16).Length | Should -Be 22
        New-SBSecret | Should -Not -Be (New-SBSecret)
    }
    It 'New-SBFernetKey is 44 chars decoding to 32 bytes' {
        $k = New-SBFernetKey
        $k.Length | Should -Be 44
        $k | Should -Match '^[A-Za-z0-9_-]{43}=$'
        ([Convert]::FromBase64String($k.Replace('-', '+').Replace('_', '/'))).Length | Should -Be 32
    }
}

Describe 'ConvertTo-SBIanaTimeZone' {
    It 'maps Lebanon' { ConvertTo-SBIanaTimeZone 'Middle East Standard Time' | Should -Be 'Asia/Beirut' }
    It 'maps UTC' { ConvertTo-SBIanaTimeZone 'UTC' | Should -Be 'UTC' }
    It 'maps Pacific' { ConvertTo-SBIanaTimeZone 'Pacific Standard Time' | Should -Be 'America/Los_Angeles' }
    It 'unknown falls back to UTC' { ConvertTo-SBIanaTimeZone 'Nowhere Standard Time' | Should -Be 'UTC' }
}

Describe 'Merge-SBEnv' {
    It 'keeps existing secrets, adds missing, refreshes volatile keys' {
        $existing = @{ JWT_SECRET_KEY = 'old'; MACHINE_ID = 'mid'; TZ = 'Asia/Beirut'; APP_BASE_URL = 'http://1.1.1.1:8000'; IMAGE_TAG = '1.0.0' }
        $generated = @{ JWT_SECRET_KEY = 'new'; MACHINE_ID = 'other'; FERNET_KEY = 'fk'; TZ = 'UTC'; APP_BASE_URL = 'http://10.0.0.5:8000'; IMAGE_TAG = '1.1.0' }
        $r = Merge-SBEnv $existing $generated
        $r['JWT_SECRET_KEY'] | Should -Be 'old'
        $r['MACHINE_ID'] | Should -Be 'mid'
        $r['FERNET_KEY'] | Should -Be 'fk'
        $r['TZ'] | Should -Be 'UTC'
        $r['APP_BASE_URL'] | Should -Be 'http://10.0.0.5:8000'
        $r['IMAGE_TAG'] | Should -Be '1.1.0'
    }
    It 'does not blank volatile keys when generated lacks them' {
        $r = Merge-SBEnv @{ TZ = 'Asia/Beirut'; X = '1' } @{ Y = '2' }
        $r['TZ'] | Should -Be 'Asia/Beirut'
        $r['X'] | Should -Be '1'
        $r['Y'] | Should -Be '2'
    }
    It 'handles a null existing table' {
        (Merge-SBEnv $null @{ A = '1' })['A'] | Should -Be '1'
    }
}

Describe 'env text' {
    It 'parses ignoring comments and blanks, keeping = in values' {
        $t = "# c`r`n`r`nA=1`nB=x=y`n  # another`nC="
        $h = ConvertFrom-SBEnvText $t
        $h['A'] | Should -Be '1'
        $h['B'] | Should -Be 'x=y'
        $h['C'] | Should -Be ''
        $h.Count | Should -Be 3
    }
    It 'round-trips and preserves ordered insertion' {
        $o = [ordered]@{ Z = '1'; A = 'b=c' }
        $text = ConvertTo-SBEnvText $o
        $text | Should -Be "Z=1`nA=b=c`n"
        $back = ConvertFrom-SBEnvText $text
        $back['A'] | Should -Be 'b=c'
        $back['Z'] | Should -Be '1'
    }
    It 'sorts keys for plain hashtables' {
        ConvertTo-SBEnvText @{ B = '2'; A = '1' } | Should -Be "A=1`nB=2`n"
    }
}

Describe 'Get-SBLanIp' {
    It 'picks the first private non-virtual address' {
        $a = @(
            [pscustomobject]@{ IPAddress = '172.20.0.1'; InterfaceAlias = 'vEthernet (WSL)'; PrefixOrigin = 'Manual' },
            [pscustomobject]@{ IPAddress = '8.8.8.8'; InterfaceAlias = 'Ethernet'; PrefixOrigin = 'Dhcp' },
            [pscustomobject]@{ IPAddress = '192.168.1.20'; InterfaceAlias = 'Ethernet'; PrefixOrigin = 'Dhcp' },
            [pscustomobject]@{ IPAddress = '10.0.0.4'; InterfaceAlias = 'Wi-Fi'; PrefixOrigin = 'Dhcp' }
        )
        Get-SBLanIp $a | Should -Be '192.168.1.20'
    }
    It 'accepts 172.16/12 but not 172.32' {
        Get-SBLanIp @([pscustomobject]@{ IPAddress = '172.32.0.1'; InterfaceAlias = 'Ethernet'; PrefixOrigin = 'Dhcp' }) | Should -Be 'localhost'
        Get-SBLanIp @([pscustomobject]@{ IPAddress = '172.31.0.1'; InterfaceAlias = 'Ethernet'; PrefixOrigin = 'Dhcp' }) | Should -Be '172.31.0.1'
    }
    It 'falls back to localhost' {
        Get-SBLanIp @() | Should -Be 'localhost'
        Get-SBLanIp $null | Should -Be 'localhost'
        Get-SBLanIp @([pscustomobject]@{ IPAddress = '169.254.1.1'; InterfaceAlias = 'Ethernet'; PrefixOrigin = 'WellKnown' }) | Should -Be 'localhost'
    }
}

Describe 'Get-SBBackupsToDelete' {
    It 'returns only the oldest beyond keep and ignores foreign files' {
        $names = @(1..16 | ForEach-Object { 'servicebills-202601{0:00}-0130.zip' -f $_ }) + 'notes.txt'
        $del = @(Get-SBBackupsToDelete $names 14)
        $del.Count | Should -Be 2
        $del | Should -Contain 'servicebills-20260101-0130.zip'
        $del | Should -Contain 'servicebills-20260102-0130.zip'
        $del | Should -Not -Contain 'notes.txt'
    }
    It 'returns nothing when under the limit' {
        @(Get-SBBackupsToDelete @('servicebills-20260101-0130.zip') 14).Count | Should -Be 0
    }
}

Describe 'state file' {
    BeforeEach {
        $script:dir = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory $script:dir | Out-Null
    }
    AfterEach { Remove-Item $script:dir -Recurse -Force -ErrorAction SilentlyContinue }
    It 'defaults when missing' {
        $s = Read-SBState (Join-Path $script:dir 'state.json')
        $s.current | Should -BeNullOrEmpty
        $s.previous | Should -BeNullOrEmpty
        $s.last_update | Should -BeNullOrEmpty
        $s.ContainsKey('last_update') | Should -BeTrue
    }
    It 'round-trips without BOM or leftover tmp' {
        $p = Join-Path $script:dir 'state.json'
        Write-SBState $p @{ current = '1.2.3'; previous = '1.2.2'; last_update = (New-SBUpdateResult 'ok' '1.2.3' 'done') }
        $bytes = [IO.File]::ReadAllBytes($p)
        ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) | Should -BeFalse
        Test-Path "$p.tmp" | Should -BeFalse
        $s = Read-SBState $p
        $s.current | Should -Be '1.2.3'
        $s.previous | Should -Be '1.2.2'
        $s.last_update.status | Should -Be 'ok'
        $s.last_update.target | Should -Be '1.2.3'
    }
}

Describe 'New-SBUpdateResult' {
    It 'has fields and ISO UTC timestamp' {
        $r = New-SBUpdateResult 'failed' '1.0.1' 'boom'
        $r.status | Should -Be 'failed'
        $r.target | Should -Be '1.0.1'
        $r.message | Should -Be 'boom'
        $r.at | Should -Match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
    }
}

Describe 'Write-SBLog' {
    It 'appends to a dated log under root\logs' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        $old = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = $dir
        try {
            Write-SBLog 'unit' 'hello'
            $f = @(Get-ChildItem (Join-Path $dir 'logs') -Filter 'unit-*.log')
            $f.Count | Should -Be 1
            (Get-Content $f[0].FullName -Raw) | Should -Match '^\[\d{4}-\d{2}-\d{2}T[\d:]+Z\] hello'
        }
        finally {
            $env:SERVICEBILLS_ROOT = $old
            Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Docker wrappers' {
    It 'Test-SBDockerEngine is false when docker fails' {
        Mock Invoke-SBDockerRaw { $global:LASTEXITCODE = 1; return @() }
        Test-SBDockerEngine | Should -BeFalse
    }
    It 'Test-SBDockerEngine is true when docker info succeeds' {
        Mock Invoke-SBDockerRaw { $global:LASTEXITCODE = 0; return @('ok') }
        Test-SBDockerEngine | Should -BeTrue
    }
    It 'Invoke-SBDocker throws with output on non-zero exit' {
        Mock Invoke-SBDockerRaw { $global:LASTEXITCODE = 3; return @('bad thing') }
        { Invoke-SBDocker @('ps') } | Should -Throw '*bad thing*'
    }
    It 'Invoke-SBDocker returns stdout on success' {
        Mock Invoke-SBDockerRaw { $global:LASTEXITCODE = 0; return @('a', 'b') }
        @(Invoke-SBDocker @('ps')) | Should -Be @('a', 'b')
    }
    It 'Wait-SBHealth returns true on healthy poll' {
        Mock Test-SBHealthOnce { $true }
        Mock Start-Sleep {}
        Wait-SBHealth -Seconds 10 | Should -BeTrue
    }
    It 'Wait-SBHealth returns false after timeout' {
        Mock Test-SBHealthOnce { $false }
        Mock Start-Sleep {}
        Wait-SBHealth -Seconds 10 | Should -BeFalse
    }
}
