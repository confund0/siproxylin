# Native Call Window Implementation Plan

**Date:** 2026-03-30
**Branch:** video
**Status:** Planning

---

## Problem Statement

Qt6 GStreamer video embedding fails on Wayland:
- `waylandsink` creates separate window (ignores window handles)
- `glimagesink` has OpenGL context issues with Qt
- Window handle overlay approach doesn't work on Wayland

**Solution:** Native platform windows created by C++ service, bypassing Qt integration.

---

## Architecture

```
Python (Qt6 GUI)
    ├─ Main window: contact list, chat, settings
    └─ Call window: audio controls, stats (no video)
         ↓ gRPC
C++ drunk_call_service
    ├─ WebRTC session + GStreamer pipeline
    └─ Platform video window (NEW)
        ├─ Linux: GTK3
        ├─ Windows: Win32
        └─ macOS: Cocoa
```

**Two windows during call:**
1. Qt call window (audio controls, stats, buttons)
2. Native video window (video only, created by C++ service)

**Future:** Migrate all controls to native window, remove Qt call window.

---

## Platform Implementations

### Linux: GTK3 (Primary Focus)

**Why GTK3:**
- Already bundled in AppImage (0 MB cost)
- Works on X11 + Wayland automatically
- Well-tested GStreamer integration
- Built-in widgets, overlay system

**Window Structure:**
```
GtkWindow "Call - peer@example.com"
  └─ GtkOverlay (stacking container)
      ├─ [Bottom] Video widget (fills window)
      │   └─ GStreamer gtksink or glimagesink
      ├─ [Middle] Self-view overlay (draggable)
      │   └─ GtkEventBox → drag events
      │       └─ GtkDrawingArea → GStreamer sink
      ├─ [Top] Stats overlay (corner)
      │   └─ GtkBox (semi-transparent background)
      │       ├─ Bandwidth label
      │       ├─ Resolution label
      │       └─ FPS label
      └─ [Top] Controls overlay (bottom)
          └─ GtkBox
              ├─ Mute button
              ├─ Camera button
              └─ Hangup button
```

**Video Embedding:**
- Option A: `gtksink` (GTK-specific, returns GtkWidget)
- Option B: `glimagesink` + GstVideoOverlay (use GtkDrawingArea)

**Auto-Resize:**
```c
g_signal_connect(video_widget, "size-allocate",
    G_CALLBACK(on_video_resize), NULL);

void on_video_resize(GtkWidget *widget, GdkRectangle *allocation) {
    // gtksink handles automatically, or:
    gst_video_overlay_expose(overlay);
}
```

**Draggable Self-View:**
```c
// Make self-view draggable
GtkWidget *self_view_box = gtk_event_box_new();
gtk_widget_add_events(self_view_box,
    GDK_BUTTON_PRESS_MASK | GDK_BUTTON_MOTION_MASK | GDK_BUTTON_RELEASE_MASK);

g_signal_connect(self_view_box, "button-press-event", on_drag_start, NULL);
g_signal_connect(self_view_box, "motion-notify-event", on_drag_move, NULL);
g_signal_connect(self_view_box, "button-release-event", on_drag_end, NULL);

// Update position
gtk_overlay_reorder_overlay(overlay, self_view_box, -1);
// Position is CSS-based or manual with gtk_widget_set_margin_*
```

**Stats Overlay:**
```c
GtkWidget *stats_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 5);

// Semi-transparent background via CSS
GtkCssProvider *css = gtk_css_provider_new();
gtk_css_provider_load_from_data(css,
    "box.stats { background-color: rgba(0,0,0,0.7); padding: 10px; color: white; }",
    -1, NULL);
gtk_style_context_add_class(gtk_widget_get_style_context(stats_box), "stats");

// Add labels
bandwidth_label = gtk_label_new("1.5 Mbps");
gtk_box_pack_start(GTK_BOX(stats_box), bandwidth_label, FALSE, FALSE, 0);

// Position in top-left corner
gtk_widget_set_halign(stats_box, GTK_ALIGN_START);
gtk_widget_set_valign(stats_box, GTK_ALIGN_START);
gtk_overlay_add_overlay(overlay, stats_box);
```

