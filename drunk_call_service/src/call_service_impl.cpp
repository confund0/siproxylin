/**
 * CallService gRPC Implementation (Phase 4.2)
 *
 * Phase 4.2: Stub implementations returning UNIMPLEMENTED
 * Phase 4.3+: Implement actual logic per docs/CALLS/4-GRPC-PLAN.md
 *
 * LOGGING: Use LOG_*() macros (logger.h) for all application logging
 *          DO NOT use std::cout/std::cerr in production code
 *          See: docs/LOGGING-POLICY.md
 */

#include "call_service_impl.h"
#include "logger.h"
#include "media_session.h"  // For DeviceEnumerator

// Global shutdown flag (defined in main.cpp, global namespace)
extern std::atomic<bool> g_shutdown_requested;

namespace drunk_call {

CallServiceImpl::CallServiceImpl()
    : shutdown_requested_(false) {
    LOG_INFO("CallServiceImpl initialized");
}

CallServiceImpl::~CallServiceImpl() {
    LOG_INFO("CallServiceImpl destroyed");
}

// ============================================================================
// Session Lifecycle
// ============================================================================

grpc::Status CallServiceImpl::CreateSession(
    grpc::ServerContext* context,
    const call::CreateSessionRequest* request,
    call::CreateSessionResponse* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: CreateSession - session_id={}", session_id);
    LOG_INFO("CreateSession: session_id={}, peer={}", session_id, request->peer_jid());

    try {
        // Create session
        auto session = std::make_shared<CallSession>();
        session->session_id = session_id;
        session->peer_jid = request->peer_jid();
        session->event_queue = std::make_shared<ThreadSafeQueue<call::CallEvent>>();
        session->active = true;
        session->created_at = std::chrono::steady_clock::now();

        // Create WebRTC session
        session->webrtc = std::make_unique<WebRTCSession>();

        // Configure session
        SessionConfig config;
        config.session_id = session_id;
        config.microphone_device = request->microphone_device();
        config.speakers_device = request->speakers_device();
        config.relay_only = request->relay_only();

        // Proxy config
        if (!request->proxy_host().empty()) {
            config.proxy_host = request->proxy_host();
            config.proxy_port = request->proxy_port();
            config.proxy_username = request->proxy_username();
            config.proxy_password = request->proxy_password();
            config.proxy_type = request->proxy_type();
            LOG_DEBUG("Session {}: Proxy configured: {}:{} ({})",
                     session_id, config.proxy_host, config.proxy_port, config.proxy_type);
        }

        // TURN config
        if (!request->turn_server().empty()) {
            // Build TURN URL: turn://username:password@host:port
            // NOTE: Username and password should be URL-encoded by Python before sending
            std::string turn_url = "turn://";
            if (!request->turn_username().empty()) {
                turn_url += request->turn_username();
                if (!request->turn_password().empty()) {
                    turn_url += ":" + request->turn_password();
                }
                turn_url += "@";
            }
            turn_url += request->turn_server();
            config.turn_servers.push_back(turn_url);
            LOG_DEBUG("Session {}: TURN server configured: {}", session_id, turn_url);
        }

        // Audio processing
        config.echo_cancel = request->echo_cancel();
        config.noise_suppression = request->noise_suppression();
        config.gain_control = request->gain_control();

        // Video configuration
        if (request->enable_video_receive()) {
            config.video_udp_host = request->video_udp_host();
            config.video_udp_port = request->video_udp_port();
            LOG_INFO("Session {}: Video enabled - UDP {}:{}", session_id, config.video_udp_host, config.video_udp_port);
        }

        // Set callbacks (BEFORE initialize to avoid race)
        // CRITICAL: Use weak_ptr to avoid circular reference leak!
        // session owns webrtc (unique_ptr), webrtc stores callback (std::function),
        // callback captures session → circular reference → memory leak
        // Solution: Capture weak_ptr, lock() in callback to get shared_ptr
        std::weak_ptr<CallSession> weak_session = session;

        // ICE candidate callback - fires in GLib thread, pushes to queue
        session->webrtc->set_ice_candidate_callback([weak_session](const ICECandidate& cand) {
            // THIS RUNS IN GLIB THREAD!
            auto session = weak_session.lock();
            if (!session) {
                // Session destroyed, ignore callback
                return;
            }

            call::CallEvent event;
            event.set_session_id(session->session_id);
            auto* ice_event = event.mutable_ice_candidate();
            ice_event->set_candidate(cand.candidate);
            ice_event->set_sdp_mid(cand.sdp_mid);
            ice_event->set_sdp_mline_index(cand.sdp_mline_index);
            session->event_queue->push(event);
            LOG_DEBUG("Session {}: ICE candidate pushed to queue", session->session_id);
        });

        // State callback - fires in GLib thread, pushes to queue
        session->webrtc->set_state_callback([weak_session](MediaSession::ConnectionState state) {
            // THIS RUNS IN GLIB THREAD!
            auto session = weak_session.lock();
            if (!session) {
                // Session destroyed, ignore callback
                return;
            }

            call::CallEvent event;
            event.set_session_id(session->session_id);
            auto* state_event = event.mutable_connection_state();

            // Map connection state to proto enum
            switch (state) {
                case MediaSession::ConnectionState::NEW:
                    state_event->set_state(call::ConnectionStateEvent::NEW);
                    break;
                case MediaSession::ConnectionState::CHECKING:
                    state_event->set_state(call::ConnectionStateEvent::CHECKING);
                    break;
                case MediaSession::ConnectionState::CONNECTED:
                case MediaSession::ConnectionState::COMPLETED:
                    state_event->set_state(call::ConnectionStateEvent::CONNECTED);
                    break;
                case MediaSession::ConnectionState::FAILED:
                    state_event->set_state(call::ConnectionStateEvent::FAILED);
                    break;
                case MediaSession::ConnectionState::DISCONNECTED:
                    state_event->set_state(call::ConnectionStateEvent::DISCONNECTED);
                    break;
                case MediaSession::ConnectionState::CLOSED:
                    state_event->set_state(call::ConnectionStateEvent::CLOSED);
                    break;
            }

            session->event_queue->push(event);
            LOG_DEBUG("Session {}: State change pushed to queue: {}",
                     session->session_id, static_cast<int>(state));
        });

        // Initialize WebRTC session
        LOG_DEBUG("Session {}: Calling webrtc->initialize()", session_id);
        if (!session->webrtc->initialize(config)) {
            LOG_ERROR("Session {}: Failed to initialize WebRTC session", session_id);
            response->set_success(false);
            response->set_error("Failed to initialize WebRTC session");
            return grpc::Status::OK;
        }
        LOG_DEBUG("Session {}: initialize() succeeded", session_id);

        // Start WebRTC pipeline (set to PLAYING state)
        LOG_DEBUG("Session {}: Calling webrtc->start()", session_id);
        if (!session->webrtc->start()) {
            LOG_ERROR("Session {}: Failed to start WebRTC pipeline", session_id);
            response->set_success(false);
            response->set_error("Failed to start WebRTC pipeline");
            return grpc::Status::OK;
        }
        LOG_DEBUG("Session {}: start() succeeded", session_id);

        // Add to SessionManager (atomic check-and-add)
        LOG_DEBUG("Session {}: Adding to SessionManager", session_id);
        if (!session_manager_.try_add_session(session_id, session)) {
            LOG_WARN("Session already exists: {}, cleaning up orphaned pipeline", session_id);
            // CRITICAL: Stop the pipeline we just started to prevent resource leak
            if (session->webrtc) {
                session->webrtc->stop();
            }
            response->set_success(false);
            response->set_error("Session already exists");
            return grpc::Status::OK;
        }
        LOG_DEBUG("Session {}: Added to SessionManager successfully", session_id);

        LOG_INFO("Session created successfully: {}, peer: {}", session_id, request->peer_jid());
        response->set_success(true);
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in CreateSession: {}", e.what());
        response->set_success(false);
        response->set_error(std::string("Exception: ") + e.what());
        return grpc::Status::OK;
    }
}

grpc::Status CallServiceImpl::EndSession(
    grpc::ServerContext* context,
    const call::EndSessionRequest* request,
    call::Empty* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: EndSession - session_id={}", session_id);
    LOG_INFO("EndSession: session_id={}", session_id);

    // Get session from SessionManager
    auto session = session_manager_.get_session(session_id);
    if (!session) {
        LOG_WARN("EndSession: Session not found (already ended?): {}", session_id);
        return grpc::Status::OK;  // Not an error - session may have already ended
    }

    // Calculate session duration
    auto duration = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::steady_clock::now() - session->created_at
    ).count();

