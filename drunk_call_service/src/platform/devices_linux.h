#pragma once

#include "device_manager.h"

namespace drunk_call {

class LinuxDeviceManager : public DeviceManager {
 public:
  LinuxDeviceManager();
  ~LinuxDeviceManager() override = default;

  std::vector<AudioDevice> ListAudioDevices() override;
  std::vector<VideoDevice> ListVideoDevices() override;
  std::string GetDefaultMicrophone() override;
  std::string GetDefaultSpeakers() override;
  std::string GetDefaultCamera() override;
};

}  // namespace drunk_call
