param([string]$Bundle = 'dist/Fauxphonic')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class ReleaseWindows {
    public delegate bool Callback(IntPtr hwnd, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumWindows(Callback call, IntPtr data);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, Callback cb, IntPtr data);
    [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, string text);
    public static IntPtr Find(int process, string title) { IntPtr found=IntPtr.Zero; EnumWindows((h,d)=> { uint p; GetWindowThreadProcessId(h,out p); var t=new StringBuilder(512); GetWindowText(h,t,512); var cls=new StringBuilder(100); GetClassName(h,cls,100); if(p==process && t.ToString().Contains(title) && (title=="Local Audio Studio" || cls.ToString()=="#32770")) {found=h; return false;} return true; },IntPtr.Zero); return found; }
    public static bool SetFile(IntPtr dialog, string path) {
        IntPtr edit=IntPtr.Zero, button=IntPtr.Zero;
        EnumChildWindows(dialog,(h,d)=> { var cls=new StringBuilder(100); GetClassName(h,cls,100);
            if(GetDlgCtrlID(h)==1 && cls.ToString()=="Button") button=h;
            if(cls.ToString()=="Edit") { var parent=GetParent(h); var grand=GetParent(parent);
                if(GetDlgCtrlID(h)==1001 || GetDlgCtrlID(h)==1148 || GetDlgCtrlID(parent)==1148 || GetDlgCtrlID(grand)==1148) edit=h;
            } return true; },IntPtr.Zero);
        if(edit==IntPtr.Zero) return false;
        ShowWindow(dialog,4);
        SetForegroundWindow(dialog);
        SendMessage(edit,0x000C,IntPtr.Zero,path);
        if(button!=IntPtr.Zero) PostMessage(button,0x00F5,IntPtr.Zero,IntPtr.Zero);
        else PostMessage(dialog,0x0111,new IntPtr(1),IntPtr.Zero);
        return true;
    }
}
'@
$root = Split-Path -Parent $PSScriptRoot
$bundlePath = (Resolve-Path -LiteralPath (Join-Path $root $Bundle)).Path
$work = Join-Path $root ('.cache/packaged-ui-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$source = Join-Path $work 'speech.wav'
Copy-Item -LiteralPath (Join-Path $root 'tests/fixtures/silero-leveling-tts.wav') -Destination $source
$sourceHash = (Get-FileHash -LiteralPath $source).Hash
$projectPath = Join-Path $work 'session.json'
@{tracks=@(@{path=$source});preset=@{mode='master-only';target_lufs=-16}} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $projectPath -Encoding ascii
$result = @{work=$work;gui_sha256=(Get-FileHash -LiteralPath (Join-Path $bundlePath 'Fauxphonic.exe')).Hash;human_listening='not performed';status='running'}
$scope = [System.Windows.Automation.TreeScope]::Descendants
$invocations = New-Object 'System.Collections.Generic.List[object]'
function Wait-For($condition, [int]$seconds=30) {
    $deadline = (Get-Date).AddSeconds($seconds)
    do { if (& $condition) { return }; Start-Sleep -Milliseconds 100 } while ((Get-Date) -lt $deadline)
    throw 'Timed out waiting for packaged GUI acceptance condition.'
}
function Find-Control([string]$name) {
    $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name)
    return $script:window.FindFirst($scope, $condition)
}
function Invoke-Control([string]$name) {
    $result.stage = $name
    Write-Output "Testing: $name"
    $element = Find-Control $name
    if (-not $element -or -not $element.Current.IsEnabled) { throw "Unavailable control: $name" }
    # Qt can keep Invoke blocked while a native modal file dialog is open.
    $pipeline = [powershell]::Create().AddScript({param($control)
        $control.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    }).AddArgument($element)
    $pending = $pipeline.BeginInvoke()
    $invocations.Add(@{pipeline=$pipeline;pending=$pending})
}
function Complete-FileDialog([string]$path, [string]$buttonName) {
    Wait-For {
        $title = if ($buttonName -eq 'Open') { 'Open project' } else { 'Export audio' }
        $script:dialogHandle = [ReleaseWindows]::Find($app.Id, $title)
        return $script:dialogHandle -ne [IntPtr]::Zero
    }
    # The shell finishes restoring the filename after it creates the dialog.
    Start-Sleep -Milliseconds 700
    Wait-For { return [ReleaseWindows]::SetFile($script:dialogHandle, $path) } 10
    Write-Output "Dialog submitted: $buttonName"
}
try {
    $app = Start-Process -FilePath (Join-Path $bundlePath 'Fauxphonic.exe') -WindowStyle Hidden -PassThru
    Wait-For { $script:handle = [ReleaseWindows]::Find($app.Id, 'Local Audio Studio'); return $script:handle -ne [IntPtr]::Zero }
    $script:window = [System.Windows.Automation.AutomationElement]::FromHandle($script:handle)
    [ReleaseWindows]::ShowWindow($script:handle,4) | Out-Null
    Invoke-Control ('Open project' + [char]0x2026)
    Complete-FileDialog $projectPath 'Open'
    Wait-For { return (Find-Control 'Play original').Current.IsEnabled }
    Invoke-Control 'Play original'
    $slider = (Find-Control 'Playback position').GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern)
    Wait-For { return $slider.Current.Value -gt 2000 }
    $result.original_position_past_two_seconds = $slider.Current.Value
    Invoke-Control 'Pause'
    Start-Sleep -Milliseconds 300
    $paused = $slider.Current.Value
    Start-Sleep -Milliseconds 400
    if ([Math]::Abs($slider.Current.Value-$paused) -gt 100) { throw 'Pause did not hold the playback position.' }
    Invoke-Control 'Play original'
    Wait-For { return $slider.Current.Value -gt $paused+500 }
    Wait-For { return $slider.Current.Maximum -gt 0 -and $slider.Current.Value -ge $slider.Current.Maximum-100 } 30
    $result.original_endpoint_ms = $slider.Current.Value
    $output = Join-Path $work 'gui-master.mp3'
    Invoke-Control ('Export audio' + [char]0x2026)
    Complete-FileDialog $output 'Save'
    Wait-For { return (Test-Path -LiteralPath ($output + '.report.json')) -and (Find-Control 'Play export').Current.IsEnabled } 90
    $report = Get-Content -LiteralPath ($output + '.report.json') -Raw | ConvertFrom-Json
    if ($report.verification.status -ne 'passed') { throw 'Packaged GUI export did not pass verification.' }
    $result.export_verification = $report.verification
    Invoke-Control 'Play export'
    Wait-For { return $slider.Current.Value -gt 2000 -and $slider.Current.Value -lt 8000 }
    Invoke-Control 'Pause'
    Start-Sleep -Milliseconds 300
    $paused = $slider.Current.Value
    Invoke-Control 'Play export'
    Wait-For { return $slider.Current.Value -gt $paused+500 }
    $result.compressed_resume_ms = $slider.Current.Value
    Wait-For { return $slider.Current.Maximum -gt 0 -and $slider.Current.Value -ge $slider.Current.Maximum-100 } 30
    $result.compressed_endpoint_ms = $slider.Current.Value
    $result.source_unchanged = (Get-FileHash -LiteralPath $source).Hash -eq $sourceHash
    if (-not $result.source_unchanged) { throw 'Original source changed.' }
    $result.status = 'passed'
} catch {
    $result.status='failed'; $result.error=$_.Exception.Message; $result.location=$_.InvocationInfo.PositionMessage
    throw
} finally {
    if ($script:handle -ne [IntPtr]::Zero) { [ReleaseWindows]::PostMessage($script:handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }
    if (-not $app.WaitForExit(20000)) { $result.status='failed'; $result.close_error='Did not close normally'; $app.Kill() }
    foreach ($invocation in $invocations) { $invocation.pipeline.Dispose() }
    $result | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath (Join-Path $root 'evidence/packaged-ui-0.2.2.json') -Encoding utf8
    $result | ConvertTo-Json -Depth 15
    if ($result.close_error) { throw $result.close_error }
}
