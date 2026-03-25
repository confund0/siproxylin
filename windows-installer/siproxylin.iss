; ============================================================================
; Siproxylin Windows Installer Script for Inno Setup
; ============================================================================
;
; This script creates a professional Windows installer that:
; - Detects and optionally installs Python 3.11.9
; - Detects and optionally installs GStreamer 1.0
; - Installs Siproxylin to Program Files
; - Creates Start Menu and Desktop shortcuts
; - Generates proper uninstaller
;
; Build: Open in Inno Setup Compiler and click "Compile"
; Or run: build-installer.bat
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
OutputBaseFilename=Siproxylin-Setup-v{#AppVersion}
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
; Optional Components (Prerequisites)
; ============================================================================
[Types]
Name: "full"; Description: "Full installation (includes Python and GStreamer)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "main"; Description: "Siproxylin Application"; Types: full custom; Flags: fixed
Name: "python"; Description: "Python 3.11.9 Runtime (~25 MB)"; Types: full custom; Check: not IsPythonInstalled
Name: "gstreamer"; Description: "GStreamer 1.0 for audio/video calls (~150 MB)"; Types: full custom; Check: not IsGStreamerInstalled

; ============================================================================
; Application Files
; ============================================================================
[Files]
; Main application (from dist/windows built by build-windows.bat)
Source: "..\dist\windows\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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
; Prerequisite Installation (Downloaded on-demand from URLs)
; ============================================================================
[Run]
; Download and install Python 3.11.9 silently with PATH
; Verified params: /quiet = silent install, PrependPath=1 = add to PATH, InstallAllUsers=1 = all users
Filename: "{tmp}\python-3.11.9-amd64.exe"; Parameters: "/quiet PrependPath=1 InstallAllUsers=1"; StatusMsg: "Installing Python 3.11.9..."; Flags: waituntilterminated; Components: python; Check: DownloadPython and not IsPythonInstalled

; Download and install GStreamer silently (Inno Setup .exe installer)
; Verified Inno Setup params from running gstreamer-1.0-msvc-x86_64-1.28.1.exe /?
; /VERYSILENT = completely silent, /SP- = no startup prompt, /SUPPRESSMSGBOXES = suppress dialogs, /NORESTART = no reboot
Filename: "{tmp}\gstreamer-1.0-msvc-x86_64-1.28.1.exe"; Parameters: "/VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART"; StatusMsg: "Installing GStreamer 1.0..."; Flags: waituntilterminated; Components: gstreamer; Check: DownloadGStreamer and not IsGStreamerInstalled

; Install Python dependencies after Python is installed
Filename: "cmd.exe"; Parameters: "/c python -m pip install --upgrade pip"; StatusMsg: "Upgrading pip..."; Flags: runhidden waituntilterminated; Check: WasPythonJustInstalled
Filename: "cmd.exe"; Parameters: "/c cd /d ""{app}"" && python -m pip install -r requirements.txt"; StatusMsg: "Installing Python dependencies..."; Flags: runhidden waituntilterminated; Check: IsPythonInstalled

; ============================================================================
; Post-Installation Actions
; ============================================================================
[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; ============================================================================
; Uninstaller
; ============================================================================
[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\.siproxylin"
Type: filesandordirs; Name: "{localappdata}\.siproxylin"

; ============================================================================
; Detection and Download Functions (Pascal Code)
; ============================================================================
[Code]
var
  PythonWasInstalled: Boolean;
  DownloadPage: TDownloadWizardPage;

const
  PYTHON_URL = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe';
  GSTREAMER_URL = 'https://gstreamer.freedesktop.org/data/pkg/windows/1.28.1/msvc/gstreamer-1.0-msvc-x86_64-1.28.1.exe';

function IsPythonInstalled: Boolean;
var
  Version: String;
  ResultCode: Integer;
begin
  // Check registry for Python 3.11
  Result := RegQueryStringValue(HKLM, 'Software\Python\PythonCore\3.11\InstallPath', '', Version) or
            RegQueryStringValue(HKCU, 'Software\Python\PythonCore\3.11\InstallPath', '', Version);

  // Also check if python command works
  if not Result then
  begin
    if Exec('cmd.exe', '/c python --version 2>nul | findstr "3.11"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := ResultCode = 0;
  end;
end;

function IsGStreamerInstalled: Boolean;
begin
  // Check if GStreamer directory exists
  Result := DirExists('C:\Program Files\gstreamer\1.0\msvc_x86_64\bin') or
            DirExists('C:\gstreamer\1.0\msvc_x86_64\bin');
end;

function WasPythonJustInstalled: Boolean;
begin
  // Track if Python was installed by this installer (for pip install)
  Result := not PythonWasInstalled and IsPythonInstalled;
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if Progress = ProgressMax then
    Log(Format('Successfully downloaded %s to %s', [Url, FileName]));
  Result := True;
end;

procedure InitializeWizard;
begin
  PythonWasInstalled := IsPythonInstalled;

  // Create download wizard page for prerequisites
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = wpReady then begin
    // Clear download list
    DownloadPage.Clear;

    // Add Python download if component selected and not installed
    if WizardIsComponentSelected('python') and not IsPythonInstalled then
      DownloadPage.Add(PYTHON_URL, 'python-3.11.9-amd64.exe', '');

    // Add GStreamer download if component selected and not installed
    if WizardIsComponentSelected('gstreamer') and not IsGStreamerInstalled then
      DownloadPage.Add(GSTREAMER_URL, 'gstreamer-1.0-msvc-x86_64-1.28.1.exe', '');

    // Download files if any were added
    if DownloadPage.AbortedByUser then
      Result := False
    else begin
      DownloadPage.Show;
      try
        try
          DownloadPage.Download;
          Result := True;
        except
          if DownloadPage.AbortedByUser then
            Log('Download aborted by user.')
          else
            SuppressibleMsgBox(AddPeriod(GetExceptionMessage), mbCriticalError, MB_OK, IDOK);
          Result := False;
        end;
      finally
        DownloadPage.Hide;
      end;
    end;
  end else
    Result := True;
end;

function DownloadPython: Boolean;
begin
  // Python was downloaded if file exists in temp
  Result := FileExists(ExpandConstant('{tmp}\python-3.11.9-amd64.exe'));
end;

function DownloadGStreamer: Boolean;
begin
  // GStreamer was downloaded if file exists in temp
  Result := FileExists(ExpandConstant('{tmp}\gstreamer-1.0-msvc-x86_64-1.28.1.exe'));
end;

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function UnInstallOldVersion(): Integer;
var
  sUnInstallString: String;
  iResultCode: Integer;
begin
  // Return Values:
  // 1 - uninstall string is empty
  // 2 - error executing the UnInstallString
  // 3 - successfully executed the UnInstallString

  // default return value
  Result := 0;

  // get the uninstall string of the old app
  sUnInstallString := GetUninstallString();
  if sUnInstallString <> '' then begin
    sUnInstallString := RemoveQuotes(sUnInstallString);
    if Exec(sUnInstallString, '/SILENT /NORESTART /SUPPRESSMSGBOXES','', SW_HIDE, ewWaitUntilTerminated, iResultCode) then
      Result := 3
    else
      Result := 2;
  end else
    Result := 1;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep=ssInstall) then
  begin
    if (IsUpgrade()) then
    begin
      UnInstallOldVersion();
    end;
  end;
end;
