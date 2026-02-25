# Call Service C++ Implementation Plan

**Status:** Planning Phase
**Created:** 2026-02-25
**Estimated Duration:** 15 days full-time (3 weeks) or 6 weeks part-time
**Target Release:** TBD

---

## Motivation

**Problem:** Pion WebRTC cannot generate non-BUNDLE SDP offers, breaking compatibility with traditional XMPP Jingle clients (Dino, Conversations.im). BundlePolicyMaxCompat only affects CreateAnswer(), not CreateOffer().

**Evidence:** Tested 2026-02-25 - Pion always generates BUNDLE offers with shared ICE credentials, regardless of policy setting.

**Solution:** Rewrite drunk_call_service in C++ using LibWebRTC, which correctly implements BundlePolicyMaxCompat for both offers and answers.

**Why LibWebRTC:** Used by Conversations.im for Jingle calls, proven interoperability, battle-tested quality.

---

## Goals

**Primary:**
- ✅ Video calls work with Dino (outgoing and incoming)
- ✅ Video calls work with Conversations.im
- ✅ Audio quality maintained or improved
- ✅ 100% gRPC interface compatibility (Python GUI unchanged)
- ✅ Same logging format as Go version (slog-compatible structured logs)
- ✅ Drop-in binary replacement (same path, rename Go binary)
- ✅ Heartbeat and service lifecycle management work with Python

**Secondary:**
- Device enumeration improvements (friendly names)
- Better stats reporting (LibWebRTC provides richer data)
- Foundation for future features (H.264, simulcast, etc.)

---

## Non-Goals (Deferred)

- Screen sharing (future phase)
- H.264 codec support (VP8 only initially)
- iOS/Android ports (desktop platforms only)
- Audio/video recording or transcoding
- Multi-party calls (SFU functionality)

---

## Phases

### Phase 0: Project Setup (Day 1)

**Tasks:**
- **Rename Go binary** for backup: `drunk-call-service-linux` → `drunk-call-service-linux_go`
- Create C++ project structure in same directory (directories, Makefile, CMakeLists.txt)
- Download LibWebRTC prebuilt binaries for Linux x64
- Set up gRPC toolchain (protoc-gen-grpc-cpp)
- Create hello-world gRPC service (verify build works)
- Build outputs to same path: `bin/drunk-call-service-linux`

**Deliverable:** Builds and runs simple gRPC server, Go binary preserved as `_go` suffix

**Note:** Python code unchanged - still looks for `drunk-call-service-{platform}`, now finds C++ version

---

### Phase 1: gRPC Service Skeleton + Heartbeat (Day 2)

