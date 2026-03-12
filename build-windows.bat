@echo off
REM ============================================================================
REM Windows Distribution Package Build Script for Siproxylin
REM Creates a portable package with all dependencies
REM ============================================================================

setlocal enabledelayedexpansion

REM =============================================================================
REM CONFIGURATION
REM =============================================================================

set DIST_DIR=dist\windows
set BINARY_NAME=drunk-call-service-windows.exe
set PACKAGE_NAME=Siproxylin-Windows-x64.zip

REM Load version
if exist version.sh (
    for /f "tokens=2 delims==" %%a in ('findstr /r "SIPROXYLIN_VERSION=" version.sh') do set VERSION=%%a
    set VERSION=!VERSION:"=!
) else (
    set VERSION=unknown
)

echo ============================================================================
echo Siproxylin Windows Package Builder v!VERSION!
echo ============================================================================
echo.

REM =============================================================================
REM STEP 1: Check build output
REM =============================================================================

echo [1/6] Checking build output...
if not exist "drunk_call_service\build\Release\%BINARY_NAME%" (
    echo ERROR: Binary not found: drunk_call_service\build\Release\%BINARY_NAME%
    echo.
    echo Build it first with:
    echo   cd drunk_call_service
    echo   make winrel
    echo.
    exit /b 1
)
echo Found: %BINARY_NAME%
echo.

REM =============================================================================
REM STEP 2: Create distribution directory
REM =============================================================================

echo [2/6] Creating distribution directory...
if exist "%DIST_DIR%" (
    echo Cleaning old distribution...
    rd /s /q "%DIST_DIR%"
)
mkdir "%DIST_DIR%"
mkdir "%DIST_DIR%\bin"
echo Created: %DIST_DIR%
echo.

REM =============================================================================
REM STEP 3: Copy binary
REM =============================================================================

echo [3/6] Copying binary...
copy "drunk_call_service\build\Release\%BINARY_NAME%" "%DIST_DIR%\bin\" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy binary
    exit /b 1
)
echo Copied: %BINARY_NAME%
echo.

REM =============================================================================
REM STEP 4: Copy vcpkg runtime DLLs
REM =============================================================================

echo [4/6] Copying vcpkg runtime DLLs...
set VCPKG_BIN=%USERPROFILE%\Desktop\vcpkg\installed\x64-windows\bin
if not exist "%VCPKG_BIN%" (
    echo ERROR: vcpkg bin directory not found: %VCPKG_BIN%
    echo Update the path in this script if vcpkg is elsewhere
    exit /b 1
)

for %%f in ("%VCPKG_BIN%\*.dll") do (
    copy "%%f" "%DIST_DIR%\bin\" >nul 2>&1
)
echo Copied vcpkg DLLs from: %VCPKG_BIN%
echo.

REM =============================================================================
REM STEP 5: Create launcher scripts and documentation
REM =============================================================================

echo [5/6] Creating launcher scripts...

REM Main launcher
echo @echo off > "%DIST_DIR%\siproxylin.bat"
echo REM Siproxylin Windows Launcher >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Check for GStreamer >> "%DIST_DIR%\siproxylin.bat"
echo if not exist "C:\Program Files\gstreamer\1.0\msvc_x86_64\bin" ( >> "%DIST_DIR%\siproxylin.bat"
echo     echo ERROR: GStreamer not found! >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     echo Please install GStreamer first: >> "%DIST_DIR%\siproxylin.bat"
echo     echo   1. Download from: https://gstreamer.freedesktop.org/download/ >> "%DIST_DIR%\siproxylin.bat"
echo     echo   2. Install BOTH Runtime AND Development packages >> "%DIST_DIR%\siproxylin.bat"
echo     echo   3. Choose "Complete" installation >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     pause >> "%DIST_DIR%\siproxylin.bat"
echo     exit /b 1 >> "%DIST_DIR%\siproxylin.bat"
echo ^) >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Add GStreamer to PATH >> "%DIST_DIR%\siproxylin.bat"
echo set PATH=C:\Program Files\gstreamer\1.0\msvc_x86_64\bin;%%PATH%% >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Add local bin to PATH >> "%DIST_DIR%\siproxylin.bat"
echo set PATH=%%~dp0bin;%%PATH%% >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Launch service >> "%DIST_DIR%\siproxylin.bat"
echo "%%~dp0bin\%BINARY_NAME%" %%* >> "%DIST_DIR%\siproxylin.bat"

REM Test devices helper
echo @echo off > "%DIST_DIR%\test-devices.bat"
echo call "%%~dp0siproxylin.bat" --test-devices >> "%DIST_DIR%\test-devices.bat"
echo pause >> "%DIST_DIR%\test-devices.bat"

REM README
(
echo Siproxylin - Windows Binary Distribution v!VERSION!
echo ============================================================================
echo.
echo REQUIREMENTS:
echo   - Windows 10/11 x64
echo   - GStreamer 1.x for Windows ^(MSVC build^)
echo   - Audio devices ^(microphone + speakers^)
echo.
echo INSTALLATION:
echo   1. Install GStreamer:
echo      Download: https://gstreamer.freedesktop.org/download/
echo      Install BOTH packages:
echo        - gstreamer-1.0-msvc-x86_64-*.msi ^(Runtime^)
echo        - gstreamer-1.0-devel-msvc-x86_64-*.msi ^(Development^)
echo      Choose "Complete" installation
echo      Default path: C:\gstreamer\1.0\msvc_x86_64
echo.
echo   2. Extract this folder anywhere
echo.
echo USAGE:
echo   siproxylin.bat --help          Show help
echo   test-devices.bat               List audio devices
echo   siproxylin.bat                 Start service
echo.
echo NOTES:
echo   - All vcpkg dependencies are included in bin/
echo   - GStreamer must be installed separately ^(~500MB^)
echo   - Service runs on localhost:50051 by default
echo.
echo DOCUMENTATION:
echo   See docs/ in the source repository for full documentation
echo   GitHub: https://github.com/confund0/siproxylin
echo.
) > "%DIST_DIR%\README.txt"

echo Created: siproxylin.bat, test-devices.bat, README.txt
echo.

REM =============================================================================
REM STEP 6: Create ZIP package
REM =============================================================================

echo [6/6] Creating ZIP package...
cd dist
powershell -Command "Compress-Archive -Path windows -DestinationPath %PACKAGE_NAME% -Force"
cd ..

if exist "dist\%PACKAGE_NAME%" (
    echo.
    echo ============================================================================
    echo BUILD SUCCESSFUL
    echo ============================================================================
    echo Package: dist\%PACKAGE_NAME%
    for %%A in ("dist\%PACKAGE_NAME%") do echo Size: %%~zA bytes
    echo.
    echo To test locally:
    echo   cd dist\windows
    echo   siproxylin.bat --help
    echo.
    echo To distribute:
    echo   Upload dist\%PACKAGE_NAME% to GitHub releases
    echo ============================================================================
) else (
    echo ERROR: Failed to create ZIP package
    exit /b 1
)

endlocal
