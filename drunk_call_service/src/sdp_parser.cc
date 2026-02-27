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

std::optional<std::string> SdpParser::GetMid(int media_index) const {
    if (!message_) {
        return std::nullopt;
    }

    // Get media section
    const GstSDPMedia* media = gst_sdp_message_get_media(message_, media_index);
    if (!media) {
        LOG_WARN("No media section at index {}", media_index);
        return std::nullopt;
    }

    // Extract mid attribute
    // Based on GStreamer webrtcbin: webrtcsdp.c:198
    const char* mid = gst_sdp_media_get_attribute_val(media, "mid");
    if (!mid || std::strlen(mid) == 0) {
        LOG_WARN("No mid attribute found in media section {}", media_index);
        return std::nullopt;
    }

    LOG_DEBUG("Extracted mid: {} (media_index={})", mid, media_index);
    return std::string(mid);
}

std::optional<std::string> SdpParser::GetProtocol(int media_index) const {
    if (!message_) {
        return std::nullopt;
    }

    // Get media section
    const GstSDPMedia* media = gst_sdp_message_get_media(message_, media_index);
    if (!media) {
        LOG_WARN("No media section at index {}", media_index);
        return std::nullopt;
    }

    // Extract protocol from m= line
    // Based on GStreamer webrtcbin: gstwebrtcbin.c:5522
    const char* proto = gst_sdp_media_get_proto(media);
    if (!proto) {
        LOG_WARN("No protocol found in media section {}", media_index);
        return std::nullopt;
    }

    LOG_DEBUG("Extracted protocol: {} (media_index={})", proto, media_index);
    return std::string(proto);
}

std::vector<SdpParser::CodecFormat> SdpParser::GetCodecFormats(int media_index) const {
    std::vector<CodecFormat> formats;

    if (!message_) {
        return formats;
    }

    // Get media section
    const GstSDPMedia* media = gst_sdp_message_get_media(message_, media_index);
    if (!media) {
        LOG_WARN("No media section at index {}", media_index);
        return formats;
    }

    // Iterate through all payload types in m= line
    // Based on GStreamer webrtcbin: utils.c:205-209
    guint formats_len = gst_sdp_media_formats_len(media);
    for (guint i = 0; i < formats_len; i++) {
        const char* format_str = gst_sdp_media_get_format(media, i);
        if (!format_str) {
            continue;
        }

        // Parse payload type
        int pt = std::atoi(format_str);

        // Use GStreamer's built-in caps parser to extract rtpmap info
        // This automatically parses "a=rtpmap:96 opus/48000/2" into structured caps
        GstCaps* caps = gst_sdp_media_get_caps_from_media(media, pt);
        if (!caps) {
            // No rtpmap for this payload type (might be static PT like 0=PCMU, 8=PCMA)
            LOG_DEBUG("No caps for payload type {} (media_index={})", pt, media_index);
            continue;
        }

        // Extract codec info from caps
        if (gst_caps_get_size(caps) > 0) {
            GstStructure* s = gst_caps_get_structure(caps, 0);

            CodecFormat codec;
            codec.payload_type = pt;

            // Extract encoding-name (codec name)
            const char* encoding_name = gst_structure_get_string(s, "encoding-name");
            if (encoding_name) {
                codec.encoding = encoding_name;
                // Convert to lowercase for name field (e.g., "OPUS" -> "opus")
                codec.name = encoding_name;
                std::transform(codec.name.begin(), codec.name.end(), codec.name.begin(), ::tolower);
            } else {
                codec.name = "";
                codec.encoding = "";
            }

            // Extract clock-rate
            int clockrate = 0;
            if (gst_structure_get_int(s, "clock-rate", &clockrate)) {
                codec.clockrate = clockrate;
            } else {
                codec.clockrate = 0;
            }

            formats.push_back(codec);

            LOG_DEBUG("Extracted codec: pt={}, name={}, encoding={}, clockrate={}",
                     codec.payload_type, codec.name, codec.encoding, codec.clockrate);
        }

        gst_caps_unref(caps);
    }

    LOG_DEBUG("Extracted {} codec formats from SDP media section {}", formats.size(), media_index);
    return formats;
}

}  // namespace drunk_call
