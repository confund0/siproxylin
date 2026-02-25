#include "session.h"
#include "logger.h"
#include <sstream>
#include <iomanip>
#include <cctype>

namespace drunk_call {

// URL encode a string according to RFC 3986
// Encodes all characters except: A-Z a-z 0-9 - _ . ~
static std::string url_encode(const std::string& value) {
  std::ostringstream escaped;
  escaped.fill('0');
  escaped << std::hex;

  for (unsigned char c : value) {
    // Keep alphanumeric and safe characters intact
    if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      escaped << c;
    } else {
      // Encode special characters as %XX
      escaped << std::uppercase;
      escaped << '%' << std::setw(2) << static_cast<int>(c);
      escaped << std::nouppercase;
    }
  }

  return escaped.str();
}

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

  // CRITICAL: Bundle policy determines ICE transport behavior
  // - NONE: Separate ICE per media (required for Dino compatibility)
  // - MAX_COMPAT: Has BUNDLE group but not bundle-only (modern clients that support both)
  // - MAX_BUNDLE: Single ICE transport (browsers, Conversations.im)
  //
  // TODO: Make this configurable per-session via CreateSessionRequest.prefer_bundle
  // For now, use NONE for maximum compatibility with traditional Jingle clients
  g_object_set(webrtcbin_, "bundle-policy", GST_WEBRTC_BUNDLE_POLICY_NONE, NULL);
  LOG_INFO("Set bundle-policy=NONE - will generate non-BUNDLE offers with separate ICE per media");

  // Configure STUN/TURN servers
  // IMPORTANT: With relay-only mode, we still need STUN to discover the relay candidates
  if (!config_.turn_server.empty()) {
    // Extract host from TURN server for STUN
    std::string stun_host = config_.turn_server;
    if (stun_host.find("turn:") == 0) {
      stun_host = stun_host.substr(5);
    }
    size_t query_pos = stun_host.find('?');
    if (query_pos != std::string::npos) {
      stun_host = stun_host.substr(0, query_pos);
    }
    // Remove port if present and extract just hostname
    size_t colon_pos = stun_host.find(':');
    if (colon_pos != std::string::npos) {
      std::string port_str = stun_host.substr(colon_pos + 1);
      stun_host = stun_host.substr(0, colon_pos);
      // Set STUN server (required even for relay-only to discover relay address)
      g_object_set(webrtcbin_, "stun-server", ("stun://" + stun_host + ":" + port_str).c_str(), NULL);
      LOG_INFO("Set STUN server: stun://{}:{}", stun_host, port_str);
    }
  }

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
    // Python passes "turn:host:port?transport=udp", extract host:port
    std::string host_port = config_.turn_server;

    // Remove "turn:" prefix if present
    if (host_port.find("turn:") == 0) {
      host_port = host_port.substr(5);
    }

    // Remove query parameters (?transport=udp)
    size_t query_pos = host_port.find('?');
    if (query_pos != std::string::npos) {
      host_port = host_port.substr(0, query_pos);
    }

    // Construct proper TURN URI with URL-encoded credentials: turn://username:password@host:port
    // RFC 3986 requires special characters in credentials to be percent-encoded
    std::string encoded_username = url_encode(config_.turn_username);
    std::string encoded_password = url_encode(config_.turn_password);
    std::string turn_uri = "turn://" + encoded_username + ":" +
                          encoded_password + "@" + host_port;

    gboolean ret;
    g_signal_emit_by_name(webrtcbin_, "add-turn-server", turn_uri.c_str(), &ret);
    if (ret) {
      LOG_INFO("Added TURN server: turn://{}:***@{}", config_.turn_username, host_port);
    } else {
      LOG_WARN("Failed to add TURN server to webrtcbin (check credentials encoding)");
    }
  }

  // Add webrtcbin to pipeline
  gst_bin_add(GST_BIN(pipeline_), webrtcbin_);

  // Connect signal handlers for ICE and connection state
  g_signal_connect(webrtcbin_, "on-ice-candidate",
                   G_CALLBACK(OnIceCandidate), this);
  g_signal_connect(webrtcbin_, "notify::connection-state",
                   G_CALLBACK(OnConnectionStateChange), this);
  g_signal_connect(webrtcbin_, "notify::ice-connection-state",
                   G_CALLBACK(OnIceConnectionStateChange), this);
  g_signal_connect(webrtcbin_, "notify::ice-gathering-state",
                   G_CALLBACK(OnIceGatheringStateChange), this);

  LOG_INFO("Connected webrtcbin signal handlers for ICE and state changes");

  // Add audio test source (sine wave for testing)
  GstElement* audiotestsrc = gst_element_factory_make("audiotestsrc", "audiosrc");
  GstElement* audioconvert = gst_element_factory_make("audioconvert", "audioconv");
  GstElement* audioresample = gst_element_factory_make("audioresample", "audioresample");
  GstElement* opusenc = gst_element_factory_make("opusenc", "opusenc");
  GstElement* rtpopuspay = gst_element_factory_make("rtpopuspay", "rtpopuspay");

  if (!audiotestsrc || !audioconvert || !audioresample || !opusenc || !rtpopuspay) {
    LOG_ERROR("Failed to create audio pipeline elements");
    gst_object_unref(pipeline_);
    return false;
  }

  // Configure audiotestsrc to produce a sine wave
  g_object_set(audiotestsrc, "is-live", TRUE, "wave", 0, NULL);  // wave=0 is sine

  // Add audio elements to pipeline
  gst_bin_add_many(GST_BIN(pipeline_), audiotestsrc, audioconvert, audioresample,
                   opusenc, rtpopuspay, NULL);

  // Link audio pipeline: audiotestsrc -> audioconvert -> audioresample -> opusenc -> rtpopuspay -> webrtcbin
  if (!gst_element_link_many(audiotestsrc, audioconvert, audioresample, opusenc, rtpopuspay, NULL)) {
    LOG_ERROR("Failed to link audio elements");
    gst_object_unref(pipeline_);
    return false;
  }

  // Add audio transceiver explicitly (required for webrtcbin to include audio in offer)
  GArray* transceivers = NULL;
  g_signal_emit_by_name(webrtcbin_, "get-transceivers", &transceivers);
  LOG_INFO("webrtcbin has {} transceivers before adding audio", transceivers ? transceivers->len : 0);
  if (transceivers) g_array_unref(transceivers);

  GstWebRTCRTPTransceiver* audio_transceiver = NULL;
  g_signal_emit_by_name(webrtcbin_, "add-transceiver", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV,
                        gst_caps_from_string("application/x-rtp,media=audio"), &audio_transceiver);

  if (!audio_transceiver) {
    LOG_ERROR("Failed to add audio transceiver");
    gst_object_unref(pipeline_);
    return false;
  }

  LOG_INFO("Added audio transceiver");
  gst_object_unref(audio_transceiver);

  // Request src pad from rtpopuspay and sink pad from webrtcbin
  GstPad* audio_src_pad = gst_element_get_static_pad(rtpopuspay, "src");
  GstPad* audio_sink_pad = gst_element_request_pad_simple(webrtcbin_, "sink_0");

  if (gst_pad_link(audio_src_pad, audio_sink_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link audio to webrtcbin");
    gst_object_unref(audio_src_pad);
    gst_object_unref(audio_sink_pad);
    gst_object_unref(pipeline_);
    return false;
  }

  gst_object_unref(audio_src_pad);
  gst_object_unref(audio_sink_pad);
  LOG_INFO("Audio pipeline linked to webrtcbin sink_0");

  // Add video test source if camera is requested
  if (!config_.camera_device.empty()) {
    GstElement* videotestsrc = gst_element_factory_make("videotestsrc", "videosrc");
    GstElement* videoconvert = gst_element_factory_make("videoconvert", "videoconv");
    GstElement* vp8enc = gst_element_factory_make("vp8enc", "vp8enc");
    GstElement* rtpvp8pay = gst_element_factory_make("rtpvp8pay", "rtpvp8pay");

    if (!videotestsrc || !videoconvert || !vp8enc || !rtpvp8pay) {
      LOG_WARN("Failed to create video pipeline elements, continuing without video");
    } else {
      // Configure videotestsrc to produce a test pattern
      g_object_set(videotestsrc, "is-live", TRUE, "pattern", 0, NULL);  // pattern=0 is SMPTE color bars

      // Add video transceiver explicitly
      GstWebRTCRTPTransceiver* video_transceiver = NULL;
      g_signal_emit_by_name(webrtcbin_, "add-transceiver", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV,
                            gst_caps_from_string("application/x-rtp,media=video"), &video_transceiver);

      if (!video_transceiver) {
        LOG_WARN("Failed to add video transceiver, continuing without video");
      } else {
        LOG_INFO("Added video transceiver");
        gst_object_unref(video_transceiver);

        // Add video elements to pipeline
        gst_bin_add_many(GST_BIN(pipeline_), videotestsrc, videoconvert, vp8enc, rtpvp8pay, NULL);

        // Link video pipeline: videotestsrc -> videoconvert -> vp8enc -> rtpvp8pay -> webrtcbin
        if (!gst_element_link_many(videotestsrc, videoconvert, vp8enc, rtpvp8pay, NULL)) {
          LOG_WARN("Failed to link video elements, continuing without video");
        } else {
          // Request src pad from rtpvp8pay and sink pad from webrtcbin (transceiver 1, so sink_1)
          GstPad* video_src_pad = gst_element_get_static_pad(rtpvp8pay, "src");
          GstPad* video_sink_pad = gst_element_request_pad_simple(webrtcbin_, "sink_1");

          if (gst_pad_link(video_src_pad, video_sink_pad) != GST_PAD_LINK_OK) {
            LOG_WARN("Failed to link video to webrtcbin, continuing without video");
          } else {
            LOG_INFO("Video pipeline linked to webrtcbin sink_1");
          }

          gst_object_unref(video_src_pad);
          gst_object_unref(video_sink_pad);
        }
      }
    }
  }

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
  LOG_INFO("Creating offer for session: {}", config_.session_id);

  if (!initialized_) {
    LOG_ERROR("Cannot create offer: session not initialized");
    return "";
  }

  // Set pipeline to PLAYING state (required before creating offer)
  GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
  if (ret == GST_STATE_CHANGE_FAILURE) {
    LOG_ERROR("Failed to set pipeline to PLAYING state");
    return "";
  }

  // Wait for state change to complete (with timeout)
  ret = gst_element_get_state(pipeline_, NULL, NULL, 5 * GST_SECOND);
  if (ret == GST_STATE_CHANGE_FAILURE) {
    LOG_ERROR("Pipeline failed to reach PLAYING state");
    return "";
  }

  LOG_INFO("Pipeline is now in PLAYING state");

  // Create a promise for the offer
  GstPromise* promise = gst_promise_new();

  // Emit create-offer signal on webrtcbin
  LOG_INFO("Calling webrtcbin create-offer signal...");
  g_signal_emit_by_name(webrtcbin_, "create-offer", NULL, promise);

  // Wait for promise to complete (blocking)
  GstPromiseResult promise_result = gst_promise_wait(promise);
  if (promise_result != GST_PROMISE_RESULT_REPLIED) {
    LOG_ERROR("create-offer promise failed with result: {}", promise_result);
    gst_promise_unref(promise);
    return "";
  }

  LOG_INFO("create-offer promise completed successfully");

  // Get the reply structure
  const GstStructure* reply = gst_promise_get_reply(promise);
  if (!reply) {
    LOG_ERROR("create-offer promise has no reply");
    gst_promise_unref(promise);
    return "";
  }

  // Extract the session description
  GstWebRTCSessionDescription* offer = NULL;
  gst_structure_get(reply, "offer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &offer, NULL);

  if (!offer) {
    LOG_ERROR("No offer in create-offer reply");
    gst_promise_unref(promise);
    return "";
  }

  // Convert SDP to string
  gchar* sdp_text = gst_sdp_message_as_text(offer->sdp);
  std::string sdp_string(sdp_text);
  g_free(sdp_text);

  LOG_INFO("Generated SDP offer ({} bytes)", sdp_string.size());
  LOG_DEBUG("SDP Offer:\n{}", sdp_string);

  // Set local description on webrtcbin
  LOG_INFO("Setting local description...");
  GstPromise* local_desc_promise = gst_promise_new();
  g_signal_emit_by_name(webrtcbin_, "set-local-description", offer, local_desc_promise);

  // Wait for set-local-description to complete
  promise_result = gst_promise_wait(local_desc_promise);
  if (promise_result != GST_PROMISE_RESULT_REPLIED) {
    LOG_WARN("set-local-description promise failed with result: {}", promise_result);
  } else {
    LOG_INFO("Local description set successfully");
  }

  gst_promise_unref(local_desc_promise);
  gst_webrtc_session_description_free(offer);
  gst_promise_unref(promise);

  // Analyze the SDP for BUNDLE behavior
  LOG_INFO("Analyzing SDP for BUNDLE behavior...");
  if (sdp_string.find("a=group:BUNDLE") != std::string::npos) {
    LOG_WARN("SDP contains BUNDLE group - NOT max-compat!");
  } else {
    LOG_INFO("SDP has NO BUNDLE group - max-compat working!");
  }

  // Count ice-ufrag occurrences to verify separate credentials per media
  size_t ufrag_count = 0;
  size_t pos = 0;
  while ((pos = sdp_string.find("a=ice-ufrag:", pos)) != std::string::npos) {
    ufrag_count++;
    pos += 12;
  }
  LOG_INFO("Found {} ice-ufrag attributes in SDP", ufrag_count);

  return sdp_string;
}

std::string Session::CreateAnswer(const std::string& remote_sdp,
                                  bool offer_has_bundle) {
  LOG_INFO("Creating answer for session: {} (offer_has_bundle={})",
            config_.session_id, offer_has_bundle);

  if (!initialized_) {
    LOG_ERROR("Cannot create answer: session not initialized");
    return "";
  }

  // Set remote description (offer) from parameter
  // For incoming calls, Python calls CreateAnswer directly with the offer SDP
  if (!remote_sdp.empty()) {
    LOG_INFO("Setting remote offer before creating answer ({} bytes)", remote_sdp.size());
    LOG_DEBUG("Remote offer SDP:\n{}", remote_sdp);

    GstSDPMessage* remote_sdp_msg = NULL;
    GstSDPResult sdp_result = gst_sdp_message_new_from_text(remote_sdp.c_str(), &remote_sdp_msg);
    if (sdp_result != GST_SDP_OK) {
      LOG_ERROR("Failed to parse remote SDP offer: error code {}", sdp_result);
      return "";
    }

    GstWebRTCSessionDescription* offer = gst_webrtc_session_description_new(
        GST_WEBRTC_SDP_TYPE_OFFER, remote_sdp_msg);

    // Use a promise to check if set-remote-description succeeds
    GstPromise* remote_promise = gst_promise_new();
    g_signal_emit_by_name(webrtcbin_, "set-remote-description", offer, remote_promise);

    GstPromiseResult remote_result = gst_promise_wait(remote_promise);
    if (remote_result != GST_PROMISE_RESULT_REPLIED) {
      LOG_ERROR("set-remote-description failed with result: {}", remote_result);
      gst_promise_unref(remote_promise);
      gst_webrtc_session_description_free(offer);
      return "";
    }
    gst_promise_unref(remote_promise);
    gst_webrtc_session_description_free(offer);
    LOG_INFO("Remote offer set successfully");
  }

  // The offer_has_bundle parameter is informational (we already set bundle-policy at init)

  // Set pipeline to PLAYING state if not already
  GstState current_state, pending_state;
  gst_element_get_state(pipeline_, &current_state, &pending_state, 0);

  if (current_state != GST_STATE_PLAYING) {
    GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE) {
      LOG_ERROR("Failed to set pipeline to PLAYING state");
      return "";
    }

    // Wait for state change to complete
    ret = gst_element_get_state(pipeline_, NULL, NULL, 5 * GST_SECOND);
    if (ret == GST_STATE_CHANGE_FAILURE) {
      LOG_ERROR("Pipeline failed to reach PLAYING state");
      return "";
    }

    LOG_INFO("Pipeline is now in PLAYING state");
  }

  // Create a promise for the answer
  GstPromise* promise = gst_promise_new();

  // Emit create-answer signal on webrtcbin
  LOG_INFO("Calling webrtcbin create-answer signal...");
  g_signal_emit_by_name(webrtcbin_, "create-answer", NULL, promise);

  // Wait for promise to complete
  GstPromiseResult promise_result = gst_promise_wait(promise);
  if (promise_result != GST_PROMISE_RESULT_REPLIED) {
    LOG_ERROR("create-answer promise failed with result: {}", promise_result);
    gst_promise_unref(promise);
    return "";
  }

  LOG_INFO("create-answer promise completed successfully");

  // Get the reply structure
  const GstStructure* reply = gst_promise_get_reply(promise);
  if (!reply) {
    LOG_ERROR("create-answer promise has no reply");
    gst_promise_unref(promise);
    return "";
  }

  // Debug: log the reply structure
  gchar* reply_str = gst_structure_to_string(reply);
  LOG_DEBUG("create-answer reply structure: {}", reply_str ? reply_str : "NULL");
  g_free(reply_str);

  // Extract the session description
  GstWebRTCSessionDescription* answer = NULL;
  gst_structure_get(reply, "answer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &answer, NULL);

  if (!answer) {
    LOG_ERROR("No answer in create-answer reply (check if remote offer was set correctly)");
    gst_promise_unref(promise);
    return "";
  }

  // Convert SDP to string
  gchar* sdp_text = gst_sdp_message_as_text(answer->sdp);
  std::string sdp_string(sdp_text);
  g_free(sdp_text);

  LOG_INFO("Generated SDP answer ({} bytes)", sdp_string.size());
  LOG_DEBUG("SDP Answer:\n{}", sdp_string);

  // Set local description on webrtcbin
  LOG_INFO("Setting local description...");
  GstPromise* local_desc_promise = gst_promise_new();
  g_signal_emit_by_name(webrtcbin_, "set-local-description", answer, local_desc_promise);

  // Wait for set-local-description to complete
  promise_result = gst_promise_wait(local_desc_promise);
  if (promise_result != GST_PROMISE_RESULT_REPLIED) {
    LOG_WARN("set-local-description promise failed with result: {}", promise_result);
  } else {
    LOG_INFO("Local description set successfully");
  }

  gst_promise_unref(local_desc_promise);
  gst_webrtc_session_description_free(answer);
  gst_promise_unref(promise);

  // Analyze the SDP
  LOG_INFO("Analyzing SDP answer for BUNDLE behavior...");
  if (sdp_string.find("a=group:BUNDLE") != std::string::npos) {
    LOG_WARN("SDP answer contains BUNDLE group");
  } else {
    LOG_INFO("SDP answer has NO BUNDLE group");
  }

  return sdp_string;
}

bool Session::SetRemoteDescription(const std::string& remote_sdp,
                                   const std::string& sdp_type) {
  LOG_INFO("Setting remote description for session: {} (type: {})",
            config_.session_id, sdp_type);

  if (!initialized_) {
    LOG_ERROR("Cannot set remote description: session not initialized");
    return false;
  }

  // Parse SDP string into GstSDPMessage
  GstSDPMessage* sdp_msg = NULL;
  GstSDPResult result = gst_sdp_message_new(&sdp_msg);
  if (result != GST_SDP_OK) {
    LOG_ERROR("Failed to create GstSDPMessage");
    return false;
  }

  result = gst_sdp_message_parse_buffer((const guint8*)remote_sdp.c_str(),
                                        remote_sdp.size(), sdp_msg);
  if (result != GST_SDP_OK) {
    LOG_ERROR("Failed to parse SDP: {}", remote_sdp);
    gst_sdp_message_free(sdp_msg);
    return false;
  }

  LOG_DEBUG("Parsed remote SDP successfully ({} bytes)", remote_sdp.size());

  // Determine SDP type
  GstWebRTCSDPType type;
  if (sdp_type == "offer") {
    type = GST_WEBRTC_SDP_TYPE_OFFER;
  } else if (sdp_type == "answer") {
    type = GST_WEBRTC_SDP_TYPE_ANSWER;
  } else if (sdp_type == "pranswer") {
    type = GST_WEBRTC_SDP_TYPE_PRANSWER;
  } else if (sdp_type == "rollback") {
    type = GST_WEBRTC_SDP_TYPE_ROLLBACK;
  } else {
    LOG_ERROR("Unknown SDP type: {}", sdp_type);
    gst_sdp_message_free(sdp_msg);
    return false;
  }

  // Create WebRTC session description
  GstWebRTCSessionDescription* remote_desc =
    gst_webrtc_session_description_new(type, sdp_msg);

  if (!remote_desc) {
    LOG_ERROR("Failed to create GstWebRTCSessionDescription");
    gst_sdp_message_free(sdp_msg);
    return false;
  }

  // Set remote description on webrtcbin
  GstPromise* promise = gst_promise_new();
  g_signal_emit_by_name(webrtcbin_, "set-remote-description", remote_desc, promise);

  // Wait for promise to complete
  GstPromiseResult promise_result = gst_promise_wait(promise);

  if (promise_result != GST_PROMISE_RESULT_REPLIED) {
    LOG_ERROR("set-remote-description promise failed with result: {}", promise_result);
    gst_promise_unref(promise);
    gst_webrtc_session_description_free(remote_desc);
    return false;
  }

  LOG_INFO("Remote description set successfully");
  gst_promise_unref(promise);
  gst_webrtc_session_description_free(remote_desc);
  return true;
}

bool Session::AddICECandidate(const std::string& candidate,
                              const std::string& sdp_mid,
                              int32_t sdp_mline_index) {
  LOG_DEBUG("Adding ICE candidate for session: {} (mid: {}, mline: {}, candidate: {})",
            config_.session_id, sdp_mid, sdp_mline_index, candidate);

  if (!initialized_) {
    LOG_ERROR("Cannot add ICE candidate: session not initialized");
    return false;
  }

  // Add ICE candidate to webrtcbin
  // Note: sdp_mline_index and sdp_mid are both provided, webrtcbin can use either
  g_signal_emit_by_name(webrtcbin_, "add-ice-candidate", sdp_mline_index, candidate.c_str());

  LOG_DEBUG("ICE candidate added successfully");
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

// ============================================================================
// Event Queue Management
// ============================================================================

void Session::PushEvent(const Event& event) {
  std::lock_guard<std::mutex> lock(event_mutex_);
  event_queue_.push(event);
  event_cv_.notify_one();

  const char* event_type = "";
  switch (event.type) {
    case Event::ICE_CANDIDATE:
      event_type = "ICE_CANDIDATE";
      break;
    case Event::CONNECTION_STATE_CHANGE:
      event_type = "CONNECTION_STATE_CHANGE";
      break;
    case Event::ICE_CONNECTION_STATE_CHANGE:
      event_type = "ICE_CONNECTION_STATE_CHANGE";
      break;
    case Event::ICE_GATHERING_STATE_CHANGE:
      event_type = "ICE_GATHERING_STATE_CHANGE";
      break;
  }

  LOG_DEBUG("Event queued: {} (queue size: {})", event_type, event_queue_.size());
}

bool Session::PopEvent(Event& event, int timeout_ms) {
  std::unique_lock<std::mutex> lock(event_mutex_);

  if (event_queue_.empty()) {
    // Wait for event with timeout
    auto timeout = std::chrono::milliseconds(timeout_ms);
    if (!event_cv_.wait_for(lock, timeout, [this] { return !event_queue_.empty(); })) {
      return false;  // Timeout
    }
  }

  if (!event_queue_.empty()) {
    event = event_queue_.front();
    event_queue_.pop();
    return true;
  }

  return false;
}

bool Session::HasPendingEvents() {
  std::lock_guard<std::mutex> lock(event_mutex_);
  return !event_queue_.empty();
}

// ============================================================================
// GStreamer Signal Callbacks
// ============================================================================

void Session::OnIceCandidate(GstElement* webrtcbin, guint mline_index,
                             gchar* candidate, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  LOG_DEBUG("ICE candidate generated: mline={}, candidate={}", mline_index, candidate);

  Event event;
  event.type = Event::ICE_CANDIDATE;
  event.sdp_mline_index = mline_index;
  event.data = candidate;

  // Extract mid from candidate if present (format: "candidate:... a=mid:...")
  // For now, we'll derive mid from mline_index (audio0, video1, etc.)
  if (mline_index == 0) {
    event.sdp_mid = "audio0";
  } else if (mline_index == 1) {
    event.sdp_mid = "video1";
  } else {
    event.sdp_mid = "media" + std::to_string(mline_index);
  }

  session->PushEvent(event);
}

void Session::OnConnectionStateChange(GstElement* webrtcbin, GParamSpec* pspec,
                                      gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  GstWebRTCPeerConnectionState state;
  g_object_get(webrtcbin, "connection-state", &state, NULL);

  const char* state_str = "";
  switch (state) {
    case GST_WEBRTC_PEER_CONNECTION_STATE_NEW:
      state_str = "new";
      break;
    case GST_WEBRTC_PEER_CONNECTION_STATE_CONNECTING:
      state_str = "connecting";
      break;
    case GST_WEBRTC_PEER_CONNECTION_STATE_CONNECTED:
      state_str = "connected";
      break;
    case GST_WEBRTC_PEER_CONNECTION_STATE_DISCONNECTED:
      state_str = "disconnected";
      break;
    case GST_WEBRTC_PEER_CONNECTION_STATE_FAILED:
      state_str = "failed";
      break;
    case GST_WEBRTC_PEER_CONNECTION_STATE_CLOSED:
      state_str = "closed";
      break;
  }

  LOG_INFO("Connection state changed: {}", state_str);

  Event event;
  event.type = Event::CONNECTION_STATE_CHANGE;
  event.data = state_str;
  session->PushEvent(event);
}

void Session::OnIceConnectionStateChange(GstElement* webrtcbin, GParamSpec* pspec,
                                        gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  GstWebRTCICEConnectionState state;
  g_object_get(webrtcbin, "ice-connection-state", &state, NULL);

  const char* state_str = "";
  switch (state) {
    case GST_WEBRTC_ICE_CONNECTION_STATE_NEW:
      state_str = "new";
      break;
    case GST_WEBRTC_ICE_CONNECTION_STATE_CHECKING:
      state_str = "checking";
      break;
    case GST_WEBRTC_ICE_CONNECTION_STATE_CONNECTED:
      state_str = "connected";
      break;
    case GST_WEBRTC_ICE_CONNECTION_STATE_COMPLETED:
      state_str = "completed";
      break;
    case GST_WEBRTC_ICE_CONNECTION_STATE_FAILED:
      state_str = "failed";
      break;
    case GST_WEBRTC_ICE_CONNECTION_STATE_DISCONNECTED:
      state_str = "disconnected";
      break;
    case GST_WEBRTC_ICE_CONNECTION_STATE_CLOSED:
      state_str = "closed";
      break;
  }

  LOG_INFO("ICE connection state changed: {}", state_str);

  Event event;
  event.type = Event::ICE_CONNECTION_STATE_CHANGE;
  event.data = state_str;
  session->PushEvent(event);
}

void Session::OnIceGatheringStateChange(GstElement* webrtcbin, GParamSpec* pspec,
                                       gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  GstWebRTCICEGatheringState state;
  g_object_get(webrtcbin, "ice-gathering-state", &state, NULL);

  const char* state_str = "";
  switch (state) {
    case GST_WEBRTC_ICE_GATHERING_STATE_NEW:
      state_str = "new";
      break;
    case GST_WEBRTC_ICE_GATHERING_STATE_GATHERING:
      state_str = "gathering";
      break;
    case GST_WEBRTC_ICE_GATHERING_STATE_COMPLETE:
      state_str = "complete";
      break;
  }

  LOG_INFO("ICE gathering state changed: {}", state_str);

  Event event;
  event.type = Event::ICE_GATHERING_STATE_CHANGE;
  event.data = state_str;
  session->PushEvent(event);
}

}  // namespace drunk_call
