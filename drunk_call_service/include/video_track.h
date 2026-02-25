#pragma once

#include <string>

namespace drunk_call {

// Video track configuration
struct VideoConfig {
  std::string camera_device;  // e.g., "/dev/video0" or empty for no video
  int32_t width = 640;
  int32_t height = 480;
  int32_t fps = 30;
};

// Video track wrapper (will integrate LibWebRTC VideoTrack)
class VideoTrack {
 public:
  explicit VideoTrack(const VideoConfig& config);
  ~VideoTrack();

  // Control
  void SetEnabled(bool enabled);
  bool IsEnabled() const { return enabled_; }

  // Device info
  const std::string& device() const { return config_.camera_device; }

  // TODO: Return LibWebRTC VideoTrackInterface
  // rtc::scoped_refptr<webrtc::VideoTrackInterface> GetTrack();

 private:
  VideoConfig config_;
  bool enabled_ = true;

  // TODO: Add LibWebRTC video track member
  // rtc::scoped_refptr<webrtc::VideoTrackInterface> track_;
};

}  // namespace drunk_call