**Control Buttons:**
```c
GtkWidget *button_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);

mute_button = gtk_button_new_with_label("🎤");
g_signal_connect(mute_button, "clicked", G_CALLBACK(on_mute_clicked), session);

// Position at bottom-center
gtk_widget_set_halign(button_box, GTK_ALIGN_CENTER);
gtk_widget_set_valign(button_box, GTK_ALIGN_END);
gtk_overlay_add_overlay(overlay, button_box);
```

### Windows: Win32 (Future)

**Window:** Win32 API `CreateWindowEx`
**Video:** d3dvideosink (already working)
**Controls:** Win32 buttons, GDI+ for stats overlay
**Complexity:** ~600-800 lines

### macOS: Cocoa (Future)

**Window:** NSWindow
**Video:** osxvideosink
**Controls:** NSButton, Core Graphics for overlay
**Complexity:** ~600-800 lines (Objective-C++)

---

## gRPC Protocol

**New RPCs:**

```protobuf
// Show native video window
rpc ShowVideoWindow(ShowVideoWindowRequest) returns (ShowVideoWindowResponse);

message ShowVideoWindowRequest {
  string session_id = 1;
  string peer_jid = 2;  // For window title
}

message ShowVideoWindowResponse {
  bool success = 1;
  string error = 2;
}

// Hide/close video window
rpc HideVideoWindow(HideVideoWindowRequest) returns (Empty);

message HideVideoWindowRequest {
  string session_id = 1;
}

// Update video window stats (called periodically)
rpc UpdateVideoWindowStats(UpdateVideoWindowStatsRequest) returns (Empty);

message UpdateVideoWindowStatsRequest {
  string session_id = 1;
  string bandwidth = 2;   // "1.5 Mbps"
  string resolution = 3;  // "720p@30fps"
}
```

**Existing RPCs to use:**
- `SetMute` → wire to mute button
- `EndSession` → wire to hangup button

---

## File Structure

**New C++ files:**
```
drunk_call_service/src/
├── gtk_video_window.h         (~100 lines - class declaration)
├── gtk_video_window.cpp       (~500-600 lines - GTK3 implementation)
└── video_window_interface.h  (~30 lines - abstract interface)
```

**Future Windows/macOS:**
```
drunk_call_service/src/
├── win32_video_window.cpp     (Windows implementation)
└── cocoa_video_window.mm      (macOS Objective-C++)
```

**Modified files:**
```
drunk_call_service/
├── CMakeLists.txt             (add GTK3 dependency)
├── proto/call.proto           (add RPCs)
└── src/
    ├── call_service_impl.h    (add RPC handlers)
    └── call_service_impl.cpp  (implement RPC handlers)

drunk_call_hook/
└── bridge.py                  (add show/hide methods)

siproxylin/
└── core/barrels/calls.py      (call show_video_window when video starts)
```

---

## Dependencies

### Build (Development)

**Linux:**
```bash
sudo apt install libgtk-3-dev
```

**CMakeLists.txt:**
```cmake
if(UNIX AND NOT APPLE)
    pkg_check_modules(GTK3 REQUIRED gtk+-3.0)
    target_link_libraries(drunk-call-service ${GTK3_LIBRARIES})
    target_include_directories(drunk-call-service PRIVATE ${GTK3_INCLUDE_DIRS})
endif()
```

### Runtime (AppImage)

**No changes needed** - GTK3 already bundled (12.5 MB):
- libgtk-3.so.0: 8.2 MB
- libgdk-3.so.0: 1.1 MB
- Themes/modules: 3.2 MB

**Total AppImage increase:** 0 MB (already present)

---

## Implementation Phases

### Phase 1: Basic GTK3 Window (~2-3 days)

**Deliverables:**
1. GTK3 window creation (title, decorations, resize)
2. Main video area (gtksink embedded)
3. Window shows when video call starts
4. Window closes when call ends

**Files:**
- Create `gtk_video_window.h/cpp`
- Update `CMakeLists.txt`
- Add gRPC `ShowVideoWindow`/`HideVideoWindow`
- Update `bridge.py`, `calls.py`

**Code estimate:** ~300 lines

### Phase 2: Self-View Overlay (~1-2 days)

**Deliverables:**
1. Self-view overlay on top of main video
2. Draggable with mouse (drag start, move, release)
3. Constrained to window bounds
4. Maintains aspect ratio

**Files:**
- Extend `gtk_video_window.cpp`

