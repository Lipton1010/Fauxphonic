param([string]$Compiler = "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe",
      [string]$Bundle = 'dist/Fauxphonic', [string]$OutputDir = 'dist/installers',
      [string]$EvidencePath = 'evidence/installer-build.json')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath $Compiler)) { throw 'Inno Setup 6 compiler not found. Pass -Compiler with its ISCC.exe path.' }
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
$outputPath = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $root $OutputDir }))
if (-not (Test-Path -LiteralPath (Join-Path $bundlePath 'Fauxphonic.exe'))) { throw 'Run scripts/build_windows.ps1 first.' }
foreach ($asset in @('fauxphonic.ico','fauxphonic-icon.png')) {
    $source = Join-Path 'assets' $asset
    $bundled = Join-Path $bundlePath "_internal/assets/$asset"
    if (-not (Test-Path -LiteralPath $bundled) -or
        (Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $bundled).Hash) {
        throw "Portable build is missing the current branded asset: $asset. Rebuild it first."
    }
}
$version = @'
import tomllib
with open("pyproject.toml", "rb") as source:
    print(tomllib.load(source)["project"]["version"])
'@ | & '.venv/Scripts/python.exe' -
if ($LASTEXITCODE) { throw 'Could not read project version.' }
foreach ($line in Get-Content -LiteralPath (Join-Path $bundlePath 'SHA256SUMS.txt')) {
    $hash, $relative = $line -split '  ', 2
    $target = Join-Path $bundlePath $relative
    if ((Get-FileHash -LiteralPath $target).Hash.ToLower() -ne $hash) { throw "Bundle checksum mismatch: $relative" }
}
& $Compiler "/DAppVersion=$version" "/DBundleDir=$bundlePath" "/DInstallerDir=$outputPath" 'installer/Fauxphonic.iss' | Tee-Object -Variable compilerOutput
if ($LASTEXITCODE) { throw 'Installer compilation failed.' }
$output = Join-Path $outputPath "Fauxphonic-$version-Setup.exe"
$hash = (Get-FileHash -LiteralPath $output).Hash.ToLower()
"$hash  Fauxphonic-$version-Setup.exe" | Set-Content -LiteralPath ($output + '.sha256') -Encoding utf8
$compilerVersion = ($compilerOutput | Select-String '^Compiler engine version:').Line
@{ compiler_version=$compilerVersion; compiler_sha256=(Get-FileHash -LiteralPath $Compiler).Hash.ToLower(); installer_sha256=$hash; bytes=(Get-Item -LiteralPath $output).Length; output=$output } | ConvertTo-Json | Set-Content -LiteralPath $EvidencePath -Encoding utf8
Write-Output "Installer ready: $output"
