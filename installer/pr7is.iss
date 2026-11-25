; Paragon PR7-IS installer (Inno Setup 6): instant file search, the way Everything finds files.
;   iscc /DAppVersion=0.1.0 installer\pr7is.iss      (after pyinstaller pr7is.spec has made dist\PR7-IS)
;
; Per-user (no admin rights) into %LOCALAPPDATA%\Programs\PR7-IS, with a Start menu entry. Ticked by default:
; PR7-IS starts with Windows in the notification area (--startup on|off), and the index service is installed
; (--install-service, run with administrator rights: Windows asks once; it creates the scheduled task that reads the
; drives' own index). Uninstalling removes the start-up entry and, if there is one, the task (--uninstall-service
; asks for the rights only then).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId is permanent: it's how a new version finds and upgrades the installed one. Never change it.
AppId={{187DC039-E485-4BF2-9C1B-6026AF54C9F8}
AppName=Paragon PR7-IS
AppVersion={#AppVersion}
AppVerName=Paragon PR7-IS {#AppVersion}
AppPublisher=Paragon
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\PR7-IS
DefaultGroupName=Paragon PR7-IS
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=PR7-IS-Setup-{#AppVersion}
SetupIconFile=..\pr7is\icons\app.ico
UninstallDisplayIcon={app}\PR7-IS.exe
UninstallDisplayName=Paragon PR7-IS
WizardStyle=modern
WizardImageFile=wizard-large-1x.bmp,wizard-large-2x.bmp
WizardSmallImageFile=wizard-small-1x.bmp,wizard-small-2x.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start PR7-IS with Windows (in the notification area; Ctrl+Alt+Space searches)"; GroupDescription: "Windows:"
Name: "service"; Description: "Install the index service: searches find everything at once (Windows asks for permission once)"; GroupDescription: "Windows:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PR7-IS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Paragon PR7-IS"; Filename: "{app}\PR7-IS.exe"; Comment: "Instant file search"
Name: "{autodesktop}\Paragon PR7-IS"; Filename: "{app}\PR7-IS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PR7-IS.exe"; Parameters: "--startup on"; Flags: runhidden waituntilterminated; Tasks: startup
Filename: "{app}\PR7-IS.exe"; Parameters: "--startup off"; Flags: runhidden waituntilterminated; Tasks: not startup
Filename: "{app}\PR7-IS.exe"; Parameters: "--install-service"; Verb: "runas"; Flags: shellexec runhidden waituntilterminated; Tasks: service; StatusMsg: "Installing the index service..."
Filename: "{app}\PR7-IS.exe"; Description: "{cm:LaunchProgram,Paragon PR7-IS}"; Flags: nowait postinstall skipifsilent
; Updates (Check for updates runs this installer with /SILENT /relaunch=1): start PR7-IS again when done.
Filename: "{app}\PR7-IS.exe"; Parameters: "--tray"; Flags: nowait runasoriginaluser; Check: Relaunch

[UninstallRun]
Filename: "{app}\PR7-IS.exe"; Parameters: "--unregister"; Flags: runhidden waituntilterminated; RunOnceId: "Unregister"
Filename: "{app}\PR7-IS.exe"; Parameters: "--uninstall-service"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[Code]
// True only when the updater asked for it (/relaunch=1): normal and CI installs behave as before.
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
