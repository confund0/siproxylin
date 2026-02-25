#include "platform/devices_linux.h"
#include "logger.h"

#include <filesystem>
#include <fstream>

namespace drunk_call {

LinuxDeviceManager::LinuxDeviceManager() {
  LOG_DEBUG("LinuxDeviceManager created");
}

std::vector<AudioDevice> LinuxDeviceManager::ListAudioDevices() {
  LOG_DEBUG("Enumerating audio devices (Linux)");

  std::vector<AudioDevice> devices;

  // TODO: Implement proper audio device enumeration
  // - Use ALSA API (snd_device_name_hint)
  // - Or LibWebRTC AudioDeviceModule APIs
  // - Parse /proc/asound/cards
  // - Query PulseAudio/PipeWire

  // Stub: Return dummy devices for testing
  devices.push_back(AudioDevice{
      "default",
      "Default Audio Device",
      "Audio/Sink"
  });

  devices.push_back(AudioDevice{
      "default",
      "Default Audio Device",
      "Audio/Source"
  });

  LOG_INFO("Found {} audio devices", devices.size());
  return devices;
}

std::vector<VideoDevice> LinuxDeviceManager::ListVideoDevices() {
  LOG_DEBUG("Enumerating video devices (Linux)");

  std::vector<VideoDevice> devices;

  // TODO: Implement proper video device enumeration
  // - Scan /dev/video* devices
  // - Use V4L2 ioctl to query device info
  // - Or use LibWebRTC VideoCaptureModule APIs

  // Stub: Scan /dev/video* files
  namespace fs = std::filesystem;
  for (int i = 0; i < 10; ++i) {
    std::string device_path = "/dev/video" + std::to_string(i);
    if (fs::exists(device_path)) {
      devices.push_back(VideoDevice{
          device_path,
          "Camera " + std::to_string(i),
          "uvcvideo",  // Common driver, should query from V4L2
          "usb-0000:00:00.0-" + std::to_string(i)
      });
    }
  }

  LOG_INFO("Found {} video devices", devices.size());
  return devices;
}

std::string LinuxDeviceManager::GetDefaultMicrophone() {
  return "default";
}

std::string LinuxDeviceManager::GetDefaultSpeakers() {
  return "default";
}

std::string LinuxDeviceManager::GetDefaultCamera() {
  // Return first available /dev/video device
  namespace fs = std::filesystem;
  for (int i = 0; i < 10; ++i) {
    std::string device_path = "/dev/video" + std::to_string(i);
    if (fs::exists(device_path)) {
      return device_path;
    }
  }
  return "";
}

}  // namespace drunk_call
