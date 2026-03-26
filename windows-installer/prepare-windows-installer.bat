@echo off
REM ============================================================================
REM Siproxylin Windows Installer Preparation Script
REM ============================================================================
REM
REM This script downloads all required dependencies for Windows installer:
REM 1. Python 3.11.9 embeddable from python.org (~10 MB)
REM 2. GStreamer runtime deps from GitHub releases (~12 MB)
REM 3. vcpkg runtime deps from GitHub releases (~3 MB)
REM
REM Dependencies are extracted to project directories for dev/testing.
REM ============================================================================

setlocal enabledelayedexpansion

REM Read version from version.sh
set VERSION=unknown
if exist "..\version.sh" (
    for /f "tokens=2 delims==" %%a in ('findstr /r "SIPROXYLIN_VERSION=" ..\version.sh') do set VERSION=%%a
    set VERSION=!VERSION:"=!
)

echo ============================================================================
echo Siproxylin Windows Installer Preparation v!VERSION!
echo ============================================================================
echo.

REM Check if we're in the right directory
if not exist "..\version.sh" (
    echo ERROR: Must run from windows-installer directory!
    echo Current dir: %CD%
    exit /b 1
)

REM GitHub release tag for dependencies
set DEPS_TAG=deps-v!VERSION!

echo Downloading dependencies from GitHub release: %DEPS_TAG%
echo.

REM ============================================================================
REM Step 1: Download Python 3.11.9 Embeddable
REM ============================================================================
echo [1/4] Downloading Python 3.11.9 embeddable...
set PYTHON_ZIP=python-3.11.9-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/3.11.9/%PYTHON_ZIP%
set PYTHON_DEST=bundle\python

if exist "%PYTHON_DEST%\python.exe" (
    echo Python embeddable already extracted, skipping...
) else (
    echo Downloading from %PYTHON_URL%
    if not exist "bundle" mkdir "bundle"
    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile 'bundle\%PYTHON_ZIP%'"
    if errorlevel 1 (
        echo ERROR: Failed to download Python embeddable
        exit /b 1
    )
    echo Downloaded %PYTHON_ZIP%

    echo Extracting Python...
    powershell -Command "Expand-Archive -Path 'bundle\%PYTHON_ZIP%' -DestinationPath '%PYTHON_DEST%' -Force"
    if errorlevel 1 (
        echo ERROR: Failed to extract Python
        exit /b 1
    )

    REM Configure Python to enable site-packages for pip
    if exist "%PYTHON_DEST%\python311._pth" (
        powershell -Command "(Get-Content '%PYTHON_DEST%\python311._pth') -replace '^#import site', 'import site' | Set-Content '%PYTHON_DEST%\python311._pth'"
        echo Configured Python for pip support
    )

    REM Download get-pip.py
    echo Downloading get-pip.py...
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PYTHON_DEST%\get-pip.py'"
)

echo Python embeddable ready at: %PYTHON_DEST%
echo.

REM ============================================================================
REM Step 2: Download GStreamer runtime deps from GitHub
REM ============================================================================
echo [2/4] Downloading GStreamer runtime dependencies...
set GST_ZIP=siproxylin-windows-gst-deps-v!VERSION!.zip
set GST_URL=https://github.com/confund0/siproxylin/releases/download/%DEPS_TAG%/%GST_ZIP%
set GST_DEST=..\drunk_call_service\lib\gstreamer

if exist "%GST_DEST%\bin\gstreamer-1.0-0.dll" (
    echo GStreamer deps already extracted, skipping...
) else (
    echo Downloading from GitHub releases: %GST_ZIP%
    powershell -Command "Invoke-WebRequest -Uri '%GST_URL%' -OutFile '%GST_ZIP%'"
    if errorlevel 1 (
        echo ERROR: Failed to download GStreamer deps from GitHub
        echo Make sure the release %DEPS_TAG% exists with %GST_ZIP%
        exit /b 1
    )
    echo Downloaded %GST_ZIP%

    echo Extracting GStreamer deps...
    if not exist "%GST_DEST%" mkdir "%GST_DEST%"
    powershell -Command "Expand-Archive -Path '%GST_ZIP%' -DestinationPath '%GST_DEST%' -Force"
    if errorlevel 1 (
        echo ERROR: Failed to extract GStreamer deps
        exit /b 1
    )
    del "%GST_ZIP%"
)

