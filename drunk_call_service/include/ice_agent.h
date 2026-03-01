#pragma once

#include <string>
#include <functional>
#include <thread>
#include <memory>
#include <mutex>
#include <map>

// libnice headers
#include <nice/agent.h>

namespace drunk_call {

// IceAgent wraps NiceAgent for 2-component ICE (RTP + RTCP)
// Reference: Dino's transport_parameters.vala
class IceAgent {
 public:
  // Constructor: n_components = 2 for audio/video (RTP + RTCP)
  explicit IceAgent(int n_components);
  ~IceAgent();

  // Delete copy/move constructors (manages thread and libnice agent)
  IceAgent(const IceAgent&) = delete;
  IceAgent& operator=(const IceAgent&) = delete;

  // Lifecycle
  bool Initialize();
  void Shutdown();

  // Stream management
  bool AddStream();  // Creates n_components stream
  guint stream_id() const { return stream_id_; }

  // STUN/TURN configuration
  void SetStunServer(const std::string& host, uint16_t port);
  void SetTurnServer(const std::string& host, uint16_t port,
                     const std::string& username, const std::string& password);

  // ICE transport policy (all or relay)
  void SetTransportPolicy(bool relay_only);

  // ICE role
  void SetControllingMode(bool controlling);

  // ICE credentials
  void SetRemoteCredentials(const std::string& ufrag, const std::string& pwd);
  std::pair<std::string, std::string> GetLocalCredentials();

  // Candidate operations
  void GatherCandidates();
  bool AddRemoteCandidate(int component_id, const std::string& candidate_str,
                          const std::string& sdp_mid, int sdp_mline_index);

  // Data transmission
  bool Send(int component_id, const uint8_t* data, size_t len);

  // Callbacks
  // Following GStreamer webrtcbin pattern: IceAgent only knows about mlineindex (session_id),
  // Session layer handles mid mapping
  // Reference: gst-plugins-bad/ext/webrtc/gstwebrtcbin.c:6869-6876
  using CandidateCallback = std::function<void(int component_id,
                                                const std::string& candidate,
                                                int sdp_mline_index)>;
  using ComponentStateCallback = std::function<void(int component_id,
                                                     const std::string& state)>;
  using DataReceivedCallback = std::function<void(int component_id,
                                                   const uint8_t* data,
                                                   size_t len)>;
  using GatheringDoneCallback = std::function<void()>;

  void SetOnCandidateCallback(CandidateCallback callback);
  void SetOnComponentStateCallback(ComponentStateCallback callback);
  void SetOnDataReceivedCallback(DataReceivedCallback callback);
  void SetOnGatheringDoneCallback(GatheringDoneCallback callback);

  // State queries
  bool IsComponentReady(int component_id) const;
  std::string GetComponentState(int component_id) const;

 private:
  // libnice signal handlers (must be static)
  static void OnCandidateGatheringDone(NiceAgent* agent, guint stream_id, gpointer user_data);
  static void OnInitialBindingRequestReceived(NiceAgent* agent, guint stream_id, gpointer user_data);
  static void OnNewCandidateFull(NiceAgent* agent, NiceCandidate* candidate, gpointer user_data);
  static void OnComponentStateChanged(NiceAgent* agent, guint stream_id,
                                      guint component_id, guint state, gpointer user_data);
  static void OnNewSelectedPairFull(NiceAgent* agent, guint stream_id, guint component_id,
                                    NiceCandidate* local, NiceCandidate* remote, gpointer user_data);
  static void OnRecv(NiceAgent* agent, guint stream_id, guint component_id,
                     guint len, gchar* buf, gpointer user_data);

  // Thread entry point for ICE processing
  void IceThreadFunc();

  // Helper methods
  std::string NiceCandidateToString(NiceCandidate* candidate);
  std::string ComponentStateToString(NiceComponentState state) const;

  // Configuration
  int n_components_;
  bool initialized_;
  bool relay_only_;
  bool controlling_mode_set_;  // Track if SetControllingMode() was called

  // libnice objects
  NiceAgent* agent_;
  guint stream_id_;

  // Context (simplified - using default context only)
  GMainContext* thread_context_;  // Points to default context (not owned)

  // Callbacks
  std::mutex callback_mutex_;
  CandidateCallback on_candidate_;
  ComponentStateCallback on_component_state_;
  DataReceivedCallback on_data_received_;
  GatheringDoneCallback on_gathering_done_;

  // Component state tracking
  mutable std::mutex state_mutex_;
  std::map<int, NiceComponentState> component_states_;
};

}  // namespace drunk_call
