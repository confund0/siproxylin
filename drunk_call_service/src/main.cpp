/**
 * Drunk Call Service - gRPC Server (main.cpp)
 *
 * Architecture: Python (Jingle/XMPP) ↔ gRPC ↔ C++ (WebRTC/GStreamer)
 *
 * CRITICAL LOGGING POLICY:
 * ========================
 * STDOUT: Reserved for libnice/GStreamer debug output ONLY
 *         DO NOT use std::cout in this file
 *
 * STDERR: Reserved for FATAL errors before logger initialization ONLY
 *         Use std::cerr ONLY in main() before Logger::init()
 *
 * Logger: Use LOG_*() macros for ALL application logging after init
 *         LOG_DEBUG() - Debug info (shows nearly everything at debug level)
 *         LOG_INFO()  - Important events (session created, call started)
 *         LOG_WARN()  - Warnings (recoverable errors)
 *         LOG_ERROR() - Errors (failed operations)
 *         LOG_CRITICAL() - Critical errors (about to crash)
 *
 * Policy: docs/LOGGING-POLICY.md
 * Usage: docs/THREAD-INFRASTRUCTURE-USAGE.md
 * Threading: docs/CALLS/GSTREAMER-THREADING.md
 *
 * Example:
 *   int main() {
 *       // BEFORE logger init - ONLY std::cerr for FATAL
 *       try {
 *           gst_init(nullptr, nullptr);
 *       } catch (const std::exception& e) {
 *           std::cerr << "FATAL: GStreamer init failed: " << e.what() << std::endl;
 *           return 1;
 *       }
 *
 *       // Initialize logger
 *       try {
 *           Logger::init(log_path, "DEBUG");
 *           LOG_INFO("Logger initialized: {}", log_path);
 *       } catch (const std::exception& e) {
 *           std::cerr << "FATAL: Logger init failed: " << e.what() << std::endl;
 *           return 1;
 *       }
 *
 *       // AFTER logger init - ONLY LOG_*() macros
 *       LOG_INFO("Service starting...");
 *       // ... rest of service
 *   }
 */

#include "call_service_impl.h"
#include "logger.h"
#include "media_session.h"
#include <grpcpp/grpcpp.h>
#include <gst/gst.h>
#include <glib.h>
#include <thread>
#include <csignal>
#include <atomic>
#include <iostream>
#include <cstring>
#include <filesystem>

// Platform-specific includes
#ifdef _WIN32
    #include <windows.h>
#elif __APPLE__
    #include <mach-o/dyld.h>
    #include <climits>  // PATH_MAX
#endif

// Global shutdown flag (set by signal handler)
std::atomic<bool> g_shutdown_requested(false);

// Global GLib main loop (for clean shutdown)
GMainLoop* g_main_loop = nullptr;

/**
 * CLI configuration
 */
struct Config {
    int port = 50051;
    std::string log_level = "INFO";
    std::string log_path = "";  // Empty = default to ../app/logs/drunk-call-service.log
    bool test_devices = false;
    bool help = false;
};

/**
 * Print help message
 */
void print_help(const char* program_name) {
    std::cout << "Drunk Call Service - WebRTC/GStreamer gRPC Server\n\n";
    std::cout << "Usage: " << program_name << " [options]\n\n";
    std::cout << "Options:\n";
    std::cout << "  --port <port>         gRPC server port (default: 50051)\n";
    std::cout << "  --log-level <level>   Log level: DEBUG, INFO, WARN, ERROR (default: INFO)\n";
    std::cout << "  --log-path <path>     Log file path (default: ../app/logs/drunk-call-service.log)\n";
    std::cout << "  --test-devices        Test device enumeration and exit\n";
    std::cout << "  --help                Show this help message\n";
    std::cout << "\nExamples:\n";
    std::cout << "  " << program_name << " --port 50052 --log-level DEBUG\n";
    std::cout << "  " << program_name << " --test-devices\n";
}

/**
 * Parse command-line arguments
 */
