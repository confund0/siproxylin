#include "device_manager.h"
#include "logger.h"

namespace drunk_call {

class WindowsDeviceManager : public DeviceManager {
 public:
  WindowsDeviceManager() {
    LOG_DEBUG("WindowsDeviceManager created");
  }

  ~WindowsDeviceManager() override = default;

  std::vector<AudioDevice> ListAudioDevices() override {
    LOG_DEBUG("Enumerating audio devices (Windows)");

    // TODO: Implement using Media Foundation or LibWebRTC APIs
    std::vector<AudioDevice> devices;
    return devices;
  }

  std::vector<VideoDevice> ListVideoDevices() override {
    LOG_DEBUG("Enumerating video devices (Windows)");

    // TODO: Implement using Media Foundation or LibWebRTC APIs
    std::vector<VideoDevice> devices;
    return devices;
  }

  std::string GetDefaultMicrophone() override {
    return "";
  }

  std::string GetDefaultSpeakers() override {
    return "";
  }

  std::string GetDefaultCamera() override {
    return "";
  }
};

}  // namespace drunk_call
