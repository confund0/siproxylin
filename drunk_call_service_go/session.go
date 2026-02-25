package main

import (
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"log/slog"
	"net"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/go-gst/go-gst/gst"
	"github.com/go-gst/go-gst/gst/app"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
	pb "github.com/yourusername/drunk-call-service/proto"
	"golang.org/x/net/proxy"
)

// Session represents a single WebRTC peer connection
type Session struct {
	ID      string
	PeerJID string

	logger *slog.Logger
	pc     *webrtc.PeerConnection

	// Audio track and GStreamer pipeline
	audioTrack *webrtc.TrackLocalStaticSample
	pipeline   *gst.Pipeline

	// Stub pipeline for echo probe (created early so capture can reference it)
	echoProbeStubPipeline *gst.Pipeline

	// Mute state and volume element for muting microphone
	muted         bool
	volumeElement *gst.Element
	muteMu        sync.RWMutex

	// Audio device selection
	microphoneDevice string // Device name for pulsesrc (empty = default)
	speakersDevice   string // Device name for pulsesink (empty = default)

	// Video track and GStreamer pipeline
	videoTrack    *webrtc.TrackLocalStaticSample
	videoPipeline *gst.Pipeline

	// Video device selection
	cameraDevice string // Camera device path (e.g., "/dev/video0"), empty = no video

	// Privacy settings
	relayOnly bool // If true, only TURN relay candidates are sent (prevents IP leaks)

	// Audio processing settings
	audioConfig *AudioProcessingConfig

	// Event streaming channel (Go → Python)
	eventChan chan *pb.CallEvent
	eventMu   sync.RWMutex
	closed    bool

	// Context for canceling background goroutines
	ctx        context.Context
	cancelFunc context.CancelFunc

	// Bandwidth tracking for GetStats
	lastStatsTime     time.Time
	lastBytesSent     int64
	lastBytesReceived int64
}

// ProxyConfig holds SOCKS5/HTTP proxy configuration
type ProxyConfig struct {
	Host     string
	Port     int32
	Username string
	Password string
	Type     string // "SOCKS5" or "HTTP"
}

// TURNConfig holds TURN server configuration
type TURNConfig struct {
	Server   string
	Username string
	Password string
}

// AudioProcessingConfig holds WebRTC audio processing settings
type AudioProcessingConfig struct {
	EchoCancel            bool
	EchoSuppressionLevel  int32 // 0=low, 1=moderate, 2=high
	NoiseSuppression      bool
	NoiseSuppressionLevel int32 // 0=low, 1=moderate, 2=high, 3=very-high
	GainControl           bool
}

// NewSession creates a new WebRTC session
func NewSession(id string, peerJID string, micDevice string, speakersDevice string, cameraDevice string,
	proxyConfig *ProxyConfig, turnConfig *TURNConfig, relayOnly bool, audioConfig *AudioProcessingConfig,
	bundlePolicy webrtc.BundlePolicy, api *webrtc.API, logger *slog.Logger) (*Session, error) {

	// Determine ICE transport policy
	var iceTransportPolicy webrtc.ICETransportPolicy
	if relayOnly {
		iceTransportPolicy = webrtc.ICETransportPolicyRelay
		logger.Info("Using relay-only mode (privacy-first)", "session_id", id)
	} else {
		iceTransportPolicy = webrtc.ICETransportPolicyAll
		logger.Info("Using P2P mode with TURN fallback", "session_id", id)
	}

	// Determine TURN server configuration
	var turnURLs []string
	var turnUsername, turnCredential string

	if turnConfig != nil && turnConfig.Server != "" {
		// Use custom TURN server from account settings
		turnURLs = []string{turnConfig.Server}
		turnUsername = turnConfig.Username
		turnCredential = turnConfig.Password
		logger.Info("Using custom TURN server", "server", turnConfig.Server)
	} else {
		// Fallback to Jami public TURN server
		turnURLs = []string{
			"turn:turn.jami.net:3478",
			"turn:turn.jami.net:3478?transport=tcp",
		}
		turnUsername = "ring"
		turnCredential = "ring"
		logger.Info("Using default Jami TURN server")
	}

	// Prefer TCP TURN when proxy is configured (better compatibility with Tor/SOCKS5)
	if proxyConfig != nil && proxyConfig.Host != "" {
		// If proxy configured, prioritize TCP TURN
		// Filter out UDP TURN, add TCP explicitly
		tcpTurnURLs := []string{}
		for _, url := range turnURLs {
			if strings.Contains(url, "transport=tcp") {
				tcpTurnURLs = append(tcpTurnURLs, url)
			}
		}
		// If no TCP TURN found, add it
		if len(tcpTurnURLs) == 0 && len(turnURLs) > 0 {
			baseURL := strings.Split(turnURLs[0], "?")[0]
			tcpTurnURLs = append(tcpTurnURLs, baseURL+"?transport=tcp")
		}
		turnURLs = tcpTurnURLs
		logger.Info("Proxy detected, using TCP TURN only", "turn_urls", turnURLs)
	}

	// WebRTC configuration
	config := webrtc.Configuration{
		ICEServers: []webrtc.ICEServer{
			{
				URLs:       turnURLs,
				Username:   turnUsername,
				Credential: turnCredential,
			},
		},
		ICETransportPolicy: iceTransportPolicy,
		RTCPMuxPolicy:      webrtc.RTCPMuxPolicyRequire,
		BundlePolicy:       bundlePolicy,
	}

	logger.Info("Creating PeerConnection with config",
		"ice_transport_policy", config.ICETransportPolicy.String(),
		"bundle_policy", config.BundlePolicy.String(),
		"ice_servers_count", len(config.ICEServers),
		"proxy_enabled", proxyConfig != nil && proxyConfig.Host != "",
	)

	// Configure proxy dialer if proxy settings provided
	var apiWithProxy *webrtc.API = api
	if proxyConfig != nil && proxyConfig.Host != "" {
		settingEngine := webrtc.SettingEngine{}

		// CRITICAL: Re-apply settings from default API (server.go)
		// Without these, OnTrack won't fire and logging won't work properly
		settingEngine.SetHandleUndeclaredSSRCWithoutAnswer(true)
		settingEngine.LoggerFactory = &slogPionLoggerFactory{logger: logger}
		logger.Info("Re-applied critical settings for proxy API (undeclared SSRC + logging)", "session_id", id)

		// Create proxy dialer based on type
		var dialer proxy.Dialer
		var err error

		proxyAddr := fmt.Sprintf("%s:%d", proxyConfig.Host, proxyConfig.Port)

		if strings.ToUpper(proxyConfig.Type) == "SOCKS5" {
			// SOCKS5 proxy
			var auth *proxy.Auth
			if proxyConfig.Username != "" || proxyConfig.Password != "" {
				auth = &proxy.Auth{
					User:     proxyConfig.Username,
					Password: proxyConfig.Password,
				}
			}

			dialer, err = proxy.SOCKS5("tcp", proxyAddr, auth, proxy.Direct)
			if err != nil {
				return nil, fmt.Errorf("failed to create SOCKS5 proxy dialer: %w", err)
			}
			logger.Info("Created SOCKS5 proxy dialer", "proxy", proxyAddr, "has_auth", auth != nil)

		} else if strings.ToUpper(proxyConfig.Type) == "HTTP" {
			// HTTP CONNECT proxy
			// Create a custom HTTP proxy dialer
			dialer = &httpProxyDialer{
				proxyAddr: proxyAddr,
				username:  proxyConfig.Username,
				password:  proxyConfig.Password,
				logger:    logger,
			}
			logger.Info("Created HTTP CONNECT proxy dialer", "proxy", proxyAddr, "has_auth", proxyConfig.Username != "")

		} else {
			logger.Warn("Unknown proxy type, using direct connection", "proxy_type", proxyConfig.Type)
			dialer = proxy.Direct
		}

		// CRITICAL: Use SetICEProxyDialer instead of SetNet for simpler integration
		// This routes ICE traffic (including TURN/TCP) through the proxy
		settingEngine.SetICEProxyDialer(dialer)

		// CRITICAL: Force TCP-only IPv4 when proxy is enabled (Tor doesn't support UDP!)
		// This excludes UDP from ICE negotiations entirely
		settingEngine.SetNetworkTypes([]webrtc.NetworkType{
			webrtc.NetworkTypeTCP4,
		})
		logger.Info("Forced TCP4-only network type (UDP and IPv6 disabled for Tor compatibility)")

		// Create MediaEngine with codec support (same as default API)
		mediaEngine := &webrtc.MediaEngine{}
		if err := mediaEngine.RegisterDefaultCodecs(); err != nil {
			return nil, fmt.Errorf("failed to register codecs for proxy API: %w", err)
		}

		// Recreate API with custom SettingEngine + MediaEngine
		apiWithProxy = webrtc.NewAPI(
			webrtc.WithMediaEngine(mediaEngine),
			webrtc.WithSettingEngine(settingEngine),
		)
		logger.Info("WebRTC configured to use proxy for ICE/TURN connections via SetICEProxyDialer")
	}

	// Create peer connection
	pc, err := apiWithProxy.NewPeerConnection(config)
	if err != nil {
		return nil, fmt.Errorf("failed to create peer connection: %w", err)
	}

	// Verify configuration was applied
	logger.Info("PeerConnection created successfully",
		"session_id", id,
		"config_ice_policy", config.ICETransportPolicy.String(),
	)

	// Create cancelable context for background goroutines
	ctx, cancel := context.WithCancel(context.Background())

	session := &Session{
		ID:               id,
		PeerJID:          peerJID,
		logger:           logger,
		pc:               pc,
		microphoneDevice: micDevice,
		speakersDevice:   speakersDevice,
		cameraDevice:     cameraDevice,
		relayOnly:        relayOnly,
		audioConfig:      audioConfig,
		eventChan:        make(chan *pb.CallEvent, 100), // Buffered channel for events
		closed:           false,
		ctx:              ctx,
		cancelFunc:       cancel,
	}

	// Setup event handlers
	session.setupHandlers()

	return session, nil
}

