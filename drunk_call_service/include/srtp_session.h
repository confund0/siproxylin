#pragma once

#include <srtp2/srtp.h>
#include <cstdint>
#include <vector>
#include <stdexcept>
#include <mutex>

namespace drunk_call {

class SrtpError : public std::runtime_error {
public:
    explicit SrtpError(const std::string& msg) : std::runtime_error(msg) {}
};

// Thin wrapper around libsrtp2 (mimics Dino's Crypto.Srtp.Session)
// Reference: crypto-vala/src/srtp.vala
class SrtpSession {
public:
    SrtpSession();
    ~SrtpSession();

    // Delete copy/move constructors (manages srtp_t contexts)
    SrtpSession(const SrtpSession&) = delete;
    SrtpSession& operator=(const SrtpSession&) = delete;

    // Key setup (called after DTLS handshake)
    void SetEncryptionKey(const uint8_t* key, size_t key_len,
                         const uint8_t* salt, size_t salt_len);
    void SetDecryptionKey(const uint8_t* key, size_t key_len,
                         const uint8_t* salt, size_t salt_len);

    // Encryption (called by SendRtpData/SendRtcpData)
    // Returns encrypted data (input + SRTP overhead)
    std::vector<uint8_t> EncryptRtp(const uint8_t* data, size_t len);
    std::vector<uint8_t> EncryptRtcp(const uint8_t* data, size_t len);

    // Decryption (called by OnIceDataReceived)
    // Returns decrypted data (input - SRTP overhead)
    std::vector<uint8_t> DecryptRtp(const uint8_t* data, size_t len);
    std::vector<uint8_t> DecryptRtcp(const uint8_t* data, size_t len);

    bool HasEncrypt() const { return has_encrypt_; }
    bool HasDecrypt() const { return has_decrypt_; }

private:
    srtp_t encrypt_context_;
    srtp_t decrypt_context_;
    bool has_encrypt_ = false;
    bool has_decrypt_ = false;

    // Thread safety for crypto operations
    mutable std::mutex crypto_mutex_;

    static bool srtp_initialized_;
    static std::mutex init_mutex_;
    static void InitializeSrtp();
};

}  // namespace drunk_call
