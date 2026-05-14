#define MyAppName "AgentChat"
#define MyAppVersion "1.0.0"
#define MyAppExeName "agentchat.exe"
#define ServiceName "AgentChatBroker"

[Setup]
AppId={{6C8E2A1A-7B4F-4A0C-8D8D-AGENTCHAT0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\AgentChat
DefaultGroupName=AgentChat
OutputDir=Output
OutputBaseFilename=AgentChat-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=assets\agentchat.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\agentchat.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\nssm-2.24\win64\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: Is64BitInstallMode
Source: "vendor\nssm-2.24\win32\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion; Check: not Is64BitInstallMode
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{userdocs}\AgentChat"; Permissions: users-modify

[Run]
; Idempotent reinstall — stop & remove if present (errors ignored)
Filename: "{app}\nssm.exe"; Parameters: "stop {#ServiceName}"; Flags: runhidden skipifdoesntexist
Filename: "{app}\nssm.exe"; Parameters: "remove {#ServiceName} confirm"; Flags: runhidden skipifdoesntexist

; Install fresh
Filename: "{app}\nssm.exe"; Parameters: "install {#ServiceName} ""{app}\{#MyAppExeName}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} DisplayName ""AgentChat Broker"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} Description ""Persistent HTTP + MCP broker for AgentChat multi-agent coordination"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} Start SERVICE_AUTO_START"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppDirectory ""{app}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppEnvironmentExtra ""AGENTCHAT_WORKSPACE={userdocs}\AgentChat"" ""AGENTCHAT_HTTP_PORT=8765"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppStdout ""{app}\broker.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppStderr ""{app}\broker.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppRotateFiles 1"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppRotateOnline 1"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppRotateBytes 10485760"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start {#ServiceName}"; Flags: runhidden; StatusMsg: "Starting AgentChat service..."

; Optional finish action
Filename: "http://localhost:8765"; Description: "Open AgentChat in browser"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop {#ServiceName}"; Flags: runhidden; RunOnceId: "StopService"
Filename: "{app}\nssm.exe"; Parameters: "remove {#ServiceName} confirm"; Flags: runhidden; RunOnceId: "RemoveService"

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if Exec('powershell.exe',
          '-NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode <> 0 then
      if MsgBox('Port 8765 is in use. The AgentChat service may fail to start. Continue?', mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
  end;
end;
