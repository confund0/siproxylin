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

### Build Dependencies (C++ Call Service)

Required to compile `drunk_call_service/` on Debian/Ubuntu:

```bash
sudo apt install build-essential cmake pkg-config \
  protobuf-compiler protobuf-compiler-grpc \
  libgrpc-dev libgrpc++-dev \
  libspdlog-dev libunwind-dev libsrtp2-dev libasound2-dev \
  libnice-dev gstreamer1.0-nice \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
  libgstreamer-plugins-bad1.0-dev
```

CMake looks up Protobuf, gRPC (CONFIG mode), spdlog, Threads, GStreamer and glib-2.0 — `pkg-config` is mandatory. `protobuf-compiler-grpc` ships the `grpc_cpp_plugin` used by `proto/` codegen.

### Runtime Dependencies

Required at runtime on Debian/Ubuntu, on top of `pip install -r requirements.txt`:

```bash
# GStreamer runtime plugins (media + WebRTC + Qt6 video sink)
sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-qt6 gstreamer1.0-x gstreamer1.0-nice \
  gstreamer1.0-pipewire gstreamer1.0-pulseaudio

# Desktop integration
sudo apt install dbus-x11 libnotify-bin xdg-utils libegl1

# Spell check — libenchant is the bridge to hunspell, both are required
sudo apt install libenchant-2-2 hunspell \
  hunspell-en-us hunspell-de-de hunspell-ru hunspell-lt \
  hunspell-es hunspell-ro hunspell-ar
```

**Notes:**
- `libnotify-bin` provides `notify-send` used by `siproxylin/services/notification.py`
- `xdg-utils` provides `xdg-open` for attachments/links
- `libenchant-2-2` is required even if hunspell is installed — PyEnchant talks to libenchant, and libenchant loads hunspell dictionaries from `/usr/share/hunspell/` via `XDG_DATA_DIRS`. Installing hunspell alone is not enough.
- Install only the `hunspell-*` dictionary packages for languages you actually need
- Running the AppImage (as opposed to building from source) additionally needs FUSE — `libfuse2t64` on current Debian, or pass `--appimage-extract-and-run`

### Python Environment

```bash
sudo apt install python3 python3-venv python3-pip
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### Build and Run

```bash
cd drunk_call_service && make clean && make && make install && cd ..
venv/bin/python main.py
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
make           # Build release version (Linux/macOS)
make release   # Build optimized release binary (Linux/macOS)
make debug     # Build with debug symbols and sanitizers (Linux/macOS)
make winrel    # Build optimized release binary (Windows/MSVC)
make windbg    # Build debug binary (Windows/MSVC)
make test      # Run unit tests
make clean     # Remove build artifacts
make install   # Install binary to bin/
make check-deps # Verify all build dependencies (Linux/macOS)
make help      # Show all available targets
```

**Binary location:** `drunk_call_service/bin/drunk-call-service-{linux|windows|darwin}`

**Windows builds:**
```bash
# Default (assumes vcpkg in C:/vcpkg)
make winrel

# Custom vcpkg location
make winrel VCPKG_ROOT=D:/vcpkg
```

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

**See also:** `docs/WINDOWS.md` for complete guide

### Quick Start (Installer)

```cmd
REM 1. Build C++ service
cd drunk_call_service
make winrel VCPKG_ROOT=C:/vcpkg

REM 2. Prepare installer dependencies
cd ..\windows-installer
prepare-windows-installer.bat

REM 3. Build installer
build-installer-bundled.bat
```

**Output:**
- Installer: `dist/Siproxylin-Setup-v{VERSION}-bundled.exe` (~100 MB)
- Self-contained with Python + GStreamer + vcpkg DLLs bundled

### Prerequisites

See `docs/WINDOWS.md` for complete setup instructions.

**Summary:**
- Visual Studio 2019/2022 (C++ workload)
- vcpkg (grpc, protobuf, spdlog, glib)
- GStreamer 1.x MSVC build (for development only)
- Python 3.11.9
- Inno Setup 6.x (for installer)

### Troubleshooting

See `docs/WINDOWS.md` for detailed troubleshooting guide.

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
- ✅ AppImage (production-ready)

**Windows:**
- ✅ Inno Setup installer (production-ready)
  - Self-contained with Python + GStreamer + vcpkg bundled
  - Installer size: ~100 MB
  - Downloads pip packages during install (~760 MB, internet required)
  - See `docs/WINDOWS.md` for details

**macOS:**
- Code is cross-platform ready, untested

---

## Just a drop in section on how to run on Alpine Linux
Alpine 3.21 and 3.22 ships Python 3.12 which causes very strange issues. 
As a quick and safe solution I'd recommend:

1. Install Debian 12 chroot
2. Do the prep in the chroot (see below)
3. Setup init to mount dev and proc
4. Use bubble-wrap to provide in-chroot directory mappings and restrictions (sound, video, attachments, configs) 

P.S. Debian 13 has strange behavior when creating GStreamer pipelines in chroot+bwrap.
Stick with Debian 12 (bookworm) for now

### The full package list to install in the chroot
```
# Debian 12 (bookworm) prep for Siproxylin
# This was used to create chrooted installation on Alpine
# Run for chrrot, but also serves as a reference

# Basic OS prep
apt update && apt upgrade -y
apt install apt-file -y
apt install bash-completion  -y
apt install psmisc  -y
apt install wget curl gpg -y
apt install locales -y

echo "***************************************"
echo "* Setup locales before moving forward *"
echo "***************************************"
exit

# Siproxylin deps
apt install libegl1 -y
apt install dbus-x11  -y
apt install python3 -y
apt install python3.11-venv  -y
apt install gstreamer1.0-qt6 gstreamer1.0-x gstreamer1.0-pipewire gstreamer1.0-pulseaudio gstreamer1.0-nice -y
apt install gstreamer1.0-plugins-bad -y
apt install gstreamer1.0-plugins-good  -y
apt install fonts-noto-color-emoji
apt install xdg-utils -y
apt install hunspell-en-gb hunspell-lt hunspell libenchant-2-2 -y
apt install libnotify-bin  -y

# Dev stuff to build call service
apt install build-essential  -y
apt install cmake -y
apt install pkg-config  -y
apt install libgstreamer-plugins-base1.0-dev libgstreamer-plugins-bad1.0-dev  -y
apt install protobuf-compiler -y
apt install libgrpc-dev  -y
apt install libgrpc++-dev  -y
apt install protobuf-compiler-grpc -y
apt install libspdlog-dev  -y


# mkdir -p /opt/siproxylin
# cd /opt/siproxylin
#
# python3 -m venv venv
# venv/bin/pip install -r requirements.txt
```

