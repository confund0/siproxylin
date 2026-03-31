# Native Call Window Implementation Plan v2 - Wayland PoC

**Date:** 2026-03-31
**Branch:** video
**Status:** Implementation
**Previous Plan:** NATIVE-CALL-WINDOW-PLAN.md (deprecated - C++ GTK approach failed)

---

## Problem Statement

**C++ GTK3 approach failed after 18 hours:**
- GTK requires all operations on GLib main thread
- C++ service runs on gRPC thread pool → constant thread crossing
- Complex lifecycle: async callbacks, signals, use-after-free bugs
- Multiple crashes, race conditions, threading nightmares

**Root cause:** Tight coupling between C++ object lifecycle (managed by gRPC threads) and GTK widgets (managed by GLib thread).

---

## Solution: Python Manages Windows, C++ Renders Video

**Key insight:** Python already runs GTK main loop. Let Python manage window lifecycle, C++ just renders video into the window via native handle.

```
Python GUI (siproxylin)
  ├─ Creates GTK3 video window
  ├─ Extracts native Wayland surface handle (wl_surface*)
  └─ Passes handle to C++ via gRPC CreateSessionRequest
       ↓ gRPC (window handle)
C++ drunk_call_service
  ├─ WebRTC session + GStreamer pipeline
  ├─ Creates waylandsink (Wayland) instead of autovideosink
  └─ Calls gst_video_overlay_set_window_handle() to embed video
```

**Benefits:**
- ✅ Python manages window lifecycle (simple, no threading issues)
- ✅ C++ just renders video into provided window handle
- ✅ Clean separation of concerns
- ✅ Standard GStreamer overlay approach

**PoC Scope:** Wayland only (90% of Linux desktops)
**Future:** X11 support via xvimagesink + XID

---

## Implementation Steps

### Phase 1: Python GTK Window Creation

**Goal:** Create GTK3 window and extract Wayland surface handle

**File:** `siproxylin/gui/call_window_gtk.py`

**Implementation:**
1. Create GTK3 window with basic layout:
   ```python
   import gi
   gi.require_version('Gtk', '3.0')
   gi.require_version('GdkWayland', '3.0')
   from gi.repository import Gtk, Gdk, GdkWayland
   import ctypes

   class CallWindowGtk:
       def __init__(self, session_id, peer_jid):
           self.window = Gtk.Window(title=f"Video Call - {peer_jid}")
           self.window.set_default_size(640, 480)
           self.window.show_all()
   ```

2. Extract Wayland surface pointer using ctypes:
   ```python
   def get_wayland_surface_pointer(self):
       gdk_window = self.window.get_window()
       # Use ctypes to call gdk_wayland_window_get_wl_surface()
       libgdk = ctypes.CDLL('libgdk-3.so.0')
       get_wl_surface = libgdk.gdk_wayland_window_get_wl_surface
       get_wl_surface.restype = ctypes.c_void_p
       get_wl_surface.argtypes = [ctypes.c_void_p]

       wl_surface_ptr = get_wl_surface(hash(gdk_window))
       return wl_surface_ptr
   ```

3. Return pointer as uint64 for gRPC

**X11 Future Support:**
```python
# Check display type
if GdkWayland.WaylandDisplay.isinstance(display):
    # Wayland path (above)
else:
    # X11 path
    from gi.repository import GdkX11
    xid = GdkX11.X11Window.get_xid(gdk_window)
    return xid
```

---

### Phase 2: gRPC Protocol Extension

**Goal:** Pass window handle from Python → C++

**File:** `drunk_call_service/proto/call.proto`

**Changes:**
```protobuf
message CreateSessionRequest {
  string session_id = 1;
  string peer_jid = 2;
  string microphone_device = 3;
  string speakers_device = 4;
  string camera_device = 22;

  // ... existing proxy/TURN fields ...

  // NEW: Wayland surface pointer for video embedding
  uint64 wayland_surface_ptr = 100;  // wl_surface* from Python GTK, 0 = no window handle (audio-only or autovideosink fallback)

  // Future: X11 support
  // uint64 x11_xid = 101;           // X11 Window ID (XID) from gdk_x11_window_get_xid()
}
```

**Regenerate stubs:**
- Python: `python3 -m grpc_tools.protoc --proto_path=drunk_call_service/proto --python_out=drunk_call_hook --grpc_python_out=drunk_call_hook drunk_call_service/proto/call.proto`
- C++: Automatic during cmake/make build

