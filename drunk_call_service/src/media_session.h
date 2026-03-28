/**
 * Media Session Abstraction Layer
 *
 * Purpose: Allow switching between webrtcbin (standard) and rtpbin (exotic cases)
 * Pattern: Abstract base class with polymorphic implementations
 *
 * See: docs/CALLS/START.md requirement for dual-path support
 */

#ifndef MEDIA_SESSION_H
#define MEDIA_SESSION_H

#include <string>
#include <functional>
#include <memory>
#include <gst/gst.h>

namespace drunk_call {

// Forward declarations
struct SessionConfig;
struct SDPMessage;
struct ICECandidate;

/**
 * Abstract interface for media session implementations
 *
 * Implementations:
 * - WebRTCSession: Uses webrtcbin (standard path, full WebRTC stack)
 * - RTPSession: Uses rtpbin (exotic path, manual ICE/DTLS)
 */
class MediaSession {
public:
    virtual ~MediaSession() = default;

    // Lifecycle
    virtual bool initialize(const SessionConfig &config) = 0;
    virtual bool start() = 0;  // Set pipeline to PLAYING
    virtual bool stop() = 0;   // Set to NULL and cleanup

    // SDP operations (async via callbacks)
    using SDPCallback = std::function<void(bool success, const SDPMessage &sdp, const std::string &error)>;
    virtual void create_offer(SDPCallback callback) = 0;
    virtual void create_answer(const SDPMessage &remote_offer, SDPCallback callback) = 0;
    virtual bool set_remote_description(const SDPMessage &remote_sdp) = 0;

    // ICE operations
    using ICECandidateCallback = std::function<void(const ICECandidate &candidate)>;
    virtual void set_ice_candidate_callback(ICECandidateCallback callback) = 0;
    virtual bool add_remote_ice_candidate(const ICECandidate &candidate) = 0;

    // State callbacks
    enum class ConnectionState {
        NEW, CHECKING, CONNECTED, COMPLETED, FAILED, DISCONNECTED, CLOSED
    };
    using StateCallback = std::function<void(ConnectionState state)>;
    virtual void set_state_callback(StateCallback callback) = 0;

    // Audio control
    virtual bool set_mute(bool muted) = 0;
    virtual bool is_muted() const = 0;

    // Statistics
    struct Stats {
        // Connection states
        std::string connection_state;        // "new", "connecting", "connected", "disconnected", "failed", "closed"
        std::string ice_connection_state;    // "new", "checking", "connected", "completed", "failed", "disconnected", "closed"
        std::string ice_gathering_state;     // "new", "gathering", "complete"

        // Bandwidth & traffic
        uint64_t bytes_sent;
        uint64_t bytes_received;
        int64_t bandwidth_kbps;              // Current bandwidth in Kbps

        // Quality metrics
        double packet_loss_pct;
        int rtt_ms;
        int jitter_ms;

        // ICE candidates
        std::vector<std::string> local_candidates;   // ["IP:port (type)", ...]
        std::vector<std::string> remote_candidates;  // ["IP:port (type)", ...]

        // Connection type
        std::string connection_type;         // "P2P (direct)", "P2P (srflx)", "TURN relay", etc.

        Stats() : bytes_sent(0), bytes_received(0), bandwidth_kbps(0),
                  packet_loss_pct(0.0), rtt_ms(0), jitter_ms(0) {}
    };
    virtual Stats get_stats() const = 0;

    // Stats callback (periodic bandwidth/quality stats)
    using StatsCallback = std::function<void(const Stats &stats)>;
    virtual void set_stats_callback(StatsCallback callback) = 0;

    // Pipeline access (for debugging/visualization)
    virtual GstElement* get_pipeline() const = 0;

    // Implementation type query
    enum class Type { WEBRTC, RTP };
    virtual Type get_type() const = 0;
};

/**
 * Audio device information
 */
struct AudioDevice {
    std::string id;             // Device ID (for pulsesrc/pulsesink device property)
    std::string name;           // Human-readable name
    std::string description;    // Full description
    bool is_default;            // Is this the default device
    bool is_input;              // true = microphone, false = speaker

    AudioDevice() : is_default(false), is_input(true) {}
};

/**
 * Video device information
 */
struct VideoDevice {
    std::string id;             // Device ID (for v4l2src/ksvideosrc/avfvideosrc device property)
    std::string name;           // Human-readable name
    std::string description;    // Full description
    std::string device_path;    // Device path (/dev/video0, etc.)
    bool is_default;            // Is this the default device

    VideoDevice() : is_default(false) {}
};

/**
 * Device enumeration (static methods, platform-independent)
 */
class DeviceEnumerator {
public:
    // Audio devices
    static std::vector<AudioDevice> list_audio_inputs();
    static std::vector<AudioDevice> list_audio_outputs();
    static AudioDevice get_default_input();
    static AudioDevice get_default_output();

    // Video devices
    static std::vector<VideoDevice> list_video_sources();
    static VideoDevice get_default_video_source();

private:
    // Platform-specific helpers
    static std::vector<AudioDevice> enumerate_devices(const char *classes, bool is_input);
    static std::vector<VideoDevice> enumerate_video_devices(const char *classes);
};

/**
 * Session configuration
 */
struct SessionConfig {
    std::string session_id;
    std::string peer_jid;

    // Audio devices
    std::string microphone_device;  // Empty = default
    std::string speakers_device;    // Empty = default

    // Video devices
    std::string camera_device;      // Empty = default camera
    bool enable_video_receive;      // Enable video receive pipeline
    std::string video_udp_host;     // UDP host for video streaming (e.g., "127.127.69.69")
    int video_udp_port;             // UDP port for video streaming

    // Network configuration
    bool relay_only;                // ICE transport policy
    std::string stun_server;        // "stun://host:port"
    std::vector<std::string> turn_servers;  // "turn://user:pass@host:port"

    // Audio processing (WebRTC DSP)
    bool echo_cancel;
    int echo_suppression_level;        // 0=low, 1=moderate, 2=high
    bool noise_suppression;
    int noise_suppression_level;       // 0=low, 1=moderate, 2=high, 3=very-high
    bool gain_control;

    // Proxy settings
    std::string proxy_host;      // Empty = no proxy
    int proxy_port;              // 0 = no proxy
    std::string proxy_username;  // Optional
    std::string proxy_password;  // Optional
    std::string proxy_type;      // "HTTP" or "SOCKS5"

    // Implementation selection
    MediaSession::Type preferred_type;  // Hint for factory
};

/**
 * SDP message wrapper
 */
struct SDPMessage {
    enum class Type { OFFER, ANSWER };
    Type type;
    std::string sdp_text;

    SDPMessage() : type(Type::OFFER) {}
    SDPMessage(Type t, const std::string &text) : type(t), sdp_text(text) {}
};

/**
 * ICE candidate wrapper
 */
struct ICECandidate {
    std::string candidate;      // Full candidate string
    uint32_t sdp_mline_index;   // Media line index (0 for audio)
    std::string sdp_mid;        // Media ID (optional with mlineindex)

    ICECandidate() : sdp_mline_index(0) {}
    ICECandidate(const std::string &cand, uint32_t idx)
        : candidate(cand), sdp_mline_index(idx) {}
};

/**
 * Factory for creating media sessions
 *
 * Usage:
 *   auto session = SessionFactory::create(config);
 *   session->initialize(config);
 */
class SessionFactory {
public:
    static std::unique_ptr<MediaSession> create(const SessionConfig &config);

    // Check if implementation is available
    static bool is_webrtc_available();
    static bool is_rtp_available();
};

} // namespace drunk_call

#endif // MEDIA_SESSION_H
