#include "audio_track.h"
#include "logger.h"

namespace drunk_call {

AudioTrack::AudioTrack(const AudioConfig& config) : config_(config) {
  LOG_DEBUG("AudioTrack created (mic: {}, speakers: {})",
            config_.microphone_device.empty() ? "default" : config_.microphone_device,
            config_.speakers_device.empty() ? "default" : config_.speakers_device);

  // TODO: Create LibWebRTC AudioTrack
  // - Configure AudioProcessing options (echo cancel, noise suppression, gain control)
  // - Set device IDs
  // - Create audio source and track
}

AudioTrack::~AudioTrack() {
  LOG_DEBUG("AudioTrack destroyed");

  // TODO: Cleanup LibWebRTC audio track
}

void AudioTrack::SetMuted(bool muted) {
  muted_ = muted;
  LOG_DEBUG("AudioTrack muted: {}", muted);

  // TODO: Enable/disable audio track
  // track_->set_enabled(!muted);
}

}  // namespace drunk_call
