@echo off
REM ============================================================================
REM Build script for Siproxylin Windows Installer (BUNDLED VERSION)
REM ============================================================================
REM This creates a self-contained installer with all dependencies bundled.
REM Prerequisites: Run prepare-windows-installer.bat first
REM ============================================================================

setlocal enabledelayedexpansion

echo ============================================================================
echo Siproxylin Bundled Installer Builder
echo ============================================================================
echo.

REM =============================================================================
REM STEP 1: Check for Inno Setup
REM =============================================================================

echo [1/4] Checking for Inno Setup...

set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not exist %ISCC_PATH% (
    echo ERROR: Inno Setup not found at %ISCC_PATH%
    echo.
    echo Please install Inno Setup 6 from:
    echo   https://jrsoftware.org/isdl.php
    echo.
    pause
    exit /b 1
)

echo Found: %ISCC_PATH%
echo.

REM =============================================================================
REM STEP 2: Check for application source files
REM =============================================================================

echo [2/4] Checking for application source files...

if not exist "..\main.py" (
    echo ERROR: main.py not found in project root
    pause
    exit /b 1
)

if not exist "..\siproxylin\" (
    echo ERROR: siproxylin module not found
    pause
    exit /b 1
)

echo Found: Application source files
echo.

REM =============================================================================
REM STEP 3: Check for bundled dependencies
REM =============================================================================

echo [3/4] Checking for bundled dependencies...

REM Bundle directory structure
set BUNDLE_DIR=bundle
set PYTHON_BUNDLE=%BUNDLE_DIR%\python
set GST_BUNDLE=%BUNDLE_DIR%\gstreamer
set VCPKG_BUNDLE=%BUNDLE_DIR%\vcpkg

set MISSING_BUNDLES=0

if not exist "%PYTHON_BUNDLE%\python.exe" (
    echo ERROR: Python bundle not found at %PYTHON_BUNDLE%
    set MISSING_BUNDLES=1
)

if not exist "%GST_BUNDLE%\bin\gstreamer-1.0-0.dll" (
    echo ERROR: GStreamer bundle not found at %GST_BUNDLE%
    set MISSING_BUNDLES=1
)

if not exist "%VCPKG_BUNDLE%\abseil_dll.dll" (
    echo ERROR: vcpkg DLLs not found at %VCPKG_BUNDLE%
    set MISSING_BUNDLES=1
)

if %MISSING_BUNDLES%==1 (
    echo.
    echo ERROR: Missing bundled dependencies!
    echo.
    echo Please run prepare-windows-installer.bat first:
    echo   prepare-windows-installer.bat
    echo.
    echo Then build the C++ service:
    echo   cd drunk_call_service ^&^& make winrel
    echo.
    pause
    exit /b 1
)

echo All dependencies found:
echo   - %PYTHON_BUNDLE%
echo   - %GST_BUNDLE%
echo   - %VCPKG_BUNDLE%
echo.

REM =============================================================================
REM STEP 4: Extract version from version.sh
REM =============================================================================

echo [4/5] Extracting version from version.sh...

REM Use findstr to extract the version line
for /f "tokens=2 delims==" %%a in ('findstr "SIPROXYLIN_VERSION" "..\version.sh"') do (
    set VERSION_RAW=%%a
)

REM Remove quotes and spaces only (keep the "v" prefix)
set VERSION_RAW=%VERSION_RAW:"=%
set VERSION_RAW=%VERSION_RAW: =%

REM Create version-generated.iss
echo #define AppVersion "%VERSION_RAW%" > version-generated.iss

echo Extracted version: %VERSION_RAW%
echo Created: version-generated.iss
echo.

REM =============================================================================
REM STEP 5: Compile installer
REM =============================================================================

echo [5/5] Compiling bundled installer with Inno Setup...
echo.
echo NOTE: This installer bundles ALL dependencies (~80-100 MB)
echo       No internet connection required for installation
echo       Works completely offline
echo.

%ISCC_PATH% siproxylin-bundled.iss

if errorlevel 1 (
    echo.
    echo ERROR: Inno Setup compilation failed
    echo Check the output above for errors
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo BUILD SUCCESSFUL
echo ============================================================================

REM Find the generated installer
for %%F in (..\dist\Siproxylin-Setup-*-bundled.exe) do (
    echo Installer: %%F
    for %%A in ("%%F") do (
        set SIZE_BYTES=%%~zA
        set /a SIZE_MB=!SIZE_BYTES! / 1048576
        echo Size: !SIZE_MB! MB ^(%%~zA bytes^)
    )
)

echo.
echo Bundled components:
echo   - Python 3.11.9 embeddable
echo   - GStreamer runtime libraries
echo   - vcpkg runtime DLLs
echo   - Siproxylin application
echo.
echo To test:
echo   1. Copy installer to a fresh Windows 10/11 VM
echo   2. Run the installer (no internet needed)
echo   3. Test audio calls with Conversations.im or Dino
echo   4. Check logs in %%USERPROFILE%%\.siproxylin\logs\
echo.
echo To distribute:
echo   Upload to GitHub releases:
echo   gh release upload v{VERSION} ..\dist\Siproxylin-Setup-v{VERSION}-bundled.exe
echo ============================================================================

endlocal
pause