**Tasks:**
- Copy proto/call.proto from Go service
- Generate C++ gRPC stubs
- **Implement logging framework first** (critical for debugging):
  - Use spdlog with rotating_file_sink (replaces Go's lumberjack)
  - Configure: 10MB max, 5 backups, 30 days retention
  - Format: Match Go's slog text format: `time="..." level=... msg="..." key=value`
  - No STDOUT output (file only, except test modes)
- Add command-line argument parsing (`--log-level`, `--log-path`, `--port`)
- **Implement Heartbeat() RPC** (first working RPC, log receipt, return empty)
- **Implement Shutdown() RPC** (exit with code 0, log shutdown)
- Test Python `GoCallService` can start service, heartbeat works, graceful shutdown works

**Deliverable:** Service starts, heartbeat works, Python can manage lifecycle

**Critical:**
- Heartbeat MUST be first working RPC. Without it, Python cannot verify service health.
- Logging format MUST match Go version (verify with: compare log output side-by-side)
- Example Go log: `time="2026-02-25 12:45:41.747" level=INFO msg="Heartbeat received"`
- No STDOUT output (except --test-devices/--test-video modes)
- STDERR only for crashes/panics

---

### Phase 2: Session Management (Days 3-5)

**Tasks:**
- Implement CreateSession RPC (create PeerConnection)
- Configure BundlePolicyMaxCompat
- Configure TURN servers and proxy settings
- Configure ICE transport policy (relay-only mode)
- Implement EndSession RPC (cleanup resources)
- Session lifecycle management (map of session_id → Session)

**Deliverable:** Can create and destroy sessions, PeerConnection initialized

---

### Phase 3: SDP Negotiation (Days 4-5)

**Tasks:**
- Implement CreateOffer RPC (PeerConnection::CreateOffer)
- Implement CreateAnswer RPC (PeerConnection::CreateAnswer)
- Implement SetRemoteDescription RPC
- Verify SDP output has separate ICE credentials per media
- Add logging to analyze BUNDLE groups and ice-ufrag values

**Deliverable:** Generates correct non-BUNDLE SDP, signaling flow works

---

### Phase 4: ICE Handling (Day 5)

**Tasks:**
- Implement AddICECandidate RPC
- Implement StreamEvents RPC (ICE candidate streaming)
- Wire PeerConnection::OnIceCandidate callback
- Wire PeerConnection::OnConnectionChange callback
- Handle ICE restart scenarios

**Deliverable:** ICE candidates flow Python ↔ C++, connection establishes

---

### Phase 5: Audio Tracks (Days 6-7)

**Tasks:**
- Create audio track with AudioProcessing options (echo cancel, noise suppression, gain control)
- Map CreateSessionRequest audio parameters to LibWebRTC AudioOptions
- Add audio track to PeerConnection
- Implement SetMute RPC (toggle audio track enabled state)
- Test audio-only call end-to-end

**Deliverable:** Audio calls work with Dino

---

### Phase 6: Video Tracks (Days 7-8)

**Tasks:**
- Implement camera capture using LibWebRTC VideoCaptureModule
- Create video track with VP8 encoder
- Add video track to PeerConnection (conditional on camera_device parameter)
- Handle video track start/stop
- Test video call end-to-end

**Deliverable:** Video calls work with Dino (both directions)

---

### Phase 7: Device Enumeration (Day 8)

**Tasks:**
- Implement ListAudioDevices RPC (AudioDeviceModule APIs)
- Implement ListVideoDevices RPC (VideoCaptureModule APIs)
- Add platform-specific code for friendly device names
- Handle missing/invalid devices gracefully

**Deliverable:** Device lists match Go service output, Python GUI device selection works

---

### Phase 8: Stats Collection (Day 9)

**Tasks:**
- Implement GetStats RPC (PeerConnection::GetStats)
- Map RTCStatsReport to proto GetStatsResponse
- Extract connection state, bandwidth, candidate types
- Implement periodic stats refresh (match Go service behavior)

**Deliverable:** Stats display in Python GUI works identically

---

### Phase 9: Error Handling & Polish (Day 10)

**Tasks:**
- Add comprehensive error handling (gRPC status codes)
- Add input validation (check session_id exists, SDP valid, etc.)
- Add graceful shutdown (Shutdown RPC, SIGTERM handler)
- Memory leak checks (ASAN, valgrind)
- Thread safety review

**Deliverable:** Production-quality error handling

---

### Phase 10: Testing & Debugging (Days 11-12)

**Tasks:**
- End-to-end call testing (audio + video)
- Cross-client testing (Dino, Conversations.im)
- Test all device enumeration paths
- Test proxy/TURN relay mode
- Test audio processing settings (echo cancel, noise suppression)
- Test mute functionality
- **Test CLI parameters:** `--test-devices`, `--test-video`, `--camera-device`
- **Verify logging format:** Compare C++ logs to Go logs (must match exactly)
- **Test service lifecycle:** Start, heartbeat, shutdown, crash recovery
- Performance profiling (CPU, memory)

**Deliverable:** All features verified working, logs match Go format, CLI compatible

---

### Phase 11: Windows Build (Day 13)

**Tasks:**
- Set up Windows build environment (Visual Studio or MinGW)
- Download LibWebRTC Windows x64 binaries
- Adjust CMakeLists.txt for Windows-specific libraries
- Fix platform-specific code (device enumeration, audio APIs)
- Build and test on Windows

**Deliverable:** Windows binary that works with Python GUI

---

### Phase 12: macOS Build (Day 14)

**Tasks:**
- Set up macOS build environment (Xcode)
- Download LibWebRTC macOS arm64/x64 binaries
- Build universal binary (arm64 + x86_64)
- Fix platform-specific code (CoreAudio, AVFoundation)
- Build and test on macOS

**Deliverable:** macOS binary that works with Python GUI

---

### Phase 13: Documentation & Release (Day 15)

**Tasks:**
- Update Python integration code (point to new binary)
- Write migration guide for users
- Update build documentation
- Create release notes
- Tag version and create GitHub release

**Deliverable:** Shippable release, users can build from source

---

## Milestones

| Milestone | Phase | Target | Criteria |
|-----------|-------|--------|----------|
| **M1: Service Skeleton** | 1 | Day 2 | gRPC RPCs callable from Python |
| **M2: Sessions Work** | 2 | Day 5 | Can create/destroy PeerConnections |
| **M3: Audio Calls Work** | 5 | Day 7 | Audio calls with Dino successful |
| **M4: Video Calls Work** | 6 | Day 8 | Video calls with Dino successful |
| **M5: Feature Complete** | 9 | Day 10 | All RPCs implemented, tested |
| **M6: Cross-Platform** | 12 | Day 14 | Builds on Windows, macOS, Linux |
| **M7: Production Ready** | 13 | Day 15 | Released, documented, shippable |

---

## Success Criteria

### Must Have (MVP)

- ✅ Video calls with Dino work (outgoing and incoming)
- ✅ Audio calls work (no regressions)
- ✅ Device enumeration works (audio and video)
- ✅ Stats reporting works
- ✅ Mute functionality works
- ✅ Builds on Linux x64

### Should Have (v1.0)

- ✅ Builds on Windows x64
- ✅ Builds on macOS arm64/x64
- ✅ Cross-client tested (Conversations.im)
- ✅ Proxy/TURN relay mode tested
- ✅ Audio processing settings tested

### Could Have (Future)

- Screen sharing support
- H.264 codec support
- Hardware video encoding
- Simulcast (multiple quality streams)
- Bandwidth adaptation

---

## Risks & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| LibWebRTC MaxCompat still broken | Low | Critical | Test early (Phase 3), fallback to manual SDP manipulation |
| Cross-compilation complexity | Medium | Medium | Use native builds on CI runners instead |
| LibWebRTC API learning curve | Medium | Medium | Reference Conversations.im source code, LibWebRTC examples |
| Device enumeration platform issues | Medium | Low | Fall back to LibWebRTC built-in APIs, skip extended info |
| Build system complexity | Low | Low | Use CMake presets, document prerequisites clearly |

---

## Testing Matrix

### Platforms

- Ubuntu 22.04 x64 (primary development)
- Windows 11 x64
- macOS 13+ (arm64 and x86_64)

### Test Scenarios

**Call Types:**
- Audio-only (outgoing, incoming)
- Video calls (outgoing, incoming)
- Audio-only → video upgrade (add track mid-call)

**Network Scenarios:**
- Direct P2P (LAN)
- TURN relay (internet)
- Proxy mode (SOCKS5, HTTP)

**Devices:**
- Multiple microphones (test selection)
- Multiple cameras (test selection)
- No camera (audio-only fallback)
- Invalid device IDs (error handling)

**Cross-Client:**
- Dino (Linux) - primary target
- Conversations.im (Android) - secondary
- Siproxylin ↔ Siproxylin - self-test

---

## Go Service Deprecation Plan

**Binary Path Migration (Phase 0):**
1. Rename Go binary: `bin/drunk-call-service-linux` → `bin/drunk-call-service-linux_go`
2. C++ binary built to original path: `bin/drunk-call-service-linux`
3. Python code unchanged (still looks for `drunk-call-service-{platform}`)
4. Result: Drop-in replacement, no Python changes needed

**Parallel Development:**
- Go binary available as `_go` suffix for testing/comparison
- Both binaries can coexist during development

**Cutover:**
- No cutover needed! C++ binary replaces Go binary automatically
- User can manually switch back by renaming binaries if needed

**Grace Period:**
- 2 weeks of C++ binary in production
- Go binary kept as `_go` for emergency rollback

**Rollback (if needed):**
- Rename back: `drunk-call-service-linux_go` → `drunk-call-service-linux`
- Delete C++ binary
- Restart app

**Final Removal (after 1 release cycle):**
- Delete `bin/drunk-call-service-*_go` binaries
- Delete Go source code from drunk_call_service/
- Keep old_docs/VIDEO-CALL-PLAN.md for history

---

## Resources

**References:**
- LibWebRTC Documentation: https://webrtc.github.io/webrtc-org/
- LibWebRTC Examples: https://github.com/webrtc/samples
- Conversations.im Source: https://github.com/iNPUTmice/Conversations (Jingle implementation)
- XEP-0167: Jingle RTP Sessions specification

**Build Resources:**
- LibWebRTC Prebuilt: https://github.com/webrtc-sdk/libwebrtc/releases
- gRPC C++ Quickstart: https://grpc.io/docs/languages/cpp/quickstart/

---

**Last Updated:** 2026-02-25
**Next Review:** After Phase 3 (verify SDP generation works)
