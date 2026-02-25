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
