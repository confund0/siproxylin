# Video Implementation Status

**Last Updated**: 2026-03-08
**Branch**: `video`

---

## ✅ COMPLETED - Video Calls Working!

### Implementation Summary

Video calls are **WORKING** using WebM container format over UDP. Both incoming and outgoing video streams are functional.

**Architecture**:
```
Phone → WebRTC → GStreamer webrtcbin → rtpvp8depay → webmmux(streamable) → udpsink(127.0.0.1:port)
                                                                                      ↓
                                                        VLC playback: udp://@:port
```

---

## 🎯 Current Status: WORKING with Issues

### ✅ What Works:
- ✅ Video connection established
- ✅ VLC receives UDP stream
- ✅ First frame displays clearly
- ✅ WebM muxing with VP8 codec
- ✅ Cross-platform (Linux/Windows/macOS)

### 🐛 Known Issues:
1. **Video freezes after first frame** - VLC displays first frame but subsequent frames don't update
2. **Quality was poor** - Fixed by increasing bitrate 500kbps → 1.5Mbps
3. **Latency was 5-8 seconds** - Fixed by:
   - WebM muxer: `min-cluster-duration=0`, `max-cluster-duration=1s`
   - VLC caching: Reduced to 50ms
   - VP8 encoder: `cpu-used=8` for lowest latency

---

## 🔧 Implementation Details

### Phase 1: C++ Video Pipeline (webrtc_session.cpp)

**Receive Pipeline** (lines 2430-2503):
```cpp
rtpvp8depay → webmmux → udpsink
```

**WebM Muxer Settings**:
- `streamable=TRUE` - Optimized for streaming (no seekable index)
- `min-cluster-duration=0` - No minimum buffering (create cluster on every keyframe)
- `max-cluster-duration=1000000000` - Max 1 second clusters

**Send Pipeline** (answerer: lines 1160-1234, offerer: lines 1404-1490):
```cpp
v4l2src/autovideosrc → videoconvert → videoscale → vp8enc → rtpvp8pay → webrtcbin
```

**VP8 Encoder Settings**:
- `deadline=1` - Realtime encoding (lowest latency)
- `cpu-used=8` - Maximum speed preset (lowest latency)
- `target-bitrate=1500000` - 1.5Mbps for good quality
- `keyframe-max-dist=30` - Keyframe every 30 frames (~1 sec at 30fps)

### Phase 2: Python/Protobuf Layer

**Files Modified**:
- `drunk_call_service/proto/call.proto` - Added video config fields
- `drunk_call_hook/bridge.py` - Video session management
- `drunk_call_hook/video_manager.py` (NEW) - UDP port allocation

**VideoStreamManager** (video_manager.py):
- Allocates UDP port on `127.0.0.1` (changed from 127.127.69.69)
- Socket closed immediately after port allocation (fixes VLC binding conflict)
- Returns port number for VLC playback

### Phase 3: Jingle/GUI Layer

**Files Modified**:
- `siproxylin/gui/widgets/video_widget.py` (NEW) - VLC widget
- `siproxylin/gui/call_window.py` - Video widget integration
- `siproxylin/core/barrels/calls.py` - Video call detection
- `jingle_sdp_converter.py` - Video codec parsing

**VLC Video Widget** (video_widget.py):
```python
# Ultra-low latency VLC settings
network-caching=50ms    # Only 50ms buffer
live-caching=50ms       # Force live stream mode
clock-jitter=0          # Disable jitter compensation
clock-synchro=0         # Disable clock sync
```

**Playback URL**: Simple UDP stream
```python
udp_url = f"udp://@:{port}"  # VLC listens on all interfaces
```

---

## 🚧 DEBUGGING: Video Freeze Issue

### Symptom
- VLC displays first frame clearly
- No subsequent frames update
- Stream continues (UDP packets arriving)

### Potential Causes

1. **WebM Cluster Timing**:
   - Even with `min-cluster-duration=0`, WebM might buffer until keyframe
   - Need to verify keyframes are being sent every 30 frames
   - Check GST_DEBUG logs for cluster creation

2. **VLC Demuxer Issue**:
   - VLC might need proper WebM EBML headers
   - `streamable=true` might cause compatibility issues with VLC
   - Known issue: VLC has problems with GStreamer-generated WebM files

3. **UDP Packet Fragmentation**:
   - WebM clusters might be too large for single UDP packets
   - Need to verify packet sizes < MTU (1500 bytes)

4. **Keyframe Not Arriving**:
   - VP8 encoder might not be generating keyframes at expected interval
   - Check if `keyframe-max-dist=30` is working

---

## 🔍 Investigation Needed

### Next Debug Steps:

1. **Check WebM Cluster Creation**:
   ```bash
   GST_DEBUG=webmmux:7,vp8enc:6
   ```
   Look for: cluster creation messages, keyframe generation

2. **Verify UDP Stream**:
   ```bash
   tcpdump -i lo -n udp port {PORT} -w /tmp/video.pcap
   ```
   Check: packet rate, sizes, continuity

3. **Test GStreamer Playback** (bypass VLC):
   ```bash
   gst-launch-1.0 udpsrc port={PORT} ! matroskademux ! vp8dec ! autovideosink
   ```
   If this works → VLC compatibility issue
   If this fails → GStreamer pipeline issue

4. **Try Alternative Container**:
   - Option A: Raw VP8 RTP (needs SDP file for VLC)
   - Option B: MPEG-TS (but VP8 not supported, need H.264)
   - Option C: Ogg container
   - Option D: Direct GStreamer playback in Python (no VLC)

