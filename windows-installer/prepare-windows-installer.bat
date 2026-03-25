@echo off
REM ============================================================================
REM Siproxylin Windows Installer Preparation Script
REM ============================================================================
REM
REM This script prepares all required files for the Windows installer:
REM 1. Downloads Python 3.11.9 embeddable (~25 MB)
REM 2. Downloads GStreamer runtime bundle (~30-40 MB)
REM 3. Collects vcpkg DLLs from drunk_call_service/bin
REM 4. Organizes files for Inno Setup packaging
REM
REM The final installer will be self-contained with all dependencies bundled.
REM ============================================================================

setlocal enabledelayedexpansion

echo ============================================================================
echo Siproxylin Windows Installer Preparation
echo ============================================================================
echo.

REM Check if we're in the right directory
if not exist "..\version.sh" (
    echo ERROR: Must run from windows-installer directory!
    echo Current dir: %CD%
    exit /b 1
)

REM Create bundle directory
set BUNDLE_DIR=bundle
if not exist "%BUNDLE_DIR%" mkdir "%BUNDLE_DIR%"

REM ============================================================================
REM Step 1: Download Python 3.11.9 Embeddable
REM ============================================================================
echo [1/4] Downloading Python 3.11.9 embeddable...
set PYTHON_ZIP=python-3.11.9-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/%PYTHON_ZIP%

if exist "%BUNDLE_DIR%\%PYTHON_ZIP%" (
    echo Python embeddable already downloaded, skipping...
) else (
    echo Downloading from %PYTHON_URL%
    powershell -Command "& {Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%BUNDLE_DIR%\%PYTHON_ZIP%'}"
    if errorlevel 1 (
        echo ERROR: Failed to download Python embeddable
        exit /b 1
    )
    echo Downloaded %PYTHON_ZIP%
)

REM Extract Python to bundle/python/
if exist "%BUNDLE_DIR%\python" (
    echo Python already extracted, skipping...
) else (
    echo Extracting Python...
    powershell -Command "& {Expand-Archive -Path '%BUNDLE_DIR%\%PYTHON_ZIP%' -DestinationPath '%BUNDLE_DIR%\python' -Force}"
    if errorlevel 1 (
        echo ERROR: Failed to extract Python
        exit /b 1
    )
    echo Python extracted to %BUNDLE_DIR%\python\
)

REM Download get-pip.py for bootstrapping pip
if exist "%BUNDLE_DIR%\python\get-pip.py" (
    echo get-pip.py already downloaded, skipping...
) else (
    echo Downloading get-pip.py...
    powershell -Command "& {Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%BUNDLE_DIR%\python\get-pip.py'}"
    if errorlevel 1 (
        echo WARNING: Failed to download get-pip.py (not critical)
    )
)

REM Uncomment python311._pth to enable site-packages
echo Configuring Python embeddable for pip...
if exist "%BUNDLE_DIR%\python\python311._pth" (
    powershell -Command "(Get-Content '%BUNDLE_DIR%\python\python311._pth') -replace '^#import site', 'import site' | Set-Content '%BUNDLE_DIR%\python\python311._pth'"
    echo Enabled site-packages in python311._pth
)

echo.

REM ============================================================================
REM Step 2: Download or Locate GStreamer Runtime Bundle
REM ============================================================================
echo [2/4] Locating GStreamer runtime bundle...

