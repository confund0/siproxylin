cmake_minimum_required(VERSION 3.15)
project(drunk_call_service VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ============================================================================
# Find Dependencies
# ============================================================================

if(WIN32)
    # Windows: Use CMake find_package() with config files
    # vcpkg and GStreamer Windows installer provide CMake config files

    # Set GStreamer prefix path from environment variable
    if(DEFINED ENV{GSTREAMER_1_0_ROOT_MSVC_X86_64})
        list(APPEND CMAKE_PREFIX_PATH "$ENV{GSTREAMER_1_0_ROOT_MSVC_X86_64}")
    endif()

    # Find packages
    find_package(GStreamer REQUIRED COMPONENTS gstreamer-1.0 gstreamer-webrtc-1.0 gstreamer-sdp-1.0)
    find_package(Protobuf REQUIRED)
    find_package(gRPC CONFIG REQUIRED)
    find_package(spdlog REQUIRED)
    find_package(Threads REQUIRED)
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

    # GStreamer packages
    pkg_check_modules(GST REQUIRED
        gstreamer-1.0
        gstreamer-webrtc-1.0
        gstreamer-sdp-1.0
    )

    # gRPC and Protobuf
    find_package(Protobuf REQUIRED)
    find_package(gRPC CONFIG REQUIRED)

    # spdlog for logging
    pkg_check_modules(SPDLOG REQUIRED spdlog)

    # Threads
    find_package(Threads REQUIRED)

    # GLib (for g_main_loop)
    pkg_check_modules(GLIB REQUIRED glib-2.0)
endif()

# ============================================================================
# Generate Protobuf and gRPC C++ sources
# ============================================================================

set(PROTO_DIR "${CMAKE_SOURCE_DIR}/proto")
set(PROTO_FILE "${PROTO_DIR}/call.proto")
set(GENERATED_DIR "${CMAKE_BINARY_DIR}/generated")

file(MAKE_DIRECTORY ${GENERATED_DIR})

set(PROTO_SRCS "${GENERATED_DIR}/call.pb.cc")
set(PROTO_HDRS "${GENERATED_DIR}/call.pb.h")
set(GRPC_SRCS "${GENERATED_DIR}/call.grpc.pb.cc")
set(GRPC_HDRS "${GENERATED_DIR}/call.grpc.pb.h")

add_custom_command(
    OUTPUT ${PROTO_SRCS} ${PROTO_HDRS} ${GRPC_SRCS} ${GRPC_HDRS}
    COMMAND ${Protobuf_PROTOC_EXECUTABLE}
    ARGS --grpc_out=${GENERATED_DIR}
         --cpp_out=${GENERATED_DIR}
         -I ${PROTO_DIR}
         --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>
         ${PROTO_FILE}
    DEPENDS ${PROTO_FILE}
    COMMENT "Generating protobuf and gRPC C++ sources"
)

# ============================================================================
# Service Binary (platform-specific naming)
# ============================================================================

# Determine platform suffix
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set(PLATFORM_SUFFIX "linux")
elseif(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    set(PLATFORM_SUFFIX "darwin")
elseif(CMAKE_SYSTEM_NAME STREQUAL "Windows")
    set(PLATFORM_SUFFIX "windows")
else()
    set(PLATFORM_SUFFIX "unknown")
endif()

set(BINARY_NAME "drunk-call-service-${PLATFORM_SUFFIX}")

add_executable(${BINARY_NAME}
    src/main.cpp
    src/call_service_impl.cpp
    src/webrtc_session.cpp
    src/session_manager.cpp
    src/device_enumerator.cpp
    src/logger.cpp
    ${PROTO_SRCS}
    ${GRPC_SRCS}
)

target_include_directories(${BINARY_NAME} PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${GENERATED_DIR}
    ${GST_INCLUDE_DIRS}
    ${GLIB_INCLUDE_DIRS}
    ${SPDLOG_INCLUDE_DIRS}
)

target_link_libraries(${BINARY_NAME} PRIVATE
    ${GST_LIBRARIES}
    ${GLIB_LIBRARIES}
    ${SPDLOG_LIBRARIES}
    gRPC::grpc++
    protobuf::libprotobuf
    Threads::Threads
)

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

# ============================================================================
# Installation
# ============================================================================

install(TARGETS ${BINARY_NAME}
    RUNTIME DESTINATION ${CMAKE_SOURCE_DIR}/bin
)

# ============================================================================
# Testing (standalone tests in tests/standalone/ use their own Makefile)
# ============================================================================

enable_testing()

# TODO: Add integration tests here (Phase 4.8)
# For now, standalone tests are built with tests/standalone/Makefile
