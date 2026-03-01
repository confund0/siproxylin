# Call Service C++ Implementation Plan

**Status:** Phase 5 Complete - Audio Working ✅
**Created:** 2026-02-25
**Last Updated:** 2026-02-25
**Actual Duration:** 2 days (Phases 0-5)
**Remaining Work:** Device enumeration, audio processing, video

---

## Motivation

**Problem:** Pion WebRTC cannot generate non-BUNDLE SDP offers, breaking compatibility with traditional XMPP Jingle clients (Dino, Conversations.im). BundlePolicyMaxCompat only affects CreateAnswer(), not CreateOffer().

**Evidence:** Tested 2026-02-25 - Pion always generates BUNDLE offers with shared ICE credentials, regardless of policy setting.

**Solution:** Rewrite drunk_call_service in C++ using GStreamer webrtcbin, which correctly implements BUNDLE_POLICY_NONE for non-BUNDLE offers.

**Architecture Decision:** Switched from LibWebRTC to GStreamer due to C++ ABI incompatibility (LibWebRTC prebuilts used Clang/libc++, system libs use GCC/libstdc++).

---

## Goals

**Primary:**
- ✅ Audio calls work with Dino (outgoing and incoming)
- ⏳ Video calls work with Dino (pending)
- ✅ Audio quality maintained
- ✅ 100% gRPC interface compatibility (Python GUI unchanged)
- ✅ Same logging format as Go version (slog-compatible structured logs)
- ✅ Drop-in binary replacement (same path, Go binary moved to _go/)
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

### Phase 0: Project Setup ✅ COMPLETE

**Status:** Done (2026-02-25)

**Completed Tasks:**
- ✅ Go binary moved to `drunk_call_service_go/` directory
- ✅ C++ project structure created (include/, src/, proto/, tests/, cmake/)
- ✅ CMake build system with Makefile wrapper
- ✅ gRPC toolchain configured (protoc, grpc_cpp_plugin)
- ✅ Build system tested and working
- ✅ Binary outputs to `bin/drunk-call-service-linux`

**Deliverable:** ✅ Complete build system, compiles cleanly

**Note:** LibWebRTC download deferred to Phase 2 (not needed for skeleton)

---

### Phase 1: gRPC Service Skeleton + Heartbeat ✅ COMPLETE

**Status:** Done (2026-02-25)

**Completed Tasks:**
- ✅ Copied proto/call.proto from Go service
- ✅ Generated C++ gRPC stubs (automated in CMake)
- ✅ **Logging framework implemented:**
  - ✅ spdlog with rotating_file_sink (10MB max, 5 backups)
  - ✅ Format matches Go slog: `time="2026-02-25 14:09:02.597" level=info msg="..."`
  - ✅ Millisecond-precision timestamps
  - ✅ Flush on every log (trace level) for debugging
  - ✅ No STDOUT output (file only)
- ✅ CLI argument parsing (`--log-level`, `--log-path`, `--port`, `--help`, `--version`)
- ✅ **All 13 gRPC RPCs stubbed and callable:**
  - ✅ Heartbeat() - tested with grpcurl
  - ✅ Shutdown() - graceful shutdown
  - ✅ CreateSession/EndSession
  - ✅ CreateOffer/CreateAnswer/SetRemoteDescription
  - ✅ AddICECandidate/StreamEvents
  - ✅ ListAudioDevices/ListVideoDevices - return stub data
  - ✅ SetMute/GetStats
- ✅ **Signal handling (SIGTERM/SIGINT):**
  - ✅ Separate thread using sigwait() (NOT async signal handler)
  - ✅ No deadlocks, clean shutdown
  - ✅ Graceful server.Wait() termination

**Deliverable:** ✅ Fully functional skeleton, all RPCs respond, clean shutdown

**Test Results:**
```
✅ Service starts on port 50051
✅ Heartbeat RPC works (tested with grpcurl)
✅ ListAudioDevices returns 2 stub devices
✅ Logs written with correct format
✅ SIGTERM/SIGINT shutdown cleanly (no deadlocks)
✅ No zombie processes
```

**Known Issues Fixed:**
- ✅ Logger deadlock (was flushing only on error, now flushes on trace)
- ✅ Signal handler deadlock (moved to thread with sigwait)
- ✅ Log format placeholders (switched to spdlog `{}` formatting)

---

### Phase 2: GStreamer Integration ✅ COMPLETE

**Status:** Done (2026-02-25)

