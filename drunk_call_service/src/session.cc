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
    : config_(config), pipeline_(nullptr), webrtcbin_(nullptr), playback_pipeline_(nullptr) {
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
  g_signal_connect(webrtcbin_, "on-negotiation-needed",
                   G_CALLBACK(OnNegotiationNeeded), this);
  g_signal_connect(webrtcbin_, "pad-added",
                   G_CALLBACK(OnPadAdded), this);

  LOG_INFO("Connected webrtcbin signal handlers for ICE, state changes, negotiation, and pad-added");

  // Create audio source based on microphone_device config
  GstElement* audiosrc = nullptr;
  if (!config_.microphone_device.empty()) {
    // Use specific PulseAudio device
    audiosrc = gst_element_factory_make("pulsesrc", "audiosrc");
    if (audiosrc) {
      g_object_set(audiosrc, "device", config_.microphone_device.c_str(), NULL);
      LOG_INFO("Using selected microphone device: {}", config_.microphone_device);
    } else {
      LOG_ERROR("Failed to create pulsesrc element");
      gst_object_unref(pipeline_);
      return false;
    }
  } else {
    // Use system default (auto-detect)
    audiosrc = gst_element_factory_make("autoaudiosrc", "audiosrc");
    if (audiosrc) {
      LOG_INFO("Using default microphone device (autoaudiosrc)");
    } else {
      LOG_ERROR("Failed to create autoaudiosrc element");
      gst_object_unref(pipeline_);
      return false;
    }
  }

  // Create rest of audio capture pipeline
  GstElement* audioconvert = gst_element_factory_make("audioconvert", "audioconv");
  GstElement* audioresample = gst_element_factory_make("audioresample", "audioresample");
  GstElement* opusenc = gst_element_factory_make("opusenc", "opusenc");
  GstElement* rtpopuspay = gst_element_factory_make("rtpopuspay", "rtpopuspay");

  if (!audioconvert || !audioresample || !opusenc || !rtpopuspay) {
    LOG_ERROR("Failed to create audio pipeline elements");
    gst_object_unref(pipeline_);
    return false;
  }

  // Add audio elements to pipeline
  gst_bin_add_many(GST_BIN(pipeline_), audiosrc, audioconvert, audioresample,
                   opusenc, rtpopuspay, NULL);

  // Link audio pipeline: audiosrc -> audioconvert -> audioresample -> opusenc -> rtpopuspay -> webrtcbin
  if (!gst_element_link_many(audiosrc, audioconvert, audioresample, opusenc, rtpopuspay, NULL)) {
    LOG_ERROR("Failed to link audio elements");
    gst_object_unref(pipeline_);
    return false;
  }

  // Link audio pipeline to webrtcbin (webrtcbin will create transceivers automatically)
  GstPad* audio_src_pad = gst_element_get_static_pad(rtpopuspay, "src");
  GstPad* audio_sink_pad = gst_element_request_pad_simple(webrtcbin_, "sink_%u");

  if (gst_pad_link(audio_src_pad, audio_sink_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link audio to webrtcbin");
    gst_object_unref(audio_src_pad);
    gst_object_unref(audio_sink_pad);
    gst_object_unref(pipeline_);
    return false;
  }

  gst_object_unref(audio_src_pad);
  gst_object_unref(audio_sink_pad);
  LOG_INFO("Audio pipeline linked to webrtcbin (transceivers will be created automatically)");

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

      // Add video elements to pipeline
      gst_bin_add_many(GST_BIN(pipeline_), videotestsrc, videoconvert, vp8enc, rtpvp8pay, NULL);

      // Link video pipeline: videotestsrc -> videoconvert -> vp8enc -> rtpvp8pay -> webrtcbin
      if (!gst_element_link_many(videotestsrc, videoconvert, vp8enc, rtpvp8pay, NULL)) {
        LOG_WARN("Failed to link video elements, continuing without video");
      } else {
        // Link video pipeline to webrtcbin
        GstPad* video_src_pad = gst_element_get_static_pad(rtpvp8pay, "src");
        GstPad* video_sink_pad = gst_element_request_pad_simple(webrtcbin_, "sink_%u");

        if (gst_pad_link(video_src_pad, video_sink_pad) != GST_PAD_LINK_OK) {
          LOG_WARN("Failed to link video to webrtcbin, continuing without video");
        } else {
          LOG_INFO("Video pipeline linked to webrtcbin (transceivers will be created automatically)");
        }

        gst_object_unref(video_src_pad);
        gst_object_unref(video_sink_pad);
      }
    }
  }

  initialized_ = true;
  LOG_INFO("Session initialized: {}", config_.session_id);
  return true;
}

