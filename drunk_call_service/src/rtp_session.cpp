/**
 * RTP Session Implementation (Stub)
 *
 * Exotic path using rtpbin for manual ICE/DTLS control
 * TODO: Implement after webrtcbin path is stable
 */

#include "rtp_session.h"
#include <iostream>

namespace drunk_call {

RTPSession::RTPSession() {
}

RTPSession::~RTPSession() {
}

bool RTPSession::initialize(const SessionConfig &config) {
    std::cerr << "[RTPSession] Not implemented yet" << std::endl;
    return false;
}

bool RTPSession::start() {
    return false;
}

bool RTPSession::stop() {
    return false;
}

void RTPSession::create_offer(SDPCallback callback) {
    if (callback) {
        callback(false, SDPMessage(), "RTPSession not implemented");
    }
}

void RTPSession::create_answer(const SDPMessage &remote_offer, SDPCallback callback) {
    if (callback) {
        callback(false, SDPMessage(), "RTPSession not implemented");
    }
}

bool RTPSession::set_remote_description(const SDPMessage &remote_sdp) {
    return false;
}

void RTPSession::set_ice_candidate_callback(ICECandidateCallback callback) {
}

bool RTPSession::add_remote_ice_candidate(const ICECandidate &candidate) {
    return false;
}

void RTPSession::set_state_callback(StateCallback callback) {
}

void RTPSession::set_stats_callback(StatsCallback callback) {
    // TODO: Implement for RTP session
}

void RTPSession::set_error_callback(ErrorCallback callback) {
}

bool RTPSession::set_mute(bool muted) {
    return false;
}

MediaSession::Stats RTPSession::get_stats() const {
    return Stats{};
}

} // namespace drunk_call
