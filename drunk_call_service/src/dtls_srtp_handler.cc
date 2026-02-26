#include "dtls_srtp_handler.h"
#include "logger.h"
#include <cstring>
#include <sstream>
#include <iomanip>
#include <chrono>

namespace drunk_call {

DtlsSrtpHandler::DtlsSrtpHandler()
    : cert_cred_(nullptr),
      certificate_(nullptr),
      private_key_(nullptr),
      mode_(DtlsMode::SERVER),
      dtls_session_(nullptr),
      session_initialized_(false) {
    LOG_DEBUG("DtlsSrtpHandler created");
}

DtlsSrtpHandler::~DtlsSrtpHandler() {
    StopHandshake();

    // Wait for handshake thread to finish
    if (handshake_thread_.joinable()) {
        handshake_thread_.join();
    }

    // Cleanup DTLS session
    if (session_initialized_ && dtls_session_) {
        gnutls_deinit(dtls_session_);
    }

    // Cleanup credentials
    if (certificate_) {
        gnutls_x509_crt_deinit(certificate_);
    }
    if (private_key_) {
        gnutls_x509_privkey_deinit(private_key_);
    }
    if (cert_cred_) {
        gnutls_certificate_free_credentials(cert_cred_);
    }

    LOG_DEBUG("DtlsSrtpHandler destroyed");
}

bool DtlsSrtpHandler::GenerateCertificate() {
    LOG_INFO("Generating DTLS certificate");

    // Initialize GnuTLS (idempotent)
    int ret = gnutls_global_init();
    if (ret < 0) {
        LOG_ERROR("Failed to initialize GnuTLS: {}", gnutls_strerror(ret));
        return false;
    }

    // Allocate credentials
    ret = gnutls_certificate_allocate_credentials(&cert_cred_);
    if (ret < 0) {
        LOG_ERROR("Failed to allocate certificate credentials: {}", gnutls_strerror(ret));
        return false;
    }

    // Generate ECDSA private key (256-bit, like Dino)
    ret = gnutls_x509_privkey_init(&private_key_);
    if (ret < 0) {
        LOG_ERROR("Failed to initialize private key: {}", gnutls_strerror(ret));
        return false;
    }

    ret = gnutls_x509_privkey_generate(private_key_, GNUTLS_PK_ECDSA,
                                       GNUTLS_CURVE_TO_BITS(GNUTLS_ECC_CURVE_SECP256R1), 0);
    if (ret < 0) {
        LOG_ERROR("Failed to generate ECDSA key: {}", gnutls_strerror(ret));
        return false;
    }

    LOG_INFO("ECDSA-256 private key generated");

    // Create self-signed certificate
    ret = gnutls_x509_crt_init(&certificate_);
    if (ret < 0) {
        LOG_ERROR("Failed to initialize certificate: {}", gnutls_strerror(ret));
        return false;
    }

    // Set version (X.509 v1 = 0, v3 = 2; use v1 like Dino)
    ret = gnutls_x509_crt_set_version(certificate_, 1);
    if (ret < 0) {
        LOG_ERROR("Failed to set certificate version: {}", gnutls_strerror(ret));
        return false;
    }

    // Set key
    ret = gnutls_x509_crt_set_key(certificate_, private_key_);
    if (ret < 0) {
        LOG_ERROR("Failed to set certificate key: {}", gnutls_strerror(ret));
        return false;
    }

    // Set validity: 1 day before to 1 day after (like Dino)
    time_t now = time(nullptr);
    ret = gnutls_x509_crt_set_activation_time(certificate_, now - 86400);
    if (ret < 0) {
        LOG_ERROR("Failed to set activation time: {}", gnutls_strerror(ret));
        return false;
    }

    ret = gnutls_x509_crt_set_expiration_time(certificate_, now + 86400);
    if (ret < 0) {
        LOG_ERROR("Failed to set expiration time: {}", gnutls_strerror(ret));
        return false;
    }

    // Serial number
    uint32_t serial = 1;
    ret = gnutls_x509_crt_set_serial(certificate_, &serial, sizeof(serial));
    if (ret < 0) {
        LOG_ERROR("Failed to set serial number: {}", gnutls_strerror(ret));
        return false;
    }

    // Self-sign with SHA-256
    ret = gnutls_x509_crt_sign2(certificate_, certificate_, private_key_,
                               GNUTLS_DIG_SHA256, 0);
    if (ret < 0) {
        LOG_ERROR("Failed to self-sign certificate: {}", gnutls_strerror(ret));
        return false;
    }

    LOG_INFO("Self-signed certificate created");

    // Calculate SHA-256 fingerprint
    own_fingerprint_ = CalculateFingerprint();
    LOG_INFO("Certificate fingerprint (SHA-256): {}", GetFingerprintString());

    // Set credentials
    ret = gnutls_certificate_set_x509_key(cert_cred_, &certificate_, 1, private_key_);
    if (ret < 0) {
        LOG_ERROR("Failed to set X.509 key: {}", gnutls_strerror(ret));
        return false;
    }

    LOG_INFO("DTLS certificate generated successfully");
    return true;
}

std::vector<uint8_t> DtlsSrtpHandler::CalculateFingerprint() {
    uint8_t buf[512];
    size_t buf_size = sizeof(buf);

    int ret = gnutls_x509_crt_get_fingerprint(certificate_, GNUTLS_DIG_SHA256, buf, &buf_size);
    if (ret < 0) {
        LOG_ERROR("Failed to calculate fingerprint: {}", gnutls_strerror(ret));
        return {};
    }

    return std::vector<uint8_t>(buf, buf + buf_size);
}

std::string DtlsSrtpHandler::GetFingerprintString() const {
    std::ostringstream oss;
    for (size_t i = 0; i < own_fingerprint_.size(); ++i) {
        oss << std::hex << std::uppercase << std::setw(2) << std::setfill('0')
            << static_cast<int>(own_fingerprint_[i]);
        if (i < own_fingerprint_.size() - 1) {
            oss << ":";
        }
    }
    return oss.str();
}

bool DtlsSrtpHandler::StartHandshake() {
    if (ready_) {
        LOG_WARN("DTLS handshake already complete");
        return true;
    }

    LOG_INFO("Starting DTLS handshake (mode: {})", mode_ == DtlsMode::SERVER ? "SERVER" : "CLIENT");

    stop_ = false;
    handshake_thread_ = std::thread(&DtlsSrtpHandler::HandshakeThread, this);

    return true;
}

void DtlsSrtpHandler::StopHandshake() {
    if (!stop_.exchange(true)) {
        LOG_INFO("Stopping DTLS handshake");
        buffer_cv_.notify_all();
    }
}

void DtlsSrtpHandler::HandshakeThread() {
    LOG_INFO("DTLS handshake thread started");

    // Create DTLS session
    unsigned int flags = GNUTLS_DATAGRAM | GNUTLS_NONBLOCK;
    flags |= (mode_ == DtlsMode::SERVER) ? GNUTLS_SERVER : GNUTLS_CLIENT;

    int ret = gnutls_init(&dtls_session_, flags);
    if (ret < 0) {
        LOG_ERROR("Failed to initialize DTLS session: {}", gnutls_strerror(ret));
        return;
    }
    session_initialized_ = true;

    // Set SRTP profile
    const char* err_pos = nullptr;
    ret = gnutls_priority_set_direct(dtls_session_,
                                     "NORMAL:!VERS-TLS-ALL:+VERS-DTLS-ALL:+CTYPE-CLI-X509",
                                     &err_pos);
    if (ret < 0) {
        LOG_ERROR("Failed to set priority: {} (at: {})", gnutls_strerror(ret), err_pos ? err_pos : "unknown");
        gnutls_deinit(dtls_session_);
        session_initialized_ = false;
        return;
    }

    ret = gnutls_srtp_set_profile_direct(dtls_session_, "SRTP_AES128_CM_HMAC_SHA1_80", &err_pos);
    if (ret < 0) {
        LOG_ERROR("Failed to set SRTP profile: {} (at: {})", gnutls_strerror(ret), err_pos ? err_pos : "unknown");
        gnutls_deinit(dtls_session_);
        session_initialized_ = false;
        return;
    }

    // Set credentials
    ret = gnutls_credentials_set(dtls_session_, GNUTLS_CRD_CERTIFICATE, cert_cred_);
    if (ret < 0) {
        LOG_ERROR("Failed to set credentials: {}", gnutls_strerror(ret));
        gnutls_deinit(dtls_session_);
        session_initialized_ = false;
        return;
    }

    // Request client certificate
    gnutls_certificate_server_set_request(dtls_session_, GNUTLS_CERT_REQUEST);

    // Set transport callbacks
    gnutls_transport_set_ptr(dtls_session_, this);
    gnutls_transport_set_pull_function(dtls_session_, PullFunction);
    gnutls_transport_set_pull_timeout_function(dtls_session_, PullTimeoutFunction);
    gnutls_transport_set_push_function(dtls_session_, PushFunction);

    // Set verify callback
    gnutls_session_set_verify_function(dtls_session_, VerifyFunction);

    LOG_INFO("DTLS session configured, starting handshake");

    // Perform handshake (with 20-second timeout like Dino)
    if (!DoHandshake()) {
        LOG_ERROR("DTLS handshake failed");
        gnutls_deinit(dtls_session_);
        session_initialized_ = false;
        return;
    }

    // Extract SRTP keys
    if (!ExtractSrtpKeys()) {
        LOG_ERROR("Failed to extract SRTP keys");
        gnutls_deinit(dtls_session_);
        session_initialized_ = false;
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
            LOG_ERROR("DTLS handshake timeout (20s)");
            return false;
        }

        if (ret < 0 && !gnutls_error_is_fatal(ret)) {
            // Non-fatal error, wait and retry
            if (ret == GNUTLS_E_AGAIN || ret == GNUTLS_E_INTERRUPTED) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            } else {
                LOG_DEBUG("Non-fatal handshake error: {}", gnutls_strerror(ret));
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
        }
    } while (ret < 0 && !gnutls_error_is_fatal(ret));

