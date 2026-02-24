package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/pion/logging"
	"github.com/pion/webrtc/v4"
	pb "github.com/yourusername/drunk-call-service/proto"
)

// slogPionLogger adapts slog.Logger to Pion's LeveledLogger interface
type slogPionLogger struct {
	logger *slog.Logger
	scope  string
}

func (l *slogPionLogger) Trace(msg string)                { l.logger.Debug("[PION:"+l.scope+"] "+msg) }
func (l *slogPionLogger) Tracef(format string, args ...interface{}) { l.logger.Debug(fmt.Sprintf("[PION:"+l.scope+"] "+format, args...)) }
func (l *slogPionLogger) Debug(msg string)                { l.logger.Debug("[PION:"+l.scope+"] "+msg) }
func (l *slogPionLogger) Debugf(format string, args ...interface{}) { l.logger.Debug(fmt.Sprintf("[PION:"+l.scope+"] "+format, args...)) }
func (l *slogPionLogger) Info(msg string)                 { l.logger.Info("[PION:"+l.scope+"] "+msg) }
func (l *slogPionLogger) Infof(format string, args ...interface{})  { l.logger.Info(fmt.Sprintf("[PION:"+l.scope+"] "+format, args...)) }
func (l *slogPionLogger) Warn(msg string)                 { l.logger.Warn("[PION:"+l.scope+"] "+msg) }
func (l *slogPionLogger) Warnf(format string, args ...interface{})  { l.logger.Warn(fmt.Sprintf("[PION:"+l.scope+"] "+format, args...)) }
func (l *slogPionLogger) Error(msg string)                { l.logger.Error("[PION:"+l.scope+"] "+msg) }
func (l *slogPionLogger) Errorf(format string, args ...interface{}) { l.logger.Error(fmt.Sprintf("[PION:"+l.scope+"] "+format, args...)) }

// slogPionLoggerFactory creates Pion loggers that forward to slog
type slogPionLoggerFactory struct {
	logger *slog.Logger
}

func (f *slogPionLoggerFactory) NewLogger(scope string) logging.LeveledLogger {
	return &slogPionLogger{logger: f.logger, scope: scope}
}

// CallServer implements the gRPC CallService
type CallServer struct {
	pb.UnimplementedCallServiceServer

	logger   *slog.Logger
	mu       sync.RWMutex
	sessions map[string]*Session

	// WebRTC API configuration
	api *webrtc.API

	// Heartbeat tracking
	lastHeartbeat time.Time
	heartbeatMu   sync.RWMutex

	// Candidate queueing (fix for race condition where transport-info arrives before CreateSession completes)
	pendingCandidates map[string][]string // sessionID → queued candidates
	pendingMu         sync.Mutex          // Protects pendingCandidates
}

// NewCallServer creates a new CallServer instance
func NewCallServer(logger *slog.Logger) *CallServer {
	// Create WebRTC API with settings
	// TODO: Configure with STUN/TURN servers from config
	mediaEngine := &webrtc.MediaEngine{}

	// Register Opus codec for audio
	if err := mediaEngine.RegisterDefaultCodecs(); err != nil {
		logger.Error("Failed to register codecs", "error", err)
		panic(err)
	}

	// Create SettingEngine to configure ICE behavior
	settingEngine := webrtc.SettingEngine{}

	// Configure Pion logger to forward to slog
	settingEngine.LoggerFactory = &slogPionLoggerFactory{logger: logger}

	// CRITICAL: Allow handling RTP packets with undeclared SSRCs
	// Without this, OnTrack won't fire if peer sends media before SDP answer completes
	// This is common with early media scenarios or fast peers
	settingEngine.SetHandleUndeclaredSSRCWithoutAnswer(true)
	logger.Info("Enabled handling of undeclared SSRCs (allows early media)")

	logger.Info("Using vanilla Pion with component 2 filtering workaround")

	api := webrtc.NewAPI(
		webrtc.WithMediaEngine(mediaEngine),
		webrtc.WithSettingEngine(settingEngine),
	)

	logger.Info("Created WebRTC API with Pion logging enabled")

	server := &CallServer{
		logger:            logger,
		sessions:          make(map[string]*Session),
		api:               api,
		lastHeartbeat:     time.Now(), // Initialize to now
		pendingCandidates: make(map[string][]string),
	}

	// Start heartbeat monitor goroutine
	go server.monitorHeartbeat()

	return server
}

