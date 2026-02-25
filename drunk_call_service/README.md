# Drunk Call Service (C++)

WebRTC call service for Siproxylin, using LibWebRTC for Jingle compatibility.

## Why C++ Rewrite?

The original Go/Pion implementation cannot generate non-BUNDLE SDP offers, breaking compatibility with traditional XMPP clients (Dino, Conversations.im). LibWebRTC correctly implements `BundlePolicyMaxCompat` for both offers and answers.

## Requirements

### Build Dependencies

**All Platforms:**
- CMake 3.20+
- C++17 compiler (GCC 9+, Clang 10+, MSVC 2019+)
- Protocol Buffers 3.x (`protobuf-compiler`)
- gRPC C++ (`libgrpc++-dev`, `protobuf-compiler-grpc`)

**Linux:**
- ALSA development headers (`libasound2-dev`)
- V4L2 headers (`linux-headers`)

**Optional:**
- spdlog (`libspdlog-dev`) - for structured logging with rotation
- Google Test (`libgtest-dev`) - for unit tests

### Runtime Dependencies

**LibWebRTC Prebuilt Binaries:**
- Download from: https://github.com/webrtc-sdk/libwebrtc/releases
- Extract to `third_party/libwebrtc/`
- Versions: M104+ (stable branch)

## Building

### Quick Start

```bash
# Check dependencies
make check-deps

# Build release version
make

# Build with debug symbols
make debug

# Run tests (if gtest available)
make test
```

### Manual CMake Build

```bash
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make
make install  # Installs to ../bin/
```

## Usage

```bash
./bin/drunk-call-service-linux --log-path /path/to/drunk-call-service.log --log-level INFO
```

### Command-Line Arguments

**Required:**
- `--log-path PATH` - Log file path (absolute path from Python)
- `--log-level LEVEL` - Logging level (DEBUG, INFO, WARN, ERROR)

**Optional:**
- `--port PORT` - gRPC server port (default: 50051)
- `--version` - Print version and exit
- `--help` - Show help message

**Testing:**
- `--test-devices` - Test device enumeration and exit
- `--test-video PORT` - Test video streaming on UDP port
- `--camera-device PATH` - Camera device for test-video (default: /dev/video0)

## Interface Compatibility

The gRPC interface is 100% compatible with the Go version. Python GUI code requires **no changes**.

**Proto file:** `proto/call.proto` (copied from Go service)

## Project Structure

```
drunk_call_service/
├── proto/                  # gRPC interface (protobuf)
├── include/                # Public headers
├── src/                    # Implementation
│   ├── main.cc            # Entry point
│   ├── call_server.cc     # gRPC service
│   ├── session.cc         # WebRTC session wrapper
│   ├── audio_track.cc     # Audio capture/playback
│   ├── video_track.cc     # Video capture/encoding
│   ├── device_manager.cc  # Device enumeration
│   ├── stats_collector.cc # Statistics
│   └── platform/          # Platform-specific code
├── tests/                  # Unit and integration tests
├── third_party/            # External dependencies
├── CMakeLists.txt         # Build configuration
└── Makefile               # Convenience wrapper
```

## Development Status

**Current Phase:** Skeleton implementation (Phase 0-1)

See `docs/CALLS-ON-C++-PLAN.md` for detailed roadmap.

## License

AGPL-3.0 (same as Siproxylin)