    if (ret != GNUTLS_E_SUCCESS) {
        LOG_ERROR("DTLS handshake error: {}", gnutls_strerror(ret));
        return false;
    }

    LOG_INFO("DTLS handshake succeeded");
    return true;
}

bool DtlsSrtpHandler::ExtractSrtpKeys() {
    LOG_INFO("Extracting SRTP keys from DTLS session");

    gnutls_datum_t client_key, client_salt, server_key, server_salt;
    uint8_t km[150];

    int ret = gnutls_srtp_get_keys(dtls_session_, km, sizeof(km),
                                   &client_key, &client_salt,
                                   &server_key, &server_salt);
    if (ret < 0) {
        LOG_ERROR("Failed to extract SRTP keys: {}", gnutls_strerror(ret));
        return false;
    }

    LOG_DEBUG("SRTP keys extracted: client_key_len={}, client_salt_len={}, server_key_len={}, server_salt_len={}",
              client_key.size, client_salt.size, server_key.size, server_salt.size);

    // Create SRTP session
    try {
        srtp_session_ = std::make_unique<SrtpSession>();

        // Set keys based on mode
        // SERVER uses server keys for encryption, client keys for decryption
        // CLIENT uses client keys for encryption, server keys for decryption
        if (mode_ == DtlsMode::SERVER) {
            srtp_session_->SetEncryptionKey(server_key.data, server_key.size,
                                           server_salt.data, server_salt.size);
            srtp_session_->SetDecryptionKey(client_key.data, client_key.size,
                                           client_salt.data, client_salt.size);
            LOG_INFO("SRTP keys configured (SERVER mode: encrypt=server, decrypt=client)");
        } else {
            srtp_session_->SetEncryptionKey(client_key.data, client_key.size,
                                           client_salt.data, client_salt.size);
            srtp_session_->SetDecryptionKey(server_key.data, server_key.size,
                                           server_salt.data, server_salt.size);
            LOG_INFO("SRTP keys configured (CLIENT mode: encrypt=client, decrypt=server)");
        }
    } catch (const SrtpError& e) {
        LOG_ERROR("Failed to configure SRTP session: {}", e.what());
        return false;
    }

    return true;
}

