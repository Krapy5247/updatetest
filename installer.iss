; Inno Setup 6 — 與 build_installer.ps1 併用
; 版本：由 ISCC 參數 /DMyAppVersion=x.y.z 傳入（與 version_info.py 對齊）
#ifndef MyAppVersion
  #define MyAppVersion "1.0.1"
#endif
#define MyAppName "Treasure Claw AI Agent"
#define MyAppPublisher "Treasure Claw"
#define MyAppExeName "TreasureClawLauncher.exe"

[Setup]
AppId={{E7F3A2B1-9C4D-5E6F-A0B1-23456789ABCD}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 預設路徑（使用者可在安裝精靈中變更）
DefaultDirName={localappdata}\Programs\TreasureClaw
; 顯示「選擇目標位置」頁，允許自訂安裝資料夾
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=TreasureClawSetup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\TreasureClawLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