echo GStreamer deps ready at: %GST_DEST%
echo.

REM ============================================================================
REM Step 3: Download vcpkg runtime deps from GitHub
REM ============================================================================
echo [3/4] Downloading vcpkg runtime dependencies...
set VCPKG_ZIP=siproxylin-windows-vcpkg-deps-v!VERSION!.zip
set VCPKG_URL=https://github.com/confund0/siproxylin/releases/download/%DEPS_TAG%/%VCPKG_ZIP%
set VCPKG_DEST=..\drunk_call_service\bin

if exist "%VCPKG_DEST%\abseil_dll.dll" (
    echo vcpkg deps already extracted, skipping...
) else (
    echo Downloading from GitHub releases: %VCPKG_ZIP%
    powershell -Command "Invoke-WebRequest -Uri '%VCPKG_URL%' -OutFile '%VCPKG_ZIP%'"
    if errorlevel 1 (
        echo ERROR: Failed to download vcpkg deps from GitHub
        echo Make sure the release %DEPS_TAG% exists with %VCPKG_ZIP%
        exit /b 1
    )
    echo Downloaded %VCPKG_ZIP%

    echo Extracting vcpkg deps...
    if not exist "%VCPKG_DEST%" mkdir "%VCPKG_DEST%"
    powershell -Command "Expand-Archive -Path '%VCPKG_ZIP%' -DestinationPath '%VCPKG_DEST%' -Force"
    if errorlevel 1 (
        echo ERROR: Failed to extract vcpkg deps
        exit /b 1
    )
    del "%VCPKG_ZIP%"
)

echo vcpkg deps ready at: %VCPKG_DEST%
echo.

REM ============================================================================
REM Step 4: Verify all dependencies
REM ============================================================================
echo [4/4] Verifying dependencies...

set MISSING=0

REM Check Python
if not exist "%PYTHON_DEST%\python.exe" (
    echo   ERROR: Python not found
    set MISSING=1
) else (
    echo   OK: Python embeddable
)

REM Check GStreamer
if not exist "%GST_DEST%\bin\gstreamer-1.0-0.dll" (
    echo   ERROR: GStreamer bin DLLs not found
    set MISSING=1
) else (
    echo   OK: GStreamer bin DLLs
)

if not exist "%GST_DEST%\lib\gstreamer-1.0" (
    echo   ERROR: GStreamer plugins not found
    set MISSING=1
) else (
    echo   OK: GStreamer plugins
)

REM Check vcpkg
if not exist "%VCPKG_DEST%\abseil_dll.dll" (
    echo   ERROR: vcpkg DLLs not found
    set MISSING=1
) else (
    echo   OK: vcpkg runtime DLLs
)

REM Check if exe was built
if not exist "%VCPKG_DEST%\drunk-call-service-windows.exe" (
    echo   WARNING: drunk-call-service-windows.exe not found
    echo   Build it with: cd drunk_call_service ^&^& make winrel
)

echo.

if %MISSING%==1 (
    echo ============================================================================
    echo PREPARATION FAILED
    echo ============================================================================
    echo Some dependencies are missing. Check errors above.
    exit /b 1
)

echo ============================================================================
echo PREPARATION COMPLETE
echo ============================================================================
echo.
echo Dependencies ready:
echo   Python:    %PYTHON_DEST%
echo   GStreamer: %GST_DEST%
echo   vcpkg:     %VCPKG_DEST%
echo.
echo Next steps:
echo   1. Build the C++ service: cd drunk_call_service ^&^& make winrel
echo   2. Build the installer: build-installer-bundled.bat
echo ============================================================================
echo.

pause
