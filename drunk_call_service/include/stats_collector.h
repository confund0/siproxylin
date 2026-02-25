#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace drunk_call {

// Call statistics (maps to proto GetStatsResponse)
struct CallStats {
  std::string connection_state;
  std::string ice_connection_state;
  std::string ice_gathering_state;
  int64_t bytes_sent = 0;
  int64_t bytes_received = 0;
  int64_t bandwidth_kbps = 0;
  std::vector<std::string> local_candidates;
  std::vector<std::string> remote_candidates;
  std::string connection_type;
};

// Stats collector (will integrate with LibWebRTC RTCStatsReport)
class StatsCollector {
 public:
  StatsCollector() = default;
  ~StatsCollector() = default;

  // Collect stats from LibWebRTC PeerConnection (stubbed for now)
  // CallStats CollectStats(webrtc::PeerConnectionInterface* pc);

  // Placeholder for testing
  static CallStats GetDummyStats();

 private:
  // Helper to determine connection type from candidate pair
  static std::string DetermineConnectionType(const std::string& local_type,
                                             const std::string& remote_type);
};

}  // namespace drunk_call
