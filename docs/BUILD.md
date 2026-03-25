# Build & Packaging Documentation

> **Last Updated:** 2026-03-13
> **Platforms:** Linux (primary), Windows 10/11, macOS (experimental)

---

## Development Build

**Prerequisites:**
- Python 3.11+
- CMake 3.15+
- C++ compiler (GCC 12+ or Clang)
- GStreamer 1.0 + WebRTC plugin
- Qt6 libraries

**C++ Call Service Dependencies:**
```bash
sudo apt install cmake build-essential \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  libgstreamer-plugins-bad1.0-dev gstreamer1.0-nice \
  libnice-dev libgrpc++-dev libspdlog-dev libunwind-dev \
  libsrtp2-dev libasound2-dev
```

**Steps:**
```bash
cd drunk_call_service && make clean && make && make install && cd ..
pip install -r requirements.txt
python main.py
```

---

## Call Service Architecture

**Technology:** C++ with GStreamer WebRTCBin

**Build system:** CMake + Makefile wrapper

**Key components:**
- `drunk_call_service/` - C++ WebRTC service using GStreamer
- `drunk_call_hook/` - Python gRPC bridge to call service
- `drunk_xmpp/calls/` - XMPP Jingle signaling (XEP-0353)

**Build targets:**
```bash
make           # Release build (optimized, 1.2MB)
make debug     # Debug build (with sanitizers, 59MB)
make test      # Run unit tests
make clean     # Remove build artifacts
```

**Binary location:** `drunk_call_service/bin/drunk-call-service-linux`

---

## Version Management

**Single source of truth:** `version.sh` at repo root

**Setup (one-time):**
```bash
./.githooks/install.sh
```

**How versioning works:**

1. **Edit `version.sh`** (accepts both formats):
   ```bash
   SIPROXYLIN_VERSION="v0.0.4"  # or "0.0.4", both work
   SIPROXYLIN_CODENAME="FreshVibes"
   ```

2. **Commit** (pre-commit hook validates):
   ```bash
   git add version.sh
   git commit -m "Bump to v0.0.4"
   ```
   - ✓ Validates version increased (v0.0.4 > v0.0.3)
   - ✓ Validates codename changed
   - ✗ Blocks commit if validation fails

3. **Tag**:
   ```bash
   git tag -a v0.0.4 -m "FreshVibes"
   ```

4. **Push** (pre-push hook validates):
   ```bash
   git push origin v0.0.4
   ```
   - ✓ Validates tag matches version.sh
   - ✗ Blocks push if mismatch

5. **CI builds automatically**:
   - Reads version.sh
   - Builds: `Siproxylin-v0.0.4-x86_64.AppImage`
   - Creates GitHub release
   - Help → About shows: "v0.0.4 - FreshVibes"

**Fallback behavior:**
- **Dev mode**: version.sh missing → shows "dev - 🍺"
- **Build mode**: version.sh missing → ERROR and exit

**Version normalization:**
- Accepts both "v0.0.4" and "0.0.4" in version.sh
- Always normalizes to "v0.0.4" format
- Strips 'v' for semver comparisons, adds back for display/filenames

**Implementation:**
- `version.sh` - Root file with `SIPROXYLIN_VERSION` and `SIPROXYLIN_CODENAME`
- `siproxylin/version.py` - Reads version.sh at runtime (dev) or in AppDir (AppImage)
- `.githooks/pre-commit` - Validates version increased, codename changed
- `.githooks/pre-push` - Validates tag matches version.sh
- `.package-builder.sh` - Sources version.sh (mandatory for builds)
- `build-appimage.sh` - Copies version.sh into AppDir
- `.github/workflows/release.yml` - Reads version.sh instead of parsing tag

---

## Linux AppImage Build

**Local build:**
```bash
./build-appimage.sh
```

**Output:** `Siproxylin-{VERSION}-x86_64.AppImage` (280MB)