// setupHandlers configures WebRTC event handlers
func (s *Session) setupHandlers() {
	// ICE gathering state changes
	s.pc.OnICEGatheringStateChange(func(state webrtc.ICEGatheringState) {
		s.logger.Info("ICE gathering state changed",
			"session_id", s.ID,
			"state", state.String(),
		)
	})

	// PeerConnection state changes (overall state machine)
	s.pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		s.logger.Info("PeerConnection state changed",
			"session_id", s.ID,
			"state", state.String(),
		)

		// Start ICE statistics logging when stuck in connecting
		if state == webrtc.PeerConnectionStateConnecting {
			s.logger.Info("Starting ICE statistics monitoring",
				"session_id", s.ID,
			)
			go s.logICEStats(s.ctx)
		}
	})

	// Signaling state changes (offer/answer state machine)
	s.pc.OnSignalingStateChange(func(state webrtc.SignalingState) {
		s.logger.Info("Signaling state changed",
			"session_id", s.ID,
			"state", state.String(),
		)
	})

	// SCTP/DataChannel state (not used for audio-only, but good for debugging)
	s.pc.OnDataChannel(func(dc *webrtc.DataChannel) {
		s.logger.Info("DataChannel opened (unexpected for audio-only)",
			"session_id", s.ID,
			"label", dc.Label(),
		)
	})

	// ICE connection state changes
	s.pc.OnICEConnectionStateChange(func(state webrtc.ICEConnectionState) {
		s.logger.Info("ICE connection state changed",
			"session_id", s.ID,
			"state", state.String(),
		)

		// Convert WebRTC state to proto enum
		var protoState pb.ConnectionStateEvent_State
		switch state {
		case webrtc.ICEConnectionStateNew:
			protoState = pb.ConnectionStateEvent_NEW
		case webrtc.ICEConnectionStateChecking:
			protoState = pb.ConnectionStateEvent_CHECKING
		case webrtc.ICEConnectionStateConnected:
			protoState = pb.ConnectionStateEvent_CONNECTED
		case webrtc.ICEConnectionStateCompleted:
			protoState = pb.ConnectionStateEvent_COMPLETED
		case webrtc.ICEConnectionStateFailed:
			protoState = pb.ConnectionStateEvent_FAILED
		case webrtc.ICEConnectionStateDisconnected:
			protoState = pb.ConnectionStateEvent_DISCONNECTED
		case webrtc.ICEConnectionStateClosed:
			protoState = pb.ConnectionStateEvent_CLOSED
		default:
			protoState = pb.ConnectionStateEvent_NEW
		}

		// Send state change event to Python
		s.sendEvent(&pb.CallEvent{
			SessionId: s.ID,
			Event: &pb.CallEvent_ConnectionState{
				ConnectionState: &pb.ConnectionStateEvent{
					State: protoState,
				},
			},
		})
	})

	// ICE candidate gathering
	s.pc.OnICECandidate(func(candidate *webrtc.ICECandidate) {
		if candidate == nil {
			s.logger.Info("ICE gathering complete", "session_id", s.ID)
			return
		}

		// Enhanced candidate logging with details INCLUDING component
		candidateInit := candidate.ToJSON()
		s.logger.Info("New ICE candidate",
			"session_id", s.ID,
			"type", candidate.Typ.String(),
			"protocol", candidate.Protocol.String(),
			"address", candidate.Address,
			"port", candidate.Port,
			"priority", candidate.Priority,
			"component", candidate.Component,
			"candidate_string", candidateInit.Candidate,
		)

		// PRIVACY: Filter out non-relay candidates in relay-only mode (defense in depth)
		if s.relayOnly && candidate.Typ != webrtc.ICECandidateTypeRelay {
			s.logger.Warn("Dropping non-relay ICE candidate in relay-only mode (privacy protection)",
				"session_id", s.ID,
				"type", candidate.Typ.String(),
				"address", candidate.Address,
			)
			return
		}

		// Send ICE candidate to Python
		// Pion now gathers BOTH component 1 and component 2 via patched ICE library
		s.sendEvent(&pb.CallEvent{
			SessionId: s.ID,
			Event: &pb.CallEvent_IceCandidate{
				IceCandidate: &pb.ICECandidateEvent{
					Candidate:     candidateInit.Candidate,
					SdpMid:        *candidateInit.SDPMid,
					SdpMlineIndex: int32(*candidateInit.SDPMLineIndex),
				},
			},
		})
	})

	// Track handling (remote audio and video)
	s.pc.OnTrack(func(track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		s.logger.Info("Got remote track",
			"session_id", s.ID,
			"kind", track.Kind(),
			"codec", track.Codec().MimeType,
			"ssrc", track.SSRC(),
		)

		// Handle video tracks
		if track.Kind() == webrtc.RTPCodecTypeVideo {
			s.logger.Info("Starting video playback for remote track",
				"session_id", s.ID,
			)
			go s.playVideoTrack(track)
			return
		}

		// Handle audio tracks
		if track.Kind() == webrtc.RTPCodecTypeAudio {
			s.logger.Info("Starting audio playback for remote track",
				"session_id", s.ID,
			)
			go s.playAudioTrack(track)
			return
		}

		// Unknown track type
		s.logger.Warn("Received unknown track type, ignoring",
			"session_id", s.ID,
			"kind", track.Kind(),
		)
	})

	// OnNegotiationNeeded - fires when we need to renegotiate
	s.pc.OnNegotiationNeeded(func() {
		s.logger.Info("Negotiation needed",
			"session_id", s.ID,
		)
	})
}

