/**
 * WebRTC Session Implementation - Audio Pipeline
 *
 * Audio pipeline setup for answerer and offerer modes
 */

#include "webrtc_session.h"
#include "logger.h"
#include <gst/webrtc/webrtc.h>

namespace drunk_call {

bool WebRTCSession::setup_answerer_audio_pipeline() {
    try {
        LOG_DEBUG("[WebRTCSession] [ANSWERER] Creating audio source pipeline...");

        // CRITICAL: Pause pipeline before adding elements to avoid FLUSHING state
        GstState current_state, pending_state;
        gst_element_get_state(pipeline_, &current_state, &pending_state, 0);
        LOG_INFO("[WebRTCSession] Pipeline state before pause: current={}, pending={}",
                 gst_element_state_get_name(current_state),
                 gst_element_state_get_name(pending_state));

        LOG_DEBUG("[WebRTCSession] Pausing pipeline to add audio elements...");
        GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PAUSED);
        LOG_DEBUG("[WebRTCSession] Pause state change result: {}", static_cast<int>(ret));
        gst_element_get_state(pipeline_, &current_state, nullptr, GST_CLOCK_TIME_NONE);
        LOG_INFO("[WebRTCSession] Pipeline state after pause: {}", gst_element_state_get_name(current_state));

        // Create audio elements - platform-specific audio source selection
        const char *src_name;
        if (config_.microphone_device.empty()) {
            // No device specified: use auto-detection (works on all platforms)
            src_name = "autoaudiosrc";
        } else {
            // Device specified: use platform-specific source
            #ifdef _WIN32
                src_name = "wasapisrc";      // Windows Audio Session API
            #elif __APPLE__
                src_name = "osxaudiosrc";    // macOS CoreAudio
            #else
                src_name = "pulsesrc";       // Linux PulseAudio
            #endif
        }

        audio_src_ = gst_element_factory_make(src_name, "audio_src");
        if (!audio_src_) {
            LOG_ERROR("[WebRTCSession] Failed to create audio source: {}", src_name);
            return false;
        }

        // Only create webrtcdsp if at least one DSP feature is enabled
        bool use_dsp = config_.echo_cancel || config_.noise_suppression || config_.gain_control;
        GstElement *webrtcdsp = nullptr;
        if (use_dsp) {
            webrtcdsp = gst_element_factory_make("webrtcdsp", "webrtcdsp");
            if (!webrtcdsp) {
                LOG_ERROR("[WebRTCSession] Failed to create webrtcdsp element");
                return false;
            }
        }

        volume_ = gst_element_factory_make("volume", "volume");
        GstElement *queue = gst_element_factory_make("queue", "queue_src");
        GstElement *convert = gst_element_factory_make("audioconvert", "convert");
        GstElement *resample = gst_element_factory_make("audioresample", "resample");
        GstElement *opusenc = gst_element_factory_make("opusenc", "opusenc");
        GstElement *rtpopuspay = gst_element_factory_make("rtpopuspay", "rtpopuspay");
        GstElement *capsfilter = gst_element_factory_make("capsfilter", "rtp_caps");

        if (!volume_ || !queue || !convert || !resample || !opusenc || !rtpopuspay || !capsfilter) {
            LOG_ERROR("[WebRTCSession] Failed to create audio elements");
            return false;
        }

        // Configure microphone device if specified
        if (!config_.microphone_device.empty()) {
            g_object_set(audio_src_, "device", config_.microphone_device.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] ✓ Set microphone device: {} (using {})",
                     config_.microphone_device, src_name);
        } else {
            LOG_INFO("[WebRTCSession] Using {} (default device)", src_name);
        }

