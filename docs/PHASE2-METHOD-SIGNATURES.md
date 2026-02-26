# Phase 2: Method Signatures & Integration Plan

**Goal:** Replace webrtcbin with rtpbin + IceAgent while preserving 100% gRPC compatibility

---

## Session Class Structure

### Private Members (session.h)

```cpp
class Session {
 private:
  // Configuration
  Config config_;
  bool initialized_ = false;
  bool muted_ = false;
  bool rtcp_mux_ = true;  // RTCP-mux mode (Component 1 for RTCP)

  // GStreamer components
  GstElement* pipeline_;
  GstElement* rtpbin_;  // CHANGED: was webrtcbin_

  // Appsink elements (capture outgoing RTP/RTCP from rtpbin)
  GstElement* send_rtp_appsink_;   // NEW
  GstElement* send_rtcp_appsink_;  // NEW

  // Appsrc elements (inject incoming RTP/RTCP to rtpbin)
  GstElement* recv_rtp_appsrc_;   // NEW
  GstElement* recv_rtcp_appsrc_;  // NEW

  // Audio elements (store references for cleanup)
  GstElement* audio_src_;   // NEW: microphone source
  GstElement* audio_sink_;  // NEW: speaker sink (from SetupAudioPlayback)

  // ICE agent (replaces webrtcbin's ICE)
  std::unique_ptr<IceAgent> ice_agent_;  // NEW

  // Event queue (unchanged)
  std::queue<Event> event_queue_;
  std::mutex event_mutex_;
  std::condition_variable event_cv_;

  // Stats tracking (unchanged)
  int64_t last_bytes_sent_ = 0;
  int64_t last_bytes_received_ = 0;
  std::chrono::steady_clock::time_point last_stats_time_;
};
```

---

## Constructor & Destructor

### `Session::Session(const Config& config)`

```cpp
Session::Session(const Config& config)
    : config_(config),
      pipeline_(nullptr),
      rtpbin_(nullptr),  // CHANGED from webrtcbin_
      send_rtp_appsink_(nullptr),   // NEW
      send_rtcp_appsink_(nullptr),  // NEW
      recv_rtp_appsrc_(nullptr),    // NEW
      recv_rtcp_appsrc_(nullptr),   // NEW
      audio_src_(nullptr),          // NEW
      audio_sink_(nullptr)          // NEW
{
  LOG_DEBUG("Session created: {}", config_.session_id);
  gst_init(nullptr, nullptr);  // Idempotent
}
```

**Changes:** Initialize new member pointers to nullptr

---

### `Session::~Session()`

```cpp
Session::~Session() {
  if (initialized_) {
    Close();
  }
  LOG_DEBUG("Session destroyed: {}", config_.session_id);
  // ice_agent_ automatically deleted (unique_ptr)
}
```

**Changes:** None (unique_ptr handles IceAgent cleanup)

---

## Lifecycle Methods

### `bool Session::Initialize()`

**gRPC contract:** Same signature, same return type
**Called by:** Python once per session

**New implementation:**
```cpp
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

  // 5. Configure STUN/TURN on IceAgent (CHANGED from webrtcbin)
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

  // 6. Set ICE transport policy (CHANGED from webrtcbin)
  ice_agent_->SetTransportPolicy(config_.relay_only);

  // 7. Wire IceAgent callbacks to event queue (NEW)
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

  // 8. Create ICE stream (NEW)
  if (!ice_agent_->AddStream()) {
    LOG_ERROR("Failed to add ICE stream");
    gst_object_unref(pipeline_);
    return false;
  }

  // 9. Create audio pipeline (SAME as before, but link to rtpbin)
  if (!SetupAudioPipeline()) {
    LOG_ERROR("Failed to setup audio pipeline");
    gst_object_unref(pipeline_);
    return false;
  }

  initialized_ = true;
  LOG_INFO("Session initialized: {}", config_.session_id);
  return true;
}
```

**Changes:**
- Replace webrtcbin creation with SetupRtpbin()
- Add SetupAppsinkAppsrc()
- Create IceAgent and wire callbacks
- Move STUN/TURN config to IceAgent
- Remove webrtcbin signal handlers
- Add SetupAudioPipeline() helper

---

### `void Session::Close()`

**gRPC contract:** Same signature
**Called by:** Python when ending session

**New implementation:**
```cpp
void Session::Close() {
  if (!initialized_) {
    return;
  }

  LOG_INFO("Closing session: {}", config_.session_id);

  // 1. Shutdown IceAgent first (NEW)
  if (ice_agent_) {
    ice_agent_->Shutdown();
    ice_agent_.reset();
  }

  // 2. Stop pipeline
  if (pipeline_) {
    gst_element_set_state(pipeline_, GST_STATE_NULL);
    gst_object_unref(pipeline_);
    pipeline_ = nullptr;
    // All elements owned by pipeline (rtpbin, appsink, appsrc, etc.)
    rtpbin_ = nullptr;
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
```

