#include "call_server.h"

#include "device_manager.h"
#include "logger.h"
#include "stats_collector.h"

namespace drunk_call {

CallServer::CallServer() {
  LOG_INFO("CallServer created");
}

CallServer::~CallServer() {
  LOG_INFO("CallServer destroyed");
}

std::shared_ptr<Session> CallServer::GetSession(const std::string& session_id) {
  std::lock_guard<std::mutex> lock(sessions_mutex_);
  auto it = sessions_.find(session_id);
  if (it != sessions_.end()) {
    return it->second;
  }
  return nullptr;
}

// ============================================================================
// Service Lifecycle
// ============================================================================

grpc::Status CallServer::Heartbeat(grpc::ServerContext* context,
                                    const call::Empty* request,
                                    call::Empty* response) {
  LOG_DEBUG("Heartbeat received");
  return grpc::Status::OK;
}

grpc::Status CallServer::Shutdown(grpc::ServerContext* context,
                                   const call::Empty* request,
                                   call::Empty* response) {
  LOG_INFO("Shutdown RPC received, exiting gracefully");
  RequestShutdown();
  return grpc::Status::OK;
}

void CallServer::RequestShutdown() {
  shutdown_requested_.store(true);
}

bool CallServer::IsShutdownRequested() const {
  return shutdown_requested_.load();
}

// ============================================================================
// Session Management (stubbed)
// ============================================================================

grpc::Status CallServer::CreateSession(
    grpc::ServerContext* context,
    const call::CreateSessionRequest* request,
    call::CreateSessionResponse* response) {
  LOG_INFO("CreateSession: session_id={}, peer_jid={}, camera={}",
           request->session_id(), request->peer_jid(),
           request->camera_device().empty() ? "none" : request->camera_device());

  // Build session config
  Session::Config config;
  config.session_id = request->session_id();
  config.peer_jid = request->peer_jid();
  config.microphone_device = request->microphone_device();
  config.speakers_device = request->speakers_device();
  config.camera_device = request->camera_device();
  config.proxy_host = request->proxy_host();
  config.proxy_port = request->proxy_port();
  config.proxy_username = request->proxy_username();
  config.proxy_password = request->proxy_password();
  config.proxy_type = request->proxy_type();
  config.turn_server = request->turn_server();
  config.turn_username = request->turn_username();
  config.turn_password = request->turn_password();
  config.relay_only = request->relay_only();
  config.echo_cancel = request->echo_cancel();
  config.echo_suppression_level = request->echo_suppression_level();
  config.noise_suppression = request->noise_suppression();
  config.noise_suppression_level = request->noise_suppression_level();
  config.gain_control = request->gain_control();
  config.offer_has_bundle = request->offer_has_bundle();

  // Create session
  auto session = std::make_shared<Session>(config);
  if (!session->Initialize()) {
    response->set_success(false);
    response->set_error("Failed to initialize session");
    LOG_ERROR("Failed to initialize session {}", request->session_id());
    return grpc::Status::OK;
  }

  // Store session
  {
    std::lock_guard<std::mutex> lock(sessions_mutex_);
    sessions_[request->session_id()] = session;
  }

  response->set_success(true);
  LOG_INFO("Session {} created successfully", request->session_id());
  return grpc::Status::OK;
}

grpc::Status CallServer::EndSession(grpc::ServerContext* context,
                                     const call::EndSessionRequest* request,
                                     call::Empty* response) {
  LOG_INFO("EndSession: session_id={}", request->session_id());

  auto session = GetSession(request->session_id());
  if (!session) {
    LOG_WARN("EndSession: Session {} not found", request->session_id());
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
  }

  session->Close();

  {
    std::lock_guard<std::mutex> lock(sessions_mutex_);
    sessions_.erase(request->session_id());
  }

  LOG_INFO("Session {} ended", request->session_id());
  return grpc::Status::OK;
}

// ============================================================================
// SDP Negotiation (stubbed)
// ============================================================================

grpc::Status CallServer::CreateOffer(grpc::ServerContext* context,
                                      const call::CreateOfferRequest* request,
                                      call::SDPResponse* response) {
  LOG_INFO("CreateOffer: session_id={}", request->session_id());

  auto session = GetSession(request->session_id());
  if (!session) {
    response->set_error("Session not found");
    return grpc::Status::OK;
  }

  std::string sdp = session->CreateOffer();
  response->set_sdp(sdp);
  LOG_DEBUG("CreateOffer: Generated SDP ({} bytes)", sdp.size());
  return grpc::Status::OK;
}

grpc::Status CallServer::CreateAnswer(grpc::ServerContext* context,
                                       const call::CreateAnswerRequest* request,
                                       call::SDPResponse* response) {
  LOG_INFO("CreateAnswer: session_id={}, offer_has_bundle={}",
           request->session_id(), request->offer_has_bundle());

  auto session = GetSession(request->session_id());
  if (!session) {
    response->set_error("Session not found");
    return grpc::Status::OK;
  }

  std::string sdp = session->CreateAnswer(request->remote_sdp(),
                                          request->offer_has_bundle());
  response->set_sdp(sdp);
  LOG_DEBUG("CreateAnswer: Generated SDP ({} bytes)", sdp.size());
  return grpc::Status::OK;
}

grpc::Status CallServer::SetRemoteDescription(
    grpc::ServerContext* context,
    const call::SetRemoteDescriptionRequest* request,
    call::Empty* response) {
  LOG_INFO("SetRemoteDescription: session_id={}, type={}",
           request->session_id(), request->sdp_type());

  auto session = GetSession(request->session_id());
  if (!session) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
  }