// CreateOffer generates an SDP offer
func (s *Session) CreateOffer() (string, error) {
	// Add local audio track before creating offer
	if err := s.addAudioTrack(); err != nil {
		return "", fmt.Errorf("failed to add audio track: %w", err)
	}

	// Add video track if camera configured (graceful degradation)
	if s.cameraDevice != "" {
		if err := s.addVideoTrack(); err != nil {
			// Log warning but don't fail - continue with audio-only call
			s.logger.Warn("Failed to add video track, continuing audio-only",
				"session_id", s.ID,
				"error", err,
			)
		}
	}

	offer, err := s.pc.CreateOffer(nil)
	if err != nil {
		return "", fmt.Errorf("failed to create offer: %w", err)
	}

	if err := s.pc.SetLocalDescription(offer); err != nil {
		return "", fmt.Errorf("failed to set local description: %w", err)
	}

	// Filter component 2 candidates (Pion bug workaround)
	// Pion generates component 2 with DUPLICATE PORTS when rtcp-mux is enabled
	// This is invalid per RFC 8445 - only component 1 should exist with rtcp-mux
	// See: https://github.com/pion/webrtc/issues/2731
	filteredSDP := s.filterComponent2Candidates(offer.SDP)

	// Log local SDP offer validation
	s.logger.Info("Created SDP offer", "session_id", s.ID)
	s.validateSDP(filteredSDP, "local_offer")

	// TESTING: Log BUNDLE and ICE credentials to verify MaxCompat behavior
	s.analyzeBundleAndICE(filteredSDP, "OFFER")

	return filteredSDP, nil
}

// CreateAnswer generates an SDP answer
func (s *Session) CreateAnswer(remoteSDP string) (string, error) {
	// Log remote SDP validation
	s.logger.Info("Received remote SDP offer",
		"session_id", s.ID,
		"sdp_length", len(remoteSDP),
	)
	s.validateSDP(remoteSDP, "remote_offer")

	// Set remote description
	err := s.pc.SetRemoteDescription(webrtc.SessionDescription{
		Type: webrtc.SDPTypeOffer,
		SDP:  remoteSDP,
	})
	if err != nil {
		return "", fmt.Errorf("failed to set remote description: %w", err)
	}

	// Add local audio track before creating answer
	if err := s.addAudioTrack(); err != nil {
		return "", fmt.Errorf("failed to add audio track: %w", err)
	}

	// Add video track if camera configured (graceful degradation)
	if s.cameraDevice != "" {
		if err := s.addVideoTrack(); err != nil {
			// Log warning but don't fail - continue with audio-only call
			s.logger.Warn("Failed to add video track, continuing audio-only",
				"session_id", s.ID,
				"error", err,
			)
		}
	}

	answer, err := s.pc.CreateAnswer(nil)
	if err != nil {
		return "", fmt.Errorf("failed to create answer: %w", err)
	}

	// CRITICAL: Set up gathering complete handler BEFORE SetLocalDescription
	// This ensures we catch the gathering complete event
	gatherComplete := make(chan struct{})
	s.pc.OnICEGatheringStateChange(func(state webrtc.ICEGatheringState) {
		s.logger.Debug("ICE gathering state changed in answer flow", "session_id", s.ID, "state", state.String())
		if state == webrtc.ICEGatheringStateComplete {
			select {
			case <-gatherComplete:
				// Already closed
			default:
				close(gatherComplete)
			}
		}
	})

	if err := s.pc.SetLocalDescription(answer); err != nil {
		return "", fmt.Errorf("failed to set local description: %w", err)
	}

	// Wait for ICE gathering to complete before sending answer
	// This ensures candidates are included in the SDP for Conversations.im compatibility
	s.logger.Info("Waiting for ICE gathering to complete before sending answer", "session_id", s.ID)
	select {
	case <-gatherComplete:
		s.logger.Info("ICE gathering completed, answer includes all candidates", "session_id", s.ID)
	case <-time.After(3 * time.Second):
		s.logger.Warn("ICE gathering timeout (3s), sending answer with partial candidates", "session_id", s.ID)
	}

	// Get the updated local description with gathered candidates
	finalAnswer := s.pc.LocalDescription()

	// Filter component 2 candidates (Pion bug workaround)
	// Pion generates component 2 with DUPLICATE PORTS when rtcp-mux is enabled
	// This is invalid per RFC 8445 - only component 1 should exist with rtcp-mux
	// See: https://github.com/pion/webrtc/issues/2731
	filteredSDP := s.filterComponent2Candidates(finalAnswer.SDP)

	// Log local SDP answer validation
	s.logger.Info("Created SDP answer", "session_id", s.ID)
	s.validateSDP(filteredSDP, "local_answer")

	return filteredSDP, nil
}

