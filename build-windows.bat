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

echo [1/5] Checking build output...
if not exist "drunk_call_service\bin\%BINARY_NAME%" (
    echo ERROR: Binary not found in drunk_call_service\bin\
    echo.
    echo Build it first with:
    echo   cd drunk_call_service
    echo   make winrel
    echo.
    echo The Makefile will build the binary and copy all DLLs to bin/
    exit /b 1
)
echo Found: drunk_call_service\bin\%BINARY_NAME%
echo.

REM =============================================================================
REM STEP 2: Create distribution directory
REM =============================================================================

echo [2/5] Creating distribution directory...
if exist "%DIST_DIR%" (
    echo Cleaning old distribution...
    rd /s /q "%DIST_DIR%"
)
mkdir "%DIST_DIR%"
echo Created: %DIST_DIR%
echo.

REM =============================================================================
REM STEP 3: Copy Python application code
REM =============================================================================

echo [3/5] Copying Python application...

REM Main entry point
if not exist "main.py" (
    echo ERROR: main.py not found
    exit /b 1
)
copy "main.py" "%DIST_DIR%\" >nul

REM Python modules
for %%d in (drunk_xmpp drunk_call_hook siproxylin) do (
    if exist "%%d" (
        xcopy /E /I /Q "%%d" "%DIST_DIR%\%%d\" >nul
        echo Copied: %%d/
    ) else (
        echo WARNING: %%d directory not found, skipping
    )
)

REM Version info
if exist "version.sh" copy "version.sh" "%DIST_DIR%\" >nul

echo Copied Python application code
echo.

REM =============================================================================
REM STEP 4: Copy C++ service binary + DLLs
REM =============================================================================

echo [4/5] Copying C++ service (binary + runtime DLLs)...
xcopy /E /I /Q "drunk_call_service\bin" "%DIST_DIR%\drunk_call_service\bin\" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy drunk_call_service\bin\
    exit /b 1
)
echo Copied: drunk_call_service\bin\ (binary + DLLs)
echo.

REM =============================================================================
REM STEP 5: Create launcher scripts and documentation
REM =============================================================================

echo [5/5] Creating launcher scripts...

REM Main launcher
echo @echo off > "%DIST_DIR%\siproxylin.bat"
echo REM Siproxylin Windows Launcher >> "%DIST_DIR%\siproxylin.bat"
echo setlocal >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Check for Python 3.11 >> "%DIST_DIR%\siproxylin.bat"
echo python --version 2^^^>nul ^| findstr /C:"3.11" ^^^>nul >> "%DIST_DIR%\siproxylin.bat"
echo if errorlevel 1 ( >> "%DIST_DIR%\siproxylin.bat"
echo     echo ERROR: Python 3.11 not found! >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     echo Please install Python 3.11.9 from: >> "%DIST_DIR%\siproxylin.bat"
echo     echo   https://www.python.org/downloads/release/python-3119/ >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     echo Make sure to check "Add Python to PATH" during installation! >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     pause >> "%DIST_DIR%\siproxylin.bat"
echo     exit /b 1 >> "%DIST_DIR%\siproxylin.bat"
echo ^) >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Check for GStreamer >> "%DIST_DIR%\siproxylin.bat"
echo if not exist "C:\Program Files\gstreamer\1.0\msvc_x86_64\bin" ( >> "%DIST_DIR%\siproxylin.bat"
echo     echo ERROR: GStreamer not found! >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     echo Please install GStreamer from: >> "%DIST_DIR%\siproxylin.bat"
echo     echo   https://gstreamer.freedesktop.org/download/ >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     echo Install BOTH packages: >> "%DIST_DIR%\siproxylin.bat"
echo     echo   - gstreamer-1.0-msvc-x86_64-*.msi ^(Runtime^) >> "%DIST_DIR%\siproxylin.bat"
echo     echo   - gstreamer-1.0-devel-msvc-x86_64-*.msi ^(Development^) >> "%DIST_DIR%\siproxylin.bat"
echo     echo Choose "Complete" installation >> "%DIST_DIR%\siproxylin.bat"
echo     echo. >> "%DIST_DIR%\siproxylin.bat"
echo     pause >> "%DIST_DIR%\siproxylin.bat"
echo     exit /b 1 >> "%DIST_DIR%\siproxylin.bat"
echo ^) >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Setup environment >> "%DIST_DIR%\siproxylin.bat"
echo set PATH=C:\Program Files\gstreamer\1.0\msvc_x86_64\bin;%%~dp0drunk_call_service\bin;%%PATH%% >> "%DIST_DIR%\siproxylin.bat"
echo set PYTHONPATH=%%~dp0 >> "%DIST_DIR%\siproxylin.bat"
echo. >> "%DIST_DIR%\siproxylin.bat"
echo REM Launch Python application >> "%DIST_DIR%\siproxylin.bat"
echo python "%%~dp0main.py" %%* >> "%DIST_DIR%\siproxylin.bat"

REM Test devices helper
echo @echo off > "%DIST_DIR%\test-devices.bat"
echo call "%%~dp0siproxylin.bat" --test-devices >> "%DIST_DIR%\test-devices.bat"
echo pause >> "%DIST_DIR%\test-devices.bat"

REM README
(
echo Siproxylin - Windows Distribution v!VERSION!
echo ============================================================================
echo.
echo REQUIREMENTS:
echo   - Windows 10/11 x64
echo   - Python 3.11.9 ^(specific version!^)
echo   - GStreamer 1.x for Windows ^(MSVC build^)
echo   - Audio devices ^(microphone + speakers^)
echo.
echo INSTALLATION:
echo.
echo   1. Install Python 3.11.9:
echo      Download: https://www.python.org/downloads/release/python-3119/
echo      IMPORTANT: Check "Add Python to PATH" during installation!
echo.
echo   2. Install GStreamer:
echo      Download: https://gstreamer.freedesktop.org/download/
echo      Install BOTH packages:
echo        - gstreamer-1.0-msvc-x86_64-*.msi ^(Runtime^)
echo        - gstreamer-1.0-devel-msvc-x86_64-*.msi ^(Development^)
echo      Choose "Complete" installation
echo      Default path: C:\gstreamer\1.0\msvc_x86_64
echo.
echo   3. Install Python dependencies:
echo      pip install slixmpp==1.8.5
echo      pip install -r requirements.txt
echo.
echo   4. Extract this folder anywhere
echo.
echo USAGE:
echo   siproxylin.bat                 Start application ^(GUI^)
echo   siproxylin.bat --help          Show help
echo.
echo NOTES:
echo   - All C++ dependencies are included in drunk_call_service/bin/
echo   - Python and GStreamer must be installed separately
echo   - The application will check for dependencies on startup
echo.
echo TROUBLESHOOTING:
echo   - "Python 3.11 not found": Install Python 3.11.9 and add to PATH
echo   - "GStreamer not found": Install both Runtime and Development packages
echo   - Python errors: Install requirements with: pip install -r requirements.txt
echo.
echo DOCUMENTATION:
echo   GitHub: https://github.com/confund0/siproxylin
echo   See docs/ in the repository for full documentation
echo.
) > "%DIST_DIR%\README.txt"

echo Created: siproxylin.bat, test-devices.bat, README.txt
echo.

REM =============================================================================
REM STEP 7: Create ZIP package
REM =============================================================================

echo [7/7] Creating ZIP package...
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