REM Option A: Download from GitHub Releases (if you've published it)
REM set GSTREAMER_BUNDLE_URL=https://github.com/confund0/siproxylin/releases/download/deps-v1/gstreamer-runtime-x64-windows.zip
REM if exist "%BUNDLE_DIR%\gstreamer-runtime-x64-windows.zip" (
REM     echo GStreamer bundle already downloaded, skipping...
REM ) else (
REM     echo Downloading GStreamer runtime bundle...
REM     powershell -Command "& {Invoke-WebRequest -Uri '%GSTREAMER_BUNDLE_URL%' -OutFile '%BUNDLE_DIR%\gstreamer-runtime-x64-windows.zip'}"
REM )

REM Option B: Create from local GStreamer installation
echo Checking for local GStreamer installation...
set GSTREAMER_ROOT=C:\Program Files\gstreamer\1.0\msvc_x86_64

if not exist "%GSTREAMER_ROOT%\bin" (
    echo WARNING: GStreamer not found at %GSTREAMER_ROOT%
    echo.
    echo Please either:
    echo   1. Install GStreamer MSVC runtime from https://gstreamer.freedesktop.org/download/
    echo   2. Or provide gstreamer-runtime-bundle.zip in %BUNDLE_DIR%\
    echo.
    echo If you already have a pre-made bundle, place it at:
    echo   %BUNDLE_DIR%\gstreamer-runtime-bundle.zip
    echo.
    pause
    exit /b 1
)

REM Create GStreamer bundle from local installation
echo Found GStreamer at %GSTREAMER_ROOT%
echo Creating GStreamer runtime bundle...

REM Create temp directory for collecting GStreamer files
set GSTREAMER_TEMP=%BUNDLE_DIR%\gstreamer_temp
if not exist "%GSTREAMER_TEMP%\bin" mkdir "%GSTREAMER_TEMP%\bin"
if not exist "%GSTREAMER_TEMP%\lib\gstreamer-1.0" mkdir "%GSTREAMER_TEMP%\lib\gstreamer-1.0"

echo Copying minimal GStreamer DLLs (this may take a moment)...

REM Copy essential bin DLLs (from /tmp/gst-bin-dlls.txt)
for %%f in (
    ffi-7.dll
    gio-2.0-0.dll
    glib-2.0-0.dll
    gmodule-2.0-0.dll
    gobject-2.0-0.dll
    gstapp-1.0-0.dll
    gstaudio-1.0-0.dll
    gstbase-1.0-0.dll
    gstcontroller-1.0-0.dll
    gstnet-1.0-0.dll
    gstpbutils-1.0-0.dll
    gstreamer-1.0-0.dll
    gstrtp-1.0-0.dll
    gstrtsp-1.0-0.dll
    gstsdp-1.0-0.dll
    gsttag-1.0-0.dll
    gstvideo-1.0-0.dll
    gstwebrtc-1.0-0.dll
    intl-8.dll
    json-glib-1.0-0.dll
    libcrypto-3-x64.dll
    nice-10.dll
    orc-0.4-0.dll
    pcre2-8-0.dll
    z-1.dll
) do (
    if exist "%GSTREAMER_ROOT%\bin\%%f" (
        copy /Y "%GSTREAMER_ROOT%\bin\%%f" "%GSTREAMER_TEMP%\bin\" >nul
    ) else (
        echo WARNING: Missing %%f
    )
)

REM Copy essential plugins (minimal set for WebRTC)
for %%f in (
    gstcoreelements.dll
    gsttypefindfunctions.dll
    gstwebrtc.dll
    gstdtls.dll
    gstsrtp.dll
    gstnice.dll
    gstrtp.dll
    gstrtpmanager.dll
    gstrtsp.dll
    gstopus.dll
    gstopusparse.dll
    gstaudioconvert.dll
    gstaudioresample.dll
    gstaudiotestsrc.dll
    gstvolume.dll
    gstvpx.dll
    gstvideoconvertscale.dll
    gstvideotestsrc.dll
    gstapp.dll
    gstautodetect.dll
    gstdirectsound.dll
    gstwasapi.dll
    gstwasapi2.dll
) do (
    if exist "%GSTREAMER_ROOT%\lib\gstreamer-1.0\%%f" (
        copy /Y "%GSTREAMER_ROOT%\lib\gstreamer-1.0\%%f" "%GSTREAMER_TEMP%\lib\gstreamer-1.0\" >nul
    ) else (
        echo WARNING: Missing plugin %%f
    )
)

echo GStreamer runtime files collected in %GSTREAMER_TEMP%\
echo.

REM ============================================================================
REM Step 3: Collect vcpkg DLLs from drunk_call_service/bin
REM ============================================================================
echo [3/4] Collecting vcpkg runtime DLLs...

set VCPKG_SOURCE=..\drunk_call_service\bin
set VCPKG_DEST=%BUNDLE_DIR%\vcpkg

if not exist "%VCPKG_SOURCE%" (
    echo ERROR: drunk_call_service/bin not found!
    echo Please build the Windows C++ service first with: make winrel
    exit /b 1
)

if not exist "%VCPKG_DEST%" mkdir "%VCPKG_DEST%"

REM Copy vcpkg DLLs
echo Copying vcpkg runtime DLLs...
for %%f in (
    abseil_dll.dll
    cares.dll
    libprotobuf.dll
    re2.dll
    spdlog.dll
    zlib1.dll
    upb*.dll
) do (
    if exist "%VCPKG_SOURCE%\%%f" (
        copy /Y "%VCPKG_SOURCE%\%%f" "%VCPKG_DEST%\" >nul
    ) else (
        echo WARNING: Missing vcpkg DLL %%f
    )
)

REM Also copy the main executable
if exist "%VCPKG_SOURCE%\drunk-call-service-windows.exe" (
    copy /Y "%VCPKG_SOURCE%\drunk-call-service-windows.exe" "%VCPKG_DEST%\" >nul
    echo Copied drunk-call-service-windows.exe
) else (
    echo WARNING: drunk-call-service-windows.exe not found! Build it with: make winrel
)

echo vcpkg runtime files collected in %VCPKG_DEST%\
echo.

REM ============================================================================
REM Step 4: Verify Application Distribution
REM ============================================================================
echo [4/4] Verifying application distribution...

set DIST_DIR=..\dist\windows

if not exist "%DIST_DIR%" (
    echo WARNING: Application distribution not found at %DIST_DIR%
    echo Please run build-windows.bat first to create the distribution
    echo.
    pause
    exit /b 1
)

echo Application distribution found at %DIST_DIR%
echo.

REM ============================================================================
REM Summary
REM ============================================================================
echo ============================================================================
echo Preparation Complete!
echo ============================================================================
echo.
echo Bundle directory structure:
echo   %BUNDLE_DIR%\
echo     python\                   - Python 3.11.9 embeddable
echo     gstreamer_temp\           - GStreamer runtime files
echo     vcpkg\                    - vcpkg runtime DLLs
echo.
echo Next steps:
echo   1. Review the collected files in %BUNDLE_DIR%\
echo   2. Run build-installer.bat to create the installer
echo   3. Test the installer on a fresh Windows VM
echo.
echo Total estimated installer size: ~80-100 MB (bundled, no downloads)
echo ============================================================================
echo.

pause
