param([string]$OutputRoot = "dist")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$distBase = Join-Path $root 'dist'
$distPath = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $root $OutputRoot }))
if ($distPath -ne $distBase -and -not $distPath.StartsWith($distBase + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Portable output must stay within this repository dist directory.'
}
if ($distPath -ne $distBase -and (Test-Path -LiteralPath $distPath)) {
    throw 'Choose a new portable output directory to preserve existing bundles.'
}
$bundle = Join-Path $distPath 'Fauxphonic'
$icon = 'assets/fauxphonic.ico'
$brandImage = 'assets/fauxphonic-icon.png'
if (-not (Test-Path -LiteralPath $brandImage -PathType Leaf)) { throw "App icon image is missing: $brandImage" }
$toolManifest = Get-Content -Raw -LiteralPath 'tools/manifest.json' | ConvertFrom-Json
foreach ($binary in $toolManifest.files.PSObject.Properties) {
    $path = Join-Path 'tools' $binary.Name
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() -ne $binary.Value) {
        throw "Pinned FFmpeg binary hash mismatch: $($binary.Name)"
    }
}
$sndfileManifest = Get-Content -Raw 'tools/libsndfile_manifest.json' | ConvertFrom-Json
$sndfileCandidate = '.cache/sndfile-controlled/package/libsndfile_x64.dll'
if (-not (Test-Path -LiteralPath $sndfileCandidate) -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $sndfileCandidate).Hash.ToLower() -ne $sndfileManifest.dll_sha256) {
    & 'scripts/build_sndfile.ps1' -DownloadSources
    if ($LASTEXITCODE) { throw 'Pinned controlled libsndfile source build failed.' }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $sndfileCandidate).Hash.ToLower() -ne $sndfileManifest.dll_sha256) {
    throw 'Pinned controlled libsndfile checksum mismatch.'
}
$sourceManifest = Get-Content -Raw 'tools/source_manifest.json' | ConvertFrom-Json
$lameManifest = $sourceManifest.ffmpeg.linked_libraries.lame
$lameCandidate = '.cache/lame-controlled/package/libmp3lame.a'
if (-not (Test-Path -LiteralPath $lameCandidate) -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $lameCandidate).Hash.ToLower() -ne $lameManifest.static_library_sha256) {
    & 'scripts/build_lame.ps1' -DownloadSources
    if ($LASTEXITCODE) { throw 'Pinned LAME source build failed.' }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $lameCandidate).Hash.ToLower() -ne $lameManifest.static_library_sha256) {
    throw 'Pinned LAME static library checksum mismatch.'
}
$env:PYINSTALLER_CONFIG_DIR = Join-Path $root '.cache/pyinstaller'
$pythonBase = & '.venv/Scripts/python.exe' -c 'import sys; print(sys.base_prefix)'
# Isolate DLL discovery from unrelated applications on the developer's PATH.
$env:PATH = (Join-Path $root '.venv/Scripts') + ';' + $pythonBase + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
foreach ($variable in @('PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH')) {
    Remove-Item -LiteralPath "Env:$variable" -ErrorAction SilentlyContinue
}
& '.venv/Scripts/python.exe' scripts/make_windows_icon.py
if ($LASTEXITCODE) { throw 'Windows icon conversion failed.' }
if (-not (Test-Path -LiteralPath $icon -PathType Leaf)) { throw "Windows icon was not created: $icon" }
$buildRun = & '.venv/Scripts/python.exe' scripts/storage_run.py begin-build (Join-Path $root '.build-runs')
if ($LASTEXITCODE) { throw 'Could not create an owned build payload.' }
& '.venv/Scripts/python.exe' -m PyInstaller --clean --noconfirm --workpath (Join-Path $buildRun 'payload') --distpath $distPath Fauxphonic.spec
if ($LASTEXITCODE) { throw 'Packaging failed.' }
# The GUI loads the same icon at runtime; the executables also embed it for
# Explorer, task switcher, shortcuts, and file properties.
New-Item -ItemType Directory -Force -Path (Join-Path $bundle '_internal/assets') | Out-Null
Copy-Item -LiteralPath $icon -Destination (Join-Path $bundle '_internal/assets/fauxphonic.ico') -Force
Copy-Item -LiteralPath 'assets/fauxphonic-icon.png' -Destination (Join-Path $bundle '_internal/assets/fauxphonic-icon.png') -Force
# The desktop capability panel reads these relative to PyInstaller's runtime
# root. Keep matching evidence and the model license beside the bundled model.
New-Item -ItemType Directory -Force -Path (Join-Path $bundle '_internal/models') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $bundle '_internal/evidence') | Out-Null
Copy-Item -LiteralPath 'models/silero-vad-LICENSE' -Destination (Join-Path $bundle '_internal/models/silero-vad-LICENSE') -Force
foreach ($evidence in @('silero-artifact-verification.json','leveler-regression.json')) {
    $source = Join-Path 'evidence' $evidence
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required capability evidence is missing: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path (Join-Path $bundle '_internal/evidence') $evidence) -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $bundle 'licenses') | Out-Null
Copy-Item -LiteralPath 'models/silero-vad-LICENSE' -Destination (Join-Path $bundle 'licenses/silero-vad-LICENSE') -Force
Copy-Item -LiteralPath 'docs/licenses/OpenSSL-Apache-2.0.txt' -Destination (Join-Path $bundle 'licenses/OpenSSL-Apache-2.0.txt') -Force
Copy-Item -LiteralPath '.cache/ffmpeg-build/toolchain/w64devkit/COPYING.MinGW-w64-runtime.txt' -Destination (Join-Path $bundle 'licenses/MinGW-w64-runtime.txt') -Force
& 'scripts/prepare_sources.ps1' -Bundle $bundle
if ($LASTEXITCODE) { throw 'Corresponding source staging failed.' }
& '.venv/Scripts/python.exe' scripts/notices.py --bundle $bundle
if ($LASTEXITCODE) { throw 'License/native dependency inventory failed.' }
Copy-Item -LiteralPath THIRD_PARTY_NOTICES.md -Destination $bundle
$forbidden = @('Qt6Pdf.dll','Qt6Qml.dll','Qt6QmlMeta.dll','Qt6QmlModels.dll','Qt6QmlWorkerScript.dll',
               'Qt6Quick.dll','Qt6VirtualKeyboard.dll','ffmpegmediaplugin.dll',
               'qtvirtualkeyboardplugin.dll','qpdf.dll','avcodec-61.dll','avformat-61.dll',
               'avutil-59.dll','swresample-5.dll','swscale-8.dll','qopensslbackend.dll',
               'qgif.dll','qicns.dll','qtga.dll','qtiff.dll','qwbmp.dll','qwebp.dll')