bool DtlsSrtpHandler::VerifyPeerCertificate() {
    LOG_INFO("Verifying peer certificate");

    // Get peer certificate list
    unsigned int cert_list_size = 0;
    const gnutls_datum_t* cert_list = gnutls_certificate_get_peers(dtls_session_, &cert_list_size);

    if (cert_list_size == 0 || !cert_list) {
        LOG_ERROR("No peer certificates received");
        return false;
    }

    if (cert_list_size > 1) {
        LOG_WARN("Received {} peer certificates, using first", cert_list_size);
    }

    // Import first certificate
    gnutls_x509_crt_t peer_cert;
    int ret = gnutls_x509_crt_init(&peer_cert);
    if (ret < 0) {
        LOG_ERROR("Failed to init peer certificate: {}", gnutls_strerror(ret));
        return false;
    }

    ret = gnutls_x509_crt_import(peer_cert, &cert_list[0], GNUTLS_X509_FMT_DER);
    if (ret < 0) {
        LOG_ERROR("Failed to import peer certificate: {}", gnutls_strerror(ret));
        gnutls_x509_crt_deinit(peer_cert);
        return false;
    }

    // Calculate fingerprint
    gnutls_digest_algorithm_t algo = GNUTLS_DIG_SHA256;
    if (peer_fp_algo_ == "sha-1") {
        algo = GNUTLS_DIG_SHA1;
    } else if (peer_fp_algo_ == "sha-384") {
        algo = GNUTLS_DIG_SHA384;
    } else if (peer_fp_algo_ == "sha-512") {
        algo = GNUTLS_DIG_SHA512;
    } else if (!peer_fp_algo_.empty() && peer_fp_algo_ != "sha-256") {
        LOG_WARN("Unknown peer fingerprint algorithm: {}, using SHA-256", peer_fp_algo_);
    }

    uint8_t fingerprint[512];
    size_t fp_size = sizeof(fingerprint);
    ret = gnutls_x509_crt_get_fingerprint(peer_cert, algo, fingerprint, &fp_size);
    if (ret < 0) {
        LOG_ERROR("Failed to calculate peer fingerprint: {}", gnutls_strerror(ret));
        gnutls_x509_crt_deinit(peer_cert);
        return false;
    }

    gnutls_x509_crt_deinit(peer_cert);

    // Compare with advertised fingerprint
    if (fp_size != peer_fingerprint_.size()) {
        LOG_ERROR("Fingerprint size mismatch: calculated={}, expected={}", fp_size, peer_fingerprint_.size());
        return false;
    }

    if (memcmp(fingerprint, peer_fingerprint_.data(), fp_size) != 0) {
        // Log both fingerprints for debugging
        std::ostringstream calc_fp, exp_fp;
        for (size_t i = 0; i < fp_size; ++i) {
            calc_fp << std::hex << std::uppercase << std::setw(2) << std::setfill('0')
                    << static_cast<int>(fingerprint[i]);
            exp_fp << std::hex << std::uppercase << std::setw(2) << std::setfill('0')
                   << static_cast<int>(peer_fingerprint_[i]);
            if (i < fp_size - 1) {
                calc_fp << ":";
                exp_fp << ":";
            }
        }
        LOG_ERROR("Fingerprint mismatch! Calculated: {}, Expected: {}", calc_fp.str(), exp_fp.str());
        return false;
    }

    LOG_INFO("Peer certificate verified successfully (fingerprint matches)");
    return true;
}