        // Configure webrtcdsp if enabled
        if (use_dsp) {
            g_object_set(webrtcdsp,
                "probe", "webrtcechoprobe0",  // Name of echoprobe created in create_pipeline()
                "echo-cancel", config_.echo_cancel,
                "echo-suppression-level", config_.echo_suppression_level,
                "noise-suppression", config_.noise_suppression,
                "noise-suppression-level", config_.noise_suppression_level,
                "gain-control", config_.gain_control,
                nullptr);
            LOG_INFO("[WebRTCSession] ✓ Configured webrtcdsp: probe=webrtcechoprobe0, echo_cancel={}, echo_level={}, noise_supp={}, noise_level={}, gain_ctrl={}",
                    config_.echo_cancel, config_.echo_suppression_level,
                    config_.noise_suppression, config_.noise_suppression_level, config_.gain_control);
        } else {
            LOG_INFO("[WebRTCSession] DSP disabled (all features off)");
        }

        // Configure elements
        // Note: autoaudiosrc doesn't have "is-live" property (it's on pulsesrc child)
        // The child pulsesrc is automatically configured as live

        // Configure opusenc for negotiated channels (mono vs stereo)
        if (negotiated_channels_ == 2) {
            // Stereo configuration
            g_object_set(opusenc,
                "bitrate", 64000,          // Higher bitrate for stereo
                "frame-size", 20,
                "audio-type", 2049,        // Generic audio (not voice-only)
                nullptr);
            LOG_INFO("[WebRTCSession] ✓ Configured opusenc for STEREO (channels=2, bitrate=64kbps)");
        } else {
            // Mono configuration (default)
            g_object_set(opusenc,
                "bitrate", 32000,
                "frame-size", 20,
                nullptr);
            LOG_INFO("[WebRTCSession] ✓ Configured opusenc for MONO (channels=1, bitrate=32kbps)");
        }

        // CRITICAL: Use negotiated payload type from answer SDP
        // If negotiated_payload_ is -1, we're in offerer mode, use 111 (OPUS standard)
        int payload = (negotiated_payload_ > 0) ? negotiated_payload_ : 111;

        GstCaps *rtp_caps = gst_caps_new_simple("application/x-rtp",
            "media", G_TYPE_STRING, "audio",
            "encoding-name", G_TYPE_STRING, "OPUS",
            "payload", G_TYPE_INT, payload,
            nullptr);
        g_object_set(capsfilter, "caps", rtp_caps, nullptr);
        gst_caps_unref(rtp_caps);
        LOG_INFO("[WebRTCSession] ✓ Set RTP caps: application/x-rtp,media=audio,encoding-name=OPUS,payload={}", payload);

        // Add to pipeline
        if (use_dsp) {
            gst_bin_add_many(GST_BIN(pipeline_), audio_src_, webrtcdsp, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
        } else {
            gst_bin_add_many(GST_BIN(pipeline_), audio_src_, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
        }

        // Link audio chain FIRST (while pipeline is PAUSED, elements are NULL)
        // Note: capsfilter goes between rtpopuspay and webrtcbin
        bool link_ok;
        if (use_dsp) {
            // With DSP: src→webrtcdsp→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter
            link_ok = gst_element_link_many(audio_src_, webrtcdsp, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
            if (link_ok) {
                LOG_INFO("[WebRTCSession] ✓ Linked audio chain: src→webrtcdsp→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter");
            }
        } else {
            // Without DSP: src→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter
            link_ok = gst_element_link_many(audio_src_, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
            if (link_ok) {
                LOG_INFO("[WebRTCSession] ✓ Linked audio chain: src→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter");
            }
        }

        if (!link_ok) {
            LOG_ERROR("[WebRTCSession] Failed to link audio chain");
            return false;
        }

        // Get webrtcbin sink pad - ANSWERER MODE
        // Reuse the pad we created during set-remote-description
        // This ensures audio pipeline connects to the same transceiver used for SDP negotiation
        if (!negotiated_pad_) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] No negotiated pad available!");
            return false;
        }

        GstPad *webrtc_sink = negotiated_pad_;
        negotiated_pad_ = nullptr;  // Transfer ownership (we'll unref at end of function)

        gchar *pad_name = gst_pad_get_name(webrtc_sink);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Reusing negotiated pad: {}", pad_name);
        g_free(pad_name);

        // CRITICAL: Get the transceiver for this pad and set direction to SENDRECV
        // Without this, the transceiver defaults to RECVONLY!
        GValue val = G_VALUE_INIT;
        g_object_get_property(G_OBJECT(webrtc_sink), "transceiver", &val);
        GstWebRTCRTPTransceiver *trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));

