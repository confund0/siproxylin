package main

import (
	"fmt"
	"time"

	"github.com/go-gst/go-gst/gst"
	"github.com/go-gst/go-gst/gst/app"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
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
// Phase 2 implementation - adds video to PeerConnection for Jingle calls
func (s *Session) addVideoTrack() error {
	// Skip if track already exists
	if s.videoTrack != nil {
		s.logger.Debug("Video track already exists", "session_id", s.ID)
		return nil
	}

	s.logger.Info("Setting up video track for WebRTC", "session_id", s.ID, "camera", s.cameraDevice)

	// Step 1: Create VP8 video track for WebRTC
	track, err := webrtc.NewTrackLocalStaticSample(
		webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeVP8},
		"video",
		"pion-video",
	)
	if err != nil {
		s.logger.Error("Failed to create video track", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to create video track: %w", err)
	}

	s.videoTrack = track
	s.logger.Info("Created VP8 video track", "session_id", s.ID)

	// Step 2: Add track to PeerConnection
	rtpSender, err := s.pc.AddTrack(track)
	if err != nil {
		s.logger.Error("Failed to add video track to peer connection", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to add video track: %w", err)
	}

	s.logger.Info("Video track added to peer connection", "session_id", s.ID)

	// Handle RTCP packets (sender reports, etc.) - same pattern as audio
	go func() {
		rtcpBuf := make([]byte, 1500)
		for {
			if _, _, rtcpErr := rtpSender.Read(rtcpBuf); rtcpErr != nil {
				return
			}
		}
	}()

	// Step 3: Build GStreamer pipeline for video capture
	var videoSrc string
	if s.cameraDevice == "" || s.cameraDevice == "test" {
		// Fallback to test pattern if no camera or "test" specified
		videoSrc = "videotestsrc pattern=smpte is-live=true"
		s.logger.Info("Using test pattern for video", "session_id", s.ID)
	} else {
		// Use real camera via v4l2src
		videoSrc = fmt.Sprintf("v4l2src device=%s", s.cameraDevice)
		s.logger.Info("Using camera device for video", "session_id", s.ID, "device", s.cameraDevice)
	}

	// Build pipeline: source → videoconvert → vp8enc → appsink
	// Note: Using different appsink name to avoid conflict with audio
	pipelineStr := fmt.Sprintf(
		"%s ! "+
			"video/x-raw,width=640,height=480,framerate=30/1 ! "+
			"videoconvert ! "+
			"vp8enc deadline=1 target-bitrate=500000 ! "+
			"appsink name=video-appsink",
		videoSrc,
	)

	s.logger.Info("Creating video pipeline", "session_id", s.ID, "pipeline", pipelineStr)

	pipeline, err := gst.NewPipelineFromString(pipelineStr)
	if err != nil {
		s.logger.Error("Failed to create video pipeline", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to create video pipeline: %w", err)
	}

	s.videoPipeline = pipeline

	// Get appsink element
	appsinkElement, err := pipeline.GetElementByName("video-appsink")
	if err != nil {
		s.logger.Error("Failed to get video appsink element", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to get video appsink: %w", err)
	}

	appsink := app.SinkFromElement(appsinkElement)

	// Step 4: Start pipeline
	s.logger.Info("Starting video pipeline", "session_id", s.ID)
	if err := pipeline.SetState(gst.StatePlaying); err != nil {
		s.logger.Error("Failed to start video pipeline", "session_id", s.ID, "error", err)
		return fmt.Errorf("failed to start video pipeline: %w", err)
	}

	// Step 5: Start goroutine to read samples from appsink and write to WebRTC track
	go func() {
		s.logger.Info("Starting video sample reader", "session_id", s.ID)

		frameCount := 0
		for {
			// Pull sample from appsink
			sample := appsink.PullSample()
			if sample == nil {
				s.logger.Debug("No more video samples (pipeline stopped)", "session_id", s.ID)
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
				s.logger.Error("Failed to write video sample to track", "session_id", s.ID, "error", err)
				buffer.Unmap()
				return
			}

			buffer.Unmap()

			// Debug logging every 30 frames (~1 second at 30fps)
			frameCount++
			if frameCount%30 == 0 {
				s.logger.Info("Video frames sent", "session_id", s.ID, "frame_count", frameCount, "last_frame_size", len(samples))
			}
		}
	}()

	s.logger.Info("Video track setup complete",
		"session_id", s.ID,
		"source", videoSrc,
		"resolution", "640x480",
		"framerate", "30fps",
		"codec", "VP8",
		"bitrate", "500kbps",
	)

	return nil
}

// playVideoTrack handles incoming remote video track
// Creates GStreamer pipeline to decode and display remote video
func (s *Session) playVideoTrack(track *webrtc.TrackRemote) {
	s.logger.Info("Starting remote video playback",
		"session_id", s.ID,
		"codec", track.Codec().MimeType,
		"track_id", track.ID(),
	)

	// Build GStreamer pipeline for video playback
	// appsrc → vp8dec → videoconvert → autovideosink
	pipelineStr := "appsrc name=video-appsrc format=time is-live=true ! " +
		"application/x-rtp,media=video,clock-rate=90000,encoding-name=VP8,payload=96 ! " +
		"rtpvp8depay ! " +
		"vp8dec ! " +
		"videoconvert ! " +
		"autovideosink sync=false"

	s.logger.Info("Creating remote video playback pipeline",
		"session_id", s.ID,
		"pipeline", pipelineStr,
	)

	pipeline, err := gst.NewPipelineFromString(pipelineStr)
	if err != nil {
		s.logger.Error("Failed to create remote video pipeline",
			"session_id", s.ID,
			"error", err,
		)
		return
	}

	// Get appsrc element
	appsrcElement, err := pipeline.GetElementByName("video-appsrc")
	if err != nil {
		s.logger.Error("Failed to get video appsrc element",
			"session_id", s.ID,
			"error", err,
		)
		return
	}

	appsrc := app.SrcFromElement(appsrcElement)

	// Start pipeline
	s.logger.Info("Starting remote video playback pipeline", "session_id", s.ID)
	if err := pipeline.SetState(gst.StatePlaying); err != nil {
		s.logger.Error("Failed to start remote video pipeline",
			"session_id", s.ID,
			"error", err,
		)
		return
	}

	s.logger.Info("Remote video playback started", "session_id", s.ID)

	// Read RTP packets from WebRTC track and push to GStreamer
	for {
		// Read RTP packet
		rtpPacket, _, readErr := track.ReadRTP()
		if readErr != nil {
			s.logger.Debug("Remote video track ended",
				"session_id", s.ID,
				"error", readErr,
			)
			pipeline.SetState(gst.StateNull)
			return
		}

		// Marshal RTP packet to bytes
		rtpData, err := rtpPacket.Marshal()
		if err != nil {
			s.logger.Error("Failed to marshal RTP packet",
				"session_id", s.ID,
				"error", err,
			)
			continue
		}

		// Create GStreamer buffer from RTP data
		buffer := gst.NewBufferFromBytes(rtpData)

		// Push buffer to appsrc
		if ret := appsrc.PushBuffer(buffer); ret != gst.FlowOK {
			s.logger.Error("Failed to push buffer to appsrc",
				"session_id", s.ID,
				"flow_return", ret,
			)
			pipeline.SetState(gst.StateNull)
			return
		}
	}
}

// TODO Phase 3: setVideoEnabled(enabled bool) - toggle video mid-call
