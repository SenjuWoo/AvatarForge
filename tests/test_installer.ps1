$ErrorActionPreference='Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'tools\Install.ps1')
$reviewedCatalog=$catalog

function Assert-True($Condition,$Message) { if(-not $Condition){throw $Message} }
function Assert-Throws($Action,$Pattern) {
    try { & $Action } catch {
        if($_.Exception.Message -match $Pattern){return}
        throw
    }
    throw "Expected failure matching: $Pattern"
}

# Exercise the real installer with a small local archive; no network or personal configuration.
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('AvatarForgeInstallerTests-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    $runtimeRoot=Join-Path $testRoot 'runtime'
    $cacheRoot=Join-Path $runtimeRoot 'downloads'
    $receiptRoot=Join-Path $runtimeRoot 'receipts'
    $source=Join-Path $testRoot 'source'
    New-Item -ItemType Directory -Path $source,$cacheRoot -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $source 'marker.txt'),'official fixture')
    [IO.File]::WriteAllText((Join-Path $source 'license.txt'),'fixture license')
    $archive=Join-Path $testRoot 'fixture.zip'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($source,$archive)
    $hash=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $archive -Destination (Join-Path $cacheRoot ($hash+'.zip'))
    $entry=[pscustomobject]@{name='Installer fixture';version='1.0.0';license='MIT';sha256=$hash;download_url='https://example.invalid/not-used.zip'}
    $catalog=[pscustomobject]@{dependencies=[pscustomobject]@{fixture=$entry}}
    $destination=Join-Path $runtimeRoot 'fixture'
    Install-Portable fixture $destination marker.txt
    $receipt=Get-Receipt fixture
    Assert-True (Test-Receipt $receipt fixture $destination $entry) 'Fresh receipt did not validate.'
    Assert-True ($receipt.schema_version -eq 2 -and $receipt.files.Count -eq 2) 'Receipt did not account for all payload files.'
    Install-Portable fixture $destination marker.txt

    # A marker alone must never classify a partial or modified installation as ready.
    [IO.File]::WriteAllText((Join-Path $destination 'license.txt'),'modified user bytes')
    Assert-True (-not(Test-Receipt $receipt fixture $destination $entry)) 'Modified payload was accepted.'
    Write-InstallState ready $null
    $state=Get-Content -Raw -LiteralPath (Join-Path $runtimeRoot 'install-state.json') | ConvertFrom-Json
    Assert-True (-not $state.installed.fixture) 'Install state advertised a failed payload verification.'
    Install-Portable fixture $destination marker.txt
    $backups=@(Get-ChildItem -LiteralPath $runtimeRoot -Directory -Filter 'fixture.backup-*')
    Assert-True ($backups.Count -eq 1) 'Repair did not preserve the previous installation.'
    Assert-True ([IO.File]::ReadAllText((Join-Path $backups[0].FullName 'license.txt')) -eq 'modified user bytes') 'Repair altered the preserved bytes.'
    Assert-True (Test-Receipt (Get-Receipt fixture) fixture $destination $entry) 'Repaired payload did not validate.'
    Remove-Item -LiteralPath (Join-Path $destination 'license.txt')
    Assert-True (-not(Test-Receipt (Get-Receipt fixture) fixture $destination $entry)) 'Partial payload was accepted.'

    $unknown=Join-Path $runtimeRoot 'unknown'
    New-Item -ItemType Directory -Path $unknown | Out-Null
    [IO.File]::WriteAllText((Join-Path $unknown 'marker.txt'),'unknown bytes')
    Assert-Throws { Install-Portable fixture $unknown marker.txt } 'Unverified files occupy'
    Assert-True ([IO.File]::ReadAllText((Join-Path $unknown 'marker.txt')) -eq 'unknown bytes') 'Unknown files were changed.'

    $unsafe=Join-Path $testRoot 'unsafe.zip'
    $zip=[IO.Compression.ZipFile]::Open($unsafe,[IO.Compression.ZipArchiveMode]::Create)
    try { [void]$zip.CreateEntry('../escape.txt') } finally { $zip.Dispose() }
    Assert-Throws { Expand-Checked $unsafe (Join-Path $testRoot 'unsafe-output') } 'Unsafe dependency archive path'
    $duplicate=Join-Path $testRoot 'duplicate.zip'
    $zip=[IO.Compression.ZipFile]::Open($duplicate,[IO.Compression.ZipArchiveMode]::Create)
    try { [void]$zip.CreateEntry('file.txt'); [void]$zip.CreateEntry('FILE.txt') } finally { $zip.Dispose() }
    Assert-Throws { Expand-Checked $duplicate (Join-Path $testRoot 'duplicate-output') } 'duplicate file paths'

    # Real SDK/runtime predicates, including newer SDKs without the required net8 runtime.
    # Use an explicitly enabled caller value so a test harness's environment
    # cannot hide an absent installer guard.
    $callerSettings=@{DOTNET_GENERATE_ASPNET_CERTIFICATE='true';DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='true';DOTNET_CLI_TELEMETRY_OPTOUT='false'}
    $previousSettings=@{}
    foreach($name in $callerSettings.Keys){$previousSettings[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
    try{
        foreach($name in $callerSettings.Keys){[Environment]::SetEnvironmentVariable($name,$callerSettings[$name],'Process')}
        $script:guardedDotnetCalls=0
        function Assert-DotnetCertificateGuard {
            Assert-True ($env:DOTNET_GENERATE_ASPNET_CERTIFICATE -ceq 'false') 'SDK invocation did not receive the literal false certificate guard.'
            Assert-True ($env:DOTNET_ADD_GLOBAL_TOOLS_TO_PATH -ceq 'false') 'SDK invocation did not suppress global tools PATH changes.'
            Assert-True ($env:DOTNET_CLI_TELEMETRY_OPTOUT -ceq 'true') 'SDK invocation did not disable CLI telemetry.'
            $script:guardedDotnetCalls++
        }
        function Assert-DotnetCallerRestored {
            foreach($name in $callerSettings.Keys){Assert-True ([Environment]::GetEnvironmentVariable($name,'Process') -ceq $callerSettings[$name]) "SDK invocation changed the caller setting: $name"}
        }
        function Test-Dotnet6 { param($Mode) Assert-DotnetCertificateGuard; $global:LASTEXITCODE=0; if($Mode -eq '--list-sdks'){'6.0.428 [fixture]'}else{'Microsoft.NETCore.App 6.0.36 [fixture]'} }
        function Test-Dotnet7 { param($Mode) Assert-DotnetCertificateGuard; $global:LASTEXITCODE=0; if($Mode -eq '--list-sdks'){'7.0.410 [fixture]'}else{'Microsoft.NETCore.App 7.0.20 [fixture]'} }
        function Test-Dotnet8 { param($Mode) Assert-DotnetCertificateGuard; $global:LASTEXITCODE=0; if($Mode -eq '--list-sdks'){'8.0.425 [fixture]'}else{'Microsoft.NETCore.App 8.0.31 [fixture]'} }
        function Test-Dotnet10Only { param($Mode) Assert-DotnetCertificateGuard; $global:LASTEXITCODE=0; if($Mode -eq '--list-sdks'){'10.0.301 [fixture]'}else{'Microsoft.NETCore.App 10.0.9 [fixture]'} }
        Assert-True (-not(Test-DotnetForVpm Test-Dotnet6)) '.NET6 was accepted for the net8 VPM tool.'
        Assert-True (-not(Test-DotnetForVpm Test-Dotnet7)) '.NET7 was accepted for the net8 VPM tool.'
        Assert-True (Test-DotnetForVpm Test-Dotnet8) '.NET8 SDK/runtime was rejected.'
        Assert-True (-not(Test-DotnetForVpm Test-Dotnet10Only)) 'A newer SDK without the net8 runtime was accepted.'
        Assert-True ($script:guardedDotnetCalls -eq 8) 'An SDK/runtime probe bypassed the certificate guard.'
        Assert-DotnetCallerRestored

        # Verify an actual child receives the flag, and preserve its nonzero
        # exit code as well as the caller setting on ordinary and thrown exits.
        $captureChild=Join-Path $testRoot 'capture-certificate-child.ps1'
        $captureOutput=Join-Path $testRoot 'captured-certificate-setting.txt'
        [IO.File]::WriteAllText($captureChild,"param([string]`$Output)`n`$values=@([Environment]::GetEnvironmentVariable('DOTNET_GENERATE_ASPNET_CERTIFICATE','Process'),[Environment]::GetEnvironmentVariable('DOTNET_ADD_GLOBAL_TOOLS_TO_PATH','Process'),[Environment]::GetEnvironmentVariable('DOTNET_CLI_TELEMETRY_OPTOUT','Process'))`n[IO.File]::WriteAllLines(`$Output,`$values)`nexit 23`n")
        Invoke-ScopedDotnet (Join-Path $PSHOME 'powershell.exe') @('-NoProfile','-NonInteractive','-File',$captureChild,$captureOutput)
        Assert-True ($LASTEXITCODE -eq 23) 'Guarded SDK invocation lost the child exit code.'
        Assert-True (([IO.File]::ReadAllLines($captureOutput) -join ',') -ceq 'false,false,true') 'A real child did not inherit all three first-use protections.'
        Assert-DotnetCallerRestored
        function Test-ThrowingDotnet { Assert-DotnetCertificateGuard; throw 'CERTIFICATE_GUARD_CHILD_FAILURE' }
        Assert-Throws { Invoke-ScopedDotnet Test-ThrowingDotnet @() } 'CERTIFICATE_GUARD_CHILD_FAILURE'
        Assert-DotnetCallerRestored
        foreach($name in $callerSettings.Keys){[Environment]::SetEnvironmentVariable($name,$null,'Process')}
        Invoke-ScopedDotnet (Join-Path $PSHOME 'powershell.exe') @('-NoProfile','-NonInteractive','-File',$captureChild,$captureOutput)
        foreach($name in $callerSettings.Keys){Assert-True ($null -eq [Environment]::GetEnvironmentVariable($name,'Process')) "The guard retained a setting that was originally absent: $name"}
    }finally{
        foreach($name in $callerSettings.Keys){[Environment]::SetEnvironmentVariable($name,$previousSettings[$name],'Process')}
    }

    # Verify incompatible hosts route to the private SDK before any VPM installation.
    $script:hostDotnet='Test-Dotnet6'
    function Get-Command { param($Name,$ErrorAction) [pscustomobject]@{Source=$script:hostDotnet} }
    function Install-Portable { param($Id,$Destination,$Marker) if($Id -eq 'dotnet8'){throw 'PRIVATE_SDK_INSTALL_ATTEMPTED'}; throw "Unexpected installation: $Id" }
    Assert-Throws { Get-VpmDotnet } 'PRIVATE_SDK_INSTALL_ATTEMPTED'
    $script:hostDotnet='Test-Dotnet7'
    Assert-Throws { Get-VpmDotnet } 'PRIVATE_SDK_INSTALL_ATTEMPTED'
    $script:hostDotnet='Test-Dotnet8'
    Assert-True ((Get-VpmDotnet) -eq 'Test-Dotnet8') 'Compatible .NET8 did not use the existing runtime.'

    # Run the real component-selection path with package writes intercepted.
    # An optional native reducer left from an older setup must stay untouched.
    $catalog=$reviewedCatalog
    $Component='optimization'
    $script:componentInstalls=@()
    function Install-Portable {
        param($Id,$Destination,$Marker)
        $script:componentInstalls+=@{id=$Id;destination=$Destination;marker=$Marker}
    }
    function Write-InstallState { param($Status,$Failure) Assert-True ($Status -eq 'ready' -and -not $Failure) 'Optimization setup failed.' }
    $nativeFolder=Join-Path $runtimeRoot 'unity-packages\com.ramtype0.meshia.mesh-simplification'
    New-Item -ItemType Directory -Path $nativeFolder -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $nativeFolder 'preserved.txt'),'existing optional reducer')
    Invoke-AvatarForgeInstall
    $installedIds=@($script:componentInstalls | ForEach-Object {$_.id})
    Assert-True ($installedIds -contains 'unity_mesh_simplifier') 'The managed reducer was omitted from automatic setup.'
    Assert-True ($installedIds -contains 'ndmf' -and $installedIds -contains 'modular_avatar') 'Supporting assembly packages were omitted.'
    Assert-True ($installedIds -notcontains 'meshia') 'The optional native reducer was automatically installed.'
    $managed=@($script:componentInstalls | Where-Object {$_.id -eq 'unity_mesh_simplifier'})
    Assert-True ($managed.Count -eq 1 -and $managed[0].marker -eq 'package.json') 'Managed package staging is ambiguous.'
    Assert-True ($managed[0].destination -eq (Join-Path $runtimeRoot 'unity-packages\com.whinarn.unitymeshsimplifier')) 'Managed package destination differs from its Unity package name.'
    Assert-True ([IO.File]::ReadAllText((Join-Path $nativeFolder 'preserved.txt')) -eq 'existing optional reducer') 'Setup changed an existing optional reducer.'
    Assert-True ($catalog.dependencies.meshia.install_mode -eq 'manual-link-only') 'Native reducer catalog incorrectly advertises automatic installation.'
    Write-Host 'AVATARFORGE_INSTALLER_TESTS_PASS: verified receipts, partial/stale payloads, preserved repair, unknown installs, ZIP paths, scoped certificate guard, .NET6/7 fallback and managed optimization selection.'
} finally {
    $resolved=[IO.Path]::GetFullPath($testRoot)
    $tempPrefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if(-not $resolved.StartsWith($tempPrefix,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolved -Leaf) -notlike 'AvatarForgeInstallerTests-*'){throw 'Refusing to clean an unverified test directory.'}
    if(Test-Path -LiteralPath $resolved){Remove-Item -LiteralPath $resolved -Recurse -Force}
}
