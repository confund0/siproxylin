# Windows Build and Distribution Guide

Complete reference for building, distributing, and maintaining Siproxylin on Windows.

## Overview

Siproxylin supports **Windows 10/11 x64** with three distribution methods:

1. **Inno Setup Installer** (recommended for end users) - Downloads dependencies automatically
2. **ZIP Package** - Manual dependency installation required
3. **Bundled GStreamer Libraries** (planned) - Minimal ~28MB GStreamer DLLs included

**Architecture**: Python GUI + C++ call service (drunk_call_service) with GStreamer for audio/video

---

## For End Users

### Installation via Inno Setup Installer (Recommended)

1. **Download** `Siproxylin-Setup-vX.Y.Z.exe` from [GitHub Releases](https://github.com/confund0/siproxylin/releases)

2. **Run installer** - It will automatically:
   - Detect if Python 3.11.9 is installed
   - Detect if GStreamer 1.0 is installed
   - Download and install missing dependencies
   - Install Siproxylin to `C:\Program Files\Siproxylin`
   - Create Start Menu and Desktop shortcuts

3. **Launch** via Start Menu or Desktop shortcut

**Known Issue**: GStreamer installer may show a dialog despite silent install parameters. Click through the installer if prompted.

### Installation from ZIP Package

1. **Install Python 3.11.9**:
   - Download: https://www.python.org/downloads/release/python-3119/
   - **IMPORTANT**: Check "Add Python to PATH" during installation
   - Verify: `python --version` should show `3.11.9`

2. **Install GStreamer**:
   - Download: https://gstreamer.freedesktop.org/download/
   - Install **BOTH** packages:
     - `gstreamer-1.0-msvc-x86_64-*.msi` (Runtime)
     - `gstreamer-1.0-devel-msvc-x86_64-*.msi` (Development)
   - Choose "Complete" installation
   - Default path: `C:\Program Files\gstreamer\1.0\msvc_x86_64`

3. **Install Python dependencies**:
   ```cmd
   pip install slixmpp==1.8.5
   pip install -r requirements.txt
   ```

4. **Extract ZIP** anywhere (e.g., `C:\Users\YourName\Siproxylin`)

5. **Run** `siproxylin.bat`

### System Requirements

- **OS**: Windows 10/11 x64
- **Python**: 3.11.9 (specific version required)
- **GStreamer**: 1.x MSVC build (~3.5GB installed, or ~28MB bundled libs in future)
- **Audio**: Microphone and speakers/headset

---

## For Developers - Building from Source

### Prerequisites

1. **Visual Studio 2019 or 2022** (Community Edition works)
   - Install "Desktop development with C++" workload
   - Ensure C++17 support is included

2. **CMake 3.15+**
   - Download: https://cmake.org/download/
   - Add to PATH during installation

3. **vcpkg** (Microsoft's C++ package manager):
   ```cmd
   git clone https://github.com/Microsoft/vcpkg.git C:\vcpkg
   cd C:\vcpkg
   bootstrap-vcpkg.bat
   vcpkg integrate install
   ```

4. **Install C++ dependencies via vcpkg**:
   ```cmd
   vcpkg install grpc:x64-windows
   vcpkg install protobuf:x64-windows
   vcpkg install spdlog:x64-windows
   vcpkg install glib:x64-windows
   ```

5. **GStreamer Development Files**:
   - Install as described in "For End Users" section
   - Set environment variable:
     ```cmd
     set GSTREAMER_1_0_ROOT_MSVC_X86_64=C:\Program Files\gstreamer\1.0\msvc_x86_64
     ```

6. **Python 3.11.9 + Git** (see "For End Users" section)

### Building drunk_call_service

From Git Bash or Command Prompt:

```bash
cd drunk_call_service

# Release build (optimized)
make winrel VCPKG_ROOT=C:/vcpkg

# Debug build (with symbols)
make windbg VCPKG_ROOT=C:/vcpkg
```

**Output**:
- Binary: `drunk_call_service/bin/drunk-call-service-windows.exe`
- vcpkg DLLs automatically copied to `drunk_call_service/bin/`

**PATH Requirements**: Add to PATH (Git Bash syntax):
```bash
export PATH="/c/Program Files/gstreamer/1.0/msvc_x86_64/bin:C:/vcpkg/installed/x64-windows/bin:$PATH"
```

### Building Windows Distribution Package

1. **Build drunk_call_service** (see above)

2. **Build distribution ZIP**:
   ```cmd
   build-windows.bat
   ```

   This creates:
   - `dist/windows/` - Portable package directory
   - `dist/Siproxylin-Windows-x64.zip` - Distributable archive

3. **Test locally**:
   ```cmd
   cd dist\windows
   siproxylin.bat --help
   ```

### Directory Structure (dist/windows/)

```
dist/windows/
├── siproxylin.bat              # Main launcher (checks Python + GStreamer)
├── test-devices.bat            # Audio device tester
├── README.txt                  # User installation instructions
├── main.py                     # Python entry point
├── version.sh                  # Version info
├── requirements.txt            # Python dependencies
├── drunk_xmpp/                 # XMPP client module
├── drunk_call_hook/            # Call bridge (Python ↔ C++ gRPC)
├── siproxylin/                 # GUI + resources
│   └── resources/
│       └── icons/
│           └── siproxylin.ico  # App icon (256x256)
└── drunk_call_service/
    └── bin/
        ├── drunk-call-service-windows.exe
        └── *.dll               # vcpkg runtime DLLs
```

**Key Files**:
- `build-windows.bat` - Creates dist/windows/ package
- `drunk_call_service/Makefile` - C++ build automation
- `drunk_call_hook/bridge.py` - Sets up Windows PATH (lines 113-145)

---

## For Maintainers - Creating Releases

### Building the Inno Setup Installer

1. **Install Inno Setup 6.x**:
   - Download: https://jrsoftware.org/isdl.php

2. **Build distribution package** (see "Building Windows Distribution Package")

3. **Compile installer**:
   ```cmd
   cd windows-installer
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" siproxylin.iss
   ```

   **Output**: `dist/Siproxylin-Setup-vX.Y.Z.exe`

4. **Test installer**:
   - Run in a clean Windows VM
   - Verify Python/GStreamer auto-installation
   - Test application launch

### Installer Configuration

**File**: `windows-installer/siproxylin.iss`

**Features**:
- Detects existing Python 3.11 and GStreamer installations
- Downloads dependencies on-demand:
  - Python 3.11.9 (~25MB): https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
  - GStreamer 1.28.1 (~150MB): https://gstreamer.freedesktop.org/data/pkg/windows/1.28.1/msvc/gstreamer-1.0-msvc-x86_64-1.28.1.exe
- Silent installation parameters:
  - Python: `/quiet PrependPath=1 InstallAllUsers=1` (verified working)
  - GStreamer: `/VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART` (ISSUE: shows dialog)
- Automatic pip dependency installation
- Upgrade detection (uninstalls old version first)

### Known Issues

**GStreamer Silent Install**:
- **Problem**: GStreamer installer shows dialog despite `/VERYSILENT` parameter
- **Workaround**: User must click through installer
- **Root Cause**: GStreamer uses Inno Setup; parameters may not fully suppress UI
- **Tracking**: See SESSION-ROTATE.md for investigation notes

**Solution in Progress**: Bundle minimal GStreamer DLLs (see "Future Improvements")

### GitHub Actions Integration (Future)

Planned workflow:
```yaml
name: Build Windows Installer
on: [push, release]
jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install vcpkg dependencies
      - name: Build drunk_call_service
      - name: Run build-windows.bat
      - name: Build Inno Setup installer
      - name: Upload artifacts
```

---

## Technical Details

### PATH Management

**File**: `drunk_call_hook/bridge.py` (lines 113-145)

When starting the call service on Windows, `bridge.py` automatically adds to PATH:
1. **GStreamer binaries**: `C:\Program Files\gstreamer\1.0\msvc_x86_64\bin`
2. **vcpkg DLLs**: `{project_root}/drunk_call_service/bin/`

This ensures DLLs are found at runtime without manual PATH configuration.

### vcpkg Dependencies

Bundled in `drunk_call_service/bin/` (copied by Makefile):
- `grpc++.dll` - gRPC C++ runtime
- `protobuf.dll` - Protocol Buffers
- `spdlog.dll` - Logging
- `glib-2.0-0.dll` - GLib (required by GStreamer)
- Plus transitive dependencies (~50MB total)

### GStreamer Dependency

**Current Approach**: Requires full GStreamer installation (~3.5GB)

**Required Files** (from `/tmp/drunk-ldd-full.txt`):
- Core GStreamer DLLs (~28MB total)
- Plugins: `autodetect`, `coreelements`, `audiotestsrc`, `wasapi`, etc.
- Dependencies: GLib, libintl, libffi, pcre, etc.

**Future Approach**: Bundle minimal GStreamer subset (~28MB) in GitHub Release

### High DPI Support

**File**: `siproxylin/resources/icons/siproxylin.ico`
- 256x256 icon for high-DPI displays
- Set via AppUserModelID (see `siproxylin/gui/main_window.py`)

### Emoji Font Fix

**Issue**: Segoe UI doesn't render color emojis on Windows
**Solution**: Changed to Noto Color Emoji font (see `siproxylin/gui/styles.py`)

---

## Current Status

### Working ✅
- ZIP distribution with manual dependency setup
- Inno Setup installer framework
- Automatic Python installation
- Python dependency installation via pip
- C++ build system (vcpkg + CMake + MSVC)
- PATH auto-configuration in bridge.py
- Windows UI fixes (emoji fonts, app icon, high-DPI)
- Verified installer parameters (Python ✅, GStreamer ⚠️)

### Session History
**2026-03-13**: UI fixes (fonts, icon, scaling), installer framework created
**2026-03-17**: Installer parameters verified, minimal GStreamer DLLs identified (~28MB)
**2026-03-24**: Current session - implementing GStreamer bundling

### Known Issues 🔴
1. **GStreamer silent install doesn't work** (shows dialog despite `/VERYSILENT`)
   - Root cause: GStreamer's Inno Setup has silent mode disabled (likely for license/patent acknowledgment)
   - File: `gstreamer-1.0-msvc-x86_64-1.28.1.exe` (Inno Setup, verified with `strings`)
   - Tried: `/VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART` - still shows dialog
   - **Solution**: Bundle minimal DLLs instead (see "Planned Improvements" below)

2. **Large GStreamer footprint**
   - Full installation: ~3.5GB (includes DVD, FFmpeg, TensorFlow, AWS, GTK4, etc.)
   - Actually needed for Siproxylin:
     - Current (audio-only): **15.9 MB** (confirmed via recursive ldd analysis)
     - Future (audio+video+screen): **23.5 MB** (estimated)

### Testing Status 🔄
**Linux**: ✅ All functionality working, no regressions
**Windows**: ⚠️ Not yet tested end-to-end
- Application launch: Not tested
- UI rendering (icon, fonts): Not tested
- Installer (Python auto-install): Not tested
- Installer (GStreamer bundling): Not implemented yet
- Call functionality: Not tested

### Planned Improvements
1. **Bundle Python + GStreamer** (HIGH PRIORITY - complete standalone installer):
   - **Python embeddable**: 25 MB (always bundled)
   - **GStreamer DLLs**: ~30 MB (generous bundle with extra codecs)
     - Current minimal (audio-only): 15.9 MB
     - With extras (H.264, H.265, additional formats): ~30 MB
     - Analysis files: `./tmp/sip_exe_ldd.txt`, `core-dlls-ldd.txt`, `plugins-ldd.txt`
     - Detailed minimal list: `./tmp/minimal_webrtc_dlls.txt`
   - **vcpkg DLLs**: 50 MB (already bundled from build)
   - **App code**: 7 MB
   - **Total installer download: ~112 MB**

   **Installation flow:**
   - Extract bundled Python to `{app}\python\`
   - Extract bundled GStreamer to `{app}\drunk_call_service\bin\gstreamer\`
   - Detect Python packages (PySide6, slixmpp, etc.)
   - **User choice**: Use bundled Python OR browse to existing Python installation
   - Run `pip install -r requirements.txt` with progress bar (~760 MB download)
   - Save Python path to registry/config for app startup
   - GStreamer always uses bundled version (no user choice)

   **Expected result**:
   - Installer: 112 MB download
   - First run: ~760 MB pip packages (internet required)
   - Installation time: ~2-5 minutes (depending on connection)
   - No external dependencies required

2. **Code Signing**:
   - Sign `.exe` files to avoid SmartScreen warnings
   - Requires code signing certificate

4. **Automated Testing**:
   - Windows VM testing via GitHub Actions
   - Audio device mocking for CI

---

## GitHub Actions CI/CD

**Goal**: Automate Windows builds with clean, reproducible environment (no Git Bash/mingw PATH pollution)

### Strategy

**Problem**: Local Git Bash builds suffer from PATH pollution - mingw's zlib/glib get picked up instead of vcpkg/GStreamer libraries, causing ABI conflicts and linker issues.

**Solution**: Pre-bundle runtime dependencies, use GitHub's clean Windows runners for builds.

### Dependency Architecture

**Two separate runtime bundles:**

1. **vcpkg runtime** (~10 MB compressed):
   - gRPC stack: `abseil_dll.dll`, `cares.dll`, `re2.dll`
   - Protobuf: `libprotobuf.dll`, `libprotobuf-lite.dll`, `upb*.dll`
   - Logging: `spdlog.dll`
   - Compression: `zlib1.dll` (MSVC-built, for gRPC)

2. **GStreamer runtime** (~30 MB compressed):
   - 28 core + dependency DLLs from `bin/`
   - 18 plugins from `lib/gstreamer-1.0/`
   - **Note**: GStreamer has its own zlib (`z-1.dll`) - no conflict with vcpkg's `zlib1.dll`

**Binary compatibility:**
- .exe built with MSVC 19.50 (VS 2022 17.10+)
- GitHub Actions uses MSVC 19.29-19.40 (VS 2022, compatible)
- All DLLs are MSVC-built (no mingw mixing)

### Creating Dependency Bundles (One-time Setup)

**On Windows machine with successful build:**

```bash
# 1. Create vcpkg runtime bundle
cd ~/Desktop/vcpkg/installed/x64-windows/bin
zip ~/Desktop/vcpkg-runtime-x64-windows.zip \
  abseil_dll.dll cares.dll re2.dll \
  libprotobuf.dll libprotobuf-lite.dll \
  spdlog.dll zlib1.dll upb*.dll

# 2. Create GStreamer bundle (from tested build)
cd ~/Desktop/siproxylin/drunk_call_service
zip -r ~/Desktop/gstreamer-windows-libs-1.28.1.zip lib/gstreamer/

# 3. Upload to GitHub Releases
gh release create deps-v1 \
  --title "Windows Build Dependencies v1" \
  --notes "Pre-built runtime dependencies for Windows CI builds. MSVC-compatible, no mingw." \
  ~/Desktop/vcpkg-runtime-x64-windows.zip \
  ~/Desktop/gstreamer-windows-libs-1.28.1.zip
```

### GitHub Actions Workflow

**File**: `.github/workflows/build-windows.yml`

```yaml
name: Build Windows

on:
  push:
    branches: [main, win]
  workflow_dispatch:

jobs:
  build:
    runs-on: windows-2022  # MSVC 19.29-19.40

    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Setup MSVC
        uses: ilammy/msvc-dev-cmd@v1
        with:
          arch: x64

      - name: Download runtime dependencies
        shell: bash
        run: |
          # vcpkg runtime DLLs
          curl -L -o vcpkg-deps.zip \
            https://github.com/${{ github.repository }}/releases/download/deps-v1/vcpkg-runtime-x64-windows.zip
          unzip vcpkg-deps.zip -d drunk_call_service/bin/

          # GStreamer runtime DLLs + plugins
          curl -L -o gstreamer-deps.zip \
            https://github.com/${{ github.repository }}/releases/download/deps-v1/gstreamer-windows-libs-1.28.1.zip
          unzip gstreamer-deps.zip -d drunk_call_service/lib/

      - name: Setup vcpkg (build-time only)
        run: |
          git clone https://github.com/Microsoft/vcpkg.git C:\vcpkg
          C:\vcpkg\bootstrap-vcpkg.bat
          C:\vcpkg\vcpkg install grpc:x64-windows protobuf:x64-windows spdlog:x64-windows

      - name: Build drunk-call-service
        shell: bash
        run: |
          cd drunk_call_service
          mkdir build && cd build
          cmake -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake -A x64 ..
          cmake --build . --config Release
          cmake --install . --config Release

      - name: Upload build artifact
        uses: actions/upload-artifact@v3
        with:
          name: drunk-call-service-windows
          path: drunk_call_service/bin/
```

### Benefits

✅ **Clean environment**: No mingw PATH pollution
✅ **Reproducible**: Same dependencies every build
✅ **Automated**: Push code → get build
✅ **Fast**: Pre-bundled runtimes, only build C++ code
✅ **Version locked**: MSVC compatibility guaranteed

### Troubleshooting

**Issue**: Local Git Bash build links to mingw's zlib
**Cause**: `/mingw64/lib` in library search path before vcpkg
**Solution**: Use CI builds, or temporarily rename `/mingw64/lib/libz.*` during local build

**Issue**: ABI mismatch between DLLs
**Cause**: Mixing MSVC and mingw builds
**Prevention**: All bundled DLLs are MSVC-built from vcpkg or GStreamer MSVC distribution

---

## Future Improvements

### Bundled GStreamer Libraries (In Progress - 2026-03-24)

**Goal**: Eliminate 3.5GB GStreamer installation by bundling minimal DLLs

**Analysis Complete** (2026-03-24):
- Recursive ldd analysis on drunk-call-service-windows.exe + all dependencies
- Analysis files: `./tmp/sip_exe_ldd.txt`, `./tmp/core-dlls-ldd.txt`, `./tmp/plugins-ldd.txt`
- **Minimal (audio-only): 15.9 MB confirmed**
  - Core: 5.8 MB (gstreamer, gstbase, gstwebrtc, gstrtp, gstaudio, etc.)
  - Plugins: 3.7 MB (webrtcbin, dtls, srtp, nice, opus, wasapi, etc.)
  - External: 6.4 MB (libcrypto, libssl, libnice)
- **Generous bundle (audio+video+screen+extras): ~30 MB**
  - Minimal: 15.9 MB
  - Additional codecs: 7.6 MB (VP8/VP9, H.264, H.265)
  - Extra formats/filters: 6.5 MB (more RTP payloaders, audio filters, compatibility)
- **Rationale**: Don't be cheap - some users may want H.264, others H.265, include both
- File lists: `./tmp/minimal_webrtc_dlls.txt` (minimal), `./tmp/gstreamer_dll_analysis.md` (comprehensive)

**Implementation Plan**:
1. **On Windows machine** (where GStreamer is installed):
   ```powershell
   # Navigate to GStreamer installation
   cd "C:\Program Files\gstreamer\1.0\msvc_x86_64"

   # Create directory structure
   New-Item -ItemType Directory -Path gstreamer-bundle\bin
   New-Item -ItemType Directory -Path gstreamer-bundle\lib\gstreamer-1.0

   # Copy core DLLs from bin/ (reference /tmp/drunk-ldd-full.txt)
   # Example: gstreamer-1.0-0.dll, glib-2.0-0.dll, gobject-2.0-0.dll, etc.

   # Copy plugins from lib/gstreamer-1.0/
   # WebRTC: gstwebrtc.dll, gstwebrtcdsp.dll
   # RTP: gstrtp.dll, gstrtpmanager.dll
   # Security: gstdtls.dll, gstsrtp.dll, gstsctp.dll
   # Codecs: gstopus.dll, gstopusparse.dll
   # Windows Audio: gstwasapi.dll, gstwasapi2.dll

   # Package as ZIP
   Compress-Archive -Path gstreamer-bundle\* -DestinationPath gstreamer-windows-libs-1.28.1.zip
   ```

2. **Create GitHub Release**:
   ```bash
   gh release create gstreamer-libs-v1.28.1 \
     --title "GStreamer Windows Dependencies v1.28.1" \
     --notes "Minimal GStreamer runtime libraries for Windows builds (~28MB)" \
     gstreamer-windows-libs-1.28.1.zip
   ```

3. **Update `build-windows.bat`**:
   ```batch
   REM Download GStreamer libs if not present
   if not exist "drunk_call_service\bin\gstreamer-1.0-0.dll" (
       echo Downloading GStreamer dependencies...
       powershell -Command "Invoke-WebRequest -Uri 'https://github.com/confund0/siproxylin/releases/download/gstreamer-libs-v1.28.1/gstreamer-windows-libs-1.28.1.zip' -OutFile 'gst.zip'"
       powershell -Command "Expand-Archive -Path 'gst.zip' -DestinationPath 'drunk_call_service\bin\' -Force"
       del gst.zip
   )
   ```

4. **Update `.gitignore`**:
   ```gitignore
   # GStreamer runtime libs (downloaded from releases)
   drunk_call_service/bin/gst*.dll
   drunk_call_service/bin/orc-*.dll
   drunk_call_service/bin/z-*.dll
   drunk_call_service/bin/gstreamer-1.0/
   ```

5. **Update `bridge.py`** (set GST_PLUGIN_PATH):
   ```python
   if platform.system() == "Windows":
       plugin_path = os.path.abspath("drunk_call_service/bin/gstreamer-1.0")
       env["GST_PLUGIN_PATH"] = plugin_path
   ```

6. **Update installer** (`windows-installer/siproxylin.iss`):
   - Remove GStreamer component section
   - Remove GStreamer download/install code
   - Update README.txt to not mention GStreamer installation

**Installer Size Breakdown**:
- Python embeddable: 25 MB (always bundled)
- GStreamer DLLs: 30 MB (generous bundle - always bundled)
- vcpkg DLLs: 50 MB (already bundled from build)
- App code + binary: 7 MB
- **Total installer download: ~112 MB**
- **First run pip install**: ~760 MB (PySide6 + dependencies, internet required)

**Python Flexibility**:
- Default: Use bundled Python 3.11.9 embeddable in `{app}\python\`
- Advanced: User can browse to existing Python 3.11.x installation
- Installer runs `pip install -r requirements.txt` against chosen Python
- Python path saved to registry/config for app startup

**GStreamer**:
- Always uses bundled version in `{app}\drunk_call_service\bin\gstreamer\`
- No user choice (ensures compatibility)
- Includes extra codecs (H.264, H.265) for maximum compatibility

**Benefits**:
- Installer: 112 MB download (reasonable for multimedia app)
- Installation time: 2-5 minutes (depending on internet speed for pip packages)
- No silent install issues (everything bundled or downloaded via pip)
- No 3.5GB GStreamer requirement
- User can leverage existing Python installation (saves ~25 MB disk space)
- pip packages downloaded fresh (always latest compatible versions)
- Cleaner uninstall

### Portable Mode

Add `siproxylin.ini` config to run without installation:
```ini
[Paths]
Config=%AppData%\Siproxylin
Logs=%AppData%\Siproxylin\Logs
```

### MSI Installer

Alternative to Inno Setup using WiX Toolset:
- Native Windows Installer format
- Better enterprise deployment support
- Group Policy installation

---

## Troubleshooting

### "Python 3.11 not found"
- Reinstall Python 3.11.9 with "Add Python to PATH" checked
- Verify: `python --version` in Command Prompt

### "GStreamer not found"
- Check installation path: `C:\Program Files\gstreamer\1.0\msvc_x86_64\bin`
- Reinstall with "Complete" installation option

### "DLL not found" errors
- Ensure vcpkg DLLs are in `drunk_call_service/bin/`
- Re-run `make winrel` to copy DLLs

### "Call service won't start"
- Check logs: `%AppData%\.siproxylin\logs\call_service_stderr.log`
- Verify GStreamer PATH in `call_service.log`
- Test manually: `drunk_call_service\bin\drunk-call-service-windows.exe --log-level DEBUG`

### Build failures
- Verify vcpkg integration: `vcpkg integrate install`
- Check CMake output in `drunk_call_service/build/`
- Ensure GSTREAMER_1_0_ROOT_MSVC_X86_64 environment variable is set

---

## Reference Files

- `/home/m/claude/siproxylin/build-windows.bat` - Distribution package builder
- `/home/m/claude/siproxylin/windows-installer/siproxylin.iss` - Inno Setup script
- `/home/m/claude/siproxylin/drunk_call_service/Makefile` - C++ build automation
- `/home/m/claude/siproxylin/drunk_call_hook/bridge.py` - PATH setup (lines 113-145)
- `/home/m/claude/siproxylin/docs/SESSION-ROTATE.md` - Windows work log

---

## Contributing

When working on Windows support:

1. **Test on clean Windows VM** - Don't rely on your development environment
2. **Document PATH requirements** - GStreamer/vcpkg paths are critical
3. **Update this doc** - Keep WINDOWS.md in sync with changes
4. **Log installer changes** - Track Inno Setup modifications in SESSION-ROTATE.md

**Questions?** Open an issue at https://github.com/confund0/siproxylin/issues