**Changes:**
- Add IceAgent shutdown before pipeline
- Clear new element pointers

---

## SDP Methods (STUBS for Phase 2)

### `std::string Session::CreateOffer()`

**gRPC contract:** Same signature, returns SDP string
**Called by:** Python for outgoing calls

**Phase 2 stub implementation:**
```cpp
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
    "a=fingerprint:sha-256 TODO-FINGERPRINT\r\n"
    "a=setup:actpass\r\n";

  LOG_INFO("Generated stub SDP offer ({} bytes)", sdp.size());
  LOG_DEBUG("SDP Offer:\n{}", sdp);

  return sdp;
}
```

**Changes:**
- Remove webrtcbin create-offer logic
- Add IceAgent::GatherCandidates()
- Return stub SDP (Phase 4 will implement full generation)

---

### `std::string Session::CreateAnswer(const std::string& remote_sdp, bool offer_has_bundle)`

**gRPC contract:** Same signature
**Called by:** Python for incoming calls

**Phase 2 stub implementation:**
```cpp
std::string Session::CreateAnswer(const std::string& remote_sdp, bool offer_has_bundle) {
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
    "a=fingerprint:sha-256 TODO-FINGERPRINT\r\n"
    "a=setup:active\r\n";  // active for answer

  LOG_INFO("Generated stub SDP answer ({} bytes)", sdp.size());
  LOG_DEBUG("SDP Answer:\n{}", sdp);

  return sdp;
}
```

**Changes:**
- Remove webrtcbin set-remote-description and create-answer logic
- Add IceAgent::SetRemoteCredentials() (stub)
- Return stub SDP

---

### `bool Session::SetRemoteDescription(const std::string& remote_sdp, const std::string& sdp_type)`

**gRPC contract:** Same signature
**Called by:** Python after CreateOffer to set peer's answer

**Phase 2 implementation:**
```cpp
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
```

**Changes:**
- Remove webrtcbin set-remote-description logic
- Add stub for SDP parsing
- Will implement in Phase 4

---

## ICE Methods

### `bool Session::AddICECandidate(const std::string& candidate, const std::string& sdp_mid, int32_t sdp_mline_index)`

**gRPC contract:** Same signature
**Called by:** Python repeatedly as remote candidates arrive

**Phase 2 implementation:**
```cpp
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
```

**Changes:**
- Replace webrtcbin add-ice-candidate with IceAgent::AddRemoteCandidate()
- Map sdp_mline_index to component_id

---

## Stats & Control

### `Session::Stats Session::GetStats()`

**gRPC contract:** Same signature and return type
**Called by:** Python polls repeatedly

**Phase 2 implementation:**
```cpp
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
  bool comp2_ready = ice_agent_->IsComponentReady(2);

  std::string comp1_state = ice_agent_->GetComponentState(1);
  std::string comp2_state = ice_agent_->GetComponentState(2);

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
```

**Changes:**
- Get states from IceAgent instead of webrtcbin
- Stub bytes/bandwidth/candidates (Phase 4)

---

### `void Session::SetMute(bool muted)`

**gRPC contract:** Same signature
**Called by:** Python for mute/unmute

**Implementation:** UNCHANGED
```cpp
void Session::SetMute(bool muted) {
  muted_ = muted;
  LOG_INFO("Session {} mute state: {}", config_.session_id, muted);
  // TODO: Enable/disable audio track
}
```

---

## Event Queue (UNCHANGED)

### `bool Session::PopEvent(Event& event, int timeout_ms)`

**gRPC contract:** UNCHANGED
**Implementation:** UNCHANGED

```cpp
bool Session::PopEvent(Event& event, int timeout_ms) {
  std::unique_lock<std::mutex> lock(event_mutex_);

  if (event_queue_.empty()) {
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
```

### `void Session::PushEvent(const Event& event)`

**Implementation:** UNCHANGED

---

## Helper Methods (NEW)

### `bool Session::SetupRtpbin()`

**Private helper for Initialize()**

```cpp
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
```

---

### `bool Session::SetupAppsinkAppsrc()`

**Private helper for Initialize()**