// GnuTLS transport callbacks

ssize_t DtlsSrtpHandler::PullFunction(gnutls_transport_ptr_t ptr, void* data, size_t len) {
    auto* self = static_cast<DtlsSrtpHandler*>(ptr);

    std::unique_lock<std::mutex> lock(self->buffer_mutex_);

    // Wait for data or stop signal
    while (self->dtls_buffer_.empty() && !self->stop_) {
        self->buffer_cv_.wait(lock);
    }

    if (self->stop_) {
        gnutls_transport_set_errno(self->dtls_session_, EINTR);
        return -1;
    }

    if (self->dtls_buffer_.empty()) {
        gnutls_transport_set_errno(self->dtls_session_, EAGAIN);
        return -1;
    }

    auto packet = self->dtls_buffer_.front();
    self->dtls_buffer_.pop();
    lock.unlock();

    size_t copy_len = std::min(len, packet.size());
    memcpy(data, packet.data(), copy_len);

    LOG_DEBUG("DTLS pull: {} bytes", copy_len);
    return copy_len;
}

int DtlsSrtpHandler::PullTimeoutFunction(gnutls_transport_ptr_t ptr, unsigned int ms) {
    auto* self = static_cast<DtlsSrtpHandler*>(ptr);

    std::unique_lock<std::mutex> lock(self->buffer_mutex_);
    auto timeout = std::chrono::milliseconds(ms);

    // Wait for data with timeout
    bool has_data = self->buffer_cv_.wait_for(lock, timeout, [self] {
        return !self->dtls_buffer_.empty() || self->stop_;
    });

    if (self->stop_) {
        return -1;
    }

    // Return 1 if data available, 0 on timeout
    return has_data ? 1 : 0;
}

