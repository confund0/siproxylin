#include "logger.h"

#include <spdlog/sinks/rotating_file_sink.h>

namespace drunk_call {

std::shared_ptr<spdlog::logger> Logger::logger_;

void Logger::Initialize(const std::string& log_path, Level level) {
  // Create rotating file sink (10MB max, 5 files)
  auto file_sink = std::make_shared<spdlog::sinks::rotating_file_sink_mt>(
      log_path, 10 * 1024 * 1024, 5);

  // Create logger
  logger_ = std::make_shared<spdlog::logger>("drunk_call", file_sink);

  // Set pattern to match Go slog format
  // %l = full level name (INFO, DEBUG, WARN, ERROR)
  logger_->set_pattern(R"(time="%Y-%m-%d %H:%M:%S.%e" level=%l msg="%v")");

  // Set log level
  spdlog::level::level_enum spdlog_level = spdlog::level::info;  // default
  switch (level) {
    case Level::DEBUG: spdlog_level = spdlog::level::debug; break;
    case Level::INFO:  spdlog_level = spdlog::level::info;  break;
    case Level::WARN:  spdlog_level = spdlog::level::warn;  break;
    case Level::ERROR: spdlog_level = spdlog::level::err;   break;
  }
  logger_->set_level(spdlog_level);

  // Flush after every log (important for service debugging)
  logger_->flush_on(spdlog::level::trace);

  logger_->info("Logger initialized (path: {})", log_path);
  logger_->debug("DEBUG LEVEL IS ACTIVE - IF YOU SEE THIS, DEBUG WORKS!");
}

std::shared_ptr<spdlog::logger> Logger::Get() {
  if (!logger_) {
    // Fallback to console if not initialized (shouldn't happen)
    logger_ = spdlog::default_logger();
  }
  return logger_;
}

void Logger::Shutdown() {
  if (logger_) {
    logger_->flush();
    spdlog::shutdown();
  }
}

}  // namespace drunk_call
