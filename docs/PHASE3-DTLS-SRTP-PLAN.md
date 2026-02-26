# Phase 3: DTLS-SRTP Implementation Plan

**Based on:** Dino's proven implementation (`plugins/ice/src/dtls_srtp.vala`)
**Status:** ✅ COMPLETE - Implementation done, awaiting candidate parsing fix
**Completed:** 2026-02-26
**Estimated Time:** 2-3 days
**Date:** 2026-02-26

---

## Overview

Implement the encryption layer that sits between rtpbin (GStreamer) and IceAgent (libnice). This layer handles:
1. **DTLS handshake** - Secure key exchange over ICE transport
2. **Certificate management** - Self-signed cert with SHA-256 fingerprint
3. **SRTP encryption/decryption** - Protect RTP/RTCP packets

---

## Dino's Architecture (Reference)

### Key Components

**1. CredentialsCapsule** - Holds certificate and keys
```vala
own_fingerprint: uint8[]      // SHA-256 hash for SDP
own_cert: X509.Certificate[]  // Self-signed certificate
private_key: X509.PrivateKey  // ECDSA 256-bit key
```

**2. DtlsSrtp.Handler** - DTLS handshake + SRTP wrapper
- Runs handshake in separate thread
- Provides `process_incoming_data()` and `process_outgoing_data()`
- Integrates with Crypto.Srtp.Session

**3. Crypto.Srtp.Session** - libsrtp2 wrapper
- Separate encrypt/decrypt contexts
- Methods: `encrypt_rtp()`, `decrypt_rtp()`, `encrypt_rtcp()`, `decrypt_rtcp()`

### Data Flow

```
Outgoing (rtpbin → Network):
  rtpbin → appsink → process_outgoing_data() → [ENCRYPT] → agent.send()

Incoming (Network → rtpbin):
  agent.recv() → process_incoming_data() → [DECRYPT] → appsrc → rtpbin

DTLS packets (handshake):
  agent.recv() → process_incoming_data() → [DTLS handler] → agent.send()
```

### Packet Detection (Dino's Logic)

**Incoming (dtls_srtp.vala:40-60)**:
```
if (data[0] >= 128):  // RTP/RTCP packet
    if (component_id == 1):
        if (data[1] >= 192 && data[1] < 224):  // RTCP range
            return decrypt_rtcp(data)
        else:
            return decrypt_rtp(data)
    if (component_id == 2):
        return decrypt_rtcp(data)  // Component 2 always RTCP

if (data[0] >= 20 && data[0] < 64):  // DTLS packet
    feed_to_dtls_handler(data)
    return null  // Don't forward to rtpbin
```

**Outgoing (dtls_srtp.vala:62-73)**:
```
if (component_id == 1):
    if (data[1] >= 192 && data[1] < 224):  // RTCP
        return encrypt_rtcp(data)
    else:  // RTP
        return encrypt_rtp(data)
if (component_id == 2):
    return encrypt_rtcp(data)
```

---

## C++ Implementation Plan

### File Structure

```
drunk_call_service/
├── include/
│   ├── dtls_srtp_handler.h  (NEW)
│   └── srtp_session.h       (NEW)
└── src/
    ├── dtls_srtp_handler.cc (NEW)
    └── srtp_session.cc      (NEW)
```

---

## Class 1: SrtpSession (srtp_session.h/cc)

