#pragma once

#include <grpcpp/grpcpp.h>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <thread>
#include <chrono>
#include <atomic>

#include "call.grpc.pb.h"
#include "session.h"

namespace drunk_call {

// gRPC service implementation for call management
class CallServer final : public call::CallService::Service {
 public:
  CallServer();
  ~CallServer() override;

  // Session management
  grpc::Status CreateSession(grpc::ServerContext* context,
                              const call::CreateSessionRequest* request,
                              call::CreateSessionResponse* response) override;

  grpc::Status EndSession(grpc::ServerContext* context,
                          const call::EndSessionRequest* request,
                          call::Empty* response) override;

  // SDP negotiation
  grpc::Status CreateOffer(grpc::ServerContext* context,
                           const call::CreateOfferRequest* request,
                           call::SDPResponse* response) override;

  grpc::Status CreateAnswer(grpc::ServerContext* context,
                            const call::CreateAnswerRequest* request,
                            call::SDPResponse* response) override;

  grpc::Status SetRemoteDescription(
      grpc::ServerContext* context,
      const call::SetRemoteDescriptionRequest* request,
      call::Empty* response) override;

  // ICE handling
  grpc::Status AddICECandidate(grpc::ServerContext* context,
                               const call::AddICECandidateRequest* request,
                               call::Empty* response) override;

  grpc::Status StreamEvents(grpc::ServerContext* context,
                            const call::StreamEventsRequest* request,
                            grpc::ServerWriter<call::CallEvent>* writer) override;

  // Device management
  grpc::Status ListAudioDevices(grpc::ServerContext* context,
                                const call::Empty* request,
                                call::ListAudioDevicesResponse* response) override;

  grpc::Status ListVideoDevices(grpc::ServerContext* context,
                                const call::Empty* request,
                                call::ListVideoDevicesResponse* response) override;

  // Runtime control
  grpc::Status SetMute(grpc::ServerContext* context,
                       const call::SetMuteRequest* request,
                       call::Empty* response) override;

  grpc::Status GetStats(grpc::ServerContext* context,
                        const call::GetStatsRequest* request,
                        call::GetStatsResponse* response) override;

  grpc::Status Heartbeat(grpc::ServerContext* context,
                         const call::Empty* request,
                         call::Empty* response) override;

  grpc::Status Shutdown(grpc::ServerContext* context,
                        const call::Empty* request,
                        call::Empty* response) override;

  // Server control
  void RequestShutdown();
  bool IsShutdownRequested() const;

  // Heartbeat monitoring
  void StartHeartbeatMonitor();
  void StopHeartbeatMonitor();

 private:
  // Get session by ID (thread-safe)
  std::shared_ptr<Session> GetSession(const std::string& session_id);

  // Heartbeat monitor thread function
  void MonitorHeartbeat();

  // Session storage
  std::unordered_map<std::string, std::shared_ptr<Session>> sessions_;
  mutable std::mutex sessions_mutex_;

  // Pending remote ICE candidates (queued before session created)
  // sessionID → vector of candidate strings
  std::unordered_map<std::string, std::vector<std::string>> pending_remote_candidates_;
  mutable std::mutex pending_candidates_mutex_;

  // Shutdown flag
  std::atomic<bool> shutdown_requested_{false};

  // Heartbeat monitoring (following Go service pattern)
  // Exits process if no heartbeat for 10s (Python likely crashed)
  std::chrono::steady_clock::time_point last_heartbeat_;
  mutable std::mutex heartbeat_mutex_;
  std::thread heartbeat_monitor_thread_;
  std::atomic<bool> monitor_running_{false};
};

}  // namespace drunk_call
