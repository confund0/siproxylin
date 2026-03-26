# Windows Build and Distribution Guide

Complete reference for building, distributing, and maintaining Siproxylin on Windows.

## Overview

Siproxylin supports **Windows 10/11 x64** via a self-contained Inno Setup installer.

**Architecture**: Python GUI + C++ call service (drunk_call_service) with GStreamer for audio/video

---

## For End Users

### Installation

1. **Download** `Siproxylin-Setup-v{VERSION}-bundled.exe` from [GitHub Releases](https://github.com/confund0/siproxylin/releases)

2. **Run installer** - You'll be asked to choose:
   - **"Install for anyone using this computer"** (requires admin password)
     - Installs to: `C:\Program Files\Siproxylin`
     - Available to all users on the PC
   - **"Install just for me"** (no admin needed)
     - Installs to: `%LOCALAPPDATA%\Programs\Siproxylin`
     - Only available to current user

   Then it will:
   - Install bundled Python 3.11.9 embeddable
   - Install bundled GStreamer runtime (~30 MB)
   - Install bundled vcpkg DLLs
   - Download Python dependencies via pip (~760 MB, requires internet)
   - Create Start Menu and Desktop shortcuts

3. **Launch** via Start Menu or Desktop shortcut

### System Requirements

- **OS**: Windows 10/11 x64
- **Disk Space**: ~1 GB (installer + dependencies)
- **Internet**: Required during installation for pip packages (~760 MB download)
- **Audio**: Microphone and speakers/headset

### Notes

- **slixmpp version**: Windows uses slixmpp 1.8.5 (newer versions require Rust compiler)
- **User data locations**:
  - Config: `%APPDATA%\Siproxylin\` (e.g., `C:\Users\username\AppData\Roaming\Siproxylin\`)
  - Data/Logs/Cache: `%LOCALAPPDATA%\Siproxylin\` (e.g., `C:\Users\username\AppData\Local\Siproxylin\`)
- **Uninstall**: User data is preserved (delete manually from AppData if desired)

---

## For Developers - Building from Source

### Prerequisites

1. **Visual Studio 2019 or 2022** (Community Edition)
   - Install "Desktop development with C++" workload
   - C++17 support required

2. **CMake 3.15+**
   - Download: https://cmake.org/download/

3. **vcpkg** (C++ package manager):
   ```cmd
   git clone https://github.com/Microsoft/vcpkg.git C:\vcpkg
   cd C:\vcpkg
   bootstrap-vcpkg.bat
   vcpkg integrate install
   ```

4. **Install C++ dependencies**:
   ```cmd
   vcpkg install grpc:x64-windows
   vcpkg install protobuf:x64-windows
   vcpkg install spdlog:x64-windows
   vcpkg install glib:x64-windows
   ```

5. **GStreamer** (for building C++ service):
   - Download from https://gstreamer.freedesktop.org/download/
   - Install BOTH runtime + devel packages (MSVC x86_64)
   - Set environment variable:
     ```cmd
     setx GSTREAMER_1_0_ROOT_MSVC_X86_64 "C:\Program Files\gstreamer\1.0\msvc_x86_64"
     ```

6. **Python 3.11.9 + Git for Windows**

### Building C++ Call Service

```bash
cd drunk_call_service
make winrel VCPKG_ROOT=C:/vcpkg
```

**Output**:
- Binary: `drunk_call_service/bin/drunk-call-service-windows.exe`
- vcpkg DLLs automatically copied to `drunk_call_service/bin/`

---

## For Maintainers - Creating Releases

### Building the Installer

**Prerequisites**:
- Inno Setup 6.x: https://jrsoftware.org/isdl.php
- Built C++ service (see above)
- Dependency bundles uploaded to GitHub releases

**Steps**:

1. **Prepare dependencies** (downloads from GitHub releases):
   ```cmd
   cd windows-installer
   prepare-windows-installer.bat
   ```

2. **Build installer**:
   ```cmd
   build-installer-bundled.bat
   ```

3. **Output**: `dist/Siproxylin-Setup-v{VERSION}-bundled.exe` (~100 MB)

4. **Test**: Run installer in clean Windows VM

### Dependency Management

Dependencies are version-tagged and stored as GitHub releases (`deps-v{VERSION}`):

1. **vcpkg runtime DLLs** (~3 MB): grpc, protobuf, spdlog
2. **GStreamer runtime** (~12 MB): Core libraries + WebRTC plugins
3. **Python embeddable** (~10 MB): Downloaded from python.org during prep

**To update dependencies**:

```cmd
REM 1. On Windows dev machine with working build:
cd drunk_call_service/bin
zip ..\..\siproxylin-windows-vcpkg-deps-v{VERSION}.zip *.dll

cd ..\lib\gstreamer
zip ..\..\..\siproxylin-windows-gst-deps-v{VERSION}.zip bin\ lib\

REM 2. Upload to GitHub releases
gh release create deps-v{VERSION} --title "Windows Dependencies v{VERSION}"
gh release upload deps-v{VERSION} siproxylin-windows-*.zip
```

### Installer Configuration

**File**: `windows-installer/siproxylin-bundled.iss`

**Key features**:
- Bundles Python 3.11.9 embeddable
- Bundles GStreamer + vcpkg DLLs
- Downloads pip packages during install (shows console with progress)
- Version info from `version.sh` (auto-extracted by build script)
- Requires ~1 GB disk space
- Preserves user data on uninstall

---

## Technical Details

### PATH Management

**File**: `drunk_call_hook/bridge.py`

When starting the call service, automatically adds bundled libraries to PATH:
- GStreamer: `{app}\drunk_call_service\lib\gstreamer\bin`
- vcpkg DLLs: `{app}\drunk_call_service\bin`
- Sets `GST_PLUGIN_PATH` for GStreamer plugins

### Bundled Dependencies

**vcpkg DLLs** (~50 MB installed):
- gRPC, protobuf, spdlog, abseil, cares, re2

**GStreamer runtime** (~30 MB installed):
- Core libraries: gstreamer, gstbase, gstwebrtc, gstrtp, gstaudio
- Plugins: webrtcbin, dtls, srtp, nice, opus, wasapi, autodetect
- Supporting: GLib, libcrypto, libssl, libnice

**Python packages** (~760 MB downloaded during install):
- PySide6 (Qt6 GUI)
- slixmpp 1.8.5 (XMPP client)
- omemo (encryption)
- grpcio (call service bridge)
- See `requirements.txt` for full list

---

## Troubleshooting

### Build Issues

**vcpkg compilation slow**:
- Normal on first run (45-90 min for grpc)
- Subsequent builds use cached packages

**GStreamer not found during CMake**:
- Check: `echo %GSTREAMER_1_0_ROOT_MSVC_X86_64%`
- Restart terminal after setting environment variable

**DLL not found when running .exe**:
- Use the launcher: `siproxylin.bat` (sets PATH automatically)

### Installer Issues

**Pip install fails**:
- Check internet connection (downloads ~760 MB)
- For slixmpp issues: verify requirements.txt pins slixmpp==1.8.5 on Windows

**Application won't start**:
- Check logs: `%LOCALAPPDATA%\Siproxylin\Logs\`
- Verify launcher uses `python main.py --dot-data-dir`

**Call service won't start**:
- Check: `%LOCALAPPDATA%\Siproxylin\Logs\drunk-call-service.err`
- Verify bundled GStreamer DLLs are present

---

## Status

**Working** ✅:
- Self-contained Inno Setup installer
- Bundled Python + GStreamer + vcpkg dependencies
- Audio calls (WebRTC via GStreamer)
- Windows 10/11 compatibility
- Version display in window title
- Pip progress shown during installation

**Not Implemented**:
- GitHub Actions CI/CD (manual builds only)
- Code signing (SmartScreen warnings may appear)
- MSI installer (Inno Setup only)

---

## Reference Files

- `windows-installer/prepare-windows-installer.bat` - Download dependencies
- `windows-installer/build-installer-bundled.bat` - Compile installer
- `windows-installer/siproxylin-bundled.iss` - Inno Setup script
- `windows-installer/siproxylin-launcher.bat` - Application launcher
- `drunk_call_service/Makefile` - C++ build automation
- `drunk_call_hook/bridge.py` - Runtime PATH setup

---

**Last Updated**: 2026-03-26
**Status**: Production Ready