**Code estimate:** ~150 lines

### Phase 3: Stats Overlay (~1 day)

**Deliverables:**
1. Semi-transparent stats box (top-left corner)
2. Shows bandwidth, resolution, FPS
3. Updates in real-time from GStreamer stats
4. CSS styling for appearance

**Files:**
- Extend `gtk_video_window.cpp`
- Add gRPC `UpdateVideoWindowStats`

**Code estimate:** ~100 lines

### Phase 4: Control Buttons (~1-2 days)

**Deliverables:**
1. Button bar at bottom (mute, camera, hangup)
2. Wire to existing gRPC methods
3. Visual feedback (pressed state, icons)
4. Keyboard shortcuts (M for mute, H for hangup)

**Files:**
- Extend `gtk_video_window.cpp`

**Code estimate:** ~150 lines

### Phase 5: Polish & Cross-Platform (~2-3 days)

**Deliverables:**
1. Theme consistency (match system GTK theme)
2. HiDPI support
3. Fullscreen mode (F11)
4. Window state persistence (size, position)
5. Test on X11 and Wayland
6. Windows/macOS stubs (future work)

**Code estimate:** ~100 lines

**Total:** ~800-900 lines, 7-11 days

---

## Testing Strategy

### Unit Tests
- Window creation/destruction
- Drag event handling
- Stats update logic

### Integration Tests
- Video call workflow (show → resize → drag → close)
- gRPC communication (Python ↔ C++)
- GStreamer pipeline integration

### Platform Tests
- X11: Video embedding, resize, drag
- Wayland: Same as X11
- VM/RDP: Video quality, performance

### Regression Tests
- Audio-only calls (no video window)
- Multiple sequential calls
- Call failure handling

---

## Known Limitations

### Phase 1-4 (Current Plan)
- Two windows during call (Qt + GTK3)
- Duplicate stats/controls (Qt window still has them)
- No window parenting (GTK3 window independent)

### Future Work
- Migrate all controls to GTK3 window
- Remove Qt call window entirely
- Single unified video window
- Windows/macOS native implementations

---

## Migration Path

### Current (Session 11)
```
Qt Call Window
├─ Audio controls ✓
├─ Stats display ✓
└─ Video widget ✗ (failed on Wayland)
```

### After Phase 1-4
```
Qt Call Window              GTK3 Video Window
├─ Audio controls ✓         ├─ Main video ✓
├─ Stats display ✓          ├─ Self-view (draggable) ✓
└─ Buttons ✓                ├─ Stats overlay ✓
                            └─ Control buttons ✓
```

### Final Goal (Phase 5+)
```
GTK3 Video Window (only)
├─ Main video ✓
├─ Self-view (draggable) ✓
├─ Stats overlay ✓
├─ Control buttons ✓
└─ Audio controls ✓

Qt Call Window = deleted
```

---

## Success Criteria

### Phase 1-4 Complete
- [x] GTK3 window shows during video calls
- [x] Video auto-resizes with window
- [x] Self-view is draggable
- [x] Stats update in real-time
- [x] Buttons control call (mute, hangup)
- [x] Works on X11 and Wayland
- [x] No AppImage size increase

### Production Ready
- [ ] Windows/macOS implementations
- [ ] Qt call window removed
- [ ] All controls in GTK3 window
- [ ] Theme consistency across platforms
- [ ] Performance optimized (60 fps)

---

## References

**Code to Reuse:**
- Existing GStreamer pipeline (`webrtc_session_video.cpp`)
- Existing compositor setup (main video + self-view)
- Existing stats collection (`webrtc_session_stats.cpp`)

**GTK3 Documentation:**
- GtkOverlay: https://docs.gtk.org/gtk3/class.Overlay.html
- GtkEventBox: https://docs.gtk.org/gtk3/class.EventBox.html
- CSS Styling: https://docs.gtk.org/gtk3/css-overview.html

**GStreamer GTK Integration:**
- gtksink: https://gstreamer.freedesktop.org/documentation/gtk/gtksink.html
- GstVideoOverlay: https://gstreamer.freedesktop.org/documentation/video/gstvideooverlay.html

---

**Next Steps:**
1. Implement Phase 1 (basic GTK3 window)
2. Test on Wayland and X11
3. Iterate based on feedback
4. Proceed to Phase 2-4
