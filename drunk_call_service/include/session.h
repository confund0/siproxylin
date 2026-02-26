#pragma once

#include <memory>
#include <string>
#include <vector>
#include <queue>
#include <mutex>
#include <condition_variable>

// GStreamer headers
#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <gst/rtp/rtp.h>

// Internal headers
#include "ice_agent.h"
#include "dtls_srtp_handler.h"
#include "sdp_parser.h"

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
  static void OnPadAdded(GstElement* rtpbin, GstPad* pad, gpointer user_data);
  static GstFlowReturn OnAppsinkNewSample(GstAppSink* appsink, gpointer user_data);

  // IceAgent callbacks
  void OnIceCandidate(int component_id, const std::string& candidate,
                      const std::string& sdp_mid, int sdp_mline_index);
  void OnComponentStateChanged(int component_id, const std::string& state);
  void OnIceDataReceived(int component_id, const uint8_t* data, size_t len);
  void OnGatheringDone();

  // Helper methods
  bool SetupAudioPipeline();  // Create audio source/sink and link to rtpbin
  bool SetupRtpbin();  // Create and configure rtpbin element
  bool SetupAppsinkAppsrc();  // Create appsink/appsrc for RTP/RTCP flow
  void SetupAudioPlayback(GstPad* pad);  // Setup playback pipeline for incoming audio

  // Data flow helpers
  void SendRtpData(const uint8_t* data, size_t len);  // Encrypt and send via ICE Component 1
  void SendRtcpData(const uint8_t* data, size_t len);  // Encrypt and send via ICE Component 1 or 2
  void PushRtpData(const uint8_t* data, size_t len);  // Push decrypted RTP to rtpbin via appsrc
  void PushRtcpData(const uint8_t* data, size_t len);  // Push decrypted RTCP to rtpbin via appsrc

  // Internal event queue
  void PushEvent(const Event& event);
  Config config_;
  bool initialized_ = false;
  bool muted_ = false;
  bool rtcp_mux_ = true;  // RTCP-mux mode (use Component 1 for RTCP)

  // GStreamer components
  GstElement* pipeline_;
  GstElement* rtpbin_;  // Replaces webrtcbin_

  // Appsink elements (capture outgoing RTP/RTCP from rtpbin)
  GstElement* send_rtp_appsink_;
  GstElement* send_rtcp_appsink_;

  // Appsrc elements (inject incoming RTP/RTCP to rtpbin)
  GstElement* recv_rtp_appsrc_;
  GstElement* recv_rtcp_appsrc_;

  // Audio elements
  GstElement* audio_src_;  // Microphone capture
  GstElement* audio_sink_;  // Speaker playback

  // ICE agent (replaces webrtcbin's ICE)
  std::unique_ptr<IceAgent> ice_agent_;

  // DTLS-SRTP handler (Phase 3)
  std::unique_ptr<DtlsSrtpHandler> dtls_srtp_;

  // Event queue for streaming
  std::queue<Event> event_queue_;
  std::mutex event_mutex_;
  std::condition_variable event_cv_;

  // Negotiation signal synchronization
  bool negotiation_needed_ = false;
  std::mutex negotiation_mutex_;
  std::condition_variable negotiation_cv_;

  // Stats tracking for bandwidth calculation
  int64_t last_bytes_sent_ = 0;
  int64_t last_bytes_received_ = 0;
  std::chrono::steady_clock::time_point last_stats_time_;
};

}  // namespace drunk_call
