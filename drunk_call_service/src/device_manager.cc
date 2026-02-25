#include "device_manager.h"
#include "logger.h"

// Include platform-specific implementations
#if defined(__linux__)
  #include "platform/devices_linux.h"
#elif defined(_WIN32)
  #include "platform/devices_windows.h"
#elif defined(__APPLE__)
  #include "platform/devices_darwin.h"
#endif

namespace drunk_call {

std::unique_ptr<DeviceManager> DeviceManager::Create() {
#if defined(__linux__)
  return std::make_unique<LinuxDeviceManager>();
#elif defined(_WIN32)
  return std::make_unique<WindowsDeviceManager>();
#elif defined(__APPLE__)
  return std::make_unique<DarwinDeviceManager>();
#else
  LOG_ERROR("Unsupported platform for DeviceManager");
  return nullptr;
#endif
}

}  // namespace drunk_call
