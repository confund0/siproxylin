# Video Call Implementation Plan

**Based on**: Actual code analysis (webrtc_session.cpp, bridge.py, jingle_sdp_converter.py, call_window.py)
**Date**: 2026-03-08
**Status**: Phase 1 & 2 Complete (see STATUS.md for progress)

---

## Current Architecture Reality Check

### C++ Pipeline (webrtc_session.cpp)

**Key discovery**: Audio elements added AFTER SDP negotiation, not during `create_pipeline()`

| Method | Line | What it does |
|--------|------|--------------|
| `create_pipeline()` | 755 | Creates empty pipeline + webrtcbin only |
| `setup_offerer_audio_pipeline()` | 1006 | Outgoing: pulsesrc → opusenc → rtpopuspay → webrtcbin |
| `setup_answerer_audio_pipeline()` | 826 | Incoming: same chain, reuses negotiated pad |
| `on_incoming_stream()` | 1889 | Receive: webrtcbin → rtpopusdepay → opusdec → pulsesink |
| `parse_audio_codec_from_offer()` | 41 | Extracts OPUS caps from SDP for codec preferences |

**Pattern**: Offerer vs Answerer have separate setup methods, both called AFTER SDP negotiation.

### Python Bridge (bridge.py)

| Method | Line | Returns |
|--------|------|---------|
| `create_session()` | 403 | bool |
| `create_offer()` | 510 | SDP string |
| `create_answer()` | 534 | SDP string (triggers `setup_answerer_audio_pipeline()` in C++) |

**Service**: C++ (not Go), localhost:50051

### Jingle/SDP (jingle_sdp_converter.py)

| Method | Line | Status |
|--------|------|--------|
| `sdp_to_jingle()` | 40 | Handles multiple media types generically |
| `jingle_to_sdp()` | 309 | **BUT** codec parsing hardcoded to OPUS (line 396-462) |

**Problem**: Works for any `m=audio` or `m=video` line, but can't parse VP8/H264 codecs.

### GUI (call_window.py)

Line 39: `__init__(self, parent, account_id, session_id, peer_jid, media_types, direction)`
- `media_types` already supports `['audio', 'video']`
- Just need to add video display widget

---

## Implementation Steps

### Step 1: C++ Video Pipeline ✅ COMPLETE

**File**: `drunk_call_service/src/webrtc_session.cpp`

**Status**: Fully implemented and tested (audio regression test passed)

#### 1.1 Add Video Codec Parsing ✅

**Location**: webrtc_session.cpp:135

**Implemented**: `parse_video_codec_from_offer()` function
- Finds m=video line in SDP
- Parses rtpmap for VP8/VP9/H264
- Returns caps for codec-preferences
- Returns nullptr for audio-only calls (graceful)

#### 1.2 Add Video Pipeline Methods ✅

**Implemented**:
- `setup_offerer_video_pipeline()` - webrtc_session.cpp:1211
  - Pipeline: v4l2src/autovideosrc → queue → videoconvert → videoscale → vp8enc → rtpvp8pay → capsfilter → webrtcbin
  - Called in `create_offer()` BEFORE create-offer signal (line 482)
  - Requests new sink pad, creates transceiver
  - Sets codec-preferences to VP8/90000

- `setup_answerer_video_pipeline()` - webrtc_session.cpp:1082
  - Same pipeline as offerer
  - Reuses `negotiated_video_pad_` from `on_offer_set_for_answer()`
  - Called in `on_answer_created()` AFTER answer created (line 2029)

#### 1.3 Extend `on_incoming_stream()` ✅

**Location**: webrtc_session.cpp:2339

**Implemented**: Media type detection by encoding-name
- Gets caps from incoming pad
- Extracts encoding-name from caps structure
- Routes to audio chain if OPUS
- Routes to video chain if VP8/VP9/H264
- Video chain: rtpvp8depay/rtpvp9depay/rtph264depay → decoder → videoconvert → jpegenc → multipartmux → udpsink
- UDP sink configured with `config_.video_udp_host` and `config_.video_udp_port`
- `sync=TRUE` for A/V sync