Config parse_args(int argc, char* argv[]) {
    Config config;

    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            config.help = true;
        } else if (arg == "--port" && i + 1 < argc) {
            config.port = std::atoi(argv[++i]);
        } else if (arg == "--log-level" && i + 1 < argc) {
            config.log_level = argv[++i];
        } else if (arg == "--log-path" && i + 1 < argc) {
            config.log_path = argv[++i];
        } else if (arg == "--test-devices") {
            config.test_devices = true;
        } else {
            std::cerr << "Unknown argument: " << arg << "\n";
            std::cerr << "Use --help for usage information\n";
            std::exit(1);
        }
    }

    return config;
}

/**
 * Test device enumeration and exit
 */
void test_devices_and_exit() {
    std::cout << "=== Audio Input Devices ===\n";
    auto inputs = drunk_call::DeviceEnumerator::list_audio_inputs();
    for (const auto& dev : inputs) {
        std::cout << "  " << dev.id << "\n";
        std::cout << "    Name: " << dev.name << "\n";
        std::cout << "    Description: " << dev.description << "\n";
        std::cout << "    Default: " << (dev.is_default ? "yes" : "no") << "\n";
    }

    std::cout << "\n=== Audio Output Devices ===\n";
    auto outputs = drunk_call::DeviceEnumerator::list_audio_outputs();
    for (const auto& dev : outputs) {
        std::cout << "  " << dev.id << "\n";
        std::cout << "    Name: " << dev.name << "\n";
        std::cout << "    Description: " << dev.description << "\n";
        std::cout << "    Default: " << (dev.is_default ? "yes" : "no") << "\n";
    }

    std::cout << "\n=== Default Devices ===\n";
    auto default_input = drunk_call::DeviceEnumerator::get_default_input();
    auto default_output = drunk_call::DeviceEnumerator::get_default_output();
    std::cout << "  Input: " << default_input.name << " (" << default_input.id << ")\n";
    std::cout << "  Output: " << default_output.name << " (" << default_output.id << ")\n";

    std::exit(0);
}

/**
 * Signal/Console handler for graceful shutdown.
 * Triggers graceful shutdown on SIGINT/SIGTERM (Unix) or Ctrl+C/Break/Close (Windows).
 *
 * IMPORTANT: Keep this async-signal-safe!
 * Only set atomic flags and call async-signal-safe functions.
 * Session cleanup will happen in main() shutdown sequence.
 */
#ifdef _WIN32
// Windows Console Control Handler
BOOL WINAPI ConsoleCtrlHandler(DWORD dwCtrlType) {
    switch (dwCtrlType) {
        case CTRL_C_EVENT:        // Ctrl+C pressed
        case CTRL_BREAK_EVENT:    // Ctrl+Break pressed
        case CTRL_CLOSE_EVENT:    // Console window closing
            // Set shutdown flag (atomic, safe for Windows handler)
            g_shutdown_requested = true;
            return TRUE;  // Handled
        default:
            return FALSE;  // Not handled
    }

    // CRITICAL: Do NOT quit GLib loop here!
    // Same reasoning as POSIX signal handler - see below.
}
#else
// POSIX Signal Handler (Linux, macOS)
void signal_handler(int signum) {
    // Set shutdown flag (atomic, signal-safe)
    g_shutdown_requested = true;

    // CRITICAL: Do NOT quit GLib loop here!
    // If we quit the loop before cleaning up sessions, webrtc->stop() will hang
    // because it needs the GLib thread to process GStreamer cleanup.
    // The main shutdown sequence (Phase 8.3) will quit the loop AFTER
    // cleaning up sessions (Phase 8.1).

    // Note: Session cleanup happens in main() shutdown sequence,
    // not here, to avoid async-signal-safety issues
}
#endif

/**
 * GLib main loop thread function.
 *
 * This thread MUST start BEFORE creating any GStreamer elements.
 * All GStreamer callbacks will fire in this thread.
 *
 * See: docs/CALLS/GSTREAMER-THREADING.md
 */
