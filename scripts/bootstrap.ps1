param([string]$Python = "")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    if ($Python) { & $Python -m venv .venv } else { py -3.12 -m venv .venv }
    if ($LASTEXITCODE) { throw 'Python 3.12 is required. Supply -Python with an existing interpreter path.' }
}
& '.venv/Scripts/python.exe' -m ensurepip --upgrade
if ($LASTEXITCODE) { throw 'Could not prepare pip inside the project environment.' }
& '.venv/Scripts/python.exe' -m pip install --require-hashes -r locks/desktop.lock
if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
New-Item -ItemType Directory -Force tools,.cache | Out-Null
$hashes = Get-Content -Raw tools/manifest.json | ConvertFrom-Json
$missing = @($hashes.files.PSObject.Properties | Where-Object { -not (Test-Path -LiteralPath (Join-Path tools $_.Name)) })
if ($missing.Count) {
    & 'scripts/build_ffmpeg.ps1' -Promote
    if ($LASTEXITCODE) { throw 'Pinned local FFmpeg source build failed.' }
}
foreach ($file in $hashes.files.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path tools $file.Name)).Hash.ToLower() -ne $file.Value) { throw ('Pinned checksum mismatch: ' + $file.Name) }
}
$sndfile = Get-Content -Raw 'tools/libsndfile_manifest.json' | ConvertFrom-Json
$sndfilePath = '.cache/sndfile-controlled/package/libsndfile_x64.dll'
if (-not (Test-Path -LiteralPath $sndfilePath) -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $sndfilePath).Hash.ToLower() -ne $sndfile.dll_sha256) {
    & 'scripts/build_sndfile.ps1' -DownloadSources
    if ($LASTEXITCODE) { throw 'Pinned controlled libsndfile source build failed.' }
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $sndfilePath).Hash.ToLower() -ne $sndfile.dll_sha256) {
    throw 'Pinned controlled libsndfile checksum mismatch.'
}
if (-not (Test-Path -LiteralPath 'models/silero_vad.onnx')) {
    & 'scripts/acquire_silero_model.ps1'
}
if ((Get-FileHash -LiteralPath 'models/silero_vad.onnx').Hash.ToLower() -ne '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3') {
    throw 'Pinned Silero VAD checksum mismatch. Repair with scripts/acquire_silero_model.ps1.'
}
& '.venv/Scripts/python.exe' run_worker.py doctor --json
if ($LASTEXITCODE) { throw 'Dependency proof failed.' }
Write-Output 'Ready. Double-click Launch Fauxphonic.vbs.'
