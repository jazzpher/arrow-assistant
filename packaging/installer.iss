; Inno Setup script: wraps dist\Arrow into ArrowSetup.exe (per-user, no admin).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B7E2C1A-9F4D-4E3B-8C2A-A11C0FFEE014}
AppName=Arrow Assistant
AppVersion={#AppVersion}
AppPublisher=jazzpher
DefaultDirName={localappdata}\Programs\Arrow Assistant
DefaultGroupName=Arrow Assistant
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ArrowSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayName=Arrow Assistant

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start Arrow when I sign in to Windows"; Flags: unchecked

[Files]
Source: "..\dist\Arrow\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Arrow Assistant"; Filename: "{app}\Arrow.exe"
Name: "{group}\Uninstall Arrow Assistant"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Arrow Assistant"; Filename: "{app}\Arrow.exe"; Tasks: desktopicon
Name: "{userstartup}\Arrow Assistant"; Filename: "{app}\Arrow.exe"; Tasks: startup

[Run]
Filename: "{app}\Arrow.exe"; Description: "Start Arrow Assistant now"; Flags: nowait postinstall skipifsilent
