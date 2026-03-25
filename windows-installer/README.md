# Windows Installer for Siproxylin

This directory contains Inno Setup scripts for creating professional Windows installers.

## Two Installer Versions

### 1. Bundled Installer (RECOMMENDED) ⭐
**File:** `siproxylin-bundled.iss`
**Size:** ~80-100 MB
**Approach:** Self-contained, bundles all dependencies

The installer includes:
- ✅ Python 3.11.9 embeddable (~25 MB)
- ✅ GStreamer runtime libraries (~30 MB)
- ✅ vcpkg runtime DLLs (~10 MB)
- ✅ Siproxylin application
- ✅ Works completely offline (no downloads)
- ✅ Faster installation
- ✅ More reliable (no download failures)

### 2. Download Installer (LEGACY)
**File:** `siproxylin.iss`
**Size:** ~5 MB
**Approach:** Downloads Python + GStreamer during installation (~175 MB total)

Use only if you need a small installer package (slow internet users).

## Quick Start (Bundled Installer)

### Prerequisites

1. **Install Inno Setup 6** (Windows)
   - Download: https://jrsoftware.org/isdl.php
   - Get the **Unicode** version (default)
   - Install to: `C:\Program Files (x86)\Inno Setup 6\`

2. **Build the C++ call service** (Windows or Linux cross-compile)
   ```bash
   cd drunk_call_service
   make winrel    # Creates drunk-call-service-windows.exe
   ```

3. **Build the Windows distribution** (Windows or Linux)
   ```bash
   cd ..
   ./build-windows.bat   # Creates dist/windows/
   ```

### Build Process

**Step 1: Prepare bundled dependencies**
```cmd
cd windows-installer
prepare-windows-installer.bat
```

This script will:
- Download Python 3.11.9 embeddable from python.org
- Collect minimal GStreamer DLLs from your local installation
- Collect vcpkg runtime DLLs from `drunk_call_service/bin/`
- Organize everything in `bundle/` directory

**Step 2: Build the installer**
```cmd
build-installer-bundled.bat
```

Output: `dist/Siproxylin-Setup-v{VERSION}-bundled.exe` (~80-100 MB)

## Alternative Build Methods

### Option A: Manual (Inno Setup GUI)

1. Run `prepare-windows-installer.bat` first
2. Open `siproxylin-bundled.iss` in Inno Setup Compiler
3. Click **Build** → **Compile**
4. Installer created in `dist/`

### Option B: Command Line

```cmd
REM Prepare bundles first
prepare-windows-installer.bat

REM Then compile
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" siproxylin-bundled.iss
```

## Testing the Bundled Installer

### Test on a fresh Windows 10/11 VM:

**Requirements:**
- Windows 10 or Windows 11 (x64)
- No prior installation of Python or GStreamer
- No internet connection required (tests offline capability)

**Test Checklist:**

1. **Fresh Install**
   - [ ] Copy installer to VM (no internet needed)
   - [ ] Run `Siproxylin-Setup-v{VERSION}-bundled.exe`
   - [ ] Installation completes without errors
   - [ ] No download prompts or internet requests
   - [ ] Start Menu shortcut created
   - [ ] Desktop shortcut created (if selected)

2. **Application Launch**
   - [ ] Launch from Start Menu shortcut
   - [ ] Application window appears
   - [ ] No DLL missing errors
   - [ ] Can add XMPP account

3. **Audio Call Test**
   - [ ] Login to test account
   - [ ] Make call to Conversations.im or Dino
   - [ ] Receive incoming call
   - [ ] Bidirectional audio works
   - [ ] No crashes during call

4. **Upgrade Test**
   - [ ] Install older version first
   - [ ] Run new installer
   - [ ] Old version uninstalled automatically
   - [ ] New version installs successfully
   - [ ] Settings preserved

5. **Uninstall Test**
   - [ ] Go to "Add/Remove Programs"
   - [ ] Uninstall Siproxylin
   - [ ] Removes `C:\Program Files\Siproxylin\`
   - [ ] Removes Start Menu shortcuts
   - [ ] Asks about removing `%USERPROFILE%\.siproxylin\`

6. **File Verification**
   - [ ] Check `C:\Program Files\Siproxylin\python\` exists
   - [ ] Check `C:\Program Files\Siproxylin\lib\gstreamer\` exists
   - [ ] Check `C:\Program Files\Siproxylin\lib\*.dll` exists
   - [ ] Check logs in `%USERPROFILE%\.siproxylin\logs\`

## File Structure

```
windows-installer/
├── siproxylin-bundled.iss         # Bundled installer script (RECOMMENDED)
├── siproxylin.iss                 # Download installer script (legacy)
├── prepare-windows-installer.bat  # Prepare bundles before building
├── build-installer-bundled.bat    # Build bundled installer
├── build-installer.bat            # Build download installer (legacy)
├── siproxylin-launcher.bat        # Launcher with PATH setup
├── README.md                      # This file
└── bundle/                        # Created by prepare script
    ├── python/                    # Python 3.11.9 embeddable
    ├── gstreamer_temp/            # GStreamer runtime DLLs
    └── vcpkg/                     # vcpkg runtime DLLs
