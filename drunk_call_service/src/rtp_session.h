/**
 * RTP Session Implementation (rtpbin)
 *
 * Exotic path - uses GStreamer rtpbin element
 * Manual ICE and DTLS handling (using nice/libnice, dtls elements)
 * For cases where webrtcbin doesn't meet requirements
 *
 * TODO: Implement after webrtcbin path is stable
 *
 * See: Dino implementation for reference
 *      drunk_call_service/tmp/dino-code/
 */

#ifndef RTP_SESSION_H
#define RTP_SESSION_H

#include "media_session.h"

namespace drunk_call {

class RTPSession : public MediaSession {
public:
    RTPSession();
    ~RTPSession() override;

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
    void set_stats_callback(StatsCallback callback) override;
    void set_error_callback(ErrorCallback callback) override;

    bool set_mute(bool muted) override;
    bool is_muted() const override { return false; }  // TODO

    Stats get_stats() const override;

    GstElement* get_pipeline() const override { return nullptr; }  // TODO
    Type get_type() const override { return Type::RTP; }

private:
    // TODO: Implement
    // Will use: rtpbin, nicesrc/nicesink, dtlssrtpenc/dtlssrtpdec
};

} // namespace drunk_call

#endif // RTP_SESSION_H