    // Mark session as inactive (stops StreamEvents loop)
    session->active = false;

    // Shutdown event queue (wakes any blocked StreamEvents threads)
    session->event_queue->shutdown();

    // Stop WebRTC session
    if (session->webrtc) {
        session->webrtc->stop();
    }

    // Remove from SessionManager
    // Note: StreamEvents may still hold a shared_ptr - that's OK!
    // Session will be destroyed when the last shared_ptr is released
    session_manager_.remove_session(session_id);

    LOG_INFO("Session ended: {}, duration: {}s", session_id, duration);
    return grpc::Status::OK;
}

// ============================================================================
// SDP Operations
// ============================================================================

grpc::Status CallServiceImpl::CreateOffer(
    grpc::ServerContext* context,
    const call::CreateOfferRequest* request,
    call::SDPResponse* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: CreateOffer - session_id={}", session_id);
    LOG_INFO("CreateOffer: session_id={}", session_id);

    try {
        // Get session from SessionManager
        auto session = session_manager_.get_session(session_id);
        if (!session) {
            LOG_ERROR("CreateOffer: Session not found: {}", session_id);
            response->set_error("Session not found");
            return grpc::Status::OK;
        }

        // Pattern 1: gRPC → GLib → gRPC (using condition variable)
        // CRITICAL: Use shared_ptr to keep state alive even if function returns early
        // (e.g., on timeout or if session ends). The GLib callback may execute after
        // this function returns, so stack variables would be destroyed -> crash!
        struct SDPCallbackState {
            std::mutex mutex;
            std::condition_variable cv;
            bool ready = false;
            SDPMessage sdp;
            std::string error;
        };
        auto state = std::make_shared<SDPCallbackState>();

        // Set SDP callback (will be called from GLib thread)
        // Note: Capturing session_id (copy) is safe, no circular reference
        session->webrtc->create_offer([state, session_id](bool success, const SDPMessage& sdp, const std::string& error) {
            // THIS RUNS IN GLIB THREAD!
            std::lock_guard<std::mutex> lock(state->mutex);
            if (success) {
                state->sdp = sdp;
                LOG_DEBUG("CreateOffer: SDP generated for session {}", session_id);
            } else {
                state->error = error;
                LOG_ERROR("CreateOffer: Failed to generate SDP for session {}: {}", session_id, error);
            }
            state->ready = true;
            state->cv.notify_one();
        });

        // Wait for callback (max 10s timeout)
        std::unique_lock<std::mutex> lock(state->mutex);
        if (!state->cv.wait_for(lock, std::chrono::seconds(10), [&]{ return state->ready; })) {
            LOG_ERROR("CreateOffer: Timeout waiting for SDP generation: {}", session_id);
            response->set_error("Timeout waiting for SDP generation");
            return grpc::Status::OK;
        }

        // Check result
        if (!state->error.empty()) {
            response->set_error(state->error);
            return grpc::Status::OK;
        }

        // Return SDP in response
        response->set_sdp(state->sdp.sdp_text);
        LOG_INFO("CreateOffer: Success, session={}, sdp_size={} bytes",
                 session_id, state->sdp.sdp_text.size());
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in CreateOffer: {}", e.what());
        response->set_error(std::string("Exception: ") + e.what());
        return grpc::Status::OK;
    }
}

