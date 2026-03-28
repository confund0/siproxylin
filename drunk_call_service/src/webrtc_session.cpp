/**
 * WebRTC Session Implementation
 *
 * Implements MediaSession using GStreamer webrtcbin element
 * Reference: docs/CALLS/1-PIPELINE-PLAN.md
 */

#include "webrtc_session.h"
#include "logger.h"
#include <gst/sdp/sdp.h>
#include <gst/webrtc/webrtc.h>
#include <stdexcept>
#include <cstring>
#include <sstream>

namespace drunk_call {

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Extract ICE candidate type without exposing IP addresses (privacy-safe).
 * Returns: "host", "srflx", "relay", "prflx", or "unknown"
 */
static std::string extract_candidate_type(const char* candidate) {
    if (!candidate) return "unknown";

    if (strstr(candidate, "typ host")) return "host";
    if (strstr(candidate, "typ srflx")) return "srflx";
    if (strstr(candidate, "typ relay")) return "relay";
    if (strstr(candidate, "typ prflx")) return "prflx";

    return "unknown";
}

/**
 * Parse audio codec from SDP offer and create caps for codec-preferences.
 * Returns NULL on failure.
 * CRITICAL: encoding-name must be UPPERCASE (GStreamer requirement)
 */
static GstCaps* parse_audio_codec_from_offer(GstSDPMessage *offer) {
    const GstSDPMedia *media = nullptr;
    std::string encoding_name;
    int clock_rate = 0;
    int payload = -1;
    int encoding_params = 0;

    // Find first audio m-line
    for (guint i = 0; i < gst_sdp_message_medias_len(offer); i++) {
        media = gst_sdp_message_get_media(offer, i);
        if (strcmp(gst_sdp_media_get_media(media), "audio") == 0) {
            LOG_INFO("[WebRTCSession] Found audio m-line at index {}", i);
            break;
        }
        media = nullptr;
    }

    if (!media) {
        LOG_ERROR("[WebRTCSession] No audio m-line in offer!");
        return nullptr;
    }

    // Get first payload type
    if (gst_sdp_media_formats_len(media) > 0) {
        const char *payload_str = gst_sdp_media_get_format(media, 0);
        payload = atoi(payload_str);
        LOG_INFO("[WebRTCSession] First payload type: {}", payload);
    } else {
        LOG_ERROR("[WebRTCSession] No payload formats in audio m-line!");
        return nullptr;
    }

    // Find rtpmap for this payload
    for (guint i = 0; i < gst_sdp_media_attributes_len(media); i++) {
        const GstSDPAttribute *attr = gst_sdp_media_get_attribute(media, i);

        if (strcmp(attr->key, "rtpmap") == 0) {
            // Parse "111 opus/48000/2"
            int attr_payload;
            char codec_name[32];
            int rate, params = 0;

            // Try parsing with encoding-params (channels)
            if (sscanf(attr->value, "%d %31[^/]/%d/%d", &attr_payload, codec_name, &rate, &params) >= 3) {
                if (attr_payload == payload) {
                    // CRITICAL: GStreamer uppercases encoding-name!
                    for (char *p = codec_name; *p; p++) *p = toupper(*p);

                    encoding_name = codec_name;
                    clock_rate = rate;
                    encoding_params = params;

                    LOG_INFO("[WebRTCSession] Parsed codec: {}/{}/{}", encoding_name, clock_rate, encoding_params);
                    break;
                }
            }
            // Try without encoding-params
            else if (sscanf(attr->value, "%d %31[^/]/%d", &attr_payload, codec_name, &rate) == 3) {
                if (attr_payload == payload) {
                    for (char *p = codec_name; *p; p++) *p = toupper(*p);

                    encoding_name = codec_name;
                    clock_rate = rate;

                    LOG_INFO("[WebRTCSession] Parsed codec: {}/{}", encoding_name, clock_rate);
                    break;
                }
            }
        }
    }

    if (encoding_name.empty() || clock_rate == 0) {
        LOG_ERROR("[WebRTCSession] Could not parse rtpmap for payload {}", payload);
        return nullptr;
    }

    // Create caps WITHOUT fixed payload (allows flexible matching)
    GstCaps *caps = gst_caps_new_simple("application/x-rtp",
        "media", G_TYPE_STRING, "audio",
        "encoding-name", G_TYPE_STRING, encoding_name.c_str(),
        "clock-rate", G_TYPE_INT, clock_rate,
        nullptr);

    // Add encoding-params if present (for stereo/multi-channel)
    if (encoding_params > 0) {
        char params_str[16];
        snprintf(params_str, sizeof(params_str), "%d", encoding_params);
        gst_caps_set_simple(caps, "encoding-params", G_TYPE_STRING, params_str, nullptr);
    }

    LOG_INFO("[WebRTCSession] Created codec-preferences caps (payload NOT fixed)");
    return caps;
}

/**
 * Extract mid values from SDP and build mline index → mid mapping
 *
 * Parses a=mid: attribute from each media section in the SDP.
 * Returns a map of mline_index → mid value for populating sdpMid in ICE candidates.
 *
 * Example SDP:
 *   m=audio 9 UDP/TLS/RTP/SAVPF 111
 *   a=mid:audio0   <-- Extracted value
 *
 * Result: {0: "audio0"}
 *
 * @param sdp The SDP message to parse (typically our local offer)
 * @return Map of mline index to mid value
 */
static std::map<guint, std::string> extract_mid_mapping(GstSDPMessage *sdp) {
    std::map<guint, std::string> mid_map;

    if (!sdp) {
        LOG_ERROR("[WebRTCSession] extract_mid_mapping: NULL SDP provided");
        return mid_map;
    }

    guint num_media = gst_sdp_message_medias_len(sdp);
    LOG_DEBUG("[WebRTCSession] Extracting mid values from {} media section(s)", num_media);

    for (guint i = 0; i < num_media; i++) {
        const GstSDPMedia *media = gst_sdp_message_get_media(sdp, i);
        if (!media) {
            LOG_WARN("[WebRTCSession] Media section {} is NULL", i);
            continue;
        }

        // Extract a=mid: attribute using GStreamer API
        const gchar *mid_value = gst_sdp_media_get_attribute_val(media, "mid");

        if (mid_value && mid_value[0] != '\0') {
            mid_map[i] = std::string(mid_value);
            LOG_INFO("[WebRTCSession] Extracted mid: mline[{}]={}", i, mid_value);
        } else {
            // Mid should always exist in valid WebRTC SDP, but handle gracefully
            LOG_WARN("[WebRTCSession] Media section {} has no mid attribute", i);
        }
    }

    return mid_map;
}

// ============================================================================
// Constructor / Destructor
// ============================================================================

WebRTCSession::WebRTCSession()
    : pipeline_(nullptr)
    , webrtc_(nullptr)
    , audio_src_(nullptr)
    , audio_sink_(nullptr)
    , volume_(nullptr)
    , echoprobe_(nullptr)
    , is_muted_(false)
    , is_outgoing_(false)
    , negotiated_pad_(nullptr)
    , offer_codec_caps_(nullptr)
    , negotiated_payload_(-1)
    , negotiated_channels_(1)  // Default to mono (will be overridden by SDP negotiation)
    , sdp_done_(false)
    , last_bytes_sent_(0)
    , last_bytes_received_(0)
{
}

WebRTCSession::~WebRTCSession() {
    try {
        // CRITICAL: Disconnect all signals BEFORE stopping pipeline
        // If GStreamer fires a signal during/after destruction, the callback
        // receives a 'this' pointer to a partially-destroyed object → crash
        if (webrtc_) {
            g_signal_handlers_disconnect_by_data(webrtc_, this);
        }

        // Clean up negotiated pad if still held
        if (negotiated_pad_) {
            gst_object_unref(negotiated_pad_);
            negotiated_pad_ = nullptr;
        }

        // Clean up offer codec caps if still held
        if (offer_codec_caps_) {
            gst_caps_unref(offer_codec_caps_);
            offer_codec_caps_ = nullptr;
        }

        stop();
    } catch (...) {
        // Suppress exceptions in destructor
    }
}

// ============================================================================
// Lifecycle Methods
// ============================================================================

bool WebRTCSession::initialize(const SessionConfig &config) {
    try {
        config_ = config;

        if (!create_pipeline()) {
            LOG_ERROR("[WebRTCSession] Failed to create pipeline");
            return false;
        }

        if (!configure_webrtcbin()) {
            LOG_ERROR("[WebRTCSession] Failed to configure webrtcbin");
            return false;
        }

        if (!configure_proxy()) {
            LOG_ERROR("[WebRTCSession] Failed to configure proxy");
            return false;
        }

        if (!add_turn_servers()) {
            LOG_ERROR("[WebRTCSession] Failed to add TURN servers");
            return false;
        }

        connect_signals();

        LOG_INFO("[WebRTCSession] Initialized session: {}", config_.session_id);
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] Initialize failed: {}", e.what());
        return false;
    }
}