bool Session::AddTransceivers() {
  if (!initialized_) {
    LOG_ERROR("Cannot add transceivers: session not initialized");
    return false;
  }

  LOG_INFO("Adding transceivers to webrtcbin for session: {}", config_.session_id);

  // Check how many transceivers exist before adding
  GArray* transceivers = NULL;
  g_signal_emit_by_name(webrtcbin_, "get-transceivers", &transceivers);
  LOG_INFO("webrtcbin has {} transceivers before adding audio", transceivers ? transceivers->len : 0);
  if (transceivers) g_array_unref(transceivers);

  // Add audio transceiver explicitly (required for webrtcbin to include audio in offer)
  GstWebRTCRTPTransceiver* audio_transceiver = NULL;
  g_signal_emit_by_name(webrtcbin_, "add-transceiver", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV,
                        gst_caps_from_string("application/x-rtp,media=audio"), &audio_transceiver);

  if (!audio_transceiver) {
    LOG_ERROR("Failed to add audio transceiver");
    return false;
  }

  LOG_INFO("Added audio transceiver");
  gst_object_unref(audio_transceiver);

  // Link audio pipeline to webrtcbin
  // Get the rtpopuspay element (already exists in pipeline)
  GstElement* rtpopuspay = gst_bin_get_by_name(GST_BIN(pipeline_), "rtpopuspay");
  if (!rtpopuspay) {
    LOG_ERROR("Failed to find rtpopuspay element in pipeline");
    return false;
  }

  GstPad* audio_src_pad = gst_element_get_static_pad(rtpopuspay, "src");
  GstPad* audio_sink_pad = gst_element_request_pad_simple(webrtcbin_, "sink_0");

  if (gst_pad_link(audio_src_pad, audio_sink_pad) != GST_PAD_LINK_OK) {
    LOG_ERROR("Failed to link audio to webrtcbin");
    gst_object_unref(audio_src_pad);
    gst_object_unref(audio_sink_pad);
    gst_object_unref(rtpopuspay);
    return false;
  }

  gst_object_unref(audio_src_pad);
  gst_object_unref(audio_sink_pad);
  gst_object_unref(rtpopuspay);
  LOG_INFO("Audio pipeline linked to webrtcbin sink_0");

  // Add video transceiver if camera is requested
  if (!config_.camera_device.empty()) {
    GstWebRTCRTPTransceiver* video_transceiver = NULL;
    g_signal_emit_by_name(webrtcbin_, "add-transceiver", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV,
                          gst_caps_from_string("application/x-rtp,media=video"), &video_transceiver);

    if (!video_transceiver) {
      LOG_WARN("Failed to add video transceiver, continuing without video");
    } else {
      LOG_INFO("Added video transceiver");
      gst_object_unref(video_transceiver);

      // Link video pipeline to webrtcbin
      GstElement* rtpvp8pay = gst_bin_get_by_name(GST_BIN(pipeline_), "rtpvp8pay");
      if (rtpvp8pay) {
        GstPad* video_src_pad = gst_element_get_static_pad(rtpvp8pay, "src");
        GstPad* video_sink_pad = gst_element_request_pad_simple(webrtcbin_, "sink_1");

        if (gst_pad_link(video_src_pad, video_sink_pad) != GST_PAD_LINK_OK) {
          LOG_WARN("Failed to link video to webrtcbin, continuing without video");
        } else {
          LOG_INFO("Video pipeline linked to webrtcbin sink_1");
        }

        gst_object_unref(video_src_pad);
        gst_object_unref(video_sink_pad);
        gst_object_unref(rtpvp8pay);
      } else {
        LOG_WARN("Failed to find rtpvp8pay element in pipeline");
      }
    }
  }

  LOG_INFO("Transceivers added successfully");
  return true;
}

