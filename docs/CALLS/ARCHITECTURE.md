# Call Service Architecture

**Last Updated:** 2026-03-01

**Note:** Sections marked 🔮 are aspirational (planned but not yet implemented)

---

## Current State

### 1. Go/Pion (Production - Main Branch)
- **Status:** ✅ Deployed, stable
- **Technology:** Go + Pion WebRTC
- **Capabilities:** Audio calls with modern clients
- **Limitations:**
  - Cannot handle trickle-only peers (Conversations.im)
  - No video support
  - BUNDLE-only SDP generation

### 2. C++ GStreamer webrtcbin (Branch: `calls-webrtcbin`)
- **Status:** ✅ Complete, tested
- **Technology:** C++ + GStreamer webrtcbin
- **Capabilities:**
  - Audio calls with modern clients (Dino, Gajim)
  - Fast connection (~1 second)
  - Non-BUNDLE SDP support
- **Limitations:**
  - Cannot handle trickle-only peers
  - No video support (webrtcbin hardcodes rtcp-mux)

### 3. C++ GStreamer rtpbin (Branch: `calls-rtpbin`)
- **Status:** ⚠️ Debugging (ICE/DTLS working, audio flow pending)
- **Technology:** C++ + GStreamer rtpbin + libnice + manual DTLS-SRTP
- **Goal:** Full compatibility (video + trickle-only peers)
- **Implementation:** Following Dino's implementation
- **Current:** Outgoing calls connect with Dino, mode selection fixed, needs rebuild

---

## 🔮 Final Target Architecture (NOT YET IMPLEMENTED)

### Factory Pattern with Dual Implementations

```
Python/Qt GUI
    ↓ gRPC
CallServiceImpl
    ↓
SessionFactory::Create(config)
    ├─→ SessionWebrtc (fast path)
    │   - Modern clients (Dino, Gajim)
    │   - Audio-only
    │   - ~1s connection
    │
    └─→ SessionRtpbin (compatibility path)
        - Trickle-only peers (Conversations.im)
        - Video calls
        - All peer types
```

### Routing Logic

```cpp
ISession* SessionFactory::Create(const Config& config) {
    if (config.is_trickle_only_peer || !config.camera_device.empty()) {
        return new SessionRtpbin(config);  // Compatibility
    }
    return new SessionWebrtc(config);  // Fast default
}
```

### Interface Contract

Both implementations inherit from `ISession`:

```cpp
class ISession {
public:
    virtual bool Initialize() = 0;
    virtual std::string CreateOffer() = 0;
    virtual std::string CreateAnswer(const std::string& remote_sdp, bool offer_has_bundle) = 0;
    virtual bool SetRemoteDescription(const std::string& sdp, const std::string& type) = 0;
    virtual bool AddICECandidate(const std::string& candidate, const std::string& mid, int mline_index) = 0;
    virtual bool PopEvent(Event& event, int timeout_ms) = 0;
    virtual Stats GetStats() = 0;
    virtual bool SetMute(bool muted) = 0;
    virtual void Close() = 0;
    virtual ~ISession() = default;
};
```

**Key Principle:** No shared code between implementations (completely independent).

**Current Reality:** Working on single rtpbin implementation first. Factory pattern deferred until rtpbin is stable and audio working.

---

## 🔮 Video Streaming Architecture (NOT YET IMPLEMENTED)

Both implementations stream decoded video via UDP to Python/Qt.

### C++ Side (Video Source)
```
Camera/Remote Peer
    ↓ (decoded frames)
GStreamer Pipeline:
    videoconvert → rtpvp8pay → udpsink (localhost:5004)
```

### Python Side (Video Display)
```python
# Qt widget with GStreamer playback
pipeline = Gst.parse_launch(
    "udpsrc port=5004 caps=application/x-rtp ! "
    "rtpvp8depay ! vp8dec ! videoconvert ! autovideosink"
)
```

**Configuration:**
- Port: Configurable per session (default: 5004)
- Format: RTP/VP8 (preserves timestamps for A/V sync)
- Scope: localhost only (no network exposure)

---

## gRPC Interface

### Python → C++ (Control)

| RPC | Purpose | Parameters |
|-----|---------|------------|
| CreateSession | Initialize call | session_id, devices, TURN config, is_trickle_only_peer |
| CreateOffer | Start outgoing call | session_id |
| CreateAnswer | Accept incoming call | session_id, remote_sdp, offer_has_bundle |
| SetRemoteDescription | Apply peer's SDP | session_id, sdp, type |
| AddICECandidate | Add remote candidate | session_id, candidate, mid, mline_index |
| SetMute | Mute/unmute audio | session_id, muted |
| ListAudioDevices | Get mic/speaker list | - |
| ListVideoDevices | Get camera list | - |
| EndSession | Close call | session_id |
| Shutdown | Stop service | - |