```cpp
bool Session::SetupAppsinkAppsrc() {
  LOG_INFO("Setting up appsink/appsrc elements");

  // Create appsink for outgoing RTP
  send_rtp_appsink_ = gst_element_factory_make("appsink", "send_rtp_appsink");
  if (!send_rtp_appsink_) {
    LOG_ERROR("Failed to create send_rtp_appsink");
    return false;
  }

  // Configure appsink (no emit-signals, we'll pull samples)
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

  // Link rtpbin → appsink for outgoing
  GstPad* rtp_src = gst_element_get_request_pad(rtpbin_, "send_rtp_src_0");
  GstPad* rtp_sink = gst_element_get_static_pad(send_rtp_appsink_, "sink");
  if (gst_pad_link(rtp_src, rtp_sink) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link rtpbin send_rtp_src_0 to appsink");
    return false;
  }
  gst_object_unref(rtp_src);
  gst_object_unref(rtp_sink);

  GstPad* rtcp_src = gst_element_get_request_pad(rtpbin_, "send_rtcp_src_0");
  GstPad* rtcp_sink = gst_element_get_static_pad(send_rtcp_appsink_, "sink");
  if (gst_pad_link(rtcp_src, rtcp_sink) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link rtpbin send_rtcp_src_0 to appsink");
    return false;
  }
  gst_object_unref(rtcp_src);
  gst_object_unref(rtcp_sink);

  // Link appsrc → rtpbin for incoming
  GstPad* rtp_appsrc_pad = gst_element_get_static_pad(recv_rtp_appsrc_, "src");
  GstPad* rtp_recv_pad = gst_element_get_request_pad(rtpbin_, "recv_rtp_sink_0");
  if (gst_pad_link(rtp_appsrc_pad, rtp_recv_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link appsrc to rtpbin recv_rtp_sink_0");
    return false;
  }
  gst_object_unref(rtp_appsrc_pad);
  gst_object_unref(rtp_recv_pad);

  GstPad* rtcp_appsrc_pad = gst_element_get_static_pad(recv_rtcp_appsrc_, "src");
  GstPad* rtcp_recv_pad = gst_element_get_request_pad(rtpbin_, "recv_rtcp_sink_0");
  if (gst_pad_link(rtcp_appsrc_pad, rtcp_recv_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link appsrc to rtpbin recv_rtcp_sink_0");
    return false;
  }
  gst_object_unref(rtcp_appsrc_pad);
  gst_object_unref(rtcp_recv_pad);

  LOG_INFO("appsink/appsrc elements created and linked successfully");
  return true;
}
```

---

### `bool Session::SetupAudioPipeline()`

**Private helper for Initialize()**

```cpp
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
  GstPad* audio_sink_pad = gst_element_get_request_pad(rtpbin_, "send_rtp_sink_0");

  if (gst_pad_link(audio_src_pad, audio_sink_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link audio to rtpbin");
    gst_object_unref(audio_src_pad);
    gst_object_unref(audio_sink_pad);
    return false;
  }

  gst_object_unref(audio_src_pad);
  gst_object_unref(audio_sink_pad);
  LOG_INFO("Audio pipeline linked to rtpbin successfully");

  return true;
}
```

---

## GStreamer Callbacks

### `static GstFlowReturn Session::OnAppsinkNewSample(GstAppSink* appsink, gpointer user_data)`

**Called by:** GStreamer when rtpbin produces RTP/RTCP packets

```cpp
GstFlowReturn Session::OnAppsinkNewSample(GstAppSink* appsink, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  // Pull sample from appsink
  GstSample* sample = gst_app_sink_pull_sample(appsink);
  if (!sample) {
    LOG_ERROR("Failed to pull sample from appsink");
    return GST_FLOW_ERROR;
  }

  // Get buffer
  GstBuffer* buffer = gst_sample_get_buffer(sample);
  if (!buffer) {
    LOG_ERROR("Failed to get buffer from sample");
    gst_sample_unref(sample);
    return GST_FLOW_ERROR;
  }

  // Extract data
  GstMapInfo map;
  if (!gst_buffer_map(buffer, &map, GST_MAP_READ)) {
    LOG_ERROR("Failed to map buffer");
    gst_sample_unref(sample);
    return GST_FLOW_ERROR;
  }

  // Determine if RTP or RTCP based on which appsink this is
  if (appsink == GST_APP_SINK(session->send_rtp_appsink_)) {
    // RTP packet - send via Component 1
    session->SendRtpData(map.data, map.size);
  } else if (appsink == GST_APP_SINK(session->send_rtcp_appsink_)) {
    // RTCP packet - send via Component 2 (or 1 if rtcp_mux)
    session->SendRtcpData(map.data, map.size);
  }

  gst_buffer_unmap(buffer, &map);
  gst_sample_unref(sample);

  return GST_FLOW_OK;
}
```

---

### `static void Session::OnPadAdded(GstElement* rtpbin, GstPad* pad, gpointer user_data)`

**Called by:** GStreamer when rtpbin creates dynamic pad for incoming stream

**Implementation:** MOSTLY UNCHANGED (rtpbin generates same recv_rtp_src pads)

