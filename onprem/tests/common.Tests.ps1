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

Describe 'Get-SBLanIp with a preferred interface' {
    BeforeAll {
        $script:Addrs = @(
            [pscustomobject]@{ IPAddress = '192.168.56.1'; InterfaceAlias = 'Ethernet 2'; InterfaceIndex = 5 },
            [pscustomobject]@{ IPAddress = '192.168.1.20'; InterfaceAlias = 'Wi-Fi'; InterfaceIndex = 12 },
            [pscustomobject]@{ IPAddress = '10.0.0.4'; InterfaceAlias = 'Ethernet'; InterfaceIndex = 3 }
        )
    }
    It 'prefers the default-route interface over list order' {
        Get-SBLanIp -addresses $script:Addrs -PreferredInterfaceIndex 12 | Should -Be '192.168.1.20'
        Get-SBLanIp -addresses $script:Addrs -PreferredInterfaceIndex 3 | Should -Be '10.0.0.4'
    }
    It 'falls back to the normal order when the preferred interface has no private address' {
        Get-SBLanIp -addresses $script:Addrs -PreferredInterfaceIndex 99 | Should -Be '192.168.56.1'
    }
    It 'works without a preferred interface (unchanged behaviour)' {
        Get-SBLanIp -addresses $script:Addrs | Should -Be '192.168.56.1'
    }
    It 'Get-SBLanIpLive passes the default-route interface index' {
        Mock Get-NetIPAddress { $script:Addrs }
        Mock Get-SBDefaultRouteInterfaceIndex { 12 }
        Get-SBLanIpLive | Should -Be '192.168.1.20'
    }
}

Describe 'TLS' {
    It 'keeps TLS 1.2 enabled after loading the library' {
        ([int][Net.ServicePointManager]::SecurityProtocol -band [int][Net.SecurityProtocolType]::Tls12) | Should -Be ([int][Net.SecurityProtocolType]::Tls12)
    }
}

Describe 'Get-SBStatePath' {
    It 'is state\state.json under the root' {
        $old = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = 'C:\x\root'
        try { Get-SBStatePath | Should -Be 'C:\x\root\state\state.json' }
        finally { $env:SERVICEBILLS_ROOT = $old }
    }
}