        if (trans) {
            LOG_INFO("[WebRTCSession] Setting transceiver direction to SENDRECV...");
            g_object_set(trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
            LOG_INFO("[WebRTCSession] ✓ Transceiver direction set to SENDRECV");
        } else {
            LOG_WARN("[WebRTCSession] Could not get transceiver from pad");
        }
        g_value_unset(&val);

        // Get negotiated caps from sink_0 (already negotiated during answer creation)
        GstCaps *sink_caps = gst_pad_get_current_caps(webrtc_sink);
        if (sink_caps) {
            gchar *caps_str = gst_caps_to_string(sink_caps);
            LOG_INFO("[WebRTCSession] sink_0 negotiated caps: {}", caps_str);
            g_free(caps_str);
        } else {
            LOG_WARN("[WebRTCSession] sink_0 has no negotiated caps yet");
        }

        // Link capsfilter to webrtcbin (capsfilter is the last element in the chain)
        GstPad *caps_src = gst_element_get_static_pad(capsfilter, "src");
        GstPadLinkReturn link_ret = gst_pad_link(caps_src, webrtc_sink);
        if (link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] Failed to link capsfilter to webrtcbin sink_0: {}", static_cast<int>(link_ret));
            if (sink_caps) gst_caps_unref(sink_caps);
            gst_object_unref(caps_src);
            gst_object_unref(webrtc_sink);
            return false;
        }
        LOG_INFO("[WebRTCSession] ✓ Linked capsfilter to sink_0");

        // Force caps negotiation on the link we just made
        if (sink_caps) {
            if (!gst_pad_set_caps(caps_src, sink_caps)) {
                LOG_WARN("[WebRTCSession] Failed to set caps on capsfilter src pad");
            } else {
                LOG_INFO("[WebRTCSession] ✓ Set negotiated caps on capsfilter");
            }
            gst_caps_unref(sink_caps);
        }

        // Check if pads are linked
        GstPad *peer_of_caps = gst_pad_get_peer(caps_src);
        GstPad *peer_of_sink = gst_pad_get_peer(webrtc_sink);
        LOG_DEBUG("[WebRTCSession] Pad peers: caps_src->peer={}, sink_0->peer={}",
                  (void*)peer_of_caps, (void*)peer_of_sink);
        if (peer_of_caps) gst_object_unref(peer_of_caps);
        if (peer_of_sink) gst_object_unref(peer_of_sink);

        gst_object_unref(caps_src);
        gst_object_unref(webrtc_sink);

        // Resume pipeline to PLAYING
        LOG_DEBUG("[WebRTCSession] Resuming pipeline to PLAYING...");
        ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
        LOG_DEBUG("[WebRTCSession] Resume state change result: {}", static_cast<int>(ret));
        gst_element_get_state(pipeline_, &current_state, nullptr, GST_CLOCK_TIME_NONE);
        LOG_INFO("[WebRTCSession] Pipeline state after resume: {}", gst_element_state_get_name(current_state));