**Completed Tasks:**
- ✅ Replaced LibWebRTC with GStreamer webrtcbin
- ✅ Session class wraps GstElement pipeline and webrtcbin
- ✅ Configure `bundle-policy=NONE` for separate ICE per media
- ✅ TURN server configuration via `add-turn-server` signal
- ✅ ICE transport policy (relay/all) configuration

**Deliverable:** ✅ GStreamer-based service builds, bundle policy configured

---

### Phase 3: SDP Negotiation ✅ COMPLETE

**Status:** Done (2026-02-25)

**Completed Tasks:**
- ✅ CreateOffer with on-negotiation-needed signal synchronization
- ✅ CreateAnswer with proper pipeline state management (PLAYING → transceiver → remote desc)
- ✅ SetRemoteDescription for incoming answers
- ✅ Verified non-BUNDLE SDP with separate ICE credentials
- ✅ ICE candidate queuing for race condition handling

**Key Issues Resolved:**
- Empty SDP offers: Wait for on-negotiation-needed signal
- Empty SDP answers: Set pipeline PLAYING before remote description
- Content name mapping: Python Jingle layer handles translation

**Deliverable:** ✅ Correct non-BUNDLE SDP, both call directions work

---

### Phase 4: ICE Handling ✅ COMPLETE

**Status:** Done (2026-02-25)

**Completed Tasks:**
- ✅ AddICECandidate with candidate queuing
- ✅ StreamEvents for ICE candidate streaming
- ✅ OnIceCandidate callback implementation
- ✅ OnConnectionStateChange callbacks
- ✅ Pending candidate queue (prevents race condition)

**Deliverable:** ✅ ICE candidates flow correctly, TURN relay connects

---

### Phase 5: Audio Tracks ✅ COMPLETE

**Status:** Done (2026-02-25)

**Completed Tasks:**
- ✅ Microphone capture: autoaudiosrc/pulsesrc with device selection
- ✅ Speaker playback: autoaudiosink/pulsesink with device selection
- ✅ Audio pipeline: src → audioconvert → audioresample → opusenc → rtpopuspay → webrtcbin
- ✅ Playback pipeline: webrtcbin → rtpopusdepay → opusdec → audioconvert → audioresample → sink
- ✅ pad-added signal handler for incoming audio
- ✅ Full duplex audio tested with Dino

**Key Issues Resolved:**
- Playback pad linking: GST_PAD_LINK_WRONG_DIRECTION fixed by using individual elements

**Deliverable:** ✅ Audio calls work end-to-end with Dino

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

| Milestone | Phase | Target | Status | Date |
|-----------|-------|--------|--------|------|
| **M1: Service Skeleton** | 0-1 | Day 2 | ✅ **COMPLETE** | 2026-02-25 |
| **M2: Sessions Work** | 2-4 | Day 5 | ✅ **COMPLETE** | 2026-02-25 |
| **M3: Audio Calls Work** | 5 | Day 7 | ✅ **COMPLETE** | 2026-02-25 |
| **M4: Video Calls Work** | 6 | Day 8 | ⏳ **IN PROGRESS** | - |
| **M5: Feature Complete** | 7-9 | Day 10 | 🔲 Pending | - |
| **M6: Cross-Platform** | 11-12 | Day 14 | 🔲 Pending | - |
| **M7: Production Ready** | 13 | Day 15 | 🔲 Pending | - |

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

## Current Status (2026-02-25)

**Completed:** Phase 0-5 (Audio Calls Working)
**Next:** Phase 6-7 (Video + Device Enumeration)

**What Works:**
- ✅ Full duplex audio calls (mic + speakers)
- ✅ Outgoing and incoming calls with Dino
- ✅ Non-BUNDLE SDP generation
- ✅ ICE candidate handling with queuing
- ✅ TURN relay connectivity
- ✅ Device selection (microphone_device, speakers_device config)
- ✅ gRPC interface fully compatible with Python GUI

**What's Pending:**
- ⏳ Device enumeration RPCs (ListAudioDevices, ListVideoDevices)
- ⏳ Audio processing (webrtcdsp for echo cancellation)
- ⏳ Video capture (v4l2src for camera)
- 🔲 Cross-platform builds (Windows, macOS)

**Time Investment:** ~2 days (phases 0-5)
**Remaining Estimate:** 3-5 days for remaining features

---

**Last Updated:** 2026-02-25
**Next Review:** After Phase 2 (LibWebRTC integration complete)
