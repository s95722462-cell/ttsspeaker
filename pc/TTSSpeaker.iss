; TTS 스피커 PC 프로그램 — 설치 프로그램 (Inno Setup 6)
; build.ps1 이 부름:  ISCC /DSrcDir=<onedir 폴더> /DOutDir=<출력 폴더> /DIconFile=<icon.ico> TTSSpeaker.iss
; 관리자 권한 없이 사용자 폴더(%LOCALAPPDATA%\Programs\TTS스피커)에 설치

#ifndef SrcDir
  #define SrcDir "E:\claude_보관\ttsspk_pc\dist\TTS스피커"
#endif
#ifndef OutDir
  #define OutDir "E:\claude_보관\ttsspk_pc"
#endif
#ifndef IconFile
  #define IconFile "icon.ico"
#endif
#define AppName "TTS 스피커"
#define AppVer "1.1.0"
#define AppExe "TTS스피커.exe"

[Setup]
AppId={{B5F73AFC-3DD8-417E-A648-27FC18BCFD2F}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=TTS 스피커
AppPublisherURL=https://s95722462-cell.github.io/ttsspeaker/
AppSupportURL=https://github.com/s95722462-cell/ttsspeaker
AppCopyright=(c) 2026 TTS 스피커
VersionInfoVersion=1.1.0.0
VersionInfoCompany=TTS 스피커
VersionInfoDescription=TTS 스피커 설치 프로그램
VersionInfoProductName=TTS 스피커
VersionInfoProductVersion=1.1.0
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\TTS스피커
DisableProgramGroupPage=yes
DefaultGroupName={#AppName}
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=TTS스피커_설치_v1.1
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; Flags: unchecked
Name: "autostart"; Description: "PC를 켤 때 자동 실행 (트레이에서 계속 듣기)"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "가족 메시지를 소리 내어 읽어 줘요"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
; 앱 설정의 'PC 시작 시 자동 실행' 과 같은 바로가기 (시작 프로그램 폴더 · TTS 스피커.lnk · --tray)
Name: "{userstartup}\TTS 스피커"; Filename: "{app}\{#AppExe}"; Parameters: "--tray"; Tasks: autostart

[Registry]
; 앱이 처음 켜질 때 설치 때 고른 '자동 실행' 을 따르도록 알려 줌
Root: HKCU; Subkey: "Software\TTSSpeaker"; ValueType: dword; ValueName: "Autostart"; ValueData: 1; Tasks: autostart; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\TTSSpeaker"; ValueType: dword; ValueName: "Autostart"; ValueData: 0; Tasks: not autostart; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "TTS 스피커 바로 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 앱이 나중에 만든 자동 실행 바로가기도 지움
Type: files; Name: "{userstartup}\TTS 스피커.lnk"

[Code]
procedure KillApp();
var R: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#AppExe}"', '', SW_HIDE, ewWaitUntilTerminated, R);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillApp();          { 다시 설치(업데이트)할 때 켜져 있으면 끔 }
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  KillApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var D: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    D := ExpandConstant('{localappdata}\TTSSpeaker');
    if DirExists(D) and not UninstallSilent() then
      if MsgBox('설정과 가족 연결 정보도 지울까요?' + #13#10 + #13#10 +
                '[예] 모두 지움 (다시 설치하면 가족이 QR을 새로 찍어야 해요)' + #13#10 +
                '[아니요] 남겨 둠 (다시 설치하면 연결이 그대로 이어져요)', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(D, True, True, True);
  end;
end;
