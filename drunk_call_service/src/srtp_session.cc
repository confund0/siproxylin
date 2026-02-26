#include "srtp_session.h"
#include "logger.h"
#include <cstring>

namespace drunk_call {

// Static members
bool SrtpSession::srtp_initialized_ = false;
std::mutex SrtpSession::init_mutex_;

void SrtpSession::InitializeSrtp() {
    std::lock_guard<std::mutex> lock(init_mutex_);
    if (!srtp_initialized_) {
        srtp_err_status_t status = srtp_init();
        if (status != srtp_err_status_ok) {
            throw SrtpError("Failed to initialize libsrtp2: " + std::to_string(status));
        }
        srtp_initialized_ = true;
        LOG_INFO("libsrtp2 initialized successfully");
    }
}

SrtpSession::SrtpSession()
    : encrypt_context_(nullptr),
      decrypt_context_(nullptr) {
    // Initialize libsrtp2 (idempotent, thread-safe)
    InitializeSrtp();

    // Create contexts
    srtp_err_status_t status;

    status = srtp_create(&encrypt_context_, nullptr);
    if (status != srtp_err_status_ok) {
        throw SrtpError("Failed to create SRTP encrypt context: " + std::to_string(status));
    }

    status = srtp_create(&decrypt_context_, nullptr);
    if (status != srtp_err_status_ok) {
        srtp_dealloc(encrypt_context_);
        throw SrtpError("Failed to create SRTP decrypt context: " + std::to_string(status));
    }

    LOG_DEBUG("SrtpSession created");
}

SrtpSession::~SrtpSession() {
    if (encrypt_context_) {
        srtp_dealloc(encrypt_context_);
    }
    if (decrypt_context_) {
        srtp_dealloc(decrypt_context_);
    }
    LOG_DEBUG("SrtpSession destroyed");
}

void SrtpSession::SetEncryptionKey(const uint8_t* key, size_t key_len,
                                   const uint8_t* salt, size_t salt_len) {
    std::lock_guard<std::mutex> lock(crypto_mutex_);

    LOG_INFO("Setting SRTP encryption key: key_len={}, salt_len={}", key_len, salt_len);

    // Create policy for SRTP_AES128_CM_HMAC_SHA1_80
    srtp_policy_t policy;
    memset(&policy, 0, sizeof(policy));

    // Set crypto policy (AES-128-CM + HMAC-SHA1-80)
    srtp_crypto_policy_set_aes_cm_128_hmac_sha1_80(&policy.rtp);
    srtp_crypto_policy_set_aes_cm_128_hmac_sha1_80(&policy.rtcp);

    // Key = master key (16 bytes) + master salt (14 bytes) = 30 bytes
    policy.key = new uint8_t[key_len + salt_len];
    memcpy(policy.key, key, key_len);
    memcpy(policy.key + key_len, salt, salt_len);

    // Apply to any outbound SSRC
    policy.ssrc.type = ssrc_any_outbound;
    policy.ssrc.value = 0;
    policy.next = nullptr;

    // Add stream to encrypt context
    srtp_err_status_t status = srtp_add_stream(encrypt_context_, &policy);
    delete[] policy.key;

    if (status != srtp_err_status_ok) {
        throw SrtpError("Failed to add encryption stream: " + std::to_string(status));
    }

    has_encrypt_ = true;
    LOG_INFO("SRTP encryption key configured successfully");
}

void SrtpSession::SetDecryptionKey(const uint8_t* key, size_t key_len,
                                   const uint8_t* salt, size_t salt_len) {
    std::lock_guard<std::mutex> lock(crypto_mutex_);

    LOG_INFO("Setting SRTP decryption key: key_len={}, salt_len={}", key_len, salt_len);

    // Create policy for SRTP_AES128_CM_HMAC_SHA1_80
    srtp_policy_t policy;
    memset(&policy, 0, sizeof(policy));

    // Set crypto policy (AES-128-CM + HMAC-SHA1-80)
    srtp_crypto_policy_set_aes_cm_128_hmac_sha1_80(&policy.rtp);
    srtp_crypto_policy_set_aes_cm_128_hmac_sha1_80(&policy.rtcp);

    // Key = master key (16 bytes) + master salt (14 bytes) = 30 bytes
    policy.key = new uint8_t[key_len + salt_len];
    memcpy(policy.key, key, key_len);
    memcpy(policy.key + key_len, salt, salt_len);

    // Apply to any inbound SSRC
    policy.ssrc.type = ssrc_any_inbound;
    policy.ssrc.value = 0;
    policy.next = nullptr;

    // Add stream to decrypt context
    srtp_err_status_t status = srtp_add_stream(decrypt_context_, &policy);
    delete[] policy.key;

    if (status != srtp_err_status_ok) {
        throw SrtpError("Failed to add decryption stream: " + std::to_string(status));
    }

    has_decrypt_ = true;
    LOG_INFO("SRTP decryption key configured successfully");
}

std::vector<uint8_t> SrtpSession::EncryptRtp(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(crypto_mutex_);

    if (!has_encrypt_) {
        throw SrtpError("Cannot encrypt: encryption key not set");
    }

    // Allocate buffer with room for SRTP overhead (10 bytes for auth tag)
    std::vector<uint8_t> buffer(len + SRTP_MAX_TRAILER_LEN);
    memcpy(buffer.data(), data, len);

    int buffer_len = len;
    srtp_err_status_t status = srtp_protect(encrypt_context_, buffer.data(), &buffer_len);

    if (status != srtp_err_status_ok) {
        throw SrtpError("SRTP encryption failed: " + std::to_string(status));
    }

    // Resize to actual encrypted size
    buffer.resize(buffer_len);
    LOG_DEBUG("Encrypted RTP: {} bytes -> {} bytes", len, buffer_len);
    return buffer;
}

std::vector<uint8_t> SrtpSession::EncryptRtcp(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(crypto_mutex_);

    if (!has_encrypt_) {
        throw SrtpError("Cannot encrypt: encryption key not set");
    }

    // Allocate buffer with room for SRTCP overhead (14 bytes for auth tag + SRTCP index)
    std::vector<uint8_t> buffer(len + SRTP_MAX_TRAILER_LEN + 4);
    memcpy(buffer.data(), data, len);

    int buffer_len = len;
    srtp_err_status_t status = srtp_protect_rtcp(encrypt_context_, buffer.data(), &buffer_len);

    if (status != srtp_err_status_ok) {
        throw SrtpError("SRTCP encryption failed: " + std::to_string(status));
    }

    // Resize to actual encrypted size
    buffer.resize(buffer_len);
    LOG_DEBUG("Encrypted RTCP: {} bytes -> {} bytes", len, buffer_len);
    return buffer;
}

std::vector<uint8_t> SrtpSession::DecryptRtp(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(crypto_mutex_);

    if (!has_decrypt_) {
        throw SrtpError("Cannot decrypt: decryption key not set");
    }

    // Copy to mutable buffer (srtp_unprotect modifies in-place)
    std::vector<uint8_t> buffer(data, data + len);
    int buffer_len = len;

    srtp_err_status_t status = srtp_unprotect(decrypt_context_, buffer.data(), &buffer_len);

    if (status == srtp_err_status_auth_fail) {
        throw SrtpError("SRTP authentication failed - packet may be tampered");
    } else if (status != srtp_err_status_ok) {
        throw SrtpError("SRTP decryption failed: " + std::to_string(status));
    }

    // Resize to actual decrypted size (remove auth tag)
    buffer.resize(buffer_len);
    LOG_DEBUG("Decrypted RTP: {} bytes -> {} bytes", len, buffer_len);
    return buffer;
}

std::vector<uint8_t> SrtpSession::DecryptRtcp(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(crypto_mutex_);

    if (!has_decrypt_) {
        throw SrtpError("Cannot decrypt: decryption key not set");
    }

    // Copy to mutable buffer (srtp_unprotect_rtcp modifies in-place)
    std::vector<uint8_t> buffer(data, data + len);
    int buffer_len = len;

    srtp_err_status_t status = srtp_unprotect_rtcp(decrypt_context_, buffer.data(), &buffer_len);

    if (status == srtp_err_status_auth_fail) {
        throw SrtpError("SRTCP authentication failed - packet may be tampered");
    } else if (status != srtp_err_status_ok) {
        throw SrtpError("SRTCP decryption failed: " + std::to_string(status));
    }

    // Resize to actual decrypted size (remove auth tag + index)
    buffer.resize(buffer_len);
    LOG_DEBUG("Decrypted RTCP: {} bytes -> {} bytes", len, buffer_len);
    return buffer;
}

}  // namespace drunk_call
