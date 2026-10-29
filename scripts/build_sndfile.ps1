param([switch]$DownloadSources)
$ErrorActionPreference = 'Stop'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $workspace '.cache'
$archives = Join-Path $cache 'source-archives'
$state = Join-Path $cache 'sndfile-controlled'
$source = Join-Path $state 'source'
$build = Join-Path $state 'build'
$prefix = Join-Path $state 'prefix'
$candidateDir = Join-Path $state 'package'
$toolRoot = Join-Path $cache 'ffmpeg-build\toolchain\w64devkit'
$bin = Join-Path $toolRoot 'bin'
$cmake = Join-Path $bin 'cmake.exe'
$tar = Join-Path $bin 'tar.exe'
$objdump = Join-Path $bin 'objdump.exe'
$toolchainArchiveSha256 = '18d0a4c71a166f8401ab6305781bec5882b40b5e06ba9807c61cb5f3b3c6325e'

$packages = @(
    @{ name = 'libsndfile-1.2.2.tar.xz'; folder = 'libsndfile-1.2.2'; url = 'https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz'; sha256 = '3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e' },
    @{ name = 'libogg-1.3.5.tar.xz'; folder = 'libogg-1.3.5'; url = 'https://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.xz'; sha256 = 'c4d91be36fc8e54deae7575241e03f4211eb102afb3fc0775fbbc1b740016705' },
    @{ name = 'libvorbis-1.3.7.tar.xz'; folder = 'libvorbis-1.3.7'; url = 'https://downloads.xiph.org/releases/vorbis/libvorbis-1.3.7.tar.xz'; sha256 = 'b33cc4934322bcbf6efcbacf49e3ca01aadbea4114ec9589d1b1e9d20f72954b' },
    @{ name = 'flac-1.4.3.tar.xz'; folder = 'flac-1.4.3'; url = 'https://github.com/xiph/flac/releases/download/1.4.3/flac-1.4.3.tar.xz'; sha256 = '6c58e69cd22348f441b861092b825e591d0b822e106de6eb0ee4d05d27205b70' },
    @{ name = 'opus-1.5.2.tar.gz'; folder = 'opus-1.5.2'; url = 'https://github.com/xiph/opus/releases/download/v1.5.2/opus-1.5.2.tar.gz'; sha256 = '65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1' }
)
$sourceManifest = Get-Content -Raw (Join-Path $workspace 'tools/source_manifest.json') | ConvertFrom-Json
$expectedCmakeSha256 = $sourceManifest.libsndfile.toolchain_components.cmake_sha256
$expectedGccSha256 = $sourceManifest.libsndfile.toolchain_components.gcc_sha256
$toolArchive = Join-Path $cache 'ffmpeg-build/w64devkit-x64-2.10.0.7z.exe'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program $($Arguments -join ' ')" }
}

function Configure-BuildInstall([string]$Name, [string]$SourceDir, [string[]]$Options, [string[]]$Targets = @()) {
    $buildDir = "$aliasRoot\.cache\sndfile-controlled\build\$Name"
    $configure = @('--fresh', '-S', $SourceDir, '-B', $buildDir, '-G', 'MinGW Makefiles',
        "-DCMAKE_C_COMPILER=$aliasRoot\.cache\ffmpeg-build\toolchain\w64devkit\bin\x86_64-w64-mingw32-gcc.exe",
        "-DCMAKE_CXX_COMPILER=$aliasRoot\.cache\ffmpeg-build\toolchain\w64devkit\bin\x86_64-w64-mingw32-g++.exe",
        "-DCMAKE_MAKE_PROGRAM=$aliasRoot\.cache\ffmpeg-build\toolchain\w64devkit\bin\make.exe",
        "-DCMAKE_C_FLAGS=-O2 -ffile-prefix-map=$aliasRoot/.cache/sndfile-controlled/source=src -fdebug-prefix-map=$aliasRoot/.cache/sndfile-controlled/source=src",
        "-DCMAKE_CXX_FLAGS=-O2 -ffile-prefix-map=$aliasRoot/.cache/sndfile-controlled/source=src -fdebug-prefix-map=$aliasRoot/.cache/sndfile-controlled/source=src",
        '-DCMAKE_SHARED_LINKER_FLAGS=-Wl,--no-insert-timestamp', '-DCMAKE_EXE_LINKER_FLAGS=-Wl,--no-insert-timestamp',
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', '-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY',
        "-DCMAKE_INSTALL_PREFIX=$aliasRoot/.cache/sndfile-controlled/prefix") + $Options
    Invoke-Checked $cmake $configure
    $buildArgs = @('--build', $buildDir, '--parallel', '8')
    if ($Targets.Count) { $buildArgs += @('--target') + $Targets }
    Invoke-Checked $cmake $buildArgs
    Invoke-Checked $cmake @('--install', $buildDir)
}

