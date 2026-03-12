# Platform Compatibility Guide for drunk_call_service

**Generated:** 2026-03-12
**Status:** Planning/Pre-Implementation
**Platforms:** Linux (primary) | Windows 10/11 | macOS 10.15+

---

## Table of Contents

1. [Platform Architecture Overview](#platform-architecture-overview)
2. [Build Requirements](#build-requirements)
3. [Code Issues Summary](#code-issues-summary)
4. [Critical Fixes](#critical-fixes)
5. [High Priority Fixes](#high-priority-fixes)
6. [Complete Code Templates](#complete-code-templates)
7. [Build Instructions](#build-instructions)
8. [Testing Checklist](#testing-checklist)

---

## Platform Architecture Overview

### Compile-Time vs Runtime: How It Works

The codebase uses **TWO** distinct mechanisms for platform compatibility:

#### 1. **Compile-Time Selection** (`#ifdef` Preprocessor Directives)

Used for **OS-specific APIs** that don't exist on other platforms:

```cpp
#ifdef _WIN32
    // Windows-only code (GetModuleFileName, SetConsoleCtrlHandler, etc.)
    #include <windows.h>
    GetModuleFileNameA(NULL, buffer, MAX_PATH);
#elif __APPLE__
    // macOS-only code (_NSGetExecutablePath, etc.)
    #include <mach-o/dyld.h>
    _NSGetExecutablePath(buffer, &size);
#else  // Linux
    // Linux-only code (/proc filesystem, etc.)
    fs::path exe_path = fs::canonical("/proc/self/exe");
#endif
```

**What happens:** The compiler **removes** code for other platforms entirely. Windows builds don't include Linux code at all.

#### 2. **Runtime Plugin Loading** (GStreamer)

Used for **audio/video plugins** that exist on all platforms but have different implementations:

```cpp
// COMPILE-TIME: Choose plugin name string based on target platform
#ifdef _WIN32
    const char* plugin_name = "wasapisrc";  // String literal decided at compile-time
#elif __APPLE__
    const char* plugin_name = "osxaudiosrc";
#else
    const char* plugin_name = "pulsesrc";
#endif

// RUNTIME: GStreamer dynamically loads platform-specific DLL/dylib/SO
GstElement* src = gst_element_factory_make(plugin_name, "audio_src");
//                 ^^^^^^^^^^^^^^^^^^^^^^^^
//                 This function searches for and loads:
//                 - Windows: C:\gstreamer\...\gstwasapi.dll
//                 - macOS:   /usr/local/lib/gstreamer-1.0/libgstosxaudio.dylib
//                 - Linux:   /usr/lib/gstreamer-1.0/libgstpulse.so
```

**What happens:**
- **Compile-time:** We pick the plugin **name** string
- **Runtime:** GStreamer's plugin loader finds and loads the platform-specific shared library

### Platform Detection Macros

| Platform | Preprocessor Macro | Notes |
|----------|-------------------|-------|
| **Windows** | `_WIN32` | Defined by MSVC, MinGW, Clang on Windows |
| **macOS** | `__APPLE__` and `__MACH__` | Use `__APPLE__` (more portable) |
| **Linux** | `__linux__` | Standard Linux detection |
| **Generic Unix** | `__unix__` | Includes Linux, macOS, BSDs |

**Best Practice Pattern:**
```cpp
#ifdef _WIN32
    // Windows
#elif __APPLE__
    // macOS
#else
    // Linux (or other Unix)
#endif
```

### GStreamer Audio Backend Architecture

| Platform | Audio API | Source Plugin | Sink Plugin | Shared Library |
|----------|-----------|---------------|-------------|----------------|
| **Linux** | PulseAudio | `pulsesrc` | `pulsesink` | `libgstpulse.so` |
| **macOS** | CoreAudio | `osxaudiosrc` | `osxaudiosink` | `libgstosxaudio.dylib` |
| **Windows** | WASAPI | `wasapisrc` | `wasapisink` | `gstwasapi.dll` |

**Note:** All platforms also support `autoaudiosrc`/`autoaudiosink` which automatically select the best available backend.

---

## Build Requirements

### Linux (Primary Development Platform)

**System Packages (Debian/Ubuntu):**
```bash
sudo apt install build-essential cmake pkg-config
sudo apt install libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev
sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
sudo apt install libgrpc++-dev protobuf-compiler-grpc
sudo apt install libspdlog-dev libglib2.0-dev
```

**Already Working:** No code changes needed for Linux builds.

---

### Windows 10/11 (MSVC x64)

#### 1. Visual Studio 2019 or 2022
- Install "Desktop development with C++" workload
- Ensure C++17 support is included
- Download: https://visualstudio.microsoft.com/downloads/

#### 2. CMake (3.15+)
- Download: https://cmake.org/download/
- Add to PATH during installation

#### 3. vcpkg (C++ Package Manager)
```cmd
git clone https://github.com/Microsoft/vcpkg.git
cd vcpkg
bootstrap-vcpkg.bat
vcpkg integrate install
```

#### 4. Install Dependencies via vcpkg
```cmd
vcpkg install grpc:x64-windows
vcpkg install protobuf:x64-windows
vcpkg install spdlog:x64-windows
vcpkg install glib:x64-windows
```

#### 5. GStreamer for Windows
- Download: https://gstreamer.freedesktop.org/download/
- Install **both**:
  - Runtime installer: `gstreamer-1.0-msvc-x86_64-*.msi`
  - Development installer: `gstreamer-1.0-devel-msvc-x86_64-*.msi`
- Choose **Complete** installation
- Add to PATH: `C:\gstreamer\1.0\msvc_x86_64\bin`
- Set environment variable:
  ```cmd
  set GSTREAMER_1_0_ROOT_MSVC_X86_64=C:\gstreamer\1.0\msvc_x86_64
  ```

---

### macOS 10.15+ (Catalina or newer)

#### 1. Xcode Command Line Tools
```bash
xcode-select --install
```

#### 2. Homebrew Package Manager
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

#### 3. Install Dependencies via Homebrew
```bash
brew install cmake pkg-config
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad
brew install grpc protobuf
brew install spdlog glib
```

#### 4. Verify GStreamer Installation
```bash
gst-inspect-1.0 osxaudiosrc    # Should show CoreAudio source plugin
gst-inspect-1.0 webrtcbin      # Should show WebRTC bin element
```

**Notes:**
- macOS uses **CoreAudio** (not PulseAudio)
- GStreamer plugins: `osxaudiosrc`, `osxaudiosink`
- No special environment variables needed (Homebrew handles paths)

---

## Code Issues Summary

| Issue | File | Lines | Platforms Affected | Severity | Status |
|-------|------|-------|-------------------|----------|--------|
| `/proc/self/exe` usage | main.cpp | 245 | Windows, macOS | **CRITICAL** | ❌ Must Fix |
| GCC/Clang compiler flags | CMakeLists.txt | 105-114 | Windows (MSVC) | **CRITICAL** | ❌ Must Fix |
| pkg-config dependency | CMakeLists.txt | 11, 14-31 | Windows | **CRITICAL** | ❌ Must Fix |
| SIGTERM signal handling | main.cpp | 287-289 | Windows | **HIGH** | ⚠️ Needs Fix |
| PulseAudio hardcoded device handling | webrtc_session.cpp | 872-906, 1100-1134, 2042 | Windows, macOS | **HIGH** | ⚠️ Needs Fix |
| Device enumerator platform paths | device_enumerator.cpp | 121-146 | Windows, macOS | **MEDIUM** | ⚠️ Needs Testing |
| std::filesystem paths | main.cpp, logger.cpp | Various | None | LOW | ✅ OK (cross-platform) |
| GStreamer elements | webrtc_session.cpp | Various | None | LOW | ✅ OK (cross-platform) |
| Install paths | CMakeLists.txt | 120-122 | All | LOW | ℹ️ Consider improving |

**Total:** 9 issues
**Blocking:** 5 issues (3 Critical + 2 High)

---

## Critical Fixes

### 1. Fix Executable Path Detection (main.cpp:245)

**Problem:**
Linux-specific `/proc/self/exe` doesn't exist on Windows or macOS. Will crash on startup when determining log path.

**Current Code:**
```cpp
fs::path exe_path = fs::canonical("/proc/self/exe").parent_path();
```

**Fix (All 3 Platforms):**
```cpp
#ifdef _WIN32
    #include <windows.h>
    // Windows: Use GetModuleFileName Win32 API
    char buffer[MAX_PATH];
    GetModuleFileNameA(NULL, buffer, MAX_PATH);
    fs::path exe_path = fs::path(buffer).parent_path();
#elif __APPLE__
    #include <mach-o/dyld.h>
    // macOS: Use _NSGetExecutablePath
    char buffer[PATH_MAX];
    uint32_t size = sizeof(buffer);
    if (_NSGetExecutablePath(buffer, &size) == 0) {
        exe_path = fs::canonical(buffer).parent_path();
    } else {
        // Buffer too small (very rare), reallocate
        char* bigger_buffer = (char*)malloc(size);
        _NSGetExecutablePath(bigger_buffer, &size);
        exe_path = fs::canonical(bigger_buffer).parent_path();
        free(bigger_buffer);
    }
#else  // Linux
    // Linux: Use /proc virtual filesystem
    fs::path exe_path = fs::canonical("/proc/self/exe").parent_path();
#endif
```

**File:** `drunk_call_service/src/main.cpp`
**Location:** Add platform includes at top, replace lines 243-246

---

### 2. Fix Compiler Flags (CMakeLists.txt:105-114)

**Problem:**
GCC/Clang-specific flags fail with MSVC. macOS Clang may not support all sanitizers.

**Current Code:**
```cmake
target_compile_options(${BINARY_NAME} PRIVATE
    -Wall
    -Wextra
    $<$<CONFIG:Debug>:-g -O0 -fsanitize=address -fsanitize=undefined>
    $<$<CONFIG:Release>:-O3>
)

target_link_options(${BINARY_NAME} PRIVATE
    $<$<CONFIG:Debug>:-fsanitize=address -fsanitize=undefined>
)
```

**Fix (Platform-Specific Flags):**
```cmake
if(MSVC)
    # Windows: MSVC compiler flags
    target_compile_options(${BINARY_NAME} PRIVATE
        /W4                              # Warning level 4 (equivalent to -Wall -Wextra)
        $<$<CONFIG:Debug>:/Zi /Od>       # Debug info + no optimization
        $<$<CONFIG:Release>:/O2>         # Max optimization
    )
    # Note: MSVC AddressSanitizer requires VS 2019 16.9+ and has different semantics
    # Uncomment if needed: $<$<CONFIG:Debug>:/fsanitize=address>
elseif(APPLE)
    # macOS: Clang compiler flags
    target_compile_options(${BINARY_NAME} PRIVATE
        -Wall
        -Wextra
        $<$<CONFIG:Debug>:-g -O0>        # Debug symbols + no optimization
        $<$<CONFIG:Release>:-O3>         # Max optimization
    )
    # Note: macOS Clang may have issues with UBSan, ASan usually works
    # Uncomment if needed: $<$<CONFIG:Debug>:-fsanitize=address>
    # Link flags for sanitizers (if enabled above)
    # target_link_options(${BINARY_NAME} PRIVATE $<$<CONFIG:Debug>:-fsanitize=address>)
else()
    # Linux: GCC/Clang compiler flags (original)
    target_compile_options(${BINARY_NAME} PRIVATE
        -Wall
        -Wextra
        $<$<CONFIG:Debug>:-g -O0 -fsanitize=address -fsanitize=undefined>
        $<$<CONFIG:Release>:-O3>
    )
    target_link_options(${BINARY_NAME} PRIVATE
        $<$<CONFIG:Debug>:-fsanitize=address -fsanitize=undefined>
    )
endif()
```

**File:** `drunk_call_service/CMakeLists.txt`
**Location:** Replace lines 105-114

---

### 3. Fix pkg-config Dependency (CMakeLists.txt:11-31)

**Problem:**
`pkg-config` is Unix/Linux tool, not standard on Windows. macOS has it via Homebrew, Windows doesn't.

**Current Code:**
```cmake
find_package(PkgConfig REQUIRED)

pkg_check_modules(GST REQUIRED
    gstreamer-1.0
    gstreamer-webrtc-1.0
    gstreamer-sdp-1.0
)

pkg_check_modules(SPDLOG REQUIRED spdlog)
pkg_check_modules(GLIB REQUIRED glib-2.0)
```

**Fix (Windows uses find_package, Unix uses pkg-config):**
```cmake
if(WIN32)
    # Windows: Use CMake find_package() with config files
    # vcpkg and GStreamer Windows installer provide CMake config files

    # Set GStreamer prefix path from environment variable
    if(DEFINED ENV{GSTREAMER_1_0_ROOT_MSVC_X86_64})
        list(APPEND CMAKE_PREFIX_PATH "$ENV{GSTREAMER_1_0_ROOT_MSVC_X86_64}")
    endif()

    # Find packages
    find_package(GStreamer REQUIRED COMPONENTS gstreamer-1.0 gstreamer-webrtc-1.0 gstreamer-sdp-1.0)
    find_package(spdlog REQUIRED)
    find_package(glib REQUIRED)

    # Set variables to match pkg-config format (for compatibility with rest of CMakeLists.txt)
    set(GST_LIBRARIES ${GStreamer_LIBRARIES})
    set(GST_INCLUDE_DIRS ${GStreamer_INCLUDE_DIRS})
    set(SPDLOG_LIBRARIES spdlog::spdlog)
    set(SPDLOG_INCLUDE_DIRS "")  # Handled by target
    set(GLIB_LIBRARIES ${glib_LIBRARIES})
    set(GLIB_INCLUDE_DIRS ${glib_INCLUDE_DIRS})

else()
    # macOS and Linux: Use pkg-config (standard on Unix)
    find_package(PkgConfig REQUIRED)

    pkg_check_modules(GST REQUIRED
        gstreamer-1.0
        gstreamer-webrtc-1.0
        gstreamer-sdp-1.0
    )

    pkg_check_modules(SPDLOG REQUIRED spdlog)
    pkg_check_modules(GLIB REQUIRED glib-2.0)
endif()
```

**File:** `drunk_call_service/CMakeLists.txt`
**Location:** Replace lines 11-31

**Note:** GStreamer Windows CMake config may need adjustments based on installer version. Test and iterate.

---

## High Priority Fixes

### 4. Fix Signal Handling (main.cpp:287-289)

**Problem:**
`SIGTERM` is not raised by Windows OS. Windows needs `SetConsoleCtrlHandler()`.
macOS supports POSIX signals (same as Linux).

**Current Code:**
```cpp
#include <csignal>

void signal_handler(int signum) {
    g_shutdown_requested = true;
}

// In main():
std::signal(SIGINT, signal_handler);
std::signal(SIGTERM, signal_handler);
LOG_INFO("Signal handlers registered (SIGINT, SIGTERM)");
```

**Fix (Windows vs POSIX):**
```cpp
// At top of file, add platform-specific includes:
#ifdef _WIN32
    #include <windows.h>
#else
    #include <csignal>
#endif

// Replace signal_handler function (lines 165-177):
#ifdef _WIN32
// Windows Console Control Handler
BOOL WINAPI ConsoleCtrlHandler(DWORD dwCtrlType) {
    switch (dwCtrlType) {
        case CTRL_C_EVENT:        // Ctrl+C pressed
        case CTRL_BREAK_EVENT:    // Ctrl+Break pressed
        case CTRL_CLOSE_EVENT:    // Console window closing
            g_shutdown_requested = true;
            return TRUE;  // Handled
        default:
            return FALSE;  // Not handled
    }
}
#else
// POSIX Signal Handler (Linux, macOS)
void signal_handler(int signum) {
    g_shutdown_requested = true;
}
#endif

// In main(), replace signal registration (lines 287-289):
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
```

**File:** `drunk_call_service/src/main.cpp`
**Location:**
- Add includes at top
- Replace lines 165-177 (signal_handler function)
- Replace lines 287-289 (registration)

---

### 5. Fix Audio Plugin Selection (webrtc_session.cpp)

**Problem:**
Code hardcodes `pulsesrc`/`pulsesink` when device is specified.
**macOS doesn't have PulseAudio** - uses CoreAudio.
**Windows uses WASAPI**.

**Current Code (Answerer Audio Source, lines 872-906):**
```cpp
const char *src_name = config_.microphone_device.empty() ?
    "autoaudiosrc" : "pulsesrc";
audio_src_ = gst_element_factory_make(src_name, "audio_src");

if (!config_.microphone_device.empty() && src_name == std::string("pulsesrc")) {
    g_object_set(audio_src_, "device", config_.microphone_device.c_str(), nullptr);
}
```

**Fix (3-Platform Audio Source):**
```cpp
// Platform-specific audio source selection
const char *src_name;
if (config_.microphone_device.empty()) {
    // No device specified: use auto-detection (works on all platforms)
    src_name = "autoaudiosrc";
} else {
    // Device specified: use platform-specific source
    #ifdef _WIN32
        src_name = "wasapisrc";      // Windows Audio Session API
    #elif __APPLE__
        src_name = "osxaudiosrc";    // macOS CoreAudio
    #else
        src_name = "pulsesrc";       // Linux PulseAudio
    #endif
}

audio_src_ = gst_element_factory_make(src_name, "audio_src");
if (!audio_src_) {
    LOG_ERROR("[WebRTCSession] Failed to create audio source: {}", src_name);
    return false;
}

// Set device property if specified
if (!config_.microphone_device.empty()) {
    g_object_set(audio_src_, "device", config_.microphone_device.c_str(), nullptr);
    LOG_INFO("[WebRTCSession] ✓ Set microphone device: {} (using {})",
             config_.microphone_device, src_name);
} else {
    LOG_INFO("[WebRTCSession] Using {} (default device)", src_name);
}
```

**Apply Same Fix to 3 Locations:**

1. **Answerer Audio Source** (lines 872-906) - Fixed above
2. **Offerer Audio Source** (lines 1100-1134) - Same pattern
3. **Receiver Audio Sink** (lines 2042-2051) - Use `wasapisink`/`osxaudiosink`/`pulsesink`

**Example for Audio Sink:**
```cpp
const char *sink_name;
if (config_.speaker_device.empty()) {
    sink_name = "autoaudiosink";
} else {
    #ifdef _WIN32
        sink_name = "wasapisink";
    #elif __APPLE__
        sink_name = "osxaudiosink";
    #else
        sink_name = "pulsesink";
    #endif
}
```

**File:** `drunk_call_service/src/webrtc_session.cpp`
**Locations:** Lines 872-906, 1100-1134, 2042-2051

---

## Medium Priority

### 6. Verify Device Enumerator (device_enumerator.cpp)

**Status:**
Code **already has** Windows and macOS platform detection! Just needs testing.

**Current Code (lines 121-146):**
```cpp
#ifdef _WIN32
    #define OS_FAMILY "Windows"
#elif __APPLE__
    #define OS_FAMILY "macOS"
#else
    #define OS_FAMILY "Linux"
#endif

// Later in device parsing:
switch (OS_FAMILY[0]) {  // 'L' = Linux, 'W' = Windows, 'm' = macOS
    case 'L':  // Linux - PulseAudio/PipeWire
        id = gst_structure_get_string(props, "node.name");
        // ...
    case 'W':  // Windows - WASAPI
        id = gst_structure_get_string(props, "device.id");
        // ...
    case 'm':  // macOS - CoreAudio
        id = gst_structure_get_string(props, "device.id");
        // ...
```

**Recommendation:**
- ✅ Code structure is good
- ⚠️ **MUST TEST** on Windows and macOS to verify property names
- ℹ️ Consider replacing `OS_FAMILY[0]` with proper `#ifdef` for clarity

**File:** `drunk_call_service/src/device_enumerator.cpp`
**Lines:** 121-146, 181-207
**Action:** Test on Windows/macOS, adjust property names if needed

---

## Complete Code Templates

### Platform Detection Header (Reusable Pattern)

Create `drunk_call_service/src/platform_defs.h`:

```cpp
#pragma once

// Platform detection macros
#ifdef _WIN32
    #define PLATFORM_WINDOWS
    #define PLATFORM_NAME "Windows"
#elif __APPLE__
    #define PLATFORM_MACOS
    #define PLATFORM_NAME "macOS"
#elif __linux__
    #define PLATFORM_LINUX
    #define PLATFORM_NAME "Linux"
#else
    #define PLATFORM_UNKNOWN
    #define PLATFORM_NAME "Unknown"
#endif

// Audio backend selection
#ifdef PLATFORM_WINDOWS
    #define AUDIO_SOURCE_PLUGIN "wasapisrc"
    #define AUDIO_SINK_PLUGIN "wasapisink"
    #define AUDIO_BACKEND "WASAPI"
#elif PLATFORM_MACOS
    #define AUDIO_SOURCE_PLUGIN "osxaudiosrc"
    #define AUDIO_SINK_PLUGIN "osxaudiosink"
    #define AUDIO_BACKEND "CoreAudio"
#else  // Linux
    #define AUDIO_SOURCE_PLUGIN "pulsesrc"
    #define AUDIO_SINK_PLUGIN "pulsesink"
    #define AUDIO_BACKEND "PulseAudio"
#endif
```

**Usage:**
```cpp
#include "platform_defs.h"

LOG_INFO("Running on platform: {}", PLATFORM_NAME);
LOG_INFO("Audio backend: {}", AUDIO_BACKEND);

const char* src = config_.device.empty() ? "autoaudiosrc" : AUDIO_SOURCE_PLUGIN;
```

---

### Audio Pipeline Helper Functions

Add to `webrtc_session.cpp` or new `audio_utils.cpp`:

```cpp
/**
 * Get platform-specific audio source plugin name.
 *
 * @param use_default If true, return "autoaudiosrc" (cross-platform auto-selection)
 * @return Plugin name string
 */
const char* get_audio_source_plugin(bool use_default = false) {
    if (use_default) {
        return "autoaudiosrc";
    }

    #ifdef _WIN32
        return "wasapisrc";
    #elif __APPLE__
        return "osxaudiosrc";
    #else
        return "pulsesrc";
    #endif
}

/**
 * Get platform-specific audio sink plugin name.
 *
 * @param use_default If true, return "autoaudiosink" (cross-platform auto-selection)
 * @return Plugin name string
 */
const char* get_audio_sink_plugin(bool use_default = false) {
    if (use_default) {
        return "autoaudiosink";
    }

    #ifdef _WIN32
        return "wasapisink";
    #elif __APPLE__
        return "osxaudiosink";
    #else
        return "pulsesink";
    #endif
}
```

**Usage:**
```cpp
const char* src_name = get_audio_source_plugin(config_.microphone_device.empty());
audio_src_ = gst_element_factory_make(src_name, "audio_src");
```

---

## Build Instructions

### Linux Build (Reference)

```bash
cd drunk_call_service
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build .
./bin/drunk-call-service-linux --help
```

---

### Windows Build

```cmd
cd drunk_call_service
mkdir build
cd build

REM Configure with vcpkg toolchain
cmake .. -DCMAKE_TOOLCHAIN_FILE=C:\vcpkg\scripts\buildsystems\vcpkg.cmake -A x64

REM Build
cmake --build . --config Release

REM Run
bin\Release\drunk-call-service-windows.exe --help
```

**Troubleshooting:**
- Ensure `GSTREAMER_1_0_ROOT_MSVC_X86_64` environment variable is set
- Add GStreamer bin to PATH: `C:\gstreamer\1.0\msvc_x86_64\bin`
- If CMake can't find GStreamer, may need to manually set paths

---

### macOS Build

```bash
cd drunk_call_service
mkdir build && cd build

# Configure (Homebrew provides pkg-config, so build is same as Linux)
cmake .. -DCMAKE_BUILD_TYPE=Release

# Build
cmake --build .

# Run
./bin/drunk-call-service-darwin --help
```

**Troubleshooting:**
- Ensure Homebrew packages are installed
- If pkg-config fails, run: `brew link gstreamer pkg-config`
- Verify GStreamer plugins: `gst-inspect-1.0 osxaudiosrc`

---

## Testing Checklist

### All Platforms

After implementing fixes, test:

- [ ] Service starts without crashing
- [ ] Log file created in correct location
- [ ] Logger outputs to file (not stdout/stderr)
- [ ] Ctrl+C / Ctrl+Break gracefully shuts down service
- [ ] gRPC server accepts connections on `127.0.0.1:50051`

### Device Testing

```bash
# Linux
./drunk-call-service-linux --test-devices

# macOS
./drunk-call-service-darwin --test-devices

# Windows
drunk-call-service-windows.exe --test-devices
```

Verify:
- [ ] Audio input devices listed
- [ ] Audio output devices listed
- [ ] Default devices detected correctly
- [ ] Device IDs match platform format:
  - Linux: `alsa_input.pci-...` (PulseAudio/PipeWire)
  - macOS: Numeric ID or name
  - Windows: GUID `{...}`

### WebRTC Session Testing

- [ ] WebRTC session creation succeeds
- [ ] Audio pipeline initializes (source → encoder → webrtcbin)
- [ ] Specific device selection works (if provided)
- [ ] Default device selection works (autoaudiosrc)
- [ ] Calls connect successfully
- [ ] Audio flows in both directions

---

## Platform-Specific Notes

### Linux
- **Audio:** PulseAudio or PipeWire (both supported)
- **Logging:** `/proc/self/exe` works natively
- **Signals:** SIGINT, SIGTERM work as expected
- **Status:** ✅ Primary development platform, fully working

### macOS
- **Audio:** CoreAudio (no PulseAudio)
- **Logging:** Must use `_NSGetExecutablePath()`
- **Signals:** POSIX signals work (same as Linux)
- **GStreamer:** Installed via Homebrew, plugins in `/usr/local/lib/gstreamer-1.0/`
- **Status:** ⚠️ Needs fixes implemented and tested

### Windows
- **Audio:** WASAPI (Windows Audio Session API)
- **Logging:** Must use `GetModuleFileName()` Win32 API
- **Signals:** Must use `SetConsoleCtrlHandler()` (no SIGTERM)
- **GStreamer:** Installed via MSI, plugins in `C:\gstreamer\...\lib\gstreamer-1.0\`
- **Compiler:** MSVC (not GCC), different flags required
- **Build System:** vcpkg for dependencies (no pkg-config)
- **Status:** ⚠️ Needs critical fixes implemented and tested

---

## Files Analyzed

✅ **Complete analysis of:**
- `drunk_call_service/src/*.cpp` (all source files)
- `drunk_call_service/src/*.h` (all headers)
- `drunk_call_service/CMakeLists.txt`

**Analysis Date:** 2026-03-12
**Tool:** Plan agent (comprehensive 3-platform scan)

---

## Summary: Implementation Priority

### Phase 1: Critical (Blocking Builds)
1. main.cpp: Executable path (Windows, macOS)
2. CMakeLists.txt: Compiler flags (Windows)
3. CMakeLists.txt: pkg-config → find_package (Windows)

### Phase 2: High (Blocking Functionality)
4. main.cpp: Signal handling (Windows)
5. webrtc_session.cpp: Audio plugin selection (Windows, macOS)

### Phase 3: Testing & Verification
6. device_enumerator.cpp: Test on Windows, macOS
7. Build and test on each platform
8. Iterate on platform-specific issues

---

**End of Document**
