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

; Read version from version.sh - search for SIPROXYLIN_VERSION line
#define VersionLine ""
#expr FileHandle = FileOpen("..\\version.sh", 0)
#sub ProcessLine
  #define CurrentLine FileRead(FileHandle, 32768)
  #if Pos("SIPROXYLIN_VERSION", CurrentLine) > 0
    #define VersionLine CurrentLine
  #endif
#endsub
#for {FileHandle; FileHandle && !FileEof(FileHandle) && VersionLine == ""; ""} ProcessLine
#expr FileClose(FileHandle)
#if VersionLine == ""
  #error Could not find SIPROXYLIN_VERSION in version.sh
#endif
; Extract version: SIPROXYLIN_VERSION="v0.0.27" -> v0.0.27
#define FirstQuote Pos('"', VersionLine)
#define SecondQuote Pos('"', Copy(VersionLine, FirstQuote + 1, 32768))
#define AppVersion Copy(VersionLine, FirstQuote + 1, SecondQuote - 1)

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
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile={#ProjectRoot}\LICENSE
InfoBeforeFile={#ProjectRoot}\README.md
OutputDir={#ProjectRoot}\dist
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