bool WebRTCSession::start() {
    try {
        if (!pipeline_) {
            LOG_ERROR("[WebRTCSession] Pipeline not initialized");
            return false;
        }

        LOG_INFO("[WebRTCSession] Starting pipeline...");
        GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);

        if (ret == GST_STATE_CHANGE_FAILURE) {
            LOG_ERROR("[WebRTCSession] Failed to set pipeline to PLAYING");
            return false;
        }

        LOG_INFO("[WebRTCSession] Pipeline PLAYING");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] Start failed: {}", e.what());
        return false;
    }
}

bool WebRTCSession::stop() {
    try {
        if (!pipeline_) {
            return true;  // Already stopped
        }

        LOG_INFO("[WebRTCSession] Stopping pipeline...");

        // ========================================================================
        // ISSUE #8 FIX: Graceful pipeline shutdown with timeout
        // Official Pattern: https://gstreamer.freedesktop.org/documentation/application-development/basics/states.html
        // ========================================================================
        gst_element_set_state(pipeline_, GST_STATE_NULL);

        // Wait for state change to complete with 5-second timeout
        GstStateChangeReturn ret = gst_element_get_state(
            pipeline_, nullptr, nullptr, GST_SECOND * 5);

        if (ret == GST_STATE_CHANGE_FAILURE) {
            LOG_ERROR("[WebRTCSession] Pipeline shutdown failed");
            // Force cleanup anyway
        } else if (ret == GST_STATE_CHANGE_ASYNC) {
            LOG_WARN("[WebRTCSession] Pipeline shutdown timed out after 5 seconds");
            // Force cleanup anyway
        } else {
            LOG_DEBUG("[WebRTCSession] Pipeline state changed to NULL successfully");
        }

        gst_object_unref(pipeline_);

        pipeline_ = nullptr;
        webrtc_ = nullptr;
        audio_src_ = nullptr;
        audio_sink_ = nullptr;

        LOG_INFO("[WebRTCSession] Pipeline stopped and cleaned up");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] Stop failed: {}", e.what());
        return false;
    }
}

// ============================================================================
// SDP Operations
// ============================================================================

void WebRTCSession::create_offer(SDPCallback callback) {
    try {
        is_outgoing_ = true;
        sdp_callback_ = callback;

        LOG_INFO("[WebRTCSession] Creating offer...");

        // CRITICAL: For offerers, create audio source pipeline BEFORE create-offer
        // request_pad_simple() will automatically create the transceiver
        LOG_INFO("[WebRTCSession] Offerer mode: Creating audio source pipeline...");

        if (!setup_offerer_audio_pipeline()) {
            LOG_ERROR("[WebRTCSession] Failed to create offerer audio source pipeline!");
            if (callback) {
                callback(false, SDPMessage(), "Failed to create audio source pipeline");
            }
            return;
        }

        LOG_INFO("[WebRTCSession] Audio source pipeline created, now setting codec preferences...");

        // CRITICAL: Set codec-preferences on transceiver BEFORE create-offer
        // This ensures we only offer OPUS (not speex/PCMU/PCMA that we don't support)
        // and use payload=111 (matching Dino's convention)

        // Get the transceiver for sink_0 (created by create_audio_source_pipeline)
        GstPad *sink_pad = gst_element_get_static_pad(webrtc_, "sink_0");
        if (sink_pad) {
            GValue val = G_VALUE_INIT;
            g_object_get_property(G_OBJECT(sink_pad), "transceiver", &val);
            GstWebRTCRTPTransceiver *trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));

            if (trans) {
                // Create codec-preferences: OPUS only, payload=111, stereo
                GstCaps *codec_prefs = gst_caps_new_simple("application/x-rtp",
                    "media", G_TYPE_STRING, "audio",
                    "encoding-name", G_TYPE_STRING, "OPUS",
                    "clock-rate", G_TYPE_INT, 48000,
                    "payload", G_TYPE_INT, 111,
                    nullptr);

                // Add encoding-params for stereo
                gst_caps_set_simple(codec_prefs, "encoding-params", G_TYPE_STRING, "2", nullptr);

                g_object_set(trans, "codec-preferences", codec_prefs, nullptr);

                gchar *caps_str = gst_caps_to_string(codec_prefs);
                LOG_INFO("[WebRTCSession] ✓ Set codec-preferences for offerer: {}", caps_str);
                g_free(caps_str);
                gst_caps_unref(codec_prefs);
            } else {
                LOG_WARN("[WebRTCSession] Could not get transceiver for codec-preferences");
            }

            g_value_unset(&val);
            gst_object_unref(sink_pad);
        } else {
            LOG_WARN("[WebRTCSession] Could not get sink_0 pad for codec-preferences");
        }

        LOG_INFO("[WebRTCSession] Creating offer...");

        // Create promise for async SDP generation
        GstPromise *promise = gst_promise_new_with_change_func(
            on_offer_created_static, this, nullptr);

        // Emit create-offer signal on webrtcbin
        g_signal_emit_by_name(webrtc_, "create-offer", nullptr, promise);

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] create_offer failed: {}", e.what());
        if (callback) {
            callback(false, SDPMessage(), std::string("Exception: ") + e.what());
        }
    }
}

void WebRTCSession::create_answer(const SDPMessage &remote_offer, SDPCallback callback) {
    LOG_INFO("[WebRTCSession] ENTERED create_answer() - FIRST LINE");
    try {
        is_outgoing_ = false;
        sdp_callback_ = callback;

        LOG_INFO("[WebRTCSession] Creating answer... (set is_outgoing_=false)");

        // Follow official GStreamer pattern: Let webrtcbin auto-create transceiver
        // from the remote offer. No manual transceiver manipulation needed.

        // First set remote description (the offer)
        if (!set_remote_description(remote_offer)) {
            LOG_ERROR("[WebRTCSession] Failed to set remote offer");
            if (callback) {
                callback(false, SDPMessage(), "Failed to set remote offer");
            }
            return;
        }

        // Create answer after remote description is set
        // This will be triggered by on_offer_set_for_answer callback

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] create_answer failed: {}", e.what());
        if (callback) {
            callback(false, SDPMessage(), std::string("Exception: ") + e.what());
        }
    }
}