// CreateSession creates a new WebRTC session (gRPC handler)
func (s *CallServer) CreateSession(ctx context.Context, req *pb.CreateSessionRequest) (*pb.CreateSessionResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	sessionID := req.SessionId
	peerJID := req.PeerJid

	if _, exists := s.sessions[sessionID]; exists {
		s.logger.Warn("Session already exists", "session_id", sessionID)
		return &pb.CreateSessionResponse{
			Success: true,
			Error:   "",
		}, nil
	}

	// Extract proxy configuration from request
	var proxyConfig *ProxyConfig
	if req.ProxyHost != "" && req.ProxyPort > 0 {
		proxyConfig = &ProxyConfig{
			Host:     req.ProxyHost,
			Port:     req.ProxyPort,
			Username: req.ProxyUsername,
			Password: req.ProxyPassword,
			Type:     req.ProxyType,
		}
		s.logger.Info("Proxy configuration received from account settings",
			"session_id", sessionID,
			"proxy_host", req.ProxyHost,
			"proxy_port", req.ProxyPort,
			"proxy_type", req.ProxyType,
		)
	}

	// Extract TURN server configuration from request
	var turnConfig *TURNConfig
	if req.TurnServer != "" {
		turnConfig = &TURNConfig{
			Server:   req.TurnServer,
			Username: req.TurnUsername,
			Password: req.TurnPassword,
		}
		s.logger.Info("Custom TURN server configuration received",
			"session_id", sessionID,
			"turn_server", req.TurnServer,
		)
	}

	// Default relay_only to true if not explicitly set (privacy-first)
	relayOnly := req.RelayOnly
	if !relayOnly && proxyConfig != nil {
		// Force relay-only if proxy is configured
		relayOnly = true
		s.logger.Info("Forcing relay-only mode because proxy is configured",
			"session_id", sessionID)
	}

	// Extract audio processing settings (defaults if not set)
	audioConfig := &AudioProcessingConfig{
		EchoCancel:            req.EchoCancel,
		EchoSuppressionLevel:  req.EchoSuppressionLevel,
		NoiseSuppression:      req.NoiseSuppression,
		NoiseSuppressionLevel: req.NoiseSuppressionLevel,
		GainControl:           req.GainControl,
	}

	// Create new session with device parameters and proxy/TURN config
	session, err := NewSession(sessionID, peerJID,
		req.MicrophoneDevice, req.SpeakersDevice,
		proxyConfig, turnConfig, relayOnly, audioConfig,
		s.api, s.logger)
	if err != nil {
		s.logger.Error("Failed to create session", "session_id", sessionID, "error", err)
		return &pb.CreateSessionResponse{
			Success: false,
			Error:   err.Error(),
		}, nil
	}

	s.logger.Info("Session created with devices",
		"session_id", sessionID,
		"microphone", req.MicrophoneDevice,
		"speakers", req.SpeakersDevice,
	)

	s.sessions[sessionID] = session
	s.logger.Info("Created session", "session_id", sessionID, "peer_jid", peerJID)

	return &pb.CreateSessionResponse{
		Success: true,
		Error:   "",
	}, nil
}

// CreateOffer creates SDP offer (gRPC handler)
func (s *CallServer) CreateOffer(ctx context.Context, req *pb.CreateOfferRequest) (*pb.SDPResponse, error) {
	session, exists := s.GetSession(req.SessionId)
	if !exists {
		return &pb.SDPResponse{
			Sdp:   "",
			Error: "session not found",
		}, nil
	}

	sdp, err := session.CreateOffer()
	if err != nil {
		return &pb.SDPResponse{
			Sdp:   "",
			Error: err.Error(),
		}, nil
	}

	return &pb.SDPResponse{
		Sdp:   sdp,
		Error: "",
	}, nil
}

// CreateAnswer creates SDP answer (gRPC handler)
func (s *CallServer) CreateAnswer(ctx context.Context, req *pb.CreateAnswerRequest) (*pb.SDPResponse, error) {
	session, exists := s.GetSession(req.SessionId)
	if !exists {
		return &pb.SDPResponse{
			Sdp:   "",
			Error: "session not found",
		}, nil
	}

	sdp, err := session.CreateAnswer(req.RemoteSdp)
	if err != nil {
		return &pb.SDPResponse{
			Sdp:   "",
			Error: err.Error(),
		}, nil
	}

	// Drain any queued candidates (now that remote description is set)
	// This fixes race condition where transport-info arrives before CreateSession completes
	s.pendingMu.Lock()
	if candidates, exists := s.pendingCandidates[req.SessionId]; exists {
		s.logger.Info("Draining queued candidates (after remote description set)",
			"session_id", req.SessionId,
			"count", len(candidates))
		for _, cand := range candidates {
			if err := session.AddICECandidate(cand); err != nil {
				s.logger.Error("Failed to add queued candidate",
					"session_id", req.SessionId, "error", err)
			} else {
				s.logger.Info("Added queued candidate", "session_id", req.SessionId)
			}
		}
		delete(s.pendingCandidates, req.SessionId)
	}
	s.pendingMu.Unlock()

	return &pb.SDPResponse{
		Sdp:   sdp,
		Error: "",
	}, nil
}

