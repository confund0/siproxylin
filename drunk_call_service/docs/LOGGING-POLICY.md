# Logging Policy

**Purpose**: Clear guidelines for logging in drunk_call_service
**Date**: 2026-03-03
**Status**: ENFORCED

---

## TL;DR

| Stream | Purpose | When to Use |
|--------|---------|-------------|
| **STDOUT** | libnice/GStreamer debug ONLY | Never use directly - let libraries write to it |
| **STDERR** | Unhandled exceptions ONLY | Only for critical crashes before logger init |
| **Logger (spdlog)** | All our logs | Always use `LOG_*()` macros for application logging |

---

## The Rules

### Rule 1: NEVER use std::cout in source files

**Why**: STDOUT is reserved for libnice and GStreamer debug output. Mixing our logs with library debug makes it impossible to parse.

**Bad**:
```cpp
std::cout << "[WebRTCSession] Creating offer..." << std::endl;
```

**Good**:
```cpp
LOG_DEBUG("Creating offer for session: {}", config_.session_id);
```

### Rule 2: ONLY use std::cerr for fatal exceptions (before logger init)

**Why**: STDERR should only contain unhandled exceptions that prevent the service from starting.

**Bad**:
```cpp
std::cerr << "[WebRTCSession] Failed to create pipeline" << std::endl;
```

**Good**:
```cpp
LOG_ERROR("Failed to create pipeline: {}", error_message);
```

**Only acceptable use** (main.cpp, before logger initialized):
```cpp
int main() {
    try {
        // Logger not yet initialized
        gst_init(nullptr, nullptr);
    } catch (const std::exception& e) {
        std::cerr << "FATAL: GStreamer initialization failed: " << e.what() << std::endl;
        return 1;
    }

    try {
        Logger::init(log_path, "DEBUG");
    } catch (const std::exception& e) {
        std::cerr << "FATAL: Logger initialization failed: " << e.what() << std::endl;
        return 1;
    }

    // From here on, ONLY use LOG_*() macros
    LOG_INFO("Service starting...");
}
```

### Rule 3: ALWAYS use Logger macros for application logging

**Available macros**:
- `LOG_TRACE(...)` - Very detailed (not in production)
- `LOG_DEBUG(...)` - Debug info, should show nearly everything
- `LOG_INFO(...)` - Important events (session created, offer sent, etc.)
- `LOG_WARN(...)` - Warnings (recoverable errors)
- `LOG_ERROR(...)` - Errors (failed operations)
- `LOG_CRITICAL(...)` - Critical errors (about to crash)

**Format string support**:
```cpp
LOG_DEBUG("Session {}: Creating offer for peer {}", session_id, peer_jid);
LOG_ERROR("Failed to parse SDP: {}", error_message);
```

---

## Cross-Platform Considerations

### Windows Notes

**Myth**: "Windows doesn't have stderr"
**Reality**: Windows has stderr, but:
- Both stdout and stderr go to console by default
- Can be redirected separately like on Unix
- C++ runtime handles it correctly

**Our approach**: Don't rely on stdout/stderr at all - use file logging everywhere.

### File Logging Benefits

✅ Works identically on Linux, macOS, Windows
✅ Log rotation (10MB files, keep last 3)
✅ Structured, searchable logs
✅ Doesn't interfere with library debug output
✅ Can be tailed/grep'd during development

---

## Logger Initialization

### In main.cpp

```cpp
#include "logger.h"

int main(int argc, char* argv[]) {
    // BEFORE logger init: Only std::cerr for FATAL errors
    try {
        gst_init(&argc, &argv);
    } catch (const std::exception& e) {
        std::cerr << "FATAL: GStreamer init failed: " << e.what() << std::endl;
        return 1;
    }

    // Initialize logger
    std::string log_path = "/var/log/drunk-call-service/service.log";  // Or from config
    try {
        Logger::init(log_path, "DEBUG");
        LOG_INFO("Logger initialized: path={}, level=DEBUG", log_path);
    } catch (const std::exception& e) {
        std::cerr << "FATAL: Logger init failed: " << e.what() << std::endl;
        return 1;
    }

    // AFTER logger init: Only LOG_*() macros
    LOG_INFO("Service starting...");
    LOG_DEBUG("GStreamer version: {}", gst_version_string());

    // ... rest of main

    LOG_INFO("Service shutting down");
    Logger::shutdown();
    return 0;
}
```

### In library classes (WebRTCSession, etc.)

```cpp
#include "logger.h"

bool WebRTCSession::initialize(const SessionConfig& config) {
    LOG_DEBUG("Initializing WebRTC session: {}", config.session_id);

    try {
        if (!create_pipeline()) {
            LOG_ERROR("Failed to create pipeline for session: {}", config.session_id);
            return false;
        }

        LOG_INFO("Session initialized: {}, peer: {}", config.session_id, config.peer_jid);
        return true;
    } catch (const std::exception& e) {
        LOG_CRITICAL("Exception in initialize: {}", e.what());
        return false;
    }
}
```

---

## Log Level Guidelines

### DEBUG (development)
Use during development to see everything:
```cpp
LOG_DEBUG("ICE candidate received: type={}, foundation={}", type, foundation);
LOG_DEBUG("SDP negotiation: state={}, offer_size={}", state, offer.size());
```

### INFO (production default)
Important events that explain what the service is doing:
```cpp
LOG_INFO("Session created: session_id={}, peer={}", session_id, peer_jid);
LOG_INFO("Call established: {} ↔ {}", local_jid, remote_jid);
LOG_INFO("Session ended: {}, duration={}s", session_id, duration);
```

### WARN (recoverable issues)
```cpp
LOG_WARN("ICE candidate failed, continuing with others");
LOG_WARN("STUN server timeout, using TURN");
```

### ERROR (operation failures)
```cpp
LOG_ERROR("Failed to create offer: {}", error);
LOG_ERROR("SDP parsing failed: {}", sdp_error);
```

### CRITICAL (about to crash/exit)
```cpp
LOG_CRITICAL("Out of memory, terminating");
LOG_CRITICAL("Cannot continue: {}", fatal_error);
```

---

## Testing Logging

```bash
# Initialize logger in test
Logger::init("/tmp/test.log", "DEBUG");

# Run test
./test_step1_pipeline

# Check log
cat /tmp/test.log

# Should see structured logs with timestamps:
# [2026-03-03 10:00:00.123] [debug] Initializing WebRTC session: test-1
# [2026-03-03 10:00:00.456] [info] Session initialized: test-1, peer: alice@example.com
```

---

## Summary

**DO**:
- ✅ Use `LOG_*()` macros for all application logging
- ✅ Use format strings: `LOG_INFO("Event: {}", value)`
- ✅ Include context (session_id, peer_jid) in logs
- ✅ Use appropriate log levels (DEBUG in dev, INFO in prod)

**DON'T**:
- ❌ Never use `std::cout` in source files (tests are OK)
- ❌ Never use `std::cerr` except in main.cpp before logger init
- ❌ Never use `printf()` or `fprintf()`
- ❌ Never mix library debug with our logs

**Result**: Clean, parseable logs that work identically on Linux, macOS, and Windows.