#### 1.4 Add SessionConfig Fields ✅

**File**: `src/media_session.h:162`

**Implemented**:
```cpp
// Video devices
std::string camera_device;      // Empty = default camera
bool enable_video_receive;      // Enable video receive pipeline
std::string video_udp_host;     // UDP host for video streaming
int video_udp_port;             // UDP port for video streaming
```

**Testing**: ✅ Audio regression test passed (no regressions introduced)

---

### Step 2: Protobuf + Python Bridge ✅ COMPLETE

#### 2.1 Update Protobuf ✅

**File**: `drunk_call_service/proto/call.proto:76-79`

**Implemented**:
```protobuf
bool enable_video_receive = 19;
string video_udp_host = 20;
int32 video_udp_port = 21;
```

**Bindings**: ✅ C++ and Python protobuf bindings regenerated via Makefile

#### 2.2 Add VideoStreamManager ✅

**File**: `drunk_call_hook/video_manager.py` (NEW - 85 lines)

**Implemented**:
- `allocate_video_port()`: Allocates UDP port on 127.127.69.69
- `release_video_port()`: Closes socket
- `get_video_url()`: Returns VLC-compatible URL `udp://@127.127.69.69:port`
- Socket kept alive for port reservation
- Auto-cleanup on destruction

#### 2.3 Extend CallBridge ✅

**File**: `drunk_call_hook/bridge.py` (~40 lines modified)

**Implemented**:
- Added `enable_video: bool = False` parameter to `create_session()`
- Returns `tuple[bool, Optional[int]]` - (success, video_port)
- Allocates VideoStreamManager on enable_video=True
- Stores manager in `_video_managers` dict keyed by session_id
- Passes config to C++ via protobuf
- Added `get_video_url(session_id)` method
- Auto-cleanup on `end_session()` and on session creation failure

**Testing**: Not yet tested (needs GUI integration)

---

### Step 3: Jingle Video Support 🚧 PENDING

#### 3.1 Fix Codec Parsing

**File**: `drunk_call_hook/protocol/jingle_sdp_converter.py`

Line 396-462, extend `jingle_to_sdp()`:

```python
# Currently handles audio only
# Add video codec parsing:
if media_type == 'video':
    for payload in description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type'):
        codec_name = payload.get('name')  # VP8, VP9, H264
        codec_id = payload.get('id')
        clock_rate = payload.get('clockrate', '90000')

        sdp_lines.append(f"a=rtpmap:{codec_id} {codec_name}/{clock_rate}")

        # Parse fmtp parameters (max-fr, max-fs for VP8)
        for param in payload.findall('{urn:xmpp:jingle:apps:rtp:1}parameter'):
            # Build a=fmtp line
```

#### 3.2 Detect Video in Incoming Calls

**File**: `siproxylin/core/barrels/calls.py`

Line 581 (`_on_jingle_incoming_call`), check media types:

```python
def _on_jingle_incoming_call(self, session_id, peer_jid, sdp_offer, media):
    has_video = 'video' in media  # media = ['audio'] or ['audio', 'video']

    # Pass to create_session
    success, video_port = await self.call_bridge.create_session(
        peer_jid=peer_jid,
        session_id=session_id,
        enable_video=has_video,
        # ... other params ...
    )
```

**Test**: Conversations.im video call detected, Python creates session with video enabled

---

### Step 4: GUI Video Display 🚧 PENDING

#### 4.1 VLC Video Widget

**File**: `siproxylin/gui/widgets/video_widget.py` (new)

```python
import vlc
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout

class VLCVideoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__()
        self.vlc_instance = vlc.Instance(["--network-caching=150"])
        self.player = self.vlc_instance.media_player_new()

        self.video_frame = QFrame()
        self.player.set_xwindow(self.video_frame.winId())  # Linux

    def play_stream(self, udp_url: str):
        media = self.vlc_instance.media_new(udp_url)
        self.player.set_media(media)
        self.player.play()
```

#### 4.2 Integrate into CallWindow

**File**: `siproxylin/gui/call_window.py`

