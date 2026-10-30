#ifndef AppVersion
  #define AppVersion "0.2.3"
#endif
#ifndef BundleDir
  #define BundleDir "..\dist\Fauxphonic"
#endif
#ifndef InstallerDir
  #define InstallerDir "..\dist\installers"
#endif

[Setup]
AppId={{F829F72B-6B34-4A74-A41F-0473369C4D93}
AppName=Fauxphonic
AppVersion={#AppVersion}
AppPublisher=Fauxphonic
DefaultDirName={localappdata}\Programs\Fauxphonic
DefaultGroupName=Fauxphonic
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#InstallerDir}
OutputBaseFilename=Fauxphonic-{#AppVersion}-Setup
SetupIconFile=..\assets\fauxphonic.ico
Compression=lzma2
SolidCompression=yes
#ifdef DriveBundle
DiskSpanning=yes
DiskSliceSize=95000000
SlicesPerDisk=1
#endif
WizardStyle=modern
AppMutex=Local\Fauxphonic-ApplicationOrWorker
UninstallDisplayIcon={app}\Fauxphonic.exe
DisableProgramGroupPage=no
AllowNoIcons=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
InfoBeforeFile=README.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Fauxphonic"; Filename: "{app}\Fauxphonic.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Fauxphonic.exe"; AppUserModelID: "Fauxphonic.LocalAudioStudio"
Name: "{group}\Uninstall Fauxphonic"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Fauxphonic"; Filename: "{app}\Fauxphonic.exe"; WorkingDir: "{app}"; IconFilename: "{app}\Fauxphonic.exe"; AppUserModelID: "Fauxphonic.LocalAudioStudio"; Tasks: desktopicon

[Run]
Filename: "{app}\Fauxphonic.exe"; Description: "Launch Fauxphonic"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  InstalledVersion: String;
begin
  Result := True;
  if not RegQueryStringValue(HKCU64,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F829F72B-6B34-4A74-A41F-0473369C4D93}_is1',
    'DisplayVersion', InstalledVersion) then
    RegQueryStringValue(HKCU32,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F829F72B-6B34-4A74-A41F-0473369C4D93}_is1',
      'DisplayVersion', InstalledVersion);
  if InstalledVersion = '0.1.0' then
  begin
    SuppressibleMsgBox('The withdrawn Fauxphonic 0.1.0 preview cannot be updated in place. Close it, uninstall Fauxphonic 0.1.0 from Windows Settings, then install this version. Your recordings, projects, exports, and application data are preserved.', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

// Deliberately no UninstallDelete rules. Recordings, projects, queue, and caches
// belong to the user; uninstall removes only files installed by this package.