foreach ($path in @($archives, $source, $build, $prefix, $candidateDir)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}
if (-not (Test-Path -LiteralPath $cmake)) {
    $entry = $sourceManifest.ffmpeg.toolchain_archive
    if (-not (Test-Path -LiteralPath $toolArchive)) {
        Invoke-WebRequest -Uri $entry.url -OutFile $toolArchive
    }
    if ((Get-FileHash -Algorithm SHA256 $toolArchive).Hash.ToLowerInvariant() -ne $entry.sha256) {
        throw 'Pinned compiler/toolchain archive checksum mismatch.'
    }
    $extractor = Join-Path $env:ProgramFiles '7-Zip/7z.exe'
    $extractRoot = Join-Path $cache 'ffmpeg-build/toolchain'
    if (Test-Path -LiteralPath $extractor) {
        & $extractor x -y "-o$extractRoot" $toolArchive | Out-Null
    } else {
        & $toolArchive -y "-o$extractRoot" | Out-Null
    }
    if ($LASTEXITCODE) { throw 'Could not extract the pinned compiler and CMake toolchain.' }
}
foreach ($package in $packages) {
    $archive = Join-Path $archives $package.name
    if (-not (Test-Path -LiteralPath $archive) -and $DownloadSources) {
        Invoke-WebRequest -Uri $package.url -OutFile $archive
    }
    if (-not (Test-Path -LiteralPath $archive)) { throw "Missing source archive $archive. Use -DownloadSources to fetch pinned upstream sources." }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actual -ne $package.sha256) { throw "Source archive checksum mismatch: $($package.name)" }
    if (-not (Test-Path -LiteralPath (Join-Path $source $package.folder))) {
        Invoke-Checked $tar @('-xf', $archive, '-C', $source)
    }
}
foreach ($program in @($cmake, $tar, $objdump, (Join-Path $bin 'x86_64-w64-mingw32-gcc.exe'), (Join-Path $bin 'make.exe'))) {
    if (-not (Test-Path -LiteralPath $program)) { throw "Required project-local build tool is missing: $program" }
}
$cmakeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $cmake).Hash.ToLowerInvariant()
$gccExecutable = Join-Path $bin 'x86_64-w64-mingw32-gcc.exe'
$gccHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $gccExecutable).Hash.ToLowerInvariant()
if ($cmakeHash -ne $expectedCmakeSha256 -or $gccHash -ne $expectedGccSha256) {
    throw 'Project-local CMake or GCC hash differs from the pinned w64devkit toolchain.'
}