// SetRemoteDescription sets remote SDP description (gRPC handler)
func (s *CallServer) SetRemoteDescription(ctx context.Context, req *pb.SetRemoteDescriptionRequest) (*pb.Empty, error) {
	session, exists := s.GetSession(req.SessionId)
	if !exists {
		s.logger.Warn("Session not found for SetRemoteDescription", "session_id", req.SessionId)
		return &pb.Empty{}, nil
	}

	err := session.SetRemoteDescription(req.RemoteSdp, req.SdpType)
	if err != nil {
		s.logger.Error("Failed to set remote description", "session_id", req.SessionId, "error", err)
		return &pb.Empty{}, err
	}

	// Drain any queued candidates (now that remote description is set)
	// This handles outgoing call scenario
	s.pendingMu.Lock()
	if candidates, exists := s.pendingCandidates[req.SessionId]; exists {
		s.logger.Info("Draining queued candidates (after remote description set - outgoing)",
			"session_id", req.SessionId,
			"count", len(candidates))
		for _, cand := range candidates {
			if err := session.AddICECandidate(cand); err != nil {
				s.logger.Error("Failed to add queued candidate",
					"session_id", req.SessionId, "error", err)
			} else {
				s.logger.Info("Added queued candidate", "session_id", req.SessionId)
			}
		}
		delete(s.pendingCandidates, req.SessionId)
	}
	s.pendingMu.Unlock()

	return &pb.Empty{}, nil
}

// AddICECandidate adds ICE candidate (gRPC handler)
func (s *CallServer) AddICECandidate(ctx context.Context, req *pb.AddICECandidateRequest) (*pb.Empty, error) {
	session, exists := s.GetSession(req.SessionId)
	if !exists {
		// Queue candidate for when session is created (fixes race condition)
		s.logger.Info("[CANDIDATE] Session not found, queueing candidate",
			"session_id", req.SessionId)
		s.pendingMu.Lock()
		s.pendingCandidates[req.SessionId] = append(
			s.pendingCandidates[req.SessionId],
			req.Candidate,
		)
		s.pendingMu.Unlock()
		return &pb.Empty{}, nil
	}

	err := session.AddICECandidate(req.Candidate)
	if err != nil {
		s.logger.Error("Failed to add ICE candidate", "session_id", req.SessionId, "error", err)
	}

	return &pb.Empty{}, nil
}

// GetStats retrieves call statistics (gRPC handler)
func (s *CallServer) GetStats(ctx context.Context, req *pb.GetStatsRequest) (*pb.GetStatsResponse, error) {
	session, exists := s.GetSession(req.SessionId)
	if !exists {
		return &pb.GetStatsResponse{}, nil // Return empty stats for non-existent session
	}

	stats := session.GetStats()
	return stats, nil
}

// EndSession ends call session (gRPC handler)
func (s *CallServer) EndSession(ctx context.Context, req *pb.EndSessionRequest) (*pb.Empty, error) {
	err := s.endSessionInternal(req.SessionId)
	if err != nil {
		s.logger.Error("Error ending session", "session_id", req.SessionId, "error", err)
	}
	return &pb.Empty{}, nil
}

// ListAudioDevices enumerates available audio devices (gRPC handler)
func (s *CallServer) ListAudioDevices(ctx context.Context, req *pb.Empty) (*pb.ListAudioDevicesResponse, error) {
	devices, err := ListAudioDevices(s.logger)
	if err != nil {
		s.logger.Error("Failed to list audio devices", "error", err)
		return &pb.ListAudioDevicesResponse{Devices: []*pb.AudioDevice{}}, nil
	}

	s.logger.Info("Listed audio devices", "count", len(devices))
	return &pb.ListAudioDevicesResponse{Devices: devices}, nil
}

// ListVideoDevices enumerates available video devices (gRPC handler)
func (s *CallServer) ListVideoDevices(ctx context.Context, req *pb.Empty) (*pb.ListVideoDevicesResponse, error) {
	devices, err := ListVideoDevices(s.logger)
	if err != nil {
		s.logger.Error("Failed to list video devices", "error", err)
		return &pb.ListVideoDevicesResponse{Devices: []*pb.VideoDevice{}}, nil
	}

	s.logger.Info("Listed video devices", "count", len(devices))
	return &pb.ListVideoDevicesResponse{Devices: devices}, nil
}

