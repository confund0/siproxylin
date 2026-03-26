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

; Version is extracted by build script and passed via /D command line or included from version-generated.iss
#ifndef AppVersion
  #if FileExists("version-generated.iss")
    #include "version-generated.iss"
  #else
    #define AppVersion "0.0.0-dev"
    #pragma message "WARNING: Using default version " + AppVersion + ". Run prepare-windows-installer.bat to extract version."
  #endif
#endif

; Strip "v" prefix for VersionInfoVersion (needs numeric format like 0.0.27.0)
#if Copy(AppVersion, 1, 1) == "v"
  #define AppVersionNumeric Copy(AppVersion, 2, 999)
#else
  #define AppVersionNumeric AppVersion
#endif

; Path defines
#define BundleDir "bundle"
#define ProjectRoot ".."

[Setup]
; ============================================================================
; Application Identity
; ============================================================================
AppId={{A7E8B2F4-6C3D-4A1B-9E5F-8D2C4B1A6E3F}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersionNumeric}.0
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
ExtraDiskSpaceRequired=1073741824
LicenseFile={#ProjectRoot}\LICENSE
InfoBeforeFile={#ProjectRoot}\README.md
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=Siproxylin-Setup-{#AppVersion}-bundled
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
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
; Python application code (from project root)
Source: "{#ProjectRoot}\main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\version.sh"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\drunk_xmpp\*"; DestDir: "{app}\drunk_xmpp"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\drunk_call_hook\*"; DestDir: "{app}\drunk_call_hook"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectRoot}\siproxylin\*"; DestDir: "{app}\siproxylin"; Flags: ignoreversion recursesubdirs createallsubdirs

; Python 3.11.9 embeddable
Source: "{#BundleDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; GStreamer runtime libraries
Source: "{#BundleDir}\gstreamer\bin\*"; DestDir: "{app}\drunk_call_service\lib\gstreamer\bin"; Flags: ignoreversion
Source: "{#BundleDir}\gstreamer\lib\gstreamer-1.0\*"; DestDir: "{app}\drunk_call_service\lib\gstreamer\lib\gstreamer-1.0"; Flags: ignoreversion

; vcpkg DLLs
Source: "{#BundleDir}\vcpkg\*.dll"; DestDir: "{app}\drunk_call_service\bin"; Flags: ignoreversion

; C++ service exe (must be built first)
Source: "{#ProjectRoot}\drunk_call_service\bin\drunk-call-service-windows.exe"; DestDir: "{app}\drunk_call_service\bin"; Flags: ignoreversion

; Launcher script
Source: "siproxylin-launcher.bat"; DestName: "siproxylin.bat"; DestDir: "{app}"; Flags: ignoreversion

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
; Bootstrap pip in embeddable Python (show console for progress)
Filename: "{app}\python\python.exe"; Parameters: "get-pip.py"; StatusMsg: "Installing pip (1/2)..."; Flags: waituntilterminated

; Install Python dependencies (show console for progress, takes 3-5 minutes, downloads ~760 MB)
Filename: "{app}\python\python.exe"; Parameters: "-m pip install --no-warn-script-location -r ""{app}\requirements.txt"""; StatusMsg: "Installing Python dependencies (2/2) - Downloading ~760 MB, please wait 3-5 minutes..."; Flags: waituntilterminated

; Optional: Launch application after install
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; ============================================================================
; Uninstaller
; ============================================================================
[UninstallDelete]
Type: filesandordirs; Name: "{app}\python\Lib\site-packages"

; NOTE: User data directories are NOT automatically deleted on uninstall.
; Users can manually delete these if desired:
;   %APPDATA%\Siproxylin (config)
;   %LOCALAPPDATA%\Siproxylin (data, logs, cache)
;
; Uncomment these lines to auto-delete user data on uninstall (not recommended):
; Type: filesandordirs; Name: "{userappdata}\Siproxylin"
; Type: filesandordirs; Name: "{localappdata}\Siproxylin"

; ============================================================================
; Registry and Environment Setup
; ============================================================================
[Registry]
; Add GStreamer plugin path to registry (used by bridge.py)
Root: HKLM; Subkey: "Software\{#AppName}"; ValueType: string; ValueName: "GStreamerPath"; ValueData: "{app}\lib\gstreamer"; Flags: uninsdeletekey