$found = Get-ChildItem -LiteralPath $bundle -File -Recurse | Where-Object { $forbidden -contains $_.Name }
if ($found) { throw ('Unused Qt payload still present: ' + (($found | ForEach-Object FullName) -join ', ')) }
if ((Test-Path (Join-Path $bundle 'README.md')) -or (Test-Path (Join-Path $bundle 'PROJECT_STATE.md'))) {
    throw 'Developer documentation leaked into the user payload.'
}
Get-ChildItem -LiteralPath $bundle -File -Recurse | ForEach-Object {
    $relative = $_.FullName.Substring($bundle.Length + 1)
    if ($relative -ne 'SHA256SUMS.txt') { (Get-FileHash -LiteralPath $_.FullName).Hash.ToLower() + '  ' + $relative }
} | Set-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS.txt') -Encoding utf8
Write-Output "Portable application: $bundle/Fauxphonic.exe"
$buildRecord = Join-Path $root ('.build-runs/' + (Split-Path -Leaf $buildRun) + '.result.json')
@{kind='portable-build'; runtime_acceptance='not run'; checksums=@(Get-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS.txt'))} | ConvertTo-Json | Set-Content -LiteralPath $buildRecord -Encoding utf8
& '.venv/Scripts/python.exe' scripts/storage_run.py complete-build $buildRun $buildRecord
if ($LASTEXITCODE) { throw 'Could not record completed build payload; it will remain protected.' }