grpc::Status CallServiceImpl::CreateAnswer(
    grpc::ServerContext* context,
    const call::CreateAnswerRequest* request,
    call::SDPResponse* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: CreateAnswer - session_id={}", session_id);
    LOG_INFO("CreateAnswer: session_id={}, remote_sdp_size={} bytes",
             session_id, request->remote_sdp().size());

    try {
        // Get session from SessionManager
        auto session = session_manager_.get_session(session_id);
        if (!session) {
            LOG_ERROR("CreateAnswer: Session not found: {}", session_id);
            response->set_error("Session not found");
            return grpc::Status::OK;
        }

        // Parse remote SDP (offer) from request
        SDPMessage remote_offer(SDPMessage::Type::OFFER, request->remote_sdp());

        // Pattern 1: gRPC → GLib → gRPC (using condition variable)
        // CRITICAL: Use shared_ptr to keep state alive even if function returns early
        // (e.g., on timeout or if session ends). The GLib callback may execute after
        // this function returns, so stack variables would be destroyed -> crash!
        struct SDPCallbackState {
            std::mutex mutex;
            std::condition_variable cv;
            bool ready = false;
            SDPMessage sdp;
            std::string error;
        };
        auto state = std::make_shared<SDPCallbackState>();

        // Set SDP callback and create answer (will be called from GLib thread)
        LOG_INFO("GRPC DEBUG: About to call session->webrtc->create_answer() for session {}", session_id);
        session->webrtc->create_answer(remote_offer, [state, session_id](bool success, const SDPMessage& sdp, const std::string& error) {
            // THIS RUNS IN GLIB THREAD!
            std::lock_guard<std::mutex> lock(state->mutex);
            if (success) {
                state->sdp = sdp;
                LOG_DEBUG("CreateAnswer: SDP generated for session {}", session_id);
            } else {
                state->error = error;
                LOG_ERROR("CreateAnswer: Failed to generate SDP for session {}: {}", session_id, error);
            }
            state->ready = true;
            state->cv.notify_one();
        });

        // Wait for callback (max 10s timeout)
        std::unique_lock<std::mutex> lock(state->mutex);
        if (!state->cv.wait_for(lock, std::chrono::seconds(10), [&]{ return state->ready; })) {
            LOG_ERROR("CreateAnswer: Timeout waiting for SDP generation: {}", session_id);
            response->set_error("Timeout waiting for SDP generation");
            return grpc::Status::OK;
        }

        // Check result
        if (!state->error.empty()) {
            response->set_error(state->error);
            return grpc::Status::OK;
        }

        // Return SDP in response
        response->set_sdp(state->sdp.sdp_text);
        LOG_INFO("CreateAnswer: Success, session={}, sdp_size={} bytes",
                 session_id, state->sdp.sdp_text.size());
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in CreateAnswer: {}", e.what());
        response->set_error(std::string("Exception: ") + e.what());
        return grpc::Status::OK;
    }
}

