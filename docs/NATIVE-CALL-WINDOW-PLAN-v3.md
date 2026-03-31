# Native Call Window Implementation Plan v3 - Slim Control Bar

**Date:** 2026-03-31
**Branch:** video
**Status:** Planning
**Previous Plans:**
- v1: C++ GTK approach (FAILED - threading nightmare)
- v2: Wayland pointer approach (FAILED - process boundary impossibility)

---

## Why v2 Failed

See `NATIVE-CALL-WINDOW-PLAN-v2.md` for full details. TL;DR:

**Fatal flaw:** Wayland handles (`wl_display*`, `wl_surface*`) are **process-specific** and cannot be shared between processes.

- Python process has Wayland connection A
- C++ process has Wayland connection B
- Passing pointers from A → B = **meaningless** (invalid memory in different process)
- Result: SEGV when C++ waylandsink tries to use Python's surface pointer

**Key lesson:** Process boundaries matter. Native OS handles (Wayland, file descriptors) are process-local. X11 XIDs worked cross-process (legacy design), but Wayland doesn't do this.

---

## The v3 Approach: Embrace the Native Window

**Stop fighting GStreamer. Let it do what it does best.**

### The Solution

**For video calls:**
- C++ GStreamer creates native video window (autovideosink)
- Python Qt creates **slim control bar** that floats on top
- User can position control bar anywhere (over video, on taskbar, corner, etc.)

**For audio calls:**
- Keep existing full-size call window (works fine)

### Why This Works

✅ **No cross-process handles** - GStreamer creates its own window
✅ **Works everywhere** - No Wayland/X11/Windows-specific code
✅ **Simple** - Just Qt window positioning
✅ **User-friendly** - Control bar can be moved to preferred location
✅ **Cross-platform** - Same UX on Linux/Windows/macOS

---

## Architecture

```
Video Call:
┌─────────────────────────────────────┐
│  GStreamer Native Window (C++)      │
│  ┌────────────────────────────────┐ │
│  │                                │ │
│  │     Remote Video Feed          │ │
│  │                                │ │
│  │  ┌──────────────┐              │ │
│  │  │ Self-view    │ (PiP)        │ │
│  │  │ (compositor) │              │ │
│  │  └──────────────┘              │ │
│  │                                │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
         ▲
         │ (User can position anywhere)
         │
┌────────────────────────────────────────┐
│  ⏯ Mute  📹 Cam  ☎ Hangup  ⏱ 00:42   │ ← Slim Qt Control Bar
│  ☑ Show Technical Details             │   (always on top)
└────────────────────────────────────────┘

Audio Call:
┌─────────────────────────────────┐
│  📞 Audio Call Window           │
│  peer@example.com               │
│                                 │
│  Status: Connected              │
│  Duration: 00:42                │
│                                 │
│  [Mute]  [Hangup]               │
│                                 │
│  ☑ Show Technical Details       │
│  ┌───────────────────────────┐  │
│  │ Connection: relay         │  │
│  │ Codec: OPUS               │  │
│  │ ... (expanded details)    │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

---

## Implementation Details

### 1. Fix Windows Parent Issue

**Problem:** Call window disappears when main window minimizes (Windows only)

**Root cause:**
```python
# call_window.py line 53
super().__init__(parent)  # Takes MainWindow as parent
```

**Fix:**
```python
super().__init__(None)  # No parent = truly independent window
```

**Result:** Call window stays visible when main window minimizes (all platforms)

---

### 2. Slim Mode for Video Calls

**New window modes:**
- **Slim mode** (default for video): Compact horizontal bar, always-on-top, frameless
- **Expanded mode** (toggled by checkbox): Full window with technical details

**Window flags for slim mode:**
```python
if 'video' in media_types:
    # Slim mode
    self.setWindowFlags(
        Qt.Window |              # Independent window
        Qt.WindowStaysOnTopHint | # Always on top
        Qt.FramelessWindowHint   # No window frame
    )
    self.resize(400, 60)  # Compact bar
else:
    # Normal mode (audio calls)
    self.setWindowFlags(Qt.Window)
    self.resize(550, 350)
```

---

### 3. Slim Mode UI Layout

**Compact horizontal layout:**
```
┌────────────────────────────────────────┐
│ ⏯ Mute 📹 Cam ☎ Hangup ⏱ 00:42        │
│ ☑ Show Technical Details               │
└────────────────────────────────────────┘
```

**Components:**
- Mute button (audio toggle)
- Camera button (video toggle, future)
- Hangup button
- Call timer
- Checkbox: "Show Technical Details" → expands to full window

**Expanded mode:**
- Same as current call window
- Shows connection stats, bandwidth, codecs, etc.
- Resizable, normal window chrome

---

### 4. Implementation Checklist

**Phase 1: Fix Windows issue**
- [ ] Remove parent dependency from CallWindow
- [ ] Test on Windows: Call window persists when main minimizes
- [ ] Test on Linux: Ensure no regression

**Phase 2: Slim mode**
- [ ] Add mode detection: `self.is_slim = 'video' in media_types`
- [ ] Create slim UI layout (horizontal buttons + timer)
- [ ] Set window flags for slim mode (frameless, always-on-top)
- [ ] Add toggle: "Show Technical Details" → expand to normal mode

**Phase 3: Polish**
- [ ] Make slim window draggable (custom title bar drag)
- [ ] Remember user's preferred position (save in config)
- [ ] Smooth transition between slim ↔ expanded modes
- [ ] Test on all platforms (Linux, Windows, macOS)

---

## Benefits Over Previous Approaches

**vs v1 (C++ GTK):**
- ✅ No threading issues (Qt in Python, GStreamer in C++)
- ✅ No complex lifecycle management
- ✅ Simple Qt window code

**vs v2 (Wayland pointers):**
- ✅ No cross-process handle issues
- ✅ Works on all platforms (not just Wayland)
- ✅ No platform-specific code
- ✅ No ctypes hacks

**vs autovideosink (current):**
- ✅ Same video rendering (native GStreamer window)
- ✅ Better UX: Slim control bar instead of separate full window
- ✅ User controls positioning
- ✅ Optional expanded mode for tech details

---

## Known Limitations

**Current implementation:**
- Slim window is independent - not automatically positioned
- User must manually position it over video window
- No automatic following if video window moves

**Future enhancements:**
- Track GStreamer window position and auto-follow
- Magnetic snap to video window edges
- Remember preferred position per-user
- Keyboard shortcuts for quick toggle

---

## Success Criteria

**v3 PoC Complete:**
- [ ] Call window stays visible when main window minimizes (Windows fix)
- [ ] Video calls show slim control bar by default
- [ ] Slim bar is always-on-top and draggable
- [ ] "Show Technical Details" checkbox expands to full window
- [ ] Audio calls use current full window (unchanged)
- [ ] Works on Linux, Windows, macOS

**Production Ready:**
- [ ] Auto-follow video window option
- [ ] Remember user's preferred position
- [ ] Smooth animations for expand/collapse
- [ ] Keyboard shortcuts
- [ ] Theme-aware styling

---

## Timeline

**Phase 1 (Windows fix):** 1 hour
**Phase 2 (Slim mode):** 2-3 hours
**Phase 3 (Polish):** 1-2 hours

**Total:** ~5 hours (vs 18 hours wasted on v1, multiple hours on v2)

---

**Last Updated:** 2026-03-31
**Status:** Ready for implementation
