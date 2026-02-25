module github.com/yourusername/drunk-call-service

go 1.24.0

require (
	github.com/go-gst/go-gst v1.4.0
	github.com/pion/logging v0.2.4
	github.com/pion/webrtc/v4 v4.2.1
	golang.org/x/net v0.48.0
	google.golang.org/grpc v1.68.1
	google.golang.org/protobuf v1.36.0
	gopkg.in/natefinch/lumberjack.v2 v2.2.1
)

require (
	github.com/go-gst/go-glib v1.4.0 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/mattn/go-pointer v0.0.1 // indirect
	github.com/pion/datachannel v1.5.10 // indirect
	github.com/pion/dtls/v3 v3.0.9 // indirect
	github.com/pion/ice/v4 v4.1.0 // indirect
	github.com/pion/interceptor v0.1.42 // indirect
	github.com/pion/mdns/v2 v2.1.0 // indirect
	github.com/pion/randutil v0.1.0 // indirect
	github.com/pion/rtcp v1.2.16 // indirect
	github.com/pion/rtp v1.8.27 // indirect
	github.com/pion/sctp v1.9.0 // indirect
	github.com/pion/sdp/v3 v3.0.17 // indirect
	github.com/pion/srtp/v3 v3.0.9 // indirect
	github.com/pion/stun/v3 v3.0.2 // indirect
	github.com/pion/transport/v3 v3.1.1 // indirect
	github.com/pion/turn/v4 v4.1.3 // indirect
	github.com/wlynxg/anet v0.0.5 // indirect
	golang.org/x/crypto v0.46.0 // indirect
	golang.org/x/exp v0.0.0-20240909161429-701f63a606c0 // indirect
	golang.org/x/sys v0.39.0 // indirect
	golang.org/x/text v0.32.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20240903143218-8af14fe29dc1 // indirect
)

// Use patched Pion libraries with dual-component gathering support for Conversations.im compatibility
// DISABLED: Testing if dual-component patch is causing component 1 failures
// replace github.com/pion/ice/v4 => ./pion-ice-patched

// replace github.com/pion/webrtc/v4 => ./pion-webrtc-patched