grpc::Status CallServiceImpl::SetRemoteDescription(
    grpc::ServerContext* context,
    const call::SetRemoteDescriptionRequest* request,
    call::Empty* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: SetRemoteDescription - session_id={}, type={}",
              session_id, request->sdp_type());
    LOG_INFO("SetRemoteDescription: session_id={}, type={}, sdp_size={} bytes",
             session_id, request->sdp_type(), request->remote_sdp().size());

    try {
        // Get session from SessionManager
        auto session = session_manager_.get_session(session_id);
        if (!session) {
            LOG_ERROR("SetRemoteDescription: Session not found: {}", session_id);
            return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
        }

        // Parse remote SDP type
        SDPMessage::Type sdp_type;
        std::string type_str = request->sdp_type();
        if (type_str == "offer") {
            sdp_type = SDPMessage::Type::OFFER;
        } else if (type_str == "answer") {
            sdp_type = SDPMessage::Type::ANSWER;
        } else {
            LOG_ERROR("SetRemoteDescription: Invalid SDP type: {}", type_str);
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                              "Invalid SDP type (must be 'offer' or 'answer')");
        }

        // Create SDPMessage
        SDPMessage remote_sdp(sdp_type, request->remote_sdp());

        // Set remote description (synchronous call, no callback needed)
        if (!session->webrtc->set_remote_description(remote_sdp)) {
            LOG_ERROR("SetRemoteDescription: Failed to set remote description: {}", session_id);
            return grpc::Status(grpc::StatusCode::INTERNAL,
                              "Failed to set remote description");
        }

        LOG_INFO("SetRemoteDescription: Success, session={}", session_id);
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in SetRemoteDescription: {}", e.what());
        return grpc::Status(grpc::StatusCode::INTERNAL,
                          std::string("Exception: ") + e.what());
    }
}

// ============================================================================
// ICE Candidate Handling
// ============================================================================

grpc::Status CallServiceImpl::AddICECandidate(
    grpc::ServerContext* context,
    const call::AddICECandidateRequest* request,
    call::Empty* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: AddICECandidate - session_id={}, mid={}, mline_index={}",
              session_id, request->sdp_mid(), request->sdp_mline_index());
    LOG_INFO("AddICECandidate: session_id={}, candidate_length={} bytes",
             session_id, request->candidate().size());

    try {
        // Get session from SessionManager
        auto session = session_manager_.get_session(session_id);
        if (!session) {
            LOG_ERROR("AddICECandidate: Session not found: {}", session_id);
            return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
        }

        // Create ICECandidate struct from request
        ICECandidate candidate;
        candidate.candidate = request->candidate();
        candidate.sdp_mid = request->sdp_mid();
        candidate.sdp_mline_index = static_cast<uint32_t>(request->sdp_mline_index());

        // Add remote ICE candidate to WebRTC session
        if (!session->webrtc->add_remote_ice_candidate(candidate)) {
            LOG_ERROR("AddICECandidate: Failed to add candidate: {}", session_id);
            return grpc::Status(grpc::StatusCode::INTERNAL,
                              "Failed to add ICE candidate");
        }

        LOG_DEBUG("AddICECandidate: Success, session={}", session_id);
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in AddICECandidate: {}", e.what());
        return grpc::Status(grpc::StatusCode::INTERNAL,
                          std::string("Exception: ") + e.what());
    }
}