---

### Phase 3: C++ Video Sink Modification

**Goal:** Use waylandsink with window handle instead of autovideosink

**Files to modify:**
1. `drunk_call_service/src/media_session.h` - Add wayland_surface_ptr to SessionConfig
2. `drunk_call_service/src/webrtc_session_video.cpp` - Replace autovideosink with waylandsink
3. `drunk_call_service/src/call_service_impl.cpp` - Populate config from gRPC request

**SessionConfig changes:**
```cpp
struct SessionConfig {
    // ... existing fields ...
    uint64_t wayland_surface_ptr;  // NEW: Wayland surface pointer for video embedding (0 = not provided)
};
```

**Video sink changes (webrtc_session_video.cpp):**
```cpp
// Replace autovideosink with waylandsink
GstElement* video_sink;

if (config_.wayland_surface_ptr != 0) {
    // Embedded mode: Use waylandsink with window handle
    video_sink = gst_element_factory_make("waylandsink", "video_sink");
    if (!video_sink) {
        LOG_ERROR("[WebRTCSession] Failed to create waylandsink");
        return false;
    }

    // Set window handle for embedding
    gst_video_overlay_set_window_handle(
        GST_VIDEO_OVERLAY(video_sink),
        config_.wayland_surface_ptr
    );

    LOG_INFO("[WebRTCSession] Using waylandsink with embedded window handle");

    // TODO: X11 support
    // if (config_.x11_xid != 0) {
    //     video_sink = gst_element_factory_make("xvimagesink", "video_sink");
    //     gst_video_overlay_set_window_handle(GST_VIDEO_OVERLAY(video_sink), config_.x11_xid);
    // }
} else {
    // Fallback mode: Use autovideosink (separate window)
    video_sink = gst_element_factory_make("autovideosink", "video_sink");
    LOG_INFO("[WebRTCSession] Using autovideosink (no window handle provided)");
}
```

**Populate config (call_service_impl.cpp):**
```cpp
grpc::Status CallServiceImpl::CreateSession(...) {
    SessionConfig config;
    // ... existing field population ...
    config.wayland_surface_ptr = request->wayland_surface_ptr();

    // ... rest of method ...
}

```

**Note:** Keep compositor logic unchanged - C++ still handles PiP self-view overlay internally.

---

### Phase 4: Integration & Wiring

**Goal:** Connect Python GTK window with C++ video pipeline

**File:** `siproxylin/core/barrels/calls.py`

**Changes:**
```python
from siproxylin.gui.call_window_gtk import CallWindowGtk
from siproxylin.gui.call_window import CallWindow  # Qt controls

class CallBarrel:
    def _on_call_started(self, session_id, peer_jid, media_types):
        # Create TWO windows temporarily (PoC only)

        # 1. Qt window for controls (existing)
        qt_window = CallWindow(
            parent=self.main_window,
            account_id=self.account_id,
            session_id=session_id,
            peer_jid=peer_jid,
            media_types=media_types,
            direction='outgoing'
        )

        # 2. GTK window for video (NEW - only if video call)
        gtk_window = None
        wayland_surface_ptr = 0

        if 'video' in media_types:
            gtk_window = CallWindowGtk(session_id, peer_jid)
            wayland_surface_ptr = gtk_window.get_wayland_surface_pointer()

        # Pass wayland_surface_ptr to CreateSession RPC
        response = self.call_bridge.create_session(
            session_id=session_id,
            peer_jid=peer_jid,
            # ... existing params ...
            wayland_surface_ptr=wayland_surface_ptr  # NEW
        )
```

---

## Testing Checklist

### Unit Tests
- [ ] Python GTK window creates successfully
- [ ] Wayland surface pointer extraction works (non-zero value)
- [ ] gRPC CreateSessionRequest accepts wayland_surface_ptr field
- [ ] C++ waylandsink creates successfully
- [ ] gst_video_overlay_set_window_handle() doesn't error

### Integration Tests
- [ ] **Outgoing video call (OFFERER):** Video renders inside GTK window (not separate)
- [ ] **Incoming video call (ANSWERER):** Video renders inside GTK window (not separate)
- [ ] **Self-view PiP:** Visible in corner of GTK window
- [ ] **Window resize:** Video scales properly with window
- [ ] **Qt controls:** Hangup, mute, stats still work from Qt window
- [ ] **Window close:** Closing GTK window doesn't crash (graceful cleanup)

