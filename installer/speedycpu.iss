; SPDX-License-Identifier: LGPL-2.0-or-later
; Copyright (C) 2026 SpeedyCPU contributors
;
; Inno Setup script for SpeedyCPU.
;
;   iscc installer\speedycpu.iss /DAppVersion=0.1.0
;
; Expects the release folder to have been staged first:
;   scripts\package_release.ps1 -Version 0.1.0
;
; All paths are relative to this file (installer\), so the script works from
; any working directory. Override the defaults with /D when the bundle was
; staged somewhere else (CI does exactly that):
;   /DStageDir=C:\out\SpeedyCPU-0.1.0-win64  /DOutputDir=C:\out
;
; Two details that matter more than the rest of this file:
;
;  1. ``CurUninstallStepChanged`` runs ``s-cpu off`` before removing files.
;     Uninstalling a tool that has raised process priorities without putting
;     them back would leave the machine in a state the user cannot easily see
;     or fix - and the process that did it would be gone.
;
;  2. The PATH entry is written to the *user* environment, not the system one,
;     so no reboot is needed and nothing global is modified.

#define AppName "SpeedyCPU"
#define AppPublisher "SpeedyCPU contributors"
#define AppExeName "SpeedyCPU.exe"

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#ifndef StageDir
  #define StageDir "..\..\release\SpeedyCPU-" + AppVersion + "-win64"
#endif

#ifndef OutputDir
  #define OutputDir "..\..\release"
#endif

[Setup]
AppId={{8F3C1A54-2B7E-4C61-9E3D-5A7B1C0E4F92}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinese.CreateCmdFile=s-cpu 命令
chinese.AddToPath=将安装目录加入 PATH（使 s-cpu 命令在任何 CMD 中可用）
chinese.EnableNow=立即启用加速（执行 s-cpu on）
chinese.AutoStart=登录 Windows 时自动启用加速
chinese.Restoring=正在还原所有加速设置...
english.CreateCmdFile=s-cpu command
english.AddToPath=Add the install folder to PATH (so s-cpu works in any CMD window)
english.EnableNow=Enable the boost now (runs s-cpu on)
english.AutoStart=Enable the boost automatically at logon
english.Restoring=Restoring all boost settings...

[Tasks]
Name: "addtopath"; Description: "{cm:AddToPath}"; GroupDescription: "{cm:CreateCmdFile}"
Name: "autostart"; Description: "{cm:AutoStart}"; Flags: unchecked
Name: "enablenow"; Description: "{cm:EnableNow}"; Flags: checkedonce

[Files]
; Core CLI
Source: "{#StageDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\s-cpu.cmd"; DestDir: "{app}"; Flags: ignoreversion
; Engines - optional: a bundle may ship only some of them.
Source: "{#StageDir}\scpu-watch.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#StageDir}\scpu-timer.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#StageDir}\scpu-agent.jar"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Paperwork
Source: "{#StageDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\COPYING"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#StageDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#StageDir}\README-PORTABLE.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName} 状态"; Filename: "{app}\{#AppExeName}"; Parameters: "status"
Name: "{group}\{#AppName} 环境自检"; Filename: "{app}\{#AppExeName}"; Parameters: "doctor"
Name: "{group}\关闭并还原 {#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "off"

[Registry]
; PATH for the current user only: no reboot, nothing global touched.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Check: NeedsAddPath; Tasks: addtopath
; Optional autostart: a logon command, not a service, so it dies with the session.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SpeedyCPU"; ValueData: """{app}\{#AppExeName}"" on --quiet"; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "on"; \
    Description: "{cm:EnableNow}"; Flags: postinstall runhidden nowait skipifsilent; Tasks: enablenow

[UninstallRun]
; Belt and braces: also handled in code below, in case the user cancels out of
; a silent uninstall path.
Filename: "{app}\{#AppExeName}"; Parameters: "off"; Flags: runhidden; RunOnceId: "SpeedyCPUOff"

[Code]
function NeedsAddPath(): Boolean;
var
  Existing: String;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Existing) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(ExpandConstant('{app}')) + ';',
                ';' + Uppercase(Existing) + ';') = 0;
end;

procedure RemoveFromPath();
var
  Existing, Updated, Entry: String;
  Position: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Existing) then
    exit;
  Entry := ';' + Uppercase(ExpandConstant('{app}')) + ';';
  Updated := ';' + Uppercase(Existing) + ';';
  Position := Pos(Entry, Updated);
  if Position = 0 then
    exit;
  Delete(Updated, Position, Length(Entry) - 1);
  if (Length(Updated) > 0) and (Updated[1] = ';') then
    Delete(Updated, 1, 1);
  if (Length(Updated) > 0) and (Updated[Length(Updated)] = ';') then
    Delete(Updated, Length(Updated), 1);
  RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Updated);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Put the machine back before the binary that knows how to disappears.
    if FileExists(ExpandConstant('{app}\{#AppExeName}')) then
    begin
      Log('Restoring all boost settings before uninstall');
      Exec(ExpandConstant('{app}\{#AppExeName}'), 'off --quiet', '',
           SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    RemoveFromPath();
  end;
end;