// SetMute sets the mute state for a session's microphone (gRPC handler)
func (s *CallServer) SetMute(ctx context.Context, req *pb.SetMuteRequest) (*pb.Empty, error) {
	sessionID := req.SessionId
	muted := req.Muted

	s.logger.Info("SetMute called", "session_id", sessionID, "muted", muted)

	// Get session
	session, exists := s.GetSession(sessionID)
	if !exists {
		s.logger.Error("Session not found for SetMute", "session_id", sessionID)
		return nil, fmt.Errorf("session not found: %s", sessionID)
	}

	// Set mute state on the session
	if err := session.SetMute(muted); err != nil {
		s.logger.Error("Failed to set mute state", "session_id", sessionID, "error", err)
		return nil, err
	}

	s.logger.Info("Mute state set successfully", "session_id", sessionID, "muted", muted)
	return &pb.Empty{}, nil
}

// StreamEvents streams events to client (gRPC handler)
func (s *CallServer) StreamEvents(req *pb.StreamEventsRequest, stream pb.CallService_StreamEventsServer) error {
	sessionID := req.SessionId
	s.logger.Info("StreamEvents started", "session_id", sessionID)

	// Get session
	session, exists := s.GetSession(sessionID)
	if !exists {
		s.logger.Warn("Session not found for StreamEvents", "session_id", sessionID)
		return nil // Return gracefully (client will retry)
	}

	// Stream events from session's event channel
	eventChan := session.GetEventChannel()

	for event := range eventChan {
		// Send event to gRPC stream
		if err := stream.Send(event); err != nil {
			s.logger.Error("Failed to send event to stream",
				"session_id", sessionID,
				"error", err,
			)
			return err
		}

		s.logger.Debug("Event sent to Python",
			"session_id", sessionID,
			"event_type", fmt.Sprintf("%T", event.Event),
		)
	}

	s.logger.Info("StreamEvents ended (channel closed)", "session_id", sessionID)
	return nil
}

// endSessionInternal is the internal method for ending sessions
func (s *CallServer) endSessionInternal(sessionID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	session, exists := s.sessions[sessionID]
	if !exists {
		s.logger.Warn("Session not found", "session_id", sessionID)
		return nil
	}

	session.Close()
	delete(s.sessions, sessionID)

	// Clean up any queued candidates (shouldn't happen, but prevents memory leaks)
	s.pendingMu.Lock()
	delete(s.pendingCandidates, sessionID)
	s.pendingMu.Unlock()

	s.logger.Info("Ended session", "session_id", sessionID)

	return nil
}

// GetSession retrieves a session by ID
func (s *CallServer) GetSession(sessionID string) (*Session, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	session, exists := s.sessions[sessionID]
	return session, exists
}

// Shutdown closes all sessions and exits (gRPC handler)
func (s *CallServer) Shutdown(ctx context.Context, req *pb.Empty) (*pb.Empty, error) {
	s.logger.Info("Received graceful shutdown request from Python")

	// Close all sessions
	s.CloseAllSessions()

	// Exit process immediately
	go func() {
		time.Sleep(100 * time.Millisecond) // Allow RPC response to be sent
		s.logger.Info("Exiting after graceful shutdown")
		os.Exit(0)
	}()

	return &pb.Empty{}, nil
}

// CloseAllSessions closes all active sessions
func (s *CallServer) CloseAllSessions() {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.logger.Info("Closing all sessions", "active_sessions", len(s.sessions))

	for id, session := range s.sessions {
		session.Close()
		delete(s.sessions, id)
	}
}

// Heartbeat updates the last heartbeat timestamp (gRPC handler)
func (s *CallServer) Heartbeat(ctx context.Context, req *pb.Empty) (*pb.Empty, error) {
	s.heartbeatMu.Lock()
	s.lastHeartbeat = time.Now()
	s.heartbeatMu.Unlock()

	s.logger.Debug("Heartbeat received")
	return &pb.Empty{}, nil
}

// monitorHeartbeat runs in background and exits if no heartbeat for 10 seconds
func (s *CallServer) monitorHeartbeat() {
	ticker := time.NewTicker(2 * time.Second) // Check every 2 seconds
	defer ticker.Stop()

	for range ticker.C {
		s.heartbeatMu.RLock()
		elapsed := time.Since(s.lastHeartbeat)
		s.heartbeatMu.RUnlock()

		if elapsed > 10*time.Second {
			s.logger.Warn("No heartbeat for 10 seconds, Python likely crashed - exiting")
			s.CloseAllSessions()
			os.Exit(1)
		}

		if elapsed > 7*time.Second {
			s.logger.Warn("No heartbeat for 7 seconds, Python may have crashed", "elapsed", elapsed)
		}
	}
}
