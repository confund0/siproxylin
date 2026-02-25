#pragma once

#include <string>

namespace drunk_call {

// Audio track configuration
struct AudioConfig {
  std::string microphone_device;
  std::string speakers_device;
  bool echo_cancel = true;
  int32_t echo_suppression_level = 1;
  bool noise_suppression = true;
  int32_t noise_suppression_level = 1;
  bool gain_control = true;
};

// Audio track wrapper (will integrate LibWebRTC AudioTrack)
class AudioTrack {
 public:
  explicit AudioTrack(const AudioConfig& config);
  ~AudioTrack();

  // Control
  void SetMuted(bool muted);
  bool IsMuted() const { return muted_; }

  // TODO: Return LibWebRTC AudioTrackInterface
  // rtc::scoped_refptr<webrtc::AudioTrackInterface> GetTrack();

 private:
  AudioConfig config_;
  bool muted_ = false;

  // TODO: Add LibWebRTC audio track member
  // rtc::scoped_refptr<webrtc::AudioTrackInterface> track_;
};

}  // namespace drunk_call