ssize_t DtlsSrtpHandler::PushFunction(gnutls_transport_ptr_t ptr, const void* data, size_t len) {
    auto* self = static_cast<DtlsSrtpHandler*>(ptr);

    if (self->send_data_callback_) {
        self->send_data_callback_(static_cast<const uint8_t*>(data), len);
        LOG_DEBUG("DTLS push: {} bytes", len);
        return len;
    }

    LOG_ERROR("DTLS push: no send callback configured");
    return -1;
}

int DtlsSrtpHandler::VerifyFunction(gnutls_session_t session) {
    auto* self = static_cast<DtlsSrtpHandler*>(gnutls_transport_get_ptr(session));

    if (!self->VerifyPeerCertificate()) {
        LOG_ERROR("Peer certificate verification failed - aborting handshake");
        return 1;  // Abort handshake
    }

    return 0;  // Continue handshake
}

void DtlsSrtpHandler::OnDtlsDataReceived(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(buffer_mutex_);
    dtls_buffer_.push(std::vector<uint8_t>(data, data + len));
    buffer_cv_.notify_one();
    LOG_DEBUG("DTLS data queued: {} bytes", len);
}

// Packet processing

std::vector<uint8_t> DtlsSrtpHandler::ProcessIncomingData(uint8_t component_id,
                                                           const uint8_t* data, size_t len) {
    if (len == 0) {
        return {};
    }

    // RTP/RTCP packets (encrypted) - data[0] >= 128
    if (data[0] >= 128) {
        if (!srtp_session_ || !srtp_session_->HasDecrypt()) {
            LOG_DEBUG("Received encrypted data before SRTP ready, dropping");
            return {};
        }

        try {
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
        } catch (const SrtpError& e) {
            LOG_ERROR("SRTP decryption failed: {}", e.what());
            return {};
        }
    }

    // DTLS packets (handshake) - data[0] in [20, 64)
    if (component_id == 1 && len >= 1 && data[0] >= 20 && data[0] < 64) {
        OnDtlsDataReceived(data, len);
        return {};  // Don't forward to rtpbin
    }

    LOG_DEBUG("Unknown packet type on component {}: first_byte={}, len={}",
              component_id, data[0], len);
    return {};
}

std::vector<uint8_t> DtlsSrtpHandler::ProcessOutgoingData(uint8_t component_id,
                                                           const uint8_t* data, size_t len) {
    if (!srtp_session_ || !srtp_session_->HasEncrypt()) {
        LOG_DEBUG("Trying to send before SRTP ready");
        return {};
    }

    try {
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
    } catch (const SrtpError& e) {
        LOG_ERROR("SRTP encryption failed: {}", e.what());
        return {};
    }

    return {};
}

}  // namespace drunk_call
