/**
 * WebRTC Session Implementation - SDP Negotiation
 *
 * SDP offer/answer creation, codec parsing, and remote description handling
 */

#include "webrtc_session.h"
#include "logger.h"
#include <gst/sdp/sdp.h>
#include <gst/webrtc/webrtc.h>
#include <cstring>
#include <sstream>

namespace drunk_call {

static std::string extract_candidate_type(const char* candidate) {
    if (!candidate) return "unknown";

    if (strstr(candidate, "typ host")) return "host";
    if (strstr(candidate, "typ srflx")) return "srflx";
    if (strstr(candidate, "typ relay")) return "relay";
    if (strstr(candidate, "typ prflx")) return "prflx";

    return "unknown";
}
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
static GstCaps* parse_video_codec_from_offer(GstSDPMessage *offer, int* out_payload = nullptr) {
    const GstSDPMedia *media = nullptr;
    std::string encoding_name;
    int clock_rate = 0;
    int payload = -1;

    // Find first video m-line
    for (guint i = 0; i < gst_sdp_message_medias_len(offer); i++) {
        media = gst_sdp_message_get_media(offer, i);
        if (strcmp(gst_sdp_media_get_media(media), "video") == 0) {
            LOG_INFO("[WebRTCSession] Found video m-line at index {}", i);
            break;
        }
        media = nullptr;
    }

    if (!media) {
        LOG_INFO("[WebRTCSession] No video m-line in offer (audio-only call)");
        return nullptr;
    }

    // Get first payload type
    if (gst_sdp_media_formats_len(media) > 0) {
        const char *payload_str = gst_sdp_media_get_format(media, 0);
        payload = atoi(payload_str);
        LOG_INFO("[WebRTCSession] First video payload type: {}", payload);
    } else {
        LOG_ERROR("[WebRTCSession] No payload formats in video m-line!");
        return nullptr;
    }

    // Find rtpmap for this payload
    for (guint i = 0; i < gst_sdp_media_attributes_len(media); i++) {
        const GstSDPAttribute *attr = gst_sdp_media_get_attribute(media, i);

        if (strcmp(attr->key, "rtpmap") == 0) {
            // Parse "96 VP8/90000" or "97 H264/90000"
            int attr_payload;
            char codec_name[32];
            int rate;

            if (sscanf(attr->value, "%d %31[^/]/%d", &attr_payload, codec_name, &rate) == 3) {
                if (attr_payload == payload) {
                    // CRITICAL: GStreamer uppercases encoding-name!
                    for (char *p = codec_name; *p; p++) *p = toupper(*p);

                    encoding_name = codec_name;
                    clock_rate = rate;

                    LOG_INFO("[WebRTCSession] Parsed video codec: {}/{}", encoding_name, clock_rate);
                    break;
                }
            }
        }
    }

    if (encoding_name.empty() || clock_rate == 0) {
        LOG_ERROR("[WebRTCSession] Could not parse rtpmap for video payload {}", payload);
        return nullptr;
    }

    // Create caps WITHOUT fixed payload (allows flexible matching)
    // CRITICAL: Include RTCP feedback capabilities for:
    // - NACK PLI: Request keyframes when packets lost (fixes pixelation on packet loss)
    // - CCM FIR: Full Intra Request for severe errors
    // - Transport-CC: Transport-wide congestion control (better bandwidth adaptation)
    GstCaps *caps = gst_caps_new_simple("application/x-rtp",
        "media", G_TYPE_STRING, "video",
        "encoding-name", G_TYPE_STRING, encoding_name.c_str(),
        "clock-rate", G_TYPE_INT, clock_rate,
        "rtcp-fb-nack-pli", G_TYPE_BOOLEAN, TRUE,      // Picture Loss Indication
        "rtcp-fb-ccm-fir", G_TYPE_BOOLEAN, TRUE,       // Full Intra Request
        "rtcp-fb-transport-cc", G_TYPE_BOOLEAN, TRUE,  // Transport-wide CC
        nullptr);

    LOG_INFO("[WebRTCSession] Created video codec-preferences caps with RTCP feedback (NACK-PLI, FIR, TWCC)");

    // Return payload type via output parameter if requested
    if (out_payload) {
        *out_payload = payload;
        LOG_INFO("[WebRTCSession] Returning video payload type: {}", payload);
    }

    return caps;
}
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
void WebRTCSession::create_offer(SDPCallback callback) {
    try {
        is_outgoing_ = true;
        sdp_callback_ = callback;

        LOG_INFO("[WebRTCSession] Creating offer...");

        // Set bundle-policy for OFFERER: BALANCED adapts to peer's preference
        g_object_set(webrtc_, "bundle-policy", GST_WEBRTC_BUNDLE_POLICY_BALANCED, nullptr);
        LOG_INFO("[WebRTCSession] OFFERER: Set bundle-policy to BALANCED");

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

        // CRITICAL: For offerers with video, create video source pipeline BEFORE create-offer
        // Only if video is enabled in config
        if (config_.enable_video_receive) {
            LOG_INFO("[WebRTCSession] Offerer mode: Creating video source pipeline...");

            if (!setup_offerer_video_pipeline()) {
                LOG_ERROR("[WebRTCSession] Failed to create offerer video source pipeline!");
                if (callback) {
                    callback(false, SDPMessage(), "Failed to create video source pipeline");
                }
                return;
            }

            LOG_INFO("[WebRTCSession] Video source pipeline created, now setting codec preferences...");

            // Set codec-preferences on video transceiver BEFORE create-offer
            // Get the transceiver for sink_1 (created by setup_offerer_video_pipeline)
            GstPad *video_sink_pad = gst_element_get_static_pad(webrtc_, "sink_1");
            if (video_sink_pad) {
                GValue val = G_VALUE_INIT;
                g_object_get_property(G_OBJECT(video_sink_pad), "transceiver", &val);
                GstWebRTCRTPTransceiver *trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));

                if (trans) {
                    // Create codec-preferences: VP8 only, payload=96
                    // CRITICAL: Include RTCP feedback for proper congestion control and error recovery
                    GstCaps *codec_prefs = gst_caps_new_simple("application/x-rtp",
                        "media", G_TYPE_STRING, "video",
                        "encoding-name", G_TYPE_STRING, "VP8",
                        "clock-rate", G_TYPE_INT, 90000,
                        "payload", G_TYPE_INT, 96,
                        "rtcp-fb-nack-pli", G_TYPE_BOOLEAN, TRUE,      // Picture Loss Indication
                        "rtcp-fb-ccm-fir", G_TYPE_BOOLEAN, TRUE,       // Full Intra Request
                        "rtcp-fb-transport-cc", G_TYPE_BOOLEAN, TRUE,  // Transport-wide CC
                        nullptr);

                    g_object_set(trans, "codec-preferences", codec_prefs, nullptr);

                    gchar *caps_str = gst_caps_to_string(codec_prefs);
                    LOG_INFO("[WebRTCSession] ✓ Set video codec-preferences for offerer with RTCP feedback: {}", caps_str);
                    g_free(caps_str);
                    gst_caps_unref(codec_prefs);
                } else {
                    LOG_WARN("[WebRTCSession] Could not get video transceiver for codec-preferences");
                }

                g_value_unset(&val);
                gst_object_unref(video_sink_pad);
            } else {
                LOG_WARN("[WebRTCSession] Could not get sink_1 pad for video codec-preferences");
            }
        } else {
            LOG_INFO("[WebRTCSession] Video disabled, skipping video pipeline setup");
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

        // Set bundle-policy for ANSWERER: MAX_COMPAT to handle Dino's separate transports
        g_object_set(webrtc_, "bundle-policy", GST_WEBRTC_BUNDLE_POLICY_MAX_COMPAT, nullptr);
        LOG_INFO("[WebRTCSession] ANSWERER: Set bundle-policy to MAX_COMPAT");

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

            // Parse video codec from offer if present (may be null for audio-only calls)
            // CRITICAL: Capture the payload type for use in setup_answerer_video_pipeline()
            offer_video_codec_caps_ = parse_video_codec_from_offer(sdp_msg, &negotiated_video_payload_);
            if (offer_video_codec_caps_) {
                gchar *video_caps_str = gst_caps_to_string(offer_video_codec_caps_);
                LOG_INFO("[WebRTCSession] ✓ Parsed video codec from offer: {}", video_caps_str);
                LOG_INFO("[WebRTCSession] ✓ Negotiated video payload type: {}", negotiated_video_payload_);
                g_free(video_caps_str);
            } else {
                LOG_INFO("[WebRTCSession] No video m-line in offer (audio-only call)");
            }

            // Determine m-line order: check if video or audio is first
            // This is critical for correct transceiver mapping in answerer mode
            video_first_mline_ = false;  // Default: audio first (Dino behavior)
            if (gst_sdp_message_medias_len(sdp_msg) > 0) {
                const GstSDPMedia *first_media = gst_sdp_message_get_media(sdp_msg, 0);
                if (strcmp(gst_sdp_media_get_media(first_media), "video") == 0) {
                    video_first_mline_ = true;
                    LOG_INFO("[WebRTCSession] ✓ M-line order: VIDEO first (Conversations style)");
                } else {
                    LOG_INFO("[WebRTCSession] ✓ M-line order: AUDIO first (Dino style)");
                }
            }
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

            // If video pad was created, also create video source pipeline
            if (negotiated_video_pad_) {
                LOG_INFO("[WebRTCSession] Answerer mode: Creating video source pipeline...");

                if (!setup_answerer_video_pipeline()) {
                    LOG_ERROR("[WebRTCSession] Failed to create answerer video source pipeline!");
                    gst_webrtc_session_description_free(answer);
                    if (sdp_callback_) {
                        sdp_callback_(false, SDPMessage(), "Failed to create video pipeline");
                    }
                    return;
                }
            } else {
                LOG_INFO("[WebRTCSession] No video pad (audio-only call)");
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
        LOG_INFO("[WebRTCSession] Offer set, requesting pads in SDP m-line order...");

        // CRITICAL: Request pads in the SAME order as m-lines appear in SDP offer
        // - Conversations: m-line 0=video, m-line 1=audio (video_first_mline_=true)
        // - Dino: m-line 0=audio, m-line 1=video (video_first_mline_=false)
        // webrtcbin assigns transceivers sequentially: first request → sink_0 → m-line 0

        if (video_first_mline_ && offer_video_codec_caps_) {
            // Conversations style: VIDEO first, AUDIO second
            LOG_INFO("[WebRTCSession] Requesting VIDEO pad first (m-line 0)...");

            // Request first pad for VIDEO (will map to m-line 0)
            GstPad *video_pad = gst_element_request_pad_simple(webrtc_, "sink_%u");
            if (!video_pad) {
                LOG_ERROR("[WebRTCSession] Failed to request video pad!");
                if (sdp_callback_) sdp_callback_(false, SDPMessage(), "Failed to request video pad");
                return;
            }

            gchar *pad_name = gst_pad_get_name(video_pad);
            LOG_INFO("[WebRTCSession] ✓ Requested VIDEO pad: {}", pad_name);
            g_free(pad_name);

            // Configure video transceiver
            GValue val = G_VALUE_INIT;
            g_object_get_property(G_OBJECT(video_pad), "transceiver", &val);
            GstWebRTCRTPTransceiver *video_trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));
            if (!video_trans) {
                LOG_ERROR("[WebRTCSession] No transceiver for video pad!");
                gst_object_unref(video_pad);
                g_value_unset(&val);
                if (sdp_callback_) sdp_callback_(false, SDPMessage(), "No video transceiver");
                return;
            }

            g_object_set(video_trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
            g_object_set(video_trans, "codec-preferences", offer_video_codec_caps_, nullptr);
            gchar *video_caps_str = gst_caps_to_string(offer_video_codec_caps_);
            LOG_INFO("[WebRTCSession] ✓ Set VIDEO transceiver: SENDRECV, codec={}", video_caps_str);
            g_free(video_caps_str);
            gst_caps_unref(offer_video_codec_caps_);
            offer_video_codec_caps_ = nullptr;
            g_value_unset(&val);

            negotiated_video_pad_ = video_pad;

            // Request second pad for AUDIO (will map to m-line 1)
            LOG_INFO("[WebRTCSession] Requesting AUDIO pad second (m-line 1)...");
            GstPad *audio_pad = gst_element_request_pad_simple(webrtc_, "sink_%u");
            if (!audio_pad) {
                LOG_ERROR("[WebRTCSession] Failed to request audio pad!");
                if (sdp_callback_) sdp_callback_(false, SDPMessage(), "Failed to request audio pad");
                return;
            }

            pad_name = gst_pad_get_name(audio_pad);
            LOG_INFO("[WebRTCSession] ✓ Requested AUDIO pad: {}", pad_name);
            g_free(pad_name);

            // Configure audio transceiver
            GValue audio_val = G_VALUE_INIT;
            g_object_get_property(G_OBJECT(audio_pad), "transceiver", &audio_val);
            GstWebRTCRTPTransceiver *audio_trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&audio_val));
            if (!audio_trans) {
                LOG_ERROR("[WebRTCSession] No transceiver for audio pad!");
                gst_object_unref(audio_pad);
                g_value_unset(&audio_val);
                if (sdp_callback_) sdp_callback_(false, SDPMessage(), "No audio transceiver");
                return;
            }

