#include "sdp_parser.h"
#include "logger.h"
#include <cstring>
#include <sstream>
#include <algorithm>

namespace drunk_call {

SdpParser::SdpParser() : message_(nullptr) {
    gst_sdp_message_new(&message_);
}

SdpParser::~SdpParser() {
    if (message_) {
        gst_sdp_message_free(message_);
    }
}

bool SdpParser::Parse(const std::string& sdp) {
    if (!message_) {
        LOG_ERROR("SdpParser not initialized");
        return false;
    }

    // Free existing message if any
    gst_sdp_message_free(message_);
    gst_sdp_message_new(&message_);

    // Parse SDP string using GStreamer's parser
    GstSDPResult result = gst_sdp_message_parse_buffer(
        reinterpret_cast<const guint8*>(sdp.c_str()),
        sdp.length(),
        message_
    );

    if (result != GST_SDP_OK) {
        LOG_ERROR("Failed to parse SDP: {}", result);
        return false;
    }

    LOG_DEBUG("SDP parsed successfully: {} media sections", gst_sdp_message_medias_len(message_));
    return true;
}

std::optional<SdpParser::IceCredentials> SdpParser::GetIceCredentials(int media_index) const {
    if (!message_) {
        return std::nullopt;
    }

    // Try to get from media level first
    const char* ufrag = GetAttributeVal("ice-ufrag", media_index);
    const char* pwd = GetAttributeVal("ice-pwd", media_index);

    if (!ufrag || !pwd) {
        LOG_WARN("ICE credentials not found in SDP (media_index={})", media_index);
        return std::nullopt;
    }

    IceCredentials creds;
    creds.ufrag = ufrag;
    creds.pwd = pwd;

    LOG_DEBUG("Extracted ICE credentials: ufrag={}, pwd_len={}", creds.ufrag, creds.pwd.length());
    return creds;
}

std::optional<SdpParser::DtlsFingerprint> SdpParser::GetDtlsFingerprint(int media_index) const {
    if (!message_) {
        return std::nullopt;
    }

    // Try to get from media level first, then session level
    const char* fingerprint_str = GetAttributeVal("fingerprint", media_index);

    if (!fingerprint_str) {
        LOG_WARN("DTLS fingerprint not found in SDP (media_index={})", media_index);
        return std::nullopt;
    }

    auto fingerprint = ParseFingerprint(fingerprint_str);
    if (!fingerprint) {
        LOG_ERROR("Failed to parse DTLS fingerprint: {}", fingerprint_str);
        return std::nullopt;
    }

    LOG_DEBUG("Extracted DTLS fingerprint: algorithm={}, size={} bytes",
              fingerprint->algorithm, fingerprint->value.size());
    return fingerprint;
}

std::optional<SdpParser::SetupAttribute> SdpParser::GetSetupAttribute(int media_index) const {
    if (!message_) {
        return std::nullopt;
    }

    // Try to get from media level first, then session level
    const char* setup_str = GetAttributeVal("setup", media_index);

    if (!setup_str) {
        LOG_WARN("Setup attribute not found in SDP (media_index={})", media_index);
        return std::nullopt;
    }

    auto setup = ParseSetup(setup_str);
    if (!setup) {
        LOG_ERROR("Failed to parse setup attribute: {}", setup_str);
        return std::nullopt;
    }

    LOG_DEBUG("Extracted setup attribute: {}", setup_str);
    return setup;
}

int SdpParser::GetMediaCount() const {
    if (!message_) {
        return 0;
    }
    return gst_sdp_message_medias_len(message_);
}

std::string SdpParser::GetMediaType(int media_index) const {
    if (!message_) {
        return "";
    }

    const GstSDPMedia* media = gst_sdp_message_get_media(message_, media_index);
    if (!media) {
        return "";
    }

    const char* media_type = gst_sdp_media_get_media(media);
    return media_type ? media_type : "";
}

const char* SdpParser::GetAttributeVal(const char* key, int media_index) const {
    if (!message_) {
        return nullptr;
    }

    // Try media level first (if media_index >= 0)
    if (media_index >= 0 && media_index < GetMediaCount()) {
        const GstSDPMedia* media = gst_sdp_message_get_media(message_, media_index);
        if (media) {
            const char* val = gst_sdp_media_get_attribute_val(media, key);
            if (val) {
                return val;
            }
        }
    }

    // Fall back to session level
    return gst_sdp_message_get_attribute_val(message_, key);
}

std::optional<SdpParser::DtlsFingerprint> SdpParser::ParseFingerprint(const char* fingerprint_str) {
    if (!fingerprint_str) {
        return std::nullopt;
    }

    // Format: "sha-256 AA:BB:CC:DD:..."
    std::string str(fingerprint_str);
    size_t space_pos = str.find(' ');
    if (space_pos == std::string::npos) {
        return std::nullopt;
    }

    DtlsFingerprint fp;
    fp.algorithm = str.substr(0, space_pos);
    std::string hex_str = str.substr(space_pos + 1);

    // Convert "AA:BB:CC:..." to binary
    std::vector<uint8_t> bytes;
    std::istringstream hex_stream(hex_str);
    std::string byte_str;

    while (std::getline(hex_stream, byte_str, ':')) {
        if (byte_str.length() != 2) {
            return std::nullopt;
        }
        try {
            uint8_t byte = static_cast<uint8_t>(std::stoi(byte_str, nullptr, 16));
            bytes.push_back(byte);
        } catch (...) {
            return std::nullopt;
        }
    }

    if (bytes.empty()) {
        return std::nullopt;
    }

    fp.value = bytes;
    return fp;
}

std::optional<SdpParser::SetupAttribute> SdpParser::ParseSetup(const char* setup_str) {
    if (!setup_str) {
        return std::nullopt;
    }

    std::string setup(setup_str);
    std::transform(setup.begin(), setup.end(), setup.begin(), ::tolower);

    SetupAttribute attr;

    if (setup == "active") {
        attr.role = SetupAttribute::Role::ACTIVE;
    } else if (setup == "passive") {
        attr.role = SetupAttribute::Role::PASSIVE;
    } else if (setup == "actpass") {
        attr.role = SetupAttribute::Role::ACTPASS;
    } else if (setup == "holdconn") {
        attr.role = SetupAttribute::Role::HOLDCONN;
    } else {
        return std::nullopt;
    }

    return attr;
}

std::vector<SdpParser::IceCandidate> SdpParser::GetCandidates(int media_index) const {
    std::vector<IceCandidate> candidates;

    if (!message_) {
        return candidates;
    }

    // Get media section
    const GstSDPMedia* media = gst_sdp_message_get_media(message_, media_index);
    if (!media) {
        LOG_WARN("No media section at index {}", media_index);
        return candidates;
    }

    // Iterate through all attributes looking for "candidate"
    guint attr_count = gst_sdp_media_attributes_len(media);
    for (guint i = 0; i < attr_count; i++) {
        const GstSDPAttribute* attr = gst_sdp_media_get_attribute(media, i);
        if (!attr || !attr->key || !attr->value) {
            continue;
        }

        // Check if this is a candidate attribute
        if (std::strcmp(attr->key, "candidate") == 0) {
            // Parse component ID from candidate string
            // Format: "foundation component protocol priority..."
            // Example: "1 1 UDP 2015363327 192.168.0.129 40338 typ host"
            std::string cand_str(attr->value);
            std::istringstream iss(cand_str);
            std::string foundation;
            int component_id = 0;

            iss >> foundation >> component_id;

            if (component_id > 0) {
                IceCandidate candidate;
                candidate.component_id = component_id;
                candidate.candidate_str = "candidate:" + cand_str;  // Add "candidate:" prefix
                candidates.push_back(candidate);

                LOG_DEBUG("Extracted SDP candidate: component={}, cand={}",
                         component_id, candidate.candidate_str);
            }
        }
    }

    LOG_DEBUG("Extracted {} candidates from SDP media section {}", candidates.size(), media_index);
    return candidates;
}

}  // namespace drunk_call
