# DrunkCallService - Go WebRTC Service

WebRTC media handling service using Pion.

## Architecture

```
Python (drunk_call_hook.bridge)
    ↓ gRPC
Go Service (this)
    ↓ Pion WebRTC
Network (RTP/DTLS-SRTP)
```

## Prerequisites

- Go installed at `/usr/local/go/` (or update PATH in scripts)
- protoc (Protocol Buffers compiler)

## Build

```bash
./build.sh
```

This will:
1. Generate gRPC code from `proto/call.proto`
2. Download dependencies (Pion, gRPC)
3. Build binary for your platform

## Run

```bash
./bin/drunk-call-service-linux --port 50051
```

## Development

**TODO:**
- [ ] Complete gRPC service implementation in `server.go`
- [ ] Add audio capture with pion/mediadevices
- [ ] Implement ICE candidate streaming
- [ ] Add STUN/TURN configuration
- [ ] Audio device enumeration API

## Dependencies

- **pion/webrtc/v4** - WebRTC implementation
- **pion/mediadevices** - Audio/video capture (TODO)
- **grpc** - RPC communication with Python
