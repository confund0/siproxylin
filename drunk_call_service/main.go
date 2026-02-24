package main

import (
	"flag"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/go-gst/go-gst/gst"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	pb "github.com/yourusername/drunk-call-service/proto"
)

var (
	port        = flag.Int("port", 50051, "gRPC server port")
	logLevel    = flag.String("log-level", "INFO", "Log level (DEBUG, INFO, WARN, ERROR)")
	logPath     = flag.String("log-path", "", "Log file path (default: ../app/logs/drunk-call-service.log)")
	testDevices = flag.Bool("test-devices", false, "Test device enumeration and exit")
	testVideo   = flag.Int("test-video", 0, "Test video streaming on specified TCP port (e.g., 5004)")
)

func main() {
	flag.Parse()

	// Initialize GStreamer
	gst.Init(nil)

	// Test mode: enumerate devices and exit
	if *testDevices {
		logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelDebug}))
		fmt.Println("Testing device enumeration...")
		devices, err := ListAudioDevices(logger)
		if err != nil {
			fmt.Printf("ERROR: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("\nFound %d devices:\n", len(devices))
		for i, dev := range devices {
			fmt.Printf("%d. %s\n", i+1, dev.Description)
			fmt.Printf("   Name: %s\n", dev.Name)
			fmt.Printf("   Class: %s\n", dev.DeviceClass)
			fmt.Println()
		}
		os.Exit(0)
	}

	// Test mode: video streaming over TCP
	if *testVideo > 0 {
		logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
		fmt.Printf("=== Video Test Mode ===\n")
		fmt.Printf("Streaming test pattern on TCP port %d\n", *testVideo)
		fmt.Printf("\nConnect with:\n")
		fmt.Printf("  VLC:         vlc tcp://localhost:%d\n", *testVideo)
		fmt.Printf("  Test Player: python tests/test-video-player.py localhost %d\n", *testVideo)
		fmt.Printf("\nPress Ctrl+C to stop\n")
		fmt.Printf("=======================\n\n")

		// Create dummy session for video testing
		session := &Session{
			ID:     "video-test",
			logger: logger,
		}

		// Start video streaming
		if err := session.addVideoTrackTCP(*testVideo); err != nil {
			fmt.Printf("ERROR: Failed to start video streaming: %v\n", err)
			os.Exit(1)
		}

		// Wait for interrupt
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
		<-sigChan

		fmt.Println("\nShutting down...")
		os.Exit(0)
	}

	// Determine log path
	finalLogPath := *logPath
	if finalLogPath == "" {
		// Default: ../app/logs/drunk-call-service.log (relative to binary location)
		execPath, _ := os.Executable()
		projectRoot := filepath.Dir(filepath.Dir(execPath))
		finalLogPath = filepath.Join(projectRoot, "app", "logs", "drunk-call-service.log")
	}

	// Setup structured logging
	logger := SetupLogger(finalLogPath, GetLogLevel(*logLevel))
	slog.SetDefault(logger) // Set as default for package-level slog calls

	logger.Info("DrunkCallService starting",
		"port", *port,
		"log_level", *logLevel,
		"log_path", finalLogPath,
		"gstreamer", "initialized",
	)

	// Test TURN server connectivity
	testTURNConnectivity(logger)

	// Create gRPC server
	server := NewCallServer(logger)
	grpcServer := grpc.NewServer()

	// Register service
	pb.RegisterCallServiceServer(grpcServer, server)

	// Enable reflection for grpcurl testing
	reflection.Register(grpcServer)

	// Start listening
	listener, err := net.Listen("tcp", fmt.Sprintf(":%d", *port))
	if err != nil {
		logger.Error("Failed to listen", "error", err)
		os.Exit(1)
	}

	// Graceful shutdown
	go func() {
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
		<-sigChan

		logger.Info("Shutting down gracefully (SIGTERM)...")
		grpcServer.GracefulStop()
		server.CloseAllSessions()
	}()

	// Start serving
	logger.Info("gRPC server listening", "address", fmt.Sprintf(":%d", *port))
	if err := grpcServer.Serve(listener); err != nil {
		logger.Error("Failed to serve", "error", err)
		os.Exit(1)
	}
}

// testTURNConnectivity tests TURN server connectivity on startup
func testTURNConnectivity(logger *slog.Logger) {
	logger.Info("Testing TURN server connectivity...")

	// Test UDP connectivity
	conn, err := net.DialTimeout("udp", "turn.jami.net:3478", 5*time.Second)
	if err != nil {
		logger.Warn("TURN server UDP connectivity test failed",
			"server", "turn.jami.net:3478",
			"error", err,
		)
	} else {
		conn.Close()
		logger.Info("TURN server UDP connectivity: OK", "server", "turn.jami.net:3478")
	}

	// Test TCP connectivity
	conn, err = net.DialTimeout("tcp", "turn.jami.net:3478", 5*time.Second)
	if err != nil {
		logger.Warn("TURN server TCP connectivity test failed",
			"server", "turn.jami.net:3478",
			"error", err,
		)
	} else {
		conn.Close()
		logger.Info("TURN server TCP connectivity: OK", "server", "turn.jami.net:3478")
	}
}