$drive = @('Z','Y','X','W','V','U','T') | Where-Object { -not (Get-PSDrive -Name $_ -ErrorAction SilentlyContinue) } | Select-Object -First 1
if (-not $drive) { throw 'No free drive letter for the temporary no-space build alias.' }
$alias = "$drive`:"
$aliasRoot = "$drive`:\"
Invoke-Checked 'subst.exe' @($alias, $workspace)
$oldPath = $env:PATH
$oldPkgConfigPath = $env:PKG_CONFIG_PATH
$oldSourceDateEpoch = $env:SOURCE_DATE_EPOCH
try {
    $env:PATH = "$aliasRoot\.cache\ffmpeg-build\toolchain\w64devkit\bin;$oldPath"
    $env:PKG_CONFIG_PATH = "$aliasRoot\.cache\sndfile-controlled\prefix\lib\pkgconfig"
    $env:SOURCE_DATE_EPOCH = '1691904136' # libsndfile source archive root mtime: 2023-08-13 05:22:16 UTC
    $static = @('-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=OFF')
    Configure-BuildInstall 'ogg' "$aliasRoot\.cache\sndfile-controlled\source\libogg-1.3.5" `
        ($static + @('-DINSTALL_DOCS=OFF', '-DINSTALL_PKG_CONFIG_MODULE=OFF', '-DINSTALL_CMAKE_PACKAGE_MODULE=ON')) @('ogg')
    Configure-BuildInstall 'flac' "$aliasRoot\.cache\sndfile-controlled\source\flac-1.4.3" `
        ($static + @('-DBUILD_PROGRAMS=OFF', '-DBUILD_EXAMPLES=OFF', '-DBUILD_TESTING=OFF', '-DBUILD_DOCS=OFF',
            '-DBUILD_CXXLIBS=OFF', '-DWITH_OGG=OFF', '-DINSTALL_CMAKE_CONFIG_MODULE=ON')) @('FLAC')
    Configure-BuildInstall 'opus' "$aliasRoot\.cache\sndfile-controlled\source\opus-1.5.2" `
        ($static + @('-DOPUS_BUILD_SHARED_LIBRARY=OFF', '-DOPUS_BUILD_PROGRAMS=OFF', '-DOPUS_BUILD_TESTING=OFF',
            '-DOPUS_INSTALL_PKG_CONFIG_MODULE=ON', '-DOPUS_INSTALL_CMAKE_PACKAGE_MODULE=ON')) @('opus')
    Configure-BuildInstall 'vorbis' "$aliasRoot\.cache\sndfile-controlled\source\libvorbis-1.3.7" `
        ($static + @('-DINSTALL_CMAKE_PACKAGE_MODULE=ON'))
    Configure-BuildInstall 'libsndfile' "$aliasRoot\.cache\sndfile-controlled\source\libsndfile-1.2.2" @(
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON',
        '-DCMAKE_PREFIX_PATH=' + "$aliasRoot/.cache/sndfile-controlled/prefix", '-DBUILD_SHARED_LIBS=ON',
        '-DENABLE_EXTERNAL_LIBS=ON', '-DENABLE_MPEG=OFF', '-DENABLE_EXPERIMENTAL=OFF',
        '-DBUILD_PROGRAMS=OFF', '-DBUILD_EXAMPLES=OFF', '-DBUILD_TESTING=OFF', '-DBUILD_REGTEST=OFF',
        '-DENABLE_CPACK=OFF', '-DENABLE_PACKAGE_CONFIG=OFF', '-DINSTALL_PKGCONFIG_MODULE=OFF',
        '-DHAVE_GMTIME_R:INTERNAL=0')

    $builtDll = "$aliasRoot\.cache\sndfile-controlled\prefix\bin\libsndfile.dll"
    $candidate = Join-Path $candidateDir 'libsndfile_x64.dll'
    Copy-Item -LiteralPath $builtDll -Destination $candidate -Force
    $imports = @(& $objdump -p $builtDll | ForEach-Object {
        if ($_ -match '^\s+DLL Name: (.+)$') { $Matches[1] }
    })
    $unexpected = @($imports | Where-Object { $_ -notin @('ADVAPI32.dll', 'KERNEL32.dll', 'msvcrt.dll') })
    if ($unexpected.Count) { throw "Unexpected non-system DLL imports: $($unexpected -join ', ')" }
    $hashes = @{}
    foreach ($package in $packages) { $hashes[$package.name] = $package.sha256 }
    $cmakeVersion = (& $cmake --version | Select-Object -First 1)
    $evidence = [ordered]@{
        source_archives_sha256 = $hashes
        versions = [ordered]@{ libsndfile = '1.2.2'; libogg = '1.3.5'; libvorbis = '1.3.7'; flac = '1.4.3'; opus = '1.5.2' }
        build = 'SOURCE_DATE_EPOCH=1691904136; CMake 4.4.3, MinGW Makefiles, w64devkit GCC 16.2.0 x86_64; -Wl,--no-insert-timestamp; source/debug prefix map; static codec dependencies; ENABLE_MPEG=OFF'
        toolchain = [ordered]@{
            archive_url = 'https://github.com/skeeto/w64devkit/releases/download/v2.10.0/w64devkit-x64-2.10.0.7z.exe'
            archive_sha256 = $toolchainArchiveSha256
            cmake_version = $cmakeVersion
            cmake_sha256 = $cmakeHash
            gcc_sha256 = $gccHash
        }
        imports = $imports
        dll = [ordered]@{ path = $candidate; size = (Get-Item $candidate).Length; sha256 = (Get-FileHash -Algorithm SHA256 $candidate).Hash.ToLowerInvariant() }
    }
    $evidence | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 (Join-Path $state 'build-evidence.json')
    $evidence | ConvertTo-Json -Depth 6
}
finally {
    $env:PATH = $oldPath
    if ($null -eq $oldPkgConfigPath) { Remove-Item Env:PKG_CONFIG_PATH -ErrorAction SilentlyContinue }
    else { $env:PKG_CONFIG_PATH = $oldPkgConfigPath }
    if ($null -eq $oldSourceDateEpoch) { Remove-Item Env:SOURCE_DATE_EPOCH -ErrorAction SilentlyContinue }
    else { $env:SOURCE_DATE_EPOCH = $oldSourceDateEpoch }
    & subst.exe $alias /d | Out-Null
}
