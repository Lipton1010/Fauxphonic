param([string]$Compiler = "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath $Compiler)) { throw 'Inno Setup 6 compiler not found. Pass -Compiler with its ISCC.exe path.' }
if (-not (Test-Path -LiteralPath 'dist/Fauxphonic/Fauxphonic.exe')) { throw 'Run scripts/build_windows.ps1 first.' }
foreach ($asset in @('fauxphonic.ico','fauxphonic-icon.png')) {
    $source = Join-Path 'assets' $asset
    $bundled = Join-Path 'dist/Fauxphonic/_internal/assets' $asset
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
foreach ($line in Get-Content -LiteralPath 'dist/Fauxphonic/SHA256SUMS.txt') {
    $hash, $relative = $line -split '  ', 2
    $target = Join-Path 'dist/Fauxphonic' $relative
    if ((Get-FileHash -LiteralPath $target).Hash.ToLower() -ne $hash) { throw "Bundle checksum mismatch: $relative" }
}
& $Compiler "/DAppVersion=$version" 'installer/Fauxphonic.iss' | Tee-Object -Variable compilerOutput
if ($LASTEXITCODE) { throw 'Installer compilation failed.' }
$output = Join-Path $root "dist/installers/Fauxphonic-$version-Setup.exe"
$hash = (Get-FileHash -LiteralPath $output).Hash.ToLower()
"$hash  Fauxphonic-$version-Setup.exe" | Set-Content -LiteralPath ($output + '.sha256') -Encoding utf8
Copy-Item -LiteralPath installer/README-recipient.txt -Destination dist/installers/README-recipient.txt
$compilerVersion = ($compilerOutput | Select-String '^Compiler engine version:').Line
@{ compiler_version=$compilerVersion; compiler_sha256=(Get-FileHash -LiteralPath $Compiler).Hash.ToLower(); installer_sha256=$hash; bytes=(Get-Item -LiteralPath $output).Length; output=$output } | ConvertTo-Json | Set-Content -LiteralPath evidence/installer-build.json -Encoding utf8
Write-Output "Installer ready: $output"
