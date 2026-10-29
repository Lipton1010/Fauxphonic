param([Parameter(Mandatory)][string]$Bundle)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bundleRoot = (Resolve-Path -LiteralPath $Bundle).Path
$manifest = Get-Content -Raw -LiteralPath (Join-Path $root 'tools/source_manifest.json') | ConvertFrom-Json
$cache = Join-Path $root '.cache/source-archives'
New-Item -ItemType Directory -Force -Path $cache | Out-Null

function Copy-VerifiedSource($entry, $destination) {
    $cached = Join-Path $cache $entry.name
    if (-not (Test-Path -LiteralPath $cached)) {
        $existingToolchain = Join-Path $root ('.cache/ffmpeg-build/' + $entry.name)
        if (Test-Path -LiteralPath $existingToolchain) {
            Copy-Item -LiteralPath $existingToolchain -Destination $cached
        } else {
            Invoke-WebRequest -Uri $entry.url -OutFile $cached
        }
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $cached).Hash.ToLower()
    if ($actual -ne $entry.sha256) { throw "Source archive checksum mismatch: $($entry.name)" }
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Copy-Item -LiteralPath $cached -Destination (Join-Path $destination $entry.name) -Force
}

$ffmpeg = Join-Path $bundleRoot 'legal/sources/FFmpeg'
Copy-VerifiedSource $manifest.ffmpeg.source_archive $ffmpeg
Copy-Item -LiteralPath (Join-Path $root 'scripts/ffmpeg_configure_args.txt') -Destination $ffmpeg -Force
Copy-Item -LiteralPath (Join-Path $root 'scripts/build_ffmpeg.ps1') -Destination $ffmpeg -Force
Copy-Item -LiteralPath (Join-Path $root 'tools/source_manifest.json') -Destination $ffmpeg -Force
Copy-Item -LiteralPath (Join-Path $root 'evidence/ffmpeg-reproducible-build.json') -Destination $ffmpeg -Force
Copy-Item -LiteralPath (Join-Path $root 'evidence/ffmpeg-delivery-codec-probe.json') -Destination $ffmpeg -Force
Copy-Item -LiteralPath (Join-Path $root 'tools/LICENSE') -Destination (Join-Path $ffmpeg 'COPYING.LGPLv2.1') -Force
$instructions = @'
FFmpeg 8.1.1 source and build recipe

This FFmpeg/ffprobe build uses FFmpeg 8.1.1 (LGPL-2.1-or-later), Opus 1.5.2
(BSD-3-Clause), and LAME 4.0 (LGPL-2.0-or-later). The exact enabled components
are listed in ffmpeg_configure_args.txt. Their source archives, recipes,
license texts, compiler URL/hash, and static-library hashes are recorded here
and in source_manifest.json. The tested build used SOURCE_DATE_EPOCH 1777847117
and make -j8. To rebuild, place the included source archives under
`.cache/source-archives/` using their manifest filenames. If the pinned compiler
or other project-local build tools are absent, the recipes download them into
the project cache; no tools are installed globally. From the project root, run:

  powershell -ExecutionPolicy Bypass -File scripts/build_ffmpeg.ps1

That rebuilds to a local cache and checks both executable hashes. Add
-Promote to replace the project's tools after verification.
'@
Set-Content -LiteralPath (Join-Path $ffmpeg 'BUILDING.txt') -Value $instructions -Encoding utf8
$lame = $manifest.ffmpeg.linked_libraries.lame
Copy-VerifiedSource $lame.source_archive $ffmpeg
Copy-Item -LiteralPath (Join-Path $root 'scripts/build_lame.ps1') -Destination $ffmpeg -Force
Copy-Item -LiteralPath (Join-Path $root '.cache/lame-controlled/package/COPYING') -Destination (Join-Path $ffmpeg 'LAME-COPYING') -Force
Copy-Item -LiteralPath (Join-Path $root '.cache/lame-controlled/package/LICENSE') -Destination (Join-Path $ffmpeg 'LAME-LICENSE') -Force

$qt = Join-Path $bundleRoot 'legal/sources/Qt'
foreach ($archive in $manifest.qt.archives) { Copy-VerifiedSource $archive $qt }
$sndfile = Join-Path $bundleRoot 'legal/sources/libsndfile'
foreach ($archive in $manifest.libsndfile.archives) { Copy-VerifiedSource $archive $sndfile }
Copy-Item -LiteralPath (Join-Path $root 'scripts/build_sndfile.ps1') -Destination $sndfile -Force
Copy-Item -LiteralPath (Join-Path $root 'tools/libsndfile_manifest.json') -Destination $sndfile -Force
Copy-Item -LiteralPath (Join-Path $root 'tools/source_manifest.json') -Destination $sndfile -Force
$sndfileInstructions = @'
libsndfile 1.2.2 controlled build

The included libsndfile DLL is built from libsndfile 1.2.2 with static FLAC
1.4.3, libogg 1.3.5, libvorbis 1.3.7, and Opus 1.5.2. MPEG support is disabled.
The corresponding source archives, exact SHA256 pins, license identifiers,
toolchain URL/hash, and PowerShell build recipe are included here. The recipe
downloads the pinned w64devkit CMake/GCC toolchain into the project cache.
From the application project root run:

  powershell -ExecutionPolicy Bypass -File scripts/build_sndfile.ps1 -DownloadSources

This builds the candidate under .cache and does not modify an installed Python
environment. Review tools/libsndfile_manifest.json for the expected runtime DLL.
'@
Set-Content -LiteralPath (Join-Path $sndfile 'BUILDING.txt') -Value $sndfileInstructions -Encoding utf8
New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot 'licenses') | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'docs/licenses/Qt-LGPL-3.0.txt') -Destination (Join-Path $bundleRoot 'licenses/Qt-LGPL-3.0.txt') -Force
Copy-Item -LiteralPath (Join-Path $root 'docs/licenses/Qt-GPL-3.0.txt') -Destination (Join-Path $bundleRoot 'licenses/Qt-GPL-3.0.txt') -Force
Write-Output "Verified source archives copied to $bundleRoot/legal/sources."