Line 39: `media_types` already passed in, just use it:

```python
def __init__(self, parent, account_id, session_id, peer_jid, media_types, direction):
    # ... existing init ...

    self.has_video = 'video' in media_types
    self.video_widget = None
    self.video_port = None

    if self.has_video:
        self._setup_video_ui()

def _setup_video_ui(self):
    self.video_widget = VLCVideoWidget(self)
    self.main_layout.addWidget(self.video_widget)  # Add to existing layout

def start_video_playback(self, video_port: int):
    if self.video_widget:
        url = f"udp://@127.127.69.69:{video_port}"
        self.video_widget.play_stream(url)
```

#### 4.3 Pass Video Port to CallWindow

**File**: `siproxylin/gui/main_window.py`

When creating CallWindow, pass video port from CallBridge:

```python
# After create_session returns (success, video_port):
call_window = CallWindow(
    parent=self,
    account_id=account_id,
    session_id=session_id,
    peer_jid=peer_jid,
    media_types=media_types,  # Already correct
    direction=direction
)

if video_port:
    # Start playback after short delay (ICE needs to connect)
    QTimer.singleShot(2000, lambda: call_window.start_video_playback(video_port))
```

**Test**: Video displays in call window

---

## Testing Checklist

- [x] **Audio regression test**: PASSED - Both directions work, no regressions
- [ ] C++ loopback: Two sessions exchange video
- [ ] Python allocates port, C++ creates UDP sink
- [ ] Jingle video parsing works (test with Conversations.im XML)
- [ ] CallWindow shows video widget
- [ ] VLC plays UDP stream
- [ ] A/V sync within 100ms (metronome test)
- [ ] Long call stable (30 min)
- [ ] Conversations.im interop
- [ ] Dino interop

---

## File Changes Summary

| File | Status | Lines |
|------|--------|-------|
| `webrtc_session.cpp` | ✅ Complete | ~300 new |
| `webrtc_session.h` | ✅ Complete | ~10 new |
| `media_session.h` | ✅ Complete | ~5 new |
| `call.proto` | ✅ Complete | 3 new |
| `bridge.py` | ✅ Complete | ~40 modified |
| `video_manager.py` | ✅ Complete (NEW) | 85 new |
| `jingle_sdp_converter.py` | 🚧 Pending | ~50 to modify |
| `calls.py` | 🚧 Pending | ~10 to modify |
| `video_widget.py` | 🚧 Pending (NEW) | ~50 to create |
| `call_window.py` | 🚧 Pending | ~30 to modify |

**Completed**: ~443 lines new/modified
**Remaining**: ~140 lines to implement

---

## Critical Lessons Learned

1. ✅ **Answerer vs Offerer pattern**: Separate video pipeline methods implemented
   - Offerer: Pipeline BEFORE create-offer (creates transceiver)
   - Answerer: Pipeline AFTER create-answer (reuses transceiver)
   - This pattern is CRITICAL and must never be violated

2. ✅ **Pad timing**: Video elements added at correct times
   - Offerer: `setup_offerer_video_pipeline()` → create-offer → set-local-description
   - Answerer: set-remote-description → create-answer → `setup_answerer_video_pipeline()` → set-local-description

3. ✅ **Codec preferences**: Video codec parsing implemented
   - `parse_video_codec_from_offer()` extracts VP8/VP9/H264 from SDP
   - Sets codec-preferences on transceiver BEFORE SDP negotiation

4. 🚧 **Jingle converter**: Still needs VP8/VP9/H264 parsing (Phase 3)

5. ✅ **UDP streaming**: Video receive uses UDP sink to Python
   - Avoids GUI threading issues with direct video sink
   - VLC consumes multipart JPEG stream
   - Port allocated by Python, passed to C++

6. ✅ **Backwards compatibility**: Video is opt-in
   - Default `enable_video=False`
   - Audio-only calls completely unaffected
   - Regression test passed

---

**Last Updated**: 2026-03-08
**Status**: Phase 1 & 2 Complete, Phase 3 Pending
**See**: STATUS.md for detailed progress tracking
