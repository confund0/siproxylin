#pragma once

#include <memory>
#include <string>
#include <vector>
#include <queue>
#include <mutex>
#include <condition_variable>

// GStreamer headers
#include <gst/gst.h>
#include <gst/webrtc/webrtc.h>

namespace drunk_call {

// WebRTC session wrapper (placeholder - will integrate LibWebRTC)
class Session {
 public:
  struct Config {
    std::string session_id;
    std::string peer_jid;
    std::string microphone_device;
    std::string speakers_device;
    std::string camera_device;

    // Proxy settings
    std::string proxy_host;
    int32_t proxy_port = 0;
    std::string proxy_username;
    std::string proxy_password;
    std::string proxy_type;

    // TURN settings
    std::string turn_server;
    std::string turn_username;
    std::string turn_password;
    bool relay_only = true;

    // Audio processing
    bool echo_cancel = true;
    int32_t echo_suppression_level = 1;
    bool noise_suppression = true;
    int32_t noise_suppression_level = 1;
    bool gain_control = true;

    // BUNDLE policy
    bool offer_has_bundle = true;
  };

  explicit Session(const Config& config);
  ~Session();

  // Accessors
  const std::string& session_id() const { return config_.session_id; }
  const Config& config() const { return config_; }

  // Lifecycle (stubbed for now)
  bool Initialize();
  void Close();

  // SDP operations (stubbed)
  std::string CreateOffer();
  std::string CreateAnswer(const std::string& remote_sdp, bool offer_has_bundle);
  bool SetRemoteDescription(const std::string& remote_sdp, const std::string& sdp_type);

  // ICE operations (stubbed)
  bool AddICECandidate(const std::string& candidate,
                       const std::string& sdp_mid,
                       int32_t sdp_mline_index);

  // Control (stubbed)
  void SetMute(bool muted);

  // Stats (stubbed)
  struct Stats {
    std::string connection_state;
    std::string ice_connection_state;
    std::string ice_gathering_state;
    int64_t bytes_sent = 0;
    int64_t bytes_received = 0;
    int64_t bandwidth_kbps = 0;
    std::vector<std::string> local_candidates;
    std::vector<std::string> remote_candidates;
    std::string connection_type;
  };
  Stats GetStats();

  // Event streaming
  struct Event {
    enum Type {
      ICE_CANDIDATE,
      CONNECTION_STATE_CHANGE,
      ICE_CONNECTION_STATE_CHANGE,
      ICE_GATHERING_STATE_CHANGE
    };

    Type type;
    std::string data;  // For ICE_CANDIDATE: the candidate string
    int32_t sdp_mline_index = 0;
    std::string sdp_mid;
  };

  // Pop next event (blocking with timeout)
  // Returns true if event was retrieved, false on timeout
  bool PopEvent(Event& event, int timeout_ms = 1000);

  // Check if there are pending events
  bool HasPendingEvents();

 private:
  // GStreamer signal callbacks (must be static)
  static void OnIceCandidate(GstElement* webrtcbin, guint mline_index, gchar* candidate, gpointer user_data);
  static void OnConnectionStateChange(GstElement* webrtcbin, GParamSpec* pspec, gpointer user_data);
  static void OnIceConnectionStateChange(GstElement* webrtcbin, GParamSpec* pspec, gpointer user_data);
  static void OnIceGatheringStateChange(GstElement* webrtcbin, GParamSpec* pspec, gpointer user_data);

  // Internal event queue
  void PushEvent(const Event& event);
  Config config_;
  bool initialized_ = false;
  bool muted_ = false;

  // GStreamer components
  GstElement* pipeline_;
  GstElement* webrtcbin_;

  // Event queue for streaming
  std::queue<Event> event_queue_;
  std::mutex event_mutex_;
  std::condition_variable event_cv_;
};

}  // namespace drunk_call
