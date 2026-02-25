package main

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"

	"gopkg.in/natefinch/lumberjack.v2"
)

// SetupLogger creates a structured logger with file rotation
func SetupLogger(logPath string, level slog.Level) *slog.Logger {
	// Ensure log directory exists
	logDir := filepath.Dir(logPath)
	if err := os.MkdirAll(logDir, 0755); err != nil {
		panic("Failed to create log directory: " + err.Error())
	}

	// Setup log rotation (matching Python: 10MB, 5 backups)
	fileWriter := &lumberjack.Logger{
		Filename:   logPath,
		MaxSize:    10, // MB
		MaxBackups: 5,
		MaxAge:     30, // days
		Compress:   false,
	}

	// Write only to file (stdout is redirected to DEVNULL by Python)
	multiWriter := io.MultiWriter(fileWriter)

	// Create handler with options
	handler := slog.NewTextHandler(multiWriter, &slog.HandlerOptions{
		Level: level,
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			// Customize timestamp format to match Python
			if a.Key == slog.TimeKey {
				return slog.String("time", a.Value.Time().Format("2006-01-02 15:04:05.000"))
			}
			return a
		},
	})

	return slog.New(handler)
}

// GetLogLevel parses log level string
func GetLogLevel(levelStr string) slog.Level {
	switch levelStr {
	case "DEBUG":
		return slog.LevelDebug
	case "INFO":
		return slog.LevelInfo
	case "WARN", "WARNING":
		return slog.LevelWarn
	case "ERROR":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