### Prerequisites

**Required tools:**
- `patchelf` - **CRITICAL** for portable AppImage (patches ELF binaries)
- `python3` and `pip3` - Python runtime and package installer
- `wget` - Download tool
- `file` - File type detection
- `appimage-builder` - Bundle system dependencies (install via pip or use AppImage version)
- `appimagetool` - Final packaging tool (auto-downloaded by build script)

**Optional:**
- `imagemagick` (convert) - Icon conversion from SVG to PNG

**Install on Debian/Ubuntu:**
```bash
sudo apt install patchelf python3 python3-pip wget file imagemagick
pip install appimage-builder
```

**Before building:** Unset `PYTHONHOME` and `PYTHONPATH` to prevent host Python interference

**Note:** The build script will check for all required tools and fail early with helpful error messages if anything is missing.

### Build Modes

**Incremental (default):**
- Reuses existing `AppDir/` and `.package-builder-apt/` cache
- Fast iteration on code changes
- ~60% faster than clean build

**Clean:**
```bash
rm -rf AppDir .package-builder-apt
./build-appimage.sh
```
- Use when: Adding system packages, modifying `appimage.yml`

### Configuration

**Files:**
- `.package-builder.sh` - Shared packaging functions, language config
- `build-appimage.sh` - Linux build orchestration
- `appimage.yml` - System dependencies (Qt6, GStreamer, hunspell dictionaries)

**Languages:**
Edit `PKG_LANGUAGES` in `.package-builder.sh` (controls both UI locales and spell check dictionaries):
```bash
PKG_LANGUAGES=(
    "en:en_US" "de:de_DE" "ru:ru_RU" "lt:lt_LT"
    "es:es_ES" "ro:ro_RO" "ar:ar"
)
```

### GitHub Actions Release

**Trigger:** Push version tag
```bash
git tag v0.0.4
git push origin v0.0.4
```

**What happens:**
1. Workflow reads version from `version.sh`
2. Builds AppImage with cached dependencies (`.package-builder-apt/`, `AppDir/`, `appimagetool`)
3. Creates GitHub release with `Siproxylin-v0.0.4-x86_64.AppImage`

**Cache strategy:**
- Conservative: APT debs (~300MB), appimagetool, C++ binary
- Invalidates on: appimage.yml changes, C++ source/CMakeLists changes
- Speeds up builds significantly
- To force cache refresh: Add comment to appimage.yml

**CI-specific settings:**
- `APPIMAGE_EXTRACT_AND_RUN=1` for appimagetool (no FUSE in containers)
- `permissions: contents: write` for creating releases
- `shell: bash` to avoid sh/bash incompatibilities

---

## Troubleshooting

**"No module named 'encodings'"**
- Cause: `PYTHONHOME`/`PYTHONPATH` set from previous AppImage run
- Fix: `unset PYTHONHOME PYTHONPATH` before building

**appimage-builder not found**
- Fix: `pip install appimage-builder` or set `APPIMAGE_BUILDER=/path/to/appimage-builder.AppImage`

**AppImage too large**
- Check `PKG_LANGUAGES` in `.package-builder.sh` (each language ~1-2MB)
- Verify PySide6 cleanup in build log (step 9/10)

**Want clean rebuild**
- `rm -rf AppDir .package-builder-apt && ./build-appimage.sh`

---

## Path Modes

**Three data storage modes:**

| Mode | Flag | Directory |
|------|------|-----------|
| dev | (none) | `./app_dev_paths/` |
| xdg | `--xdg` | `~/.config/`, `~/.local/share/`, `~/.cache/` |
| dot | `--dot-data-dir` | `~/.siproxylin/` |

**AppImage default:** `--dot-data-dir` (single directory for easy cleanup/encryption)

---

---

## Windows Build

**See also:** `docs/WINDOWS.md` for detailed setup, `docs/C++_PLATFORM-COMPATIBILITY.md` for technical details

