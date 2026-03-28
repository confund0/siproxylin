# Font Configuration - Emoji Support Across Platforms

> **Last Updated:** 2026-03-13
> **Status:** Linux (implemented), Windows (planned), macOS (unknown)

---

## Overview

Siproxylin uses emojis extensively in the UI for buttons and visual elements. Emoji rendering quality varies significantly across platforms:

- **Linux:** Excellent with Noto Color Emoji (modern, colorful, vector-based)
- **Windows 10/11:** Poor with default Segoe UI Emoji (dated, low-quality)
- **macOS:** Good with Apple Color Emoji (native)

This document describes how to ensure consistent, high-quality emoji rendering across all platforms.

---

## Current Implementation: Linux (AppImage)

### How It Works

**Package:** `fonts-noto-color-emoji` bundled in AppImage

**Configuration:** `appimage.yml` line 58
```yaml
apt:
  include:
    - fonts-noto-color-emoji
    - fonts-liberation
    - fontconfig
```

**Font Path Setup:** `build-appimage.sh` AppRun script (lines 81-83)
```bash
# Font configuration (bundled fonts + system fallback)
export FONTCONFIG_PATH="$APPDIR/etc/fonts:/etc/fonts"
```

**Result:** AppImage ships with Noto Color Emoji fonts in `AppDir/usr/share/fonts/`, and fontconfig automatically discovers them.

**License:** SIL Open Font License (OFL) - free to distribute

---

## Planned Implementation: Windows

### Problem

Windows ships with Segoe UI Emoji, which:
- Looks outdated (flat design from 2016)
- Limited Unicode coverage compared to Noto Color Emoji
- Not consistent with Linux version

### Solution: Bundle Noto Color Emoji

**Strategy:**
1. Download `NotoColorEmoji_WindowsCompatible.ttf` (not the regular .ttf!)
   - Regular NotoColorEmoji.ttf uses COLRv1 format (Android/Chrome only)
   - WindowsCompatible version uses COLRv0 format (Qt/Windows compatible)
   - Source: https://fonts.google.com/noto/specimen/Noto+Color+Emoji

2. Add font to project:
   ```
   siproxylin/resources/fonts/NotoColorEmoji_WindowsCompatible.ttf
   ```

3. Load font at application startup (Windows only):
   ```python
   # In main.py after QApplication creation (around line 169)
   if platform.system() == "Windows":
       from PySide6.QtGui import QFontDatabase
       font_path = Path(__file__).parent / "siproxylin" / "resources" / "fonts" / "NotoColorEmoji_WindowsCompatible.ttf"
       font_id = QFontDatabase.addApplicationFont(str(font_path))
       if font_id != -1:
           logger.info("Loaded bundled Noto Color Emoji font")
       else:
           logger.warning("Failed to load bundled emoji font, using system default")
   ```

4. Package font in distribution:
   - `build-windows.bat` already copies `siproxylin/` directory, which includes `resources/`
   - No additional changes needed to packaging script

**Qt Support:** Qt 6.9+ supports COLRv1 (with gradients), but COLRv0 (WindowsCompatible) works on all Qt6 versions and is sufficient for our needs.

**License:** SIL Open Font License (OFL) - free to bundle and distribute

---

## macOS Status

**Unknown** - Code is cross-platform ready but untested on macOS.

**Expected behavior:**
- macOS ships with Apple Color Emoji (high quality, native)
- Should work out of the box without bundling
- If needed, same approach as Windows (bundle NotoColorEmoji_WindowsCompatible.ttf)

---

## Implementation Checklist (Windows)

- [ ] Download NotoColorEmoji_WindowsCompatible.ttf from Google Fonts
- [ ] Create `siproxylin/resources/fonts/` directory
- [ ] Add font file to `siproxylin/resources/fonts/NotoColorEmoji_WindowsCompatible.ttf`
- [ ] Add platform check in `main.py` to load font on Windows using `QFontDatabase.addApplicationFont()`
- [ ] Test on real Windows hardware (verify emoji rendering in buttons, dialogs, emoji picker)
- [ ] Verify font file is included in `dist/Siproxylin-Windows-x64.zip` (should be automatic)
- [ ] Add font license file to `siproxylin/resources/fonts/LICENSE.txt` (OFL)

---

## References

- **Qt Documentation:** [Supporting Google Emoji Font Policy (Qt 6.10)](https://doc.qt.io/qt-6/android-emojis.html)
- **Noto Color Emoji:** https://fonts.google.com/noto/specimen/Noto+Color+Emoji
- **Qt Forum:** [Using Colour Emojis as a substitution font in Windows](https://forum.qt.io/topic/89997/using-colour-emojis-as-a-substitution-font-in-windows)
- **License:** SIL Open Font License 1.1

---

## Technical Notes

### Why NotoColorEmoji_WindowsCompatible.ttf?

There are two versions of Noto Color Emoji:

1. **NotoColorEmoji.ttf** (COLRv1)
   - Modern format with gradients
   - Scales to any size without loss
   - Only supported by: Android, Chrome, Chromium OS, Qt 6.9+
   - **NOT recommended for Windows compatibility**

2. **NotoColorEmoji_WindowsCompatible.ttf** (COLRv0)
   - Older format, no gradients
   - Still excellent quality for standard emoji
   - Supported by: Qt 5.x/6.x, Windows, macOS, Linux
   - **Recommended for cross-platform Qt apps**

### Qt Font Loading

Qt provides `QFontDatabase.addApplicationFont()` to load fonts from files at runtime:
- Returns font ID on success (-1 on failure)
- Font becomes available globally in the application
- No need to explicitly set it as default - Qt's font fallback system will use it for emoji characters

### AppImage Font Discovery

AppImage uses fontconfig's standard font discovery:
- Fonts in `AppDir/usr/share/fonts/` are automatically discovered
- `FONTCONFIG_PATH` ensures bundled fonts are checked before system fonts
- No code changes needed - purely declarative (appimage.yml)

---

## Emoji Usage in Siproxylin

Emojis are used extensively in:
- **Buttons:** Call buttons (📞), mute (🔇), hang up (📵), settings (⚙️)
- **Dialogs:** Incoming call (📲), outgoing call (📞), emoji picker
- **Status indicators:** Online (🟢), away (🟡), offline (⚫)
- **Message reactions:** 👍 ❤️ 😂 🎉 etc.

Consistent emoji rendering across platforms is critical for UI/UX quality.

---

## Future: Font Customization

**Potential feature:** Allow users to choose emoji font (Noto, Apple, Segoe, Twemoji)
- Would require bundling multiple emoji fonts
- User preference stored in config
- Load selected font via `QFontDatabase.addApplicationFont()`
- Not prioritized - shipping Noto Color Emoji is sufficient for now
