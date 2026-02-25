#pragma once

#include <memory>
#include <string>
#include <vector>

namespace drunk_call {

// Audio device information
struct AudioDevice {
  std::string name;         // Internal device ID
  std::string description;  // User-friendly name
  std::string device_class; // "Audio/Source" or "Audio/Sink"
};

// Video device information
struct VideoDevice {
  std::string device_path;  // e.g., "/dev/video0"
  std::string name;         // e.g., "Integrated Camera"
  std::string driver;       // e.g., "uvcvideo"
  std::string bus_info;     // e.g., "usb-0000:00:14.0-5"
};

// Device enumeration (platform-specific implementations)
class DeviceManager {
 public:
  DeviceManager() = default;
  virtual ~DeviceManager() = default;

  // Audio device enumeration
  virtual std::vector<AudioDevice> ListAudioDevices() = 0;

  // Video device enumeration
  virtual std::vector<VideoDevice> ListVideoDevices() = 0;

  // Get default devices
  virtual std::string GetDefaultMicrophone() = 0;
  virtual std::string GetDefaultSpeakers() = 0;
  virtual std::string GetDefaultCamera() = 0;

  // Factory method (creates platform-specific instance)
  static std::unique_ptr<DeviceManager> Create();
};

}  // namespace drunk_call