### Quick Start

```bash
# In git-bash on Windows
cd ~/Desktop/siproxylin

# Build C++ service (produces binary + DLLs in drunk_call_service/bin/)
cd drunk_call_service
make winrel

# Test the binary locally (optional)
./bin/drunk-call-service-windows.exe --help

# Package distribution
cd ..
./build-windows.bat
```

**Output:**
- Build: `drunk_call_service/bin/drunk-call-service-windows.exe` + DLLs (testable immediately)
- Package: `dist/Siproxylin-Windows-x64.zip` (~15-50MB)

### System Requirements

- **OS:** Windows 10/11 x64
- **Disk Space:** ~50GB (includes Visual Studio + vcpkg cache)
- **RAM:** 16GB recommended (8GB minimum)
- **Time:** First build: 2-3 hours (vcpkg compilation), subsequent builds: 5-10 minutes

### Prerequisites (One-Time Setup)

#### 1. Visual Studio 2019 or 2022

Download: [Visual Studio](https://visualstudio.microsoft.com/downloads/)

**Required workload:**
- Desktop development with C++
- C++17 support
- CMake tools

**Size:** ~20GB

#### 2. Git for Windows

Download: [Git for Windows](https://git-scm.com/download/win)

Provides git-bash terminal for build scripts.

#### 3. Python 3.11.9

Download: [Python 3.11.9](https://www.python.org/downloads/release/python-3119/)

**IMPORTANT:**
- Use exactly 3.11.9 (latest 3.11 for Windows)
- Check "Add Python to PATH" during installation

#### 4. vcpkg (C++ Package Manager)

```bash
# In git-bash
cd ~/Desktop
git clone https://github.com/Microsoft/vcpkg.git
cd vcpkg
./bootstrap-vcpkg.bat
```

#### 5. GStreamer for Windows

Download: [GStreamer](https://gstreamer.freedesktop.org/download/)

**Install BOTH:**
1. `gstreamer-1.0-msvc-x86_64-*.msi` (Runtime)
2. `gstreamer-1.0-devel-msvc-x86_64-*.msi` (Development)

**Options:** Choose "Complete" installation

**Set environment variable:**
```cmd
setx GSTREAMER_1_0_ROOT_MSVC_X86_64 "C:\Program Files\gstreamer\1.0\msvc_x86_64" /M
```

Restart terminal after setting.

### Install C++ Dependencies

**First time only (takes 45-90 minutes):**

```bash
cd ~/Desktop/vcpkg

# Install packages (will compile from source)
./vcpkg install grpc:x64-windows
./vcpkg install spdlog[core]:x64-windows    # Note: [core] not [fmt]!
./vcpkg install glib:x64-windows
```

**Why so slow?** vcpkg compiles grpc, protobuf, abseil-cpp from source. This is a one-time cost - packages are cached.

**Disk usage:** ~15GB during build, ~5-8GB after cleanup

**Speed up future builds:** Export compiled packages:
```bash
vcpkg export grpc spdlog glib --zip --output=siproxylin-deps-windows
# Upload siproxylin-deps-windows.zip to GitHub releases
```

### Build C++ Service

```bash
cd ~/Desktop/siproxylin/drunk_call_service

# Using Makefile (recommended - handles DLL copying)
make winrel

# Or manually with CMake
mkdir build && cd build
cmake .. -DCMAKE_TOOLCHAIN_FILE=~/Desktop/vcpkg/scripts/buildsystems/vcpkg.cmake -A x64
cmake --build . --config Release
cmake --install . --config Release
# Then manually copy vcpkg DLLs:
cp ~/Desktop/vcpkg/installed/x64-windows/bin/*.dll ../bin/
```

**Output:** `bin/drunk-call-service-windows.exe` + all runtime DLLs

**Build time:** 5-10 minutes (after vcpkg setup)

**What happens:**
1. CMake builds the .exe in `build/Release/`
2. CMake install copies .exe to `bin/`
3. Makefile copies all vcpkg DLLs to `bin/`
4. Result: Complete testable runtime directory at `bin/`

### Package Distribution

```bash
cd ~/Desktop/siproxylin
./build-windows.bat
```

**Creates:**
```
dist/
└── Siproxylin-Windows-x64.zip
    ├── main.py
    ├── drunk_xmpp/
    ├── drunk_call_hook/
    ├── siproxylin/
    ├── drunk_call_service/
    │   └── bin/
    │       ├── drunk-call-service-windows.exe
    │       └── *.dll (vcpkg runtime libraries)
    ├── siproxylin.bat (launcher with dependency checks)
    ├── test-devices.bat
    └── README.txt
```

**Size:** 15-50MB (depends on number of Python modules)

**Note:** The script simply copies `drunk_call_service/bin/*` to the package - Makefile handles producing the complete runtime directory.

**Does NOT include:** Python runtime, GStreamer (users install separately)

### Windows Installer (Inno Setup)

**Status**: Framework complete, GStreamer bundling in progress

**Prerequisites:**
- Inno Setup 6.x installed
- Built distribution package (via `build-windows.bat`)

**Build installer:**
```bash
cd windows-installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" siproxylin.iss
```

**Output:** `dist/Siproxylin-Setup-vX.Y.Z.exe`

**Features:**
- Bundles Python 3.11.9 embeddable (25 MB) - with option to use system Python
- Bundles generous GStreamer DLLs (30 MB with extra codecs)
- Bundles vcpkg runtime libraries (50 MB - from build)
- Runs `pip install -r requirements.txt` during installation (~760 MB download)
- Installs to `C:\Program Files\Siproxylin` (admin) or `%LOCALAPPDATA%\Programs\Siproxylin` (user)
- Creates Start Menu + Desktop shortcuts
- Proper uninstaller with config cleanup
- Upgrade detection (removes old version first)
- **Total installer download: ~112 MB**
- **First run requires internet**: ~760 MB pip packages (or use pre-installed packages)

**See:** `docs/WINDOWS.md` for complete installer documentation and bundling details

### Distribution to End Users

**Option 1: Installer (recommended):**
- Download `Siproxylin-Setup-vX.Y.Z.exe` from GitHub Releases
- Run installer (handles all dependencies automatically)
- Launch from Start Menu

**Option 2: ZIP Package:**
1. Install Python 3.11.9 (add to PATH)
2. Install GStreamer (both runtime + devel packages)
3. Install Python dependencies: `pip install slixmpp==1.8.5 -r requirements.txt`
4. Extract ZIP and run `siproxylin.bat`

**The launcher script automatically:**
- Checks for Python 3.11
- Checks for GStreamer installation
- Sets up PATH with GStreamer and vcpkg DLLs
- Launches the application

### Platform-Specific Notes

**C++ Standard:**
- Windows uses C++23 (required by GStreamer headers)
- Linux/macOS use C++20

**Compiler Flags:**
- MSVC: `/W4 /O2 /std:c++latest`
- GCC/Clang: `-Wall -Wextra -O3 -std=c++20`

**Audio Backends:**
- Windows: WASAPI (`wasapisrc`/`wasapisink`)
- Linux: PulseAudio (`pulsesrc`/`pulsesink`)
- macOS: CoreAudio (`osxaudiosrc`/`osxaudiosink`)

See `docs/C++_PLATFORM-COMPATIBILITY.md` for implementation details.

### Troubleshooting

**vcpkg compilation extremely slow:**
- Normal on first run (45-90 min for grpc)
- Subsequent builds use cached packages (fast)
- Check disk space (need 20GB+ free)

**"GStreamer not found" during CMake:**
- Check: `echo %GSTREAMER_1_0_ROOT_MSVC_X86_64%`
- Should show: `C:\Program Files\gstreamer\1.0\msvc_x86_64`
- Restart terminal after setting environment variable

**"DLL not found" when running .exe:**
- Use the launcher: `siproxylin.bat` (sets PATH automatically)
- Or manually add to PATH:
  - `C:\Program Files\gstreamer\1.0\msvc_x86_64\bin`
  - `%USERPROFILE%\Desktop\vcpkg\installed\x64-windows\bin`

**Build fails with C++23 errors:**
- Already fixed in CMakeLists.txt (commit a5e59b6)
- Delete `build/` directory and reconfigure

**Out of RAM during compilation:**
- Increase VM RAM to 16GB
- Or limit parallel compilation: Add `/MP4` to CMake flags

**mingw PATH pollution (Git Bash):**
- Git Bash adds `/mingw64/lib` to library search path
- Can cause CMake to find mingw libraries before vcpkg/GStreamer
- Symptom: .exe links to mingw's zlib instead of vcpkg's
- Fix: Use GitHub Actions (clean environment) or temporarily rename mingw libs during build

### Windows CI/CD (GitHub Actions)

**Status:** Designed, ready for implementation

**Why CI/CD?**
- Clean build environment (no mingw PATH pollution)
- Consistent MSVC toolchain (Windows Server 2022, MSVC 19.29-19.40)
- Automated weekly releases for bugfixes and features
- Pre-bundled dependencies reduce build time

**Strategy:**

Two dependency bundles uploaded as GitHub Releases:

1. **vcpkg-runtime-x64-windows.zip** (~10 MB)
   - grpc, protobuf, abseil, cares, re2 runtime DLLs
   - spdlog with bundled fmt
   - Compatible with MSVC 19.29+ (VS 2019 16.11+)

2. **gstreamer-runtime-x64-windows.zip** (~30 MB)
   - GStreamer core libraries (gstreamer-1.0, gstwebrtc, gstsdp)
   - GLib ecosystem (glib-2.0, gobject-2.0, gio-2.0)
   - Supporting libraries (OpenSSL, libnice, pcre2, zlib, etc.)
   - WebRTC plugins (minimal set for audio/video calls)

**Workflow:**
```yaml
name: Build Windows Binary
on:
  push:
    branches: [main, win]
  workflow_dispatch:

jobs:
  build:
    runs-on: windows-2022  # MSVC 19.29-19.40, compatible with local builds (19.50)

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup MSVC
        uses: microsoft/setup-msbuild@v1.1

      - name: Download runtime dependencies
        shell: bash
        run: |
          # vcpkg runtime (grpc, protobuf, spdlog)
          curl -L -o vcpkg-deps.zip \
            https://github.com/${{ github.repository }}/releases/download/deps-v1/vcpkg-runtime-x64-windows.zip
          mkdir -p drunk_call_service/bin
          unzip vcpkg-deps.zip -d drunk_call_service/bin/

          # GStreamer runtime (WebRTC + dependencies)
          curl -L -o gst-deps.zip \
            https://github.com/${{ github.repository }}/releases/download/deps-v1/gstreamer-runtime-x64-windows.zip
          mkdir -p drunk_call_service/lib/gstreamer
          unzip gst-deps.zip -d drunk_call_service/lib/gstreamer/

      - name: Build C++ service
        shell: cmd
        run: |
          cd drunk_call_service
          mkdir build && cd build
          cmake .. -A x64 -DCMAKE_BUILD_TYPE=Release
          cmake --build . --config Release
          cmake --install . --config Release

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: drunk-call-service-windows
          path: drunk_call_service/bin/
```

**Creating dependency bundles (one-time setup):**

On Windows build machine with working vcpkg and GStreamer:

```bash
# 1. vcpkg runtime bundle
cd drunk_call_service/bin
zip vcpkg-runtime-x64-windows.zip \
  abseil_dll.dll cares.dll re2.dll \
  libprotobuf.dll libprotobuf-lite.dll \
  spdlog.dll zlib1.dll upb*.dll

# 2. GStreamer runtime bundle (from analysis in /tmp/gst-bin-dlls.txt)
cd "C:\Program Files\gstreamer\1.0\msvc_x86_64"
zip gstreamer-runtime-x64-windows.zip \
  bin/ffi-7.dll bin/gio-2.0-0.dll bin/glib-2.0-0.dll \
  bin/gmodule-2.0-0.dll bin/gobject-2.0-0.dll \
  bin/gstreamer-1.0-0.dll bin/gstsdp-1.0-0.dll \
  bin/gstwebrtc-1.0-0.dll bin/intl-8.dll \
  bin/libcrypto-3-x64.dll bin/nice-10.dll \
  bin/pcre2-8-0.dll bin/z-1.dll \
  lib/gstreamer-1.0/*.dll  # All plugins
```

**Upload to GitHub Releases:**
```bash
gh release create deps-v1 \
  --title "Windows Runtime Dependencies v1" \
  --notes "Pre-built runtime libraries for Windows CI/CD builds" \
  vcpkg-runtime-x64-windows.zip \
  gstreamer-runtime-x64-windows.zip
```

**IMPORTANT: Binary compatibility:**
- All DLLs must be MSVC-built (NOT mingw)
- GLib version MUST match between vcpkg and GStreamer
- Our strategy: Use GStreamer's GLib, vcpkg only provides grpc/protobuf/spdlog
- CMakeLists.txt is configured to link GStreamer's GLib (lines 59-68 in /tmp/sipbuild/)
- CMake explicitly disables vcpkg's zlib (line 51: `CMAKE_DISABLE_FIND_PACKAGE_ZLIB`)

**Troubleshooting CI builds:**

*Build fails with "cannot find gstreamer-1.0.lib":*
- Ensure gstreamer-runtime bundle includes import libraries (.lib files)
- Or: Install GStreamer SDK in CI (slower, but more robust)

*Runtime error "DLL not found":*
- Check that all dependencies are in the bundle (use `ldd` or `dumpbin /dependents`)
- Verify bin/ and lib/gstreamer/ structure is correct

*ABI mismatch or crashes:*
- Verify all DLLs are MSVC-built (check with `dumpbin /headers` → look for MSVC timestamp)
- Ensure GLib version matches between vcpkg dependencies and GStreamer

**See also:** `docs/WINDOWS.md` → "GitHub Actions CI/CD" for complete implementation details

---

## macOS Build (Experimental)

**Status:** Code is cross-platform ready, untested on macOS.

### Prerequisites

```bash
# Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install dependencies
brew install cmake pkg-config python@3.11
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad
brew install grpc protobuf spdlog glib
```

### Build

```bash
cd drunk_call_service
make release
```

**Expected output:** `bin/drunk-call-service-darwin`

**Audio:** Uses CoreAudio (`osxaudiosrc`/`osxaudiosink`)

**Packaging:** Not yet implemented (would use .app bundle or .dmg)

---

## Packaging Status

**Linux:**
- ✅ AppImage (implemented, tested, production-ready)
- Flatpak (future)
- Snap (future)

**Windows:**
- ✅ ZIP archive (implemented)
- 🚧 Inno Setup installer (framework complete, bundling strategy finalized)
  - See `windows-installer/siproxylin.iss` for installer script
  - See `docs/WINDOWS.md` for complete documentation
  - Bundles: Python embeddable (25 MB) + GStreamer generous (30 MB) + vcpkg (50 MB)
  - Installer download: ~112 MB
  - First run: pip install ~760 MB (internet required)
  - User can choose system Python or bundled Python
  - GStreamer always bundled (includes H.264, H.265, extra codecs)
  - Status: Implementation in progress
- MSI installer (future - enterprise deployment)

**macOS:**
- .app bundle (code ready, untested)
- .dmg disk image (future)
- Homebrew formula (future)

---
