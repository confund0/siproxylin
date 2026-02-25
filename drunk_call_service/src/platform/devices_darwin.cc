#include "device_manager.h"
#include "logger.h"

namespace drunk_call {

class DarwinDeviceManager : public DeviceManager {
 public:
  DarwinDeviceManager() {
    LOG_DEBUG("DarwinDeviceManager created");
  }

  ~DarwinDeviceManager() override = default;

  std::vector<AudioDevice> ListAudioDevices() override {
    LOG_DEBUG("Enumerating audio devices (macOS)");

    // TODO: Implement using CoreAudio or LibWebRTC APIs
    std::vector<AudioDevice> devices;
    return devices;
  }

  std::vector<VideoDevice> ListVideoDevices() override {
    LOG_DEBUG("Enumerating video devices (macOS)");

    // TODO: Implement using AVFoundation or LibWebRTC APIs
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