### C++ → Python (Events)

| RPC | Purpose | Event Types |
|-----|---------|-------------|
| StreamEvents | Event stream | ICE_CANDIDATE, CONNECTION_STATE_CHANGE, ICE_CONNECTION_STATE_CHANGE, ICE_GATHERING_STATE_CHANGE |
| GetStats | Call statistics | bytes_sent/received, connection_type, candidate_pair |
| Heartbeat | Service health | uptime, active_sessions |

### Key Parameters

**`is_trickle_only_peer`**: Detected when peer sends SDP with 0 candidates (Conversations.im behavior). Routes to rtpbin implementation.

**`camera_device`**: If non-empty, enables video and routes to rtpbin (webrtcbin lacks video support).

---

## Technology Stack

### Common Dependencies
- **gRPC/protobuf**: Python ↔ C++ communication
- **spdlog**: Structured logging (Go slog-compatible format)
- **GStreamer 1.0**: Media pipeline framework

### SessionWebrtc Dependencies
- **GStreamer webrtcbin plugin**: All-in-one WebRTC element
- Includes: ICE (libnice), DTLS-SRTP, SDP generation

### SessionRtpbin Dependencies
- **GStreamer rtpbin plugin**: RTP/RTCP handling
- **libnice**: Direct ICE agent control (2 components)
- **GnuTLS or OpenSSL**: DTLS handshake
- **libsrtp2**: SRTP encryption/decryption

---

## File Structure (Post-Merge)

```
drunk_call_service/
├── include/
│   ├── i_session.h              # Abstract interface
│   ├── session_webrtc.h         # Fast webrtcbin implementation
│   ├── session_rtpbin.h         # Compatibility rtpbin implementation
│   ├── session_factory.h        # Factory pattern
│   ├── ice_agent.h              # (rtpbin only) libnice wrapper
│   ├── dtls_srtp_handler.h      # (rtpbin only) DTLS + SRTP
│   └── sdp_parser.h             # (rtpbin only) SDP utilities
├── src/
│   ├── call_server.cc           # gRPC service (uses factory)
│   ├── session_webrtc.cc        # webrtcbin implementation
│   ├── session_rtpbin.cc        # rtpbin implementation
│   ├── session_factory.cc       # Routing logic
│   └── [ice_agent, dtls, etc.]  # rtpbin support files
└── proto/
    └── call.proto               # gRPC interface definition
```

---

## Build Process

### Branch Strategy (Temporary)

1. **`calls-webrtcbin`**: Develop/test webrtcbin in isolation
2. **`calls-rtpbin`**: Develop/test rtpbin in isolation
3. **`call-service`**: Merge both with factory pattern

### Final Merged Build

```bash
cd drunk_call_service
mkdir build && cd build
cmake ..
make -j$(nproc)
# Output: bin/drunk-call-service-linux
```

**Dependencies auto-detected via pkg-config:**
- gstreamer-1.0, gstreamer-app-1.0, gstreamer-rtp-1.0
- nice (for rtpbin)
- gnutls or openssl (for rtpbin)
- libsrtp2 (for rtpbin)
- grpc++, protobuf

---

## Dino Reference Architecture

**Why Dino?** It successfully handles video + trickle-only peers using rtpbin.

### Dino's Components (What We Follow)

| Dino Component | Our Equivalent | Purpose |
|----------------|----------------|---------|
| `plugins/ice/transport_parameters.vala` | `ice_agent.cc` | libnice direct control (2 components) |
| `plugins/ice/dtls_srtp.vala` | `dtls_srtp_handler.cc` | DTLS handshake + SRTP encryption |
| `plugins/rtp/stream.vala` | `session_rtpbin.cc` | rtpbin pipeline management |
| `crypto-vala/src/srtp.vala` | `srtp_session.cc` | libsrtp2 wrapper |

**Strategy:** Strictly refer to Dino's logic (Vala → C++) to ensure compatibility.

---

## Testing Strategy

### Phase 1: Independent Branch Testing
- **webrtcbin**: Test audio calls with Dino (already working)
- **rtpbin**: Test with Conversations.im (trickle-only) and Dino (video)

### Phase 2: Factory Integration Testing
- Route logic: Verify correct implementation chosen
- Regression: Ensure webrtcbin path still fast
- Coverage: Test all peer types (Dino, Conversations, Gajim)

### Phase 3: Production Validation
- Performance: Connection time benchmarks
- Stability: 24h call stress test
- Compatibility: Cross-client matrix testing

---