```cpp
void Session::OnPadAdded(GstElement* rtpbin, GstPad* pad, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  gchar* pad_name = gst_pad_get_name(pad);
  GstCaps* caps = gst_pad_get_current_caps(pad);

  if (!caps) {
    caps = gst_pad_query_caps(pad, NULL);
  }

  gchar* caps_str = gst_caps_to_string(caps);
  LOG_INFO("Pad added: {} with caps: {}", pad_name, caps_str);

  GstStructure* structure = gst_caps_get_structure(caps, 0);
  const gchar* media = gst_structure_get_string(structure, "media");

  if (media && g_strcmp0(media, "audio") == 0) {
    LOG_INFO("Setting up audio playback for incoming audio stream");
    session->SetupAudioPlayback(pad);
  } else if (media && g_strcmp0(media, "video") == 0) {
    LOG_INFO("Video pad detected (playback not yet implemented)");
  } else {
    LOG_WARN("Unknown media type on pad: {}", media ? media : "null");
  }

  g_free(caps_str);
  g_free(pad_name);
  gst_caps_unref(caps);
}
```

---

## IceAgent Callbacks (NEW)

### `void Session::OnIceCandidate(int component_id, const std::string& candidate, const std::string& sdp_mid, int sdp_mline_index)`

**Called by:** IceAgent when new candidate is discovered

```cpp
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
```

---

### `void Session::OnComponentStateChanged(int component_id, const std::string& state)`

**Called by:** IceAgent when component state changes

```cpp
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
}
```

---

### `void Session::OnIceDataReceived(int component_id, const uint8_t* data, size_t len)`

**Called by:** IceAgent when data arrives from network

```cpp
void Session::OnIceDataReceived(int component_id, const uint8_t* data, size_t len) {
  LOG_DEBUG("Received {} bytes on component {}", len, component_id);

  // Phase 3 will decrypt via DTLS-SRTP here
  // For Phase 2, just stub
  LOG_DEBUG("TODO: Decrypt DTLS-SRTP and push to rtpbin via appsrc");

  // Phase 4 will implement:
  // if (component_id == 1) {
  //   // Decrypt RTP
  //   PushRtpData(decrypted_data, decrypted_len);
  // } else if (component_id == 2) {
  //   // Decrypt RTCP
  //   PushRtcpData(decrypted_data, decrypted_len);
  // }
}
```

---

### `void Session::OnGatheringDone()`

**Called by:** IceAgent when all candidates gathered

```cpp
void Session::OnGatheringDone() {
  LOG_INFO("ICE candidate gathering done");

  Event event;
  event.type = Event::ICE_GATHERING_STATE_CHANGE;
  event.data = "complete";

  PushEvent(event);
}
```

---

## Data Flow Helpers (NEW - STUBS for Phase 3)

### `void Session::SendRtpData(const uint8_t* data, size_t len)`

```cpp
void Session::SendRtpData(const uint8_t* data, size_t len) {
  LOG_DEBUG("Sending {} bytes of RTP via Component 1", len);

  // Phase 3 will encrypt via DTLS-SRTP here
  // For Phase 2, just stub
  LOG_DEBUG("TODO: Encrypt DTLS-SRTP and send via IceAgent");

  // Phase 4 will implement:
  // uint8_t encrypted[len + SRTP_OVERHEAD];
  // size_t encrypted_len = dtls_srtp_->EncryptRTP(data, len, encrypted);
  // ice_agent_->Send(1, encrypted, encrypted_len);
}
```

---

### `void Session::SendRtcpData(const uint8_t* data, size_t len)`

```cpp
void Session::SendRtcpData(const uint8_t* data, size_t len) {
  int component_id = rtcp_mux_ ? 1 : 2;
  LOG_DEBUG("Sending {} bytes of RTCP via Component {}", len, component_id);

  // Phase 3 will encrypt via DTLS-SRTP here
  LOG_DEBUG("TODO: Encrypt DTLS-SRTP and send via IceAgent");

  // Phase 4 will implement:
  // ice_agent_->Send(component_id, encrypted, encrypted_len);
}
```

---

### `void Session::PushRtpData(const uint8_t* data, size_t len)`

```cpp
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
```

---

### `void Session::PushRtcpData(const uint8_t* data, size_t len)`

```cpp
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
```

---

## Summary

**Phase 2 Complete When:**
- ✅ All methods have correct signatures
- ✅ IceAgent fully integrated
- ✅ rtpbin + appsink/appsrc pipeline working
- ✅ Event queue populated from IceAgent callbacks
- ✅ Compiles without errors
- ✅ Python gRPC integration unchanged

**Stubs for Phase 3/4:**
- SDP parsing and generation
- DTLS-SRTP encryption/decryption
- Actual data flow through encryption layer
- rtpbin stats extraction

**Files to modify:**
- `drunk_call_service/include/session.h` - Update private members
- `drunk_call_service/src/session.cc` - Implement all methods above

**Ready to implement?**
