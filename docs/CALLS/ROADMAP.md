# Call Service Implementation Roadmap

**Last Updated:** 2026-02-27

---

## Overview

Three-phase strategy to achieve full call compatibility:
1. ✅ **Phase 1:** webrtcbin (fast audio) - **COMPLETE**
2. ⏳ **Phase 2:** rtpbin (video + trickle-only) - **IN PROGRESS**
3. 🔲 **Phase 3:** Factory merge - **PENDING**

---

## Phase 1: webrtcbin Implementation ✅

**Branch:** `calls-webrtcbin`
**Status:** Complete and tested
**Duration:** 2 days (2026-02-25 to 2026-02-26)

### What Works
- Audio calls with modern clients (Dino, Gajim)
- Fast connection time (~1 second)
- Non-BUNDLE SDP generation
- TURN relay support
- Device selection (mic/speakers)
- Full gRPC compatibility with Python GUI

### Limitations (By Design)
- No video support (webrtcbin hardcodes rtcp-mux)
- Cannot handle trickle-only peers (Conversations.im)

### Key Files
- `src/session.cc` - Main session implementation (webrtcbin-based)
- `include/session.h` - Session interface
- `src/call_server.cc` - gRPC service

**Deliverable:** Production-ready audio call service for modern clients

---

## Phase 2: rtpbin Implementation ⏳

**Branch:** `calls-rtpbin`
**Status:** In Progress (80% complete)
**Started:** 2026-02-26
**Strategy:** Blind copy Dino's architecture

### Completed Components ✅

#### Ice Agent (libnice wrapper)
- **File:** `src/ice_agent.cc` (528 lines)
- **Features:**
  - 2-component stream creation (RTP + RTCP)
  - TURN configuration for both components
  - Candidate gathering and state management
  - Remote candidate addition
- **Status:** ✅ Implemented, tested

#### DTLS-SRTP Handler
- **Files:** `src/dtls_srtp_handler.cc` (592 lines)
- **Features:**
  - Certificate generation (ECDSA-256)
  - DTLS handshake (client/server modes)
  - SRTP key derivation
  - RTP/RTCP encryption/decryption
- **Status:** ✅ Implemented, tested

#### SDP Parser
- **File:** `src/sdp_parser.cc` (394 lines)
- **Features:**
  - ICE credentials extraction
  - DTLS fingerprint parsing
  - Codec negotiation
  - Setup attribute handling
- **Status:** ✅ Implemented, uses GstSDPMessage API

#### SRTP Session
- **File:** `src/srtp_session.cc` (226 lines)
- **Features:**
  - libsrtp2 wrapper
  - Key management
  - Packet encryption/decryption
- **Status:** ✅ Implemented

### Remaining Work 🔲

#### Fix ICE Connectivity Issues
**Problem:** ICE checks timeout after 7-8 seconds (both components reach CONNECTING but fail)

**Diagnosis Steps:**
1. Compare with Dino's ICE handling line-by-line
2. Check candidate gathering timing
3. Verify STUN/TURN binding sequence
4. Test with longer call duration (20+ seconds)

**Reference:** Dino's `plugins/ice/transport_parameters.vala`

#### Add Video Support
**Requirements:**
- Camera capture: v4l2src (Linux) / test pattern fallback
- Encoding: VP8 (vp8enc)
- Streaming: UDP to localhost:5004
- Format: RTP/VP8 for Python/Qt playback

**Pipeline (Outgoing):**
```
v4l2src → capsfilter → videoconvert → vp8enc → rtpvp8pay → webrtcbin
```

**Pipeline (Incoming):**
```
webrtcbin → rtpvp8depay → vp8dec → videoconvert → rtpvp8pay → udpsink (localhost:5004)
```

**Reference:** Dino's `plugins/rtp/stream.vala` (video track management)

#### Test with Dino
- Incoming video call from Dino
- Outgoing video call to Dino
- Verify Component 2 candidates exchanged
- Confirm media flows bidirectionally

#### Test with Conversations.im
- Incoming call (trickle-only peer)
- Verify ICE connectivity with 2 components
- Test audio quality

### Key Files (rtpbin)
- `src/session.cc` - rtpbin-based implementation
- `src/ice_agent.cc` - libnice wrapper
- `src/dtls_srtp_handler.cc` - DTLS + SRTP
- `src/sdp_parser.cc` - SDP utilities
- `src/srtp_session.cc` - libsrtp2 wrapper

**Deliverable:** Full-featured call service (video + trickle-only support)