            g_object_set(audio_trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
            if (offer_codec_caps_) {
                g_object_set(audio_trans, "codec-preferences", offer_codec_caps_, nullptr);
                gchar *audio_caps_str = gst_caps_to_string(offer_codec_caps_);
                LOG_INFO("[WebRTCSession] ✓ Set AUDIO transceiver: SENDRECV, codec={}", audio_caps_str);
                g_free(audio_caps_str);
                gst_caps_unref(offer_codec_caps_);
                offer_codec_caps_ = nullptr;
            }
            g_value_unset(&audio_val);

            negotiated_pad_ = audio_pad;

        } else {
            // Dino style: AUDIO first, VIDEO second (or audio-only)
            LOG_INFO("[WebRTCSession] Requesting AUDIO pad first (m-line 0)...");

            // Request first pad for AUDIO (will map to m-line 0)
            GstPad *audio_pad = gst_element_request_pad_simple(webrtc_, "sink_%u");
            if (!audio_pad) {
                LOG_ERROR("[WebRTCSession] Failed to request audio pad!");
                if (sdp_callback_) sdp_callback_(false, SDPMessage(), "Failed to request audio pad");
                return;
            }

            gchar *pad_name = gst_pad_get_name(audio_pad);
            LOG_INFO("[WebRTCSession] ✓ Requested AUDIO pad: {}", pad_name);
            g_free(pad_name);

            // Configure audio transceiver
            GValue val = G_VALUE_INIT;
            g_object_get_property(G_OBJECT(audio_pad), "transceiver", &val);
            GstWebRTCRTPTransceiver *audio_trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));
            if (!audio_trans) {
                LOG_ERROR("[WebRTCSession] No transceiver for audio pad!");
                gst_object_unref(audio_pad);
                g_value_unset(&val);
                if (sdp_callback_) sdp_callback_(false, SDPMessage(), "No audio transceiver");
                return;
            }

            g_object_set(audio_trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
            if (offer_codec_caps_) {
                g_object_set(audio_trans, "codec-preferences", offer_codec_caps_, nullptr);
                gchar *audio_caps_str = gst_caps_to_string(offer_codec_caps_);
                LOG_INFO("[WebRTCSession] ✓ Set AUDIO transceiver: SENDRECV, codec={}", audio_caps_str);
                g_free(audio_caps_str);
                gst_caps_unref(offer_codec_caps_);
                offer_codec_caps_ = nullptr;
            }
            g_value_unset(&val);

            negotiated_pad_ = audio_pad;

            // Request second pad for VIDEO if present (will map to m-line 1)
            if (offer_video_codec_caps_) {
                LOG_INFO("[WebRTCSession] Requesting VIDEO pad second (m-line 1)...");
                GstPad *video_pad = gst_element_request_pad_simple(webrtc_, "sink_%u");
                if (!video_pad) {
                    LOG_ERROR("[WebRTCSession] Failed to request video pad!");
                    if (sdp_callback_) sdp_callback_(false, SDPMessage(), "Failed to request video pad");
                    return;
                }

                pad_name = gst_pad_get_name(video_pad);
                LOG_INFO("[WebRTCSession] ✓ Requested VIDEO pad: {}", pad_name);
                g_free(pad_name);

                // Configure video transceiver
                GValue video_val = G_VALUE_INIT;
                g_object_get_property(G_OBJECT(video_pad), "transceiver", &video_val);
                GstWebRTCRTPTransceiver *video_trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&video_val));
                if (!video_trans) {
                    LOG_ERROR("[WebRTCSession] No transceiver for video pad!");
                    gst_object_unref(video_pad);
                    g_value_unset(&video_val);
                    if (sdp_callback_) sdp_callback_(false, SDPMessage(), "No video transceiver");
                    return;
                }

                g_object_set(video_trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
                g_object_set(video_trans, "codec-preferences", offer_video_codec_caps_, nullptr);
                gchar *video_caps_str = gst_caps_to_string(offer_video_codec_caps_);
                LOG_INFO("[WebRTCSession] ✓ Set VIDEO transceiver: SENDRECV, codec={}", video_caps_str);
                g_free(video_caps_str);
                gst_caps_unref(offer_video_codec_caps_);
                offer_video_codec_caps_ = nullptr;
                g_value_unset(&video_val);

                negotiated_video_pad_ = video_pad;
            } else {
                LOG_INFO("[WebRTCSession] No video in offer (audio-only call)");
            }
        }

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

} // namespace drunk_call
