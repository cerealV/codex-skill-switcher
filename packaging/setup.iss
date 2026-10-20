#define AppVersion "1.0.1"

[Setup]
AppId={{B89DE78B-44C9-42BD-BAA4-36F52AC9C610}
AppName=Codex Skill Switcher
AppVersion={#AppVersion}
AppPublisher=cerealV
AppPublisherURL=https://github.com/cerealV/codex-skill-switcher
AppSupportURL=https://github.com/cerealV/codex-skill-switcher/issues
DefaultDirName={localappdata}\Programs\CodexSkillSwitcher
DefaultGroupName=Codex Skill Switcher
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\CodexSkillSwitcher.exe
OutputDir=..\dist
OutputBaseFilename=CodexSkillSwitcher-{#AppVersion}-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "..\dist\CodexSkillSwitcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Codex Skill Switcher"; Filename: "{app}\CodexSkillSwitcher.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Codex Skill Switcher"; Filename: "{app}\CodexSkillSwitcher.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\CodexSkillSwitcher.exe"; Description: "{cm:LaunchProgram,Codex Skill Switcher}"; Flags: nowait postinstall skipifsilent