void Session::Close() {
  if (!initialized_) {
    return;
  }

  LOG_INFO("Closing session: {}", config_.session_id);

  // Stop playback pipeline first
  if (playback_pipeline_) {
    gst_element_set_state(playback_pipeline_, GST_STATE_NULL);
    gst_object_unref(playback_pipeline_);
    playback_pipeline_ = nullptr;
    LOG_DEBUG("Playback pipeline closed");
  }

  // Stop main pipeline
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

  // Wait for on-negotiation-needed signal to fire (indicates webrtcbin is ready)
  LOG_INFO("Waiting for on-negotiation-needed signal...");
  {
    std::unique_lock<std::mutex> lock(negotiation_mutex_);
    auto timeout = std::chrono::seconds(5);
    if (!negotiation_cv_.wait_for(lock, timeout, [this] { return negotiation_needed_; })) {
      LOG_ERROR("Timeout waiting for on-negotiation-needed signal");
      return "";
    }
  }
  LOG_INFO("on-negotiation-needed signal received, proceeding with create-offer");

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

  // Set pipeline to PLAYING state FIRST (webrtcbin needs to be PLAYING to accept remote description)
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

  // For incoming calls: Explicitly add sendrecv transceiver BEFORE setting remote description
  // This ensures webrtcbin knows we can both send and receive
  // Without this, webrtcbin sees only our send pad and creates a recvonly transceiver
  LOG_INFO("Adding sendrecv audio transceiver for incoming call");
  GstWebRTCRTPTransceiver* audio_transceiver = NULL;
  GstCaps* audio_caps = gst_caps_from_string("application/x-rtp,media=audio");
  g_signal_emit_by_name(webrtcbin_, "add-transceiver",
                        GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV,
                        audio_caps, &audio_transceiver);
  gst_caps_unref(audio_caps);

  if (!audio_transceiver) {
    LOG_ERROR("Failed to add sendrecv audio transceiver");
    return "";
  }

  LOG_INFO("Added sendrecv audio transceiver");
  gst_object_unref(audio_transceiver);

  // NOW set remote description (offer) - pipeline must be PLAYING and transceiver added first!
  if (!remote_sdp.empty()) {
    LOG_INFO("Setting remote offer after pipeline is PLAYING ({} bytes)", remote_sdp.size());
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
    LOG_INFO("Remote offer set successfully (after pipeline was PLAYING)");
  }

  // The offer_has_bundle parameter is informational (we already set bundle-policy at init)

  // Create a promise for the answer (pipeline is already PLAYING and remote description is set)
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

  if (!webrtcbin_) {
    LOG_WARN("GetStats called but webrtcbin is null");
    stats.connection_state = "new";
    stats.ice_connection_state = "new";
    stats.ice_gathering_state = "new";
    return stats;
  }

  // Get connection states from webrtcbin properties
  GstWebRTCPeerConnectionState connection_state = GST_WEBRTC_PEER_CONNECTION_STATE_NEW;
  GstWebRTCICEConnectionState ice_connection_state = GST_WEBRTC_ICE_CONNECTION_STATE_NEW;
  GstWebRTCICEGatheringState ice_gathering_state = GST_WEBRTC_ICE_GATHERING_STATE_NEW;

  g_object_get(webrtcbin_,
               "connection-state", &connection_state,
               "ice-connection-state", &ice_connection_state,
               "ice-gathering-state", &ice_gathering_state,
               NULL);

  // Convert enums to strings
  const char* connection_state_names[] = {"new", "connecting", "connected", "disconnected", "failed", "closed"};
  const char* ice_connection_state_names[] = {"new", "checking", "connected", "completed", "failed", "disconnected", "closed"};
  const char* ice_gathering_state_names[] = {"new", "gathering", "complete"};

  if (connection_state >= 0 && connection_state < 6) {
    stats.connection_state = connection_state_names[connection_state];
  }
  if (ice_connection_state >= 0 && ice_connection_state < 7) {
    stats.ice_connection_state = ice_connection_state_names[ice_connection_state];
  }
  if (ice_gathering_state >= 0 && ice_gathering_state < 3) {
    stats.ice_gathering_state = ice_gathering_state_names[ice_gathering_state];
  }

  // Get WebRTC stats using get-stats signal
  // This returns a GstStructure with detailed statistics
  GstPromise* promise = gst_promise_new();
  g_signal_emit_by_name(webrtcbin_, "get-stats", NULL, promise);

  // Wait for promise to complete
  gst_promise_wait(promise);

  const GstStructure* stats_struct = gst_promise_get_reply(promise);
  if (stats_struct) {
    int64_t bytes_sent = 0;
    int64_t bytes_received = 0;
    std::map<std::string, std::string> local_candidates_map;
    std::map<std::string, std::string> remote_candidates_map;
    std::string nominated_local_id;
    std::string nominated_remote_id;
    std::string local_candidate_type;
    std::string remote_candidate_type;

    // DEBUG: Dump entire stats structure to see what GStreamer provides
    int n_fields = gst_structure_n_fields(stats_struct);
    LOG_DEBUG("GStreamer stats structure has {} fields - dumping ALL:", n_fields);

    for (int i = 0; i < n_fields; i++) {  // Log ALL fields
      const gchar* field_name = gst_structure_nth_field_name(stats_struct, i);
      const GValue* field_value = gst_structure_get_value(stats_struct, field_name);
      const gchar* type_name = G_VALUE_TYPE_NAME(field_value);

      LOG_DEBUG("  Field[{}]: name='{}', type={}", i, field_name, type_name);

      if (GST_VALUE_HOLDS_STRUCTURE(field_value)) {
        const GstStructure* stat = gst_value_get_structure(field_value);
        gchar* stat_str = gst_structure_to_string(stat);
        LOG_DEBUG("    {}", stat_str);
        g_free(stat_str);
      }
    }

    // Iterate through all stats objects
    for (int i = 0; i < n_fields; i++) {
      const gchar* field_name = gst_structure_nth_field_name(stats_struct, i);
      const GValue* field_value = gst_structure_get_value(stats_struct, field_name);

      if (!GST_VALUE_HOLDS_STRUCTURE(field_value)) {
        continue;
      }

      const GstStructure* stat = gst_value_get_structure(field_value);

      // GStreamer uses GstWebRTCStatsType enum, not a string "type" field
      // The structure name tells us the type (e.g., "outbound-rtp", "local-candidate")
      const gchar* struct_name = gst_structure_get_name(stat);

      if (!struct_name) {
        continue;
      }

      // Parse RTP outbound stats for bytes sent
      if (g_strcmp0(struct_name, "outbound-rtp") == 0) {
        guint64 sent = 0;
        if (gst_structure_get_uint64(stat, "bytes-sent", &sent)) {
          bytes_sent += sent;
        }
      }
      // Parse RTP inbound stats for bytes received
      else if (g_strcmp0(struct_name, "inbound-rtp") == 0) {
        guint64 received = 0;
        if (gst_structure_get_uint64(stat, "bytes-received", &received)) {
          bytes_received += received;
        }
      }
      // Parse candidates - use FIELD NAME to determine local vs remote
      // Field names starting with "ice-candidate-local_" are OUR candidates
      // Field names starting with "ice-candidate-remote_" are PEER's candidates
      else if (g_strcmp0(struct_name, "local-candidate") == 0 || g_strcmp0(struct_name, "remote-candidate") == 0) {
        const gchar* ip = gst_structure_get_string(stat, "address");
        guint port = 0;
        const gchar* candidate_type = gst_structure_get_string(stat, "candidate-type");

        if (ip && gst_structure_get_uint(stat, "port", &port) && candidate_type) {
          std::string candidate_str = std::string(ip) + ":" + std::to_string(port) + " (" + candidate_type + ")";

          // Use field name prefix to determine if it's local or remote
          if (strncmp(field_name, "ice-candidate-local_", 20) == 0) {
            // This is OUR candidate (what we sent to peer)
            local_candidates_map[field_name] = candidate_str;
          } else if (strncmp(field_name, "ice-candidate-remote_", 21) == 0) {
            // This is PEER's candidate (what they sent to us)
            remote_candidates_map[field_name] = candidate_str;
          }
        }
      }
      // Parse candidate pair to find active pair
      else if (g_strcmp0(struct_name, "candidate-pair") == 0) {
        // GStreamer only shows the selected/active pair in stats
        const gchar* local_id = gst_structure_get_string(stat, "local-candidate-id");
        const gchar* remote_id = gst_structure_get_string(stat, "remote-candidate-id");
        if (local_id && remote_id) {
          nominated_local_id = local_id;
          nominated_remote_id = remote_id;
        }
      }
    }

    // Copy candidates to stats
    for (const auto& pair : local_candidates_map) {
      stats.local_candidates.push_back(pair.second);
    }
    for (const auto& pair : remote_candidates_map) {
      stats.remote_candidates.push_back(pair.second);
    }

    // Sort candidates for consistent display
    std::sort(stats.local_candidates.begin(), stats.local_candidates.end());
    std::sort(stats.remote_candidates.begin(), stats.remote_candidates.end());

    // Set byte counts
    stats.bytes_sent = bytes_sent;
    stats.bytes_received = bytes_received;

    // Calculate bandwidth (delta over time)
    auto now = std::chrono::steady_clock::now();
    if (last_stats_time_.time_since_epoch().count() > 0) {
      auto delta_time = std::chrono::duration<double>(now - last_stats_time_).count();
      if (delta_time > 0) {
        int64_t delta_bytes = (bytes_sent + bytes_received) - (last_bytes_sent_ + last_bytes_received_);
        double bandwidth_bytes_per_sec = delta_bytes / delta_time;
        stats.bandwidth_kbps = static_cast<int64_t>(bandwidth_bytes_per_sec * 8 / 1000); // Convert to Kbps
      }
    }

    // Update last stats for next calculation
    last_stats_time_ = now;
    last_bytes_sent_ = bytes_sent;
    last_bytes_received_ = bytes_received;

    // Determine connection type from nominated pair
    std::string local_ip;
    std::string remote_ip;
    if (!nominated_local_id.empty() && !nominated_remote_id.empty()) {
      // Re-iterate to find candidate types and IPs for nominated pair
      // nominated_local_id points to ice-candidate-local_* (our candidate)
      // nominated_remote_id points to ice-candidate-remote_* (peer's candidate)
      for (int i = 0; i < n_fields; i++) {
        const gchar* field_name = gst_structure_nth_field_name(stats_struct, i);

        // Check if this is one of the nominated candidates
        if (g_strcmp0(field_name, nominated_local_id.c_str()) == 0) {
          const GValue* field_value = gst_structure_get_value(stats_struct, field_name);
          if (GST_VALUE_HOLDS_STRUCTURE(field_value)) {
            const GstStructure* stat = gst_value_get_structure(field_value);
            const gchar* type = gst_structure_get_string(stat, "candidate-type");
            if (type) local_candidate_type = type;

            // Get IP and port
            const gchar* ip = gst_structure_get_string(stat, "address");
            guint port = 0;
            if (ip && gst_structure_get_uint(stat, "port", &port)) {
              local_ip = std::string(ip) + ":" + std::to_string(port);
            }
          }
        }

        if (g_strcmp0(field_name, nominated_remote_id.c_str()) == 0) {
          const GValue* field_value = gst_structure_get_value(stats_struct, field_name);
          if (GST_VALUE_HOLDS_STRUCTURE(field_value)) {
            const GstStructure* stat = gst_value_get_structure(field_value);
            const gchar* type = gst_structure_get_string(stat, "candidate-type");
            if (type) remote_candidate_type = type;

            // Get IP and port
            const gchar* ip = gst_structure_get_string(stat, "address");
            guint port = 0;
            if (ip && gst_structure_get_uint(stat, "port", &port)) {
              remote_ip = std::string(ip) + ":" + std::to_string(port);
            }
          }
        }
      }

      // Determine connection type string with IP details
      // Show the connection path: local → remote
      if (local_candidate_type == "relay" || remote_candidate_type == "relay") {
        if (local_candidate_type == "relay" && remote_candidate_type == "relay") {
          stats.connection_type = "TURN relay (" + local_ip + " → " + remote_ip + ")";
        } else if (local_candidate_type == "relay") {
          stats.connection_type = "TURN relay (our: " + local_ip + ")";
        } else {
          stats.connection_type = "TURN relay (peer: " + remote_ip + ")";
        }
      } else if (local_candidate_type == "srflx" && remote_candidate_type == "srflx") {
        stats.connection_type = "P2P (NAT hole-punching)";
      } else if (local_candidate_type == "host" && remote_candidate_type == "host") {
        stats.connection_type = "P2P (direct)";
      } else {
        stats.connection_type = "P2P (" + local_candidate_type + " → " + remote_candidate_type + ")";
      }
    } else {
      stats.connection_type = "Unknown";
    }
  }

  gst_promise_unref(promise);

  LOG_DEBUG("GetStats: state={}, ice={}, bytes_sent={}, bytes_received={}, bandwidth={}Kbps, type={}",
            stats.connection_state, stats.ice_connection_state,
            stats.bytes_sent, stats.bytes_received, stats.bandwidth_kbps, stats.connection_type);

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

  // Use GStreamer's mid naming pattern (matches what appears in SDP)
  // This MUST match what Python's Jingle adapter uses for content names
  // GStreamer generates: a=mid:audio0 for first audio, a=mid:video1 for first video
  if (mline_index == 0) {
    event.sdp_mid = "audio0";
  } else if (mline_index == 1) {
    event.sdp_mid = "video1";
  } else {
    event.sdp_mid = "audio" + std::to_string(mline_index);
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

void Session::OnNegotiationNeeded(GstElement* webrtcbin, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  LOG_INFO("on-negotiation-needed signal fired for session: {}", session->config_.session_id);

  // Signal that negotiation is needed
  {
    std::lock_guard<std::mutex> lock(session->negotiation_mutex_);
    session->negotiation_needed_ = true;
  }
  session->negotiation_cv_.notify_one();
}

void Session::OnPadAdded(GstElement* webrtcbin, GstPad* pad, gpointer user_data) {
  Session* session = static_cast<Session*>(user_data);

  // Get pad name and caps to identify the media type
  gchar* pad_name = gst_pad_get_name(pad);
  GstCaps* caps = gst_pad_get_current_caps(pad);

  if (!caps) {
    caps = gst_pad_query_caps(pad, NULL);
  }

  gchar* caps_str = gst_caps_to_string(caps);
  LOG_INFO("Pad added: {} with caps: {}", pad_name, caps_str);

  // Check if this is an audio pad (application/x-rtp with media=audio)
  GstStructure* structure = gst_caps_get_structure(caps, 0);
  const gchar* media = gst_structure_get_string(structure, "media");

  if (media && g_strcmp0(media, "audio") == 0) {
    LOG_INFO("Setting up audio playback for incoming audio stream");
    session->SetupAudioPlayback(pad);
  } else if (media && g_strcmp0(media, "video") == 0) {
    LOG_INFO("Video pad detected (playback not yet implemented)");
    // TODO: Implement video playback
  } else {
    LOG_WARN("Unknown media type on pad: {}", media ? media : "null");
  }

  g_free(caps_str);
  g_free(pad_name);
  gst_caps_unref(caps);
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

  // Store reference to first element for cleanup
  playback_pipeline_ = depay;

  gst_object_unref(sink_pad);
}

}  // namespace drunk_call
