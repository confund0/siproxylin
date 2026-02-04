# Build & Packaging Documentation

> **Last Updated:** 2026-02-03

---

## Development Build

**Prerequisites:**
- Python 3.11+
- Go 1.21+
- GStreamer 1.0
- Qt6 libraries

**Steps:**
```bash
cd drunk_call_service && ./build.sh && cd ..
pip install -r requirements.txt
python main.py
```

---

## Alpine Linux Build (Experimental)

**Status:** Work in progress (currently in git stash)

**Goal:** Native musl build for Alpine Linux users

**Known issues:**
- Encountering method reference issues that may be related to Python version differences (3.11 on Debian vs 3.12 on Alpine)
- Root cause not yet identified
- glibc AppImage won't run on Alpine without workarounds

**Planned approach:**
- System packages: py3-pyside6, gstreamer-dev, python3-dev, build-base
- Create venv with `--system-site-packages` to use system PySide6
- Build Go call service natively

**Note:** This is a theoretical section. Implementation is pending resolution of Python version compatibility issues.

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

- `appimage-builder` - Install via pip or download AppImage version
- `appimagetool` - Auto-downloaded by build script

**Before building:** Unset `PYTHONHOME` and `PYTHONPATH` to prevent host Python interference

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
- Conservative: APT debs (~300MB), appimagetool, Go binary
- Invalidates on: appimage.yml changes, Go source changes
- Speeds up builds significantly

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

## Future Packaging

- Windows installer (NSIS/WiX)
- macOS package or Homebrew
