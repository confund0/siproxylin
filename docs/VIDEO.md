# Video Calls Implementation

**Last Updated**: 2026-03-29
**Status**: Working with Conversations and Dino

---

## Current Status

### Working Scenarios ✅

| Direction | Peer | Result |
|-----------|------|--------|
| SP → Conversations | Mobile | ✅ Works perfectly both ways |
| SP → Dino | Desktop | ✅ Works both ways (slow window start ~15s) |
| Dino → SP | Desktop | ✅ Works both ways (slow window start ~15s) |
| Conversations → SP | Mobile | ✅ Works both ways (not tested again) |

**Configuration**:
- OFFERER: bundle-policy=BALANCED (adapts to peer)
- ANSWERER: bundle-policy=MAX_COMPAT (matches peer's offer)
- Dynamic pad request order (detects m-line order from SDP)
- Dynamic video payload type (parsed from SDP offer)

### Features ✅

**Device Selection** (2026-03-29)
- Audio: Microphone and speaker selection in Settings → Calls
- Video: Camera selection in Settings → Video
- Cross-platform enumeration (V4L2/KsVideo/AVFoundation via GStreamer)
- Settings saved in `~/.config/siproxylin/calls.json`
- **Future**: Hot-swap devices during active call (post-release)

### Video Quality ✅

**Incoming Video Quality** (2026-03-29)
- RTCP feedback enabled (NACK-PLI, FIR, Transport-CC)
- Phone can request keyframes on packet loss
- Proper bandwidth adaptation via Transport-Wide Congestion Control
- Fast recovery from network issues

### Known Issues (Minor)

**1. Dino: Incoming Video Window Delay** ⏱️
- **Symptom**: GStreamer autovideosink window takes 15-18 seconds to appear (Wayland/Sway)
- **Impact**: Video works fine once window appears
- **Cause**: GStreamer PAUSED→PLAYING state transition slow on compositor
- **Workaround**: None - wait for window
- **Priority**: Low (cosmetic, does not affect functionality)

---

## Architecture

### Overview

Video calls use **GStreamer native video display** (autovideosink), not VLC.

```
Python (Signaling + GUI)         C++ (Media + WebRTC)
┌──────────────────────┐        ┌────────────────────────────┐
│  CallBarrel          │        │  WebRTCSession             │
│  (Jingle ↔ GUI)      │◄─gRPC─►│  (GStreamer webrtcbin)     │
│                      │        │                            │
│  JingleAdapter       │        │  Send: v4l2src → vp8enc    │
│  (SDP ↔ Jingle)      │        │  Receive: vp8dec → sink    │
└──────────────────────┘        └────────────────────────────┘
```

**Display**: GStreamer `autovideosink` creates native OS window (X11/Wayland/Windows/macOS)

### Why GStreamer Native (Not VLC)

**Removed**: Commit 1175758 (2026-03-28)

The old VLC/WebM path (local UDP stream to VLC) froze after the first frame.

**Why GStreamer Native Won**:
- Direct connection: webrtcbin → vp8dec → autovideosink
- Better A/V sync (no UDP hop)
- Lower latency (~200ms vs 1-2 seconds)
- Simpler architecture (fewer moving parts)
- Native GStreamer timestamps throughout

---

## Key Technical Decisions

### 1. Bundle Policy

**TL;DR**: Different peers have different bundling, need role-specific policy.

#### Dynamic Role-Based Policy
- **Implementation**:
  - **OFFERER**: `bundle-policy=BALANCED` - adapts to peer's answer
  - **ANSWERER**: `bundle-policy=MAX_COMPAT` - matches peer's offer structure
- **File**: `drunk_call_service/src/webrtc_session.cpp` (create_offer, create_answer)
- **Result**: Works with both Dino (unbundled) and Conversations (bundled)

**Key Learning**: Bundle policy must be role-specific. Static policy breaks compatibility with at least one peer type.

**DO NOT**:
- ❌ Use static bundle-policy for all calls
- ❌ Assume all XMPP clients use same bundling strategy
- ❌ Set bundle-policy in `configure_webrtcbin()` (too early, role unknown)

**DO**:
- ✅ Set bundle-policy in `create_offer()` and `create_answer()` separately
- ✅ OFFERER: Use BALANCED (adapts to peer)
- ✅ ANSWERER: Use MAX_COMPAT (matches peer)

### 2. Zero Timestamps

**Symptom**: Video appeared frozen/slideshow on remote side (Dino, Conversations)

#### Root Cause:
- **Finding**: ALL video frames had timestamp `0:00:00.000000000`
- **Impact**: RTP/WebRTC jitter buffers require timestamps to schedule playback
- **Result**: With zero timestamps, receiver couldn't determine frame order/timing → massive buffering/drops

#### The Fix ✅:
- **Solution**: v4l2src `do-timestamp=TRUE` property
- **File**: `drunk_call_service/src/webrtc_session.cpp` (setup_answerer_video_pipeline, setup_offerer_video_pipeline)
- **Result**: Timestamps now increment properly (~33ms intervals at 30fps), smooth video at 1.8-2.0 Mbps

**Key Learning**: v4l2src doesn't generate timestamps by default. MUST enable do-timestamp for RTP streaming.

**DO NOT**:
- ❌ Use autovideosrc for WebRTC (timestamp issues, bin complexity)
- ❌ Assume camera sources generate timestamps automatically
- ❌ Try to fix frame delivery issues with queue/bitrate tweaks if timestamps are broken

**DO**:
- ✅ Use v4l2src directly on Linux
- ✅ Always set `do-timestamp=TRUE`
- ✅ Verify timestamps with `GST_DEBUG=vp8enc:7` (check "src ts:" values)
- ✅ Use videotestsrc `is-live=TRUE` for testing (generates proper timestamps)

### 3. Keyframe Interval

**Old Setting**: `keyframe-max-dist=2000`
- **Problem**: 2000 frames at 30fps = **66 seconds** between keyframes
- **Impact**:
  - Video decoders REQUIRE keyframe to start decoding
  - 18-second test call had **ZERO keyframes**
  - Result: Black screen on Dino, no video start
- **File**: `drunk_call_service/src/webrtc_session.cpp` (vp8enc configuration)

**Fix**: Changed to `keyframe-max-dist=60` (keyframe every 2 seconds)

**Key Learning**: Official examples may be optimized for different use cases (recording vs live calls). Always validate settings for your use case.

**DO NOT**:
- ❌ Use keyframe-max-dist > 300 for video calls
- ❌ Blindly copy settings from examples without understanding

**DO**:
- ✅ Use keyframe interval of 1-3 seconds for calls
- ✅ Consider: keyframe-max-dist = framerate × desired_interval_seconds
- ✅ Test with short calls to ensure keyframes present

### 4. V4l2src Race Condition

**Symptom**: Pipeline crashed with "code should not be reached" at gstwebrtcbin.c:5236
- **Error**: "Internal data stream error, not-linked (-1)"
- **When**: Outgoing video calls only

**Root Cause**:
- Called `sync_state_with_parent()` BEFORE linking elements to webrtcbin
- v4l2src went to PLAYING immediately
- Started capturing BEFORE webrtcbin completed SetRemoteDescription
- Pushed frames into incomplete pipeline

**The Fix**:
- Move `sync_state_with_parent()` to AFTER all linking complete
- **File**: `drunk_call_service/src/webrtc_session.cpp` (setup_offerer_video_pipeline, setup_answerer_video_pipeline)

**Key Learning**: GStreamer state management is critical. Elements must be linked before syncing state.

**DO NOT**:
- ❌ Call sync_state_with_parent() before linking to webrtcbin
- ❌ Allow source elements to reach PLAYING before pipeline complete

**DO**:
- ✅ Link all elements first
- ✅ Sync state to parent only after linking complete
- ✅ Verify pipeline state with logging

### 5. ICE Candidate mline_index Hardcoding

**Problem**: All ICE candidates had `sdpMLineIndex: 0`
- **When**: With MAX_COMPAT (separate transports)
- **Impact**:
  - All candidates went to transport 0 (audio)
  - Video transport 1 had NO candidates
  - ICE failed for video: "No candidate pairs found"

**Why It Worked Before**:
- With MAX_BUNDLE: single ICE transport for all media
- mline_index didn't matter (only one transport exists)

**Why It Broke**:
- MAX_COMPAT creates separate transports per media
- Must route candidates to correct transport

**The Fix**:
- Map Jingle content name to SDP mline index
- **File**: `drunk_call_hook/protocol/jingle.py`
- Extract media type from content_name (e.g., "video1" → "video")
- Find index in media list: `['audio', 'video']` → audio=0, video=1

**Key Learning**: mline_index matters when using separate transports (MAX_COMPAT, NONE). Must map correctly.

**DO NOT**:
- ❌ Hardcode mline_index to 0
- ❌ Assume single transport (MAX_BUNDLE)

**DO**:
- ✅ Map content names to media types
- ✅ Use media list order to determine mline_index
- ✅ Test with both bundled and unbundled peers

### 6. Transceiver 'mid' Property

The `mid` property of a transceiver is **read-only**. Setting it fails silently. Transport mapping depends on the bundle policy (see 1).

**DO NOT**:
- ❌ Try to set 'mid' property on transceivers (read-only)
- ❌ Manually manipulate transceiver internals

**DO**:
- ✅ Let webrtcbin assign mid values automatically
- ✅ Use correct bundle-policy for your role
- ✅ Trust webrtcbin's transceiver mapping

### 7. Transceiver Order Mismatch

**Problem**: Hardcoded pad request order assumed audio-first
- **Code**: Always requested audio pad first (sink_0), video pad second (sink_1)
- **Assumption**: All peers send audio=m-line 0, video=m-line 1

**But Conversations sends**:
- m-line 0 = VIDEO (VP8)
- m-line 1 = AUDIO (OPUS)

**Result**: Transceiver mismatch
- Our audio transceiver (sink_0) → Conversations' video m-line (0) ❌
- Our video transceiver (sink_1) → Conversations' audio m-line (1) ❌
- Phone received audio on video stream, video on audio stream
- Caused one-way video (phone couldn't display our video)

**Why Dino Worked**: Dino sends audio=m-line 0, video=m-line 1 (matched old hardcoded order)

**The Fix**:
1. Parse m-line order from SDP offer in `set_remote_description()`
2. Set `video_first_mline_` flag based on which media type is at m-line 0
3. Request pads in same order as SDP m-lines in `on_offer_set_for_answer()`:
   - If video-first: Request VIDEO pad first, AUDIO pad second
   - If audio-first: Request AUDIO pad first, VIDEO pad second
4. Assign to correct variables based on media type, not request order

**Key Insight**: webrtcbin assigns transceivers sequentially
- First `request_pad_simple("sink_%u")` → sink_0 → m-line 0
- Second `request_pad_simple("sink_%u")` → sink_1 → m-line 1
- **MUST request in same order as SDP m-lines!**

**Files Modified**:
- `drunk_call_service/src/webrtc_session.h` (added `video_first_mline_`)
- `drunk_call_service/src/webrtc_session.cpp` (parse + dynamic pad ordering)

**Key Learning**: Never assume m-line order. Parse from SDP and adapt.

**DO NOT**:
- ❌ Hardcode pad request order
- ❌ Assume audio is always m-line 0
- ❌ Ignore SDP m-line ordering

**DO**:
- ✅ Parse m-line order from SDP offer
- ✅ Request pads in same order as m-lines
- ✅ Support both audio-first (Dino) and video-first (Conversations) peers

### 8. Video Payload Type Mismatch

**Problem**: Hardcoded `payload=98` in answerer video pipeline
- **Code**: `setup_answerer_video_pipeline()` used a fixed payload type 98
- **Dino's offer**: Uses PT=98 for VP8 ✓
- **Conversations' offer**: Uses PT=96 for VP8 ✗

**Result**: PAYLOAD MISMATCH with Conversations
- Our SDP answer advertised: `m=video 9 UDP/TLS/RTP/SAVPF 96` (from codec-preferences)
- But we actually sent: RTP packets with `payload=98`
- Phone received PT=98 when expecting PT=96 per SDP
- Phone couldn't decode → **BLACK SCREEN**

**Why Offerer Mode Worked**: Different code path in `setup_offerer_video_pipeline()` doesn't have this bug

**Key Clue**: "if Siproxylin calls Conversations - then video works fine in both directions" (offerer mode)
- This revealed answerer-mode specific bug

**The Fix**:
1. Added `int negotiated_video_payload_` to `webrtc_session.h`
2. Modified `parse_video_codec_from_offer()` to return payload via output parameter
3. Store parsed payload in `set_remote_description()`
4. Use negotiated payload in `setup_answerer_video_pipeline()` instead of hardcoded 98

**Result**:
- Conversations incoming: Now use PT=96 (parsed from offer) ✓
- Dino incoming: Continue using PT=98 (parsed from offer) ✓
- SDP answer and actual RTP transmission match!

**Files Modified**:
- `drunk_call_service/src/webrtc_session.h` (added `negotiated_video_payload_`)
- `drunk_call_service/src/webrtc_session.cpp` (parse + store + use payload)

**Key Learning**: Never hardcode RTP payload types. Always negotiate from SDP.

**DO NOT**:
- ❌ Hardcode RTP payload types
- ❌ Assume all peers use same payload for same codec
- ❌ Ignore payload-type from SDP offer

**DO**:
- ✅ Parse payload type from SDP offer
- ✅ Use negotiated payload in RTP capsfilter
- ✅ Verify SDP answer matches actual pipeline config

### 9. Missing RTCP Feedback Capabilities

**Problem**: No RTCP feedback capabilities in SDP answer
- **Our answer**: `<payload-type id="96" name="VP8" clockrate="90000" />` (bare minimum)
- **Conversations' offer**: Included `rtcp-fb` for NACK-PLI, FIR, Transport-CC, GOOG-REMB

**Result**: POOR INCOMING VIDEO QUALITY
- Phone couldn't request keyframes when packets lost
- Phone couldn't signal bandwidth constraints properly
- No transport-wide congestion control feedback
- **Symptom**: Periodic pixelation during longer calls
  - Video degrades to unrecognizable → Stays pixelated → Suddenly clears up → Repeats
  - Phone was adapting bitrate blindly without feedback

**Why This Happened**:
- codec-preferences caps didn't include RTCP feedback properties
- SDP answer advertised minimal capabilities
- Phone's encoder adapted overly conservatively without feedback signals

**The Fix**:
Added RTCP feedback capabilities to video codec-preferences in 3 locations:
1. `parse_video_codec_from_offer()` (answerer mode)
2. `create_offer()` offerer codec-preferences
3. Both now include:
   ```cpp
   "rtcp-fb-nack-pli", G_TYPE_BOOLEAN, TRUE,      // Picture Loss Indication
   "rtcp-fb-ccm-fir", G_TYPE_BOOLEAN, TRUE,       // Full Intra Request
   "rtcp-fb-transport-cc", G_TYPE_BOOLEAN, TRUE,  // Transport-wide CC
   ```

**Result**: SDP answer now advertises:
```xml
<payload-type id="96" name="VP8" clockrate="90000">
    <rtcp-fb type="nack" subtype="pli" />
    <rtcp-fb type="ccm" subtype="fir" />
    <rtcp-fb type="transport-cc" />
</payload-type>
```

**Benefits**:
- **NACK-PLI**: Phone requests keyframes when packets lost → Fast recovery from pixelation
- **CCM-FIR**: Full frame refresh for severe errors
- **Transport-CC**: Proper bandwidth adaptation using TWCC (Transport-Wide Congestion Control)
- **Result**: Smooth incoming video quality, fast recovery from network issues

**Files Modified**:
- `drunk_call_service/src/webrtc_session.cpp` (3 locations)

**Key Learning**: Always advertise RTCP feedback capabilities in SDP. Without them, peer flies blind on network conditions.

**DO NOT**:
- ❌ Send bare minimum codec capabilities in SDP
- ❌ Assume default RTCP feedback is enough
- ❌ Ignore rtcp-fb attributes from peer's offer

**DO**:
- ✅ Include RTCP feedback in codec-preferences caps
- ✅ Match peer's capabilities (NACK-PLI, FIR, Transport-CC)
- ✅ Enable proper congestion control feedback
- ✅ Test video quality over time, not just initial connection

---

## Pipeline Implementation

### Audio Pipeline (Reference)

**Send**:
```
pulsesrc → queue → audioconvert → audioresample → opusenc → rtpopuspay → capsfilter → webrtcbin
```
- **File**: `drunk_call_service/src/webrtc_session.cpp` (setup_offerer_audio_pipeline, setup_answerer_audio_pipeline)

**Receive**:
```
webrtcbin → rtpopusdepay → opusdec → queue → autoaudiosink
```
- **File**: `drunk_call_service/src/webrtc_session.cpp` (on_incoming_stream)

### Video Pipeline

**Send**:
```
v4l2src → videoconvert → queue → vp8enc → rtpvp8pay → queue → capsfilter → webrtcbin
```

**Key Settings**:
- **v4l2src**:
  - `do-timestamp=TRUE` (CRITICAL for timestamps)

- **vp8enc**:
  - `deadline=1` (realtime encoding, lowest latency)
  - `cpu-used=8` (max speed preset, lowest latency)
  - `target-bitrate=1500000` (1.5Mbps)
  - `keyframe-max-dist=60` (keyframe every 60 frames, ~2 seconds at 30fps)

- **rtpvp8pay**:
  - `picture-id-mode=2` (15-bit)
  - Source: Official GStreamer webrtc-sendrecv.c example
  - Reason: "Improves TWCC stats behavior and fixes stuttery video playback in Chrome"

**File**: `drunk_call_service/src/webrtc_session.cpp` (setup_answerer_video_pipeline, setup_offerer_video_pipeline)

**Receive**:
```
webrtcbin → rtpvp8depay → vp8dec → videoconvert → autovideosink
```

**Detection**:
- Inspect pad caps for `media=video` vs `media=audio`
- **File**: `drunk_call_service/src/webrtc_session.cpp` (on_incoming_stream)

**Display**:
- `autovideosink` selects best sink for platform (waylandsink, ximagesink, etc.)
- Creates native OS window automatically
- No Qt integration (separate window)

### Critical Pattern: Offerer vs Answerer

**Offerer** (Outgoing Call):
1. Set bundle-policy=BALANCED
2. Create video pipeline BEFORE create-offer
3. Request pad from webrtcbin (creates transceiver)
4. Link pipeline to webrtcbin
5. Sync state to parent AFTER linking
6. Emit create-offer signal

**Answerer** (Incoming Call):
1. Set bundle-policy=MAX_COMPAT
2. Set remote description (peer's offer)
3. Parse video codec from offer
4. Create video pipeline AFTER remote description
5. Reuse negotiated pad from webrtcbin
6. Link pipeline to webrtcbin
7. Sync state to parent AFTER linking
8. Emit create-answer signal

**Why Different**:
- Offerer creates transceivers, Answerer reuses peer's transceivers
- webrtcbin pattern documented in official examples

**DO NOT**:
- ❌ Create video pipeline in same order for both roles
- ❌ Create pipeline before setting remote description (answerer)
- ❌ Create pipeline after create-offer (offerer)

---

## File Reference

### C++ (drunk_call_service/src/)

**webrtc_session.cpp** - Main WebRTC pipeline implementation
- Audio send (offerer): `setup_offerer_audio_pipeline()`
- Audio send (answerer): `setup_answerer_audio_pipeline()`
- Video send (offerer): `setup_offerer_video_pipeline()`
- Video send (answerer): `setup_answerer_video_pipeline()`
- Audio/video receive: `on_incoming_stream()`
- Parse video codec: `parse_video_codec_from_offer()`
- Bundle-policy (offerer): `create_offer()`
- Bundle-policy (answerer): `create_answer()`

**webrtc_session.h** - Video member variables
- `video_src_`, `video_sink_`
- `negotiated_video_pad_`, `offer_video_codec_caps_`
- Method declarations

### Python (drunk_call_hook/)

**bridge.py** - gRPC client to C++ service
- VideoStreamManager usage (kept for future, currently unused)
- `create_session()` method
- Video enable_video parameter

**video_manager.py** - UDP port allocation
- **Status**: Exists but UNUSED in current GStreamer-native implementation
- **Purpose**: Was used for VLC UDP streaming (removed)
- **Kept**: For potential future Qt-embedded video or UDP streaming fallback

**protocol/jingle.py** - Jingle XML ↔ Python
- ICE candidate mline_index mapping
- Content name to media type extraction

**protocol/jingle_sdp_converter.py** - SDP ↔ Jingle conversion
- Per-media ICE credentials parsing
- SDP to Jingle conversion: `sdp_to_jingle()`
- Jingle to SDP conversion: `jingle_to_sdp()`

### Python (siproxylin/)

**core/barrels/calls.py** - Call state management
- Video call detection
- Jingle session handling

**gui/call_window.py** - Call UI window
- Video display note (handled by GStreamer)
- No video widget (autovideosink creates own window)
- Media type handling

**gui/chat_view/chat_view.py** - Chat interface
- Video call button
- Media type selection

**gui/widgets/video_widget.py** - VLC video widget
- **Status**: Exists but UNUSED in current implementation
- **Purpose**: Was used for VLC UDP streaming (removed)
- **Kept**: For potential future Qt-embedded video implementation
- Uses python-vlc for VLC integration

---

## Testing Checklist

- [x] Audio-only calls work (no regressions)
- [x] SP → Conversations video (both ways)
- [x] SP → Dino video (both ways)
- [x] Dino → SP video (both ways)
- [ ] Conversations → SP video (both ways) - not tested again
- [ ] Long call stability (30+ minutes)
- [ ] Network condition changes (WiFi → mobile)
- [ ] Multiple sequential calls without restart
- [ ] Call with poor network (packet loss simulation)
- [ ] Camera device switching
- [ ] Multiple accounts calling simultaneously

---

**Last Updated**: 2026-03-29
**Document Status**: Current Implementation Reference
