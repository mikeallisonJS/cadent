; Inno Setup script (#48): wraps the PyInstaller onedir output (scripts/build.py)
; in a per-user installer. Unsigned per the M3 charter — SmartScreen is clicked
; through; per-user install (no UAC) keeps everything under the user profile,
; matching the HKCU autostart key and %LOCALAPPDATA% data dir.
;
; Compile:  ISCC.exe [/DAppVersion=x.y.z] packaging\cadent.iss
; Requires: dist\Cadent\ from scripts/build.py.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{55891DD6-DCFD-4A14-874E-BDB73836F46C}
AppName=Cadent
AppVersion={#AppVersion}
AppPublisher=Mike Allison
AppPublisherURL=https://github.com/mikeallisonJS/cadent
DefaultDirName={autopf}\Cadent
DisableProgramGroupPage=yes
; Per-user: {autopf} resolves to {localappdata}\Programs, no admin prompt.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#SourcePath}\..\dist\installer
OutputBaseFilename=Cadent-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The mark (#73) — the installer, Add/Remove Programs and the Start menu all
; carry it, so the app is recognisable before it has ever been launched.
SetupIconFile={#SourcePath}\icons\cadent.ico
UninstallDisplayIcon={app}\Cadent.exe

[Tasks]
; Off by default per the M3 charter; the Settings UI toggles the same value.
Name: "autostart"; Description: "Start Cadent when you sign in"; Flags: unchecked

[Files]
Source: "{#SourcePath}\..\dist\Cadent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\Cadent"; Filename: "{app}\Cadent.exe"

[Registry]
; Same value cadent/autostart.py manages: quoted exe path under HKCU Run.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "Cadent"; ValueData: """{app}\Cadent.exe"""; \
  Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Cadent.exe"; Description: "Launch Cadent"; \
  Flags: postinstall nowait skipifsilent

[UninstallRun]
; The tray app holds no visible window, so Restart Manager can't close it —
; stop it outright before removing files.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Cadent.exe"; \
  Flags: runhidden; RunOnceId: "KillCadent"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  { Upgrades hit locked files if Cadent is running; stop it first. }
  if CurStep = ssInstall then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM Cadent.exe', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usPostUninstall then
    exit;

  { The Settings UI can enable autostart after install, which the Tasks-gated
    uninsdeletevalue wouldn't cover — always clear the Run value. }
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run',
                 'Cadent');

  { Settings, history, downloaded models, and the GPU support pack live in
    %LOCALAPPDATA%\Cadent and survive uninstall unless the user opts in.
    Silent uninstalls always leave the data. }
  if not UninstallSilent then
    if MsgBox('Also delete your Cadent data (settings, dictation history, '
              + 'downloaded models)?' + #13#10#13#10
              + 'Choose No to keep it for a future install.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{localappdata}\Cadent'), True, True, True);
end;
