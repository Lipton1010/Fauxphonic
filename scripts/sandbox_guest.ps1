# Executed only inside the disposable Windows Sandbox prepared by sandbox_smoke.ps1.
$ErrorActionPreference = 'Stop'
if ($env:USERNAME -ne 'WDAGUtilityAccount') { throw 'This script is only for Windows Sandbox.' }
$results = 'C:\FauxphonicResults'
$application = Join-Path $env:LOCALAPPDATA 'Programs\Fauxphonic'
$result = [ordered]@{
    status = 'running'
    platform = [Environment]::OSVersion.VersionString
    user = $env:USERNAME
    python_command_present = [bool](Get-Command python.exe -ErrorAction SilentlyContinue)
    network = 'Disabled by Windows Sandbox configuration'
    gpu = 'Virtual GPU disabled; CPU processing only'
    listening_evaluation = 'Not performed'
}
$gui = $null
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class SandboxWindows {
    public delegate bool Callback(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool EnumWindows(Callback callback, IntPtr parameter);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
    public static IntPtr Find(int pid, string title) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hwnd, IntPtr ignored) {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner == pid) {
                var text = new StringBuilder(1024);
                GetWindowText(hwnd, text, text.Capacity);
                if (text.ToString().Contains(title)) { found = hwnd; return false; }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@

function Run-TestProcess([string]$File, [string]$Arguments, [string]$Name, [int]$Timeout = 180000) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $results ($Name + '.stdout.txt')) `
        -RedirectStandardError (Join-Path $results ($Name + '.stderr.txt'))
    $null = $process.Handle
    if (-not $process.WaitForExit($Timeout)) {
        $process.Kill()
        throw ($Name + ' timed out; its owned process was stopped.')
    }
    $process.Refresh()
    return $process.ExitCode
}

try {
    $setupName = (Get-Content -LiteralPath 'C:\FauxphonicInput\setup-name.json' -Raw | ConvertFrom-Json).name
    if ([IO.Path]::GetFileName($setupName) -ne $setupName) { throw 'Invalid setup filename.' }
    $setup = Join-Path 'C:\FauxphonicInput' $setupName
    $result.installer_sha256 = (Get-FileHash -LiteralPath $setup).Hash.ToLower()
    $legacyKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{F829F72B-6B34-4A74-A41F-0473369C4D93}_is1'
    if (Test-Path -LiteralPath $legacyKey) { throw 'The disposable guest unexpectedly has a Fauxphonic install.' }
    New-Item -Path $legacyKey -Force | Out-Null
    try {
        New-ItemProperty -LiteralPath $legacyKey -Name DisplayVersion -Value '0.1.0' -PropertyType String | Out-Null
        $result.legacy_upgrade_exit = Run-TestProcess $setup '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=C:\FauxphonicResults\legacy-refusal.log' 'legacy-refusal' 30000
        if ($result.legacy_upgrade_exit -eq 0 -or (Test-Path -LiteralPath (Join-Path $application 'Fauxphonic.exe'))) {
            throw 'The withdrawn 0.1.0 registry marker did not prevent in-place installation.'
        }
    } finally {
        Remove-Item -LiteralPath $legacyKey -Force
    }
    $upgradeSentinel = Join-Path $application 'upgrade-user-project.json'
    if (Test-Path -LiteralPath 'C:\FauxphonicInput\PreviousSetup.exe') {
        $result.previous_installer_sha256 = (Get-FileHash -LiteralPath 'C:\FauxphonicInput\PreviousSetup.exe').Hash.ToLower()
        $result.previous_install_exit = Run-TestProcess 'C:\FauxphonicInput\PreviousSetup.exe' '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=C:\FauxphonicResults\previous-install.log' 'previous-install'
        if ($result.previous_install_exit -ne 0) { throw 'Previous version installation failed.' }
        $result.upgrade_from_version = (Get-ItemProperty -LiteralPath $legacyKey).DisplayVersion
        '{"test":"preserve this user project through upgrade and uninstall"}' | Set-Content -LiteralPath $upgradeSentinel -Encoding utf8
        $upgradeSentinelHash = (Get-FileHash -LiteralPath $upgradeSentinel).Hash
    }
    $result.install_exit = Run-TestProcess $setup '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=desktopicon /LOG=C:\FauxphonicResults\install.log' 'install'
    if ($result.install_exit -ne 0) { throw 'Installer failed.' }
    $result.installed_version = (Get-ItemProperty -LiteralPath $legacyKey).DisplayVersion
    if ($upgradeSentinelHash) {
        $result.upgrade_user_project_preserved = (Get-FileHash -LiteralPath $upgradeSentinel).Hash -eq $upgradeSentinelHash
        if (-not $result.upgrade_user_project_preserved -or $result.installed_version -eq $result.upgrade_from_version) {
            throw 'Upgrade did not change version or preserve user project.'
        }
    }
    $worker = Join-Path $application 'Fauxphonic-worker.exe'
    $app = Join-Path $application 'Fauxphonic.exe'
    if (-not (Test-Path -LiteralPath $worker) -or -not (Test-Path -LiteralPath $app)) { throw 'Installed executables are missing.' }
    $source = Join-Path $application 'personal-recording.wav'
    Copy-Item -LiteralPath 'C:\FauxphonicInput\speech.wav' -Destination $source
    $sourceHash = (Get-FileHash -LiteralPath $source).Hash
    $result.doctor_exit = Run-TestProcess $worker 'doctor --json' 'doctor'
    if ($result.doctor_exit -ne 0) { throw 'Installed worker doctor failed.' }
    $arguments = 'process "' + $source + '" --preset natural-speech --output C:\FauxphonicResults\master.wav'
    $result.render_exit = Run-TestProcess $worker $arguments 'render'
    if ($result.render_exit -ne 0) { throw 'Installed natural-speech render failed.' }
    $report = Get-Content -LiteralPath 'C:\FauxphonicResults\master.wav.report.json' -Raw | ConvertFrom-Json
    $result.verification = $report.verification
    $result.leveler_enabled = $report.leveling.enabled
    $result.source_unchanged = ((Get-FileHash -LiteralPath $source).Hash -eq $sourceHash)
    $expected = Get-Content -LiteralPath 'C:\FauxphonicInput\expected.json' -Raw | ConvertFrom-Json
    $result.expected_frames = $expected.frames
    if ($report.verification.samples.frames -ne $expected.frames) { throw 'Export frame count differs from the independently probed source.' }
    if ($report.verification.status -ne 'passed' -or -not $report.leveling.enabled -or -not $result.source_unchanged) {
        throw 'Rendered export did not pass verification, run leveling, or preserve its source.'
    }
    $ffmpeg = Join-Path $application '_internal\tools\ffmpeg.exe'
    $ffprobe = Join-Path $application '_internal\tools\ffprobe.exe'
    $opus = Join-Path $results 'playback-source.opus'
    $result.opus_encode_exit = Run-TestProcess $ffmpeg ('-hide_banner -nostdin -i "' + $source + '" -map 0:a:0 -c:a libopus -b:a 64k "' + $opus + '"') 'playback-opus-encode'
    if ($result.opus_encode_exit -ne 0 -or -not (Test-Path -LiteralPath $opus)) { throw 'Installed FFmpeg could not encode the playback fixture to Opus.' }
    $opusHash = (Get-FileHash -LiteralPath $opus).Hash
    $playbackData = Join-Path $env:LOCALAPPDATA 'Fauxphonic\Fauxphonic'
    $previews = Join-Path $playbackData 'previews'
    [IO.Directory]::CreateDirectory($previews) | Out-Null
    $key = [Guid]::NewGuid().ToString('N')
    $active = Join-Path $previews ('preview-active-' + $key)
    $requestPath = Join-Path $playbackData ($key + '.request.json')
    $cancelPath = Join-Path $playbackData ($key + '.cancel')
    $request = [ordered]@{ action = 'playback'; path = $opus; stream = $null; active_dir = $active
        data_dir = $playbackData; request_path = $requestPath; cancel_file = $cancelPath }
    $utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [IO.File]::WriteAllText($requestPath, ($request | ConvertTo-Json -Depth 5), $utf8NoBom)
    $result.playback_worker_exit = Run-TestProcess $worker ('worker "' + $requestPath + '"') 'playback-worker'
    if ($result.playback_worker_exit -ne 0) { throw 'Installed worker failed to prepare Opus native-playback audio.' }
    $workerEvents = @()
    foreach ($line in (Get-Content -LiteralPath (Join-Path $results 'playback-worker.stdout.txt'))) {
        try { $workerEvents += ($line | ConvertFrom-Json) } catch { }
    }
    $complete = $workerEvents | Where-Object { $_.stage -eq 'complete' -and $_.playback } | Select-Object -Last 1
    if (-not $complete) { throw 'Installed playback worker did not emit a complete playback event.' }
    $audition = $complete.playback.path
    $auditionProbe = (& $ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,channels,duration_ts,time_base -of json $audition) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $auditionProbe.streams.Count -ne 1) { throw 'Installed ffprobe could not independently inspect the audition WAV.' }
    $auditionStream = $auditionProbe.streams[0]
    $timeBase = $auditionStream.time_base -split '/'
    $frameNumerator = [long]$auditionStream.duration_ts * [long]$timeBase[0] * [long]$auditionStream.sample_rate
    $independentFrames = [long][math]::Ceiling([double]$frameNumerator / [long]$timeBase[1])
    $result.playback_acceptance = [ordered]@{
        source_expected_frames = [long]$expected.frames
        worker_presentation_frames = [long]$complete.playback.frames
        independent_audition_frames = $independentFrames
        sample_rate = [int]$auditionStream.sample_rate
        channels = [int]$auditionStream.channels
        codec = $auditionStream.codec_name
        selected_stream = [int]$complete.playback.stream
        source_unchanged = ((Get-FileHash -LiteralPath $source).Hash -eq $sourceHash)
        opus_unchanged = ((Get-FileHash -LiteralPath $opus).Hash -eq $opusHash)
        audition_path = $audition
        human_listening = 'Not performed'
    }
    if ($independentFrames -ne [long]$expected.frames -or [long]$complete.playback.frames -ne [long]$expected.frames -or
        $auditionStream.codec_name -ne 'pcm_s24le' -or [int]$auditionStream.sample_rate -ne 48000 -or
        -not $result.playback_acceptance.source_unchanged -or -not $result.playback_acceptance.opus_unchanged) {
        throw 'Installed-worker Opus audition did not preserve the independently measured source timeline or PCM24 delivery.'
    }
    $resolvedActive = (Resolve-Path -LiteralPath $active).Path
    $resolvedPreviews = (Resolve-Path -LiteralPath $previews).Path
    if ((Split-Path -Parent $resolvedActive) -ne $resolvedPreviews -or
        (Split-Path -Leaf $resolvedActive) -ne ('preview-active-' + $key)) {
        throw 'Refusing cleanup outside the owned playback test folder.'
    }
    Remove-Item -LiteralPath $resolvedActive -Recurse -Force
    Remove-Item -LiteralPath $requestPath -Force
    Remove-Item -LiteralPath (Join-Path $playbackData ($key + '.worker.pid')) -Force -ErrorAction SilentlyContinue
    $gui = Start-Process -FilePath $app -WorkingDirectory $application -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $results 'gui.stdout.txt') `
        -RedirectStandardError (Join-Path $results 'gui.stderr.txt')
    $null = $gui.Handle
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 200
        $gui.Refresh()
        $nativeWindow = [SandboxWindows]::Find($gui.Id, 'Local Audio Studio')
    } while (-not $gui.HasExited -and $nativeWindow -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline)
    $result.gui_process_exited = $gui.HasExited
    if ($gui.HasExited) { $result.gui_exit = $gui.ExitCode }
    $windowText = New-Object Text.StringBuilder 1024
    if ($nativeWindow -ne [IntPtr]::Zero) { $null = [SandboxWindows]::GetWindowText($nativeWindow, $windowText, $windowText.Capacity) }
    $result.gui_window_title = $windowText.ToString()
    if ($gui.HasExited -or $nativeWindow -eq [IntPtr]::Zero -or $result.gui_window_title -notlike '*Local Audio Studio*') {
        Get-WinEvent -LogName Application -MaxEvents 12 -ErrorAction SilentlyContinue | Format-List TimeCreated,ProviderName,Id,Message | Out-File (Join-Path $results 'application-events.txt')
        throw 'The installed native GUI did not open.'
    }
    $uninstaller = Join-Path $application 'unins000.exe'
    $result.running_uninstall_exit = Run-TestProcess $uninstaller '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=C:\FauxphonicResults\blocked-uninstall.log' 'blocked-uninstall'
    if ($result.running_uninstall_exit -eq 0 -or -not (Test-Path -LiteralPath $app)) { throw 'Uninstall was not refused while the GUI was running.' }
    $null = [SandboxWindows]::PostMessage($nativeWindow, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
    if (-not $gui.WaitForExit(15000)) { throw 'Native GUI did not close normally.' }
    $result.gui_exit = $gui.ExitCode
    if ($result.gui_exit -ne 0) { throw 'Native GUI exited with an error.' }
    $gui = $null
    $result.uninstall_exit = Run-TestProcess $uninstaller '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=C:\FauxphonicResults\uninstall.log' 'uninstall'
    # Inno's initial uninstaller process may return before its temporary child completes.
    $deadline = (Get-Date).AddSeconds(30)
    while ((Test-Path -LiteralPath $app) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    $result.application_removed = -not (Test-Path -LiteralPath $app)
    $result.personal_source_preserved = (Test-Path -LiteralPath $source) -and ((Get-FileHash -LiteralPath $source).Hash -eq $sourceHash)
    $result.export_preserved = Test-Path -LiteralPath 'C:\FauxphonicResults\master.wav'
    if ($upgradeSentinelHash) {
        $result.upgraded_project_preserved_after_uninstall = (Get-FileHash -LiteralPath $upgradeSentinel).Hash -eq $upgradeSentinelHash
        if (-not $result.upgraded_project_preserved_after_uninstall) { throw 'Uninstall changed the upgrade test project.' }
    }
    if ($result.uninstall_exit -ne 0 -or -not $result.application_removed -or -not $result.personal_source_preserved -or -not $result.export_preserved) {
        throw 'Uninstall or personal-file preservation failed.'
    }
    $result.status = 'passed'
} catch {
    $result.status = 'failed'
    $result.error = $_.Exception.Message
    $result.location = $_.InvocationInfo.PositionMessage
} finally {
    if ($gui -and -not $gui.HasExited) { $gui.Kill() }
    $result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $results 'result.json') -Encoding utf8
    Start-Sleep -Seconds 2
    shutdown.exe /s /t 0
}
