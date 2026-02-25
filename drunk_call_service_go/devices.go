package main

import (
	"bufio"
	"fmt"
	"log/slog"
	"os/exec"
	"strings"

	pb "github.com/yourusername/drunk-call-service/proto"
)

// ListAudioDevices enumerates available audio input/output devices using pactl
// (GStreamer DeviceMonitor crashes with nil caps, so we use pactl instead)
func ListAudioDevices(logger *slog.Logger) ([]*pb.AudioDevice, error) {
	logger.Info("ListAudioDevices called - starting device enumeration via pactl")

	var audioDevices []*pb.AudioDevice

	// Get sinks (speakers/outputs)
	sinks, err := getPactlDevices(logger, "sinks")
	if err != nil {
		logger.Error("Failed to get sinks", "error", err)
	} else {
		for _, dev := range sinks {
			audioDevices = append(audioDevices, &pb.AudioDevice{
				Name:        dev.Name,
				Description: dev.Description,
				DeviceClass: "Audio/Sink",
			})
			logger.Debug("Found audio sink", "name", dev.Name, "description", dev.Description)
		}
	}

	// Get sources (microphones/inputs) - exclude monitor devices
	sources, err := getPactlDevices(logger, "sources")
	if err != nil {
		logger.Error("Failed to get sources", "error", err)
	} else {
		for _, dev := range sources {
			// Skip monitor devices (loopback from outputs)
			if strings.Contains(dev.Name, ".monitor") {
				continue
			}
			audioDevices = append(audioDevices, &pb.AudioDevice{
				Name:        dev.Name,
				Description: dev.Description,
				DeviceClass: "Audio/Source",
			})
			logger.Debug("Found audio source", "name", dev.Name, "description", dev.Description)
		}
	}

	logger.Info("Audio device enumeration complete", "total_devices", len(audioDevices))
	return audioDevices, nil
}

type pactlDevice struct {
	Name        string
	Description string
}

// getPactlDevices parses pactl output to get device names and descriptions
func getPactlDevices(logger *slog.Logger, deviceType string) ([]pactlDevice, error) {
	// Run pactl list sinks/sources
	cmd := exec.Command("pactl", "list", deviceType)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to run pactl list %s: %w", deviceType, err)
	}

	var devices []pactlDevice
	var currentName string
	var currentDesc string

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.HasPrefix(line, "Name:") {
			currentName = strings.TrimSpace(strings.TrimPrefix(line, "Name:"))
		} else if strings.HasPrefix(line, "Description:") {
			currentDesc = strings.TrimSpace(strings.TrimPrefix(line, "Description:"))

			// When we have both name and description, create device
			if currentName != "" && currentDesc != "" {
				devices = append(devices, pactlDevice{
					Name:        currentName,
					Description: currentDesc,
				})
				currentName = ""
				currentDesc = ""
			}
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("failed to parse pactl output: %w", err)
	}

	logger.Debug("Parsed pactl devices", "type", deviceType, "count", len(devices))
	return devices, nil
}

// ListVideoDevices enumerates available video capture devices using v4l2-ctl
func ListVideoDevices(logger *slog.Logger) ([]*pb.VideoDevice, error) {
	logger.Info("ListVideoDevices called - starting camera enumeration via v4l2-ctl")

	var videoDevices []*pb.VideoDevice

	// Find all video devices in /dev
	cmd := exec.Command("sh", "-c", "ls /dev/video* 2>/dev/null")
	output, err := cmd.Output()
	if err != nil {
		// No video devices found - not an error, just empty list
		logger.Info("No video devices found in /dev")
		return videoDevices, nil
	}

	devicePaths := strings.Split(strings.TrimSpace(string(output)), "\n")
	logger.Debug("Found video device paths", "count", len(devicePaths))

	for _, devicePath := range devicePaths {
		if devicePath == "" {
			continue
		}

		// Get device info using v4l2-ctl
		device, err := getV4L2DeviceInfo(logger, devicePath)
		if err != nil {
			logger.Warn("Failed to get device info", "device", devicePath, "error", err)
			continue
		}

		// Only include actual capture devices (skip metadata/output devices)
		if device != nil {
			videoDevices = append(videoDevices, device)
			logger.Debug("Found video capture device",
				"path", device.DevicePath,
				"name", device.Name,
				"driver", device.Driver,
			)
		}
	}

	logger.Info("Video device enumeration complete", "total_devices", len(videoDevices))
	return videoDevices, nil
}

// getV4L2DeviceInfo queries a single video device using v4l2-ctl
func getV4L2DeviceInfo(logger *slog.Logger, devicePath string) (*pb.VideoDevice, error) {
	// Run v4l2-ctl --device=<path> --info
	cmd := exec.Command("v4l2-ctl", "--device="+devicePath, "--info")
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("failed to run v4l2-ctl: %w", err)
	}

	device := &pb.VideoDevice{
		DevicePath: devicePath,
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	isCapture := false

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.HasPrefix(line, "Card type") {
			// Example: "Card type      : Integrated Camera"
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				device.Name = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Driver name") {
			// Example: "Driver name    : uvcvideo"
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				device.Driver = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Bus info") {
			// Example: "Bus info       : usb-0000:00:14.0-5"
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				device.BusInfo = strings.TrimSpace(parts[1])
			}
		} else if strings.Contains(line, "Video Capture") {
			// Device capabilities include Video Capture - it's a camera
			isCapture = true
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("failed to parse v4l2-ctl output: %w", err)
	}

	// Only return devices that support Video Capture (skip metadata/output devices)
	if !isCapture {
		logger.Debug("Skipping non-capture device", "path", devicePath)
		return nil, nil
	}

	// Use device path as fallback name if card type not found
	if device.Name == "" {
		device.Name = devicePath
	}

	return device, nil
}
