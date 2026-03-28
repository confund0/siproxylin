/**
 * WebRTC Session Implementation (webrtcbin)
 *
 * Standard path - uses GStreamer webrtcbin element
 * Handles ICE, DTLS, SRTP automatically
 *
 * LOGGING: Use LOG_*() macros (logger.h) for all application logging
 *          DO NOT use std::cout/std::cerr in production code
 *          See: docs/LOGGING-POLICY.md
 *
 * See: docs/CALLS/PLAN.md for full architecture
 * Reference: docs/CALLS/webrtcbin-reference.cpp for implementation patterns
 */

#ifndef WEBRTC_SESSION_H
#define WEBRTC_SESSION_H

#include "media_session.h"
#include <gst/gst.h>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <map>

namespace drunk_call {

class WebRTCSession : public MediaSession {
public:
    WebRTCSession();
    ~WebRTCSession() override;

    // MediaSession interface
    bool initialize(const SessionConfig &config) override;
    bool start() override;
    bool stop() override;

    void create_offer(SDPCallback callback) override;
    void create_answer(const SDPMessage &remote_offer, SDPCallback callback) override;
    bool set_remote_description(const SDPMessage &remote_sdp) override;

    void set_ice_candidate_callback(ICECandidateCallback callback) override;
    bool add_remote_ice_candidate(const ICECandidate &candidate) override;

    void set_state_callback(StateCallback callback) override;
    void set_stats_callback(StatsCallback callback) override;  // NEW: periodic stats updates

    bool set_mute(bool muted) override;
    bool is_muted() const override { return is_muted_; }

    Stats get_stats() const override;  // Synchronous stats (legacy)

    GstElement* get_pipeline() const override { return pipeline_; }
    Type get_type() const override { return Type::WEBRTC; }

private:
    // Pipeline elements
    GstElement *pipeline_;
    GstElement *webrtc_;
    GstElement *audio_src_;
    GstElement *audio_sink_;  // Created dynamically on pad-added
    GstElement *volume_;      // For mute functionality
    GstElement *echoprobe_;   // WebRTC echo probe for echo cancellation

    // Configuration
    SessionConfig config_;
    bool is_muted_;
    std::atomic<bool> is_outgoing_;  // true = offerer, false = answerer (thread-safe)

    // Pad management: Track the pad created during negotiation for answerer mode
    // Answerer: webrtcbin auto-creates transceiver from offer, we get its pad
    // after negotiation completes, then reuse that pad for audio pipeline
    GstPad* negotiated_pad_;  // Pad used for SDP negotiation (answerer only)
    GstCaps* offer_codec_caps_;  // Codec caps parsed from remote offer (answerer only)

    // Negotiated codec parameters from answer SDP (used to configure audio pipeline)
    int negotiated_payload_;   // RTP payload type from answer (e.g., 111)
    int negotiated_channels_;  // Audio channels from answer (e.g., 2 for stereo)

    // Media mid mapping: mline index → mid value (from SDP a=mid:)
    // Extracted from our offer SDP to populate sdpMid in ICE candidates
    // Example: {0: "audio0"} or {0: "0", 1: "video0"}
    std::map<guint, std::string> media_mid_map_;

    // Callbacks
    SDPCallback sdp_callback_;
    ICECandidateCallback ice_callback_;
    StateCallback state_callback_;
    StatsCallback stats_callback_;  // NEW: periodic stats callback

    // Synchronization for async SDP operations
    std::mutex sdp_mutex_;
    std::condition_variable sdp_ready_;
    SDPMessage local_sdp_;
    bool sdp_done_;

    // Stats monitoring
    guint stats_timer_id_;  // GLib timer source ID

    // Candidate collection (for complete stats reporting)
    struct CollectedCandidate {
        std::string id;          // Candidate ID (for nominated pair lookup)
        std::string ip;
        int port;
        std::string type;        // host, srflx, relay, prflx
        std::string candidate_str; // Full candidate string

        CollectedCandidate() : port(0) {}
    };
    mutable std::vector<CollectedCandidate> collected_local_candidates_;
    mutable std::vector<CollectedCandidate> collected_remote_candidates_;
    mutable std::mutex candidates_mutex_;  // Protect candidate vectors

    // Helper: Parse ICE candidate string to extract info
    static bool parse_ice_candidate(const std::string& candidate_str, CollectedCandidate& out);

    // GStreamer bus message handler (static)
    static gboolean bus_message_handler_static(GstBus *bus, GstMessage *msg, gpointer user_data);

    // GStreamer signal handlers (static, dispatch to instance methods)
    static void on_negotiation_needed_static(GstElement *webrtc, gpointer user_data);
    static void on_offer_created_static(GstPromise *promise, gpointer user_data);
    static void on_answer_created_static(GstPromise *promise, gpointer user_data);
    static void on_ice_candidate_static(GstElement *webrtc, guint mlineindex,
                                       gchar *candidate, gpointer user_data);
    static void on_ice_connection_state_static(GstElement *webrtc, GParamSpec *pspec,
                                              gpointer user_data);
    static void on_ice_gathering_state_static(GstElement *webrtc, GParamSpec *pspec,
                                              gpointer user_data);
    static void on_signaling_state_static(GstElement *webrtc, GParamSpec *pspec,
                                         gpointer user_data);
    static void on_incoming_stream_static(GstElement *webrtc, GstPad *pad,
                                         gpointer user_data);
    static gboolean stats_timer_callback_static(gpointer user_data);  // NEW: stats timer
    static void on_stats_promise_static(GstPromise *promise, gpointer user_data);  // NEW: stats result

    // Instance methods called by static handlers
    gboolean bus_message_handler(GstBus *bus, GstMessage *msg);
    void on_negotiation_needed();
    void on_offer_created(GstPromise *promise);
    void on_answer_created(GstPromise *promise);
    void on_ice_candidate(guint mlineindex, const char *candidate);
    void on_ice_connection_state();
    void on_ice_gathering_state();
    void on_signaling_state();
    void on_incoming_stream(GstPad *pad);
    gboolean stats_timer_callback();  // NEW: instance method for stats timer
    void on_stats_promise(GstPromise *promise);  // NEW: process stats result

    // Helper methods
    bool create_pipeline();
    bool setup_answerer_audio_pipeline();  // Incoming calls (answerer mode)
    bool setup_offerer_audio_pipeline();    // Outgoing calls (offerer mode)
    bool configure_webrtcbin();
    bool configure_proxy();
    bool add_turn_servers();
    void connect_signals();

    // Promise callback for set-remote-description before answer
    static void on_offer_set_for_answer_static(GstPromise *promise, gpointer user_data);
    void on_offer_set_for_answer();

    // Stats helpers
    static void on_stats_received_static(GstPromise *promise, gpointer user_data);
    void parse_stats(const GstStructure *stats, Stats &result) const;

    // Bandwidth tracking (for get_stats)
    mutable std::chrono::steady_clock::time_point last_stats_time_;
    mutable uint64_t last_bytes_sent_;
    mutable uint64_t last_bytes_received_;
};

} // namespace drunk_call

#endif // WEBRTC_SESSION_H
