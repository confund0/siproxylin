/**
 * WebRTC Session Implementation - Statistics
 *
 * Statistics gathering and parsing for WebRTC sessions
 */

#include "webrtc_session.h"
#include "logger.h"
#include <gst/webrtc/webrtc.h>
#include <sstream>
#include <chrono>

namespace drunk_call {

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

} // namespace drunk_call
