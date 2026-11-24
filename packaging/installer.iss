; Установщик (Inno Setup 6). Собирается из dist\DayZRvmatStudio после PyInstaller. Права администратора не нужны.
#define AppName "DayZ RVMAT Studio"
#define AppVersion "0.4.1"
[Setup]
AppId={{6B0E4C5A-3D1F-4F7B-9A52-0C7A1D5E8B21}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\DayZRvmatStudio
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=DayZRvmatStudio-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\DayZRvmatStudio.exe
WizardStyle=modern
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked
[Files]
Source: "..\dist\DayZRvmatStudio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\DayZRvmatStudio.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DayZRvmatStudio.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\DayZRvmatStudio.exe"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent
