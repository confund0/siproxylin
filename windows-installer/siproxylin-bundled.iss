; ============================================================================
; Siproxylin Windows Installer Script (BUNDLED VERSION)
; ============================================================================
;
; This installer bundles ALL dependencies (no downloads required):
; - Python 3.11.9 embeddable (~25 MB)
; - GStreamer runtime libraries (~30 MB)
; - vcpkg runtime DLLs (~10 MB)
; - Siproxylin application files
;
; Total installer size: ~80-100 MB (self-contained, works offline)
;
; Build: Run prepare-windows-installer.bat first, then build this script
; ============================================================================

#define AppName "Siproxylin"
#define AppPublisher "Siproxylin Project"
#define AppURL "https://github.com/confund0/siproxylin"
#define AppExeName "siproxylin.bat"

; Read version from version.sh (format: SIPROXYLIN_VERSION="x.y.z")
#define VersionFile FileOpen("..\version.sh")
#define VersionLine FileRead(VersionFile)
#define AppVersion Copy(VersionLine, Pos('="', VersionLine) + 2, Pos('"', Copy(VersionLine, Pos('="', VersionLine) + 2, 100)) - 1)
#expr FileClose(VersionFile)

[Setup]
; ============================================================================
; Application Identity
; ============================================================================
AppId={{A7E8B2F4-6C3D-4A1B-9E5F-8D2C4B1A6E3F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
InfoBeforeFile=..\README.md
OutputDir=..\dist
OutputBaseFilename=Siproxylin-Setup-v{#AppVersion}-bundled
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
UninstallDisplayIcon={app}\siproxylin\resources\icons\siproxylin.ico

; ============================================================================
; Privileges and Compatibility
; ============================================================================
PrivilegesRequired=admin
MinVersion=10.0

; ============================================================================
; Files to Install
; ============================================================================
[Files]
; Main application (from dist/windows built by build-windows.bat)
Source: "..\dist\windows\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Python 3.11.9 embeddable (from bundle/python)
Source: "bundle\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; GStreamer runtime libraries (from bundle/gstreamer_temp)
Source: "bundle\gstreamer_temp\bin\*"; DestDir: "{app}\lib\gstreamer\bin"; Flags: ignoreversion
Source: "bundle\gstreamer_temp\lib\gstreamer-1.0\*"; DestDir: "{app}\lib\gstreamer\lib\gstreamer-1.0"; Flags: ignoreversion

; vcpkg runtime DLLs (from bundle/vcpkg)
Source: "bundle\vcpkg\*.dll"; DestDir: "{app}\lib"; Flags: ignoreversion
Source: "bundle\vcpkg\drunk-call-service-windows.exe"; DestDir: "{app}\lib"; Flags: ignoreversion

; ============================================================================
; Shortcuts
; ============================================================================
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\siproxylin\resources\icons\siproxylin.ico"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\siproxylin\resources\icons\siproxylin.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

; ============================================================================
; Post-Installation: Install Python Dependencies
; ============================================================================
[Run]
; Bootstrap pip in embeddable Python
Filename: "{app}\python\python.exe"; Parameters: "get-pip.py"; StatusMsg: "Installing pip..."; Flags: runhidden waituntilterminated

; Install Python dependencies
Filename: "{app}\python\python.exe"; Parameters: "-m pip install --no-warn-script-location -r ""{app}\requirements.txt"""; StatusMsg: "Installing Python dependencies (this may take a few minutes)..."; Flags: runhidden waituntilterminated

; Optional: Launch application after install
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; ============================================================================
; Uninstaller
; ============================================================================
[UninstallDelete]
Type: filesandordirs; Name: "{app}\python\Lib\site-packages"
Type: filesandordirs; Name: "{userappdata}\.siproxylin"
Type: filesandordirs; Name: "{localappdata}\.siproxylin"

; ============================================================================
; Registry and Environment Setup
; ============================================================================
[Registry]
; Add GStreamer plugin path to registry (used by bridge.py)
Root: HKLM; Subkey: "Software\{#AppName}"; ValueType: string; ValueName: "GStreamerPath"; ValueData: "{app}\lib\gstreamer"; Flags: uninsdeletekey

