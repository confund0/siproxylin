#include "session.h"
#include "logger.h"
#include <sstream>
#include <iomanip>
#include <cctype>
#include <chrono>
#include <algorithm>
#include <map>

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
    : config_(config),
      pipeline_(nullptr),
      rtpbin_(nullptr),
      send_rtp_appsink_(nullptr),
      send_rtcp_appsink_(nullptr),
      recv_rtp_appsrc_(nullptr),
      recv_rtcp_appsrc_(nullptr),
      audio_src_(nullptr),
      audio_sink_(nullptr) {
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

  // 1. Create pipeline
  pipeline_ = gst_pipeline_new(("pipeline-" + config_.session_id).c_str());
  if (!pipeline_) {
    LOG_ERROR("Failed to create GStreamer pipeline");
    return false;
  }

  // 2. Create and configure rtpbin (CHANGED from webrtcbin)
  if (!SetupRtpbin()) {
    LOG_ERROR("Failed to setup rtpbin");
    gst_object_unref(pipeline_);
    return false;
  }

  // 3. Create and configure appsink/appsrc elements (NEW)
  if (!SetupAppsinkAppsrc()) {
    LOG_ERROR("Failed to setup appsink/appsrc");
    gst_object_unref(pipeline_);
    return false;
  }

  // 4. Create and initialize IceAgent (NEW)
  ice_agent_ = std::make_unique<IceAgent>(2);  // 2 components: RTP + RTCP
  if (!ice_agent_->Initialize()) {
    LOG_ERROR("Failed to initialize IceAgent");
    gst_object_unref(pipeline_);
    return false;
  }

  // 5. Set ICE transport policy (CHANGED from webrtcbin)
  ice_agent_->SetTransportPolicy(config_.relay_only);

  // 6. Wire IceAgent callbacks to event queue (NEW)
  ice_agent_->SetOnCandidateCallback(
    [this](int comp_id, const std::string& cand, const std::string& mid, int mline_idx) {
      this->OnIceCandidate(comp_id, cand, mid, mline_idx);
    }
  );
  ice_agent_->SetOnComponentStateCallback(
    [this](int comp_id, const std::string& state) {
      this->OnComponentStateChanged(comp_id, state);
    }
  );
  ice_agent_->SetOnDataReceivedCallback(
    [this](int comp_id, const uint8_t* data, size_t len) {
      this->OnIceDataReceived(comp_id, data, len);
    }
  );
  ice_agent_->SetOnGatheringDoneCallback(
    [this]() {
      this->OnGatheringDone();
    }
  );

  // 7. Create ICE stream (NEW)
  if (!ice_agent_->AddStream()) {
    LOG_ERROR("Failed to add ICE stream");
    gst_object_unref(pipeline_);
    return false;
  }

  // 8. Configure STUN/TURN after stream is created (CHANGED from webrtcbin)
  if (!config_.turn_server.empty()) {
    // Parse TURN server: "turn:host:port?transport=udp"
    std::string host_port = config_.turn_server;
    if (host_port.find("turn:") == 0) {
      host_port = host_port.substr(5);
    }
    size_t query_pos = host_port.find('?');
    if (query_pos != std::string::npos) {
      host_port = host_port.substr(0, query_pos);
    }

    // Extract host and port
    size_t colon_pos = host_port.find(':');
    if (colon_pos != std::string::npos) {
      std::string host = host_port.substr(0, colon_pos);
      uint16_t port = std::stoi(host_port.substr(colon_pos + 1));

      // Set STUN (for relay discovery)
      ice_agent_->SetStunServer(host, port);

      // Set TURN (for both components)
      ice_agent_->SetTurnServer(host, port, config_.turn_username, config_.turn_password);
    }
  }

  // 9. Create and initialize DTLS-SRTP handler (Phase 3)
  dtls_srtp_ = std::make_unique<DtlsSrtpHandler>();
  if (!dtls_srtp_->GenerateCertificate()) {
    LOG_ERROR("Failed to generate DTLS certificate");
    gst_object_unref(pipeline_);
    return false;
  }

  LOG_INFO("DTLS certificate generated, fingerprint: {}", dtls_srtp_->GetFingerprintString());

  // Wire DTLS send callback to IceAgent Component 1
  dtls_srtp_->SetSendDataCallback([this](const uint8_t* data, size_t len) {
    // Send DTLS packets over Component 1
    if (!ice_agent_->Send(1, data, len)) {
      LOG_ERROR("Failed to send DTLS packet via ICE");
    }
  });

  // 10. Create audio pipeline (SAME as before, but link to rtpbin)
  if (!SetupAudioPipeline()) {
    LOG_ERROR("Failed to setup audio pipeline");
    gst_object_unref(pipeline_);
    return false;
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

  // Stop DTLS handshake if running (Phase 3)
  if (dtls_srtp_) {
    dtls_srtp_->StopHandshake();
    dtls_srtp_.reset();
    LOG_DEBUG("DTLS-SRTP handler destroyed");
  }

  // Shutdown IceAgent (NEW)
  if (ice_agent_) {
    ice_agent_->Shutdown();
    ice_agent_.reset();
    LOG_DEBUG("IceAgent shutdown complete");
  }

  // Stop main pipeline
  if (pipeline_) {
    gst_element_set_state(pipeline_, GST_STATE_NULL);
    gst_object_unref(pipeline_);
    pipeline_ = nullptr;
    rtpbin_ = nullptr;  // rtpbin is owned by pipeline
    send_rtp_appsink_ = nullptr;
    send_rtcp_appsink_ = nullptr;
    recv_rtp_appsrc_ = nullptr;
    recv_rtcp_appsrc_ = nullptr;
    audio_src_ = nullptr;
    audio_sink_ = nullptr;
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

  // 1. Set pipeline to PLAYING (SAME)
  GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
  if (ret == GST_STATE_CHANGE_FAILURE) {
    LOG_ERROR("Failed to set pipeline to PLAYING state");
    return "";
  }

  ret = gst_element_get_state(pipeline_, NULL, NULL, 5 * GST_SECOND);
  if (ret == GST_STATE_CHANGE_FAILURE) {
    LOG_ERROR("Pipeline failed to reach PLAYING state");
    return "";
  }

  LOG_INFO("Pipeline is now in PLAYING state");

  // 2. Start ICE candidate gathering (NEW)
  ice_agent_->GatherCandidates();
  // NOTE: Candidates will be streamed via OnIceCandidate callbacks → PopEvent()

  // 3. Get ICE credentials (NEW)
  auto [ufrag, pwd] = ice_agent_->GetLocalCredentials();
  LOG_INFO("Local ICE credentials: ufrag={}, pwd_len={}", ufrag, pwd.length());

  // 4. Construct SDP manually (STUB - Phase 4)
  // TODO: Implement full SDP generation in Phase 4
  // For now, return hardcoded SDP for testing
  std::string sdp =
    "v=0\r\n"
    "o=- 0 0 IN IP4 0.0.0.0\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 RTP/AVP 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:96 opus/48000/2\r\n"
    "a=mid:audio0\r\n"
    "a=sendrecv\r\n"
    "a=ice-ufrag:" + ufrag + "\r\n"
    "a=ice-pwd:" + pwd + "\r\n"
    "a=ice-options:trickle\r\n"
    "a=fingerprint:sha-256 " + dtls_srtp_->GetFingerprintString() + "\r\n"
    "a=setup:actpass\r\n";

  // Set mode to SERVER (we'll be passive, waiting for peer to connect)
  dtls_srtp_->SetMode(DtlsMode::SERVER);

  LOG_INFO("Generated stub SDP offer ({} bytes)", sdp.size());
  LOG_DEBUG("SDP Offer:\n{}", sdp);

  return sdp;
}

std::string Session::CreateAnswer(const std::string& remote_sdp,
                                  bool offer_has_bundle) {
  LOG_INFO("Creating answer for session: {} (offer_has_bundle={})",
            config_.session_id, offer_has_bundle);

  if (!initialized_) {
    LOG_ERROR("Cannot create answer: session not initialized");
    return "";
  }

  // 1. Set pipeline to PLAYING FIRST (SAME - critical!)
  GstState current_state;
  gst_element_get_state(pipeline_, &current_state, NULL, 0);

  if (current_state != GST_STATE_PLAYING) {
    GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
    if (ret == GST_STATE_CHANGE_FAILURE) {
      LOG_ERROR("Failed to set pipeline to PLAYING state");
      return "";
    }

    ret = gst_element_get_state(pipeline_, NULL, NULL, 5 * GST_SECOND);
    if (ret == GST_STATE_CHANGE_FAILURE) {
      LOG_ERROR("Pipeline failed to reach PLAYING state");
      return "";
    }

    LOG_INFO("Pipeline is now in PLAYING state");
  }

  // 2. Parse remote SDP to extract ICE credentials (NEW)
  // TODO: Implement SDP parser in Phase 4
  // For now, stub extraction
  LOG_INFO("TODO: Parse remote SDP to extract ICE credentials");
  LOG_DEBUG("Remote offer SDP:\n{}", remote_sdp);

  // 3. Set remote ICE credentials (NEW - stub for Phase 4)
  // ice_agent_->SetRemoteCredentials(remote_ufrag, remote_pwd);

  // 4. Start ICE candidate gathering (NEW)
  ice_agent_->GatherCandidates();

  // 5. Get local ICE credentials (NEW)
  auto [ufrag, pwd] = ice_agent_->GetLocalCredentials();

  // 6. Construct answer SDP (STUB - Phase 4)
  std::string sdp =
    "v=0\r\n"
    "o=- 0 0 IN IP4 0.0.0.0\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 RTP/AVP 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:96 opus/48000/2\r\n"
    "a=mid:audio0\r\n"
    "a=sendrecv\r\n"
    "a=ice-ufrag:" + ufrag + "\r\n"
    "a=ice-pwd:" + pwd + "\r\n"
    "a=ice-options:trickle\r\n"
    "a=fingerprint:sha-256 " + dtls_srtp_->GetFingerprintString() + "\r\n"
    "a=setup:active\r\n";  // active for answer

  // Set mode to CLIENT (we're active, we initiate DTLS)
  dtls_srtp_->SetMode(DtlsMode::CLIENT);

  LOG_INFO("Generated stub SDP answer ({} bytes)", sdp.size());
  LOG_DEBUG("SDP Answer:\n{}", sdp);

  return sdp;
}

bool Session::SetRemoteDescription(const std::string& remote_sdp, const std::string& sdp_type) {
  LOG_INFO("Setting remote description for session: {} (type: {})",
            config_.session_id, sdp_type);

  if (!initialized_) {
    LOG_ERROR("Cannot set remote description: session not initialized");
    return false;
  }

  LOG_DEBUG("Remote {} SDP:\n{}", sdp_type, remote_sdp);

  // Parse SDP to extract ICE credentials (STUB - Phase 4)
  // TODO: Implement SDP parser
  LOG_INFO("TODO: Parse remote SDP to extract ICE credentials and candidates");

  // For Phase 2, just log and return success
  // Phase 4 will implement:
  // 1. Parse ice-ufrag and ice-pwd from SDP
  // 2. Call ice_agent_->SetRemoteCredentials(ufrag, pwd)
  // 3. Extract candidates from SDP (if present)
  // 4. Call ice_agent_->AddRemoteCandidate() for each

  LOG_INFO("Remote description set successfully (stub)");
  return true;
}

bool Session::AddICECandidate(const std::string& candidate,
                              const std::string& sdp_mid,
                              int32_t sdp_mline_index) {
  LOG_DEBUG("Adding ICE candidate for session: {} (mid: {}, mline: {}, candidate: {})",
            config_.session_id, sdp_mid, sdp_mline_index, candidate);

  if (!initialized_ || !ice_agent_) {
    LOG_ERROR("Cannot add ICE candidate: session not initialized");
    return false;
  }

  // Determine component_id from sdp_mline_index
  // For now, simple mapping: mline 0 → Component 1, mline 1 → Component 2
  // Phase 4 may refine this based on RTCP-mux detection
  int component_id = sdp_mline_index + 1;

  // Add to IceAgent
  if (!ice_agent_->AddRemoteCandidate(component_id, candidate, sdp_mid, sdp_mline_index)) {
    LOG_ERROR("Failed to add remote candidate to IceAgent");
    return false;
  }

  LOG_DEBUG("ICE candidate added successfully to IceAgent");
  return true;
}

void Session::SetMute(bool muted) {
  muted_ = muted;
  LOG_INFO("Session {} mute state: {}", config_.session_id, muted);

  // TODO: Enable/disable audio track
}

Session::Stats Session::GetStats() {
  Stats stats;

  if (!ice_agent_) {
    LOG_WARN("GetStats called but IceAgent is null");
    stats.connection_state = "new";
    stats.ice_connection_state = "new";
    stats.ice_gathering_state = "new";
    return stats;
  }

  // Get ICE connection state from IceAgent
  bool comp1_ready = ice_agent_->IsComponentReady(1);

  std::string comp1_state = ice_agent_->GetComponentState(1);

  // Map IceAgent component states to WebRTC states
  if (comp1_ready) {
    stats.ice_connection_state = "completed";
    stats.connection_state = "connected";
  } else if (comp1_state == "CONNECTING") {
    stats.ice_connection_state = "checking";
    stats.connection_state = "connecting";
  } else if (comp1_state == "FAILED") {
    stats.ice_connection_state = "failed";
    stats.connection_state = "failed";
  } else {
    stats.ice_connection_state = "new";
    stats.connection_state = "new";
  }

  // Gathering state (stub - will track in IceAgent)
  stats.ice_gathering_state = "complete";  // STUB

  // Bytes and bandwidth (stub - Phase 4 will get from rtpbin stats)
  stats.bytes_sent = 0;
  stats.bytes_received = 0;
  stats.bandwidth_kbps = 0;

  // Candidates (stub - Phase 4 will get from IceAgent)
  stats.local_candidates = {"STUB: local candidates"};
  stats.remote_candidates = {"STUB: remote candidates"};

  // Connection type (determine from Component 1 state)
  if (comp1_ready) {
    stats.connection_type = "ICE connected";  // STUB
  } else {
    stats.connection_type = "Unknown";
  }

  LOG_DEBUG("GetStats: state={}, ice={}, type={}",
            stats.connection_state, stats.ice_connection_state, stats.connection_type);

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
// GStreamer Signal Callbacks (OLD webrtcbin callbacks removed)
// ============================================================================

void Session::OnPadAdded(GstElement* rtpbin, GstPad* pad, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  // Get pad name
  gchar* pad_name = gst_pad_get_name(pad);
  LOG_INFO("rtpbin pad added: {}", pad_name);

  // Handle send_rtcp_src_0 pad (dynamically created by rtpbin)
  if (g_str_has_prefix(pad_name, "send_rtcp_src_")) {
    LOG_INFO("Linking {} to RTCP appsink", pad_name);
    GstPad* rtcp_sink = gst_element_get_static_pad(session->send_rtcp_appsink_, "sink");
    if (gst_pad_link(pad, rtcp_sink) != GST_PAD_LINK_OK) {
      LOG_ERROR("Failed to link {} to RTCP appsink", pad_name);
    } else {
      LOG_INFO("RTCP appsink linked successfully");
    }
    gst_object_unref(rtcp_sink);
    g_free(pad_name);
    return;
  }

  // Handle recv_rtp_src pads (incoming RTP for playback)
  if (g_str_has_prefix(pad_name, "recv_rtp_src_")) {
    GstCaps* caps = gst_pad_get_current_caps(pad);
    if (!caps) {
      caps = gst_pad_query_caps(pad, NULL);
    }

    gchar* caps_str = gst_caps_to_string(caps);
    LOG_INFO("Incoming RTP stream: {} with caps: {}", pad_name, caps_str);
    g_free(caps_str);

    // Check if this is an audio pad
    GstStructure* structure = gst_caps_get_structure(caps, 0);
    const gchar* media = gst_structure_get_string(structure, "media");

    if (media && g_strcmp0(media, "audio") == 0) {
      LOG_INFO("Setting up audio playback for incoming audio stream");
      session->SetupAudioPlayback(pad);
    } else if (media && g_strcmp0(media, "video") == 0) {
      LOG_INFO("Video pad detected (playback not yet implemented)");
    } else {
      LOG_DEBUG("Non-media pad (media={})", media ? media : "null");
    }

    gst_caps_unref(caps);
  }

  g_free(pad_name);
}

void Session::SetupAudioPlayback(GstPad* pad) {
  LOG_INFO("Creating audio playback pipeline for session: {}", config_.session_id);

  // Determine audio sink based on speakers_device config
  std::string audio_sink;
  if (!config_.speakers_device.empty()) {
    // Use specific PulseAudio device
    audio_sink = "pulsesink device=" + config_.speakers_device;
    LOG_INFO("Using selected speakers device: {}", config_.speakers_device);
  } else {
    // Use system default
    audio_sink = "autoaudiosink";
    LOG_INFO("Using default speakers device (autoaudiosink)");
  }

  // Create individual elements (not using parse_bin_from_description because of ghost pad issues)
  GstElement* depay = gst_element_factory_make("rtpopusdepay", nullptr);
  GstElement* decoder = gst_element_factory_make("opusdec", nullptr);
  GstElement* convert = gst_element_factory_make("audioconvert", nullptr);
  GstElement* resample = gst_element_factory_make("audioresample", nullptr);

  // Create sink element based on config
  GstElement* sink = nullptr;
  if (!config_.speakers_device.empty()) {
    sink = gst_element_factory_make("pulsesink", nullptr);
    if (sink) {
      g_object_set(sink, "device", config_.speakers_device.c_str(), NULL);
    }
  } else {
    sink = gst_element_factory_make("autoaudiosink", nullptr);
  }

  if (!depay || !decoder || !convert || !resample || !sink) {
    LOG_ERROR("Failed to create playback pipeline elements");
    if (depay) gst_object_unref(depay);
    if (decoder) gst_object_unref(decoder);
    if (convert) gst_object_unref(convert);
    if (resample) gst_object_unref(resample);
    if (sink) gst_object_unref(sink);
    return;
  }

  // Add all elements to main pipeline
  gst_bin_add_many(GST_BIN(pipeline_), depay, decoder, convert, resample, sink, NULL);

  // Link elements: depay -> decoder -> convert -> resample -> sink
  if (!gst_element_link_many(depay, decoder, convert, resample, sink, NULL)) {
    LOG_ERROR("Failed to link playback elements");
    return;
  }

  LOG_INFO("Created and linked playback elements: depay -> decoder -> convert -> resample -> sink");

  // Get depay's sink pad
  GstPad* sink_pad = gst_element_get_static_pad(depay, "sink");
  if (!sink_pad) {
    LOG_ERROR("Failed to get sink pad from depay element");
    return;
  }

  // Link webrtcbin's src pad to depay's sink pad
  GstPadLinkReturn link_result = gst_pad_link(pad, sink_pad);
  if (link_result != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link webrtcbin pad to depay sink: {} ({})",
              link_result,
              link_result == GST_PAD_LINK_WRONG_DIRECTION ? "wrong direction" :
              link_result == GST_PAD_LINK_NOFORMAT ? "no format" :
              link_result == GST_PAD_LINK_NOSCHED ? "no sched" :
              link_result == GST_PAD_LINK_REFUSED ? "refused" : "unknown");
    gst_object_unref(sink_pad);
    return;
  }

  LOG_INFO("Linked webrtcbin audio pad to depay sink pad");

  // Sync all playback elements to PLAYING state
  gst_element_sync_state_with_parent(depay);
  gst_element_sync_state_with_parent(decoder);
  gst_element_sync_state_with_parent(convert);
  gst_element_sync_state_with_parent(resample);
  gst_element_sync_state_with_parent(sink);

  LOG_INFO("Audio playback pipeline created and started successfully");

  // Note: playback elements are owned by main pipeline, no need to track separately
  gst_object_unref(sink_pad);
}

// ============================================================================
// GStreamer Signal Callbacks (rtpbin migration)
// ============================================================================

// Static callback for appsink new-sample signal
GstFlowReturn Session::OnAppsinkNewSample(GstAppSink* appsink, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  // Pull sample from appsink
  GstSample* sample = gst_app_sink_pull_sample(appsink);
  if (!sample) {
    LOG_ERROR("Failed to pull sample from appsink");
    return GST_FLOW_ERROR;
  }

  // Get buffer from sample
  GstBuffer* buffer = gst_sample_get_buffer(sample);
  if (!buffer) {
    LOG_ERROR("Failed to get buffer from sample");
    gst_sample_unref(sample);
    return GST_FLOW_ERROR;
  }

  // Map buffer to read data
  GstMapInfo map;
  if (!gst_buffer_map(buffer, &map, GST_MAP_READ)) {
    LOG_ERROR("Failed to map buffer");
    gst_sample_unref(sample);
    return GST_FLOW_ERROR;
  }

  // Determine if this is RTP or RTCP based on appsink name
  GstElement* element = GST_ELEMENT(appsink);
  const gchar* name = gst_element_get_name(element);

  if (g_str_has_prefix(name, "send_rtp_appsink")) {
    // This is outgoing RTP data from rtpbin → send via ICE Component 1
    session->SendRtpData(map.data, map.size);
  } else if (g_str_has_prefix(name, "send_rtcp_appsink")) {
    // This is outgoing RTCP data from rtpbin → send via ICE Component 1 or 2
    session->SendRtcpData(map.data, map.size);
  } else {
    LOG_WARN("Unknown appsink: {}", name);
  }

  // Cleanup
  gst_buffer_unmap(buffer, &map);
  gst_sample_unref(sample);

  return GST_FLOW_OK;
}

// ============================================================================
// Helper Methods (NEW for rtpbin migration)
// ============================================================================

bool Session::SetupRtpbin() {
  LOG_INFO("Setting up rtpbin for session: {}", config_.session_id);

  // Create rtpbin element
  rtpbin_ = gst_element_factory_make("rtpbin", ("rtpbin-" + config_.session_id).c_str());
  if (!rtpbin_) {
    LOG_ERROR("Failed to create rtpbin element");
    return false;
  }

  // Configure rtpbin properties (same as Dino)
  g_object_set(rtpbin_,
               "latency", 100,
               "do-lost", TRUE,
               "drop-on-latency", TRUE,
               NULL);

  // Add to pipeline
  gst_bin_add(GST_BIN(pipeline_), rtpbin_);

  // Connect pad-added signal for incoming streams
  g_signal_connect(rtpbin_, "pad-added",
                   G_CALLBACK(OnPadAdded), this);

  LOG_INFO("rtpbin created and configured successfully");
  return true;
}

bool Session::SetupAppsinkAppsrc() {
  LOG_INFO("Setting up appsink/appsrc elements");

  // Create appsink for outgoing RTP
  send_rtp_appsink_ = gst_element_factory_make("appsink", "send_rtp_appsink");
  if (!send_rtp_appsink_) {
    LOG_ERROR("Failed to create send_rtp_appsink");
    return false;
  }

  // Configure appsink
  g_object_set(send_rtp_appsink_, "emit-signals", TRUE, "sync", FALSE, NULL);
  g_signal_connect(send_rtp_appsink_, "new-sample",
                   G_CALLBACK(OnAppsinkNewSample), this);

  // Create appsink for outgoing RTCP
  send_rtcp_appsink_ = gst_element_factory_make("appsink", "send_rtcp_appsink");
  if (!send_rtcp_appsink_) {
    LOG_ERROR("Failed to create send_rtcp_appsink");
    return false;
  }

  g_object_set(send_rtcp_appsink_, "emit-signals", TRUE, "sync", FALSE, NULL);
  g_signal_connect(send_rtcp_appsink_, "new-sample",
                   G_CALLBACK(OnAppsinkNewSample), this);

  // Create appsrc for incoming RTP
  recv_rtp_appsrc_ = gst_element_factory_make("appsrc", "recv_rtp_appsrc");
  if (!recv_rtp_appsrc_) {
    LOG_ERROR("Failed to create recv_rtp_appsrc");
    return false;
  }

  // Configure appsrc
  g_object_set(recv_rtp_appsrc_, "format", GST_FORMAT_TIME, NULL);

  // Create appsrc for incoming RTCP
  recv_rtcp_appsrc_ = gst_element_factory_make("appsrc", "recv_rtcp_appsrc");
  if (!recv_rtcp_appsrc_) {
    LOG_ERROR("Failed to create recv_rtcp_appsrc");
    return false;
  }

  g_object_set(recv_rtcp_appsrc_, "format", GST_FORMAT_TIME, NULL);

  // Add all to pipeline
  gst_bin_add_many(GST_BIN(pipeline_),
                   send_rtp_appsink_, send_rtcp_appsink_,
                   recv_rtp_appsrc_, recv_rtcp_appsrc_,
                   NULL);

  // NOTE: We do NOT link appsinks yet!
  // rtpbin's send_rtp_src_0 and send_rtcp_src_0 pads are created AFTER
  // we link the audio pipeline to send_rtp_sink_0.
  // We'll link them dynamically when rtpbin creates the pads.

  // Link appsrc → rtpbin for incoming (these pads exist immediately)
  GstPad* rtp_appsrc_pad = gst_element_get_static_pad(recv_rtp_appsrc_, "src");
  GstPad* rtp_recv_pad = gst_element_request_pad_simple(rtpbin_, "recv_rtp_sink_0");
  if (!rtp_recv_pad) {
    LOG_ERROR("Failed to request recv_rtp_sink_0 pad from rtpbin");
    gst_object_unref(rtp_appsrc_pad);
    return false;
  }
  if (gst_pad_link(rtp_appsrc_pad, rtp_recv_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link appsrc to rtpbin recv_rtp_sink_0");
    gst_object_unref(rtp_appsrc_pad);
    gst_object_unref(rtp_recv_pad);
    return false;
  }
  gst_object_unref(rtp_appsrc_pad);
  gst_object_unref(rtp_recv_pad);

  GstPad* rtcp_appsrc_pad = gst_element_get_static_pad(recv_rtcp_appsrc_, "src");
  GstPad* rtcp_recv_pad = gst_element_request_pad_simple(rtpbin_, "recv_rtcp_sink_0");
  if (!rtcp_recv_pad) {
    LOG_ERROR("Failed to request recv_rtcp_sink_0 pad from rtpbin");
    gst_object_unref(rtcp_appsrc_pad);
    return false;
  }
  if (gst_pad_link(rtcp_appsrc_pad, rtcp_recv_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link appsrc to rtpbin recv_rtcp_sink_0");
    gst_object_unref(rtcp_appsrc_pad);
    gst_object_unref(rtcp_recv_pad);
    return false;
  }
  gst_object_unref(rtcp_appsrc_pad);
  gst_object_unref(rtcp_recv_pad);

  LOG_INFO("appsink/appsrc elements created and linked (appsinks will be linked after audio pipeline)");
  return true;
}

bool Session::SetupAudioPipeline() {
  LOG_INFO("Setting up audio pipeline");

  // Create audio source (SAME logic as before)
  if (!config_.microphone_device.empty()) {
    audio_src_ = gst_element_factory_make("pulsesrc", "audiosrc");
    g_object_set(audio_src_, "device", config_.microphone_device.c_str(), NULL);
    LOG_INFO("Using selected microphone: {}", config_.microphone_device);
  } else {
    audio_src_ = gst_element_factory_make("autoaudiosrc", "audiosrc");
    LOG_INFO("Using default microphone (autoaudiosrc)");
  }

  if (!audio_src_) {
    LOG_ERROR("Failed to create audio source");
    return false;
  }

  // Create rest of pipeline (SAME)
  GstElement* audioconvert = gst_element_factory_make("audioconvert", "audioconv");
  GstElement* audioresample = gst_element_factory_make("audioresample", "audioresample");
  GstElement* opusenc = gst_element_factory_make("opusenc", "opusenc");
  GstElement* rtpopuspay = gst_element_factory_make("rtpopuspay", "rtpopuspay");

  if (!audioconvert || !audioresample || !opusenc || !rtpopuspay) {
    LOG_ERROR("Failed to create audio pipeline elements");
    return false;
  }

  // Add to pipeline
  gst_bin_add_many(GST_BIN(pipeline_), audio_src_, audioconvert,
                   audioresample, opusenc, rtpopuspay, NULL);

  // Link: audiosrc → audioconvert → audioresample → opusenc → rtpopuspay
  if (!gst_element_link_many(audio_src_, audioconvert, audioresample,
                              opusenc, rtpopuspay, NULL)) {
    LOG_ERROR("Failed to link audio elements");
    return false;
  }

  // Link rtpopuspay → rtpbin send_rtp_sink_0 (CHANGED from webrtcbin)
  GstPad* audio_src_pad = gst_element_get_static_pad(rtpopuspay, "src");
  GstPad* audio_sink_pad = gst_element_request_pad_simple(rtpbin_, "send_rtp_sink_0");

  if (gst_pad_link(audio_src_pad, audio_sink_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link audio to rtpbin");
    gst_object_unref(audio_src_pad);
    gst_object_unref(audio_sink_pad);
    return false;
  }

  gst_object_unref(audio_src_pad);
  gst_object_unref(audio_sink_pad);
  LOG_INFO("Audio pipeline linked to rtpbin successfully");

  // NOW link the RTP appsink (send_rtp_src_0 exists after linking send_rtp_sink_0)
  GstPad* rtp_src = gst_element_get_static_pad(rtpbin_, "send_rtp_src_0");
  if (!rtp_src) {
    LOG_ERROR("Failed to get send_rtp_src_0 pad from rtpbin");
    return false;
  }
  GstPad* rtp_sink = gst_element_get_static_pad(send_rtp_appsink_, "sink");
  if (gst_pad_link(rtp_src, rtp_sink) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link rtpbin send_rtp_src_0 to appsink");
    gst_object_unref(rtp_src);
    gst_object_unref(rtp_sink);
    return false;
  }
  gst_object_unref(rtp_src);
  gst_object_unref(rtp_sink);
  LOG_INFO("RTP appsink linked to rtpbin send_rtp_src_0");

  // NOTE: send_rtcp_src_0 is created dynamically by rtpbin when RTCP is generated
  // We handle it in the OnPadAdded callback (already connected in SetupRtpbin)
  LOG_INFO("RTCP appsink will be linked dynamically when rtpbin creates send_rtcp_src_0");

  return true;
}

// ============================================================================
// IceAgent Callbacks (NEW)
// ============================================================================

void Session::OnIceCandidate(int component_id, const std::string& candidate,
                              const std::string& sdp_mid, int sdp_mline_index) {
  LOG_DEBUG("ICE candidate: component={}, mid={}, mline={}, cand={}",
            component_id, sdp_mid, sdp_mline_index, candidate);

  Event event;
  event.type = Event::ICE_CANDIDATE;
  event.sdp_mid = sdp_mid;
  event.sdp_mline_index = sdp_mline_index;
  event.data = candidate;

  PushEvent(event);
}

void Session::OnComponentStateChanged(int component_id, const std::string& state) {
  LOG_INFO("Component {} state changed: {}", component_id, state);

  // Map to WebRTC ICE connection state
  Event event;
  event.type = Event::ICE_CONNECTION_STATE_CHANGE;

  if (state == "READY" || state == "CONNECTED") {
    event.data = "connected";
  } else if (state == "CONNECTING") {
    event.data = "checking";
  } else if (state == "FAILED") {
    event.data = "failed";
  } else if (state == "DISCONNECTED") {
    event.data = "disconnected";
  } else {
    event.data = "new";
  }

  PushEvent(event);

  // Also push CONNECTION_STATE_CHANGE
  Event conn_event;
  conn_event.type = Event::CONNECTION_STATE_CHANGE;
  conn_event.data = event.data;
  PushEvent(conn_event);

  // Start DTLS handshake when Component 1 is ready (Phase 3)
  if (component_id == 1 && (state == "READY" || state == "CONNECTED") &&
      dtls_srtp_ && !dtls_srtp_->IsReady()) {
    LOG_INFO("Component 1 ready, starting DTLS handshake");
    dtls_srtp_->StartHandshake();
  }
}

void Session::OnIceDataReceived(int component_id, const uint8_t* data, size_t len) {
  LOG_DEBUG("Received {} bytes on component {}", len, component_id);

  if (!dtls_srtp_) {
    LOG_ERROR("DTLS-SRTP handler not initialized");
    return;
  }

  // Process incoming data (handles DTLS and SRTP decryption)
  auto decrypted = dtls_srtp_->ProcessIncomingData(component_id, data, len);

  if (decrypted.empty()) {
    // Either DTLS packet (handled internally) or error
    return;
  }

  // Push decrypted RTP/RTCP to rtpbin
  if (component_id == 1) {
    // Component 1: Could be RTP or RTCP (check packet type)
    if (len >= 2 && data[1] >= 192 && data[1] < 224) {
      PushRtcpData(decrypted.data(), decrypted.size());
    } else {
      PushRtpData(decrypted.data(), decrypted.size());
    }
  } else if (component_id == 2) {
    // Component 2: Always RTCP
    PushRtcpData(decrypted.data(), decrypted.size());
  }
}

void Session::OnGatheringDone() {
  LOG_INFO("ICE candidate gathering done");

  Event event;
  event.type = Event::ICE_GATHERING_STATE_CHANGE;
  event.data = "complete";

  PushEvent(event);
}

// ============================================================================
// Data Flow Helpers (NEW - STUBS for Phase 3)
// ============================================================================

void Session::SendRtpData(const uint8_t* data, size_t len) {
  LOG_DEBUG("Sending {} bytes of RTP", len);

  if (!dtls_srtp_ || !dtls_srtp_->IsReady()) {
    LOG_DEBUG("DTLS-SRTP not ready, cannot send RTP");
    return;
  }

  // Encrypt RTP via DTLS-SRTP
  auto encrypted = dtls_srtp_->ProcessOutgoingData(1, data, len);
  if (encrypted.empty()) {
    LOG_ERROR("Failed to encrypt RTP");
    return;
  }

  // Send encrypted RTP via Component 1
  if (!ice_agent_->Send(1, encrypted.data(), encrypted.size())) {
    LOG_ERROR("Failed to send encrypted RTP via ICE");
  }
}

void Session::SendRtcpData(const uint8_t* data, size_t len) {
  int component_id = rtcp_mux_ ? 1 : 2;
  LOG_DEBUG("Sending {} bytes of RTCP via Component {}", len, component_id);

  if (!dtls_srtp_ || !dtls_srtp_->IsReady()) {
    LOG_DEBUG("DTLS-SRTP not ready, cannot send RTCP");
    return;
  }

  // Encrypt RTCP via DTLS-SRTP
  auto encrypted = dtls_srtp_->ProcessOutgoingData(component_id, data, len);
  if (encrypted.empty()) {
    LOG_ERROR("Failed to encrypt RTCP");
    return;
  }

  // Send encrypted RTCP
  if (!ice_agent_->Send(component_id, encrypted.data(), encrypted.size())) {
    LOG_ERROR("Failed to send encrypted RTCP via ICE");
  }
}

void Session::PushRtpData(const uint8_t* data, size_t len) {
  LOG_DEBUG("Pushing {} bytes of RTP to rtpbin", len);

  // Create GstBuffer
  GstBuffer* buffer = gst_buffer_new_allocate(NULL, len, NULL);
  GstMapInfo map;
  gst_buffer_map(buffer, &map, GST_MAP_WRITE);
  memcpy(map.data, data, len);
  gst_buffer_unmap(buffer, &map);

  // Push to appsrc
  GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(recv_rtp_appsrc_), buffer);
  if (ret != GST_FLOW_OK) {
    LOG_ERROR("Failed to push RTP buffer to appsrc: {}", ret);
  }
}

void Session::PushRtcpData(const uint8_t* data, size_t len) {
  LOG_DEBUG("Pushing {} bytes of RTCP to rtpbin", len);

  // Create GstBuffer
  GstBuffer* buffer = gst_buffer_new_allocate(NULL, len, NULL);
  GstMapInfo map;
  gst_buffer_map(buffer, &map, GST_MAP_WRITE);
  memcpy(map.data, data, len);
  gst_buffer_unmap(buffer, &map);

  // Push to appsrc
  GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(recv_rtcp_appsrc_), buffer);
  if (ret != GST_FLOW_OK) {
    LOG_ERROR("Failed to push RTCP buffer to appsrc: {}", ret);
  }
}

}  // namespace drunk_call
