#include "session.h"
#include "logger.h"

namespace drunk_call {

Session::Session(const Config& config)
    : config_(config), pipeline_(nullptr), webrtcbin_(nullptr) {
  LOG_DEBUG("Session created: {}", config_.session_id);

  // Initialize GStreamer (idempotent, safe to call multiple times)
  gst_init(nullptr, nullptr);
}

Session::~Session() {
  if (initialized_) {
    Close();
  }
  LOG_DEBUG("Session destroyed: {}", config_.session_id);
}

bool Session::Initialize() {
  LOG_INFO("Initializing session: {}", config_.session_id);

  // Create pipeline
  pipeline_ = gst_pipeline_new(("pipeline-" + config_.session_id).c_str());
  if (!pipeline_) {
    LOG_ERROR("Failed to create GStreamer pipeline");
    return false;
  }

  // Create webrtcbin element
  webrtcbin_ = gst_element_factory_make("webrtcbin", ("webrtc-" + config_.session_id).c_str());
  if (!webrtcbin_) {
    LOG_ERROR("Failed to create webrtcbin element");
    gst_object_unref(pipeline_);
    return false;
  }

  // CRITICAL: Set bundle-policy to max-compat for separate ICE per media
  g_object_set(webrtcbin_, "bundle-policy", GST_WEBRTC_BUNDLE_POLICY_MAX_COMPAT, NULL);
  LOG_INFO("Set bundle-policy=max-compat - will generate non-BUNDLE offers");

  // Configure STUN/TURN servers
  if (!config_.turn_server.empty()) {
    GstElement* ice_transport = NULL;
    g_object_get(webrtcbin_, "ice-transport-policy", &ice_transport, NULL);

    // Set ICE transport policy (relay-only if requested)
    if (config_.relay_only) {
      g_object_set(webrtcbin_, "ice-transport-policy", GST_WEBRTC_ICE_TRANSPORT_POLICY_RELAY, NULL);
      LOG_INFO("ICE transport policy: RELAY only");
    } else {
      g_object_set(webrtcbin_, "ice-transport-policy", GST_WEBRTC_ICE_TRANSPORT_POLICY_ALL, NULL);
      LOG_INFO("ICE transport policy: ALL (P2P + relay)");
    }

    // Add TURN server using signal
    std::string turn_uri = "turn://" + config_.turn_username + ":" +
                          config_.turn_password + "@" + config_.turn_server;
    gboolean ret;
    g_signal_emit_by_name(webrtcbin_, "add-turn-server", turn_uri.c_str(), &ret);
    if (ret) {
      LOG_INFO("Added TURN server: turn://{}:***@{}", config_.turn_username, config_.turn_server);
    } else {
      LOG_WARN("Failed to add TURN server");
    }
  }

  // Add webrtcbin to pipeline
  gst_bin_add(GST_BIN(pipeline_), webrtcbin_);

  // TODO: Add audio/video sources

  initialized_ = true;
  LOG_INFO("Session initialized: {}", config_.session_id);
  return true;
}

void Session::Close() {
  if (!initialized_) {
    return;
  }

  LOG_INFO("Closing session: {}", config_.session_id);

  // Stop pipeline
  if (pipeline_) {
    gst_element_set_state(pipeline_, GST_STATE_NULL);
    gst_object_unref(pipeline_);
    pipeline_ = nullptr;
    webrtcbin_ = nullptr;  // webrtcbin is owned by pipeline
  }

  initialized_ = false;
  LOG_INFO("Session closed: {}", config_.session_id);
}

std::string Session::CreateOffer() {
  LOG_DEBUG("Creating offer for session: {}", config_.session_id);

  // TODO: Call PeerConnection::CreateOffer()
  // - Return real SDP with BundlePolicyMaxCompat (separate ICE per media)

  // Stub: Return minimal SDP for testing
  return "v=0\r\n"
         "o=- 0 0 IN IP4 0.0.0.0\r\n"
         "s=-\r\n"
         "t=0 0\r\n"
         "a=group:BUNDLE audio video\r\n"  // TODO: Remove BUNDLE for MaxCompat
         "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
         "c=IN IP4 0.0.0.0\r\n"
         "a=rtcp:9 IN IP4 0.0.0.0\r\n"
         "a=ice-ufrag:stub\r\n"
         "a=ice-pwd:stubpassword\r\n"
         "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
         "a=setup:actpass\r\n"
         "a=mid:audio\r\n"
         "a=sendrecv\r\n"
         "a=rtpmap:111 opus/48000/2\r\n";
}

std::string Session::CreateAnswer(const std::string& remote_sdp,
                                  bool offer_has_bundle) {
  LOG_DEBUG("Creating answer for session: {} (offer_has_bundle={})",
            config_.session_id, offer_has_bundle);

  // TODO: Call PeerConnection::CreateAnswer()
  // - Parse remote_sdp
  // - If !offer_has_bundle, don't use BUNDLE in answer (MaxCompat mode)
  // - Return real SDP

  // Stub: Return minimal SDP
  return "v=0\r\n"
         "o=- 0 0 IN IP4 0.0.0.0\r\n"
         "s=-\r\n"
         "t=0 0\r\n"
         "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
         "c=IN IP4 0.0.0.0\r\n"
         "a=rtcp:9 IN IP4 0.0.0.0\r\n"
         "a=ice-ufrag:stub\r\n"
         "a=ice-pwd:stubpassword\r\n"
         "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
         "a=setup:active\r\n"
         "a=mid:audio\r\n"
         "a=sendrecv\r\n"
         "a=rtpmap:111 opus/48000/2\r\n";
}

bool Session::SetRemoteDescription(const std::string& remote_sdp,
                                   const std::string& sdp_type) {
  LOG_DEBUG("Setting remote description for session: {} (type: {})",
            config_.session_id, sdp_type);

  // TODO: Call PeerConnection::SetRemoteDescription()
  // - Parse remote_sdp
  // - Apply to peer connection

  LOG_DEBUG("Remote description set successfully");
  return true;
}

bool Session::AddICECandidate(const std::string& candidate,
                              const std::string& sdp_mid,
                              int32_t sdp_mline_index) {
  LOG_DEBUG("Adding ICE candidate for session: {} (mid: {}, mline: {})",
            config_.session_id, sdp_mid, sdp_mline_index);

  // TODO: Call PeerConnection::AddIceCandidate()
  // - Parse candidate string
  // - Add to peer connection

  return true;
}

void Session::SetMute(bool muted) {
  muted_ = muted;
  LOG_INFO("Session {} mute state: {}", config_.session_id, muted);

  // TODO: Enable/disable audio track
}

Session::Stats Session::GetStats() {
  Stats stats;

  // TODO: Call PeerConnection::GetStats()
  // - Map RTCStatsReport to Stats struct

  // Stub values for testing
  stats.connection_state = "new";
  stats.ice_connection_state = "checking";
  stats.ice_gathering_state = "gathering";
  stats.bytes_sent = 0;
  stats.bytes_received = 0;
  stats.bandwidth_kbps = 0;
  stats.connection_type = "unknown";

  return stats;
}

}  // namespace drunk_call
