param([string]$Installer = '', [string]$PreviousInstaller = '', [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Installer) { $Installer = Join-Path $root 'dist\installers\Fauxphonic-0.2.2-Setup.exe' }
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$sandbox = Join-Path $env:WINDIR 'System32\WindowsSandbox.exe'
if (-not (Test-Path -LiteralPath $sandbox)) { throw 'Windows Sandbox is not available; no system features will be installed.' }
$run = Join-Path $root ('.cache\sandbox-installer-' + [Guid]::NewGuid().ToString('N'))
$inputFolder = Join-Path $run 'input'
$outputFolder = Join-Path $run 'results'
New-Item -ItemType Directory -Path $inputFolder,$outputFolder | Out-Null
$setupName = Split-Path -Leaf $Installer
Copy-Item -LiteralPath $Installer -Destination (Join-Path $inputFolder $setupName)
@{name=$setupName} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $inputFolder 'setup-name.json') -Encoding utf8
$setupStem = [IO.Path]::GetFileNameWithoutExtension($Installer)
Get-ChildItem -LiteralPath (Split-Path -Parent $Installer) -Filter ($setupStem + '-*.bin') -File |
    Copy-Item -Destination $inputFolder
if ($PreviousInstaller) {
    Copy-Item -LiteralPath (Resolve-Path -LiteralPath $PreviousInstaller).Path -Destination (Join-Path $inputFolder 'PreviousSetup.exe')
}
Copy-Item -LiteralPath (Join-Path $root 'tests\fixtures\silero-leveling-tts.wav') -Destination (Join-Path $inputFolder 'speech.wav')
$probe = Join-Path $root 'tools\ffprobe.exe'
$sourceInfo = (& $probe -v error -show_entries stream=duration_ts,time_base,sample_rate -of json (Join-Path $inputFolder 'speech.wav')) | ConvertFrom-Json
if ($LASTEXITCODE -or $sourceInfo.streams[0].time_base -ne '1/48000' -or $sourceInfo.streams[0].sample_rate -ne '48000') { throw 'Unexpected acceptance fixture time base.' }
@{ frames = [long]$sourceInfo.streams[0].duration_ts } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $inputFolder 'expected.json') -Encoding utf8
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox_guest.ps1') -Destination (Join-Path $inputFolder 'guest.ps1')
$inputXml = [Security.SecurityElement]::Escape($inputFolder)
$outputXml = [Security.SecurityElement]::Escape($outputFolder)
$configuration = @"
<Configuration>
  <VGpu>Disable</VGpu><Networking>Disable</Networking>
  <AudioInput>Disable</AudioInput><VideoInput>Disable</VideoInput>
  <ClipboardRedirection>Disable</ClipboardRedirection><PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$inputXml</HostFolder><SandboxFolder>C:\FauxphonicInput</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$outputXml</HostFolder><SandboxFolder>C:\FauxphonicResults</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\FauxphonicInput\guest.ps1</Command></LogonCommand>
</Configuration>
"@
$configPath = Join-Path $run 'test.wsb'
$configuration | Set-Content -LiteralPath $configPath -Encoding utf8
Write-Output ('Sandbox results: ' + $outputFolder)
if ($PrepareOnly) { return }
Start-Process -FilePath $sandbox -ArgumentList ('"' + $configPath + '"') -WindowStyle Hidden | Out-Null
$resultPath = Join-Path $outputFolder 'result.json'
$deadline = (Get-Date).AddMinutes(10)
while (-not (Test-Path -LiteralPath $resultPath)) {
    if ((Get-Date) -gt $deadline) { throw ('No result after ten minutes. Inspect only this run: ' + $run) }
    Start-Sleep -Seconds 1
}
$result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
$result | Add-Member -NotePropertyName evidence_folder -NotePropertyValue $outputFolder
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $root 'evidence\sandbox-installer.json') -Encoding utf8
$result | ConvertTo-Json -Depth 20
if ($result.status -ne 'passed') { throw ('Clean Windows installer test failed: ' + $result.error) }