### Compatibility Tests
- [ ] SP → Conversations: Video both ways
- [ ] SP → Dino: Video both ways
- [ ] Conversations → SP: Video both ways
- [ ] Dino → SP: Video both ways

### Regression Tests
- [ ] Audio-only calls work (no GTK window, Qt only)
- [ ] Stats update correctly in Qt window
- [ ] Call duration timer works
- [ ] Multiple sequential calls (no memory leaks)

---

## Known Limitations (PoC)

### Current PoC
- **Two windows:** Qt window (controls) + GTK window (video) - confusing UX
- **Wayland only:** No X11 support yet
- **No controls in GTK:** All buttons in Qt window
- **Window management:** User must manage two separate windows

### Future Work (Post-PoC)
- **Single GTK window:** Migrate all controls from Qt → GTK, remove Qt window
- **X11 support:** Add xvimagesink + XID extraction
- **Windows/macOS:** Win32 HWND and Cocoa NSView implementations
- **Draggable self-view:** Make PiP movable within GTK window
- **Stats overlay:** Show bandwidth/codec info on video
- **Resolution control:** Dynamic resolution adjustment

---

## Success Criteria

### PoC Complete ✅
- [ ] GTK window appears when video call starts
- [ ] Video renders **inside** GTK window (embedded, not separate)
- [ ] Self-view PiP visible in corner
- [ ] Window resizes properly
- [ ] Both OFFERER and ANSWERER modes work
- [ ] No crashes, no segfaults
- [ ] Works with Conversations and Dino

### Production Ready (Future)
- [ ] Single unified window (GTK only, Qt removed)
- [ ] All controls in GTK window
- [ ] X11, Windows, macOS support
- [ ] Professional UI with stats overlay
- [ ] Draggable/resizable self-view

---

## Why This Approach Works

**Separation of concerns:**
- Python: Window creation, lifecycle management
- C++: WebRTC, GStreamer pipeline, video rendering

**No threading issues:**
- Python GTK runs in Python's main thread
- C++ receives window handle once (at CreateSession)
- No cross-thread GTK widget access

**Standard approach:**
- Uses GStreamer's `GstVideoOverlay` interface (documented, stable)
- Same pattern as VLC, mpv, totem players
- Platform-native rendering (Wayland, X11, Win32, Cocoa)

**Clean separation:**
- Python doesn't touch GStreamer elements
- C++ doesn't touch GTK widgets
- Only data passed: uint64 window handle

---

## Lessons Learned from v1

**What failed (v1 C++ GTK approach):**
- C++ owning GTK widgets → threading nightmare
- Async callbacks from destructor → use-after-free
- GTK signals crossing thread boundaries → crashes
- 18 hours wasted fighting gRPC vs GLib thread conflicts

**What works (v2 Window Handle approach):**
- Let Python manage GTK window lifecycle
- C++ only renders into provided window handle
- Standard GStreamer overlay mechanism
- No widget pointers crossing process boundaries

**Key insight:**
- Window handle (uint64) is just data - safe to pass across processes
- GtkWidget* pointers only valid within same process - v2 plan was flawed
- This v2 revision uses correct cross-process approach

---

## Implementation Status

**Completed:**
- [x] Documentation updated with PoC plan
- [x] Python GTK window creation
- [x] gRPC protocol extension
- [x] C++ waylandsink integration
- [x] Python-C++ wiring
- [ ] Testing and validation

**Current:** User testing Wayland implementation

---

## Implementation Journal - The Actual Journey

### Dead-End #1: The ctypes Pointer Confusion (2026-03-31)

**The Blunder:**
- Used ctypes to call `gdk_wayland_window_get_wl_surface()` from Python
- THOUGHT we were getting wl_surface* pointer back
- ACTUALLY were getting the GdkWindow* pointer echoed back unchanged
- The ctypes function call wasn't working correctly - returned input == output

**The Symptom:**
- AddressSanitizer SEGV when waylandsink tried to use the "surface" pointer
- Crash address exactly matched our "extracted surface pointer"
- Proof: We were passing GdkWindow* to waylandsink instead of wl_surface*