```

## Installer Features

### Bundled Installer (siproxylin-bundled.iss)
- ✅ Self-contained (~80-100 MB)
- ✅ Works offline (no downloads)
- ✅ Faster installation
- ✅ More reliable
- ✅ Bundles Python 3.11.9 embeddable
- ✅ Bundles minimal GStreamer runtime
- ✅ Bundles vcpkg DLLs
- ✅ Installs pip packages from PyPI during install
- ✅ Auto-upgrades old versions
- ✅ Clean uninstaller

### Silent Installation
```cmd
Siproxylin-Setup-v0.0.4-bundled.exe /SILENT
```

### Upgrade Handling
- Detects previous installation
- Uninstalls old version silently
- Installs new version
- Preserves user config in `%USERPROFILE%\.siproxylin\`

### Uninstaller
- Removes all program files
- Removes shortcuts
- Optionally removes user data (prompts user)

## Customization

### Change GStreamer version
Edit `siproxylin.iss` line with GStreamer filename:
```pascal
Source: "prereqs\gstreamer-1.0-msvc-x86_64-1.24.10.msi"; ...
```

Update both the filename and the installer command.

### Change installation directory
Users can change during installation, but default is:
```pascal
DefaultDirName={autopf}\{#AppName}  ; C:\Program Files\Siproxylin
```

### Add more prerequisites
Add new component in `[Components]` section and corresponding `[Run]` command.

## Troubleshooting

### "Error reading version from version.sh"
- Make sure `../version.sh` exists
- Check format: `SIPROXYLIN_VERSION="x.y.z"`

### "Source file not found: dist/windows"
- Run `build-windows.bat` first to create distribution

### "Download failed"
- Check internet connection
- Prerequisites are downloaded from python.org and gstreamer.org
- User can cancel and install Python/GStreamer manually

### Installer won't run on Windows 7/8
- Minimum version is Windows 10 (set in `[Setup]` section)
- Change `MinVersion=10.0` to `MinVersion=6.1` for Windows 7+

### Python dependencies fail to install
- Check internet connection (pip downloads from PyPI)
- Check `requirements.txt` is in `dist/windows/`
- Run installer as Administrator

## Code signing (optional)

To sign the installer for Windows SmartScreen:

1. Get a code signing certificate
2. Add to `[Setup]` section:
```pascal
SignTool=signtool /f MyCert.pfx /p MyPassword /t http://timestamp.digicert.com $f
SignedUninstaller=yes
```

## Distribution

Upload to GitHub releases:
```bash
gh release upload v0.0.4 dist/Siproxylin-Setup-v0.0.4.exe
```

Installer size: ~2-5 MB (without bundled prerequisites)
With prerequisites: ~180 MB

## References

- Inno Setup Documentation: https://jrsoftware.org/ishelp/
- Gajim Windows installer (similar Python app): https://github.com/gajim/gajim/tree/master/win
- Pidgin Windows installer: https://github.com/pidgin/pidgin/tree/master/pidgin/win32/nsis
