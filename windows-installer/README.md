# Windows Installer for Siproxylin

This directory contains the Inno Setup bundled installer for Windows.

## Overview

**Installer Type:** Self-contained with bundled dependencies
**File:** `siproxylin-bundled.iss`
**Size:** ~100 MB
**Internet Required:** Yes (for pip packages, ~760 MB during install)

The installer includes:
- ✅ Python 3.11.9 embeddable (~25 MB)
- ✅ GStreamer runtime libraries (~30 MB)
- ✅ vcpkg runtime DLLs (~50 MB)
- ✅ Siproxylin application
- ✅ Downloads pip packages during installation (shows progress)
- ✅ User choice: Install for all users (requires admin) OR just for me (no admin)
- ✅ Auto-upgrades old versions
- ✅ Clean uninstaller

---

## Quick Start

### Prerequisites

1. **Inno Setup 6.x**
   - Download: https://jrsoftware.org/isdl.php
   - Install to: `C:\Program Files (x86)\Inno Setup 6\`

2. **Build C++ call service**
   ```bash
   cd drunk_call_service
   make winrel VCPKG_ROOT=C:/vcpkg
   ```

### Build Installer

```cmd
cd windows-installer

REM Step 1: Prepare bundled dependencies
prepare-windows-installer.bat

REM Step 2: Build installer
build-installer-bundled.bat
```

**Output:** `dist/Siproxylin-Setup-v{VERSION}-bundled.exe` (~100 MB)

---

## What the Scripts Do

### prepare-windows-installer.bat

Downloads and organizes dependencies into `bundle/` directory:

1. **Python 3.11.9 embeddable** (~10 MB) - from python.org
2. **GStreamer runtime** (~12 MB) - from GitHub releases `deps-v{VERSION}`
3. **vcpkg DLLs** (~3 MB) - from GitHub releases `deps-v{VERSION}`

### build-installer-bundled.bat

1. Extracts version from `../version.sh`
2. Creates `version-generated.iss` with version define
3. Compiles installer using Inno Setup
4. Output: `dist/Siproxylin-Setup-v{VERSION}-bundled.exe`

---

## Testing the Installer

### Test on fresh Windows 10/11 VM:

**Test Checklist:**

1. **Installation**
   - [ ] Run installer
   - [ ] Installation mode page appears with radio buttons
   - [ ] Test "Install just for me" (no admin password needed)
   - [ ] Pip packages download (console shows progress)
   - [ ] Installation completes without errors
   - [ ] Start Menu shortcut created
   - [ ] Desktop shortcut created (if selected)
   - [ ] Verify install location matches choice

2. **Application Launch**
   - [ ] Launch from Start Menu
   - [ ] Application window shows version in title
   - [ ] No DLL errors
   - [ ] Can add XMPP account

3. **Audio Call**
   - [ ] Login to account
   - [ ] Make call (test with Conversations.im or Dino)
   - [ ] Bidirectional audio works
   - [ ] No crashes

4. **Upgrade**
   - [ ] Install older version first
   - [ ] Run new installer
   - [ ] Old version auto-uninstalled
   - [ ] Settings preserved

**Bonus Tests:**
   - [ ] Test "Install for anyone using this computer" mode (requires admin password)
   - [ ] Verify installation to `C:\Program Files\Siproxylin`
   - [ ] Test that non-admin user can still run the app (if installed for all users)

5. **Uninstall**
   - [ ] Go to "Add/Remove Programs"
   - [ ] Uninstall Siproxylin
   - [ ] Program files removed
   - [ ] Shortcuts removed
   - [ ] User data preserved in `%APPDATA%\Siproxylin\` and `%LOCALAPPDATA%\Siproxylin\`

---

## File Structure

```
windows-installer/
├── siproxylin-bundled.iss         # Inno Setup script
├── prepare-windows-installer.bat  # Download dependencies
├── build-installer-bundled.bat    # Build installer
├── siproxylin-launcher.bat        # Application launcher (sets PATH)
├── version-generated.iss          # Auto-generated version (gitignored)
├── README.md                      # This file
└── bundle/                        # Created by prepare script (gitignored)
    ├── python/                    # Python 3.11.9 embeddable
    ├── gstreamer/                 # GStreamer runtime DLLs
    │   ├── bin/                   # Core DLLs
    │   └── lib/gstreamer-1.0/     # Plugins
    └── vcpkg/                     # vcpkg runtime DLLs
```

---

## Installer Features

- **Self-contained**: All dependencies bundled (except pip packages)
- **Installation mode choice**: User can choose during install:
  - **"Install for anyone using this computer"** (requires admin)
    - → `C:\Program Files\Siproxylin` (all users)
  - **"Install just for me"** (no admin needed)
    - → `%LOCALAPPDATA%\Programs\Siproxylin` (current user only)
- **Version info**: Reads from `version.sh`, shows in window title
- **Progress**: Console window shows pip install progress
- **Disk space**: Warns user about ~1 GB requirement
- **Upgrade detection**: Auto-removes old version
- **User data**: Preserved on uninstall (stored in AppData)
- **Silent install**:
  - All users: `Siproxylin-Setup-v{VERSION}-bundled.exe /ALLUSERS /SILENT`
  - Current user: `Siproxylin-Setup-v{VERSION}-bundled.exe /CURRENTUSER /SILENT`

---

## Dependency Management

Dependencies are version-tagged and stored as GitHub releases (`deps-v{VERSION}`).

**To update dependencies for new version:**

```cmd
REM 1. On Windows dev machine with working build:
cd drunk_call_service/bin
zip ..\..\siproxylin-windows-vcpkg-deps-v{VERSION}.zip *.dll

cd ..\lib\gstreamer
zip ..\..\..\siproxylin-windows-gst-deps-v{VERSION}.zip bin\ lib\

REM 2. Upload to GitHub
gh release create deps-v{VERSION} --title "Windows Dependencies v{VERSION}"
gh release upload deps-v{VERSION} siproxylin-windows-*.zip

REM 3. Update version.sh
REM prepare-windows-installer.bat will auto-download from new release
```

---

## Troubleshooting

**"Error reading version from version.sh"**
- Ensure `../version.sh` exists with `SIPROXYLIN_VERSION="v0.0.27"`

**"Source file not found"**
- Run `make winrel` to build C++ service first
- Run `prepare-windows-installer.bat` to download dependencies

**"Pip install fails"**
- Check internet connection (downloads ~760 MB)
- Verify `requirements.txt` pins slixmpp==1.8.5 on Windows

**"Application won't start"**
- Check logs: `%LOCALAPPDATA%\Siproxylin\Logs\`
- Verify launcher uses `python main.py --dot-data-dir`

**"Call service won't start"**
- Check: `%LOCALAPPDATA%\Siproxylin\Logs\drunk-call-service.err`

---

## Code Signing (Optional)

To avoid Windows SmartScreen warnings, sign the installer:

1. Obtain code signing certificate
2. Add to `[Setup]` section in `siproxylin-bundled.iss`:
   ```pascal
   SignTool=signtool /f MyCert.pfx /p MyPassword /t http://timestamp.digicert.com $f
   SignedUninstaller=yes
   ```

---

## Distribution

Upload to GitHub releases:
```bash
gh release upload v{VERSION} dist/Siproxylin-Setup-v{VERSION}-bundled.exe
```

---

## References

- Inno Setup Documentation: https://jrsoftware.org/ishelp/
- Project Documentation: `../docs/WINDOWS.md`

---

**Last Updated**: 2026-03-26
**Status**: Production Ready