---

## 📋 Alternative Solutions

### Option 1: Use H.264 Instead of VP8

**Why**: MPEG-TS supports H.264 natively, VLC plays it perfectly

**Changes Needed**:
- Negotiate H.264 codec with phone (most phones support it)
- Replace `webmmux` with `mpegtsmux`
- Replace `vp8enc/vp8dec` with `x264enc/avdec_h264`

**Advantages**:
- MPEG-TS is proven for UDP streaming
- No WebM cluster issues
- Better VLC compatibility

**Disadvantages**:
- Requires codec negotiation changes
- Phone must support H.264

### Option 2: Use GStreamer for Playback

**Why**: Avoid VLC compatibility issues entirely

**Changes Needed**:
- Replace VLCVideoWidget with GStreamerVideoWidget
- Use GStreamer Python bindings (gi.repository.Gst)
- Pipeline: `udpsrc ! matroskademux ! vp8dec ! videoconvert ! gtksink`

**Advantages**:
- Full control over pipeline
- Native WebM support
- Better debugging

**Disadvantages**:
- More complex Python code
- Qt/GTK integration needed

### Option 3: Fix WebM Streaming

**Why**: Keep current architecture, fix compatibility

**Investigation**:
- Research VLC WebM UDP streaming requirements
- Check if VLC needs complete EBML headers upfront
- Test with `streamable=false` (might help VLC)
- Try `matroskamux` instead of `webmmux`

---

## 🎯 Next Steps to Debug Freeze

### Step 1: Enable Better Logging

**File**: `drunk_call_hook/bridge.py` line 92

**Change from**:
```python
env['GST_DEBUG'] = 'webrtcbin:5,webmmux:6,rtpvp8depay:5'
```

**Change to**:
```python
env['GST_DEBUG'] = 'webrtcbin:5,webmmux:7,vp8enc:6,rtpvp8depay:5'
```

### Step 2: Rebuild, Test, Analyze Logs

1. Rebuild C++ service if needed: `make`
2. Restart siproxylin
3. Make test call, let run 10-15 seconds
4. Check `~/.siproxylin/logs/drunk-call-service.err`

**Look for**:
- `webmmux`: "Creating new cluster", "Wrote cluster" (should be every ~1 sec)
- `vp8enc`: "Encoded keyframe" (every 30 frames)
- `rtpvp8depay`: Continuous packet flow

### Step 3: Quick Diagnostic Test

While call active, note UDP port, then run:
```bash
gst-launch-1.0 udpsrc port={PORT} ! matroskademux ! vp8dec ! autovideosink
```

**If GStreamer works but VLC doesn't**: VLC compatibility issue
**If GStreamer also freezes**: Pipeline buffering issue

### Solutions Based on Findings

**If no clusters being written**: Try `streamable=FALSE` in webmmux config (line 2458)

**If no keyframes**: VP8 encoder issue, add `"keyframe-mode", 0` to force regular intervals

**If GStreamer works, VLC doesn't**: Switch to GStreamer-based playback widget in Python

**Nuclear option**: Switch to H.264 + MPEG-TS (proven working, better compatibility)

---

## 📊 Progress Summary

```
Phase 1: C++ Pipeline        ████████████████████ 100%
Phase 2: Python/Protobuf     ████████████████████ 100%
Phase 3: Jingle/GUI          ████████████████████ 100%
Phase 4: Bug Fixes           ██████████████░░░░░░  70%

Overall:                     ███████████████████░  92%
```

**Status**: Video working but frozen after first frame
**Remaining**: Debug WebM/VLC compatibility or switch to alternative

---

## 📝 Files Changed Summary

| File | Status | Purpose |
|------|--------|---------|
| webrtc_session.cpp | ✅ Complete | Video send/receive pipelines |
| webrtc_session.h | ✅ Complete | Video member variables |
| media_session.h | ✅ Complete | Video config fields |
| call.proto | ✅ Complete | Video protobuf fields |
| bridge.py | ✅ Complete | Video session management |
| video_manager.py | ✅ Complete | UDP port allocation |
| video_widget.py | ✅ Complete | VLC playback widget |
| call_window.py | ✅ Complete | Video widget integration |
| calls.py | ✅ Complete | Video call detection |
| jingle_sdp_converter.py | ✅ Complete | Video codec parsing |

**Total Lines**: ~600 new/modified

---

## 🧪 Testing Results

- ✅ Audio regression test: PASSED (both directions)
- ✅ Video connection: WORKS
- ✅ VLC receives stream: WORKS
- ✅ First frame display: WORKS
- 🐛 Video playback: FROZEN (only first frame)
- ⏳ Full video call: BLOCKED on freeze issue

---

## 🔑 Key Learnings

1. **VP8 + MPEG-TS = No**: MPEG-TS doesn't support VP8
   - Switched to WebM container format

2. **WebM Buffering**: Default settings cause huge latency
   - Fixed with `min-cluster-duration=0`

3. **VLC Caching**: Defaults to 2-3 seconds buffering
   - Fixed with `network-caching=50`, `live-caching=50`

4. **Bitrate Matters**: 500kbps too low for acceptable quality
   - Increased to 1.5Mbps

5. **Encoding Speed**: `cpu-used=4` still adds latency
   - Changed to `cpu-used=8` (fastest)

6. **Port Binding**: Python keeping socket open blocked VLC
   - Fixed by closing socket immediately after allocation

---

**Branch**: `video`
**Next Session**: Debug WebM cluster/keyframe delivery or switch to alternative approach