        LOG_INFO("[WebRTCSession] [ANSWERER] Audio source pipeline created and linked");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] [ANSWERER] setup_answerer_audio_pipeline exception: {}", e.what());
        return false;
    }
}
bool WebRTCSession::setup_offerer_audio_pipeline() {
    try {
        LOG_DEBUG("[WebRTCSession] [OFFERER] Creating audio source pipeline...");

        // CRITICAL: Pause pipeline before adding elements to avoid FLUSHING state
        GstState current_state, pending_state;
        gst_element_get_state(pipeline_, &current_state, &pending_state, 0);
        LOG_INFO("[WebRTCSession] [OFFERER] Pipeline state before pause: current={}, pending={}",
                 gst_element_state_get_name(current_state),
                 gst_element_state_get_name(pending_state));

        LOG_DEBUG("[WebRTCSession] [OFFERER] Pausing pipeline to add audio elements...");
        GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PAUSED);
        LOG_DEBUG("[WebRTCSession] [OFFERER] Pause state change result: {}", static_cast<int>(ret));
        gst_element_get_state(pipeline_, &current_state, nullptr, GST_CLOCK_TIME_NONE);
        LOG_INFO("[WebRTCSession] [OFFERER] Pipeline state after pause: {}", gst_element_state_get_name(current_state));

        // Create audio elements - platform-specific audio source selection
        const char *src_name;
        if (config_.microphone_device.empty()) {
            // No device specified: use auto-detection (works on all platforms)
            src_name = "autoaudiosrc";
        } else {
            // Device specified: use platform-specific source
            #ifdef _WIN32
                src_name = "wasapisrc";      // Windows Audio Session API
            #elif __APPLE__
                src_name = "osxaudiosrc";    // macOS CoreAudio
            #else
                src_name = "pulsesrc";       // Linux PulseAudio
            #endif
        }

        audio_src_ = gst_element_factory_make(src_name, "audio_src");
        if (!audio_src_) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create audio source: {}", src_name);
            return false;
        }

        // Only create webrtcdsp if at least one DSP feature is enabled
        bool use_dsp = config_.echo_cancel || config_.noise_suppression || config_.gain_control;
        GstElement *webrtcdsp = nullptr;
        if (use_dsp) {
            webrtcdsp = gst_element_factory_make("webrtcdsp", "webrtcdsp");
            if (!webrtcdsp) {
                LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create webrtcdsp element");
                return false;
            }
        }

        volume_ = gst_element_factory_make("volume", "volume");
        GstElement *queue = gst_element_factory_make("queue", "queue_src");
        GstElement *convert = gst_element_factory_make("audioconvert", "convert");
        GstElement *resample = gst_element_factory_make("audioresample", "resample");
        GstElement *opusenc = gst_element_factory_make("opusenc", "opusenc");
        GstElement *rtpopuspay = gst_element_factory_make("rtpopuspay", "rtpopuspay");
        GstElement *capsfilter = gst_element_factory_make("capsfilter", "rtp_caps");

        if (!volume_ || !queue || !convert || !resample || !opusenc || !rtpopuspay || !capsfilter) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create audio elements");
            return false;
        }

        // Configure microphone device if specified
        if (!config_.microphone_device.empty()) {
            g_object_set(audio_src_, "device", config_.microphone_device.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] [OFFERER] ✓ Set microphone device: {} (using {})",
                     config_.microphone_device, src_name);
        } else {
            LOG_INFO("[WebRTCSession] [OFFERER] Using {} (default device)", src_name);
        }

        // Configure webrtcdsp if enabled
        if (use_dsp) {
            g_object_set(webrtcdsp,
                "probe", "webrtcechoprobe0",  // Name of echoprobe created in create_pipeline()
                "echo-cancel", config_.echo_cancel,
                "echo-suppression-level", config_.echo_suppression_level,
                "noise-suppression", config_.noise_suppression,
                "noise-suppression-level", config_.noise_suppression_level,
                "gain-control", config_.gain_control,
                nullptr);
            LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured webrtcdsp: probe=webrtcechoprobe0, echo_cancel={}, echo_level={}, noise_supp={}, noise_level={}, gain_ctrl={}",
                    config_.echo_cancel, config_.echo_suppression_level,
                    config_.noise_suppression, config_.noise_suppression_level, config_.gain_control);
        } else {
            LOG_INFO("[WebRTCSession] [OFFERER] DSP disabled (all features off)");
        }

        // Configure opusenc for stereo (offerer always uses stereo for compatibility)
        g_object_set(opusenc,
            "bitrate", 64000,
            "frame-size", 20,
            "audio-type", 2049,        // Generic audio (not voice-only)
            nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured opusenc for STEREO (channels=2, bitrate=64kbps)");

        // Use payload=111 (matches codec-preferences we'll set)
        GstCaps *rtp_caps = gst_caps_new_simple("application/x-rtp",
            "media", G_TYPE_STRING, "audio",
            "encoding-name", G_TYPE_STRING, "OPUS",
            "payload", G_TYPE_INT, 111,
            nullptr);
        g_object_set(capsfilter, "caps", rtp_caps, nullptr);
        gst_caps_unref(rtp_caps);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Set RTP caps: application/x-rtp,media=audio,encoding-name=OPUS,payload=111");

        // Add to pipeline
        if (use_dsp) {
            gst_bin_add_many(GST_BIN(pipeline_), audio_src_, webrtcdsp, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
        } else {
            gst_bin_add_many(GST_BIN(pipeline_), audio_src_, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
        }

        // Link audio chain
        bool link_ok;
        if (use_dsp) {
            // With DSP: src→webrtcdsp→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter
            link_ok = gst_element_link_many(audio_src_, webrtcdsp, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
            if (link_ok) {
                LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked audio chain: src→webrtcdsp→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter");
            }
        } else {
            // Without DSP: src→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter
            link_ok = gst_element_link_many(audio_src_, volume_, queue, convert, resample, opusenc, rtpopuspay, capsfilter, nullptr);
            if (link_ok) {
                LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked audio chain: src→volume→queue→convert→resample→opusenc→rtpopuspay→capsfilter");
            }
        }

        if (!link_ok) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link audio chain");
            return false;
        }

        // Get webrtcbin sink pad - OFFERER MODE
        // Create new pad (will auto-create transceiver)
        GstPad *webrtc_sink = gst_element_request_pad_simple(webrtc_, "sink_%u");
        if (!webrtc_sink) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to request sink pad from webrtcbin!");
            return false;
        }

        gchar *pad_name = gst_pad_get_name(webrtc_sink);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Created new pad: {}", pad_name);
        g_free(pad_name);

        // Set transceiver direction to SENDRECV
        GValue val = G_VALUE_INIT;
        g_object_get_property(G_OBJECT(webrtc_sink), "transceiver", &val);
        GstWebRTCRTPTransceiver *trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));

        if (trans) {
            LOG_INFO("[WebRTCSession] [OFFERER] Setting transceiver direction to SENDRECV...");
            g_object_set(trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
            LOG_INFO("[WebRTCSession] [OFFERER] ✓ Transceiver direction set to SENDRECV");
        } else {
            LOG_WARN("[WebRTCSession] [OFFERER] Could not get transceiver from pad");
        }
        g_value_unset(&val);

        // Link capsfilter to webrtcbin
        GstPad *caps_src = gst_element_get_static_pad(capsfilter, "src");
        GstPadLinkReturn link_ret = gst_pad_link(caps_src, webrtc_sink);
        if (link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link capsfilter to webrtcbin: {}", static_cast<int>(link_ret));
            gst_object_unref(caps_src);
            gst_object_unref(webrtc_sink);
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked capsfilter to webrtcbin");

        gst_object_unref(caps_src);
        gst_object_unref(webrtc_sink);

        // Resume pipeline to PLAYING
        LOG_DEBUG("[WebRTCSession] [OFFERER] Resuming pipeline to PLAYING...");
        ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
        LOG_DEBUG("[WebRTCSession] [OFFERER] Resume state change result: {}", static_cast<int>(ret));
        gst_element_get_state(pipeline_, &current_state, nullptr, GST_CLOCK_TIME_NONE);
        LOG_INFO("[WebRTCSession] [OFFERER] Pipeline state after resume: {}", gst_element_state_get_name(current_state));

        LOG_INFO("[WebRTCSession] [OFFERER] Audio source pipeline created and linked");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] [OFFERER] setup_offerer_audio_pipeline exception: {}", e.what());
        return false;
    }
}

