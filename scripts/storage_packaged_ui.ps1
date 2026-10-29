param([Parameter(Mandatory)][string]$Bundle, [Parameter(Mandatory)][string]$Work, [Parameter(Mandatory)][string]$Registry)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class StorageSmokeWindows {
    public delegate bool Callback(IntPtr h, IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback cb, IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, Callback cb, IntPtr data);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder b,int n);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder b,int n);
    [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr h);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,string text);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
    public static IntPtr Find(int process,string title) {
        IntPtr found=IntPtr.Zero;
        EnumWindows((h,d)=> { uint p; GetWindowThreadProcessId(h,out p); var b=new StringBuilder(512); GetWindowText(h,b,512);
            if(p==process && b.ToString().Contains(title)) { found=h; return false; } return true; },IntPtr.Zero);
        return found;
    }
    public static string Titles(int process) {
        var names=new StringBuilder();
        EnumWindows((h,d)=> {uint p; GetWindowThreadProcessId(h,out p); if(p==process) { var b=new StringBuilder(512); GetWindowText(h,b,512); names.AppendLine(b.ToString()); } return true; },IntPtr.Zero);
        return names.ToString();
    }
    public static bool SetFile(IntPtr dialog,string path) {
        IntPtr edit=IntPtr.Zero;
        EnumChildWindows(dialog,(h,d)=> {var cls=new StringBuilder(100); GetClassName(h,cls,100);
            var parent=GetParent(h); var grand=GetParent(parent);
            if(cls.ToString()=="Edit" && (GetDlgCtrlID(h)==1001 || GetDlgCtrlID(h)==1148 || GetDlgCtrlID(parent)==1148 || GetDlgCtrlID(grand)==1148)) edit=h;
            return true; },IntPtr.Zero);
        if(edit==IntPtr.Zero) return false;
        SendMessage(edit,0x000C,IntPtr.Zero,path);
        PostMessage(dialog,0x0111,new IntPtr(1),IntPtr.Zero);
        return true;
    }
}
'@
$scope = [System.Windows.Automation.TreeScope]::Descendants
$invocations = New-Object 'System.Collections.Generic.List[object]'
$result = @{status='running';foreground_input_used=$false;controls=@()}
function Wait-For($predicate, [int]$seconds=30) {
    $deadline=(Get-Date).AddSeconds($seconds)
    do { if (& $predicate) { return }; Start-Sleep -Milliseconds 100 } while ((Get-Date) -lt $deadline)
    throw 'Timed out waiting for the packaged storage UI.'
}
function Window([string]$title) {
    $handle=[StorageSmokeWindows]::Find($app.Id,$title)
    if($handle -ne [IntPtr]::Zero) { return [System.Windows.Automation.AutomationElement]::FromHandle($handle) }
    return $null
}
function Control($window,[string]$name) {
    $condition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,$name)
    return $window.FindFirst($scope,$condition)
}
function Invoke($window,[string]$name) {
    $result.stage=$name
    $element=Control $window $name
    if(-not $element -or -not $element.Current.IsEnabled) { throw "Unavailable packaged control: $name" }
    $result.controls += $name
    $pipeline=[powershell]::Create().AddScript({param($element)
        $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    }).AddArgument($element)
    $pending=$pipeline.BeginInvoke()
    $invocations.Add(@{pipeline=$pipeline;pending=$pending})
}
function File-Dialog([string]$title,[string]$path) {
    Wait-For { $script:dialog=[StorageSmokeWindows]::Find($app.Id,$title); return $script:dialog -ne [IntPtr]::Zero }
    Start-Sleep -Milliseconds 700
    Wait-For { [StorageSmokeWindows]::SetFile($script:dialog,$path) } 10
}
try {
    $app=Start-Process -FilePath (Join-Path $Bundle 'Fauxphonic.exe') -ArgumentList @('--data-dir',('"'+(Join-Path $Work 'gui-data')+'"')) -WindowStyle Hidden -PassThru
    Wait-For { $script:main=Window 'Local Audio Studio'; return $null -ne $script:main }
    $ellipsis=[char]0x2026
    Invoke $script:main ('Storage'+$ellipsis)
    Wait-For { $script:storage=Window 'Storage cleanup preview'; return $null -ne $script:storage }
    foreach($name in @(('Choose folder and preview'+$ellipsis),('Quarantine reviewed payloads'+$ellipsis),('Restore quarantine'+$ellipsis),('Permanently delete reviewed intermediates'+$ellipsis))) {
        if(-not (Control $script:storage $name)) { throw "Missing storage control: $name" }
        $result.controls += $name
    }
    Invoke $script:storage 'Close'
    Wait-For { return $null -eq (Window 'Storage cleanup preview') }
    Wait-For { return $invocations[0].pending.IsCompleted -and $invocations[1].pending.IsCompleted -and $script:main.Current.IsEnabled }
    Invoke $script:main ('Capabilities'+$ellipsis)
    Wait-For {
        $errorWindow=Window 'Capabilities unavailable'
        if($errorWindow) {
            $texts=$errorWindow.FindAll($scope,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object { $_.Current.Name }
            throw ('Packaged capabilities error: '+($texts -join ' | '))
        }
        $script:capabilities=Window 'Capabilities and model status'; return $null -ne $script:capabilities
    }
    Invoke $script:capabilities ('Use shared registry'+$ellipsis)
    File-Dialog 'Choose existing shared model registry' $Registry
    Wait-For { $script:selected=Window 'Existing model selected'; return $null -ne $script:selected }
    Invoke $script:selected 'OK'
    Invoke $script:capabilities 'Close'
    Wait-For { return $null -eq (Window 'Capabilities and model status') }
    $configuration=Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'Fauxphonic/model-store.json') -Raw | ConvertFrom-Json
    $result.model_directory=$configuration.directory
    # Exercise the actual packaged GUI -> queue -> packaged worker render.
    Invoke $script:main ('Open project'+$ellipsis)
    File-Dialog 'Open project' (Join-Path $Work 'gui-project.json')
    Wait-For { $control=Control $script:main ('Export audio'+$ellipsis); return $control -and $control.Current.IsEnabled }
    Invoke $script:main ('Export audio'+$ellipsis)
    File-Dialog 'Export audio' (Join-Path $Work 'gui-final.wav')
    Wait-For { (Test-Path -LiteralPath (Join-Path $Work 'gui-final.wav.report.json')) -and (Control $script:main 'Play export').Current.IsEnabled } 90
    $result.verification=(Get-Content -LiteralPath (Join-Path $Work 'gui-final.wav.report.json') -Raw | ConvertFrom-Json).verification
    if($result.verification.status -ne 'passed') { throw 'Packaged GUI render verification failed.' }
    $result.status='passed'
} catch {
    $result.status='failed'; $result.error=$_.Exception.Message; $result.location=$_.InvocationInfo.PositionMessage
    if($app) { $result.window_titles=[StorageSmokeWindows]::Titles($app.Id) }
    throw
} finally {
    if($app -and -not $app.HasExited) {
        foreach($title in @('Capabilities unavailable','Existing model selected')) {
            $modal=[StorageSmokeWindows]::Find($app.Id,$title)
            if($modal -ne [IntPtr]::Zero) {
                $modalWindow=[System.Windows.Automation.AutomationElement]::FromHandle($modal)
                $ok=Control $modalWindow 'OK'
                if($ok) { $ok.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
            }
        }
        $handle=[StorageSmokeWindows]::Find($app.Id,'Local Audio Studio')
        if($handle -ne [IntPtr]::Zero) { [StorageSmokeWindows]::PostMessage($handle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero) | Out-Null }
        if(-not $app.WaitForExit(15000)) { $result.close_error='Test instance did not close normally; process retained for diagnosis.' }
    }
    $result.invocation_errors=@($invocations | ForEach-Object { $_.pipeline.Streams.Error | ForEach-Object { $_.ToString() } })
    foreach($invocation in $invocations) { $invocation.pipeline.Dispose() }
    $result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $Work 'gui-result.json') -Encoding utf8
    if($result.close_error) { throw $result.close_error }
}
