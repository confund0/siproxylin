/**
 * CallService gRPC Implementation (Phase 4.2)
 *
 * Purpose: gRPC service layer that bridges Python (Jingle) ↔ C++ (WebRTC/GStreamer)
 *
 * Threading Model:
 * - gRPC thread pool: Handles all RPC calls (CreateSession, CreateOffer, etc.)
 * - GLib thread: Processes GStreamer callbacks, pushes events to queues
 * - Communication: ThreadSafeQueue + SessionManager (cross-thread safe)
 *
 * LOGGING: Use LOG_*() macros (logger.h) for all application logging
 *          DO NOT use std::cout/std::cerr in production code
 *          See: docs/LOGGING-POLICY.md
 *
 * Architecture: docs/CALLS/4-GRPC-PLAN.md
 * Threading: docs/CALLS/GSTREAMER-THREADING.md
 * Usage: docs/THREAD-INFRASTRUCTURE-USAGE.md
 */

#ifndef CALL_SERVICE_IMPL_H
#define CALL_SERVICE_IMPL_H

#include <grpcpp/grpcpp.h>
#include "call.grpc.pb.h"
#include "session_manager.h"
#include <memory>

namespace drunk_call {

/**
 * CallService gRPC implementation.
 *
 * RPC handlers run in gRPC thread pool.
 * GStreamer callbacks run in GLib main loop thread.
 * Cross-thread communication via ThreadSafeQueue + SessionManager.
 *
 * Phase 4.2: All methods return UNIMPLEMENTED (stub implementations)
 * Phase 4.3+: Implement CreateSession, StreamEvents, SDP operations, etc.
 */
class CallServiceImpl final : public call::CallService::Service {
public:
    CallServiceImpl();
    ~CallServiceImpl() override;

    // Session lifecycle
    grpc::Status CreateSession(
        grpc::ServerContext* context,
        const call::CreateSessionRequest* request,
        call::CreateSessionResponse* response) override;

    grpc::Status EndSession(
        grpc::ServerContext* context,
        const call::EndSessionRequest* request,
        call::Empty* response) override;

    // SDP operations
    grpc::Status CreateOffer(
        grpc::ServerContext* context,
        const call::CreateOfferRequest* request,
        call::SDPResponse* response) override;

    grpc::Status CreateAnswer(
        grpc::ServerContext* context,
        const call::CreateAnswerRequest* request,
        call::SDPResponse* response) override;

    grpc::Status SetRemoteDescription(
        grpc::ServerContext* context,
        const call::SetRemoteDescriptionRequest* request,
        call::Empty* response) override;

    // ICE candidate handling
    grpc::Status AddICECandidate(
        grpc::ServerContext* context,
        const call::AddICECandidateRequest* request,
        call::Empty* response) override;

    // Event streaming (C++ → Python)
    grpc::Status StreamEvents(
        grpc::ServerContext* context,
        const call::StreamEventsRequest* request,
        grpc::ServerWriter<call::CallEvent>* writer) override;

    // Audio device management
    grpc::Status ListAudioDevices(
        grpc::ServerContext* context,
        const call::Empty* request,
        call::ListAudioDevicesResponse* response) override;

    // Video device management
    grpc::Status ListVideoDevices(
        grpc::ServerContext* context,
        const call::Empty* request,
        call::ListVideoDevicesResponse* response) override;

    grpc::Status SetMute(
        grpc::ServerContext* context,
        const call::SetMuteRequest* request,
        call::Empty* response) override;

    // Statistics
    grpc::Status GetStats(
        grpc::ServerContext* context,
        const call::GetStatsRequest* request,
        call::GetStatsResponse* response) override;

    // Service management
    grpc::Status Heartbeat(
        grpc::ServerContext* context,
        const call::Empty* request,
        call::Empty* response) override;

    grpc::Status Shutdown(
        grpc::ServerContext* context,
        const call::Empty* request,
        call::Empty* response) override;

    // Cleanup all sessions (called during shutdown)
    void cleanup_all_sessions();

private:
    // Session manager (thread-safe)
    SessionManager session_manager_;

    // Shutdown flag (atomic, checked by Shutdown RPC)
    std::atomic<bool> shutdown_requested_;
};

} // namespace drunk_call

#endif // CALL_SERVICE_IMPL_H
