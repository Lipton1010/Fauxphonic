param(
    [string]$Extractor = "${env:ProgramFiles}/7-Zip/7z.exe",
    [switch]$Promote,
    [switch]$BuildCandidate
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$manifest = Get-Content -Raw 'tools/source_manifest.json' | ConvertFrom-Json
$cache = Join-Path $root '.cache/ffmpeg-build'
$archives = Join-Path $root '.cache/source-archives'
$sourceArchive = Join-Path $archives $manifest.ffmpeg.source_archive.name
$toolArchive = Join-Path $cache $manifest.ffmpeg.toolchain_archive.name
$sourceRoot = Join-Path $cache 'source/ffmpeg-8.1.1'
$toolchainRoot = Join-Path $cache 'toolchain/w64devkit'
$opusPrefix = Join-Path $root '.cache/sndfile-controlled/prefix'
$lamePackage = Join-Path $root '.cache/lame-controlled/package'

# Recreate exact static codec inputs when a fresh checkout has only source pins.
if (-not (Test-Path -LiteralPath (Join-Path $opusPrefix 'lib/libopus.a'))) {
    & 'scripts/build_sndfile.ps1' -DownloadSources
    if ($LASTEXITCODE) { throw 'Pinned Opus/libSndfile source build failed.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $lamePackage 'libmp3lame.a'))) {
    & 'scripts/build_lame.ps1' -DownloadSources
    if ($LASTEXITCODE) { throw 'Pinned LAME source build failed.' }
}
$opusHash = (Get-FileHash -Algorithm SHA256 (Join-Path $opusPrefix 'lib/libopus.a')).Hash.ToLower()
$lameHash = (Get-FileHash -Algorithm SHA256 (Join-Path $lamePackage 'libmp3lame.a')).Hash.ToLower()
if ($opusHash -ne $manifest.ffmpeg.linked_libraries.opus.static_library_sha256 -or
    $lameHash -ne $manifest.ffmpeg.linked_libraries.lame.static_library_sha256) {
    throw 'Pinned FFmpeg static codec library checksum mismatch.'
}

function Get-VerifiedArchive($entry, $destination) {
    if (-not (Test-Path -LiteralPath $destination)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Invoke-WebRequest -Uri $entry.url -OutFile $destination
    }
    if ((Get-FileHash -Algorithm SHA256 $destination).Hash.ToLower() -ne $entry.sha256) {
        throw "Pinned archive hash mismatch: $($entry.name)"
    }
}

Get-VerifiedArchive $manifest.ffmpeg.source_archive $sourceArchive
Get-VerifiedArchive $manifest.ffmpeg.toolchain_archive $toolArchive
if (-not (Test-Path -LiteralPath (Join-Path $toolchainRoot 'bin/make.exe'))) {
    $extractRoot = Join-Path $cache 'toolchain'
    if (Test-Path -LiteralPath $Extractor) {
        & $Extractor x -y "-o$extractRoot" $toolArchive | Out-Null
    } else {
        & $toolArchive -y "-o$extractRoot" | Out-Null
    }
    if ($LASTEXITCODE) { throw 'Could not extract the pinned project-local compiler.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'configure'))) {
    $extractRoot = Join-Path $cache 'source'
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    & tar.exe -xf $sourceArchive -C $extractRoot
    if ($LASTEXITCODE) { throw 'Could not extract the pinned FFmpeg source archive.' }
}

$bin = (Resolve-Path (Join-Path $toolchainRoot 'bin')).Path.Replace('\', '/')
$source = (Resolve-Path $sourceRoot).Path.Replace('\', '/')
$epoch = $manifest.ffmpeg.source_date_epoch
$argsFile = (Join-Path $root $manifest.ffmpeg.configure_args).Replace('\', '/')
$drive = @('Z','Y','X','W','V','U','T') | Where-Object { -not (Get-PSDrive -Name $_ -ErrorAction SilentlyContinue) } | Select-Object -First 1
if (-not $drive) { throw 'No free drive letter for FFmpeg codec include paths.' }
$alias = "${drive}:"
& subst.exe $alias $root
if ($LASTEXITCODE) { throw 'Could not create project-local codec path alias.' }
$aliasPosix = "$($drive.ToLowerInvariant()):"
$opusPrefixPosix = "$aliasPosix/.cache/sndfile-controlled/prefix"
$lamePackagePosix = "$aliasPosix/.cache/lame-controlled/package"
$pkgconfigDir = Join-Path $cache 'rebuild/pkgconfig'
New-Item -ItemType Directory -Force -Path $pkgconfigDir | Out-Null
$opusPc = Get-Content -Raw -LiteralPath (Join-Path $opusPrefix 'lib/pkgconfig/opus.pc')
$opusPc = $opusPc -replace '(?m)^prefix=.*$', "prefix=$opusPrefixPosix"
$opusPc = $opusPc -replace '(?m)^exec_prefix=.*$', "exec_prefix=$opusPrefixPosix"
$opusPc = $opusPc -replace '(?m)^libdir=.*$', "libdir=$opusPrefixPosix/lib"
$opusPc = $opusPc -replace '(?m)^includedir=.*$', "includedir=$opusPrefixPosix/include"
$opusPc = $opusPc -replace '(?m)^Libs:.*$', "Libs: -L$opusPrefixPosix/lib -lopus"
$opusPc = $opusPc -replace '(?m)^Cflags:.*$', "Cflags: -I$opusPrefixPosix/include/opus"
Set-Content -LiteralPath (Join-Path $pkgconfigDir 'opus.pc') -Value $opusPc -Encoding ascii
$pkgconfigDirPosix = "$aliasPosix/.cache/ffmpeg-build/rebuild/pkgconfig"
$logDir = Join-Path $cache 'rebuild'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$recipe = Join-Path $logDir 'rebuild.sh'
@"
#!/bin/sh
set -eu
PATH='$bin;C:/Windows/System32'
export PATH
PKG_CONFIG_PATH='$pkgconfigDirPosix'
export PKG_CONFIG_PATH
export SOURCE_DATE_EPOCH=$epoch
cd '$source'
if [ -f ffbuild/config.mak ]; then
  make clean > '$($logDir.Replace('\', '/'))/clean.log' 2>&1
fi
set --
while IFS= read -r arg; do
  [ -n `"`$arg`" ] && set -- `"`$@`" `"`$arg`"
done < '$argsFile'
set -- `"`$@`" `"--extra-cflags=-I$opusPrefixPosix/include -I$lamePackagePosix`" `"--extra-ldflags=-L$opusPrefixPosix/lib -L$lamePackagePosix -Wl,--no-insert-timestamp`"
sh ./configure `"`$@`" > '$($logDir.Replace('\', '/'))/configure.log' 2>&1
make V=1 -j8 > '$($logDir.Replace('\', '/'))/make.log' 2>&1
"@ | Set-Content -LiteralPath $recipe -Encoding ascii

$shell = Join-Path $toolchainRoot 'bin/sh.exe'
try {
    & $shell $recipe
    $shellExit = $LASTEXITCODE
} finally {
    & subst.exe $alias /d | Out-Null
}
if ($shellExit) { throw "FFmpeg source build failed; logs are in $logDir" }
$candidate = Join-Path $cache 'rebuilt'
New-Item -ItemType Directory -Force -Path $candidate | Out-Null
foreach ($name in $manifest.ffmpeg.binaries.PSObject.Properties.Name) {
    $built = Join-Path $sourceRoot $name
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $built).Hash.ToLower()
    $expected = $manifest.ffmpeg.binaries.$name
    if (-not $BuildCandidate -and $hash -ne $expected) { throw "$name is not byte-identical to the pinned build: $hash" }
    Copy-Item -LiteralPath $built -Destination (Join-Path $candidate $name) -Force
}
if ($Promote) {
    foreach ($name in $manifest.ffmpeg.binaries.PSObject.Properties.Name) {
        Copy-Item -LiteralPath (Join-Path $candidate $name) -Destination (Join-Path $root "tools/$name") -Force
    }
}
if ($BuildCandidate) {
    Get-ChildItem -LiteralPath $candidate -File | Get-FileHash -Algorithm SHA256 |
        Select-Object Path, Hash | Format-Table -AutoSize
} else {
    Write-Output "Verified byte-identical FFmpeg tools: $candidate"
}
