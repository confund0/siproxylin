#include <grpcpp/grpcpp.h>
#include <signal.h>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <glib.h>  // For GMainLoop on default context (NiceAgent needs this!)

#include "call_server.h"
#include "logger.h"

namespace {

constexpr char kVersion[] = "0.1.0-cpp";

void PrintUsage(const char* program_name) {
  std::cout << "Drunk Call Service (C++) v" << kVersion << "\n\n"
            << "Usage: " << program_name << " [OPTIONS]\n\n"
            << "REQUIRED:\n"
            << "  --log-level LEVEL    Logging level: DEBUG|INFO|WARN|ERROR (default: INFO)\n"
            << "  --log-path PATH      Log file path (required, no default)\n\n"
            << "OPTIONAL:\n"
            << "  --port PORT          gRPC server port (default: 50051)\n"
            << "  --version            Print version and exit\n"
            << "  --help               Show this help message\n\n"
            << "TESTING/DEBUG:\n"
            << "  --test-devices       Test device enumeration and exit\n"
            << "  --test-video PORT    Test video streaming on UDP port\n"
            << "  --camera-device PATH Camera device for test-video\n\n";
}

// Signal handling thread (like Go's goroutine pattern)
void SignalHandlerThread(grpc::Server* server, drunk_call::CallServer* call_server) {
  // Block all signals in this thread
  sigset_t sigset;
  sigemptyset(&sigset);
  sigaddset(&sigset, SIGINT);
  sigaddset(&sigset, SIGTERM);

  int sig;
  // Wait for signal (this is thread-safe, not async-signal-unsafe)
  sigwait(&sigset, &sig);

  // Now we're in a normal thread context, can log safely
  const char* signame = (sig == SIGINT) ? "SIGINT" : "SIGTERM";
  LOG_INFO("Received signal {}, shutting down gracefully...", signame);

  // Graceful shutdown
  if (call_server) {
    call_server->RequestShutdown();
  }
  if (server) {
    server->Shutdown();
  }
}

}  // namespace

int main(int argc, char** argv) {
  // Parse command-line arguments
  std::string log_path;
  std::string log_level_str = "INFO";
  int port = 50051;
  bool show_version = false;
  bool show_help = false;
  bool test_devices = false;
  bool test_video = false;
  int test_video_port = 0;
  std::string camera_device;

  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    // Accept both -flag and --flag formats (Go uses single dash, GNU uses double)
    if ((arg == "--log-path" || arg == "-log-path") && i + 1 < argc) {
      log_path = argv[++i];
    } else if ((arg == "--log-level" || arg == "-log-level") && i + 1 < argc) {
      log_level_str = argv[++i];
    } else if ((arg == "--port" || arg == "-port") && i + 1 < argc) {
      port = std::stoi(argv[++i]);
    } else if (arg == "--version" || arg == "-version") {
      show_version = true;
    } else if (arg == "--help" || arg == "-help" || arg == "-h") {
      show_help = true;
    } else if (arg == "--test-devices" || arg == "-test-devices") {
      test_devices = true;
    } else if ((arg == "--test-video" || arg == "-test-video") && i + 1 < argc) {
      test_video = true;
      test_video_port = std::stoi(argv[++i]);
    } else if ((arg == "--camera-device" || arg == "-camera-device") && i + 1 < argc) {
      camera_device = argv[++i];
    } else {
      std::cerr << "Unknown argument: " << arg << "\n";
      PrintUsage(argv[0]);
      return 1;
    }
  }

  if (show_version) {
    std::cout << "drunk_call_service version " << kVersion << "\n";
    return 0;
  }

  if (show_help) {
    PrintUsage(argv[0]);
    return 0;
  }

  // Test modes (output to stdout, no logging to file)
  if (test_devices) {
    std::cout << "Device enumeration test - TODO: Implement\n";
    return 0;
  }

  if (test_video) {
    std::cout << "Video streaming test on port " << test_video_port
              << " (camera: " << (camera_device.empty() ? "default" : camera_device)
              << ") - TODO: Implement\n";
    return 0;
  }

  // Validate required arguments
  if (log_path.empty()) {
    std::cerr << "ERROR: --log-path is required\n";
    PrintUsage(argv[0]);
    return 1;
  }

  // Initialize logger
  drunk_call::Logger::Level log_level = drunk_call::Logger::Level::INFO;
  if (log_level_str == "DEBUG") {
    log_level = drunk_call::Logger::Level::DEBUG;
  } else if (log_level_str == "WARN") {
    log_level = drunk_call::Logger::Level::WARN;
  } else if (log_level_str == "ERROR") {
    log_level = drunk_call::Logger::Level::ERROR;
  }

  try {
    drunk_call::Logger::Initialize(log_path, log_level);
  } catch (const std::exception& e) {
    std::cerr << "FATAL: Failed to initialize logger: " << e.what() << "\n";
    return 1;
  }

  LOG_INFO("Drunk Call Service starting (version: {}, port: {})", kVersion, port);

  // Create gRPC service
  drunk_call::CallServer call_server;

  // Build and start gRPC server
  std::string server_address = "0.0.0.0:" + std::to_string(port);
  grpc::ServerBuilder builder;
  builder.AddListeningPort(server_address, grpc::InsecureServerCredentials());
  builder.RegisterService(&call_server);

  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  if (!server) {
    LOG_ERROR("Failed to start gRPC server on {}", server_address);
    return 1;
  }

  LOG_INFO("gRPC server listening on {}", server_address);

  // CRITICAL: Start GLib main loop on default context for NiceAgent
  // NiceAgent was created with g_main_context_default() (like Dino),
  // but we're not a GTK app, so we need to explicitly run the loop
  GMainLoop* glib_main_loop = g_main_loop_new(g_main_context_default(), FALSE);
  std::thread glib_thread([glib_main_loop]() {
    LOG_INFO("GLib main loop thread started (for NiceAgent state machine on default context)");
    g_main_loop_run(glib_main_loop);
    LOG_INFO("GLib main loop thread stopped");
  });
  LOG_INFO("GLib main loop running on default context (required for libnice agent)");

  // Block signals in main thread (will be handled by signal thread)
  sigset_t sigset;
  sigemptyset(&sigset);
  sigaddset(&sigset, SIGINT);
  sigaddset(&sigset, SIGTERM);
  pthread_sigmask(SIG_BLOCK, &sigset, nullptr);

  // Start signal handling thread (like Go's goroutine)
  std::thread signal_thread(SignalHandlerThread, server.get(), &call_server);

  // Wait for shutdown (blocks until Shutdown() called by signal thread)
  server->Wait();

  // Shutdown GLib main loop
  LOG_INFO("Shutting down GLib main loop...");
  g_main_loop_quit(glib_main_loop);
  glib_thread.join();
  g_main_loop_unref(glib_main_loop);
  LOG_INFO("GLib main loop shutdown complete");

  // Join signal thread
  signal_thread.join();

  LOG_INFO("Server shutdown complete");
  drunk_call::Logger::Shutdown();

  return 0;
}
