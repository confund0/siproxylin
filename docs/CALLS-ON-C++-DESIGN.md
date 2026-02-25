# Call Service C++ Architecture Design

**Status:** Planning (Not Implemented)
**Created:** 2026-02-25
**Target:** Replace Pion-based Go service with LibWebRTC-based C++ service

---

## Overview

Rewrite `drunk_call_service` in C++ using LibWebRTC to achieve proper Jingle compatibility with traditional XMPP clients (Dino, Conversations.im). The Go/Pion implementation cannot generate non-BUNDLE offers, breaking interoperability.

**Key Requirement:** 100% gRPC interface compatibility - Python GUI remains unchanged.

---

## Interface

**Protocol:** gRPC (proto/call.proto - unchanged from Go version)

**Service Methods:**
- Session management: CreateSession, EndSession
- SDP negotiation: CreateOffer, CreateAnswer, SetRemoteDescription
- ICE handling: AddICECandidate, StreamEvents
- Device enumeration: ListAudioDevices, ListVideoDevices
- Runtime control: SetMute, GetStats, Heartbeat, Shutdown

**Wire Format:** Protobuf v3 (identical to Go service)

---

## Code Hierarchy

```
drunk_call_service_cpp/
├── proto/                      # gRPC interface (copied from Go version)
│   └── call.proto
├── include/                    # Public headers
│   ├── call_server.h          # gRPC service interface
│   ├── session.h              # WebRTC session wrapper
│   ├── device_manager.h       # Audio/video device enumeration
│   └── stats_collector.h      # Statistics gathering
├── src/                        # Implementation
│   ├── main.cc                # Entry point, gRPC server setup
│   ├── call_server.cc         # RPC method implementations
│   ├── session.cc             # PeerConnection lifecycle
│   ├── audio_track.cc         # Audio capture/playback
│   ├── video_track.cc         # Camera capture, VP8 encoding
│   ├── device_manager.cc      # Device APIs (cross-platform)
│   ├── stats_collector.cc     # LibWebRTC stats → proto mapping
│   └── platform/              # Platform-specific code
│       ├── devices_linux.cc
│       ├── devices_windows.cc
│       └── devices_darwin.cc
├── cmake/                      # Build configuration
│   ├── FindLibWebRTC.cmake
│   └── toolchains/            # Cross-compilation support
├── third_party/                # External dependencies
│   └── libwebrtc/             # Prebuilt binaries (not source)
└── Makefile                    # Build wrapper
```

---

## Dependencies

### Core Libraries

**LibWebRTC** (prebuilt binaries)
- Purpose: WebRTC stack (ICE, DTLS, SRTP, codecs)
- Source: https://github.com/webrtc-sdk/libwebrtc/releases
- Platforms: Linux x64, Windows x64, macOS arm64/x64
- Version: M104+ (stable branch)

**gRPC + Protobuf**
- Purpose: Service interface
- Install: System packages (libgrpc++-dev, protobuf-compiler-grpc)

