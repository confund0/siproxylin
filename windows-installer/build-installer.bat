@echo off
REM ============================================================================
REM Automated build script for Siproxylin Windows Installer
REM ============================================================================

setlocal enabledelayedexpansion

echo ============================================================================
echo Siproxylin Installer Builder
echo ============================================================================
echo.

REM =============================================================================
REM STEP 1: Check for Inno Setup
REM =============================================================================

echo [1/3] Checking for Inno Setup...

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
REM STEP 2: Check for distribution files
REM =============================================================================

echo [2/3] Checking for distribution files...

if not exist "..\dist\windows\" (
    echo ERROR: Distribution not found at ..\dist\windows\
    echo.
    echo Build it first with:
    echo   cd ..
    echo   build-windows.bat
    echo.
    pause
    exit /b 1
)

echo Found: ..\dist\windows\
echo.

REM =============================================================================
REM STEP 3: Compile installer
REM =============================================================================

echo [3/3] Compiling installer with Inno Setup...
echo.
echo NOTE: Prerequisites (Python, GStreamer) will be downloaded on-demand
echo       during installation from official sources (python.org, gstreamer.org)
echo       The installer package will be ~5 MB (no bundled prerequisites)
echo.
echo.

%ISCC_PATH% siproxylin.iss

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
for %%F in (..\dist\Siproxylin-Setup-*.exe) do (
    echo Installer: %%F
    for %%A in ("%%F") do echo Size: %%~zA bytes
)

echo.
echo To test:
echo   Run the installer on a Windows VM or test machine
echo   Check README.md for testing checklist
echo.
echo To distribute:
echo   Upload to GitHub releases:
echo   gh release upload v{VERSION} ..\dist\Siproxylin-Setup-v{VERSION}.exe
echo ============================================================================

endlocal
pause
