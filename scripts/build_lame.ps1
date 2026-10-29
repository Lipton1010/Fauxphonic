param([switch]$DownloadSources)
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $workspace '.cache'
$archives = Join-Path $cache 'source-archives'
$state = Join-Path $cache 'lame-controlled'
$sourceRoot = Join-Path $state 'source'
$sourceDir = Join-Path $sourceRoot 'lame-4.0'
$toolRoot = Join-Path $cache 'ffmpeg-build\toolchain\w64devkit'
$bin = Join-Path $toolRoot 'bin'
$archiveName = 'lame-4.0.tar.gz'
$archiveUrl = 'https://downloads.sourceforge.net/project/lame/lame/4.0/lame-4.0.tar.gz'
$archiveSha256 = '3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb'
$sourceDateEpoch = '1783756028' # upstream archive root timestamp: 2026-07-11 07:47:08 UTC
$toolchainArchiveSha256 = '18d0a4c71a166f8401ab6305781bec5882b40b5e06ba9807c61cb5f3b3c6325e'
$gccPath = Join-Path $bin 'x86_64-w64-mingw32-gcc.exe'
$arPath = Join-Path $bin 'ar.exe'
$nmPath = Join-Path $bin 'x86_64-w64-mingw32-nm.exe'
$objdumpPath = Join-Path $bin 'objdump.exe'
$shPath = Join-Path $bin 'sh.exe'
$makePath = Join-Path $bin 'make.exe'
$tarPath = Join-Path $bin 'tar.exe'
$toolManifest = Get-Content -Raw (Join-Path $workspace 'tools/source_manifest.json') | ConvertFrom-Json
$expectedGccHash = $toolManifest.libsndfile.toolchain_components.gcc_sha256
$archive = Join-Path $archives $archiveName

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program $($Arguments -join ' ')" }
}

New-Item -ItemType Directory -Force -Path $archives, $state, $sourceRoot | Out-Null
if (-not (Test-Path -LiteralPath $archive) -and $DownloadSources) {
    Invoke-Checked 'curl.exe' @('-fL', '--retry', '2', $archiveUrl, '-o', $archive)
}
if (-not (Test-Path -LiteralPath $archive)) { throw "Missing official source archive: $archive. Use -DownloadSources to fetch it." }
$actualArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actualArchiveHash -ne $archiveSha256) { throw 'Official LAME archive SHA-256 does not match the pinned digest.' }
if (-not (Test-Path -LiteralPath (Join-Path $sourceDir 'configure'))) {
    Invoke-Checked $tarPath @('-xzf', $archive, '-C', $sourceRoot)
}
foreach ($tool in @($gccPath, $arPath, $nmPath, $objdumpPath, $shPath, $makePath, $tarPath)) {
    if (-not (Test-Path -LiteralPath $tool)) { throw "Missing project-local build tool: $tool" }
}
$gccHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $gccPath).Hash.ToLowerInvariant()
if ($gccHash -ne $expectedGccHash) { throw 'Project-local GCC differs from the toolchain manifest.' }