**Time Wasted:**
- Hours debugging ctypes mechanics, pointer conversions, logging
- All while missing the REAL problem (see below)

---

### The Missing Piece: Display Handle Discovery (2026-03-31)

**The Breakthrough:**
Web research on official GStreamer examples (`gst-plugins-bad/tests/examples/waylandsink/main.c`) revealed:

**waylandsink requires TWO handles, not one:**
1. `wl_display*` - Wayland display connection (WE NEVER PROVIDED THIS!)
2. `wl_surface*` - Window surface for rendering

**How it works:**
- **Display handle:** Set via sync bus handler responding to NEED_CONTEXT message
- **Surface handle:** Set via async prepare-window-handle message
- **Both required:** waylandsink can't initialize with only one

**Our error:**
- Only tried to provide surface handle
- Never provided display handle
- waylandsink failed silently because display context was missing

---

### The Solution: Dual-Handle Approach (2026-03-31)

**Architecture change from original plan:**
- Plan said: Pass single wl_surface* pointer
- Reality: Must pass BOTH wl_display* AND wl_surface* pointers

**Implementation approach:**

**Python side:**
- Changed method from `get_wayland_surface_pointer()` → `get_wayland_handles()`
- Returns tuple: `(wl_display*, wl_surface*)`
- Uses ctypes to call BOTH GDK functions:
  - `gdk_wayland_display_get_wl_display()`
  - `gdk_wayland_window_get_wl_surface()`
- Both extracted from GTK window before passing to gRPC

**gRPC protocol:**
- Added `wayland_display_ptr` field (field 100)
- Renumbered `wayland_surface_ptr` to field 101
- Both uint64 values passed in CreateSessionRequest

**C++ side:**
- Added `wayland_display_ptr` to SessionConfig struct
- Implemented sync bus handler to provide display context
- Sync handler intercepts GST_MESSAGE_NEED_CONTEXT
- Creates GstContext with "GstWaylandDisplayHandleContextType"
- Async handler still provides surface via prepare-window-handle
- Sync handler registered BEFORE async handler on bus

**Pattern matches official GStreamer example exactly**

---

### Files Modified (Actual Implementation)

**Python:**
- `siproxylin/gui/call_window_gtk.py` - Dual handle extraction via ctypes
- `siproxylin/core/barrels/calls.py` - Updated 3 call sites (outgoing/incoming/legacy)
- `drunk_call_hook/bridge.py` - Added wayland_display_ptr parameter

**gRPC:**
- `drunk_call_service/proto/call.proto` - Added display pointer field

**C++:**
- `drunk_call_service/src/media_session.h` - Added display ptr to SessionConfig
- `drunk_call_service/src/call_service_impl.cpp` - Populate both handles from gRPC
- `drunk_call_service/src/webrtc_session.h` - Added sync handler declarations
- `drunk_call_service/src/webrtc_session.cpp` - Implemented sync handler + registration

---

### Known Issues & TODOs

**🚨 CRITICAL - Missing Platform Guards:**

**Problem:** Code will NOT compile on Windows/macOS

**C++ issues:**
- Wayland-specific context type string in sync handler (Linux-only)
- No `#ifdef __linux__` guards around Wayland code
- Will fail at compile time on other platforms

**Python issues:**
- Tries to load `libgdk-3.so.0` (Linux library name)
- Will crash on import on Windows/macOS
- Needs `platform.system()` check before ctypes.CDLL()

**gRPC fields:** ✅ OK - uint64 can be 0 on any platform

**Required fixes:**
1. Wrap C++ Wayland code in `#ifdef __linux__` blocks
2. Add Python platform check before loading Linux-specific libraries
3. Graceful fallback to autovideosink when handles not available
4. Document cross-platform approach for future work

**Future platform support:**
- **Windows:** HWND extraction + d3dvideosink/glimagesink
- **macOS:** NSView* extraction + osxvideosink/glimagesink
- **X11:** XID extraction + xvimagesink

---

### Testing Status

**Test results - COMPLETE FAILURE (2026-03-31):**
- ❌ Video does NOT render in embedded GTK window
- ✅ Both display and surface handles extracted successfully
- ❌ Service crashes with SEGV when waylandsink tries to use handles
- ❌ Does NOT work in OFFERER mode
- ❌ Does NOT work in ANSWERER mode