---

## Phase 3: Factory Integration 🔲

**Branch:** `call-service` (new, from merge)
**Status:** Not Started
**Prerequisites:** Phase 2 complete

### Implementation Steps

#### 1. Create Abstract Interface
```cpp
// include/i_session.h
class ISession {
public:
    virtual bool Initialize() = 0;
    virtual std::string CreateOffer() = 0;
    // ... (see ARCHITECTURE.md for full interface)
    virtual ~ISession() = default;
};
```

#### 2. Rename Implementations
- Current `Session` → `SessionWebrtc`
- rtpbin `Session` → `SessionRtpbin`
- Both inherit from `ISession`

#### 3. Create Factory
```cpp
// include/session_factory.h
class SessionFactory {
public:
    static std::unique_ptr<ISession> Create(const Config& config);
};
```

**Routing Logic:**
```cpp
std::unique_ptr<ISession> SessionFactory::Create(const Config& config) {
    if (config.is_trickle_only_peer || !config.camera_device.empty()) {
        return std::make_unique<SessionRtpbin>(config);
    }
    return std::make_unique<SessionWebrtc>(config);
}
```

#### 4. Update CallServer
```cpp
// src/call_server.cc (CreateSession RPC)
auto session = SessionFactory::Create(config);
sessions_[session_id] = std::move(session);
```

#### 5. Build System Updates
**CMakeLists.txt:**
- Conditionally link dependencies (nice/gnutls only if rtpbin enabled)
- Optional: Build flags to disable rtpbin (webrtcbin-only builds)

#### 6. Testing
- **Unit tests:** Mock ISession interface
- **Integration tests:** Both paths with real calls
- **Performance:** Verify webrtcbin path unchanged (still ~1s)
- **Regression:** All existing functionality works

**Deliverable:** Unified call service with automatic routing

---

## Deployment Strategy

### Development (Current)
- **Branch:** `calls-rtpbin` (work in progress)
- **Testing:** Manual calls with Dino/Conversations

### Staging (Phase 2 Complete)
- **Branch:** `calls-rtpbin` (stable)
- **Testing:** Extended compatibility testing
- **Duration:** 1 week of validation

### Integration (Phase 3)
- **Branch:** `call-service` (factory merge)
- **Testing:** Full regression suite
- **Duration:** 3-5 days

### Production (Final)
- **Merge to:** `main` branch
- **Binary:** `drunk-call-service-linux`
- **Rollback Plan:** Keep Go binary as `_go` suffix for 1 release cycle

---

## Success Criteria

### Phase 2 (rtpbin)
- ✅ ICE connects reliably (both components)
- ✅ Audio flows bidirectionally
- ✅ Video calls work with Dino
- ✅ Trickle-only peers (Conversations.im) work
- ✅ No regressions from existing functionality

### Phase 3 (Factory)
- ✅ Correct routing: webrtcbin for normal, rtpbin for video/trickle
- ✅ webrtcbin path performance unchanged
- ✅ All clients tested: Dino, Conversations, Gajim
- ✅ Clean code: No shared state between implementations

---

## Timeline Estimate

| Phase | Duration | Status |
|-------|----------|--------|
| Phase 1 (webrtcbin) | 2 days | ✅ Complete |
| Phase 2 (rtpbin) | 5-7 days | ⏳ In Progress (80%) |
| Phase 3 (factory) | 3-5 days | 🔲 Pending |
| **Total** | **10-14 days** | - |

**Current Progress:** Day 4 of Phase 2

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| ICE timeout unfixable | Low | High | Compare Dino line-by-line, copy exact logic |
| Video implementation complex | Medium | Medium | Start with test pattern, incremental camera support |
| Factory integration issues | Low | Low | Keep implementations completely independent |
| Performance regression | Low | Medium | Benchmark webrtcbin path before/after merge |

---

## Next Session Focus

**Priority 1:** Fix ICE connectivity timeouts in rtpbin
- Compare with Dino's ICE agent initialization
- Check TURN relay timing
- Test with extended call duration

**Priority 2:** Add video support to rtpbin
- Implement camera capture pipeline
- UDP streaming to Python/Qt
- Test with Dino video calls

**Reference Files:**
- Dino: `plugins/ice/transport_parameters.vala` (ICE)
- Dino: `plugins/rtp/stream.vala` (Video)
- GStreamer: webrtcbin source (reference patterns)

---

**Next Steps:** See `SESSION-NOTES.md` for technical details and pitfalls