bool WebRTCSession::set_remote_description(const SDPMessage &remote_sdp) {
    try {
        LOG_INFO("[WebRTCSession] Setting remote description...");

        // Parse SDP text
        GstSDPMessage *sdp_msg;
        if (gst_sdp_message_new(&sdp_msg) != GST_SDP_OK) {
            LOG_ERROR("[WebRTCSession] Failed to create SDP message");
            return false;
        }

        if (gst_sdp_message_parse_buffer(
                (const guint8*)remote_sdp.sdp_text.c_str(),
                remote_sdp.sdp_text.length(),
                sdp_msg) != GST_SDP_OK) {
            LOG_ERROR("[WebRTCSession] Failed to parse SDP");
            gst_sdp_message_free(sdp_msg);
            return false;
        }

        // NEW APPROACH: For answering, let webrtcbin auto-create transceiver from offer
        // This ensures proper PT mapping and receive pipeline setup
        // We'll set codec-preferences AFTER set-remote-description completes
        if (!is_outgoing_ && remote_sdp.type == SDPMessage::Type::OFFER) {
            LOG_INFO("[WebRTCSession] Answerer mode: parsing codec from offer for later use...");

            // Parse audio codec from offer and store for use in on_offer_set_for_answer
            offer_codec_caps_ = parse_audio_codec_from_offer(sdp_msg);
            if (!offer_codec_caps_) {
                LOG_ERROR("[WebRTCSession] Failed to parse audio codec from offer");
                gst_sdp_message_free(sdp_msg);
                return false;
            }

            gchar *caps_str = gst_caps_to_string(offer_codec_caps_);
            LOG_INFO("[WebRTCSession] ✓ Parsed codec from offer: {}", caps_str);
            g_free(caps_str);
        }
        // For offerer mode receiving answer: parse negotiated payload/channels
        else if (is_outgoing_ && remote_sdp.type == SDPMessage::Type::ANSWER) {
            LOG_INFO("[WebRTCSession] Offerer mode: parsing negotiated codec from answer...");

            const GstSDPMedia *audio_media = nullptr;
            for (guint i = 0; i < gst_sdp_message_medias_len(sdp_msg); i++) {
                const GstSDPMedia *media = gst_sdp_message_get_media(sdp_msg, i);
                if (strcmp(gst_sdp_media_get_media(media), "audio") == 0) {
                    audio_media = media;
                    break;
                }
            }

            if (audio_media && gst_sdp_media_formats_len(audio_media) > 0) {
                const char *payload_str = gst_sdp_media_get_format(audio_media, 0);
                negotiated_payload_ = atoi(payload_str);

                for (guint i = 0; i < gst_sdp_media_attributes_len(audio_media); i++) {
                    const GstSDPAttribute *attr = gst_sdp_media_get_attribute(audio_media, i);
                    if (strcmp(attr->key, "rtpmap") == 0) {
                        int attr_payload;
                        char codec_name[32];
                        int rate, channels = 0;

                        if (sscanf(attr->value, "%d %31[^/]/%d/%d", &attr_payload, codec_name, &rate, &channels) >= 3) {
                            if (attr_payload == negotiated_payload_) {
                                negotiated_channels_ = channels;
                                LOG_INFO("[WebRTCSession] ✓ Parsed answer: payload={}, {}/{}/{}",
                                         negotiated_payload_, codec_name, rate, channels);
                                break;
                            }
                        } else if (sscanf(attr->value, "%d %31[^/]/%d", &attr_payload, codec_name, &rate) == 3) {
                            if (attr_payload == negotiated_payload_) {
                                negotiated_channels_ = 1;
                                LOG_INFO("[WebRTCSession] ✓ Parsed answer: payload={}, {}/{} (mono)",
                                         negotiated_payload_, codec_name, rate);
                                break;
                            }
                        }
                    }
                }

                // TODO: For offerer mode, we may need to reconfigure the audio pipeline
                // if the negotiated values differ from what we initially set up.
                // For now, log a warning if there's a mismatch.
                LOG_INFO("[WebRTCSession] Note: Offerer audio pipeline already created with payload=97");
                LOG_INFO("[WebRTCSession] Negotiated payload={}, channels={}", negotiated_payload_, negotiated_channels_);
            }
        }

        // Create WebRTC session description
        GstWebRTCSDPType sdp_type = (remote_sdp.type == SDPMessage::Type::OFFER) ?
            GST_WEBRTC_SDP_TYPE_OFFER : GST_WEBRTC_SDP_TYPE_ANSWER;

        GstWebRTCSessionDescription *desc = gst_webrtc_session_description_new(
            sdp_type, sdp_msg);

        if (!desc) {
            LOG_ERROR("[WebRTCSession] Failed to create session description");
            return false;
        }

        // Set remote description with promise
        // CRITICAL: Do NOT interrupt/unref promise immediately after emitting!
        // The promise callback may execute asynchronously, and we'd cause use-after-free.
        // GStreamer will unref the promise when done.
        GstPromise *promise;
        if (!is_outgoing_ && remote_sdp.type == SDPMessage::Type::OFFER) {
            // Answerer receiving offer - need to create answer afterward
            promise = gst_promise_new_with_change_func(
                on_offer_set_for_answer_static, this, nullptr);
        } else {
            // Offerer receiving answer - just set it (no callback needed)
            promise = gst_promise_new();
        }

        g_signal_emit_by_name(webrtc_, "set-remote-description", desc, promise);

        // FIXED: Don't interrupt/unref here - GStreamer owns the promise now
        // Old buggy code was:
        //   gst_promise_interrupt(promise);  // ❌ BAD!
        //   gst_promise_unref(promise);      // ❌ BAD!

        // For offerer receiving answer: manually connect receive pipeline
        // webrtcbin creates receive pads synchronously during set-remote-description,
        // but pad-added signal may not fire (or fires before we're ready).
        // Solution: Enumerate existing src pads and connect them manually.
        if (is_outgoing_ && remote_sdp.type == SDPMessage::Type::ANSWER) {
            LOG_INFO("[WebRTCSession] Offerer mode: Enumerating receive pads...");

            // Give webrtcbin a moment to finish setting up pads
            g_usleep(10000);  // 10ms delay

            GstIterator *it = gst_element_iterate_src_pads(webrtc_);
            if (it) {
                GValue item = G_VALUE_INIT;
                gboolean done = FALSE;
                int pad_count = 0;

                while (!done) {
                    switch (gst_iterator_next(it, &item)) {
                        case GST_ITERATOR_OK: {
                            GstPad *pad = GST_PAD(g_value_get_object(&item));
                            gchar *pad_name = gst_pad_get_name(pad);

                            // Only process src pads (receive pads have "src_" prefix)
                            if (g_str_has_prefix(pad_name, "src_")) {
                                LOG_INFO("[WebRTCSession] Found receive pad: {}", pad_name);
                                pad_count++;

                                // Call the existing on_incoming_stream handler
                                on_incoming_stream(pad);
                            }

                            g_free(pad_name);
                            g_value_reset(&item);
                            break;
                        }
                        case GST_ITERATOR_RESYNC:
                            gst_iterator_resync(it);
                            break;
                        case GST_ITERATOR_ERROR:
                            LOG_ERROR("[WebRTCSession] Error iterating pads");
                            done = TRUE;
                            break;
                        case GST_ITERATOR_DONE:
                            done = TRUE;
                            break;
                    }
                }

                g_value_unset(&item);
                gst_iterator_free(it);

                LOG_INFO("[WebRTCSession] Offerer: Connected {} receive pad(s)", pad_count);
            } else {
                LOG_WARN("[WebRTCSession] Failed to create pad iterator");
            }
        }

        LOG_INFO("[WebRTCSession] Remote description set");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] set_remote_description failed: {}", e.what());
        return false;
    }
}

// ============================================================================
// ICE Operations
// ============================================================================

void WebRTCSession::set_ice_candidate_callback(ICECandidateCallback callback) {
    ice_callback_ = callback;
}

bool WebRTCSession::add_remote_ice_candidate(const ICECandidate &candidate) {
    try {
        LOG_TRACE("[WebRTCSession] Adding remote ICE candidate: mline={}", candidate.sdp_mline_index);

        // Collect remote candidate for stats reporting
        CollectedCandidate cand;
        if (parse_ice_candidate(candidate.candidate, cand)) {
            // Set proper ID based on GStreamer format
            std::string component = std::to_string(candidate.sdp_mline_index + 1);
            cand.id = "ice-candidate-remote_" + component + "_" + cand.ip + "_" + std::to_string(cand.port);

            std::lock_guard<std::mutex> lock(candidates_mutex_);
            collected_remote_candidates_.push_back(cand);
            LOG_DEBUG("[WebRTCSession] Collected remote candidate: {} (type={})", cand.ip + ":" + std::to_string(cand.port), cand.type);
        }

        g_signal_emit_by_name(webrtc_, "add-ice-candidate",
                             candidate.sdp_mline_index,
                             candidate.candidate.c_str());

        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] add_remote_ice_candidate failed: {}", e.what());
        return false;
    }
}

// ============================================================================
// State Callbacks
// ============================================================================

void WebRTCSession::set_state_callback(StateCallback callback) {
    state_callback_ = callback;
}

void WebRTCSession::set_stats_callback(StatsCallback callback) {
    stats_callback_ = callback;
    // TODO: Start g_timeout_add timer when callback is set
    // For now, just store the callback
}

// ============================================================================
// Audio Control
// ============================================================================

bool WebRTCSession::set_mute(bool muted) {
    try {
        is_muted_ = muted;

        if (volume_) {
            // Mute by setting volume to 0 on the volume element
            g_object_set(volume_, "volume", muted ? 0.0 : 1.0, nullptr);
            LOG_INFO("[WebRTCSession] Audio {}", muted ? "muted" : "unmuted");
        }

        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] set_mute failed: {}", e.what());
        return false;
    }
}

// ============================================================================
// Statistics
// ============================================================================

MediaSession::Stats WebRTCSession::get_stats() const {
    Stats stats;

    try {
        if (!webrtc_) {
            return stats;
        }

        // Get ICE states from webrtcbin properties FIRST (parse_stats needs these)
        GstWebRTCICEConnectionState ice_conn_state;
        GstWebRTCICEGatheringState ice_gather_state;
        g_object_get(webrtc_,
                    "ice-connection-state", &ice_conn_state,
                    "ice-gathering-state", &ice_gather_state,
                    nullptr);

        // Convert enums to strings
        const char *ice_conn_names[] = {"new", "checking", "connected", "completed", "failed", "disconnected", "closed"};
        const char *ice_gather_names[] = {"new", "gathering", "complete"};

        if (ice_conn_state < 7) {
            stats.ice_connection_state = ice_conn_names[ice_conn_state];
        }
        if (ice_gather_state < 3) {
            stats.ice_gathering_state = ice_gather_names[ice_gather_state];
        }

        // Get stats synchronously using get-stats action
        GstPromise *promise = gst_promise_new();
        g_signal_emit_by_name(webrtc_, "get-stats", nullptr, promise);

        // Wait for promise (blocking, but stats should be fast)
        GstPromiseResult result = gst_promise_wait(promise);

        if (result == GST_PROMISE_RESULT_REPLIED) {
            const GstStructure *reply = gst_promise_get_reply(promise);
            if (reply) {
                parse_stats(reply, stats);
            }
        }

        gst_promise_unref(promise);

        // Calculate bandwidth based on deltas
        auto now = std::chrono::steady_clock::now();
        if (last_stats_time_.time_since_epoch().count() > 0) {
            auto delta_time = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - last_stats_time_).count();

            if (delta_time > 0) {
                int64_t delta_bytes_sent = stats.bytes_sent - last_bytes_sent_;
                int64_t delta_bytes_received = stats.bytes_received - last_bytes_received_;
                int64_t delta_bytes_total = delta_bytes_sent + delta_bytes_received;

                // Convert to Kbps: (bytes * 8 bits/byte) / (ms / 1000) / 1000 = Kbps
                stats.bandwidth_kbps = (delta_bytes_total * 8) / delta_time;
            }
        }

        // Update last sample
        last_stats_time_ = now;
        last_bytes_sent_ = stats.bytes_sent;
        last_bytes_received_ = stats.bytes_received;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] get_stats failed: {}", e.what());
    }

    return stats;
}