void glib_main_loop_thread(GMainLoop* loop) {
    LOG_INFO("GLib main loop thread started");

    // Run main loop (blocks until g_main_loop_quit is called)
    g_main_loop_run(loop);

    LOG_INFO("GLib main loop thread exiting");
}

/**
 * Main entry point.
 *
 * Threading model:
 * 1. Main thread: Initialize, start GLib thread, start gRPC server, wait for shutdown
 * 2. GLib thread: Process GStreamer callbacks, push events to queues
 * 3. gRPC thread pool: Handle RPC calls, pop events from queues
 *
 * See: docs/CALLS/4-GRPC-PLAN.md lines 61-100
 */
int main(int argc, char* argv[]) {
    // ========================================================================
    // Phase 0: Parse CLI arguments
    // ========================================================================

    Config config = parse_args(argc, argv);

    // Handle --help
    if (config.help) {
        print_help(argv[0]);
        return 0;
    }

    // ========================================================================
    // Phase 1: Initialize GStreamer (BEFORE logger)
    // ========================================================================

    try {
        gst_init(&argc, &argv);
    } catch (const std::exception& e) {
        std::cerr << "FATAL: GStreamer initialization failed: " << e.what() << std::endl;
        return 1;
    }

    // Handle --test-devices (after GStreamer init)
    if (config.test_devices) {
        test_devices_and_exit();
        return 0;  // Never reached
    }

    // ========================================================================
    // Phase 2: Initialize Logger
    // ========================================================================

    // Determine log path (default: ../app/logs/drunk-call-service.log)
    std::string log_path = config.log_path;
    if (log_path.empty()) {
        namespace fs = std::filesystem;

        // Get executable directory (platform-specific)
        fs::path exe_path;
        #ifdef _WIN32
            // Windows: Use GetModuleFileName Win32 API
            char buffer[MAX_PATH];
            GetModuleFileNameA(NULL, buffer, MAX_PATH);
            exe_path = fs::path(buffer).parent_path();
        #elif __APPLE__
            // macOS: Use _NSGetExecutablePath
            char buffer[PATH_MAX];
            uint32_t size = sizeof(buffer);
            if (_NSGetExecutablePath(buffer, &size) == 0) {
                exe_path = fs::canonical(buffer).parent_path();
            } else {
                // Buffer too small (very rare), reallocate
                char* bigger_buffer = new char[size];
                _NSGetExecutablePath(bigger_buffer, &size);
                exe_path = fs::canonical(bigger_buffer).parent_path();
                delete[] bigger_buffer;
            }
        #else  // Linux
            // Linux: Use /proc virtual filesystem
            exe_path = fs::canonical("/proc/self/exe").parent_path();
        #endif

        // Go up one level and into app/logs
        fs::path logs_dir = exe_path.parent_path() / "app" / "logs";
        // Create directory if it doesn't exist
        fs::create_directories(logs_dir);
        log_path = (logs_dir / "drunk-call-service.log").string();
    }

    std::string log_level = config.log_level;

    try {
        drunk_call::Logger::init(log_path, log_level);
        LOG_INFO("Logger initialized: path={}, level={}", log_path, log_level);
    } catch (const std::exception& e) {
        std::cerr << "FATAL: Logger initialization failed: " << e.what() << std::endl;
        return 1;
    }

    // From here on: ONLY use LOG_*() macros, NO std::cout/std::cerr

    // ========================================================================
    // Phase 3: Log GStreamer version info
    // ========================================================================

    guint gst_major, gst_minor, gst_micro, gst_nano;
    gst_version(&gst_major, &gst_minor, &gst_micro, &gst_nano);
    LOG_INFO("GStreamer version: {}.{}.{}.{}", gst_major, gst_minor, gst_micro, gst_nano);

    // ========================================================================
    // Phase 4: Start GLib main loop thread (CRITICAL - MUST start before sessions)
    // ========================================================================

    LOG_INFO("Starting GLib main loop thread...");
    g_main_loop = g_main_loop_new(nullptr, FALSE);
    std::thread glib_thread(glib_main_loop_thread, g_main_loop);

    LOG_INFO("GLib main loop thread started successfully");

    // ========================================================================
    // Phase 5: Setup signal handlers
    // ========================================================================

    #ifdef _WIN32
        // Windows: Console control handler
        SetConsoleCtrlHandler(ConsoleCtrlHandler, TRUE);
        LOG_INFO("Signal handlers registered (CTRL_C, CTRL_BREAK, CTRL_CLOSE)");
    #else
        // Linux, macOS: POSIX signals
        std::signal(SIGINT, signal_handler);
        std::signal(SIGTERM, signal_handler);
        LOG_INFO("Signal handlers registered (SIGINT, SIGTERM)");
    #endif

    // ========================================================================
    // Phase 6: Start gRPC server
    // ========================================================================

    std::string server_address = "127.0.0.1:" + std::to_string(config.port);

    drunk_call::CallServiceImpl service;

    grpc::ServerBuilder builder;

    // Listen on address without authentication (local only)
    builder.AddListeningPort(server_address, grpc::InsecureServerCredentials());

    // Register service
    builder.RegisterService(&service);

    // Build and start server
    std::unique_ptr<grpc::Server> server(builder.BuildAndStart());
    if (!server) {
        LOG_CRITICAL("Failed to start gRPC server on {}", server_address);
        g_main_loop_quit(g_main_loop);
        glib_thread.join();
        g_main_loop_unref(g_main_loop);
        drunk_call::Logger::shutdown();
        return 1;
    }

    LOG_INFO("gRPC server listening on {}", server_address);
    LOG_INFO("Service ready - waiting for RPCs...");

    // ========================================================================
    // Phase 7: Wait for shutdown signal
    // ========================================================================

    // Main thread blocks here, waiting for SIGINT/SIGTERM or Shutdown RPC
    // gRPC server handles requests in thread pool
    // GLib thread processes GStreamer callbacks

    // Check shutdown flag periodically (every 100ms for responsiveness)
    while (!g_shutdown_requested) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    LOG_INFO("Shutdown signal received, cleaning up...");
    LOG_DEBUG("Shutdown source: SIGINT/SIGTERM or gRPC Shutdown command");

    // ========================================================================
    // Phase 8: Graceful shutdown
    // ========================================================================

    // Step 1: Cleanup all active sessions
    LOG_DEBUG("Phase 8.1: Cleaning up active sessions");
    service.cleanup_all_sessions();

    // Step 2: Shutdown gRPC server (graceful shutdown with deadline)
    LOG_DEBUG("Phase 8.2: Shutting down gRPC server");
    LOG_INFO("Shutting down gRPC server (5s graceful deadline)...");
    server->Shutdown(std::chrono::system_clock::now() + std::chrono::seconds(5));
    LOG_INFO("gRPC server stopped");

    // Step 3: Stop GLib main loop (may already be stopped by signal handler)
    LOG_DEBUG("Phase 8.3: Stopping GLib main loop");
    if (g_main_loop && g_main_loop_is_running(g_main_loop)) {
        LOG_DEBUG("GLib main loop still running, quitting...");
        g_main_loop_quit(g_main_loop);
    } else {
        LOG_DEBUG("GLib main loop already stopped (signal handler)");
    }
    LOG_DEBUG("Waiting for GLib thread to join...");
    glib_thread.join();
    if (g_main_loop) {
        g_main_loop_unref(g_main_loop);
    }
    LOG_INFO("GLib main loop stopped");

    // Step 4: Deinitialize GStreamer (cleanup resources)
    LOG_DEBUG("Phase 8.4: Deinitializing GStreamer");
    gst_deinit();
    LOG_DEBUG("GStreamer deinitialized");

    // Step 5: Shutdown logger
    LOG_INFO("Service shutdown complete - exiting cleanly");
    LOG_DEBUG("Phase 8.5: Shutting down logger");
    drunk_call::Logger::shutdown();

    return 0;
}
