#ifndef AppVersion
  #define AppVersion "0.18.1"
#endif
[Setup]
AppId={{D423859E-B8CB-4962-BC4C-E5F140F83815}
AppName=Trace2Task
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Trace2Task
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=Trace2Task-Setup-{#AppVersion}-win64
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\Trace2Task.exe
CloseApplications=yes
SetupLogging=yes
[Files]
Source: "..\..\dist\Trace2Task\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Trace2Task"; Filename: "{app}\Trace2Task.exe"
Name: "{autodesktop}\Trace2Task"; Filename: "{app}\Trace2Task.exe"
[Run]
Filename: "{app}\Trace2Task.exe"; Description: "Launch Trace2Task"; Flags: nowait postinstall skipifsilent
; No UninstallDelete section: user data and model directories are never removed.
