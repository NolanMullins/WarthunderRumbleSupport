; WT Haptics installer (Inno Setup 6)
; -----------------------------------------------------------------------------
; Produces a single setup .exe for the PyInstaller --onedir build in dist_final\WTHaptics.
;
; KEY DESIGN: this is a PER-USER install to %LOCALAPPDATA%\Programs\WT Haptics, NOT Program
; Files. That matters because the app already self-updates by swapping its own --onedir folder
; in place and relaunching (src/winwinghaptics/update/installer.py). A per-user, user-writable
; install location lets that self-updater keep working with NO admin / UAC prompt -- install
; once here, then every future GitHub release lands automatically via the in-app updater. A
; Program Files install would need elevation to overwrite, silently breaking self-update.
;
; Build (local):
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.1.1 installer\WTHaptics.iss
; The version is injected by CI from the git tag; it defaults to 0.0.0-dev for local test builds.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

; Windows VersionInfoVersion must be purely numeric (x.x.x), so derive a numeric core from
; MyAppVersion by dropping any pre-release suffix (e.g. "0.2.0-rc1" -> "0.2.0").
#if Pos("-", MyAppVersion) > 0
  #define NumericVersion Copy(MyAppVersion, 1, Pos("-", MyAppVersion) - 1)
#else
  #define NumericVersion MyAppVersion
#endif

#ifndef BuildDir
  ; default: the onedir build relative to this script (installer\ -> ..\dist_final\WTHaptics)
  #define BuildDir "..\dist_final\WTHaptics"
#endif

#define MyAppName "WT Haptics"
#define MyAppExeName "WTHaptics.exe"
#define MyAppPublisher "Nolan Mullins"
#define MyAppURL "https://github.com/NolanMullins/WarthunderRumbleSupport"

[Setup]
; A STABLE AppId is what lets a new installer upgrade the previous install in place and keep a
; single Add/Remove Programs entry. Never change this GUID for this app.
AppId={{B7E9F2A4-3C8D-4E1F-A05B-6D2C9E4F1A37}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#NumericVersion}

; Per-user install (no admin / no UAC) so the in-app self-updater can overwrite the folder.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\src\winwinghaptics\ui\assets\wt_haptics.ico

; Close a running WT Haptics before installing/uninstalling so its --onedir files aren't locked.
CloseApplications=yes
RestartApplications=no

WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\dist_installer
OutputBaseFilename=WTHaptics-Setup-v{#MyAppVersion}
; The app needs Windows 10+ (winsdk OCR, modern HID/GDI capture).
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller --onedir tree. recursesubdirs + createallsubdirs preserves the layout
; (the _internal folder, tksvg DLL, winsdk, assets) the frozen exe needs.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; NOTE: no [UninstallDelete]. Uninstall removes only what setup installed, so the user's data that
; lives next to the exe in this self-contained app -- winwing_haptics.json (settings),
; hud_calib.json (learned HUD), and any hud_rec_* recordings -- is deliberately LEFT BEHIND.