// SetRemoteDescription sets remote SDP description (for outgoing calls)
func (s *Session) SetRemoteDescription(remoteSDP string, sdpType string) error {
	s.logger.Info("Setting remote description",
		"session_id", s.ID,
		"sdp_type", sdpType,
		"sdp_length", len(remoteSDP),
	)
	s.validateSDP(remoteSDP, fmt.Sprintf("remote_%s", sdpType))

	// Map string type to WebRTC SDPType
	var descType webrtc.SDPType
	switch sdpType {
	case "offer":
		descType = webrtc.SDPTypeOffer
	case "answer":
		descType = webrtc.SDPTypeAnswer
	default:
		return fmt.Errorf("invalid SDP type: %s (expected 'offer' or 'answer')", sdpType)
	}

	// Set remote description
	err := s.pc.SetRemoteDescription(webrtc.SessionDescription{
		Type: descType,
		SDP:  remoteSDP,
	})
	if err != nil {
		return fmt.Errorf("failed to set remote description: %w", err)
	}

	s.logger.Info("Successfully set remote description", "session_id", s.ID)
	return nil
}

// AddICECandidate adds a remote ICE candidate
func (s *Session) AddICECandidate(candidate string) error {
	iceCandidate := webrtc.ICECandidateInit{
		Candidate: candidate,
	}

	// Log candidate (truncate if too long)
	logCandidate := candidate
	if len(candidate) > 60 {
		logCandidate = candidate[:60] + "..."
	}
	s.logger.Info("Adding remote ICE candidate",
		"session_id", s.ID,
		"candidate", logCandidate,
	)

	if err := s.pc.AddICECandidate(iceCandidate); err != nil {
		s.logger.Error("Failed to add remote ICE candidate",
			"session_id", s.ID,
			"error", err,
		)
		return fmt.Errorf("failed to add ICE candidate: %w", err)
	}

	s.logger.Info("Successfully added remote ICE candidate to Pion", "session_id", s.ID)
	return nil
}

// Close terminates the peer connection and GStreamer pipeline
func (s *Session) Close() {
	// Cancel all background goroutines (ICE stats, audio readers, etc.)
	if s.cancelFunc != nil {
		s.cancelFunc()
		s.logger.Debug("Canceled background goroutines", "session_id", s.ID)
	}

	s.eventMu.Lock()
	if !s.closed {
		s.closed = true
		close(s.eventChan)
	}
	s.eventMu.Unlock()

	// Stop GStreamer pipelines
	if s.pipeline != nil {
		s.pipeline.SetState(gst.StateNull)
		s.logger.Debug("GStreamer pipeline stopped", "session_id", s.ID)
	}

	// Stop echo probe stub pipeline if it exists
	if s.echoProbeStubPipeline != nil {
		s.echoProbeStubPipeline.SetState(gst.StateNull)
		s.logger.Debug("Echo probe stub pipeline stopped", "session_id", s.ID)
	}

	// Close peer connection
	if s.pc != nil {
		s.pc.Close()
		s.logger.Info("Session closed", "session_id", s.ID)
	}
}

// sendEvent sends an event to the event channel (non-blocking)
func (s *Session) sendEvent(event *pb.CallEvent) {
	s.eventMu.RLock()
	defer s.eventMu.RUnlock()

	if s.closed {
		s.logger.Debug("Skipping event send (session closed)", "session_id", s.ID)
		return
	}

	// Non-blocking send to prevent goroutine deadlock
	select {
	case s.eventChan <- event:
		// Event sent successfully
	default:
		s.logger.Warn("Event channel full, dropping event", "session_id", s.ID)
	}
}

// GetEventChannel returns the event channel for streaming to gRPC
func (s *Session) GetEventChannel() <-chan *pb.CallEvent {
	return s.eventChan
}