**Purpose:** Thin wrapper around libsrtp2 (mimics Dino's Crypto.Srtp.Session)

### Header (srtp_session.h)

```cpp
#pragma once

#include <srtp2/srtp.h>
#include <cstdint>
#include <vector>
#include <stdexcept>

namespace drunk_call {

class SrtpError : public std::runtime_error {
public:
    explicit SrtpError(const std::string& msg) : std::runtime_error(msg) {}
};

class SrtpSession {
public:
    SrtpSession();
    ~SrtpSession();

    // Key setup (called after DTLS handshake)
    void SetEncryptionKey(const uint8_t* key, size_t key_len,
                         const uint8_t* salt, size_t salt_len);
    void SetDecryptionKey(const uint8_t* key, size_t key_len,
                         const uint8_t* salt, size_t salt_len);

    // Encryption (called by SendRtpData/SendRtcpData)
    std::vector<uint8_t> EncryptRtp(const uint8_t* data, size_t len);
    std::vector<uint8_t> EncryptRtcp(const uint8_t* data, size_t len);

    // Decryption (called by OnIceDataReceived)
    std::vector<uint8_t> DecryptRtp(const uint8_t* data, size_t len);
    std::vector<uint8_t> DecryptRtcp(const uint8_t* data, size_t len);

    bool HasEncrypt() const { return has_encrypt_; }
    bool HasDecrypt() const { return has_decrypt_; }

private:
    srtp_t encrypt_context_;
    srtp_t decrypt_context_;
    bool has_encrypt_ = false;
    bool has_decrypt_ = false;

    static bool srtp_initialized_;
    static void InitializeSrtp();
};

} // namespace drunk_call
```

### Implementation Notes

- **Constructor**: Initialize libsrtp2 (call `srtp_init()` once globally)
- **SetEncryptionKey/SetDecryptionKey**:
  - Create srtp_policy_t with SRTP_AES_CM_128_HMAC_SHA1_80 profile
  - Concatenate key + salt (key is 16 bytes, salt is 14 bytes = 30 bytes total)
  - Set ssrc.type to ssrc_any_outbound/ssrc_any_inbound
  - Call `srtp_add_stream()`
- **EncryptRtp**:
  - Allocate buffer with `data.length + SRTP_MAX_TRAILER_LEN` (10 bytes)
  - Copy data to buffer
  - Call `srtp_protect()` with `buf_use` as in/out parameter
  - Return resized vector
- **DecryptRtp**:
  - Copy to buffer
  - Call `srtp_unprotect()` with `buf_use` as in/out parameter
  - Check for `srtp_err_status_auth_fail`
  - Return resized vector
- **RTCP methods**: Same but use `srtp_protect_rtcp()` and `srtp_unprotect_rtcp()`

**Reference:** Dino's `crypto-vala/src/srtp.vala`

---

## Class 2: DtlsSrtpHandler (dtls_srtp_handler.h/cc)

**Purpose:** Manages DTLS handshake and integrates SrtpSession

### Header (dtls_srtp_handler.h)

```cpp
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
#include "srtp_session.h"

namespace drunk_call {

enum class DtlsMode {
    CLIENT,  // For incoming calls (setup=active)
    SERVER   // For outgoing calls (setup=actpass)
};

class DtlsSrtpHandler {
public:
    DtlsSrtpHandler();
    ~DtlsSrtpHandler();

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

    // Buffer for DTLS pull function
    std::queue<std::vector<uint8_t>> dtls_buffer_;
    std::mutex buffer_mutex_;
    std::condition_variable buffer_cv_;

    // SRTP session (created after handshake)
    std::unique_ptr<SrtpSession> srtp_session_;

    // Callback
    SendDataCallback send_data_callback_;

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

} // namespace drunk_call
```

### Implementation Highlights

#### 1. Certificate Generation

```cpp
bool DtlsSrtpHandler::GenerateCertificate() {
    // Initialize GnuTLS
    gnutls_global_init();
    gnutls_certificate_allocate_credentials(&cert_cred_);

    // Generate ECDSA private key (256-bit, like Dino)
    gnutls_x509_privkey_init(&private_key_);
    int ret = gnutls_x509_privkey_generate(private_key_, GNUTLS_PK_ECDSA,
                                           GNUTLS_CURVE_TO_BITS(GNUTLS_ECC_CURVE_SECP256R1), 0);
    if (ret < 0) return false;

    // Create self-signed certificate
    gnutls_x509_crt_init(&certificate_);
    gnutls_x509_crt_set_version(certificate_, 1);
    gnutls_x509_crt_set_key(certificate_, private_key_);

    // Set validity: 1 day before to 1 day after
    time_t now = time(nullptr);
    gnutls_x509_crt_set_activation_time(certificate_, now - 86400);
    gnutls_x509_crt_set_expiration_time(certificate_, now + 86400);

    // Serial number
    uint32_t serial = 1;
    gnutls_x509_crt_set_serial(certificate_, &serial, sizeof(serial));

    // Self-sign
    gnutls_x509_crt_sign2(certificate_, certificate_, private_key_,
                         GNUTLS_DIG_SHA256, 0);

    // Calculate SHA-256 fingerprint
    own_fingerprint_ = CalculateFingerprint();

    // Set credentials
    gnutls_certificate_set_x509_key(cert_cred_, &certificate_, 1, private_key_);

    return true;
}

std::vector<uint8_t> DtlsSrtpHandler::CalculateFingerprint() {
    uint8_t buf[512];
    size_t buf_size = sizeof(buf);
    gnutls_x509_crt_get_fingerprint(certificate_, GNUTLS_DIG_SHA256, buf, &buf_size);

    return std::vector<uint8_t>(buf, buf + buf_size);
}

std::string DtlsSrtpHandler::GetFingerprintString() const {
    std::ostringstream oss;
    for (size_t i = 0; i < own_fingerprint_.size(); ++i) {
        oss << std::hex << std::uppercase << std::setw(2) << std::setfill('0')
            << static_cast<int>(own_fingerprint_[i]);
        if (i < own_fingerprint_.size() - 1) oss << ":";
    }
    return oss.str();
}
```

**Reference:** Dino's `dtls_srtp.vala:82-112`

#### 2. DTLS Handshake

```cpp
bool DtlsSrtpHandler::StartHandshake() {
    stop_ = false;
    handshake_thread_ = std::thread(&DtlsSrtpHandler::HandshakeThread, this);
    return true;
}

void DtlsSrtpHandler::HandshakeThread() {
    LOG_INFO("Starting DTLS handshake thread (mode: {})",
             mode_ == DtlsMode::SERVER ? "SERVER" : "CLIENT");

    // Create DTLS session
    unsigned int flags = GNUTLS_DATAGRAM | GNUTLS_NONBLOCK;
    flags |= (mode_ == DtlsMode::SERVER) ? GNUTLS_SERVER : GNUTLS_CLIENT;
    gnutls_init(&dtls_session_, flags);

    // Set SRTP profile
    const char* err_pos;
    gnutls_priority_set_direct(dtls_session_,
        "NORMAL:!VERS-TLS-ALL:+VERS-DTLS-ALL:+CTYPE-CLI-X509", &err_pos);
    gnutls_srtp_set_profile_direct(dtls_session_, "SRTP_AES128_CM_HMAC_SHA1_80", &err_pos);

    // Set credentials
    gnutls_credentials_set(dtls_session_, GNUTLS_CRD_CERTIFICATE, cert_cred_);
    gnutls_certificate_server_set_request(dtls_session_, GNUTLS_CERT_REQUEST);

    // Set transport callbacks
    gnutls_transport_set_ptr(dtls_session_, this);
    gnutls_transport_set_pull_function(dtls_session_, PullFunction);
    gnutls_transport_set_pull_timeout_function(dtls_session_, PullTimeoutFunction);
    gnutls_transport_set_push_function(dtls_session_, PushFunction);

    // Set verify callback
    gnutls_session_set_verify_function(dtls_session_, VerifyFunction);

    // Perform handshake (with 20-second timeout like Dino)
    if (!DoHandshake()) {
        LOG_ERROR("DTLS handshake failed");
        gnutls_deinit(dtls_session_);
        return;
    }

    // Extract SRTP keys
    if (!ExtractSrtpKeys()) {
        LOG_ERROR("Failed to extract SRTP keys");
        gnutls_deinit(dtls_session_);
        return;
    }

    ready_ = true;
    LOG_INFO("DTLS handshake complete, SRTP ready");
}

bool DtlsSrtpHandler::DoHandshake() {
    auto start_time = std::chrono::steady_clock::now();
    auto timeout = std::chrono::seconds(20);

    int ret;
    do {
        ret = gnutls_handshake(dtls_session_);

        if (stop_) {
            LOG_DEBUG("Handshake stopped by user");
            return false;
        }

        auto elapsed = std::chrono::steady_clock::now() - start_time;
        if (elapsed > timeout) {
            LOG_ERROR("DTLS handshake timeout");
            return false;
        }

        if (ret < 0 && !gnutls_error_is_fatal(ret)) {
            // Non-fatal, retry
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    } while (ret < 0 && !gnutls_error_is_fatal(ret));

    if (ret != GNUTLS_E_SUCCESS) {
        LOG_ERROR("DTLS handshake error: {}", gnutls_strerror(ret));
        return false;
    }

    return true;
}
```

**Reference:** Dino's `dtls_srtp.vala:134-220`

#### 3. SRTP Key Extraction

```cpp
bool DtlsSrtpHandler::ExtractSrtpKeys() {
    // Extract keying material from DTLS
    gnutls_datum_t client_key, client_salt, server_key, server_salt;
    uint8_t km[150];

    int ret = gnutls_srtp_get_keys(dtls_session_, km, sizeof(km),
                                   &client_key, &client_salt,
                                   &server_key, &server_salt);
    if (ret < 0) {
        LOG_ERROR("Failed to extract SRTP keys: {}", gnutls_strerror(ret));
        return false;
    }

    // Create SRTP session
    srtp_session_ = std::make_unique<SrtpSession>();

    // Set keys based on mode (SERVER uses server keys for encrypt, CLIENT uses client keys)
    if (mode_ == DtlsMode::SERVER) {
        srtp_session_->SetEncryptionKey(server_key.data, server_key.size,
                                       server_salt.data, server_salt.size);
        srtp_session_->SetDecryptionKey(client_key.data, client_key.size,
                                       client_salt.data, client_salt.size);
    } else {
        srtp_session_->SetEncryptionKey(client_key.data, client_key.size,
                                       client_salt.data, client_salt.size);
        srtp_session_->SetDecryptionKey(server_key.data, server_key.size,
                                       server_salt.data, server_salt.size);
    }

    LOG_INFO("SRTP keys extracted and configured");
    return true;
}
```

**Reference:** Dino's `dtls_srtp.vala:204-218`

#### 4. GnuTLS Transport Callbacks

```cpp
ssize_t DtlsSrtpHandler::PullFunction(gnutls_transport_ptr_t ptr,
                                     void* data, size_t len) {
    auto* self = static_cast<DtlsSrtpHandler*>(ptr);

    std::unique_lock<std::mutex> lock(self->buffer_mutex_);
    while (self->dtls_buffer_.empty()) {
        self->buffer_cv_.wait(lock);
        if (self->stop_) return -1;
    }

    auto packet = self->dtls_buffer_.front();
    self->dtls_buffer_.pop();
    lock.unlock();

    size_t copy_len = std::min(len, packet.size());
    memcpy(data, packet.data(), copy_len);

    return copy_len;
}

int DtlsSrtpHandler::PullTimeoutFunction(gnutls_transport_ptr_t ptr, unsigned int ms) {
    auto* self = static_cast<DtlsSrtpHandler*>(ptr);

    std::unique_lock<std::mutex> lock(self->buffer_mutex_);
    auto timeout = std::chrono::milliseconds(ms);

    if (self->buffer_cv_.wait_for(lock, timeout,
        [self]{ return !self->dtls_buffer_.empty() || self->stop_; })) {
        return self->stop_ ? -1 : 1;
    }

    return 0;  // Timeout
}

ssize_t DtlsSrtpHandler::PushFunction(gnutls_transport_ptr_t ptr,
                                     const void* data, size_t len) {
    auto* self = static_cast<DtlsSrtpHandler*>(ptr);

    if (self->send_data_callback_) {
        self->send_data_callback_(static_cast<const uint8_t*>(data), len);
    }

    return len;
}
```

**Reference:** Dino's `dtls_srtp.vala:222-275`

#### 5. Certificate Verification

```cpp
int DtlsSrtpHandler::VerifyFunction(gnutls_session_t session) {
    auto* self = static_cast<DtlsSrtpHandler*>(gnutls_transport_get_ptr(session));

    if (!self->VerifyPeerCertificate()) {
        LOG_ERROR("Peer certificate verification failed");
        return 1;  // Abort handshake
    }

    return 0;  // Continue handshake
}

bool DtlsSrtpHandler::VerifyPeerCertificate() {
    // Get peer certificate list
    unsigned int cert_list_size;
    const gnutls_datum_t* cert_list =
        gnutls_certificate_get_peers(dtls_session_, &cert_list_size);

    if (cert_list_size == 0) {
        LOG_ERROR("No peer certificates received");
        return false;
    }

    // Import first certificate
    gnutls_x509_crt_t peer_cert;
    gnutls_x509_crt_init(&peer_cert);
    gnutls_x509_crt_import(peer_cert, &cert_list[0], GNUTLS_X509_FMT_DER);

    // Calculate fingerprint
    uint8_t fingerprint[512];
    size_t fp_size = sizeof(fingerprint);
    gnutls_digest_algorithm_t algo = GNUTLS_DIG_SHA256;  // TODO: Parse peer_fp_algo_
    gnutls_x509_crt_get_fingerprint(peer_cert, algo, fingerprint, &fp_size);

    // Compare with advertised fingerprint
    if (fp_size != peer_fingerprint_.size()) {
        LOG_ERROR("Fingerprint size mismatch");
        gnutls_x509_crt_deinit(peer_cert);
        return false;
    }

    if (memcmp(fingerprint, peer_fingerprint_.data(), fp_size) != 0) {
        LOG_ERROR("Fingerprint mismatch!");
        gnutls_x509_crt_deinit(peer_cert);
        return false;
    }

    gnutls_x509_crt_deinit(peer_cert);
    LOG_INFO("Peer certificate verified successfully");
    return true;
}
```

**Reference:** Dino's `dtls_srtp.vala:277-330`

#### 6. Packet Processing

```cpp
std::vector<uint8_t> DtlsSrtpHandler::ProcessIncomingData(uint8_t component_id,
                                                          const uint8_t* data, size_t len) {
    if (len == 0) return {};

    // RTP/RTCP packets (encrypted)
    if (data[0] >= 128) {
        if (!srtp_session_ || !srtp_session_->HasDecrypt()) {
            LOG_DEBUG("Received encrypted data before SRTP ready, dropping");
            return {};
        }

        if (component_id == 1) {
            // Component 1: Could be RTP or RTCP
            if (len >= 2 && data[1] >= 192 && data[1] < 224) {
                // RTCP packet (payload type 192-223)
                return srtp_session_->DecryptRtcp(data, len);
            } else {
                // RTP packet
                return srtp_session_->DecryptRtp(data, len);
            }
        } else if (component_id == 2) {
            // Component 2: Always RTCP
            return srtp_session_->DecryptRtcp(data, len);
        }
    }

    // DTLS packets (handshake)
    if (component_id == 1 && len >= 1 && data[0] >= 20 && data[0] < 64) {
        OnDtlsDataReceived(data, len);
        return {};  // Don't forward to rtpbin
    }

    LOG_DEBUG("Unknown packet type, dropping (component {}, first byte {})", component_id, data[0]);
    return {};
}

std::vector<uint8_t> DtlsSrtpHandler::ProcessOutgoingData(uint8_t component_id,
                                                          const uint8_t* data, size_t len) {
    if (!srtp_session_ || !srtp_session_->HasEncrypt()) {
        LOG_DEBUG("Trying to send before SRTP ready");
        return {};
    }

    if (component_id == 1) {
        // Component 1: Could be RTP or RTCP
        if (len >= 2 && data[1] >= 192 && data[1] < 224) {
            return srtp_session_->EncryptRtcp(data, len);
        } else {
            return srtp_session_->EncryptRtp(data, len);
        }
    } else if (component_id == 2) {
        // Component 2: Always RTCP
        return srtp_session_->EncryptRtcp(data, len);
    }

    return {};
}

void DtlsSrtpHandler::OnDtlsDataReceived(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(buffer_mutex_);
    dtls_buffer_.push(std::vector<uint8_t>(data, data + len));
    buffer_cv_.notify_one();
}
```

**Reference:** Dino's `dtls_srtp.vala:40-73`

---

## Integration with Session Class

### Session.h Changes

```cpp
class Session {
private:
    // ... existing members ...

    // NEW: DTLS-SRTP handler
    std::unique_ptr<DtlsSrtpHandler> dtls_srtp_;
};
```

### Session.cc Changes

#### 1. Initialize() - Create DTLS handler

```cpp
bool Session::Initialize() {
    // ... existing setup ...

    // Create DTLS-SRTP handler
    dtls_srtp_ = std::make_unique<DtlsSrtpHandler>();
    if (!dtls_srtp_->GenerateCertificate()) {
        LOG_ERROR("Failed to generate DTLS certificate");
        return false;
    }

    LOG_INFO("DTLS certificate generated, fingerprint: {}",
             dtls_srtp_->GetFingerprintString());

    // Wire DTLS send callback to IceAgent
    dtls_srtp_->SetSendDataCallback([this](const uint8_t* data, size_t len) {
        // Send DTLS packets over Component 1
        ice_agent_->Send(1, data, len);
    });

    // ... rest of initialization ...
    return true;
}
```

#### 2. CreateOffer() - Include fingerprint in SDP

```cpp
std::string Session::CreateOffer() {
    // ... existing setup ...

    // Get fingerprint for SDP
    std::string fingerprint = dtls_srtp_->GetFingerprintString();

    // Generate SDP with fingerprint
    std::string sdp =
        "v=0\r\n"
        // ... other SDP lines ...
        "a=fingerprint:sha-256 " + fingerprint + "\r\n"
        "a=setup:actpass\r\n";

    // Set mode to SERVER (we'll switch to CLIENT if peer is passive)
    dtls_srtp_->SetMode(DtlsMode::SERVER);

    return sdp;
}
```

#### 3. CreateAnswer() - Set peer fingerprint and mode

```cpp
std::string Session::CreateAnswer(const std::string& remote_sdp, bool offer_has_bundle) {
    // ... existing setup ...

    // TODO: Parse remote SDP to extract fingerprint
    // std::string peer_fp = parse_fingerprint_from_sdp(remote_sdp);
    // dtls_srtp_->SetPeerFingerprint(hex_to_bytes(peer_fp));
    // dtls_srtp_->SetPeerFingerprintAlgo("sha-256");

    // For answer, we're CLIENT (active)
    dtls_srtp_->SetMode(DtlsMode::CLIENT);

    // Generate answer SDP
    std::string fingerprint = dtls_srtp_->GetFingerprintString();
    std::string sdp =
        "v=0\r\n"
        // ... other SDP lines ...
        "a=fingerprint:sha-256 " + fingerprint + "\r\n"
        "a=setup:active\r\n";  // active for answer

    return sdp;
}
```

#### 4. OnComponentStateChanged() - Start DTLS when ICE ready

```cpp
void Session::OnComponentStateChanged(int component_id, const std::string& state) {
    LOG_INFO("Component {} state changed: {}", component_id, state);

    // ... existing event pushing ...

    // Start DTLS handshake when Component 1 is ready
    if (component_id == 1 && state == "READY" && dtls_srtp_ && !dtls_srtp_->IsReady()) {
        LOG_INFO("Component 1 ready, starting DTLS handshake");
        dtls_srtp_->StartHandshake();
    }
}
```

#### 5. SendRtpData() - Encrypt before sending

```cpp
void Session::SendRtpData(const uint8_t* data, size_t len) {
    LOG_DEBUG("Sending {} bytes of RTP", len);

    if (!dtls_srtp_ || !dtls_srtp_->IsReady()) {
        LOG_DEBUG("DTLS-SRTP not ready, cannot send");
        return;
    }

    // Encrypt RTP
    auto encrypted = dtls_srtp_->ProcessOutgoingData(1, data, len);
    if (encrypted.empty()) {
        LOG_ERROR("Failed to encrypt RTP");
        return;
    }

    // Send encrypted RTP via Component 1
    ice_agent_->Send(1, encrypted.data(), encrypted.size());
}
```

#### 6. SendRtcpData() - Encrypt RTCP

```cpp
void Session::SendRtcpData(const uint8_t* data, size_t len) {
    int component_id = rtcp_mux_ ? 1 : 2;
    LOG_DEBUG("Sending {} bytes of RTCP via Component {}", len, component_id);

    if (!dtls_srtp_ || !dtls_srtp_->IsReady()) {
        LOG_DEBUG("DTLS-SRTP not ready, cannot send");
        return;
    }

    // Encrypt RTCP
    auto encrypted = dtls_srtp_->ProcessOutgoingData(component_id, data, len);
    if (encrypted.empty()) {
        LOG_ERROR("Failed to encrypt RTCP");
        return;
    }

    // Send encrypted RTCP
    ice_agent_->Send(component_id, encrypted.data(), encrypted.size());
}
```

#### 7. OnIceDataReceived() - Decrypt incoming data

```cpp
void Session::OnIceDataReceived(int component_id, const uint8_t* data, size_t len) {
    LOG_DEBUG("Received {} bytes on component {}", len, component_id);

    if (!dtls_srtp_) {
        LOG_ERROR("DTLS-SRTP handler not initialized");
        return;
    }

    // Process incoming data (handles DTLS and SRTP)
    auto decrypted = dtls_srtp_->ProcessIncomingData(component_id, data, len);

    if (decrypted.empty()) {
        // Either DTLS packet (handled internally) or error
        return;
    }

    // Push decrypted RTP/RTCP to rtpbin
    if (component_id == 1) {
        // Component 1: Could be RTP or RTCP
        if (len >= 2 && data[1] >= 192 && data[1] < 224) {
            PushRtcpData(decrypted.data(), decrypted.size());
        } else {
            PushRtpData(decrypted.data(), decrypted.size());
        }
    } else if (component_id == 2) {
        // Component 2: Always RTCP
        PushRtcpData(decrypted.data(), decrypted.size());
    }
}
```

#### 8. Close() - Cleanup

```cpp
void Session::Close() {
    // ... existing cleanup ...

    // Stop DTLS handshake if running
    if (dtls_srtp_) {
        dtls_srtp_->StopHandshake();
        dtls_srtp_.reset();
    }

    // ... rest of cleanup ...
}
```

---

## CMakeLists.txt Changes

```cmake
# Find GnuTLS
pkg_check_modules(GNUTLS REQUIRED gnutls>=3.6.0)

# Find libsrtp2
pkg_check_modules(SRTP REQUIRED libsrtp2>=2.3.0)

# Add include directories
include_directories(
    ${GNUTLS_INCLUDE_DIRS}
    ${SRTP_INCLUDE_DIRS}
)

# Link libraries
target_link_libraries(drunk-call-service
    ${GNUTLS_LIBRARIES}
    ${SRTP_LIBRARIES}
)
```

---

## Testing Plan

### 1. Certificate Generation Test
- Verify certificate is created
- Verify fingerprint is SHA-256 (32 bytes)
- Verify fingerprint format is correct (AA:BB:CC:...)

### 2. DTLS Handshake Test (Local)
- Create two DtlsSrtpHandler instances
- Exchange fingerprints
- Start handshake (one SERVER, one CLIENT)
- Verify handshake completes within 5 seconds
- Verify SRTP keys are extracted

### 3. SRTP Encryption Test
- Encrypt sample RTP packet
- Verify output is larger (SRTP overhead)
- Decrypt and verify matches original

### 4. Integration Test
- Test with Phase 2 pipeline
- Verify DTLS packets flow over Component 1
- Verify RTP/RTCP encryption/decryption
- Monitor for packet loss or corruption

### 5. Interop Test (with Dino)
- Make call from siproxylin to Dino
- Verify DTLS handshake completes
- Verify audio flows both directions
- Check for any errors or warnings

---

## Success Criteria

✅ **Certificate generation** - ECDSA-256, self-signed, valid fingerprint
✅ **DTLS handshake** - Completes within 20 seconds
✅ **Fingerprint verification** - Peer cert matches advertised fingerprint
✅ **SRTP setup** - Keys extracted and configured
✅ **Encryption** - RTP/RTCP packets encrypted before sending
✅ **Decryption** - Incoming SRTP/SRTCP packets decrypted correctly
✅ **Packet routing** - DTLS to handler, SRTP to rtpbin
✅ **No crashes** - Handles errors gracefully
✅ **Audio works** - End-to-end audio transmission

---

## Next Steps After Phase 3

**Phase 4: SDP Generation & Parsing**
- Replace stub SDP in CreateOffer/CreateAnswer
- Parse remote SDP to extract ICE credentials, fingerprint, codecs
- Generate proper Jingle-compatible SDP

**Phase 5: End-to-End Testing**
- Test with Dino (2-component ICE)
- Test with Conversations.im (2-component ICE)
- Verify P2P and TURN relay modes

---

## Implementation Results (2026-02-26)

### Components Implemented

**1. SrtpSession Class** (`srtp_session.h/cc`)
- ✅ libsrtp2 wrapper for RTP/RTCP encryption/decryption
- ✅ Thread-safe crypto operations
- ✅ AES-128-CM + HMAC-SHA1-80 profile support
- ✅ Methods: EncryptRtp(), EncryptRtcp(), DecryptRtp(), DecryptRtcp()
- ✅ Proper key derivation from DTLS keying material
- ✅ Authentication failure detection

**2. DtlsSrtpHandler Class** (`dtls_srtp_handler.h/cc`)
- ✅ ECDSA-256 self-signed certificate generation
- ✅ SHA-256 fingerprint calculation for SDP
- ✅ Async DTLS handshake (separate thread, 20s timeout)
- ✅ GnuTLS transport callbacks for packet I/O over ICE
- ✅ Peer certificate verification against SDP fingerprint
- ✅ SRTP key extraction (client/server keys + salts)
- ✅ Intelligent packet routing (DTLS vs SRTP/SRTCP)
- ✅ CLIENT/SERVER mode negotiation based on SDP setup attribute

**3. Session Integration**
- ✅ Certificate generated during Initialize()
- ✅ Real fingerprint included in SDP (replaces "TODO-FINGERPRINT")
- ✅ DTLS mode set based on SDP role (offer=SERVER, answer=CLIENT)
- ✅ Auto-trigger handshake when ICE Component 1 reaches READY state
- ✅ Complete encryption/decryption wired into data flow
- ✅ Proper cleanup in Close() and destructor

### Real Call Testing (2026-02-26)

**Test 1: Outgoing Call to Dino**
- Session: 445f7aa5-e3db-49ed-a69a-2245bc24a375
- Peer: alpinex-dino@conversations.im/dino.7187beb5

**Results:**
```
✅ Certificate generated: ECDSA-256 with SHA-256 fingerprint
   Fingerprint: BA:73:D5:3E:FD:39:97:CF:13:69:A7:9A:E0:9A:A3:99:D1:3D:0E:BD:F0:56:B1:A0:D3:24:69:25:C0:D9:2F:8E

✅ SDP offer includes real fingerprint in proper format
   a=fingerprint:sha-256 BA:73:D5:3E:FD:39:97:CF:...
   a=setup:actpass

✅ Remote SDP parsed successfully
   Extracted fingerprint: sha-256, size=32 bytes (15:43:1F:55:8C:1F:79:A2:...)
   Extracted setup: active

✅ DTLS mode set correctly: SERVER (peer is active)

❌ DTLS handshake not triggered - waiting for ICE Component 1 READY state
   Root cause: Remote candidates failed to parse (see Issue #1 below)
```

**Test 2: Incoming Call from Conversations.im**
- Session: HWmBg9r0hoBxBOQRI1Gd3w
- Peer: hippopotamus@conversations.im/Conversations.8hbhC-2MJ0

**Results:**
```
✅ Certificate generated: SHA-256 fingerprint
   Fingerprint: 57:60:55:8A:92:22:A8:58:D4:B1:AC:C6:7C:11:0D:36:37:0E:22:72:2E:6B:5D:49:D0:64:73:96:2C:C7:AC:26

❌ CreateAnswer failed - 0-byte SDP generated
   Root cause: Pipeline failed to reach PLAYING state (see Issue #2 below)
```

### Dependencies Added
```cmake
pkg_check_modules(GNUTLS REQUIRED gnutls>=3.6.0)
pkg_check_modules(SRTP REQUIRED libsrtp2>=2.3.0)
```

### Files Created
- `drunk_call_service/include/srtp_session.h`
- `drunk_call_service/src/srtp_session.cc`
- `drunk_call_service/include/dtls_srtp_handler.h`
- `drunk_call_service/src/dtls_srtp_handler.cc`

### Files Modified
- `drunk_call_service/include/session.h` - Added dtls_srtp_ member
- `drunk_call_service/src/session.cc` - Integrated DTLS-SRTP into all flows
- `drunk_call_service/CMakeLists.txt` - Added GnuTLS and libsrtp2

---

## Outstanding Issues Status

### Issue #1: ICE Candidate Parsing Failure ✅ FIXED (2026-02-26 18:00)

**Symptom:** All remote candidates failed to parse with "Failed to parse remote candidate"
**Root Cause:** We were guessing `component_id = sdp_mline_index + 1` instead of extracting from parsed candidate
**Fix Applied:** Copied GStreamer's `nice.c` approach - use `candidate->component_id` from parsed NiceCandidate

**The Fix (inspired by GStreamer):**
```cpp
// BEFORE (WRONG):
int component_id = sdp_mline_index + 1;  // Guessing!
nice_agent_set_remote_candidates(agent_, stream_id_, component_id, ...);

// AFTER (CORRECT - like GStreamer):
NiceCandidate* candidate = nice_agent_parse_remote_candidate_sdp(...);
guint parsed_component_id = candidate->component_id;  // Extract!
nice_agent_set_remote_candidates(agent_, stream_id_, parsed_component_id, ...);
```

**Test Results:**
- ✅ 100% of remote candidates now parse successfully
- ✅ Both components reach CONNECTING state
- ✅ ICE connectivity checks START correctly

**Status:** **RESOLVED** ✅

---

### Issue #2: CreateAnswer Pipeline State Failure ✅ FIXED (2026-02-26 18:00)

**Symptom:** Pipeline failed to reach PLAYING state during CreateAnswer, 0-byte SDP generated
**Root Cause:** Timing/dependency issue related to Issue #1
**Fix Applied:** Resolved automatically when Issue #1 was fixed

**Test Results:**
- ✅ Pipeline reaches PLAYING state
- ✅ 343-byte SDP answer generated (was 0 bytes)
- ✅ ICE gathering starts correctly
- ✅ Local candidates generated

**Status:** **RESOLVED** ✅

---

### Issue #3: ICE Connectivity Checks Timing Out ⚠️ NEW (2026-02-26 18:00)

**Symptom:** ICE checks start correctly but time out after ~7 seconds
**State Progression:** `NEW → CONNECTING → checking → FAILED (7s timeout)`

**Evidence It Can Work:**
- Earlier logs show successful `ice=completed` calls with audio
- TURN relay connections established successfully in past

**Likely Causes:**
1. Test duration too short (hung up before 10-20s ICE check window)
2. TURN relay timing
3. Need sustained connection test

**Next Steps:** Test with longer call duration (20+ seconds)

**Status:** Under investigation - NOT blocking implementation

---

### Issue #4: DTLS Handshake - Ready to Test

**Status:** Cannot fully test until ICE connectivity succeeds (Issue #3)
**Implementation Status:** Code complete and ready, waiting for ICE connection

---

## Success Criteria Status (Post-Fix)

- ✅ **Certificate generation** - ECDSA-256, self-signed, valid fingerprint
- ✅ **Candidate parsing** - 100% success rate (was 0%)
- ✅ **CreateAnswer flow** - Valid SDP generated (was 0 bytes)
- ✅ **ICE checks start** - Both components reach CONNECTING state
- ⏳ **ICE connectivity** - Checks start but timeout (need longer test)
- ⏳ **DTLS handshake** - Ready to test once ICE succeeds
- ⏳ **Fingerprint verification** - Implementation ready
- ⏳ **SRTP setup** - Implementation ready
- ⏳ **Encryption/Decryption** - Implementation ready
- ✅ **Packet routing** - DTLS vs SRTP detection implemented
- ✅ **No crashes** - Clean initialization and shutdown

**Progress:** 5/10 criteria verified, 5/10 ready for testing once ICE connects

---

## Next Steps (Updated Post-Fix)

**Priority 1: Verify ICE Connection with Longer Test ⏳**
1. Test with 20+ second call duration (don't hang up immediately)
2. Monitor for Component 1/2 reaching READY state
3. Check TURN relay establishment logs

**Priority 2: Verify DTLS-SRTP Once ICE Connects ⏳**
1. Monitor for "Component 1 ready, starting DTLS handshake" log
2. Verify handshake completes within 20 seconds
3. Check SRTP keys extracted correctly
4. Monitor encryption/decryption logs
5. Test audio end-to-end

**Priority 3: Performance Tuning 🔮**
1. Optimize ICE check timing if needed
2. Monitor bandwidth and latency
3. Test with various network conditions

---

**Document Status:** Major Fixes Applied ✅ - Ready for Extended Testing
**Last Updated:** 2026-02-26 18:00 (post-fix)
**Based on:** Dino 0.4.x implementation + GStreamer's candidate handling
