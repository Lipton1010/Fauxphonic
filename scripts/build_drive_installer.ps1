$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$compiler = "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe"
$destination = Join-Path $root 'dist/installers/drive-0.2.2'
if (Test-Path -LiteralPath $destination) { throw 'Drive installer folder already exists; preserve the prior build before rebuilding.' }
New-Item -ItemType Directory -Path $destination | Out-Null
& $compiler '/DDriveBundle' '/DAppVersion=0.2.2' ("/O" + $destination) 'installer/Fauxphonic.iss'
if ($LASTEXITCODE) { throw 'Drive installer compilation failed.' }
$files = Get-ChildItem -LiteralPath $destination -File
if ($files.Where({$_.Length -ge 100MB})) { throw 'A delivery component exceeds the connector limit.' }
$files | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName).Hash.ToLower() + '  ' + $_.Name } |
    Set-Content -LiteralPath (Join-Path $destination 'SHA256SUMS.txt') -Encoding ascii
@'
Fauxphonic 0.2.2 - playback, export and cleanup fixes

DOWNLOAD AND INSTALL
1. Download this entire Google Drive folder (Drive creates a ZIP).
2. Right-click the downloaded ZIP and choose Extract All.
3. Close Fauxphonic, then double-click Fauxphonic-0.2.2-Setup.exe.

Keep both .bin files beside the .exe while installing. They are installer data,
not audio files. This is the full installer and supports a fresh installation
or an update from 0.2.0/0.2.1. It does not require downloading anything while
installing or processing audio. Recordings and projects stay unchanged.

The single-file installer exceeded this delivery connector's per-file limit;
these components install the same verified application. SHA256SUMS.txt lists
the component checksums. The installer is unsigned, as earlier previews were.

GET STARTED
For voice-only audio, import it, set Role to Speech and choose Natural speech.
Use -19 LUFS for mono or -16 LUFS for stereo as starting targets. Build an A/B
preview if desired, then choose Export audio and a new WAV or MP3 filename.
The WAV/MP3 is the audio; JSON is the separate measurement report.

This release fixes playback loading/resume, mastering retries, stale previews,
output-name collisions, silent-file reporting and damaged-WAV validation.
Noise cleanup now uses a measured noise profile. Strong reduces more noise;
review it for artifacts. If no safe noise sample is found, cleanup explicitly
bypasses instead of claiming useful processing. Master only adjusts loudness
and peaks, so an already-mastered recording may sound much the same.

Engineering tests include 133 source tests, all five delivery formats at three
speech loudness targets, actual packaged playback/export and offline Windows
installation. No human listening approval or best-possible-quality claim is made.
'@ | Set-Content -LiteralPath (Join-Path $destination 'README-FIRST.txt') -Encoding ascii
Write-Output "Drive installer folder: $destination"