void WebRTCSession::handle_incoming_audio_stream(GstPad *pad) {
    // HANDLE AUDIO RECEIVE PIPELINE (original code)
        // ========================================================================
        // Create audio sink chain: rtpopusdepay → opusdec → queue → (echoprobe_) → autoaudiosink
        // Note: echoprobe_ was already created during pipeline initialization
        GstElement *depay = gst_element_factory_make("rtpopusdepay", "depay");
        GstElement *decoder = gst_element_factory_make("opusdec", "decoder");
        GstElement *queue = gst_element_factory_make("queue", "recv_queue");

        // Platform-specific audio sink selection
        const char *sink_name;
        if (config_.speakers_device.empty()) {
            // No device specified: use auto-detection (works on all platforms)
            sink_name = "autoaudiosink";
        } else {
            // Device specified: use platform-specific sink
            #ifdef _WIN32
                sink_name = "wasapisink";      // Windows Audio Session API
            #elif __APPLE__
                sink_name = "osxaudiosink";    // macOS CoreAudio
            #else
                sink_name = "pulsesink";       // Linux PulseAudio
            #endif
        }

        audio_sink_ = gst_element_factory_make(sink_name, "audio_sink");

        if (!depay || !decoder || !queue || !audio_sink_ || !echoprobe_) {
            LOG_ERROR("[WebRTCSession] Failed to create audio sink elements (or echoprobe missing)");
            return;
        }

        // Configure speaker device if specified
        if (!config_.speakers_device.empty()) {
            g_object_set(audio_sink_, "device", config_.speakers_device.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] ✓ Set speaker device: {} (using {})",
                     config_.speakers_device, sink_name);
        } else {
            LOG_INFO("[WebRTCSession] Using {} (default device)", sink_name);
        }

        LOG_INFO("[WebRTCSession] ✓ Using pre-created webrtcechoprobe0 for echo cancellation");

        // Add elements to pipeline (echoprobe_ already in pipeline from initialization)
        gst_bin_add_many(GST_BIN(pipeline_), depay, decoder, queue, audio_sink_, nullptr);

        // Link elements: depay → decoder → queue → echoprobe_ → sink
        if (!gst_element_link_many(depay, decoder, queue, echoprobe_, audio_sink_, nullptr)) {
            LOG_ERROR("[WebRTCSession] Failed to link audio sink chain");
            return;
        }

        // Sync state with parent
        gst_element_sync_state_with_parent(depay);
        gst_element_sync_state_with_parent(decoder);
        gst_element_sync_state_with_parent(queue);
        gst_element_sync_state_with_parent(echoprobe_);
        gst_element_sync_state_with_parent(audio_sink_);

        // Link webrtcbin pad to depay
        GstPad *sink_pad = gst_element_get_static_pad(depay, "sink");
        GstPadLinkReturn link_ret = gst_pad_link(pad, sink_pad);
        gst_object_unref(sink_pad);

        if (link_ret != GST_PAD_LINK_OK) {
            // ========================================================================
            // ISSUE #9 FIX: Cleanup zombie elements on pad link failure
            // Official Pattern: https://gstreamer.freedesktop.org/documentation/application-development/advanced/pipeline-manipulation.html
            // ========================================================================
            LOG_ERROR("[WebRTCSession] Failed to link incoming pad to depay: {}", static_cast<int>(link_ret));

            // Remove elements from pipeline
            gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, queue, audio_sink_, nullptr);

            // Set to NULL state and unref (GstBin doesn't own them anymore)
            gst_element_set_state(depay, GST_STATE_NULL);
            gst_object_unref(depay);

            gst_element_set_state(decoder, GST_STATE_NULL);
            gst_object_unref(decoder);

            gst_element_set_state(queue, GST_STATE_NULL);
            gst_object_unref(queue);

            gst_element_set_state(audio_sink_, GST_STATE_NULL);
            gst_object_unref(audio_sink_);

            audio_sink_ = nullptr;  // Mark as not created

            LOG_ERROR("[WebRTCSession] Cleaned up zombie elements after pad link failure");
            return;
        }

        LOG_DEBUG("[WebRTCSession] Incoming stream linked successfully");
}

} // namespace drunk_call