// ============================================================================
// Helper Methods - Pipeline Creation
// ============================================================================

bool WebRTCSession::create_pipeline() {
    try {
        LOG_DEBUG("[WebRTCSession] Creating pipeline (webrtcbin only, audio will be added after offer processing)...");

        // Create pipeline
        std::string pipeline_name = "call-pipeline-" + config_.session_id;
        pipeline_ = gst_pipeline_new(pipeline_name.c_str());
        if (!pipeline_) {
            LOG_ERROR("[WebRTCSession] Failed to create pipeline '{}'", pipeline_name);
            return false;
        }

        // Create webrtcbin element
        webrtc_ = gst_element_factory_make("webrtcbin", "webrtc");
        if (!webrtc_) {
            LOG_ERROR("[WebRTCSession] Failed to create webrtcbin element - is gst-plugins-bad installed?");
            gst_object_unref(pipeline_);
            pipeline_ = nullptr;
            return false;
        }

        // Add webrtcbin to pipeline
        gst_bin_add(GST_BIN(pipeline_), webrtc_);

        // Create webrtcechoprobe element upfront so webrtcdsp can find it
        // It will be linked when incoming stream arrives (on_incoming_stream)
        echoprobe_ = gst_element_factory_make("webrtcechoprobe", "webrtcechoprobe0");
        if (!echoprobe_) {
            LOG_ERROR("[WebRTCSession] Failed to create webrtcechoprobe element - is gst-plugins-bad installed?");
            gst_object_unref(pipeline_);
            pipeline_ = nullptr;
            webrtc_ = nullptr;
            return false;
        }

        // Add echoprobe to pipeline but don't link yet (will be linked in on_incoming_stream)
        gst_bin_add(GST_BIN(pipeline_), echoprobe_);
        LOG_INFO("[WebRTCSession] ✓ Created webrtcechoprobe0 (will be linked when incoming stream arrives)");

        // Signals will be connected later in connect_signals() (called from initialize())
        // to avoid duplicate connections

        // Setup bus watch for errors and state changes
        GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(pipeline_));
        if (bus) {
            gst_bus_add_watch(bus, bus_message_handler_static, this);
            gst_object_unref(bus);
            LOG_DEBUG("[WebRTCSession] Bus watch added");
        }

        LOG_INFO("[WebRTCSession] Pipeline created successfully (webrtcbin only)");
        LOG_INFO("[WebRTCSession] Audio source will be added dynamically after offer processing");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] create_pipeline exception: {}", e.what());
        return false;
    }
}

// ============================================================================
// Audio Pipeline Setup - ANSWERER (Incoming Calls)
// ============================================================================

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

// ============================================================================
// Audio Pipeline Setup - OFFERER (Outgoing Calls)
// ============================================================================

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

// ============================================================================
// Helper Methods - Configuration
// ============================================================================

bool WebRTCSession::configure_webrtcbin() {
    try {
        LOG_DEBUG("[WebRTCSession] Configuring webrtcbin...");

        // Set bundle policy to max-bundle (required for modern clients)
        g_object_set(webrtc_, "bundle-policy", GST_WEBRTC_BUNDLE_POLICY_MAX_BUNDLE, nullptr);

        // Set ICE transport policy
        if (config_.relay_only) {
            g_object_set(webrtc_, "ice-transport-policy",
                        GST_WEBRTC_ICE_TRANSPORT_POLICY_RELAY, nullptr);
            LOG_INFO("[WebRTCSession] ICE policy: RELAY only");
        } else {
            g_object_set(webrtc_, "ice-transport-policy",
                        GST_WEBRTC_ICE_TRANSPORT_POLICY_ALL, nullptr);
            LOG_INFO("[WebRTCSession] ICE policy: ALL");
        }

        // Set STUN server
        if (!config_.stun_server.empty()) {
            g_object_set(webrtc_, "stun-server", config_.stun_server.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] STUN server: {}", config_.stun_server);
        }

        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] configure_webrtcbin exception: {}", e.what());
        return false;
    }
}

bool WebRTCSession::configure_proxy() {
    try {
        if (config_.proxy_host.empty() || config_.proxy_port == 0) {
            return true;  // No proxy configured
        }

        LOG_DEBUG("[WebRTCSession] Configuring proxy...");

        if (config_.proxy_type == "HTTP") {
            // Use webrtcbin's http-proxy property (GStreamer 1.22+)
            std::string proxy_url = "http://";

            if (!config_.proxy_username.empty()) {
                proxy_url += config_.proxy_username;
                if (!config_.proxy_password.empty()) {
                    proxy_url += ":" + config_.proxy_password;
                }
                proxy_url += "@";
            }

            proxy_url += config_.proxy_host + ":" + std::to_string(config_.proxy_port);

            g_object_set(webrtc_, "http-proxy", proxy_url.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] HTTP proxy configured: {}:{}", config_.proxy_host, config_.proxy_port);

        } else if (config_.proxy_type == "SOCKS5") {
            // Access NiceAgent directly for SOCKS5 support
            GObject *webrtc_ice = nullptr;
            GObject *nice_agent = nullptr;

            // First get the GstWebRTCICE object
            g_object_get(webrtc_, "ice-agent", &webrtc_ice, nullptr);

            if (!webrtc_ice) {
                LOG_ERROR("[WebRTCSession] Failed to get ice-agent for SOCKS5 proxy");
                return false;
            }

            // Then get the actual NiceAgent from GstWebRTCNice
            g_object_get(webrtc_ice, "agent", &nice_agent, nullptr);

            if (!nice_agent) {
                LOG_ERROR("[WebRTCSession] Failed to get NiceAgent for SOCKS5 proxy");
                g_object_unref(webrtc_ice);
                return false;
            }

            // Set SOCKS5 proxy on the NiceAgent
            // NiceProxyType: NICE_PROXY_TYPE_SOCKS5 = 1
            g_object_set(nice_agent,
                "proxy-type", 1,  // NICE_PROXY_TYPE_SOCKS5
                "proxy-ip", config_.proxy_host.c_str(),
                "proxy-port", static_cast<guint>(config_.proxy_port),
                nullptr);

            if (!config_.proxy_username.empty()) {
                g_object_set(nice_agent,
                    "proxy-username", config_.proxy_username.c_str(),
                    "proxy-password", config_.proxy_password.c_str(),
                    nullptr);
            }

            LOG_INFO("[WebRTCSession] SOCKS5 proxy configured: {}:{}", config_.proxy_host, config_.proxy_port);

            g_object_unref(nice_agent);
            g_object_unref(webrtc_ice);

        } else {
            LOG_ERROR("[WebRTCSession] Unknown proxy type: {}", config_.proxy_type);
            return false;
        }

        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] configure_proxy exception: {}", e.what());
        return false;
    }
}

bool WebRTCSession::add_turn_servers() {
    try {
        if (config_.turn_servers.empty()) {
            return true;  // No TURN servers to add
        }

        LOG_DEBUG("[WebRTCSession] Adding TURN servers...");

        for (const auto &turn_uri : config_.turn_servers) {
            gboolean success = FALSE;
            g_signal_emit_by_name(webrtc_, "add-turn-server", turn_uri.c_str(), &success);

            if (success) {
                LOG_INFO("[WebRTCSession] Added TURN server: {}", turn_uri);
            } else {
                LOG_ERROR("[WebRTCSession] Failed to add TURN server: {}", turn_uri);
            }
        }

        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] add_turn_servers exception: {}", e.what());
        return false;
    }
}

// ============================================================================
// Signal Connection
// ============================================================================

void WebRTCSession::connect_signals() {
    try {
        LOG_DEBUG("[WebRTCSession] Connecting signals...");

        g_signal_connect(webrtc_, "on-negotiation-needed",
                        G_CALLBACK(on_negotiation_needed_static), this);

        g_signal_connect(webrtc_, "on-ice-candidate",
                        G_CALLBACK(on_ice_candidate_static), this);

        g_signal_connect(webrtc_, "pad-added",
                        G_CALLBACK(on_incoming_stream_static), this);

        g_signal_connect(webrtc_, "notify::ice-connection-state",
                        G_CALLBACK(on_ice_connection_state_static), this);

        g_signal_connect(webrtc_, "notify::ice-gathering-state",
                        G_CALLBACK(on_ice_gathering_state_static), this);

        g_signal_connect(webrtc_, "notify::signaling-state",
                        G_CALLBACK(on_signaling_state_static), this);

        LOG_DEBUG("[WebRTCSession] Signals connected");

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] connect_signals exception: {}", e.what());
    }
}

