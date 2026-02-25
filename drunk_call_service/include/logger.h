#pragma once

#include <spdlog/spdlog.h>
#include <spdlog/sinks/rotating_file_sink.h>
#include <memory>
#include <string>

namespace drunk_call {

// Logging utility with spdlog (matches Go slog format)
class Logger {
 public:
  enum class Level {
    DEBUG,
    INFO,
    WARN,
    ERROR
  };

  // Initialize logger (must be called before any logging)
  static void Initialize(const std::string& log_path, Level level);

  // Get global logger instance
  static std::shared_ptr<spdlog::logger> Get();

  // Shutdown (flush and close files)
  static void Shutdown();

 private:
  static std::shared_ptr<spdlog::logger> logger_;
};

// Convenience macros (match slog style, use {} formatting)
#define LOG_DEBUG(...) SPDLOG_LOGGER_DEBUG(drunk_call::Logger::Get(), __VA_ARGS__)
#define LOG_INFO(...) SPDLOG_LOGGER_INFO(drunk_call::Logger::Get(), __VA_ARGS__)
#define LOG_WARN(...) SPDLOG_LOGGER_WARN(drunk_call::Logger::Get(), __VA_ARGS__)
#define LOG_ERROR(...) SPDLOG_LOGGER_ERROR(drunk_call::Logger::Get(), __VA_ARGS__)

}  // namespace drunk_call
