#pragma once

#include <gst/sdp/gstsdpmessage.h>
#include <string>
#include <vector>
#include <optional>

namespace drunk_call {

// Helper class to parse SDP using GStreamer's built-in SDP API
// Extracts ICE credentials, DTLS fingerprint, setup attribute, etc.
class SdpParser {
public:
    struct IceCredentials {
        std::string ufrag;
        std::string pwd;
    };

    struct DtlsFingerprint {
        std::string algorithm;  // e.g., "sha-256"
        std::vector<uint8_t> value;  // binary fingerprint
    };

    struct SetupAttribute {
        enum class Role {
            ACTIVE,    // We initiate DTLS (CLIENT mode)
            PASSIVE,   // Peer initiates DTLS (SERVER mode)
            ACTPASS,   // We can do either (usually means we're SERVER)
            HOLDCONN   // Connection on hold
        };
        Role role;
    };

    SdpParser();
    ~SdpParser();

    // Delete copy/move (manages GstSDPMessage)
    SdpParser(const SdpParser&) = delete;
    SdpParser& operator=(const SdpParser&) = delete;

    // Parse SDP string
    bool Parse(const std::string& sdp);

    // Extract ICE credentials (from media level, falls back to session level)
    std::optional<IceCredentials> GetIceCredentials(int media_index = 0) const;

    // Extract DTLS fingerprint (from media level, falls back to session level)
    std::optional<DtlsFingerprint> GetDtlsFingerprint(int media_index = 0) const;

    // Extract setup attribute (from media level, falls back to session level)
    std::optional<SetupAttribute> GetSetupAttribute(int media_index = 0) const;

    // Get number of media sections
    int GetMediaCount() const;

    // Get media type (e.g., "audio", "video")
    std::string GetMediaType(int media_index) const;

    // Get raw GstSDPMessage (for advanced use)
    const GstSDPMessage* GetMessage() const { return message_; }

private:
    GstSDPMessage* message_;

    // Helper to get attribute from media or session level
    const char* GetAttributeVal(const char* key, int media_index) const;

    // Parse fingerprint string (e.g., "sha-256 AA:BB:CC:...")
    static std::optional<DtlsFingerprint> ParseFingerprint(const char* fingerprint_str);

    // Parse setup string (e.g., "active", "passive", "actpass")
    static std::optional<SetupAttribute> ParseSetup(const char* setup_str);
};

}  // namespace drunk_call