// ============================================================================
// Static Bus Message Handler
// ============================================================================

gboolean WebRTCSession::bus_message_handler_static(GstBus *bus, GstMessage *msg, gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    return self->bus_message_handler(bus, msg);
}

// ============================================================================
// Static Signal Handlers (dispatch to instance methods)
// ============================================================================

void WebRTCSession::on_negotiation_needed_static(GstElement *webrtc, gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_negotiation_needed();
}

void WebRTCSession::on_offer_created_static(GstPromise *promise, gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_offer_created(promise);
}

void WebRTCSession::on_answer_created_static(GstPromise *promise, gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_answer_created(promise);
}

void WebRTCSession::on_ice_candidate_static(GstElement *webrtc, guint mlineindex,
                                            gchar *candidate, gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_ice_candidate(mlineindex, candidate);
}

void WebRTCSession::on_ice_connection_state_static(GstElement *webrtc, GParamSpec *pspec,
                                                   gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_ice_connection_state();
}

void WebRTCSession::on_ice_gathering_state_static(GstElement *webrtc, GParamSpec *pspec,
                                                  gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_ice_gathering_state();
}

void WebRTCSession::on_signaling_state_static(GstElement *webrtc, GParamSpec *pspec,
                                             gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_signaling_state();
}

void WebRTCSession::on_incoming_stream_static(GstElement *webrtc, GstPad *pad,
                                              gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_incoming_stream(pad);
}

void WebRTCSession::on_offer_set_for_answer_static(GstPromise *promise, gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    self->on_offer_set_for_answer();
}

// ============================================================================
// Instance Bus Message Handler
// ============================================================================

gboolean WebRTCSession::bus_message_handler(GstBus *bus, GstMessage *msg) {
    try {
        switch (GST_MESSAGE_TYPE(msg)) {
            case GST_MESSAGE_ERROR: {
                GError *err = nullptr;
                gchar *debug_info = nullptr;
                gst_message_parse_error(msg, &err, &debug_info);

                // Log error with source element name
                const gchar *src_name = GST_MESSAGE_SRC_NAME(msg);
                LOG_ERROR("[WebRTCSession] GStreamer ERROR from {}: {} (debug: {})",
                         src_name ? src_name : "unknown",
                         err ? err->message : "no message",
                         debug_info ? debug_info : "no debug info");

                // TODO: Propagate to Python via ErrorEvent
                // This requires event_queue access (not currently available in WebRTCSession)
                // Will be implemented when error event types are added to proto

                g_error_free(err);
                g_free(debug_info);
                break;
            }

            case GST_MESSAGE_WARNING: {
                GError *warn = nullptr;
                gchar *debug_info = nullptr;
                gst_message_parse_warning(msg, &warn, &debug_info);

                const gchar *src_name = GST_MESSAGE_SRC_NAME(msg);
                LOG_WARN("[WebRTCSession] GStreamer WARNING from {}: {} (debug: {})",
                         src_name ? src_name : "unknown",
                         warn ? warn->message : "no message",
                         debug_info ? debug_info : "no debug info");

                g_error_free(warn);
                g_free(debug_info);
                break;
            }

            case GST_MESSAGE_EOS: {
                LOG_INFO("[WebRTCSession] GStreamer EOS (end-of-stream)");
                break;
            }

            case GST_MESSAGE_STATE_CHANGED: {
                // Only log pipeline state changes (too verbose for all elements)
                if (GST_MESSAGE_SRC(msg) == GST_OBJECT(pipeline_)) {
                    GstState old_state, new_state, pending_state;
                    gst_message_parse_state_changed(msg, &old_state, &new_state, &pending_state);

                    const gchar *old_str = gst_element_state_get_name(old_state);
                    const gchar *new_str = gst_element_state_get_name(new_state);

                    LOG_DEBUG("[WebRTCSession] Pipeline state changed: {} → {}",
                             old_str, new_str);
                }
                break;
            }

            default:
                // Ignore other message types
                break;
        }

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] bus_message_handler exception: {}", e.what());
    }

    return TRUE;  // Continue receiving messages
}

// ============================================================================
// Instance Signal Handlers
// ============================================================================

void WebRTCSession::on_negotiation_needed() {
    LOG_INFO("[WebRTCSession] on_negotiation_needed");
    // This will be handled by explicit create_offer call
}

