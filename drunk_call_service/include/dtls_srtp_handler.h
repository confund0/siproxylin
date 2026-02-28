#pragma once

#include <gnutls/gnutls.h>
#include <gnutls/dtls.h>
#include <gnutls/x509.h>
#include <functional>
#include <vector>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <memory>
#include "srtp_session.h"

namespace drunk_call {

enum class DtlsMode {
    CLIENT,  // For incoming calls (setup=active)
    SERVER   // For outgoing calls (setup=actpass)
};

// DTLS-SRTP handler (based on Dino's dtls_srtp.vala)
// Manages DTLS handshake and integrates SrtpSession for encryption/decryption
class DtlsSrtpHandler {
public:
    DtlsSrtpHandler();
    ~DtlsSrtpHandler();

    // Delete copy/move constructors (manages threads and GnuTLS session)
    DtlsSrtpHandler(const DtlsSrtpHandler&) = delete;
    DtlsSrtpHandler& operator=(const DtlsSrtpHandler&) = delete;

    // Certificate generation (called in Session::Initialize)
    bool GenerateCertificate();
    std::vector<uint8_t> GetOwnFingerprint() const { return own_fingerprint_; }
    std::string GetFingerprintString() const;  // Format: "AA:BB:CC:..."

    // Peer fingerprint (from remote SDP)
    void SetPeerFingerprint(const std::vector<uint8_t>& fp) { peer_fingerprint_ = fp; }
    void SetPeerFingerprintAlgo(const std::string& algo) { peer_fp_algo_ = algo; }

    // Mode (determined by SDP offer/answer)
    void SetMode(DtlsMode mode) { mode_ = mode; }

    // Callback for sending DTLS packets over ICE (Component 1)
    using SendDataCallback = std::function<void(const uint8_t*, size_t)>;
    void SetSendDataCallback(SendDataCallback callback) { send_data_callback_ = callback; }

    // Callback when DTLS handshake completes and SRTP is ready
    using OnReadyCallback = std::function<void()>;
    void SetOnReadyCallback(OnReadyCallback callback) { on_ready_callback_ = callback; }

    // DTLS handshake (async, call after ICE connected)
    bool StartHandshake();
    void StopHandshake();
    bool IsReady() const { return ready_; }

    // Packet processing (called by Session)
    std::vector<uint8_t> ProcessIncomingData(uint8_t component_id, const uint8_t* data, size_t len);
    std::vector<uint8_t> ProcessOutgoingData(uint8_t component_id, const uint8_t* data, size_t len);

    // Feed DTLS packets from ICE (called when data[0] in [20, 64))
    void OnDtlsDataReceived(const uint8_t* data, size_t len);

private:
    // Certificate and keys
    gnutls_certificate_credentials_t cert_cred_;
    gnutls_x509_crt_t certificate_;
    gnutls_x509_privkey_t private_key_;
    std::vector<uint8_t> own_fingerprint_;

    std::vector<uint8_t> peer_fingerprint_;
    std::string peer_fp_algo_;

    DtlsMode mode_;
    std::atomic<bool> ready_{false};
    std::atomic<bool> stop_{false};

    // DTLS session
    gnutls_session_t dtls_session_;
    bool session_initialized_;

    // Buffer for DTLS pull function
    std::queue<std::vector<uint8_t>> dtls_buffer_;
    std::mutex buffer_mutex_;
    std::condition_variable buffer_cv_;

    // SRTP session (created after handshake)
    std::unique_ptr<SrtpSession> srtp_session_;

    // Callbacks
    SendDataCallback send_data_callback_;
    OnReadyCallback on_ready_callback_;

    // Handshake thread
    std::thread handshake_thread_;

    // Private methods
    bool GenerateECDSAKey();
    bool CreateSelfSignedCert();
    std::vector<uint8_t> CalculateFingerprint();
    void HandshakeThread();
    bool DoHandshake();
    bool ExtractSrtpKeys();
    bool VerifyPeerCertificate();

    // GnuTLS callbacks (static methods)
    static ssize_t PullFunction(gnutls_transport_ptr_t ptr, void* data, size_t len);
    static int PullTimeoutFunction(gnutls_transport_ptr_t ptr, unsigned int ms);
    static ssize_t PushFunction(gnutls_transport_ptr_t ptr, const void* data, size_t len);
    static int VerifyFunction(gnutls_session_t session);
};

}  // namespace drunk_call
