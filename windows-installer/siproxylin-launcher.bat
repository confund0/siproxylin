@echo off
REM ============================================================================
REM Siproxylin Launcher for Windows Installer
REM ============================================================================
REM This launcher sets up the environment for the bundled installation:
REM - Adds bundled Python to PATH
REM - Adds GStreamer DLLs to PATH
REM - Adds vcpkg DLLs to PATH
REM - Launches Siproxylin
REM ============================================================================

setlocal

REM Get the installation directory (where this script is located)
set INSTALL_DIR=%~dp0
set INSTALL_DIR=%INSTALL_DIR:~0,-1%

REM Add bundled Python to PATH (first, so it takes priority)
set PATH=%INSTALL_DIR%\python;%PATH%

REM Add bundled GStreamer to PATH
set PATH=%INSTALL_DIR%\drunk_call_service\lib\gstreamer\bin;%PATH%

REM Add vcpkg DLLs (in drunk_call_service/bin) to PATH
set PATH=%INSTALL_DIR%\drunk_call_service\bin;%PATH%

REM Set GStreamer plugin path
set GST_PLUGIN_PATH=%INSTALL_DIR%\drunk_call_service\lib\gstreamer\lib\gstreamer-1.0
set GST_PLUGIN_SYSTEM_PATH=%INSTALL_DIR%\drunk_call_service\lib\gstreamer\lib\gstreamer-1.0

REM Set PYTHONPATH to find our modules
set PYTHONPATH=%INSTALL_DIR%

REM Launch Siproxylin
echo Starting Siproxylin...
cd /d "%INSTALL_DIR%"
python main.py %*

REM Keep window open on error
if errorlevel 1 (
    echo.
    echo Siproxylin exited with an error.
    pause
)