$drive = @('Z','Y','X','W','V','U','T') | Where-Object { -not (Get-PSDrive -Name $_ -ErrorAction SilentlyContinue) } | Select-Object -First 1
if (-not $drive) { throw 'No free drive letter for the temporary no-space shell path.' }
$alias = "$drive`:"; $aliasRoot = "$drive`:"; $aliasPosix = "$($drive.ToLowerInvariant()):"
Invoke-Checked 'subst.exe' @($alias, $workspace)
$oldPath = $env:PATH
$oldEpoch = $env:SOURCE_DATE_EPOCH
$oldCflags = $env:CFLAGS
$oldLdflags = $env:LDFLAGS
$oldArflags = $env:ARFLAGS
$oldZeroArDate = $env:ZERO_AR_DATE
try {
    $env:PATH = "$aliasRoot\.cache\ffmpeg-build\toolchain\w64devkit\bin;$oldPath"
    $env:SOURCE_DATE_EPOCH = $sourceDateEpoch
    $env:CFLAGS = "-O2 -ffile-prefix-map=$aliasRoot/.cache/lame-controlled/source/lame-4.0=src -fdebug-prefix-map=$aliasRoot/.cache/lame-controlled/source/lame-4.0=src"
    $env:LDFLAGS = '-Wl,--no-insert-timestamp'
    $env:ARFLAGS = 'crD'
    $env:ZERO_AR_DATE = '1'
    $buildHashes = @()
    $runResults = @()
    for ($run = 1; $run -le 2; $run++) {
        $buildDir = Join-Path $state "build-$run"
        $prefixDir = Join-Path $state "prefix-$run"
        if (Test-Path -LiteralPath $buildDir) { Remove-Item -LiteralPath $buildDir -Recurse -Force }
        if (Test-Path -LiteralPath $prefixDir) { Remove-Item -LiteralPath $prefixDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $buildDir, $prefixDir | Out-Null
        $buildPosix = "$aliasPosix/.cache/lame-controlled/build-$run"
        $prefixPosix = "$aliasPosix/.cache/lame-controlled/prefix-$run"
        $sourcePosix = "$aliasPosix/.cache/lame-controlled/source/lame-4.0"
        $configureLine = "cd '$buildPosix' && '$sourcePosix/configure' --host=x86_64-w64-mingw32 --prefix='$prefixPosix' --disable-shared --enable-static --disable-decoder"
        Invoke-Checked $shPath @('-lc', $configureLine)
        $makeLine = "cd '$buildPosix' && make -j8 && make install"
        Invoke-Checked $shPath @('-lc', $makeLine)
        $staticLibrary = Join-Path $prefixDir 'lib/libmp3lame.a'
        $header = Join-Path $prefixDir 'include/lame/lame.h'
        $frontend = Join-Path $prefixDir 'bin/lame.exe'
        foreach ($artifact in @($staticLibrary, $header, $frontend)) {
            if (-not (Test-Path -LiteralPath $artifact)) { throw "Expected LAME build artifact is missing: $artifact" }
        }
        $buildHashes += (Get-FileHash -Algorithm SHA256 -LiteralPath $staticLibrary).Hash.ToLowerInvariant()
        $symbols = @(& $nmPath --defined-only $staticLibrary | ForEach-Object {
            if ($_ -match '\s([A-Za-z_][A-Za-z0-9_]*)$') { $Matches[1] }
        })
        foreach ($symbol in @('lame_init', 'lame_encode_buffer', 'lame_encode_flush', 'get_lame_version')) {
            if ($symbol -notin $symbols) { throw "The static interface is missing required symbol $symbol." }
        }
        $runResults += [ordered]@{
            run = $run
            static_library_sha256 = $buildHashes[-1]
            static_library_size = (Get-Item -LiteralPath $staticLibrary).Length
            static_library = $staticLibrary
            header_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $header).Hash.ToLowerInvariant()
            frontend_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $frontend).Hash.ToLowerInvariant()
            exported_interface_symbols = @('lame_init', 'lame_encode_buffer', 'lame_encode_flush', 'get_lame_version')
        }
    }
    if ($buildHashes[0] -ne $buildHashes[1]) { throw "Clean static builds are not deterministic: $($buildHashes -join ', ')" }

    $smokeDir = Join-Path $state ("smoke-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $smokeDir | Out-Null
    $smokeInput = Join-Path $smokeDir 'input.wav'
    $smokeMp3 = Join-Path $smokeDir 'encoded.mp3'
    $smokeDecoded = Join-Path $smokeDir 'decoded.wav'
    $python = Join-Path $workspace '.venv\Scripts\python.exe'
    $pythonCode = "import numpy as np, soundfile as sf; n=48000*3; t=np.arange(n)/48000; sf.write(r'$smokeInput', .15*np.sin(2*np.pi*440*t), 48000, subtype='PCM_16')"
    Invoke-Checked $python @('-c', $pythonCode)
    $frontend = Join-Path $state 'prefix-2/bin/lame.exe'
    $versionText = (& $frontend --version 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $versionText -notmatch 'LAME 64bits version 4\.0') {
        throw 'Built frontend did not identify itself as LAME 4.0.'
    }
    $frontendImports = @(& $objdumpPath -p $frontend | ForEach-Object {
        if ($_ -match '^\s+DLL Name: (.+)$') { $Matches[1] }
    })
    if ($frontendImports -contains 'libmp3lame.dll') { throw 'The LAME frontend unexpectedly depends on a separate LAME DLL.' }
    Invoke-Checked $frontend @('--silent', '-b', '128', $smokeInput, $smokeMp3)
    $ffmpeg = Join-Path $workspace 'tools/ffmpeg.exe'
    $ffprobe = Join-Path $workspace 'tools/ffprobe.exe'
    $smokeDecode = 'not run: project FFmpeg tools are unavailable'
    if ((Test-Path -LiteralPath $ffmpeg) -and (Test-Path -LiteralPath $ffprobe)) {
        Invoke-Checked $ffmpeg @('-v', 'error', '-i', $smokeMp3, '-y', '-c:a', 'pcm_s24le', $smokeDecoded)
        $decodedDuration = (& $ffprobe '-v' 'error' '-show_entries' 'format=duration' '-of' 'default=nw=1:nk=1' $smokeDecoded | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0 -or [math]::Abs([double]::Parse($decodedDuration, [Globalization.CultureInfo]::InvariantCulture) - 3.0) -gt 0.001) {
            throw "LAME CLI encode / FFmpeg decode duration check failed: $decodedDuration seconds."
        }
        $smokeDecode = "FFmpeg decoded MP3 to PCM24 WAV at 48 kHz; decoded duration $decodedDuration seconds."
    }

    $packageDir = Join-Path $state 'package'
    New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
    $candidate = Join-Path $packageDir 'libmp3lame.a'
    Copy-Item -LiteralPath (Join-Path $state 'prefix-2/lib/libmp3lame.a') -Destination $candidate -Force
    $headerDir = Join-Path $packageDir 'lame'
    New-Item -ItemType Directory -Force -Path $headerDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $state 'prefix-2/include/lame/lame.h') -Destination (Join-Path $headerDir 'lame.h') -Force
    Copy-Item -LiteralPath (Join-Path $headerDir 'lame.h') -Destination (Join-Path $packageDir 'lame.h') -Force
    Copy-Item -LiteralPath (Join-Path $sourceDir 'COPYING') -Destination (Join-Path $packageDir 'COPYING') -Force
    Copy-Item -LiteralPath (Join-Path $sourceDir 'LICENSE') -Destination (Join-Path $packageDir 'LICENSE') -Force
    $licenseHashes = [ordered]@{
        COPYING = (Get-FileHash -Algorithm SHA256 (Join-Path $packageDir 'COPYING')).Hash.ToLowerInvariant()
        LICENSE = (Get-FileHash -Algorithm SHA256 (Join-Path $packageDir 'LICENSE')).Hash.ToLowerInvariant()
    }
    $nmOutput = @(& $nmPath -g --defined-only $candidate | ForEach-Object { $_.Trim() })
    $symbols = @($nmOutput | ForEach-Object { if ($_ -match '\s([A-Za-z_][A-Za-z0-9_]*)$') { $Matches[1] } })
    $evidence = [ordered]@{
        upstream = [ordered]@{
            project = 'LAME 4.0'
            official_download_page = 'https://lame.sourceforge.io/download.php'
            sourceforge_file = 'https://sourceforge.net/projects/lame/files/lame/4.0/lame-4.0.tar.gz/download'
            source_archive_url = $archiveUrl
            source_archive_sha256 = $archiveSha256
            license = 'LGPL-2.0-or-later'
            license_files_sha256 = $licenseHashes
            rights_note = 'Upstream LICENSE says commercial use is permitted under LGPL: keep LAME as a separate library, acknowledge use and link to https://lame.sourceforge.io/, and release any LAME modifications under LGPL. Source README also carries a software-patent caveat; this build adds no patent grant.'
        }
        build = [ordered]@{
            compiler = 'w64devkit 2.10.0, GCC 16.2.0 x86_64'
            toolchain_archive_sha256 = $toolchainArchiveSha256
            gcc_sha256 = $gccHash
            source_date_epoch = [int64]$sourceDateEpoch
            reproducibility = 'Two clean out-of-tree MinGW builds; configure --disable-shared --enable-static --disable-decoder; encoder frontend enabled; -O2; SOURCE_DATE_EPOCH; -Wl,--no-insert-timestamp; file/debug prefix maps; ARFLAGS=crD; ZERO_AR_DATE=1'
            runs = $runResults
        }
        smoke = [ordered]@{
            frontend_version = (($versionText -split "`r?`n" | Select-Object -First 1).Trim())
            frontend_linkage = 'libmp3lame statically linked into frontend executable'
            frontend_imports = $frontendImports
            input_duration_seconds = 3
            output_mp3_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $smokeMp3).Hash.ToLowerInvariant()
            decode_check = $smokeDecode
            artifacts = $smokeDir
        }
        candidate = [ordered]@{
            path = $candidate
            size = (Get-Item -LiteralPath $candidate).Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash.ToLowerInvariant()
            deterministic = $buildHashes[0] -eq $buildHashes[1]
            api_symbols_verified = @('lame_init', 'lame_encode_buffer', 'lame_encode_flush', 'get_lame_version') | Where-Object { $_ -in $symbols }
        }
    }
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $state 'build-evidence.json')
    $evidence | ConvertTo-Json -Depth 8
}
finally {
    $env:PATH = $oldPath
    if ($null -eq $oldEpoch) { Remove-Item Env:SOURCE_DATE_EPOCH -ErrorAction SilentlyContinue } else { $env:SOURCE_DATE_EPOCH = $oldEpoch }
    if ($null -eq $oldCflags) { Remove-Item Env:CFLAGS -ErrorAction SilentlyContinue } else { $env:CFLAGS = $oldCflags }
    if ($null -eq $oldLdflags) { Remove-Item Env:LDFLAGS -ErrorAction SilentlyContinue } else { $env:LDFLAGS = $oldLdflags }
    if ($null -eq $oldArflags) { Remove-Item Env:ARFLAGS -ErrorAction SilentlyContinue } else { $env:ARFLAGS = $oldArflags }
    if ($null -eq $oldZeroArDate) { Remove-Item Env:ZERO_AR_DATE -ErrorAction SilentlyContinue } else { $env:ZERO_AR_DATE = $oldZeroArDate }
    & subst.exe $alias /d | Out-Null
}
