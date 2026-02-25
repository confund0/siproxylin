#include "video_track.h"
#include "logger.h"

namespace drunk_call {

VideoTrack::VideoTrack(const VideoConfig& config) : config_(config) {
  if (config_.camera_device.empty()) {
    LOG_DEBUG("VideoTrack created (no camera)");
    return;
  }

  LOG_DEBUG("VideoTrack created (camera: {}, {}x{} @ {}fps)",
            config_.camera_device, config_.width, config_.height, config_.fps);

  // TODO: Create LibWebRTC VideoTrack
  // - Open camera device
  // - Configure capture (width, height, fps)
  // - Create video source and track
  // - Set up VP8 encoder
}

VideoTrack::~VideoTrack() {
  LOG_DEBUG("VideoTrack destroyed");

  // TODO: Cleanup LibWebRTC video track and capture
}

void VideoTrack::SetEnabled(bool enabled) {
  enabled_ = enabled;
  LOG_DEBUG("VideoTrack enabled: {}", enabled);

  // TODO: Enable/disable video track
  // if (track_) track_->set_enabled(enabled);
}

}  // namespace drunk_call