// addAudioTrack creates and adds an audio track using GStreamer
func (s *Session) addAudioTrack() error {
	// Skip if track already exists
	if s.audioTrack != nil {
		s.logger.Debug("Audio track already exists", "session_id", s.ID)
		return nil
	}

	s.logger.Info("Setting up GStreamer audio pipeline", "session_id", s.ID)

	// Create Opus audio track for WebRTC
	track, err := webrtc.NewTrackLocalStaticSample(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus},
		"audio",
		"pion-audio",
	)
	if err != nil {
		s.logger.Error("Failed to create audio track", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to create audio track: %w", err)
	}

	s.audioTrack = track

	// Add track to peer connection
	rtpSender, err := s.pc.AddTrack(track)
	if err != nil {
		s.logger.Error("Failed to add track to peer connection", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to add track: %w", err)
	}

	s.logger.Info("Audio track added to peer connection", "session_id", s.ID)

	// Handle RTCP packets (sender reports, etc.)
	go func() {
		rtcpBuf := make([]byte, 1500)
		for {
			if _, _, rtcpErr := rtpSender.Read(rtcpBuf); rtcpErr != nil {
				if rtcpErr == io.EOF {
					return
				}
				return
			}
		}
	}()

	// Create GStreamer pipeline for audio capture
	// Use pulsesrc with device selection if specified, otherwise autoaudiosrc
	var audioSrc string
	if s.microphoneDevice != "" {
		audioSrc = fmt.Sprintf("pulsesrc device=%s", s.microphoneDevice)
		s.logger.Info("Using selected microphone device", "session_id", s.ID, "device", s.microphoneDevice)
	} else {
		audioSrc = "autoaudiosrc"
		s.logger.Info("Using default microphone device", "session_id", s.ID)
	}

	// If echo cancellation is enabled, create stub pipeline with probe FIRST
	// This ensures the probe element exists before capture pipeline references it
	if s.audioConfig != nil && s.audioConfig.EchoCancel {
		stubPipelineStr := "fakesrc ! webrtcechoprobe name=webrtcechoprobe0 ! fakesink"
		stubPipeline, err := gst.NewPipelineFromString(stubPipelineStr)
		if err != nil {
			s.logger.Error("Failed to create echo probe stub pipeline", "session_id", s.ID, "error", err)
			return fmt.Errorf("failed to create echo probe stub: %w", err)
		}

		// Start the stub pipeline so the probe element is active
		if err := stubPipeline.SetState(gst.StatePlaying); err != nil {
			s.logger.Error("Failed to start echo probe stub pipeline", "session_id", s.ID, "error", err)
			return fmt.Errorf("failed to start echo probe stub: %w", err)
		}

		s.echoProbeStubPipeline = stubPipeline
		s.logger.Info("Created echo probe stub pipeline", "session_id", s.ID)
	}

	// Build webrtcdsp element with audio processing settings
	var pipelineStr string
	if s.audioConfig != nil && (s.audioConfig.EchoCancel || s.audioConfig.NoiseSuppression || s.audioConfig.GainControl) {
		// Map level values to webrtcdsp enum values
		echoLevelMap := []string{"low", "moderate", "high"}
		noiseLevelMap := []string{"low", "moderate", "high", "very-high"}

		echoLevel := "moderate"
		if s.audioConfig.EchoSuppressionLevel >= 0 && int(s.audioConfig.EchoSuppressionLevel) < len(echoLevelMap) {
			echoLevel = echoLevelMap[s.audioConfig.EchoSuppressionLevel]
		}

		noiseLevel := "moderate"
		if s.audioConfig.NoiseSuppressionLevel >= 0 && int(s.audioConfig.NoiseSuppressionLevel) < len(noiseLevelMap) {
			noiseLevel = noiseLevelMap[s.audioConfig.NoiseSuppressionLevel]
		}

		dspElement := fmt.Sprintf("webrtcdsp probe=webrtcechoprobe0 echo-cancel=%t echo-suppression-level=%s noise-suppression=%t noise-suppression-level=%s gain-control=%t",
			s.audioConfig.EchoCancel, echoLevel,
			s.audioConfig.NoiseSuppression, noiseLevel,
			s.audioConfig.GainControl)

		s.logger.Info("Audio processing enabled",
			"session_id", s.ID,
			"echo_cancel", s.audioConfig.EchoCancel,
			"echo_level", echoLevel,
			"noise_suppression", s.audioConfig.NoiseSuppression,
			"noise_level", noiseLevel,
			"gain_control", s.audioConfig.GainControl)

		pipelineStr = fmt.Sprintf("%s ! audioconvert ! audioresample ! %s ! volume name=volume ! opusenc ! appsink name=appsink", audioSrc, dspElement)
	} else {
		pipelineStr = fmt.Sprintf("%s ! audioconvert ! audioresample ! volume name=volume ! opusenc ! appsink name=appsink", audioSrc)
	}

	s.logger.Info("Creating GStreamer pipeline", "session_id", s.ID, "pipeline", pipelineStr)

	pipeline, err := gst.NewPipelineFromString(pipelineStr)
	if err != nil {
		s.logger.Error("Failed to create GStreamer pipeline", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to create pipeline: %w", err)
	}

	s.pipeline = pipeline

	// Get volume element for mute control
	volumeElement, err := pipeline.GetElementByName("volume")
	if err != nil {
		s.logger.Error("Failed to get volume element", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to get volume: %w", err)
	}
	s.volumeElement = volumeElement

	// Apply initial mute state
	s.muteMu.RLock()
	initialMuted := s.muted
	s.muteMu.RUnlock()
	if err := s.volumeElement.SetProperty("mute", initialMuted); err != nil {
		s.logger.Error("Failed to set initial mute state", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to set initial mute: %w", err)
	}
	s.logger.Info("Applied initial mute state to volume element", "session_id", s.ID, "muted", initialMuted)

	// Get appsink element
	appsinkElement, err := pipeline.GetElementByName("appsink")
	if err != nil {
		s.logger.Error("Failed to get appsink element", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to get appsink: %w", err)
	}

	appsink := app.SinkFromElement(appsinkElement)

	// Start pipeline
	s.logger.Info("Starting GStreamer pipeline", "session_id", s.ID)
	if err := pipeline.SetState(gst.StatePlaying); err != nil {
		s.logger.Error("Failed to start pipeline", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to start pipeline: %w", err)
	}

	// Start goroutine to read samples from appsink and write to WebRTC track
	go func() {
		s.logger.Info("Starting audio sample reader", "session_id", s.ID)

		for {
			// Pull sample from appsink
			sample := appsink.PullSample()
			if sample == nil {
				s.logger.Debug("No more samples (pipeline stopped)", "session_id", s.ID)
				return
			}

			// Get buffer from sample
			buffer := sample.GetBuffer()
			if buffer == nil {
				continue
			}

			// Read buffer data
			samples := buffer.Map(gst.MapRead).Bytes()
			if len(samples) == 0 {
				buffer.Unmap()
				continue
			}

			// Get duration
			duration := buffer.Duration()

			// Write to WebRTC track
			if err := track.WriteSample(media.Sample{
				Data:     samples,
				Duration: time.Duration(duration),
			}); err != nil {
				s.logger.Error("Failed to write sample to track", "session_id", s.ID, "error", err)
				buffer.Unmap()
				return
			}

			buffer.Unmap()
		}
	}()

	s.logger.Info("GStreamer audio capture started", "session_id", s.ID)
	return nil
}

// playAudioTrack plays incoming audio from remote peer through speakers
func (s *Session) playAudioTrack(track *webrtc.TrackRemote) {
	s.logger.Info("Setting up audio playback", "session_id", s.ID, "codec", track.Codec().MimeType)

	// Clean up echo probe stub pipeline if it exists
	// The real playback pipeline will create its own probe
	if s.echoProbeStubPipeline != nil {
		s.logger.Info("Cleaning up echo probe stub pipeline", "session_id", s.ID)
		s.echoProbeStubPipeline.SetState(gst.StateNull)
		s.echoProbeStubPipeline = nil
	}

	// Create GStreamer playback pipeline: appsrc ! opusdec ! audioconvert ! sink
	// Use pulsesink with device selection if specified, otherwise autoaudiosink
	var audioSink string
	if s.speakersDevice != "" {
		audioSink = fmt.Sprintf("pulsesink device=%s", s.speakersDevice)
		s.logger.Info("Using selected speakers device", "session_id", s.ID, "device", s.speakersDevice)
	} else {
		audioSink = "autoaudiosink"
		s.logger.Info("Using default speakers device", "session_id", s.ID)
	}

	// CRITICAL: Must set caps on appsrc for opusdec to know the format
	// Add webrtcechoprobe if echo cancellation is enabled (probe captures playback for echo cancellation)
	var probeElement string
	if s.audioConfig != nil && s.audioConfig.EchoCancel {
		probeElement = "webrtcechoprobe name=webrtcechoprobe0 ! "
		s.logger.Info("Adding echo probe to playback pipeline", "session_id", s.ID)
	}

	pipelineStr := fmt.Sprintf("appsrc name=appsrc format=time is-live=true caps=audio/x-opus,channel-mapping-family=0 ! opusdec ! audioconvert ! audioresample ! %s%s", probeElement, audioSink)
	s.logger.Info("Creating playback pipeline", "session_id", s.ID, "pipeline", pipelineStr)

	pipeline, err := gst.NewPipelineFromString(pipelineStr)
	if err != nil {
		s.logger.Error("Failed to create playback pipeline", "session_id", s.ID, "error", err)
		return
	}

	// Get appsrc element
	appsrcElement, err := pipeline.GetElementByName("appsrc")
	if err != nil {
		s.logger.Error("Failed to get appsrc element", "session_id", s.ID, "error", err)
		return
	}

	appsrc := app.SrcFromElement(appsrcElement)

	// Watch for GStreamer bus messages (errors, warnings, state changes)
	// This is CRITICAL for debugging audio issues
	go func() {
		bus := pipeline.GetPipelineBus()
		for {
			msg := bus.TimedPop(gst.ClockTime(1 * time.Second))
			if msg == nil {
				continue
			}

			switch msg.Type() {
			case gst.MessageError:
				err := msg.ParseError()
				s.logger.Error("GStreamer ERROR", "session_id", s.ID, "error", err.Error(), "debug", err.DebugString())
			case gst.MessageWarning:
				err := msg.ParseWarning()
				s.logger.Warn("GStreamer WARNING", "session_id", s.ID, "warning", err.Error(), "debug", err.DebugString())
			case gst.MessageStateChanged:
				if msg.Source() == pipeline.GetName() {
					_, newState := msg.ParseStateChanged()
					s.logger.Info("Pipeline state changed", "session_id", s.ID, "state", newState.String())
				}
			case gst.MessageEOS:
				s.logger.Info("GStreamer EOS (end of stream)", "session_id", s.ID)
				return
			}
		}
	}()

	// Start pipeline
	s.logger.Info("Starting playback pipeline", "session_id", s.ID)
	if err := pipeline.SetState(gst.StatePlaying); err != nil {
		s.logger.Error("Failed to start playback pipeline", "session_id", s.ID, "error", err)
		return
	}

	s.logger.Info("Audio playback started", "session_id", s.ID)

	// Read RTP packets and extract Opus payload
	packetCount := 0
	for {
		rtpPacket, _, err := track.ReadRTP()
		if err != nil {
			if err == io.EOF {
				s.logger.Debug("Remote track ended", "session_id", s.ID, "packets_received", packetCount)
			} else {
				s.logger.Error("Error reading RTP packet", "session_id", s.ID, "error", err, "packets_received", packetCount)
			}
			break
		}

		packetCount++

		// Log first few packets and then periodically
		if packetCount <= 5 || packetCount%100 == 0 {
			s.logger.Info("Received RTP packet", "session_id", s.ID, "packet_num", packetCount, "payload_size", len(rtpPacket.Payload))
		}

		// Extract Opus payload (strip RTP header) and push to GStreamer
		// rtpPacket.Payload contains the raw Opus data
		buffer := gst.NewBufferFromBytes(rtpPacket.Payload)
		flowRet := appsrc.PushBuffer(buffer)
		if flowRet != gst.FlowOK {
			s.logger.Error("Failed to push buffer to appsrc", "session_id", s.ID, "flow_return", flowRet, "packets_pushed", packetCount)
			break
		}
	}

	// Cleanup
	s.logger.Info("Stopping audio playback", "session_id", s.ID)
	pipeline.SetState(gst.StateNull)
}

// filterComponent2Candidates removes component 2 ICE candidates from SDP
// Workaround for Pion bug where LocalDescription() includes component 2 even with RTCPMuxPolicyRequire
func (s *Session) filterComponent2Candidates(sdp string) string {
	lines := strings.Split(sdp, "\r\n")
	filtered := make([]string, 0, len(lines))
	removedCount := 0

	for _, line := range lines {
		// Check if line is an ICE candidate with component 2
		// Format: a=candidate:foundation component protocol priority ip port typ type ...
		// Example: a=candidate:1723050305 2 udp 16777215 51.222.138.120 17982 typ relay ...
		if strings.HasPrefix(line, "a=candidate:") {
			parts := strings.Fields(line)
			// parts[0] = "a=candidate:foundation"
			// parts[1] = component (1 or 2)
			if len(parts) >= 2 && parts[1] == "2" {
				s.logger.Debug("Filtered component 2 candidate from SDP",
					"session_id", s.ID,
					"candidate", line,
				)
				removedCount++
				continue // Skip this line
			}
		}
		filtered = append(filtered, line)
	}

	if removedCount > 0 {
		s.logger.Info("Removed component 2 candidates from SDP",
			"session_id", s.ID,
			"removed_count", removedCount,
		)
	}

	return strings.Join(filtered, "\r\n")
}

// validateSDP logs SDP statistics for debugging
func (s *Session) validateSDP(sdp string, sdpType string) {
	lines := strings.Split(sdp, "\n")
	mediaCount := 0
	iceCount := 0
	var iceUfrag, icePwd string

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "m=") {
			mediaCount++
		} else if strings.HasPrefix(line, "a=candidate:") {
			iceCount++
		} else if strings.HasPrefix(line, "a=ice-ufrag:") {
			iceUfrag = strings.TrimPrefix(line, "a=ice-ufrag:")
		} else if strings.HasPrefix(line, "a=ice-pwd:") {
			icePwd = strings.TrimPrefix(line, "a=ice-pwd:")
		}
	}

	s.logger.Info("SDP validation",
		"session_id", s.ID,
		"type", sdpType,
		"media_sections", mediaCount,
		"ice_candidates", iceCount,
		"ice_ufrag", iceUfrag,
		"ice_pwd_present", icePwd != "",
	)

	// Warn if missing critical fields
	if iceUfrag == "" || icePwd == "" {
		s.logger.Warn("SDP missing ICE credentials",
			"session_id", s.ID,
			"type", sdpType,
			"ufrag_present", iceUfrag != "",
			"pwd_present", icePwd != "",
		)
	}
}

// analyzeBundleAndICE logs BUNDLE group and ICE credentials per media section
// This helps verify if BundlePolicyMaxCompat is working (should have different ufrag per m-line)
func (s *Session) analyzeBundleAndICE(sdp string, sdpType string) {
	lines := strings.Split(sdp, "\n")

	var bundleGroup string
	var currentMedia string
	mediaIceInfo := make(map[string]map[string]string) // media -> {ufrag, pwd}
	mediaIndex := 0

	for _, line := range lines {
		line = strings.TrimSpace(line)

		// Capture BUNDLE group line
		if strings.HasPrefix(line, "a=group:BUNDLE") {
			bundleGroup = strings.TrimPrefix(line, "a=group:BUNDLE ")
		}

		// Track which media section we're in
		if strings.HasPrefix(line, "m=") {
			parts := strings.Fields(line)
			if len(parts) >= 1 {
				currentMedia = fmt.Sprintf("%s[%d]", strings.TrimPrefix(parts[0], "m="), mediaIndex)
				mediaIndex++
				mediaIceInfo[currentMedia] = make(map[string]string)
			}
		}

		// Capture ICE credentials per media section
		if currentMedia != "" {
			if strings.HasPrefix(line, "a=ice-ufrag:") {
				mediaIceInfo[currentMedia]["ufrag"] = strings.TrimPrefix(line, "a=ice-ufrag:")
			} else if strings.HasPrefix(line, "a=ice-pwd:") {
				mediaIceInfo[currentMedia]["pwd"] = strings.TrimPrefix(line, "a=ice-pwd:")
			}
		}
	}

	// Log BUNDLE status
	if bundleGroup != "" {
		s.logger.Warn("⚠️  SDP contains BUNDLE group - Dino will REJECT this!",
			"session_id", s.ID,
			"type", sdpType,
			"bundle_group", bundleGroup,
		)
	} else {
		s.logger.Info("✅ No BUNDLE group found - compatible with Dino",
			"session_id", s.ID,
			"type", sdpType,
		)
	}

	// Log ICE credentials per media
	s.logger.Info("ICE credentials per media section",
		"session_id", s.ID,
		"type", sdpType,
		"media_count", len(mediaIceInfo),
	)

	// Check if all ufrag values are unique (required for non-BUNDLE)
	ufrags := make(map[string]int)
	for media, ice := range mediaIceInfo {
		ufrag := ice["ufrag"]
		pwd := ice["pwd"]
		ufrags[ufrag]++

		s.logger.Info("Media ICE info",
			"session_id", s.ID,
			"media", media,
			"ice_ufrag", ufrag,
			"ice_pwd_len", len(pwd),
		)
	}

	// Verify uniqueness
	uniqueUfrags := len(ufrags) == len(mediaIceInfo)
	if uniqueUfrags && len(mediaIceInfo) > 1 {
		s.logger.Info("✅ All media sections have UNIQUE ICE credentials - MaxCompat working!",
			"session_id", s.ID,
			"type", sdpType,
		)
	} else if len(mediaIceInfo) > 1 {
		s.logger.Warn("⚠️  Media sections share SAME ICE credentials - MaxCompat NOT working!",
			"session_id", s.ID,
			"type", sdpType,
			"unique_ufrags", len(ufrags),
			"total_media", len(mediaIceInfo),
		)
	}
}

// logICEStats periodically logs ICE candidate pair statistics
func (s *Session) logICEStats(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for i := 0; i < 6; i++ { // Log for 30 seconds max
		select {
		case <-ctx.Done():
			// Session closed, stop monitoring
			s.logger.Info("ICE statistics monitoring stopped (session closed)",
				"session_id", s.ID,
			)
			return
		case <-ticker.C:
			// Continue with stats collection
		}

		stats := s.pc.GetStats()

		// Get transceiver to check ICE role and DTLS state
		transceivers := s.pc.GetTransceivers()
		if len(transceivers) > 0 {
			transport := transceivers[0].Sender().Transport()
			if transport != nil {
				// ICE role
				iceTransport := transport.ICETransport()
				if iceTransport != nil {
					role := iceTransport.Role()
					s.logger.Info("ICE role",
						"session_id", s.ID,
						"role", role.String(),
					)
				}

				// DTLS state
				dtlsState := transport.State()
				s.logger.Info("DTLS transport state",
					"session_id", s.ID,
					"state", dtlsState.String(),
				)
			}
		}

		// Count candidates by type
		localCandCount := make(map[string]int)
		remoteCandCount := make(map[string]int)
		pairCount := 0
		succeededPairs := 0
		nominatedPairs := 0

		for _, stat := range stats {
			switch v := stat.(type) {
			case webrtc.ICECandidatePairStats:
				pairCount++
				if v.State == "succeeded" {
					succeededPairs++
				}
				if v.Nominated {
					nominatedPairs++
				}

				// Log all pairs with detailed info
				s.logger.Info("ICE candidate pair",
					"session_id", s.ID,
					"local_id", v.LocalCandidateID,
					"remote_id", v.RemoteCandidateID,
					"state", v.State,
					"nominated", v.Nominated,
					"bytes_sent", v.BytesSent,
					"bytes_received", v.BytesReceived,
					"requests_sent", v.RequestsSent,
					"requests_received", v.RequestsReceived,
					"responses_sent", v.ResponsesSent,
					"responses_received", v.ResponsesReceived,
				)

			case webrtc.ICECandidateStats:
				if v.Type == webrtc.StatsTypeLocalCandidate {
					candType := string(v.CandidateType)
					localCandCount[candType]++
					s.logger.Debug("Local ICE candidate stats",
						"session_id", s.ID,
						"type", v.CandidateType,
						"protocol", v.Protocol,
						"address", v.IP,
						"port", v.Port,
					)
				} else if v.Type == webrtc.StatsTypeRemoteCandidate {
					candType := string(v.CandidateType)
					remoteCandCount[candType]++
					s.logger.Debug("Remote ICE candidate stats",
						"session_id", s.ID,
						"type", v.CandidateType,
						"protocol", v.Protocol,
						"address", v.IP,
						"port", v.Port,
					)
				}
			}
		}

		s.logger.Info("ICE statistics summary",
			"session_id", s.ID,
			"total_pairs", pairCount,
			"succeeded_pairs", succeededPairs,
			"nominated_pairs", nominatedPairs,
			"local_candidates", localCandCount,
			"remote_candidates", remoteCandCount,
			"ice_state", s.pc.ICEConnectionState().String(),
			"peer_state", s.pc.ConnectionState().String(),
		)

		// Stop if connected or failed
		state := s.pc.ConnectionState()
		if state == webrtc.PeerConnectionStateConnected || state == webrtc.PeerConnectionStateFailed {
			s.logger.Info("Stopping ICE statistics monitoring",
				"session_id", s.ID,
				"final_state", state.String(),
			)
			break
		}
	}
}

// GetStats collects and returns call statistics
func (s *Session) GetStats() *pb.GetStatsResponse {
	stats := s.pc.GetStats()

	response := &pb.GetStatsResponse{
		ConnectionState:    s.pc.ConnectionState().String(),
		IceConnectionState: s.pc.ICEConnectionState().String(),
		IceGatheringState:  s.pc.ICEGatheringState().String(),
		BytesSent:          0,
		BytesReceived:      0,
		BandwidthKbps:      0,
		LocalCandidates:    []string{},
		RemoteCandidates:   []string{},
		ConnectionType:     "Unknown",
	}

	// Track unique candidate IPs
	localIPs := make(map[string]bool)
	remoteIPs := make(map[string]bool)
	localCandidates := make(map[string]webrtc.ICECandidateStats)
	remoteCandidates := make(map[string]webrtc.ICECandidateStats)

	var nominatedPair *webrtc.ICECandidatePairStats

	// Parse WebRTC stats
	for _, stat := range stats {
		switch v := stat.(type) {
		case webrtc.ICECandidatePairStats:
			// Track nominated pair for connection type (check State field which is ICECandidatePairState)
			if v.Nominated && v.State == webrtc.StatsICECandidatePairStateSucceeded {
				nominatedPair = &v
			}

		case webrtc.ICECandidateStats:
			ip := v.IP
			if ip == "" {
				continue
			}

			// Store candidates for lookup
			if v.Type == webrtc.StatsTypeLocalCandidate {
				localCandidates[v.ID] = v
			} else if v.Type == webrtc.StatsTypeRemoteCandidate {
				remoteCandidates[v.ID] = v
			}

			// Format: "IP:port (type)" - e.g., "193.19.207.206:42925 (srflx)"
			candidateStr := fmt.Sprintf("%s:%d (%s)", ip, v.Port, v.CandidateType)

			if v.Type == webrtc.StatsTypeLocalCandidate {
				if !localIPs[candidateStr] {
					response.LocalCandidates = append(response.LocalCandidates, candidateStr)
					localIPs[candidateStr] = true
				}
			} else if v.Type == webrtc.StatsTypeRemoteCandidate {
				if !remoteIPs[candidateStr] {
					response.RemoteCandidates = append(response.RemoteCandidates, candidateStr)
					remoteIPs[candidateStr] = true
				}
			}

		case webrtc.TransportStats:
			response.BytesSent += int64(v.BytesSent)
			response.BytesReceived += int64(v.BytesReceived)
		}
	}

	// Calculate bandwidth (total bytes delta over time)
	now := time.Now()
	if !s.lastStatsTime.IsZero() {
		deltaTime := now.Sub(s.lastStatsTime).Seconds()
		if deltaTime > 0 {
			deltaBytes := (response.BytesSent + response.BytesReceived) - (s.lastBytesSent + s.lastBytesReceived)
			bandwidthBytesPerSec := float64(deltaBytes) / deltaTime
			response.BandwidthKbps = int64(bandwidthBytesPerSec * 8 / 1000) // Convert to Kbps
		}
	}

	// Update last stats for next calculation
	s.lastStatsTime = now
	s.lastBytesSent = response.BytesSent
	s.lastBytesReceived = response.BytesReceived

	// Sort IP lists for consistent display
	sort.Strings(response.LocalCandidates)
	sort.Strings(response.RemoteCandidates)

	// Determine connection type from nominated pair
	if nominatedPair != nil {
		// Look up local and remote candidate info from our stored maps
		var localType, remoteType webrtc.ICECandidateType
		var localIP, remoteIP string

		if localCand, ok := localCandidates[nominatedPair.LocalCandidateID]; ok {
			localType = localCand.CandidateType
			localIP = fmt.Sprintf("%s:%d", localCand.IP, localCand.Port)
		}
		if remoteCand, ok := remoteCandidates[nominatedPair.RemoteCandidateID]; ok {
			remoteType = remoteCand.CandidateType
			remoteIP = fmt.Sprintf("%s:%d", remoteCand.IP, remoteCand.Port)
		}

		// Determine connection type with details
		if localType == webrtc.ICECandidateTypeRelay || remoteType == webrtc.ICECandidateTypeRelay {
			// Show which relay is being used
			if localType == webrtc.ICECandidateTypeRelay {
				response.ConnectionType = fmt.Sprintf("TURN relay (our: %s)", localIP)
			} else {
				response.ConnectionType = fmt.Sprintf("TURN relay (peer: %s)", remoteIP)
			}
		} else if localType == webrtc.ICECandidateTypeSrflx && remoteType == webrtc.ICECandidateTypeSrflx {
			response.ConnectionType = "P2P (NAT hole-punching)"
		} else if localType == webrtc.ICECandidateTypeHost && remoteType == webrtc.ICECandidateTypeHost {
			response.ConnectionType = "P2P (direct)"
		} else {
			response.ConnectionType = fmt.Sprintf("P2P (%s → %s)", localType.String(), remoteType.String())
		}
	}

	return response
}

// httpProxyDialer implements HTTP CONNECT proxy dialing
type httpProxyDialer struct {
	proxyAddr string
	username  string
	password  string
	logger    *slog.Logger
}

// Dial connects to the target address via HTTP CONNECT proxy
func (h *httpProxyDialer) Dial(network, address string) (net.Conn, error) {
	h.logger.Info("Dialing via HTTP CONNECT proxy",
		"network", network,
		"target", address,
		"proxy", h.proxyAddr)

	// Connect to the HTTP proxy
	conn, err := net.Dial("tcp", h.proxyAddr)
	if err != nil {
		h.logger.Error("Failed to connect to HTTP proxy",
			"proxy", h.proxyAddr,
			"error", err)
		return nil, fmt.Errorf("failed to connect to HTTP proxy: %w", err)
	}

	// Send HTTP CONNECT request
	connectReq := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\n", address, address)

	// Add proxy authentication if credentials provided
	if h.username != "" {
		auth := base64.StdEncoding.EncodeToString([]byte(h.username + ":" + h.password))
		connectReq += fmt.Sprintf("Proxy-Authorization: Basic %s\r\n", auth)
	}

	connectReq += "\r\n"

	// Write CONNECT request
	_, err = conn.Write([]byte(connectReq))
	if err != nil {
		conn.Close()
		h.logger.Error("Failed to send CONNECT request",
			"proxy", h.proxyAddr,
			"error", err)
		return nil, fmt.Errorf("failed to send CONNECT request: %w", err)
	}

	// Read response (up to 4096 bytes should be enough for headers)
	buf := make([]byte, 4096)
	n, err := conn.Read(buf)
	if err != nil {
		conn.Close()
		h.logger.Error("Failed to read CONNECT response",
			"proxy", h.proxyAddr,
			"error", err)
		return nil, fmt.Errorf("failed to read CONNECT response: %w", err)
	}

	response := string(buf[:n])
	h.logger.Debug("HTTP CONNECT response", "response", response)

	// Check for successful connection (HTTP/1.x 200)
	if !strings.Contains(response, "200") {
		conn.Close()
		h.logger.Error("HTTP CONNECT failed",
			"proxy", h.proxyAddr,
			"response", response)
		return nil, fmt.Errorf("HTTP CONNECT failed: %s", strings.Split(response, "\r\n")[0])
	}

	h.logger.Info("Successfully connected via HTTP CONNECT proxy",
		"target", address,
		"proxy", h.proxyAddr)

	return conn, nil
}

// SetMute sets the mute state for the session's microphone
func (s *Session) SetMute(muted bool) error {
	s.muteMu.Lock()
	defer s.muteMu.Unlock()

	s.logger.Info("Setting mute state",
		"session_id", s.ID,
		"muted", muted,
		"previous_muted", s.muted,
	)

	// Update mute state
	s.muted = muted

	// If pipeline and volume element exist, update the volume property
	if s.volumeElement != nil {
		// GStreamer volume element: mute property is a boolean
		// volume property is 0.0 (silent) to 1.0 (full volume)
		// We use the mute property which is cleaner
		if err := s.volumeElement.SetProperty("mute", muted); err != nil {
			s.logger.Error("Failed to set mute property on volume element",
				"session_id", s.ID,
				"error", err,
			)
			return fmt.Errorf("failed to set mute property: %w", err)
		}

		s.logger.Info("Mute property set on volume element",
			"session_id", s.ID,
			"muted", muted,
		)
	} else {
		// Pipeline not created yet - mute state will be applied when pipeline starts
		s.logger.Debug("Volume element not yet created, mute state will be applied on pipeline creation",
			"session_id", s.ID,
		)
	}

	return nil
}