**Crash details:**
- Segfault at wl_surface* address
- Happens when waylandsink tries to create wl_buffer "from our display"
- AddressSanitizer confirms: accessing invalid memory at extracted surface pointer

---

### The Fatal Flaw: Cross-Process Wayland Pointers (2026-03-31)

**THE FUNDAMENTAL ARCHITECTURAL MISTAKE:**

Wayland handles (`wl_display*`, `wl_surface*`) are **process-specific** and **cannot be shared between processes**.

**Why it fails:**
- Python process has its own Wayland connection → creates wl_display* + wl_surface*
- C++ process has its own separate Wayland connection → different display/compositor connection
- Passing raw Wayland pointers from Python → C++ is **meaningless**
- C++ waylandsink tries to use Python's surface pointer → **SEGV** (invalid memory in C++ process space)

**Why the official example works:**
- GStreamer example has GTK + GStreamer in **THE SAME PROCESS**
- Both share the same Wayland connection
- Handles are valid within same process space

**Our architecture:**
```
Python Process (Wayland connection A)
  └─ GTK window → wl_surface* (valid in process A only)
       ↓ gRPC (passes pointer VALUE)
C++ Process (Wayland connection B)
  └─ GStreamer waylandsink → tries to use wl_surface* from connection A
     → SEGFAULT (pointer invalid in process B)
```

**This is NOT a waylandsink bug. This is a fundamental misunderstanding of Wayland architecture.**

---

### Lessons Learned (The Hard Way)

**What we learned about Wayland:**
- ✅ Wayland handles ARE process-specific (like file descriptors, not like XIDs)
- ✅ Wayland doesn't have XEmbed equivalent for easy cross-process window embedding
- ✅ Official examples assume same-process architecture
- ✅ ctypes CAN extract native pointers - but passing them cross-process doesn't work

**What didn't work:**
- ❌ Assuming Wayland handles work like X11 XIDs (cross-process integers)
- ❌ Not understanding process boundaries for native handles
- ❌ Believing "it's just a pointer, we can pass it"
- ❌ Focusing on HOW to extract handles instead of WHETHER they're usable cross-process

**The brutal truth:**
- We spent hours perfecting pointer extraction via ctypes
- We implemented dual-handle approach exactly per GStreamer docs
- We got valid pointers successfully
- **ALL OF IT WAS ARCHITECTURALLY IMPOSSIBLE FROM THE START**

**Key insight:**
- **Process boundaries matter:** Native OS handles (Wayland, file descriptors, etc.) are process-local
- **X11 was special:** XIDs worked cross-process (legacy design), Wayland doesn't do this
- **Same-process is king:** Official examples work because GTK + GStreamer share process space

---

### Next Step: UDP + gtksink in Python (v3 approach)

**Why this might work:**

We ALREADY have UDP video streaming working (C++ → Python). The missing piece was embedding the player in GTK.

**New architecture:**
```
C++ Process (drunk_call_service)
  └─ GStreamer WebRTC pipeline
  └─ udpsink → localhost:PORT (already working!)
       ↓ UDP packets (network, not pointers!)
Python Process (siproxylin)
  └─ GStreamer pipeline: udpsrc ! decoder ! gtksink
  └─ gtksink.props.widget → GtkWidget (same process!)
  └─ Embed widget in GTK window
```

**Why this solves the problem:**
- ✅ No cross-process native handles
- ✅ UDP is process-agnostic (network layer)
- ✅ Python GStreamer + gtksink in SAME process (handles are valid!)
- ✅ We already have UDP streaming code
- ✅ gtksink was RECOMMENDED approach from web research (we ignored it)

**Previous attempt challenges:**
- VLC embedding in Qt6 didn't work
- Sync issues between C++ pipeline and Python player

**Why it might work now:**
- Using GTK window (not Qt6) - better GStreamer integration
- Using GStreamer gtksink (not VLC) - same toolkit, designed for embedding
- Understanding synchronization better now

**TODO before implementing v3:**
- Review previous UDP + player sync issues
- Check if GStreamer Python bindings support gtksink properly
- Verify UDP latency is acceptable for real-time video

---

**Last Updated:** 2026-03-31
**Status:** ❌ Wayland pointer approach FAILED (architectural impossibility)
**Next:** Evaluate UDP + gtksink approach (v3)