  if (!session->SetRemoteDescription(request->remote_sdp(), request->sdp_type())) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "SetRemoteDescription failed");
  }

  LOG_DEBUG("SetRemoteDescription succeeded");
  return grpc::Status::OK;
}

// ============================================================================
// ICE Handling (stubbed)
// ============================================================================

grpc::Status CallServer::AddICECandidate(
    grpc::ServerContext* context,
    const call::AddICECandidateRequest* request,
    call::Empty* response) {
  LOG_DEBUG("AddICECandidate: session_id={}, mid={}, mline={}",
            request->session_id(), request->sdp_mid(), request->sdp_mline_index());

  auto session = GetSession(request->session_id());
  if (!session) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
  }

  if (!session->AddICECandidate(request->candidate(), request->sdp_mid(),
                                 request->sdp_mline_index())) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "AddICECandidate failed");
  }

  return grpc::Status::OK;
}

grpc::Status CallServer::StreamEvents(
    grpc::ServerContext* context,
    const call::StreamEventsRequest* request,
    grpc::ServerWriter<call::CallEvent>* writer) {
  LOG_INFO("StreamEvents: session_id={}", request->session_id());

  auto session = GetSession(request->session_id());
  if (!session) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
  }

  // TODO: Implement event streaming (ICE candidates, connection state changes)
  // For now, just keep the stream open and return when session ends

  LOG_INFO("StreamEvents stream closed for session {}", request->session_id());
  return grpc::Status::OK;
}

// ============================================================================
// Device Management (stubbed)
// ============================================================================

grpc::Status CallServer::ListAudioDevices(
    grpc::ServerContext* context,
    const call::Empty* request,
    call::ListAudioDevicesResponse* response) {
  LOG_DEBUG("ListAudioDevices called");

  auto device_manager = DeviceManager::Create();
  auto devices = device_manager->ListAudioDevices();

  for (const auto& dev : devices) {
    auto* proto_dev = response->add_devices();
    proto_dev->set_name(dev.name);
    proto_dev->set_description(dev.description);
    proto_dev->set_device_class(dev.device_class);
  }

  LOG_INFO("ListAudioDevices: Found {} devices", devices.size());
  return grpc::Status::OK;
}

grpc::Status CallServer::ListVideoDevices(
    grpc::ServerContext* context,
    const call::Empty* request,
    call::ListVideoDevicesResponse* response) {
  LOG_DEBUG("ListVideoDevices called");

  auto device_manager = DeviceManager::Create();
  auto devices = device_manager->ListVideoDevices();

  for (const auto& dev : devices) {
    auto* proto_dev = response->add_devices();
    proto_dev->set_device_path(dev.device_path);
    proto_dev->set_name(dev.name);
    proto_dev->set_driver(dev.driver);
    proto_dev->set_bus_info(dev.bus_info);
  }

  LOG_INFO("ListVideoDevices: Found {} devices", devices.size());
  return grpc::Status::OK;
}

// ============================================================================
// Runtime Control (stubbed)
// ============================================================================

grpc::Status CallServer::SetMute(grpc::ServerContext* context,
                                  const call::SetMuteRequest* request,
                                  call::Empty* response) {
  LOG_INFO("SetMute: session_id={}, muted={}", request->session_id(), request->muted());

  auto session = GetSession(request->session_id());
  if (!session) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
  }

  session->SetMute(request->muted());
  return grpc::Status::OK;
}

grpc::Status CallServer::GetStats(grpc::ServerContext* context,
                                   const call::GetStatsRequest* request,
                                   call::GetStatsResponse* response) {
  LOG_DEBUG("GetStats: session_id={}", request->session_id());

  auto session = GetSession(request->session_id());
  if (!session) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
  }

  auto stats = session->GetStats();

  response->set_connection_state(stats.connection_state);
  response->set_ice_connection_state(stats.ice_connection_state);
  response->set_ice_gathering_state(stats.ice_gathering_state);
  response->set_bytes_sent(stats.bytes_sent);
  response->set_bytes_received(stats.bytes_received);
  response->set_bandwidth_kbps(stats.bandwidth_kbps);
  response->set_connection_type(stats.connection_type);

  for (const auto& candidate : stats.local_candidates) {
    response->add_local_candidates(candidate);
  }
  for (const auto& candidate : stats.remote_candidates) {
    response->add_remote_candidates(candidate);
  }

  return grpc::Status::OK;
}

}  // namespace drunk_call
