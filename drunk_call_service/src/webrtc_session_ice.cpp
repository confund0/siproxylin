/**
 * WebRTC Session Implementation - ICE Handling
 *
 * ICE candidate gathering, filtering, and state management
 */

#include "webrtc_session.h"
#include "logger.h"
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