// ============================================================================
// Event Streaming (C++ → Python)
// ============================================================================

grpc::Status CallServiceImpl::StreamEvents(
    grpc::ServerContext* context,
    const call::StreamEventsRequest* request,
    grpc::ServerWriter<call::CallEvent>* writer) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: StreamEvents - session_id={}", session_id);
    LOG_INFO("StreamEvents started: session_id={}", session_id);

    // Get session from SessionManager
    auto session = session_manager_.get_session(session_id);
    if (!session) {
        LOG_ERROR("StreamEvents: Session not found: {}", session_id);
        return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
    }

    // Stream events until session ends or client disconnects
    int event_count = 0;
    while (session->active && !context->IsCancelled()) {
        call::CallEvent event;

        // Pop from queue with 1s timeout (allows checking cancellation)
        if (session->event_queue->pop(event, std::chrono::milliseconds(1000))) {
            // Write event to stream
            if (!writer->Write(event)) {
                LOG_WARN("StreamEvents: Failed to write event to stream (client disconnected?): {}",
                        session_id);
                break;
            }
            event_count++;
            LOG_DEBUG("StreamEvents: Event #{} sent to client: {}", event_count, session_id);
        }
        // Timeout is OK - just loop and check cancellation
    }

    if (context->IsCancelled()) {
        LOG_INFO("StreamEvents cancelled by client: {}, events sent: {}",
                session_id, event_count);
    } else {
        LOG_INFO("StreamEvents completed: {}, events sent: {}", session_id, event_count);
    }

    return grpc::Status::OK;
}

// ============================================================================
// Audio Device Management
// ============================================================================

grpc::Status CallServiceImpl::ListAudioDevices(
    grpc::ServerContext* context,
    const call::Empty* request,
    call::ListAudioDevicesResponse* response) {

    LOG_DEBUG("gRPC: ListAudioDevices");
    LOG_INFO("ListAudioDevices: Enumerating audio devices");

    try {
        // Get audio input devices (microphones)
        auto inputs = DeviceEnumerator::list_audio_inputs();
        LOG_DEBUG("ListAudioDevices: Found {} input device(s)", inputs.size());

        for (const auto& device : inputs) {
            auto* proto_device = response->add_devices();
            proto_device->set_name(device.id);
            proto_device->set_description(device.description);
            proto_device->set_device_class("Audio/Source");
            LOG_DEBUG("  → Input: name='{}', description='{}', class='{}'",
                     device.id, device.description, "Audio/Source");
        }

        // Get audio output devices (speakers)
        auto outputs = DeviceEnumerator::list_audio_outputs();
        LOG_DEBUG("ListAudioDevices: Found {} output device(s)", outputs.size());

        for (const auto& device : outputs) {
            auto* proto_device = response->add_devices();
            proto_device->set_name(device.id);
            proto_device->set_description(device.description);
            proto_device->set_device_class("Audio/Sink");
            LOG_DEBUG("  → Output: name='{}', description='{}', class='{}'",
                     device.id, device.description, "Audio/Sink");
        }

        LOG_INFO("ListAudioDevices: Success, total devices: {}",
                 inputs.size() + outputs.size());
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in ListAudioDevices: {}", e.what());
        return grpc::Status(grpc::StatusCode::INTERNAL,
                          std::string("Exception: ") + e.what());
    }
}

grpc::Status CallServiceImpl::SetMute(
    grpc::ServerContext* context,
    const call::SetMuteRequest* request,
    call::Empty* response) {

    std::string session_id = request->session_id();
    bool muted = request->muted();
    LOG_DEBUG("gRPC: SetMute - session_id={}, muted={}", session_id, muted);
    LOG_INFO("SetMute: session_id={}, muted={}", session_id, muted);

    try {
        // Get session from SessionManager
        auto session = session_manager_.get_session(session_id);
        if (!session) {
            LOG_ERROR("SetMute: Session not found: {}", session_id);
            return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
        }

        // Set mute state on WebRTC session
        if (!session->webrtc->set_mute(muted)) {
            LOG_ERROR("SetMute: Failed to set mute state: {}", session_id);
            return grpc::Status(grpc::StatusCode::INTERNAL,
                              "Failed to set mute state");
        }

        LOG_INFO("SetMute: Success, session={}, muted={}", session_id, muted);
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in SetMute: {}", e.what());
        return grpc::Status(grpc::StatusCode::INTERNAL,
                          std::string("Exception: ") + e.what());
    }
}

