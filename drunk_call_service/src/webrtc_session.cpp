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
#include <gst/video/videooverlay.h>
#include <stdexcept>
#include <cstring>
#include <sstream>

#ifdef _WIN32
#include <windows.h>  // For FindWindowExW, ShowWindow, SetForegroundWindow
#endif

namespace drunk_call {

// ============================================================================
// Helper Functions
// ============================================================================

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
    , video_src_(nullptr)
    , video_sink_(nullptr)
    , video_tee_(nullptr)
    , compositor_(nullptr)
    , is_muted_(false)
    , is_outgoing_(false)
    , negotiated_pad_(nullptr)
    , offer_codec_caps_(nullptr)
    , negotiated_video_pad_(nullptr)
    , offer_video_codec_caps_(nullptr)
    , video_first_mline_(false)
    , negotiated_payload_(-1)
    , negotiated_channels_(1)  // Default to mono (will be overridden by SDP negotiation)
    , negotiated_video_payload_(-1)  // Will be parsed from video offer SDP
    , sdp_done_(false)
    , stats_timer_id_(0)
#ifdef _WIN32
    , window_maximize_timer_id_(0)
    , window_maximize_attempts_(0)
#endif
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

#ifdef _WIN32
        // Cancel window maximize timer if running
        if (window_maximize_timer_id_ > 0) {
            g_source_remove(window_maximize_timer_id_);
            window_maximize_timer_id_ = 0;
        }
#endif

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

#ifdef _WIN32
        // Cancel window maximize timer if running
        if (window_maximize_timer_id_ > 0) {
            g_source_remove(window_maximize_timer_id_);
            window_maximize_timer_id_ = 0;
            LOG_DEBUG("[WebRTCSession] Cancelled window maximize timer");
        }
#endif

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

// ============================================================================
// ICE Operations
// ============================================================================

void WebRTCSession::set_ice_candidate_callback(ICECandidateCallback callback) {
    ice_callback_ = callback;
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

// ============================================================================
// Video Pipeline Setup - ANSWERER (Incoming Calls)
// ============================================================================

// ============================================================================
// Audio Pipeline Setup - OFFERER (Outgoing Calls)
// ============================================================================

// Video Pipeline Setup - OFFERER (Outgoing Calls)
// ============================================================================

// ============================================================================
// Helper Methods - Configuration
// ============================================================================

bool WebRTCSession::configure_webrtcbin() {
    try {
        LOG_DEBUG("[WebRTCSession] Configuring webrtcbin...");

        // Bundle-policy is set dynamically based on role:
        // - OFFERER (create_offer): Uses BALANCED to adapt to peer
        // - ANSWERER (create_answer): Uses MAX_COMPAT to match Dino's separate transports
        LOG_INFO("[WebRTCSession] Bundle-policy will be set based on call direction");

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
// Windows-specific Helper Functions
// ============================================================================

#ifdef _WIN32
void WebRTCSession::maximize_d3dvideosink_window() {
    // Start non-blocking timer to retry finding d3dvideosink window
    // (window is created asynchronously after prepare-window-handle message)

    // Cancel any existing timer first
    if (window_maximize_timer_id_ > 0) {
        g_source_remove(window_maximize_timer_id_);
        window_maximize_timer_id_ = 0;
    }

    // Reset attempt counter
    window_maximize_attempts_ = 0;

    // Start timer: retry every 50ms (non-blocking)
    window_maximize_timer_id_ = g_timeout_add(50, window_maximize_timer_callback_static, this);
    LOG_DEBUG("[WebRTCSession] Started window maximize timer");
}

gboolean WebRTCSession::window_maximize_timer_callback_static(gpointer user_data) {
    WebRTCSession *self = static_cast<WebRTCSession*>(user_data);
    return self->window_maximize_timer_callback();
}
#endif

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

#ifdef _WIN32
                // Fallback: Try to maximize window when d3dvideosink reaches PLAYING
                const gchar *src_name = GST_MESSAGE_SRC_NAME(msg);
                if (src_name && strcmp(src_name, "d3dvideosink0") == 0) {
                    GstState old_state, new_state, pending_state;
                    gst_message_parse_state_changed(msg, &old_state, &new_state, &pending_state);
                    if (new_state == GST_STATE_PLAYING) {
                        LOG_DEBUG("[WebRTCSession] d3dvideosink reached PLAYING, attempting maximize");
                        maximize_d3dvideosink_window();
                    }
                }
#endif
                break;
            }

            case GST_MESSAGE_ELEMENT: {
#ifdef _WIN32
                // Check if this is prepare-window-handle from video sink
                if (gst_is_video_overlay_prepare_window_handle_message(msg)) {
                    LOG_DEBUG("[WebRTCSession] Video overlay window ready, maximizing...");
                    maximize_d3dvideosink_window();
                }
#endif
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

#ifdef _WIN32
// ============================================================================
// Windows-specific Instance Methods
// ============================================================================

gboolean WebRTCSession::window_maximize_timer_callback() {
    const int max_retries = 10;

    window_maximize_attempts_++;

    // Try to find and maximize the d3dvideosink window
    HWND hwnd = FindWindowExW(nullptr, nullptr, L"GstD3DVideoSinkInternalWindow", nullptr);
    if (hwnd) {
        ShowWindow(hwnd, SW_MAXIMIZE);
        SetForegroundWindow(hwnd);
        LOG_INFO("[WebRTCSession] ✓ Maximized d3dvideosink window (attempt {})", window_maximize_attempts_);

        // Success! Cancel timer
        window_maximize_timer_id_ = 0;
        return G_SOURCE_REMOVE;  // Stop timer
    }

    // Window not found yet
    if (window_maximize_attempts_ >= max_retries) {
        LOG_WARN("[WebRTCSession] Failed to find d3dvideosink window after {} attempts", max_retries);
        window_maximize_timer_id_ = 0;
        return G_SOURCE_REMOVE;  // Stop timer
    }

    // Retry
    LOG_DEBUG("[WebRTCSession] d3dvideosink window not found yet (attempt {})", window_maximize_attempts_);
    return G_SOURCE_CONTINUE;  // Continue timer
}
#endif

// ============================================================================
// Instance Signal Handlers
// ============================================================================

void WebRTCSession::on_negotiation_needed() {
    LOG_INFO("[WebRTCSession] on_negotiation_needed");
    // This will be handled by explicit create_offer call
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

        // Determine media type from pad caps
        GstCaps *caps = gst_pad_get_current_caps(pad);
        if (!caps) {
            caps = gst_pad_get_pad_template_caps(pad);
        }

        bool is_video = false;
        if (caps) {
            GstStructure *structure = gst_caps_get_structure(caps, 0);
            const gchar *media = gst_structure_get_string(structure, "media");
            if (media && g_strcmp0(media, "video") == 0) {
                is_video = true;
                LOG_INFO("[WebRTCSession] Detected INCOMING VIDEO stream");
            } else {
                LOG_INFO("[WebRTCSession] Detected INCOMING AUDIO stream");
            }
            gst_caps_unref(caps);
        } else {
            LOG_WARN("[WebRTCSession] Could not determine media type from caps, assuming audio");
        }

        // Dispatch to appropriate handler
        if (is_video) {
            handle_incoming_video_stream(pad);
        } else {
            handle_incoming_audio_stream(pad);
        }

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] on_incoming_stream exception: {}", e.what());
    }
}

} // namespace drunk_call
