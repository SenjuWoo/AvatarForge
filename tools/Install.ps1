[CmdletBinding()]
param([ValidateSet('launcher','core','extended','unity','optimization','all')][string]$Component='core')
$ErrorActionPreference='Stop'
# Python launched by PowerShell 7 can inherit its incompatible module search paths.
# Load Windows PowerShell's own builtins explicitly inside this installer process.
if($PSVersionTable.PSEdition -eq 'Desktop'){
    foreach($moduleName in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Management','Microsoft.PowerShell.Archive')){
        $moduleManifest=[IO.Path]::Combine($PSHOME,'Modules',$moduleName,($moduleName+'.psd1'))
        Import-Module $moduleManifest -Force
    }
}
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$projectRoot=Split-Path $PSScriptRoot -Parent
$runtimeRoot=Join-Path $projectRoot '.runtime'
$cacheRoot=Join-Path $runtimeRoot 'downloads'
$receiptRoot=Join-Path $runtimeRoot 'receipts'
$catalog=Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'avatarforge\dependencies.json') | ConvertFrom-Json
$script:externalTools=@()

function Assert-OwnedPath($Path,$Parent) {
    $full=[IO.Path]::GetFullPath($Path)
    $parentFull=[IO.Path]::GetFullPath($Parent).TrimEnd('\')+'\'
    if(-not $full.StartsWith($parentFull,[StringComparison]::OrdinalIgnoreCase)){throw "Path leaves the owned directory: $Path"}
    return $full
}

function Remove-Stage($Path) {
    $full=Assert-OwnedPath $Path $cacheRoot
    if(Test-Path -LiteralPath $full){Remove-Item -LiteralPath $full -Recurse -Force}
}

function Get-ArchiveDigest($Entry) {
    if($Entry.sha256){return @{algorithm='SHA256';value=$Entry.sha256.ToLowerInvariant()}}
    if($Entry.sha512){return @{algorithm='SHA512';value=$Entry.sha512.ToLowerInvariant()}}
    throw "Missing verified checksum for $($Entry.name). Use its official dependency link."
}

function Get-PinnedArchive($Entry) {
    $digest=Get-ArchiveDigest $Entry
    if(-not $Entry.download_url){throw "Missing verified download URL for $($Entry.name)."}
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
    $archive=Join-Path $cacheRoot ($digest.value+'.zip')
    if((Test-Path -LiteralPath $archive) -and (Get-FileHash -LiteralPath $archive -Algorithm $digest.algorithm).Hash.ToLowerInvariant() -ne $digest.value){
        Remove-Item -LiteralPath $archive
    }
    if(-not (Test-Path -LiteralPath $archive)){
        Write-Host "Downloading $($Entry.name) $($Entry.version)..."
        $partial=$archive+'.partial-'+[Guid]::NewGuid().ToString('N')
        try{
            Invoke-WebRequest -UseBasicParsing -Uri $Entry.download_url -OutFile $partial
            if((Get-FileHash -LiteralPath $partial -Algorithm $digest.algorithm).Hash.ToLowerInvariant() -ne $digest.value){throw "Checksum failed: $($Entry.name)."}
            Move-Item -LiteralPath $partial -Destination $archive
        }finally{if(Test-Path -LiteralPath $partial){Remove-Item -LiteralPath $partial -Force}}
    }
    return $archive
}

function Expand-Checked($Archive,$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip=[IO.Compression.ZipFile]::OpenRead($Archive)
    $destinationFull=[IO.Path]::GetFullPath($Destination).TrimEnd('\')+'\'
    $seen=@{}
    try{
        foreach($entry in $zip.Entries){
            $name=$entry.FullName.Replace('/','\')
            if(-not $name -or $name -match '(^|\\)\.\.(\\|$)|:|^\\'){throw 'Unsafe dependency archive path.'}
            $entryFull=[IO.Path]::GetFullPath((Join-Path $Destination $name))
            if(-not $entryFull.StartsWith($destinationFull,[StringComparison]::OrdinalIgnoreCase)){throw 'Dependency archive leaves install directory.'}
            if($entry.Name){
                if($seen.ContainsKey($entryFull)){throw 'Dependency archive contains duplicate file paths.'}
                $seen[$entryFull]=$true
            }
            if(($entry.ExternalAttributes -shr 16 -band 0xF000) -eq 0xA000){throw 'Dependency archive contains a symbolic link.'}
        }
    }finally{$zip.Dispose()}
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination
}

function Get-PayloadFiles($Folder) {
    if(-not (Test-Path -LiteralPath $Folder -PathType Container)){throw "Install directory is missing: $Folder"}
    if((Get-Item -LiteralPath $Folder -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Install directory is a link: $Folder"}
    $pending=New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push([IO.Path]::GetFullPath($Folder))
    while($pending.Count){
        foreach($item in Get-ChildItem -LiteralPath $pending.Pop() -Force){
            if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Dependency contains a filesystem link: $($item.FullName)"}
            if($item.PSIsContainer){$pending.Push($item.FullName)}else{$item}
        }
    }
}

function Get-PayloadManifest($Folder) {
    $prefix=[IO.Path]::GetFullPath($Folder).TrimEnd('\')+'\'
    foreach($file in @(Get-PayloadFiles $Folder | Sort-Object FullName)){
        $relative=$file.FullName.Substring($prefix.Length).Replace('\','/')
        # Blender may generate these after a verified installation. They are not source payload.
        if($relative -match '(^|/)__pycache__/'){continue}
        [ordered]@{path=$relative;size_bytes=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
}

function Test-Payload($Folder,$Manifest) {
    try{
        $expected=@{}
        foreach($record in @($Manifest)){
            $relative=[string]$record.path
            if(-not $relative -or $relative -match '(^|/)\.\.(/|$)|:|^[/\\]' -or $expected.ContainsKey($relative)){return $false}
            $expected[$relative]=$record
        }
        if(-not $expected.Count){return $false}
        $actual=@(Get-PayloadFiles $Folder)
        $prefix=[IO.Path]::GetFullPath($Folder).TrimEnd('\')+'\'
        foreach($file in $actual){
            $relative=$file.FullName.Substring($prefix.Length).Replace('\','/')
            if($relative -match '(^|/)__pycache__/'){continue}
            if(-not $expected.ContainsKey($relative)){return $false}
            $record=$expected[$relative]
            if($file.Length -ne $record.size_bytes -or (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $record.sha256){return $false}
            $expected.Remove($relative)
        }
        return $expected.Count -eq 0
    }catch{return $false}
}

function Get-Receipt($Id) {
    $path=Join-Path $receiptRoot ($Id+'.json')
    if(-not (Test-Path -LiteralPath $path)){return $null}
    try{return Get-Content -Raw -LiteralPath $path | ConvertFrom-Json}catch{return $null}
}

function Test-ReceiptLocation($Receipt,$Id,$Destination) {
    if(-not $Receipt -or $Receipt.schema_version -ne 2 -or $Receipt.id -ne $Id){return $false}
    try{
        $full=Assert-OwnedPath $Destination $runtimeRoot
        $relative=$full.Substring([IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\').Length+1).Replace('\','/')
        return ([IO.Path]::GetFullPath($Receipt.destination) -eq $full -or $Receipt.destination_relative -eq $relative)
    }catch{return $false}
}

function Test-Receipt($Receipt,$Id,$Destination,$Entry) {
    if(-not $Receipt -or $Receipt.schema_version -ne 2 -or $Receipt.id -ne $Id -or $Receipt.version -ne $Entry.version){return $false}
    $digest=Get-ArchiveDigest $Entry
    if($Receipt.archive_algorithm -ne $digest.algorithm -or $Receipt.archive_digest -ne $digest.value){return $false}
    if(-not(Test-ReceiptLocation $Receipt $Id $Destination)){return $false}
    return Test-Payload $Destination $Receipt.files
}

function Write-Receipt($Id,$Entry,$Destination,$Marker,$Manifest,$Adopted,$Verification='archive-payload') {
    New-Item -ItemType Directory -Path $receiptRoot -Force | Out-Null
    $digest=Get-ArchiveDigest $Entry
    $receipt=[ordered]@{
        schema_version=2;id=$Id;version=$Entry.version;license=$Entry.license
        destination=[IO.Path]::GetFullPath($Destination);marker=$Marker
        destination_relative=([IO.Path]::GetFullPath($Destination).Substring([IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\').Length+1).Replace('\','/'))
        archive_algorithm=$digest.algorithm;archive_digest=$digest.value;download_url=$Entry.download_url
        verification=$Verification;verified_at=(Get-Date).ToUniversalTime().ToString('o')
        adopted=[bool]$Adopted;files=@($Manifest)
    }
    $path=Join-Path $receiptRoot ($Id+'.json')
    $temporary=$path+'.tmp'
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $path -Force
}

function Install-Portable($Id,$Destination,$Marker) {
    $entry=$catalog.dependencies.$Id
    if(-not $entry){throw "Unknown dependency: $Id"}
    $destinationFull=Assert-OwnedPath $Destination $runtimeRoot
    $receipt=Get-Receipt $Id
    if(Test-Receipt $receipt $Id $destinationFull $entry){
        if([IO.Path]::GetFullPath($receipt.destination) -ne $destinationFull){Write-Receipt $Id $entry $destinationFull $Marker $receipt.files $receipt.adopted $receipt.verification}
        Write-Host "$Id verified locally ($($receipt.version)).";return
    }
    $archive=Get-PinnedArchive $entry
    $stage=Join-Path $cacheRoot ('stage-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    try{
        Expand-Checked $archive $stage
        $source=$stage
        if($entry.archive_subdirectory){$source=Assert-OwnedPath (Join-Path $stage $entry.archive_subdirectory) $stage}
        if(-not (Test-Path -LiteralPath (Join-Path $source $Marker) -PathType Leaf)){throw "Archive layout changed for $Id; not installing it."}
        if($entry.unity_package){
            $package=Get-Content -Raw -LiteralPath (Join-Path $source 'package.json') | ConvertFrom-Json
            if($package.name -ne $entry.unity_package -or $package.version -ne $entry.version){throw "Unity package identity differs from the $Id pin."}
        }
        $manifest=@(Get-PayloadManifest $source)
        if(Test-Path -LiteralPath $destinationFull){
            if(Test-Payload $destinationFull $manifest){
                Write-Receipt $Id $entry $destinationFull $Marker $manifest $true
                Write-Host "Adopted byte-verified $($entry.name) $($entry.version)."
                return
            }
            $occupied=@(Get-ChildItem -LiteralPath $destinationFull -Force).Count -ne 0
            if($occupied){
                # Only a receipt for this exact owned location allows a repair. Preserve every old byte.
                if(-not(Test-ReceiptLocation $receipt $Id $destinationFull)){
                    throw "Unverified files occupy $destinationFull. They were preserved. Move them to a backup folder, then run Install again."
                }
                $backup=Assert-OwnedPath ($destinationFull+'.backup-'+[Guid]::NewGuid().ToString('N')) $runtimeRoot
                if((Get-Item -LiteralPath $destinationFull -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Refusing to repair a linked install directory.'}
                Move-Item -LiteralPath $destinationFull -Destination $backup
                Write-Host "Preserved the previous $Id installation at $backup"
            }else{Remove-Item -LiteralPath $destinationFull}
        }
        New-Item -ItemType Directory -Path (Split-Path $destinationFull -Parent) -Force | Out-Null
        Move-Item -LiteralPath $source -Destination $destinationFull
        if(-not (Test-Payload $destinationFull $manifest)){throw "Installed payload verification failed: $Id"}
        Write-Receipt $Id $entry $destinationFull $Marker $manifest $false
        Write-Host "Installed $($entry.name) $($entry.version), license $($entry.license)."
    }finally{Remove-Stage $stage}
}

function Get-BlenderVersion($Executable) {
    try{
        $output=@(& $Executable --version 2>&1)
        if($LASTEXITCODE -eq 0 -and ($output -join "`n") -match '(?m)^Blender (\d+\.\d+\.\d+)'){return $Matches[1]}
    }catch{}
    return $null
}

function Find-Blender {
    $candidates=@($env:AVATARFORGE_BLENDER,$env:BLENDER_PATH)
    $candidates+=@(Get-Process -Name blender -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path)
    $cmd=Get-Command blender.exe -ErrorAction SilentlyContinue
    if($cmd){$candidates+=$cmd.Source}
    foreach($root in @((Join-Path $env:ProgramFiles 'Blender Foundation'),(Join-Path ${env:ProgramFiles(x86)} 'Steam\steamapps\common\Blender'))){
        $candidates+=@(Get-ChildItem -LiteralPath $root -Filter blender.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    }
    foreach($candidate in @($candidates | Where-Object {$_} | Select-Object -Unique)){
        if(-not(Test-Path -LiteralPath $candidate -PathType Leaf)){continue}
        $version=Get-BlenderVersion $candidate
        if($version -and [version]$version -ge [version]'5.0.0'){return @{path=$candidate;version=$version}}
    }
    return $null
}

function Invoke-ScopedDotnet($Executable,[string[]]$Arguments) {
    # SDK first use must not create certificates, append the global tools PATH,
    # or send CLI telemetry. Restore the caller's settings after every child.
    $settings=@{DOTNET_GENERATE_ASPNET_CERTIFICATE='false';DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false';DOTNET_CLI_TELEMETRY_OPTOUT='true'}
    $previous=@{}
    foreach($name in $settings.Keys){$previous[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
    try{
        foreach($name in $settings.Keys){[Environment]::SetEnvironmentVariable($name,$settings[$name],'Process')}
        & $Executable @Arguments
    }finally{
        foreach($name in $settings.Keys){[Environment]::SetEnvironmentVariable($name,$previous[$name],'Process')}
    }
}

function Test-DotnetForVpm($Executable) {
    try{
        $sdks=@(Invoke-ScopedDotnet $Executable @('--list-sdks') 2>&1)
        if($LASTEXITCODE -ne 0){return $false}
        $runtimes=@(Invoke-ScopedDotnet $Executable @('--list-runtimes') 2>&1)
        if($LASTEXITCODE -ne 0){return $false}
        $sdkOK=@($sdks | Where-Object {$_ -match '^([0-9]+)\.' -and [int]$Matches[1] -ge 8}).Count -gt 0
        $runtimeOK=@($runtimes | Where-Object {$_ -match '^Microsoft\.NETCore\.App 8\.'}).Count -gt 0
        return $sdkOK -and $runtimeOK
    }catch{return $false}
}

function Get-VpmDotnet {
    $dotnet=Get-Command dotnet -ErrorAction SilentlyContinue
    if($dotnet -and (Test-DotnetForVpm $dotnet.Source)){return $dotnet.Source}
    Write-Host 'Installing private .NET 8 SDK and runtime for official VRChat VPM...'
    $folder=Join-Path $runtimeRoot 'dotnet'
    Install-Portable 'dotnet8' $folder 'dotnet.exe'
    $dotnetExe=Join-Path $folder 'dotnet.exe'
    $env:DOTNET_ROOT=$folder
    $env:DOTNET_ROOT_X64=$folder
    $env:PATH=$folder+[IO.Path]::PathSeparator+$env:PATH
    if(-not(Test-DotnetForVpm $dotnetExe)){throw 'The private .NET SDK does not provide the SDK/runtime VPM requires.'}
    return $dotnetExe
}

function Install-UnityTools {
    # Probe SDK and runtime BEFORE tool install: a .NET 6/7 SDK cannot install the net8 tool.
    $dotnetExe=Get-VpmDotnet
    $entry=$catalog.dependencies.vpm
    $archive=Get-PinnedArchive $entry
    $vpmFolder=Join-Path $runtimeRoot 'vpm'
    $executable=Join-Path $vpmFolder 'vpm.exe'
    $stage=Join-Path $cacheRoot ('stage-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    try{
        Expand-Checked $archive $stage
        $payload=Join-Path $stage 'tools\net8.0\any'
        if(-not(Test-Path -LiteralPath (Join-Path $payload 'vpm.dll'))){throw 'The official VPM NuGet payload layout changed.'}
        $manifest=@(Get-PayloadManifest $payload)
        if(-not(Test-Path -LiteralPath $executable)){
            if((Test-Path -LiteralPath $vpmFolder) -and @(Get-ChildItem -LiteralPath $vpmFolder -Force).Count){throw "Unverified files occupy $vpmFolder. They were preserved."}
            $feed=Join-Path $stage 'feed'
            New-Item -ItemType Directory -Path $feed | Out-Null
            Copy-Item -LiteralPath $archive -Destination (Join-Path $feed ('vrchat.vpm.cli.'+$entry.version+'.nupkg'))
            Invoke-ScopedDotnet $dotnetExe @('tool','install','--tool-path',$vpmFolder,'vrchat.vpm.cli','--version',$entry.version,'--add-source',$feed,'--ignore-failed-sources')
            if($LASTEXITCODE -ne 0){throw 'Official VPM installation failed.'}
        }
        $store=Join-Path $vpmFolder ('.store\vrchat.vpm.cli\'+$entry.version+'\vrchat.vpm.cli\'+$entry.version+'\tools\net8.0\any')
        if(-not(Test-Payload $store $manifest)){throw 'Installed VPM package files differ from the verified official NuGet package. Existing files were preserved.'}
        $versionOutput=@(& $executable --version 2>&1)
        if($LASTEXITCODE -ne 0 -or ($versionOutput -join "`n") -notmatch ('(?m)^'+[regex]::Escape($entry.version)+'(?:\+[^\s]+)?\s*$')){throw 'Installed VPM cannot start or reports a different version.'}
        Write-Receipt 'vpm' $entry $store 'vpm.dll' $manifest $true 'archive-payload-and-tool-version'
        Write-Host "Official VPM $($entry.version) is ready."
        Write-Host 'Unity/SDK installers: https://vcc.docs.vrchat.com/'
    }finally{Remove-Stage $stage}
}

function Write-InstallState($Status,$Failure) {
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $verified=[ordered]@{}
    foreach($file in @(Get-ChildItem -LiteralPath $receiptRoot -Filter '*.json' -ErrorAction SilentlyContinue)){
        $id=$file.BaseName
        $receipt=Get-Receipt $id
        $entry=$catalog.dependencies.$id
        if(-not $entry -or -not $receipt){continue}
        $destination=$receipt.destination
        if($receipt.destination_relative){$destination=Join-Path $runtimeRoot $receipt.destination_relative}
        if(Test-Receipt $receipt $id $destination $entry){
            $verified[$id]=[ordered]@{version=$receipt.version;destination=$destination;verification=$receipt.verification;archive_algorithm=$receipt.archive_algorithm;archive_digest=$receipt.archive_digest;receipt=$file.FullName}
        }
    }
    $state=[ordered]@{schema_version=2;checked_at=(Get-Date).ToUniversalTime().ToString('o');component=$Component;status=$Status;installed=$verified;external_tools=@($script:externalTools)}
    if($Failure){$state.error=$Failure}
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runtimeRoot 'install-state.json') -Encoding UTF8
}

function Invoke-AvatarForgeInstall {
    New-Item -ItemType Directory -Path $runtimeRoot,$cacheRoot -Force | Out-Null
    try{
        Install-Portable 'python' (Join-Path $runtimeRoot 'python') 'python.exe'
        if($Component -in @('core','all')){
            $localBlender=Join-Path $runtimeRoot 'blender'
            $blender=$null
            if(Test-Path -LiteralPath $localBlender){Install-Portable 'blender' $localBlender 'blender.exe'}
            else{$blender=Find-Blender;if(-not $blender){Install-Portable 'blender' $localBlender 'blender.exe'}}
            if($blender){
                $script:externalTools+=@{id='blender';path=$blender.path;version=$blender.version;verification='executable-version'}
                Write-Host "Using Blender $($blender.version): $($blender.path)"
            }
            Install-Portable 'sourceio' (Join-Path $runtimeRoot 'addons\SourceIO') '__init__.py'
        }
        if($Component -in @('extended','all')){
            foreach($id in @('source_tools','mmd_tools','vrm','xps_extension')){
                $entry=$catalog.dependencies.$id
                Install-Portable $id (Join-Path $runtimeRoot ('addons\'+$entry.module)) '__init__.py'
            }
        }
        if($Component -in @('optimization','all')){
            foreach($id in @('localization','ndmf','modular_avatar','unity_mesh_simplifier')){
                $entry=$catalog.dependencies.$id
                Install-Portable $id (Join-Path $runtimeRoot ('unity-packages\'+$entry.unity_package)) 'package.json'
            }
        }
        if($Component -in @('unity','all')){Install-UnityTools}
        Write-InstallState 'ready' $null
        Write-Host 'AvatarForge tools ready.'
    }catch{
        $failure=$_.Exception.Message
        Write-InstallState 'failed' $failure
        throw
    }
}

# Dot-sourcing loads the same installer functions for focused verification without installing anything.
if($MyInvocation.InvocationName -ne '.'){Invoke-AvatarForgeInstall}