Describe 'Write-SBLog on an existing file' {
    It 'appends to an existing log without failing or truncating' {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        $old = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = $dir
        try {
            Write-SBLog 'unit' 'first'
            $f = @(Get-ChildItem (Join-Path $dir 'logs') -Filter 'unit-*.log')[0]
            $f.IsReadOnly | Should -BeFalse
            { Write-SBLog 'unit' 'second' } | Should -Not -Throw
            $text = Get-Content $f.FullName -Raw
            $text | Should -Match 'first'
            $text | Should -Match 'second'
        }
        finally {
            $env:SERVICEBILLS_ROOT = $old
            Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Resolve-SBImageTag' {
    It 'uses the requested tag on a fresh install' { Resolve-SBImageTag -Existing '' -Requested '1.0.0' | Should -Be '1.0.0' }
    It 'never lowers a SemVer tag' { Resolve-SBImageTag -Existing '1.4.0' -Requested '1.2.0' | Should -Be '1.4.0' }
    It 'raises to a newer requested tag' { Resolve-SBImageTag -Existing '1.2.0' -Requested '1.4.0' | Should -Be '1.4.0' }
    It 'keeps a SemVer tag when the requested one is latest' { Resolve-SBImageTag -Existing '1.4.0' -Requested 'latest' | Should -Be '1.4.0' }
    It 'replaces an unknown existing tag' { Resolve-SBImageTag -Existing 'latest' -Requested '1.0.0' | Should -Be '1.0.0' }
    It 'stays on latest when both are unknown' { Resolve-SBImageTag -Existing 'latest' -Requested 'latest' | Should -Be 'latest' }
}

Describe 'permission wrappers' {
    BeforeEach {
        $global:SBIcacls = New-Object System.Collections.ArrayList
        Mock Invoke-SBIcacls { [void]$global:SBIcacls.Add(($Arguments -join ' ')) }
        Mock Get-SBCurrentUserSid { 'S-1-5-21-1-2-3-1001' }
    }
    AfterEach { Remove-Variable -Name SBIcacls -Scope Global -ErrorAction SilentlyContinue }
    It 'Grant-SBRootAccess grants the user Modify recursively' {
        Grant-SBRootAccess -Root 'C:\ProgramData\ServiceBills'
        @($global:SBIcacls) | Should -Contain 'C:\ProgramData\ServiceBills /grant *S-1-5-21-1-2-3-1001:(OI)(CI)M /T /C'
    }
    It 'Protect-SBEnvFile removes inheritance and grants only SYSTEM, Administrators and the user' {
        Protect-SBEnvFile -EnvPath 'C:\r\.env'
        @($global:SBIcacls) | Should -Contain 'C:\r\.env /inheritance:r /grant:r *S-1-5-18:F *S-1-5-32-544:F *S-1-5-21-1-2-3-1001:M'
    }
}

Describe 'Set-SBScheduledTask' {
    It 'registers with battery / missed-run / 2 hour settings' {
        $global:SBTaskSettings = $null
        Mock Register-ScheduledTask { $global:SBTaskSettings = $Settings }
        try {
            Set-SBScheduledTask -Name 'ServiceBills Backup' -At '01:30' -ScriptName 'backup.ps1' -ScriptArguments '-Quiet'
            Should -Invoke Register-ScheduledTask -Times 1 -Exactly -ParameterFilter { $TaskName -eq 'ServiceBills Backup' }
            $s = $global:SBTaskSettings
            $s | Should -Not -BeNullOrEmpty
            $s.StartWhenAvailable | Should -BeTrue
            $s.DisallowStartIfOnBatteries | Should -BeFalse
            $s.StopIfGoingOnBatteries | Should -BeFalse
            $s.ExecutionTimeLimit | Should -Be 'PT2H'
        }
        finally { Remove-Variable -Name SBTaskSettings -Scope Global -ErrorAction SilentlyContinue }
    }
}

Describe 'Set-SBDockerAutoStart' {
    BeforeEach {
        $script:AppData = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path (Join-Path $script:AppData 'Docker') -Force | Out-Null
        Mock Set-SBRunKey { }
    }
    AfterEach { Remove-Item $script:AppData -Recurse -Force -ErrorAction SilentlyContinue }

    It 'sets AutoStart in settings-store.json (UTF-8 no BOM), keeping other keys' {
        $p = Join-Path $script:AppData 'Docker\settings-store.json'
        [IO.File]::WriteAllText($p, '{"AutoStart": false, "Theme": "dark"}')
        Set-SBDockerAutoStart -AppData $script:AppData | Should -Be 'settings-store.json'
        $j = [IO.File]::ReadAllText($p) | ConvertFrom-Json
        $j.AutoStart | Should -BeTrue
        $j.Theme | Should -Be 'dark'
        $b = [IO.File]::ReadAllBytes($p)
        ($b[0] -eq 0xEF -and $b[1] -eq 0xBB) | Should -BeFalse
        Should -Invoke Set-SBRunKey -Times 0 -Exactly
    }
    It 'adds AutoStart when the key is missing' {
        $p = Join-Path $script:AppData 'Docker\settings-store.json'
        [IO.File]::WriteAllText($p, '{"Theme": "dark"}')
        [void](Set-SBDockerAutoStart -AppData $script:AppData)
        ([IO.File]::ReadAllText($p) | ConvertFrom-Json).AutoStart | Should -BeTrue
    }
    It 'uses legacy settings.json (matching the existing key case) when there is no settings-store.json' {
        $p = Join-Path $script:AppData 'Docker\settings.json'
        [IO.File]::WriteAllText($p, '{"autoStart": false}')
        Set-SBDockerAutoStart -AppData $script:AppData | Should -Be 'settings.json'
        ([IO.File]::ReadAllText($p) | ConvertFrom-Json).autoStart | Should -BeTrue
    }
    It 'prefers settings-store.json when both exist' {
        [IO.File]::WriteAllText((Join-Path $script:AppData 'Docker\settings-store.json'), '{}')
        [IO.File]::WriteAllText((Join-Path $script:AppData 'Docker\settings.json'), '{"autoStart": false}')
        Set-SBDockerAutoStart -AppData $script:AppData | Should -Be 'settings-store.json'
        ([IO.File]::ReadAllText((Join-Path $script:AppData 'Docker\settings.json')) | ConvertFrom-Json).autoStart | Should -BeFalse
    }
    It 'falls back to the HKCU Run value when no settings file exists' {
        Set-SBDockerAutoStart -AppData $script:AppData -ExePath 'C:\Program Files\Docker\Docker\Docker Desktop.exe' | Should -Be 'run-key'
        Should -Invoke Set-SBRunKey -Times 1 -Exactly -ParameterFilter {
            $Name -eq 'Docker Desktop' -and $Value -eq '"C:\Program Files\Docker\Docker\Docker Desktop.exe"'
        }
    }
    It 'falls back to the Run value when the settings file is not valid JSON' {
        [IO.File]::WriteAllText((Join-Path $script:AppData 'Docker\settings-store.json'), '{not json')
        Set-SBDockerAutoStart -AppData $script:AppData | Should -Be 'run-key'
        Should -Invoke Set-SBRunKey -Times 1 -Exactly
    }
}

Describe 'zip helpers and backup keys' {
    BeforeEach {
        $script:Work = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path (Join-Path $script:Work 'src\uploads\empty') -Force | Out-Null
        Set-Content -Path (Join-Path $script:Work 'src\db.dump') -Value 'dump'
        Set-Content -Path (Join-Path $script:Work 'src\uploads\a.txt') -Value 'a'
        [IO.File]::WriteAllText((Join-Path $script:Work 'src\env.keys'), "FERNET_KEY=fk1`nJWT_SECRET_KEY=jw1`n")
    }
    AfterEach { Remove-Item $script:Work -Recurse -Force -ErrorAction SilentlyContinue }

    It 'New-SBZip / Expand-SBZip round-trip files and empty folders, replacing an existing zip' {
        $zip = Join-Path $script:Work 'out.zip'
        Set-Content -Path $zip -Value 'stale'
        New-SBZip -SourceDir (Join-Path $script:Work 'src') -ZipPath $zip
        $dest = Join-Path $script:Work 'dest'
        New-Item -ItemType Directory -Path $dest | Out-Null
        Expand-SBZip -ZipPath $zip -Destination $dest
        Test-Path (Join-Path $dest 'db.dump') | Should -BeTrue
        Test-Path (Join-Path $dest 'uploads\a.txt') | Should -BeTrue
        Test-Path (Join-Path $dest 'uploads\empty') | Should -BeTrue
    }
    It 'Read-SBBackupKeys returns the two keys, or $null for an old backup without env.keys' {
        $zip = Join-Path $script:Work 'k.zip'
        New-SBZip -SourceDir (Join-Path $script:Work 'src') -ZipPath $zip
        $k = Read-SBBackupKeys -Zip $zip
        $k['FERNET_KEY'] | Should -Be 'fk1'
        $k['JWT_SECRET_KEY'] | Should -Be 'jw1'
        Remove-Item (Join-Path $script:Work 'src\env.keys')
        $zip2 = Join-Path $script:Work 'k2.zip'
        New-SBZip -SourceDir (Join-Path $script:Work 'src') -ZipPath $zip2
        Read-SBBackupKeys -Zip $zip2 | Should -BeNullOrEmpty
    }
    It 'Test-SBKeysDiffer compares backup keys with the current env' {
        $b = [ordered]@{ FERNET_KEY = 'a'; JWT_SECRET_KEY = 'b' }
        Test-SBKeysDiffer -BackupKeys $b -CurrentEnv ([ordered]@{ FERNET_KEY = 'a'; JWT_SECRET_KEY = 'b'; X = 'y' }) | Should -BeFalse
        Test-SBKeysDiffer -BackupKeys $b -CurrentEnv ([ordered]@{ FERNET_KEY = 'a'; JWT_SECRET_KEY = 'other' }) | Should -BeTrue
        Test-SBKeysDiffer -BackupKeys $b -CurrentEnv $null | Should -BeTrue
        Test-SBKeysDiffer -BackupKeys $null -CurrentEnv ([ordered]@{ FERNET_KEY = 'a' }) | Should -BeFalse
    }
    It 'Select-SBKeys keeps only FERNET_KEY and JWT_SECRET_KEY' {
        $k = Select-SBKeys ([ordered]@{ FERNET_KEY = 'a'; JWT_SECRET_KEY = 'b'; POSTGRES_PASSWORD = 'p'; TZ = 'UTC' })
        @($k.Keys) | Should -Be @('FERNET_KEY', 'JWT_SECRET_KEY')
    }
    It 'Set-SBEnvKeys merges keys into .env preserving the rest' {
        $old = $env:SERVICEBILLS_ROOT
        $env:SERVICEBILLS_ROOT = Join-Path $script:Work 'root'
        try {
            New-Item -ItemType Directory -Path $env:SERVICEBILLS_ROOT -Force | Out-Null
            [IO.File]::WriteAllText((Join-Path $env:SERVICEBILLS_ROOT '.env'), "FERNET_KEY=old`nPOSTGRES_PASSWORD=pw`n")
            Set-SBEnvKeys -Keys ([ordered]@{ FERNET_KEY = 'new'; JWT_SECRET_KEY = 'j' })
            $m = ConvertFrom-SBEnvText ([IO.File]::ReadAllText((Join-Path $env:SERVICEBILLS_ROOT '.env')))
            $m['FERNET_KEY'] | Should -Be 'new'
            $m['JWT_SECRET_KEY'] | Should -Be 'j'
            $m['POSTGRES_PASSWORD'] | Should -Be 'pw'
        }
        finally { $env:SERVICEBILLS_ROOT = $old }
    }
}
