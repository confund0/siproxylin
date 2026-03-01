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
#include "srtp_session.h"
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

    // SDES-SRTP encryption (Dino's approach: use SDES for immediate encryption)
    std::string sdes_local_key_params;   // Our SDES key (format: "inline:BASE64")
    std::string sdes_remote_key_params;  // Remote SDES key (format: "inline:BASE64")
    std::string sdes_crypto_suite;       // Crypto suite (e.g., "AES_CM_128_HMAC_SHA1_80")
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

  // Update SDES remote key (for initiator after receiving session-accept)
  bool UpdateSdesRemoteKey(const std::string& sdes_remote_key_params,
                           const std::string& sdes_crypto_suite);

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
  void OnGatheringTimeout();  // Safety timeout if gathering hangs

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

  // Flush buffered candidates to event queue
  void FlushCandidateBuffer();

  // Drain queued remote candidates (candidates that arrived before ICE stream creation)
  void DrainRemoteCandidateQueue();
  Config config_;
  bool initialized_ = false;
  bool muted_ = false;
  bool rtcp_mux_ = false;  // NO rtcp-mux (2 components: RTP=1, RTCP=2 for Conversations.im)

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

  // SDES-SRTP handler (Dino's approach: use SDES for immediate encryption before DTLS)
  std::unique_ptr<SrtpSession> sdes_srtp_;
  bool sdes_ready_ = false;

  // Encryption mode selection (Dino's logic: SDES vs DTLS-SRTP)
  // Rule: use_dtls_srtp_ = (peer_has_fingerprint || is_offerer_)
  bool use_dtls_srtp_ = false;  // Which encryption mode to use for this session
  bool is_offerer_ = false;      // Are we the offerer (CreateOffer) or answerer (CreateAnswer)?

  // Event queue for streaming
  std::queue<Event> event_queue_;
  std::mutex event_mutex_;
  std::condition_variable event_cv_;

  // SDP negotiation state
  std::string local_mid_;  // The mid attribute for our media (set in CreateOffer/CreateAnswer)

  // Connection state tracking (Dino pattern: only connected after receiving first RTP)
  bool connection_state_emitted_ = false;

  // Candidate buffering (like Go Pion and webrtcbin implementations)
  // Buffer candidates until CreateOffer/CreateAnswer completes
  struct BufferedCandidate {
    int component_id;
    std::string candidate;
    std::string sdp_mid;
    int sdp_mline_index;
  };
  std::vector<BufferedCandidate> candidate_buffer_;
  bool buffer_candidates_ = true;  // Buffer until session setup completes
  std::mutex candidate_buffer_mutex_;

  // Candidate gathering buffer (NEW - wait for gathering-done OR timeout)
  // Buffers ALL candidates until gathering completes, then emits as single batch
  std::vector<BufferedCandidate> gathering_buffer_;
  bool gathering_done_ = false;
  bool gathering_timeout_started_ = false;
  std::atomic<bool> gathering_timeout_cancelled_{false};
  std::unique_ptr<std::thread> gathering_timeout_thread_;
  std::mutex gathering_buffer_mutex_;
  static constexpr int GATHERING_TIMEOUT_MS = 10000;  // 10 seconds safety timeout

  // Remote candidate queue (for candidates arriving before ICE stream creation)
  // Separate from local candidate buffer above
  struct QueuedRemoteCandidate {
    std::string candidate;
    std::string sdp_mid;
    int32_t sdp_mline_index;
  };
  std::vector<QueuedRemoteCandidate> remote_candidate_queue_;
  std::mutex remote_candidate_queue_mutex_;

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
