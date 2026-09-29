#ifndef AppVersion
  #define AppVersion "0.18.8"
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
Source: "..\..\build\desktop-vendor\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "..\..\dist\Trace2Task\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Trace2Task"; Filename: "{app}\Trace2Task.exe"
Name: "{autodesktop}\Trace2Task"; Filename: "{app}\Trace2Task.exe"
[Run]
Filename: "{app}\Trace2Task.exe"; Description: "Launch Trace2Task"; Flags: nowait postinstall skipifsilent
; No UninstallDelete section: user data and model directories are never removed.

[Code]
function WebViewInstalled: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if not WebViewInstalled then begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := '无法启动 WebView2 安装程序，请检查权限后重试。'
    else if not WebViewInstalled then
      Result := 'WebView2 未安装成功（代码 ' + IntToStr(Code) + '）。请联网后重试，软件尚未完成安装。';
  end;
end;
