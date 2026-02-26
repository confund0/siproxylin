#include "ice_agent.h"
#include "logger.h"
#include <nice/debug.h>
#include <cstring>
#include <sstream>

namespace drunk_call {

IceAgent::IceAgent(int n_components)
    : n_components_(n_components),
      initialized_(false),
      relay_only_(false),
      controlling_mode_set_(false),
      agent_(nullptr),
      stream_id_(0),
      thread_context_(nullptr),
      thread_loop_(nullptr),
      shutdown_requested_(false) {
  LOG_DEBUG("IceAgent created with {} components", n_components_);
}

IceAgent::~IceAgent() {
  Shutdown();
  LOG_DEBUG("IceAgent destroyed");
}

bool IceAgent::Initialize() {
  if (initialized_) {
    LOG_WARN("IceAgent already initialized");
    return true;
  }

  LOG_INFO("Initializing IceAgent with {} components", n_components_);

  // Enable libnice debug output (requires NICE_DEBUG and G_MESSAGES_DEBUG env vars)
  nice_debug_enable(TRUE);  // TRUE = include STUN debug messages too
  LOG_INFO("libnice debug enabled (check stderr for libnice output)");

  // Create dedicated GMainContext for ICE thread (like Dino)
  thread_context_ = g_main_context_new();
  if (!thread_context_) {
    LOG_ERROR("Failed to create GMainContext");
    return false;
  }

  // Create NiceAgent with RFC5245 compatibility
  agent_ = nice_agent_new(thread_context_, NICE_COMPATIBILITY_RFC5245);
  if (!agent_) {
    LOG_ERROR("Failed to create NiceAgent");
    g_main_context_unref(thread_context_);
    return false;
  }

  // Configure agent
  g_object_set(agent_, "ice-tcp", FALSE, NULL);  // UDP only

  // Connect signals
  g_signal_connect(agent_, "candidate-gathering-done",
                   G_CALLBACK(OnCandidateGatheringDone), this);
  g_signal_connect(agent_, "new-candidate-full",
                   G_CALLBACK(OnNewCandidateFull), this);
  g_signal_connect(agent_, "component-state-changed",
                   G_CALLBACK(OnComponentStateChanged), this);
  g_signal_connect(agent_, "new-selected-pair-full",
                   G_CALLBACK(OnNewSelectedPairFull), this);

  LOG_INFO("NiceAgent created successfully");

  // Start ICE thread
  ice_thread_ = std::thread(&IceAgent::IceThreadFunc, this);

  initialized_ = true;
  return true;
}

void IceAgent::Shutdown() {
  if (!initialized_) {
    return;
  }

  LOG_INFO("Shutting down IceAgent");

  // Signal shutdown
  shutdown_requested_ = true;

  // Quit the main loop
  if (thread_loop_) {
    g_main_loop_quit(thread_loop_);
  }

  // Wait for thread to finish
  if (ice_thread_.joinable()) {
    ice_thread_.join();
  }

  // Cleanup
  if (agent_) {
    g_object_unref(agent_);
    agent_ = nullptr;
  }

  if (thread_loop_) {
    g_main_loop_unref(thread_loop_);
    thread_loop_ = nullptr;
  }

  if (thread_context_) {
    g_main_context_unref(thread_context_);
    thread_context_ = nullptr;
  }

  initialized_ = false;
  LOG_INFO("IceAgent shutdown complete");
}

bool IceAgent::AddStream() {
  if (!initialized_) {
    LOG_ERROR("IceAgent not initialized");
    return false;
  }

  // CRITICAL: Dino pattern requires SetControllingMode() BEFORE AddStream()
  // See transport_parameters.vala line 97-98
  if (!controlling_mode_set_) {
    LOG_ERROR("CRITICAL: SetControllingMode() must be called BEFORE AddStream()! (Dino pattern requirement)");
    return false;
  }

  LOG_INFO("Adding ICE stream with {} components", n_components_);

  // Create stream with n_components (CRITICAL: 2 for RTP + RTCP)
  stream_id_ = nice_agent_add_stream(agent_, n_components_);
  if (stream_id_ == 0) {
    LOG_ERROR("Failed to add stream to NiceAgent");
    return false;
  }

  LOG_INFO("ICE stream created: stream_id={}, n_components={}", stream_id_, n_components_);

  // Attach receive callbacks for ALL components
  for (int i = 1; i <= n_components_; i++) {
    if (!nice_agent_attach_recv(agent_, stream_id_, i,
                                 g_main_context_ref(thread_context_),
                                 OnRecv, this)) {
      LOG_ERROR("Failed to attach recv callback for component {}", i);
      return false;
    }
    LOG_INFO("✓ Component {} initialized: recv callback attached", i);

    // Initialize component state
    {
      std::lock_guard<std::mutex> lock(state_mutex_);
      component_states_[i] = NICE_COMPONENT_STATE_DISCONNECTED;
    }
  }

  LOG_INFO("All {} components initialized successfully", n_components_);
  return true;
}

void IceAgent::SetStunServer(const std::string& host, uint16_t port) {
  if (!agent_) {
    LOG_ERROR("Agent not initialized");
    return;
  }

  LOG_INFO("Setting STUN server: {}:{}", host, port);
  g_object_set(agent_, "stun-server", host.c_str(), NULL);
  g_object_set(agent_, "stun-server-port", static_cast<guint>(port), NULL);
}

void IceAgent::SetTurnServer(const std::string& host, uint16_t port,
                              const std::string& username, const std::string& password) {
  if (!agent_ || stream_id_ == 0) {
    LOG_ERROR("Agent or stream not initialized");
    return;
  }

  LOG_INFO("Setting TURN server for ALL components: {}:{} (user={})", host, port, username);

  // CRITICAL: Set relay info for BOTH components (like Dino)
  for (int i = 1; i <= n_components_; i++) {
    if (!nice_agent_set_relay_info(agent_, stream_id_, i,
                                    host.c_str(), port,
                                    username.c_str(), password.c_str(),
                                    NICE_RELAY_TYPE_TURN_UDP)) {
      LOG_ERROR("Failed to set TURN relay for component {}", i);
    } else {
      LOG_DEBUG("TURN relay configured for component {}", i);
    }
  }
}

void IceAgent::SetTransportPolicy(bool relay_only) {
  relay_only_ = relay_only;
  LOG_INFO("ICE transport policy: {}", relay_only ? "relay-only" : "all");

  if (agent_) {
    // Note: libnice doesn't have a direct "relay-only" mode like webrtcbin
    // Instead, we can limit candidate types by not gathering host/srflx
    // For now, we log the preference and will handle it in candidate filtering
    LOG_DEBUG("Relay-only mode will be enforced during candidate gathering");
  }
}

void IceAgent::SetControllingMode(bool controlling) {
  if (!agent_) {
    LOG_ERROR("Agent not initialized");
    return;
  }

  if (stream_id_ != 0) {
    LOG_ERROR("CRITICAL: SetControllingMode() must be called BEFORE AddStream()! (Dino pattern requirement)");
    return;
  }

  LOG_INFO("Setting ICE controlling mode: {} (Dino pattern: BEFORE add_stream)", controlling);
  g_object_set(agent_, "controlling-mode", controlling ? TRUE : FALSE, NULL);
  controlling_mode_set_ = true;
}

void IceAgent::SetRemoteCredentials(const std::string& ufrag, const std::string& pwd) {
  if (!agent_ || stream_id_ == 0) {
    LOG_ERROR("Agent or stream not initialized");
    return;
  }

  LOG_INFO("Setting remote ICE credentials: ufrag={}, pwd_len={}", ufrag, pwd.length());

  if (!nice_agent_set_remote_credentials(agent_, stream_id_,
                                          ufrag.c_str(), pwd.c_str())) {
    LOG_ERROR("Failed to set remote credentials");
  } else {
    LOG_INFO("Remote credentials set successfully - ICE connectivity checks should start");
  }
}

std::pair<std::string, std::string> IceAgent::GetLocalCredentials() {
  if (!agent_ || stream_id_ == 0) {
    LOG_ERROR("Agent or stream not initialized");
    return {"", ""};
  }

  gchar* ufrag = nullptr;
  gchar* pwd = nullptr;

  if (!nice_agent_get_local_credentials(agent_, stream_id_, &ufrag, &pwd)) {
    LOG_ERROR("Failed to get local credentials");
    return {"", ""};
  }

  std::string ufrag_str(ufrag);
  std::string pwd_str(pwd);

  g_free(ufrag);
  g_free(pwd);

  LOG_DEBUG("Local ICE credentials: ufrag={}, pwd_len={}", ufrag_str, pwd_str.length());
  return {ufrag_str, pwd_str};
}

void IceAgent::GatherCandidates() {
  if (!agent_ || stream_id_ == 0) {
    LOG_ERROR("Agent or stream not initialized");
    return;
  }

  LOG_INFO("Starting candidate gathering for stream {}", stream_id_);

  if (!nice_agent_gather_candidates(agent_, stream_id_)) {
    LOG_ERROR("Failed to start candidate gathering");
  }
}

bool IceAgent::AddRemoteCandidate(int component_id, const std::string& candidate_str,
                                   const std::string& sdp_mid, int sdp_mline_index) {
  if (!agent_ || stream_id_ == 0) {
    LOG_ERROR("Agent or stream not initialized");
    return false;
  }

  LOG_DEBUG("Adding remote candidate: mid={}, mline={}, cand={}",
            sdp_mid, sdp_mline_index, candidate_str);

  // Parse SDP candidate string to NiceCandidate
  // Format: "candidate:foundation component protocol priority ip port typ type ..."
  // Note: nice_agent_parse_remote_candidate_sdp expects "a=candidate:..." but we might receive just "candidate:..."
  std::string sdp_line = candidate_str;
  if (sdp_line.find("a=") != 0) {
    sdp_line = "a=" + sdp_line;
  }

  NiceCandidate* candidate = nice_agent_parse_remote_candidate_sdp(agent_, stream_id_, sdp_line.c_str());
  if (!candidate) {
    LOG_ERROR("Failed to parse remote candidate: {}", candidate_str);
    return false;
  }

  // IMPORTANT: Use component_id from the PARSED candidate, not the parameter!
  // The component (1=RTP, 2=RTCP) is extracted by libnice from the candidate string
  guint parsed_component_id = candidate->component_id;

  LOG_DEBUG("Parsed candidate component: {}", parsed_component_id);

  // Add to remote candidates list
  GSList* candidates = g_slist_append(nullptr, candidate);
  int added = nice_agent_set_remote_candidates(agent_, stream_id_, parsed_component_id, candidates);
  g_slist_free_full(candidates, (GDestroyNotify)nice_candidate_free);

  if (added < 0) {
    LOG_ERROR("Failed to add remote candidate for component {}", parsed_component_id);
    return false;
  }

  LOG_DEBUG("Added {} remote candidate(s) for component {}", added, parsed_component_id);
  return true;
}

bool IceAgent::Send(int component_id, const uint8_t* data, size_t len) {
  if (!agent_ || stream_id_ == 0) {
    LOG_ERROR("Agent or stream not initialized");
    return false;
  }

  // Use send_messages_nonblocking like Dino (NOT blocking send!)
  // This is critical - blocking send can deadlock with ICE state machine
  GOutputVector vector;
  vector.buffer = data;
  vector.size = len;

  NiceOutputMessage message;
  message.buffers = &vector;
  message.n_buffers = 1;

  GError* error = nullptr;
  gint sent = nice_agent_send_messages_nonblocking(agent_, stream_id_, component_id,
                                                    &message, 1, nullptr, &error);

  if (sent < 0) {
    if (error) {
      LOG_ERROR("Failed to send data on component {}: {}", component_id, error->message);
      g_error_free(error);
    } else {
      LOG_ERROR("Failed to send data on component {}", component_id);
    }
    return false;
  }

  if (sent == 0) {
    LOG_DEBUG("Send would block on component {} (not ready yet)", component_id);
    return false;
  }

  return true;
}

void IceAgent::SetOnCandidateCallback(CandidateCallback callback) {
  std::lock_guard<std::mutex> lock(callback_mutex_);
  on_candidate_ = std::move(callback);
}

void IceAgent::SetOnComponentStateCallback(ComponentStateCallback callback) {
  std::lock_guard<std::mutex> lock(callback_mutex_);
  on_component_state_ = std::move(callback);
}

void IceAgent::SetOnDataReceivedCallback(DataReceivedCallback callback) {
  std::lock_guard<std::mutex> lock(callback_mutex_);
  on_data_received_ = std::move(callback);
}

void IceAgent::SetOnGatheringDoneCallback(GatheringDoneCallback callback) {
  std::lock_guard<std::mutex> lock(callback_mutex_);
  on_gathering_done_ = std::move(callback);
}

bool IceAgent::IsComponentReady(int component_id) const {
  std::lock_guard<std::mutex> lock(state_mutex_);
  auto it = component_states_.find(component_id);
  if (it == component_states_.end()) {
    return false;
  }
  // Dino pattern: Both CONNECTED and READY states are valid for sending data
  // See Dino util.vala is_component_ready()
  return it->second == NICE_COMPONENT_STATE_CONNECTED ||
         it->second == NICE_COMPONENT_STATE_READY;
}

std::string IceAgent::GetComponentState(int component_id) const {
  std::lock_guard<std::mutex> lock(state_mutex_);
  auto it = component_states_.find(component_id);
  if (it == component_states_.end()) {
    return "UNKNOWN";
  }
  return ComponentStateToString(it->second);
}

// Static signal handlers

void IceAgent::OnCandidateGatheringDone(NiceAgent* agent, guint stream_id, gpointer user_data) {
  auto* self = static_cast<IceAgent*>(user_data);
  LOG_INFO("Candidate gathering done for stream {}", stream_id);

  std::lock_guard<std::mutex> lock(self->callback_mutex_);
  if (self->on_gathering_done_) {
    self->on_gathering_done_();
  }
}

void IceAgent::OnNewCandidateFull(NiceAgent* agent, NiceCandidate* candidate, gpointer user_data) {
  auto* self = static_cast<IceAgent*>(user_data);

  std::string candidate_str = self->NiceCandidateToString(candidate);
  LOG_DEBUG("New candidate: component={}, type={}, {}",
            candidate->component_id,
            nice_candidate_type_to_string(candidate->type),
            candidate_str);

  // Filter candidates based on relay-only policy
  if (self->relay_only_ && candidate->type != NICE_CANDIDATE_TYPE_RELAYED) {
    LOG_DEBUG("Filtering non-relay candidate (relay-only mode)");
    return;
  }

  std::lock_guard<std::mutex> lock(self->callback_mutex_);
  if (self->on_candidate_) {
    // CRITICAL: In 2-component ICE (RTP + RTCP), BOTH components belong to the SAME m-line!
    // Component 1 = RTP, Component 2 = RTCP, but both use mline_index=0 for audio
    // Component ID is used by libnice internally, NOT for SDP m-line mapping
    // See Dino: All candidates for same stream go to same m-line
    self->on_candidate_(candidate->component_id, candidate_str,
                        "audio", 0);  // Always mline=0 for audio (single media line)
  }
}

void IceAgent::OnComponentStateChanged(NiceAgent* agent, guint stream_id,
                                        guint component_id, guint state, gpointer user_data) {
  auto* self = static_cast<IceAgent*>(user_data);
  NiceComponentState nice_state = static_cast<NiceComponentState>(state);
  std::string state_str = self->ComponentStateToString(nice_state);

  LOG_INFO("Component {} state changed: {}", component_id, state_str);

  // Update internal state
  {
    std::lock_guard<std::mutex> lock(self->state_mutex_);
    self->component_states_[component_id] = nice_state;
  }

  // Notify callback
  std::lock_guard<std::mutex> lock(self->callback_mutex_);
  if (self->on_component_state_) {
    self->on_component_state_(component_id, state_str);
  }
}

void IceAgent::OnNewSelectedPairFull(NiceAgent* agent, guint stream_id, guint component_id,
                                      NiceCandidate* local, NiceCandidate* remote, gpointer user_data) {
  auto* self = static_cast<IceAgent*>(user_data);

  std::string local_str = self->NiceCandidateToString(local);
  std::string remote_str = self->NiceCandidateToString(remote);

  LOG_INFO("Selected candidate pair for component {}: local={}, remote={}",
           component_id, local_str, remote_str);
}

void IceAgent::OnRecv(NiceAgent* agent, guint stream_id, guint component_id,
                      guint len, gchar* buf, gpointer user_data) {
  auto* self = static_cast<IceAgent*>(user_data);

  LOG_DEBUG("Received {} bytes on component {}", len, component_id);

  std::lock_guard<std::mutex> lock(self->callback_mutex_);
  if (self->on_data_received_) {
    self->on_data_received_(component_id, reinterpret_cast<uint8_t*>(buf), len);
  }
}

// Thread function

void IceAgent::IceThreadFunc() {
  LOG_INFO("ICE thread started");

  // Push thread context as default
  g_main_context_push_thread_default(thread_context_);

  // Create main loop
  thread_loop_ = g_main_loop_new(thread_context_, FALSE);

  // Run loop until quit
  g_main_loop_run(thread_loop_);

  // Cleanup
  g_main_context_pop_thread_default(thread_context_);

  LOG_INFO("ICE thread stopped");
}

// Helper methods

std::string IceAgent::NiceCandidateToString(NiceCandidate* candidate) {
  gchar* sdp = nice_agent_generate_local_candidate_sdp(agent_, candidate);
  std::string result(sdp);
  g_free(sdp);
  return result;
}

std::string IceAgent::ComponentStateToString(NiceComponentState state) const {
  switch (state) {
    case NICE_COMPONENT_STATE_DISCONNECTED:
      return "DISCONNECTED";
    case NICE_COMPONENT_STATE_GATHERING:
      return "GATHERING";
    case NICE_COMPONENT_STATE_CONNECTING:
      return "CONNECTING";
    case NICE_COMPONENT_STATE_CONNECTED:
      return "CONNECTED";
    case NICE_COMPONENT_STATE_READY:
      return "READY";
    case NICE_COMPONENT_STATE_FAILED:
      return "FAILED";
    default:
      return "UNKNOWN";
  }
}

}  // namespace drunk_call
