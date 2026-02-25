#include "stats_collector.h"
#include "logger.h"

namespace drunk_call {

CallStats StatsCollector::GetDummyStats() {
  CallStats stats;
  stats.connection_state = "new";
  stats.ice_connection_state = "checking";
  stats.ice_gathering_state = "gathering";
  stats.bytes_sent = 0;
  stats.bytes_received = 0;
  stats.bandwidth_kbps = 0;
  stats.connection_type = "unknown";
  return stats;
}

std::string StatsCollector::DetermineConnectionType(const std::string& local_type,
                                                     const std::string& remote_type) {
  // Determine connection type from candidate pair types
  // - "host" + "host" = "P2P (direct)"
  // - "srflx" + "srflx" = "P2P (srflx)"
  // - "relay" + anything = "TURN relay"

  if (local_type == "relay" || remote_type == "relay") {
    return "TURN relay";
  } else if (local_type == "srflx" || remote_type == "srflx") {
    return "P2P (srflx)";
  } else if (local_type == "host" && remote_type == "host") {
    return "P2P (direct)";
  }

  return "unknown";
}

}  // namespace drunk_call