void WebRTCSession::on_offer_created(GstPromise *promise) {
    try {
        LOG_INFO("[WebRTCSession] on_offer_created");

        // FIXED: Don't use g_assert (can be compiled out in release builds)
        // Use proper error handling instead
        GstPromiseResult result = gst_promise_wait(promise);
        if (result != GST_PROMISE_RESULT_REPLIED) {
            LOG_ERROR("[WebRTCSession] Promise did not reply: {}", static_cast<int>(result));
            gst_promise_unref(promise);
            if (sdp_callback_) {
                sdp_callback_(false, SDPMessage(), "Promise failed to reply");
            }
            return;
        }

        const GstStructure *reply = gst_promise_get_reply(promise);
        GstWebRTCSessionDescription *offer = nullptr;
        gst_structure_get(reply, "offer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &offer, nullptr);
        gst_promise_unref(promise);

        if (!offer) {
            LOG_ERROR("[WebRTCSession] Failed to get offer from promise");
            if (sdp_callback_) {
                sdp_callback_(false, SDPMessage(), "Failed to create offer");
            }
            return;
        }

        // Set local description
        // FIXED: Don't interrupt/unref after emitting - GStreamer owns the promise
        GstPromise *local_promise = gst_promise_new();
        g_signal_emit_by_name(webrtc_, "set-local-description", offer, local_promise);
        // Promise is now owned by GStreamer - don't touch it!

        // Extract mid values from our offer for ICE candidate routing
        // This is only needed for offerer role - ICE candidates we generate need to know
        // which media section they belong to (via sdpMid field)
        media_mid_map_ = extract_mid_mapping(offer->sdp);
        if (!media_mid_map_.empty()) {
            LOG_INFO("[WebRTCSession] Extracted {} mid value(s) from offer", media_mid_map_.size());
        } else {
            LOG_WARN("[WebRTCSession] No mid values found in offer - ICE candidates may fail routing");
        }

        // Convert SDP to text
        gchar *sdp_text = gst_sdp_message_as_text(offer->sdp);
        std::string sdp_str(sdp_text);
        g_free(sdp_text);

        LOG_INFO("[WebRTCSession] Offer SDP created ({} bytes)", sdp_str.length());

        // Call user callback
        if (sdp_callback_) {
            SDPMessage sdp_msg(SDPMessage::Type::OFFER, sdp_str);
            sdp_callback_(true, sdp_msg, "");
        }

        gst_webrtc_session_description_free(offer);

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_offer_created exception: {}", e.what());
        if (sdp_callback_) {
            sdp_callback_(false, SDPMessage(), std::string("Exception: ") + e.what());
        }
    }
}

void WebRTCSession::on_answer_created(GstPromise *promise) {
    try {
        LOG_INFO("[WebRTCSession] on_answer_created");

        // FIXED: Don't use g_assert (can be compiled out in release builds)
        // Use proper error handling instead
        GstPromiseResult result = gst_promise_wait(promise);
        if (result != GST_PROMISE_RESULT_REPLIED) {
            LOG_ERROR("[WebRTCSession] Promise did not reply: {}", static_cast<int>(result));
            gst_promise_unref(promise);
            if (sdp_callback_) {
                sdp_callback_(false, SDPMessage(), "Promise failed to reply");
            }
            return;
        }

        const GstStructure *reply = gst_promise_get_reply(promise);
        GstWebRTCSessionDescription *answer = nullptr;
        gst_structure_get(reply, "answer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &answer, nullptr);
        gst_promise_unref(promise);

        if (!answer) {
            LOG_ERROR("[WebRTCSession] Failed to get answer from promise");
            if (sdp_callback_) {
                sdp_callback_(false, SDPMessage(), "Failed to create answer");
            }
            return;
        }

        // Set local description
        // FIXED: Don't interrupt/unref after emitting - GStreamer owns the promise
        GstPromise *local_promise = gst_promise_new();
        g_signal_emit_by_name(webrtc_, "set-local-description", answer, local_promise);
        // Promise is now owned by GStreamer - don't touch it!

        // Extract mid values from our answer for ICE candidate routing
        // This is needed for answerer role - ICE candidates we generate need to know
        // which media section they belong to (via sdpMid field)
        media_mid_map_ = extract_mid_mapping(answer->sdp);
        if (!media_mid_map_.empty()) {
            LOG_INFO("[WebRTCSession] Extracted {} mid value(s) from answer", media_mid_map_.size());
        } else {
            LOG_WARN("[WebRTCSession] No mid values found in answer - ICE candidates may fail routing");
        }

        // Convert SDP to text
        gchar *sdp_text = gst_sdp_message_as_text(answer->sdp);
        std::string sdp_str(sdp_text);

        LOG_INFO("[WebRTCSession] Answer SDP created ({} bytes)", sdp_str.length());
        LOG_DEBUG("[WebRTCSession] Answer SDP:\n{}", sdp_str);

        g_free(sdp_text);

        // Parse answer SDP to extract negotiated payload and channels for audio pipeline
        // This is CRITICAL - we must use the payload/channels that were actually negotiated
        const GstSDPMedia *audio_media = nullptr;
        for (guint i = 0; i < gst_sdp_message_medias_len(answer->sdp); i++) {
            const GstSDPMedia *media = gst_sdp_message_get_media(answer->sdp, i);
            if (strcmp(gst_sdp_media_get_media(media), "audio") == 0) {
                audio_media = media;
                break;
            }
        }

        if (audio_media && gst_sdp_media_formats_len(audio_media) > 0) {
            // Get the first (negotiated) payload type
            const char *payload_str = gst_sdp_media_get_format(audio_media, 0);
            negotiated_payload_ = atoi(payload_str);

            // Find rtpmap for this payload to get channels
            for (guint i = 0; i < gst_sdp_media_attributes_len(audio_media); i++) {
                const GstSDPAttribute *attr = gst_sdp_media_get_attribute(audio_media, i);
                if (strcmp(attr->key, "rtpmap") == 0) {
                    int attr_payload;
                    char codec_name[32];
                    int rate, channels = 0;

                    // Parse "111 opus/48000/2"
                    if (sscanf(attr->value, "%d %31[^/]/%d/%d", &attr_payload, codec_name, &rate, &channels) >= 3) {
                        if (attr_payload == negotiated_payload_) {
                            negotiated_channels_ = channels;
                            LOG_INFO("[WebRTCSession] ✓ Parsed negotiated codec: payload={}, {}/{}/{}",
                                     negotiated_payload_, codec_name, rate, channels);
                            break;
                        }
                    }
                    // Try without channels (default to mono)
                    else if (sscanf(attr->value, "%d %31[^/]/%d", &attr_payload, codec_name, &rate) == 3) {
                        if (attr_payload == negotiated_payload_) {
                            negotiated_channels_ = 1;
                            LOG_INFO("[WebRTCSession] ✓ Parsed negotiated codec: payload={}, {}/{} (mono)",
                                     negotiated_payload_, codec_name, rate);
                            break;
                        }
                    }
                }
            }
        } else {
            LOG_WARN("[WebRTCSession] Could not parse negotiated payload from answer, using defaults");
        }

        // For answerers: create audio pipeline using the pad we created earlier
        if (!is_outgoing_) {
            LOG_INFO("[WebRTCSession] Answerer mode: Creating audio source pipeline...");

            if (!negotiated_pad_) {
                LOG_ERROR("[WebRTCSession] No negotiated pad available!");
                gst_webrtc_session_description_free(answer);
                if (sdp_callback_) {
                    sdp_callback_(false, SDPMessage(), "No negotiated pad");
                }
                return;
            }

            // Create audio source pipeline using stored pad (answerer mode)
            if (!setup_answerer_audio_pipeline()) {
                LOG_ERROR("[WebRTCSession] Failed to create answerer audio source pipeline!");
                gst_webrtc_session_description_free(answer);
                if (sdp_callback_) {
                    sdp_callback_(false, SDPMessage(), "Failed to create audio pipeline");
                }
                return;
            }
        }

        // Call user callback
        if (sdp_callback_) {
            SDPMessage sdp_msg(SDPMessage::Type::ANSWER, sdp_str);
            sdp_callback_(true, sdp_msg, "");
        }

        gst_webrtc_session_description_free(answer);

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_answer_created exception: {}", e.what());
        if (sdp_callback_) {
            sdp_callback_(false, SDPMessage(), std::string("Exception: ") + e.what());
        }
    }
}

void WebRTCSession::on_offer_set_for_answer() {
    try {
        LOG_INFO("[WebRTCSession] Offer set, requesting pad and setting SENDRECV...");

        // CORRECT APPROACH (like FIFO test):
        // 1. Request a pad - this creates transceiver with proper mline
        // 2. Set that transceiver's direction to SENDRECV
        // 3. Create answer - webrtcbin reuses our transceiver

        // Request a sink pad - this creates a transceiver
        GstPad *webrtc_sink = gst_element_request_pad_simple(webrtc_, "sink_%u");
        if (!webrtc_sink) {
            LOG_ERROR("[WebRTCSession] Failed to request sink pad!");
            if (sdp_callback_) {
                sdp_callback_(false, SDPMessage(), "Failed to request sink pad");
            }
            return;
        }

        gchar *pad_name = gst_pad_get_name(webrtc_sink);
        LOG_INFO("[WebRTCSession] ✓ Requested pad: {}", pad_name);
        g_free(pad_name);

        // Get the transceiver for this pad
        GValue val = G_VALUE_INIT;
        g_object_get_property(G_OBJECT(webrtc_sink), "transceiver", &val);
        GstWebRTCRTPTransceiver *trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));

        if (!trans) {
            LOG_ERROR("[WebRTCSession] No transceiver associated with pad!");
            gst_object_unref(webrtc_sink);
            g_value_unset(&val);
            if (sdp_callback_) {
                sdp_callback_(false, SDPMessage(), "No transceiver found");
            }
            return;
        }

        // Set direction to SENDRECV
        g_object_set(trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
        LOG_INFO("[WebRTCSession] ✓ Set transceiver direction to SENDRECV");

        // Set codec-preferences if we have them
        if (offer_codec_caps_) {
            g_object_set(trans, "codec-preferences", offer_codec_caps_, nullptr);
            gchar *caps_str = gst_caps_to_string(offer_codec_caps_);
            LOG_INFO("[WebRTCSession] ✓ Set codec-preferences: {}", caps_str);
            g_free(caps_str);
            gst_caps_unref(offer_codec_caps_);
            offer_codec_caps_ = nullptr;
        }

        g_value_unset(&val);

        // Store this pad for audio pipeline creation AFTER answer
        negotiated_pad_ = webrtc_sink;
        LOG_INFO("[WebRTCSession] ✓ Stored pad for audio pipeline");

        // Create the answer - webrtcbin will reuse our transceiver
        LOG_INFO("[WebRTCSession] Creating answer...");
        GstPromise *promise = gst_promise_new_with_change_func(
            on_answer_created_static, this, nullptr);

        g_signal_emit_by_name(webrtc_, "create-answer", nullptr, promise);

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_offer_set_for_answer exception: {}", e.what());
        if (sdp_callback_) {
            sdp_callback_(false, SDPMessage(), std::string("Exception: ") + e.what());
        }
    }
}

void WebRTCSession::on_ice_candidate(guint mlineindex, const char *candidate) {
    try {
        // Candidate filtering for privacy (relay-only mode)
        if (config_.relay_only) {
            // In relay-only mode, only send relay candidates to prevent IP leaks
            if (strstr(candidate, "typ host") != nullptr ||
                strstr(candidate, "typ srflx") != nullptr) {
                // PRIVACY: Don't log IP addresses! Only log candidate type.
                LOG_DEBUG("[WebRTCSession] Filtering non-relay candidate (relay-only mode): type={}",
                         extract_candidate_type(candidate));
                return;  // Skip this candidate
            }
        }

        // PRIVACY: Don't log full candidate string (contains IP addresses)
        // Only log mline index and candidate type
        LOG_TRACE("[WebRTCSession] ICE candidate: mline={} type={}",
                 mlineindex, extract_candidate_type(candidate));

        // Collect candidate for stats reporting (before filtering for privacy)
        CollectedCandidate cand;
        if (parse_ice_candidate(candidate, cand)) {
            // Set proper ID based on GStreamer format
            std::string component = std::to_string(mlineindex + 1);  // Component is typically mlineindex + 1
            cand.id = "ice-candidate-local_" + component + "_" + cand.ip + "_" + std::to_string(cand.port);

            std::lock_guard<std::mutex> lock(candidates_mutex_);
            collected_local_candidates_.push_back(cand);
            LOG_DEBUG("[WebRTCSession] Collected local candidate: {} (type={})", cand.ip + ":" + std::to_string(cand.port), cand.type);
        }

        if (ice_callback_) {
            ICECandidate ice_cand(candidate, mlineindex);

            // Populate sdpMid from our extracted mid mapping
            // This is critical for Jingle transport-info content name matching
            auto it = media_mid_map_.find(mlineindex);
            if (it != media_mid_map_.end()) {
                ice_cand.sdp_mid = it->second;
                LOG_TRACE("[WebRTCSession] ICE candidate sdpMid={} (from mapping)", it->second);
            } else {
                // This should not happen for valid offers - log warning
                LOG_WARN("[WebRTCSession] No mid found for mlineindex={}, sdpMid will be empty",
                        mlineindex);
            }

            ice_callback_(ice_cand);
        }

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_ice_candidate exception: {}", e.what());
    }
}

void WebRTCSession::on_ice_connection_state() {
    try {
        GstWebRTCICEConnectionState ice_state;
        g_object_get(webrtc_, "ice-connection-state", &ice_state, nullptr);

        const char *state_str = "";
        ConnectionState mapped_state = ConnectionState::NEW;

        switch (ice_state) {
            case GST_WEBRTC_ICE_CONNECTION_STATE_NEW:
                state_str = "NEW";
                mapped_state = ConnectionState::NEW;
                break;
            case GST_WEBRTC_ICE_CONNECTION_STATE_CHECKING:
                state_str = "CHECKING";
                mapped_state = ConnectionState::CHECKING;
                break;
            case GST_WEBRTC_ICE_CONNECTION_STATE_CONNECTED:
                state_str = "CONNECTED";
                mapped_state = ConnectionState::CONNECTED;
                break;
            case GST_WEBRTC_ICE_CONNECTION_STATE_COMPLETED:
                state_str = "COMPLETED";
                mapped_state = ConnectionState::COMPLETED;
                break;
            case GST_WEBRTC_ICE_CONNECTION_STATE_FAILED:
                state_str = "FAILED";
                mapped_state = ConnectionState::FAILED;
                break;
            case GST_WEBRTC_ICE_CONNECTION_STATE_DISCONNECTED:
                state_str = "DISCONNECTED";
                mapped_state = ConnectionState::DISCONNECTED;
                break;
            case GST_WEBRTC_ICE_CONNECTION_STATE_CLOSED:
                state_str = "CLOSED";
                mapped_state = ConnectionState::CLOSED;
                break;
        }

        LOG_INFO("[WebRTCSession] ICE connection state: {}", state_str);

        if (state_callback_) {
            state_callback_(mapped_state);
        }

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_ice_connection_state exception: {}", e.what());
    }
}

void WebRTCSession::on_ice_gathering_state() {
    try {
        GstWebRTCICEGatheringState gathering_state;
        g_object_get(webrtc_, "ice-gathering-state", &gathering_state, nullptr);

        const char *state_str = "";
        switch (gathering_state) {
            case GST_WEBRTC_ICE_GATHERING_STATE_NEW:
                state_str = "NEW";
                break;
            case GST_WEBRTC_ICE_GATHERING_STATE_GATHERING:
                state_str = "GATHERING";
                break;
            case GST_WEBRTC_ICE_GATHERING_STATE_COMPLETE:
                state_str = "COMPLETE";
                break;
        }

        LOG_TRACE("[WebRTCSession] ICE gathering state: {}", state_str);

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_ice_gathering_state exception: {}", e.what());
    }
}

void WebRTCSession::on_signaling_state() {
    try {
        GstWebRTCSignalingState state;
        g_object_get(webrtc_, "signaling-state", &state, nullptr);

        const char *state_str = "UNKNOWN";
        switch (state) {
            case GST_WEBRTC_SIGNALING_STATE_STABLE:
                state_str = "STABLE";
                break;
            case GST_WEBRTC_SIGNALING_STATE_CLOSED:
                state_str = "CLOSED";
                break;
            case GST_WEBRTC_SIGNALING_STATE_HAVE_LOCAL_OFFER:
                state_str = "HAVE_LOCAL_OFFER";
                break;
            case GST_WEBRTC_SIGNALING_STATE_HAVE_REMOTE_OFFER:
                state_str = "HAVE_REMOTE_OFFER";
                break;
            case GST_WEBRTC_SIGNALING_STATE_HAVE_LOCAL_PRANSWER:
                state_str = "HAVE_LOCAL_PRANSWER";
                break;
            case GST_WEBRTC_SIGNALING_STATE_HAVE_REMOTE_PRANSWER:
                state_str = "HAVE_REMOTE_PRANSWER";
                break;
        }

        LOG_INFO("[WebRTCSession] Signaling state: {}", state_str);

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_signaling_state exception: {}", e.what());
    }
}

void WebRTCSession::on_incoming_stream(GstPad *pad) {
    try {
        LOG_DEBUG("[WebRTCSession] Incoming stream on pad: {}", GST_PAD_NAME(pad));

        // Only handle src pads (incoming media)
        if (GST_PAD_DIRECTION(pad) != GST_PAD_SRC) {
            return;
        }

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

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_incoming_stream exception: {}", e.what());
    }
}

// ============================================================================
// Stats Parsing
// ============================================================================

void WebRTCSession::parse_stats(const GstStructure *stats_struct, Stats &stats) const {
    try {
        // Helper structs for two-pass parsing
        struct CandidatePairInfo {
            std::string id;
            std::string local_candidate_id;
            std::string remote_candidate_id;
            bool selected;
            int rtt_ms;
        };

        struct CandidateInfo {
            std::string id;
            std::string type;
            std::string ip;
            int port;
        };

        struct ParseContext {
            Stats *stats;
            std::string selected_pair_id;  // From TRANSPORT
            std::vector<CandidatePairInfo> candidate_pairs;
            std::vector<CandidateInfo> local_candidates;
            std::vector<CandidateInfo> remote_candidates;
        };

        ParseContext ctx = { &stats, "", {}, {}, {} };

        // PASS 1: Collect all stats data
        gst_structure_foreach(stats_struct,
            [](GQuark field_id, const GValue *value, gpointer user_data) -> gboolean {
                ParseContext *ctx = static_cast<ParseContext*>(user_data);

                const gchar *field_name = g_quark_to_string(field_id);

                if (!GST_VALUE_HOLDS_STRUCTURE(value)) {
                    LOG_DEBUG("[WebRTCSession] parse_stats: Field {} is not a structure", field_name);
                    return TRUE;  // Continue
                }

                const GstStructure *stat = gst_value_get_structure(value);

                // Get type enum value
                GstWebRTCStatsType type_enum = GST_WEBRTC_STATS_CODEC;  // Default
                if (!gst_structure_get_enum(stat, "type",
                    g_type_from_name("GstWebRTCStatsType"), (gint*)&type_enum)) {
                    return TRUE;  // Skip entries without type
                }

                // Collect stats data by type
                if (type_enum == GST_WEBRTC_STATS_TRANSPORT) {
                    // Selected candidate pair ID
                    const gchar *selected_pair = gst_structure_get_string(stat, "selected-candidate-pair-id");
                    if (selected_pair) {
                        ctx->selected_pair_id = selected_pair;
                    }

                } else if (type_enum == GST_WEBRTC_STATS_CANDIDATE_PAIR) {
                    // Collect candidate pair info
                    CandidatePairInfo pair;
                    const gchar *pair_id = gst_structure_get_string(stat, "id");
                    const gchar *local_id = gst_structure_get_string(stat, "local-candidate-id");
                    const gchar *remote_id = gst_structure_get_string(stat, "remote-candidate-id");
                    gboolean selected = FALSE;
                    gst_structure_get_boolean(stat, "selected", &selected);
                    gdouble rtt = 0.0;
                    gst_structure_get_double(stat, "round-trip-time", &rtt);

                    if (pair_id) pair.id = pair_id;
                    if (local_id) pair.local_candidate_id = local_id;
                    if (remote_id) pair.remote_candidate_id = remote_id;
                    pair.selected = selected;
                    pair.rtt_ms = static_cast<int>(rtt * 1000);

                    ctx->candidate_pairs.push_back(pair);

                } else if (type_enum == GST_WEBRTC_STATS_LOCAL_CANDIDATE) {
                    // Collect local candidate info
                    CandidateInfo candidate;
                    const gchar *candidate_id = gst_structure_get_string(stat, "id");
                    const gchar *candidate_type = gst_structure_get_string(stat, "candidate-type");
                    const gchar *ip = gst_structure_get_string(stat, "address");
                    if (!ip) ip = gst_structure_get_string(stat, "ip");
                    guint port = 0;
                    gst_structure_get_uint(stat, "port", &port);

                    if (candidate_id) candidate.id = candidate_id;
                    if (candidate_type) candidate.type = candidate_type;
                    if (ip) candidate.ip = ip;
                    candidate.port = port;

                    if (!candidate.ip.empty()) {
                        ctx->local_candidates.push_back(candidate);
                    }

                } else if (type_enum == GST_WEBRTC_STATS_REMOTE_CANDIDATE) {
                    // Collect remote candidate info
                    CandidateInfo candidate;
                    const gchar *candidate_type = gst_structure_get_string(stat, "candidate-type");
                    const gchar *ip = gst_structure_get_string(stat, "address");
                    if (!ip) ip = gst_structure_get_string(stat, "ip");
                    guint port = 0;
                    gst_structure_get_uint(stat, "port", &port);

                    if (candidate_type) candidate.type = candidate_type;
                    if (ip) candidate.ip = ip;
                    candidate.port = port;

                    if (!candidate.ip.empty()) {
                        ctx->remote_candidates.push_back(candidate);
                    }

                } else if (type_enum == GST_WEBRTC_STATS_OUTBOUND_RTP) {
                    // Outgoing RTP stream - bytes sent and packets sent
                    guint64 bytes_sent = 0;
                    guint packets_sent = 0;
                    gst_structure_get_uint64(stat, "bytes-sent", &bytes_sent);
                    gst_structure_get_uint(stat, "packets-sent", &packets_sent);

                    LOG_TRACE("[WebRTCSession] parse_stats: OUTBOUND_RTP bytes_sent={}, packets_sent={}",
                             bytes_sent, packets_sent);

                    ctx->stats->bytes_sent += bytes_sent;  // Accumulate in case of multiple streams

                } else if (type_enum == GST_WEBRTC_STATS_INBOUND_RTP) {
                    // Incoming RTP stream - bytes received, packets, loss, jitter
                    guint64 bytes_received = 0;
                    guint packets_lost = 0, packets_received = 0;

                    gst_structure_get_uint64(stat, "bytes-received", &bytes_received);
                    gst_structure_get_uint(stat, "packets-lost", &packets_lost);
                    gst_structure_get_uint(stat, "packets-received", &packets_received);

                    LOG_TRACE("[WebRTCSession] parse_stats: INBOUND_RTP bytes_received={}, packets_received={}, packets_lost={}",
                             bytes_received, packets_received, packets_lost);

                    ctx->stats->bytes_received += bytes_received;  // Accumulate in case of multiple streams

                    // Packet loss percentage
                    if (packets_received > 0) {
                        ctx->stats->packet_loss_pct =
                            (100.0 * packets_lost) / (packets_lost + packets_received);
                    }

                    // Jitter (in seconds, convert to ms)
                    gdouble jitter = 0.0;
                    if (gst_structure_get_double(stat, "jitter", &jitter)) {
                        ctx->stats->jitter_ms = static_cast<int>(jitter * 1000);
                    }
                }

                return TRUE;  // Continue iteration
            },
            &ctx);

        // PASS 2: Process collected data
        LOG_DEBUG("[WebRTCSession] parse_stats: Collected {} candidate pairs from stats", ctx.candidate_pairs.size());

        // Find the selected candidate pair from stats
        std::string selected_local_id, selected_remote_id;
        int rtt_ms = 0;

        for (const auto& pair : ctx.candidate_pairs) {
            LOG_DEBUG("[WebRTCSession] parse_stats: Checking pair id='{}', selected={}, local_id='{}', remote_id='{}'",
                     pair.id, pair.selected, pair.local_candidate_id, pair.remote_candidate_id);

            bool is_selected = pair.selected ||
                             (!ctx.selected_pair_id.empty() && pair.id == ctx.selected_pair_id);
            if (is_selected) {
                selected_local_id = pair.local_candidate_id;
                selected_remote_id = pair.remote_candidate_id;
                rtt_ms = pair.rtt_ms;
                LOG_INFO("[WebRTCSession] parse_stats: Found selected pair! local_id='{}', remote_id='{}', rtt={}ms",
                        selected_local_id, selected_remote_id, rtt_ms);
                break;
            }
        }

        // Use our collected candidates (complete list) instead of stats candidates
        std::lock_guard<std::mutex> lock(candidates_mutex_);

        LOG_DEBUG("[WebRTCSession] parse_stats: Using {} collected local candidates, {} collected remote candidates",
                 collected_local_candidates_.size(), collected_remote_candidates_.size());

        // Process collected local candidates and determine connection type
        for (const auto& candidate : collected_local_candidates_) {
            // Add to display list
            std::string formatted = candidate.ip + ":" + std::to_string(candidate.port) +
                                  " (" + candidate.type + ")";
            stats.local_candidates.push_back(formatted);

            LOG_DEBUG("[WebRTCSession] parse_stats: Local candidate id='{}', type='{}', ip='{}:{}', selected_local_id='{}'",
                     candidate.id, candidate.type, candidate.ip, candidate.port, selected_local_id);

            // Check if this is the selected candidate
            if (!selected_local_id.empty() && candidate.id == selected_local_id) {
                LOG_INFO("[WebRTCSession] parse_stats: MATCH! This is the selected local candidate: {} ({})",
                        candidate.ip, candidate.type);
                if (candidate.type == "host") {
                    stats.connection_type = "P2P (direct)";
                } else if (candidate.type == "srflx") {
                    stats.connection_type = "P2P (srflx - NAT hole-punching)";
                } else if (candidate.type == "relay") {
                    stats.connection_type = "TURN relay (" + candidate.ip + ")";
                }
                stats.rtt_ms = rtt_ms;
            }
        }

        // Process collected remote candidates
        for (const auto& candidate : collected_remote_candidates_) {
            std::string formatted = candidate.ip + ":" + std::to_string(candidate.port) +
                                  " (" + candidate.type + ")";
            stats.remote_candidates.push_back(formatted);
        }

        // Fallback: If we didn't find a match in collected candidates, try to extract from candidate ID
        if (stats.connection_type.empty() && !selected_local_id.empty()) {
            LOG_WARN("[WebRTCSession] parse_stats: No match found for selected candidate ID: '{}', trying fallback", selected_local_id);

            // Try to parse the ID format: ice-candidate-local_1_89.238.78.51_57096
            std::vector<std::string> id_parts;
            std::istringstream id_iss(selected_local_id);
            std::string id_part;
            while (std::getline(id_iss, id_part, '_')) {
                id_parts.push_back(id_part);
            }

            if (id_parts.size() >= 4) {
                std::string ip = id_parts[2];
                // Try to match by IP in our collected candidates
                for (const auto& candidate : collected_local_candidates_) {
                    if (candidate.ip == ip) {
                        LOG_INFO("[WebRTCSession] parse_stats: Fallback match by IP: {} ({})", ip, candidate.type);
                        if (candidate.type == "host") {
                            stats.connection_type = "P2P (direct)";
                        } else if (candidate.type == "srflx") {
                            stats.connection_type = "P2P (srflx - NAT hole-punching)";
                        } else if (candidate.type == "relay") {
                            stats.connection_type = "TURN relay (" + candidate.ip + ")";
                        }
                        stats.rtt_ms = rtt_ms;
                        break;
                    }
                }
            }

            // Still no match? Generic fallback
            if (stats.connection_type.empty()) {
                LOG_WARN("[WebRTCSession] parse_stats: Could not determine connection type, using generic");
                stats.connection_type = "Connected via ICE";
            }
        }

        // Set connection state based on ICE state
        if (stats.ice_connection_state == "completed" || stats.ice_connection_state == "connected") {
            stats.connection_state = "connected";
        } else if (stats.ice_connection_state == "checking") {
            stats.connection_state = "connecting";
        } else if (stats.ice_connection_state == "failed") {
            stats.connection_state = "failed";
        } else if (stats.ice_connection_state == "disconnected") {
            stats.connection_state = "disconnected";
        } else if (stats.ice_connection_state == "closed") {
            stats.connection_state = "closed";
        } else {
            stats.connection_state = "new";
        }

        // Default connection type if not determined
        if (stats.connection_type.empty()) {
            stats.connection_type = "--";
        }

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] parse_stats exception: {}", e.what());
    }
}

