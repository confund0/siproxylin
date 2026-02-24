package main

import (
	"fmt"

	"github.com/go-gst/go-gst/gst"
	"github.com/pion/webrtc/v4"
)

// addVideoTrackTCP creates a video track and streams over UDP for testing
// This is Phase 1 implementation - Real camera streaming for isolated testing
// Phase 2 will switch to WebRTC streaming
func (s *Session) addVideoTrackTCP(port int, cameraDevice string) error {
	s.logger.Info("Setting up camera video pipeline (UDP mode)",
		"session_id", s.ID,
		"port", port,
		"camera_device", cameraDevice,
	)

	// Create VP8 video track (even though we're not using WebRTC yet)
	// This ensures the structure is ready for Phase 2
	track, err := webrtc.NewTrackLocalStaticSample(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8},
		"video",
		"pion-video",
	)
	if err != nil {
		s.logger.Error("Failed to create video track", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to create video track: %w", err)
	}

	s.logger.Info("Created video track", "session_id", s.ID, "mime", webrtc.MimeTypeVP8)

	// Store track (for Phase 2 when we add to PeerConnection)
	// For now, we're not adding it to PC - just testing the pipeline
	_ = track

	// Build GStreamer pipeline for camera → VP8 → UDP
	// Phase 1: Real camera with v4l2src
	// Note: Using UDP for easier RTP testing, will switch to WebRTC in Phase 2
	var videoSrc string
	if cameraDevice == "" || cameraDevice == "test" {
		// Fallback to test pattern if no camera or "test" specified
		videoSrc = "videotestsrc pattern=smpte is-live=true"
		s.logger.Info("Using test pattern (no camera device specified)", "session_id", s.ID)
	} else {
		// Use real camera via v4l2src
		videoSrc = fmt.Sprintf("v4l2src device=%s", cameraDevice)
		s.logger.Info("Using camera device", "session_id", s.ID, "device", cameraDevice)
	}

	// Build pipeline based on source type
	var pipelineStr string
	if cameraDevice == "" || cameraDevice == "test" {
		// Test pattern: already outputs video/x-raw
		pipelineStr = fmt.Sprintf(
			"%s ! "+
				"video/x-raw,width=640,height=480,framerate=15/1 ! "+
				"videoconvert ! "+
				"vp8enc deadline=1 target-bitrate=500000 ! "+
				"rtpvp8pay ! "+
				"udpsink host=127.0.0.1 port=%d sync=false",
			videoSrc,
			port,
		)
	} else {
		// Real camera: let v4l2src negotiate format, just specify dimensions/framerate
		// videoconvert will handle YUYV/MJPG/etc automatically
		pipelineStr = fmt.Sprintf(
			"%s ! "+
				"video/x-raw,width=640,height=480,framerate=30/1 ! "+
				"videoconvert ! "+
				"vp8enc deadline=1 target-bitrate=500000 ! "+
				"rtpvp8pay ! "+
				"udpsink host=127.0.0.1 port=%d sync=false",
			videoSrc,
			port,
		)
	}

	s.logger.Info("Creating video pipeline", "session_id", s.ID, "pipeline", pipelineStr)

	pipeline, err := gst.NewPipelineFromString(pipelineStr)
	if err != nil {
		s.logger.Error("Failed to create video pipeline", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to create video pipeline: %w", err)
	}

	// Start pipeline
	s.logger.Info("Starting video pipeline", "session_id", s.ID)
	if err := pipeline.SetState(gst.StatePlaying); err != nil {
		s.logger.Error("Failed to start video pipeline", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to start video pipeline: %w", err)
	}

	sourceType := "camera"
	sourceDetail := cameraDevice
	if cameraDevice == "" || cameraDevice == "test" {
		sourceType = "test pattern"
		sourceDetail = "SMPTE bars"
	}

	s.logger.Info("Video pipeline started successfully",
		"session_id", s.ID,
		"source_type", sourceType,
		"source", sourceDetail,
		"resolution", "640x480",
		"framerate", "15fps",
		"codec", "VP8",
		"bitrate", "500kbps",
		"udp_port", port,
	)

	s.logger.Info("Test video stream ready - connect with:",
		"test_player", fmt.Sprintf("python tests/test-video-player.py"),
		"note", fmt.Sprintf("Streaming on UDP port %d", port),
	)

	// TODO Phase 2: Store pipeline for cleanup
	// For now, pipeline will be cleaned up when session ends

	return nil
}

// addVideoTrack creates and adds a video track using GStreamer (WebRTC mode)
// This is Phase 2 implementation - will be implemented after TCP testing works
// For now, this is a placeholder
func (s *Session) addVideoTrack() error {
	s.logger.Info("addVideoTrack called - WebRTC mode not implemented yet",
		"session_id", s.ID,
	)

	// Phase 2 TODO:
	// 1. Create video track (like TCP mode)
	// 2. Add track to PeerConnection
	// 3. Create GStreamer pipeline: videotestsrc → vp8enc → appsink
	// 4. Read from appsink, write to track (like audio)
	// 5. Handle remote video track in OnTrack

	return fmt.Errorf("video track (WebRTC mode) not implemented - use addVideoTrackTCP for testing")
}

// TODO Phase 1: addVideoTrackWithCamera(device string)
// TODO Phase 2: handleRemoteVideoTrack(track *webrtc.TrackRemote)
// TODO Phase 3: setVideoEnabled(enabled bool)