// ============================================================================
// Statistics
// ============================================================================

grpc::Status CallServiceImpl::GetStats(
    grpc::ServerContext* context,
    const call::GetStatsRequest* request,
    call::GetStatsResponse* response) {

    std::string session_id = request->session_id();
    LOG_DEBUG("gRPC: GetStats - session_id={}", session_id);
    LOG_INFO("GetStats: session_id={}", session_id);

    try {
        // Get session from SessionManager
        auto session = session_manager_.get_session(session_id);
        if (!session) {
            LOG_ERROR("GetStats: Session not found: {}", session_id);
            return grpc::Status(grpc::StatusCode::NOT_FOUND, "Session not found");
        }

        // Get statistics from WebRTC session
        auto stats = session->webrtc->get_stats();

        // Populate response with stats
        response->set_connection_state(stats.connection_state);
        response->set_ice_connection_state(stats.ice_connection_state);
        response->set_ice_gathering_state(stats.ice_gathering_state);
        response->set_bytes_sent(stats.bytes_sent);
        response->set_bytes_received(stats.bytes_received);
        response->set_bandwidth_kbps(stats.bandwidth_kbps);
        response->set_connection_type(stats.connection_type);

        // Copy local candidates
        for (const auto& candidate : stats.local_candidates) {
            response->add_local_candidates(candidate);
        }

        // Copy remote candidates
        for (const auto& candidate : stats.remote_candidates) {
            response->add_remote_candidates(candidate);
        }

        LOG_DEBUG("GetStats: Success, session={}, ice_state={}, bandwidth={}kbps",
                 session_id, stats.ice_connection_state, stats.bandwidth_kbps);
        return grpc::Status::OK;

    } catch (const std::exception& e) {
        LOG_ERROR("Exception in GetStats: {}", e.what());
        return grpc::Status(grpc::StatusCode::INTERNAL,
                          std::string("Exception: ") + e.what());
    }
}

// ============================================================================
// Service Management
// ============================================================================

grpc::Status CallServiceImpl::Heartbeat(
    grpc::ServerContext* context,
    const call::Empty* request,
    call::Empty* response) {

    // Heartbeat: Minimal logging (called every 5s from Python)
    // Only log at TRACE level to avoid spam
    LOG_TRACE("gRPC: Heartbeat");
    return grpc::Status::OK;
}

grpc::Status CallServiceImpl::Shutdown(
    grpc::ServerContext* context,
    const call::Empty* request,
    call::Empty* response) {

    LOG_DEBUG("gRPC: Shutdown");
    LOG_INFO("Shutdown requested via gRPC");

    // Cleanup all sessions
    cleanup_all_sessions();

    // Set global shutdown flag (main() will detect and shutdown)
    ::g_shutdown_requested = true;

    shutdown_requested_ = true;
    return grpc::Status::OK;
}

void CallServiceImpl::cleanup_all_sessions() {
    LOG_INFO("Cleaning up all sessions...");

    // Get all session IDs
    auto session_ids = session_manager_.get_all_session_ids();

    if (session_ids.empty()) {
        LOG_INFO("No active sessions to cleanup");
        return;
    }

    LOG_INFO("Cleaning up {} active session(s)", session_ids.size());

    // Cleanup each session
    for (const auto& session_id : session_ids) {
        auto session = session_manager_.get_session(session_id);
        if (session) {
            LOG_DEBUG("Cleaning up session: {}", session_id);

            // Calculate session duration
            auto duration = std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::steady_clock::now() - session->created_at
            ).count();

            // Mark session as inactive
            session->active = false;

            // Shutdown event queue (unblocks any waiting StreamEvents)
            session->event_queue->shutdown();

            // Stop WebRTC session
            if (session->webrtc) {
                session->webrtc->stop();
            }

            LOG_INFO("Session cleaned up: {}, duration: {}s", session_id, duration);
        }

        // Remove from SessionManager
        session_manager_.remove_session(session_id);
    }

    LOG_INFO("All sessions cleaned up successfully");
}

} // namespace drunk_call
