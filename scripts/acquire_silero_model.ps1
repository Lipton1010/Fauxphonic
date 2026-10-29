param([string]$RegistryPath)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if ($RegistryPath) {
    & '.venv/Scripts/python.exe' run_worker.py resolve-model --registry $RegistryPath --select
    if ($LASTEXITCODE) { throw 'Exact shared model resolution failed. No download was attempted.' }
    Write-Output 'Existing pinned model selected without copying or downloading.'
    return
}
$target = Join-Path $root 'models'
New-Item -ItemType Directory -Force -Path $target | Out-Null
$existingModelJson = & '.venv/Scripts/python.exe' run_worker.py resolve-model --verify
$reuseModel = $null
if ($LASTEXITCODE -eq 0) { $reuseModel = ($existingModelJson | ConvertFrom-Json).path }
$commit = '7e30209a3e901f9842f81b225f3e93d8199902b1'
$files = @{
    'silero_vad.onnx' = @{
        Uri = "https://raw.githubusercontent.com/snakers4/silero-vad/$commit/src/silero_vad/data/silero_vad.onnx"
        Sha256 = '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3'
    }
    'silero-vad-LICENSE' = @{
        Uri = "https://raw.githubusercontent.com/snakers4/silero-vad/$commit/LICENSE"
        Sha256 = '2e63e9a38b6e8fc0c7bc37ce174caca1862870856c6daf5697cfb785e925520b'
    }
}
foreach ($name in $files.Keys) {
    $destination = Join-Path $target $name
    $lockPath = $destination + '.download-lock'
    # CreateNew fails on concurrent/stale ownership; never steal another run's lock.
    $artifactLock = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $temporary = Join-Path $target ($name + '.' + [guid]::NewGuid().ToString('N') + '.download')
    try {
        if (Test-Path -LiteralPath $destination) {
            if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $files[$name].Sha256) {
                throw "Existing artifact differs from the pin; preserved without downloading: $destination"
            }
            Write-Output "Reusing verified $destination"
            continue
        }
        if ($name -eq 'silero_vad.onnx' -and $reuseModel) {
            # Staging an explicit portable-build input from verified existing bytes.
            [System.IO.File]::Copy($reuseModel, $temporary, $false)
        } else {
            Invoke-WebRequest -Uri $files[$name].Uri -OutFile $temporary
        }
        $actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $files[$name].Sha256) { throw "SHA-256 mismatch for $name." }
        [System.IO.File]::Move($temporary, $destination)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
        $artifactLock.Dispose()
        Remove-Item -LiteralPath $lockPath
    }
}
Write-Output 'Pinned Silero VAD model and MIT license installed under models/.'
exit 0