**spdlog** (structured logging with rotation)
- Purpose: Logging with automatic file rotation (replaces Go's lumberjack)
- Install: System packages (libspdlog-dev) or header-only
- Features: rotating_file_sink (10MB, 5 backups, 30 days)
- Format: Text format matching Go's slog output

**Platform Audio/Video APIs**
- Linux: ALSA (libasound2-dev), V4L2 (linux-headers)
- Windows: Media Foundation (Windows SDK)
- macOS: CoreAudio, AVFoundation (system frameworks)

### Optional Dependencies

**Proxy Support:** libcurl or platform sockets (for SOCKS5/HTTP TURN proxying)

---

## Building

### Quick Start

```bash
make                    # Build for current platform
make test               # Run unit tests
make install            # Install to bin/
```

### Platform-Specific Builds

```bash
make linux              # Build Linux x64 binary
make windows            # Build Windows x64 binary (requires MinGW or Windows host)
make macos              # Build macOS universal binary (requires macOS host or osxcross)
```

### Development Builds

```bash
make debug              # Build with debug symbols and sanitizers
make clean              # Clean build artifacts
make deps               # Download LibWebRTC prebuilt binaries
```

### Build Requirements

**All Platforms:**
- C++17 compiler (GCC 9+, Clang 10+, MSVC 2019+)
- CMake 3.20+
- gRPC and Protobuf toolchain

**Linux:**
- GCC or Clang
- ALSA development headers
- V4L2 headers (kernel headers)

**Windows:**
- Visual Studio 2019+ or MinGW-w64
- Windows SDK 10.0.19041+

**macOS:**
- Xcode 13+ or Command Line Tools
- macOS 11+ SDK

---

## CLI Parameters

```
drunk_call_service [OPTIONS]

REQUIRED (set by CallBridge):
  --log-level LEVEL      Logging level: DEBUG|INFO|WARN|ERROR (default: INFO)
  --log-path PATH        Log file path (required, no default)

OPTIONAL:
  --port PORT            gRPC server port (default: 50051)
  --version              Print version and exit
  --help                 Show this help message

TESTING/DEBUG:
  --test-devices         Test device enumeration and exit
  --test-video PORT      Test video streaming on UDP port (e.g., 5004)
  --camera-device PATH   Camera device for test-video (default: /dev/video0, use 'test' for pattern)

ENVIRONMENT:
  WEBRTC_LOG_LEVEL       Set libwebrtc internal logging (0=none, 3=verbose)
```

**Logging Requirements:**
- **MUST write structured logs to file** specified by `--log-path` (absolute path from Python)
- **MUST NOT write logs to STDOUT** (STDOUT is reserved for test modes)
- **MUST write panics/crashes to STDERR** (captured by Python to .err file)
- **Log format:** Same as Go version (slog text format: `time=... level=... msg=... key=value`)
- **Log rotation:** Built-in automatic rotation (desktop app, no cron):
  - **MaxSize:** 10 MB per file
  - **MaxBackups:** 5 rotated files
  - **MaxAge:** 30 days
  - **Compress:** No
  - **Implementation:** Use rotating file logger (e.g., spdlog's rotating_file_sink)

**Log Path Construction (by Python):**
- Python determines log directory via `siproxylin/utils/paths.py`
- Creates two files: `drunk-call-service.log` (structured logs) and `drunk-call-service.err` (panics)
- Spawns service with absolute path to log file via `--log-path` parameter
- Redirects stderr to `.err` file for crash capture

**Log Locations (depends on PATH_MODE):**
- `dev`: `./sip_dev_paths/logs/drunk-call-service.log`
- `dot`: `~/.siproxylin/logs/drunk-call-service.log`
- `xdg`: `~/.local/share/siproxylin/logs/drunk-call-service.log`

**C++ Service Receives:**
- Absolute path via `--log-path` (e.g., `/home/user/.siproxylin/logs/drunk-call-service.log`)
- No need to resolve paths - Python provides ready-to-use absolute path

**Log Rotation (C++ Implementation):**
- Use spdlog rotating_file_sink_mt (thread-safe, matches Go's lumberjack)
- Automatic rotation when file reaches 10MB
- Keeps 5 rotated files with numeric suffixes
- Old files deleted automatically after 30 days
- No manual rotation needed (desktop app, no cron)

**Service Lifecycle:**
- Started by Python `GoCallService` class on app launch
- Binary location: `drunk_call_service/bin/drunk-call-service-{platform}`
- Graceful shutdown via gRPC `Shutdown()` RPC or SIGTERM
- Health check: Python waits for gRPC port to be ready (5 second timeout)
- Heartbeat: Python sends `Heartbeat()` RPC every 5 seconds
- Auto-restart on crash: Handled by Python (detects process exit)

**Binary Path Migration:**
- Go binary renamed: `drunk-call-service-{platform}` → `drunk-call-service-{platform}_go`
- C++ binary uses original name: `drunk-call-service-{platform}`
- Python `GoCallService._find_go_binary()` unchanged (looks for `drunk-call-service-{platform}`)

---

## CallBridge Integration

**Python Service Manager:** `drunk_call_hook/bridge.py` class `GoCallService`

**Startup Sequence:**
1. Python `MainWindow` creates `GoCallService` singleton on app launch
2. `GoCallService.start()` finds binary via `_find_go_binary()`
3. Constructs log paths from `get_paths().log_dir` (respects PATH_MODE):
   - Structured logs: `log_dir / "drunk-call-service.log"`
   - Stderr (panics): `log_dir / "drunk-call-service.err"`
4. Spawns process: `subprocess.Popen([binary, "-log-level", "DEBUG", "-log-path", str(log_file)])`
   - Passes **absolute log path** to service (no path resolution needed in C++)
   - Redirects stderr to .err file (for crash capture)
5. Waits for gRPC port ready (5 second timeout via `grpc.channel_ready_future()`)
6. Starts heartbeat thread (sends `Heartbeat()` RPC every 5 seconds)
7. Returns success, accounts can now create `CallBridge` clients

**Heartbeat Protocol (Critical):**
- **Purpose:** Detect service crashes, keep process alive
- **Interval:** 5 seconds
- **Implementation:** Separate Python thread calls synchronous gRPC `Heartbeat()` RPC
- **Response:** Service MUST respond with empty message and log heartbeat receipt
- **Failure handling:** 3 missed heartbeats → Python restarts service
- **First function to implement:** Heartbeat RPC (before any session management)

**Shutdown Sequence:**
1. Python calls `GoCallService.stop()`
2. Stops heartbeat thread
3. Sends `Shutdown()` RPC (service MUST exit immediately with code 0)
4. If RPC fails, sends SIGTERM
5. Waits 2 seconds for process exit
6. If still running, sends SIGKILL

**Binary Discovery:**
- Dev mode: `drunk_call_service/bin/drunk-call-service-{platform}`
- XDG mode: `/usr/local/bin/drunk-call-service-{platform}` (or `$APPDIR/usr/local/bin/...` in AppImage)
- Platform: `linux`, `windows`, `darwin`
- Extension: `.exe` on Windows

**Log Files (Python-managed):**
- Structured logs: Written to path from `--log-path` parameter
- Panics/crashes: Python captures stderr to `.err` file in same directory
- Log directory determined by `get_paths().log_dir` (from paths.py)
- Python appends to logs on each service start (rotation handled externally)

**Error Handling:**
- Binary not found: Python shows error dialog, disables calls
- Startup timeout (5s): Python retries once, then fails
- Crash during call: Python detects via heartbeat, restarts service, terminates active calls

---

## Key Design Decisions

### 1. BundlePolicy: Always MaxCompat

Set `PeerConnectionInterface::kBundlePolicyMaxCompat` for all sessions to generate separate ICE transports per media. This ensures compatibility with Dino and other traditional Jingle clients.

### 2. Device Enumeration

Use LibWebRTC's built-in device APIs where available, fall back to platform-specific APIs for detailed info (friendly names, driver info).

### 3. Audio Processing

Leverage LibWebRTC's native audio processing pipeline (echo cancellation, noise suppression, gain control) via AudioProcessing module. No external DSP needed.

### 4. Video Encoding

VP8 only (initial implementation). H.264 support deferred to future phase. LibWebRTC handles encoding via VPx codec.

### 5. Proxy Support

TURN connections routed through SOCKS5/HTTP proxy using platform socket APIs with custom ICE transport factory.

### 6. Stats Collection

Map LibWebRTC's RTCStatsReport to proto GetStatsResponse. Update stats every 2 seconds during active calls.

### 7. Thread Safety

LibWebRTC requires single-threaded access to PeerConnection. Use dedicated signaling thread, post tasks via TaskQueue.

---

## Platform Abstraction Strategy

**Approach:** Thin platform layer for device-specific operations (enumeration, default device selection). LibWebRTC handles audio/video I/O cross-platform.

**Platform-Specific Code:**
- Device enumeration details (friendly names, driver info)
- Default device selection heuristics
- System audio/video API integration

**Cross-Platform Code (90%+):**
- PeerConnection lifecycle
- SDP negotiation
- ICE/DTLS/SRTP handling
- Codec negotiation
- Stats collection
- gRPC service logic

---

## Testing Strategy

**Unit Tests:** Per-module tests (session, device manager, stats)
**Integration Tests:** End-to-end call scenarios (offer/answer/ICE)
**Cross-Client Tests:** Actual calls with Dino, Conversations.im
**Platform Tests:** Verify on Windows, macOS, Linux

---

## Migration from Go Service

**Phase 1:** Implement C++ service in parallel (new binary)
**Phase 2:** Update Python GUI to use new binary path
**Phase 3:** Test with real calls, verify stats/devices work
**Phase 4:** Remove Go service code

**Rollback Plan:** Keep Go binary as fallback during transition.

---

**Last Updated:** 2026-02-25