// ============================================================================
// ICE Candidate Parsing
// ============================================================================

bool WebRTCSession::parse_ice_candidate(const std::string& candidate_str, CollectedCandidate& out) {
    try {
        // ICE candidate format (RFC 5245):
        // candidate:<foundation> <component> <protocol> <priority> <ip> <port> typ <type> [...]
        // Example: "candidate:1 1 UDP 2015363327 192.168.0.118 35211 typ host"

        out.candidate_str = candidate_str;

        // Split by spaces
        std::vector<std::string> parts;
        std::istringstream iss(candidate_str);
        std::string part;
        while (iss >> part) {
            parts.push_back(part);
        }

        if (parts.size() < 8) {
            LOG_DEBUG("[WebRTCSession] parse_ice_candidate: Not enough parts: {}", parts.size());
            return false;
        }

        // Extract IP and port (positions 4 and 5 after "candidate:")
        out.ip = parts[4];
        try {
            out.port = std::stoi(parts[5]);
        } catch (...) {
            LOG_DEBUG("[WebRTCSession] parse_ice_candidate: Invalid port: {}", parts[5]);
            return false;
        }

        // Extract type (after "typ" keyword at position 6)
        if (parts[6] != "typ") {
            LOG_DEBUG("[WebRTCSession] parse_ice_candidate: Missing 'typ' keyword");
            return false;
        }
        out.type = parts[7]; // host, srflx, relay, prflx

        // Generate candidate ID (GStreamer format: ice-candidate-{local|remote}_{component}_{ip}_{port})
        // We don't know if it's local or remote yet, so we'll use the IP:port as a unique identifier
        // The actual ID will be set by the caller based on context
        std::string component = parts[1];
        out.id = "ice-candidate-unknown_" + component + "_" + out.ip + "_" + std::to_string(out.port);

        LOG_DEBUG("[WebRTCSession] parse_ice_candidate: Parsed candidate: ip={}, port={}, type={}",
                 out.ip, out.port, out.type);

        return true;

    } catch (const std::exception& e) {
        LOG_ERROR("[WebRTCSession] parse_ice_candidate exception: {}", e.what());
        return false;
    }
}

} // namespace drunk_call
