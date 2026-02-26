cmake_minimum_required(VERSION 3.20)
project(drunk_call_service VERSION 0.1.0 LANGUAGES CXX)

# C++17 required
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Compiler flags
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra")
set(CMAKE_CXX_FLAGS_DEBUG "-g -O0 -fsanitize=address -fsanitize=undefined")
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG")

# Enable spdlog DEBUG macros even in Release builds
# SPDLOG_ACTIVE_LEVEL: 0=TRACE, 1=DEBUG, 2=INFO (default is INFO in Release)
add_compile_definitions(SPDLOG_ACTIVE_LEVEL=SPDLOG_LEVEL_DEBUG)

# Find dependencies
find_package(Protobuf REQUIRED)
find_package(gRPC REQUIRED)
find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)

# GStreamer dependencies
pkg_check_modules(GSTREAMER REQUIRED gstreamer-1.0 gstreamer-webrtc-1.0 gstreamer-sdp-1.0 gstreamer-app-1.0 gstreamer-rtp-1.0)

# libnice for ICE (2-component support)
pkg_check_modules(NICE REQUIRED nice>=0.1.18)

# GnuTLS for DTLS (Phase 3)
pkg_check_modules(GNUTLS REQUIRED gnutls>=3.6.0)

# libsrtp2 for SRTP encryption (Phase 3)
pkg_check_modules(SRTP REQUIRED libsrtp2>=2.3.0)

# Find gRPC C++ plugin (sometimes CMake doesn't set this correctly)
find_program(GRPC_CPP_PLUGIN_EXECUTABLE grpc_cpp_plugin)
if(NOT GRPC_CPP_PLUGIN_EXECUTABLE)
    message(FATAL_ERROR "grpc_cpp_plugin not found")
endif()
message(STATUS "Using gRPC C++ plugin: ${GRPC_CPP_PLUGIN_EXECUTABLE}")

# spdlog for logging with rotation
find_package(spdlog REQUIRED)
message(STATUS "Using spdlog for logging")

# GStreamer
message(STATUS "Using GStreamer: ${GSTREAMER_VERSION}")
message(STATUS "GStreamer include dirs: ${GSTREAMER_INCLUDE_DIRS}")
message(STATUS "GStreamer libraries: ${GSTREAMER_LIBRARIES}")

# Platform-specific settings
if(UNIX AND NOT APPLE)
    set(PLATFORM_NAME "linux")
    # GStreamer unstable API
    add_definitions(-DGST_USE_UNSTABLE_API)
    find_package(ALSA QUIET)
    if(ALSA_FOUND)
        set(PLATFORM_LIBS ${ALSA_LIBRARIES} pthread dl)
    else()
        message(WARNING "ALSA not found - audio device enumeration will be limited")
        set(PLATFORM_LIBS pthread dl)
    endif()
elseif(WIN32)
    set(PLATFORM_NAME "windows")
    add_definitions(-DGST_USE_UNSTABLE_API)
    set(PLATFORM_LIBS winmm dmoguids wmcodecdspuuid msdmo strmiids)
elseif(APPLE)
    set(PLATFORM_NAME "darwin")
    add_definitions(-DGST_USE_UNSTABLE_API)
    find_library(COREAUDIO_LIBRARY CoreAudio)
    find_library(AUDIOUNIT_LIBRARY AudioUnit)
    find_library(AVFOUNDATION_LIBRARY AVFoundation)
    set(PLATFORM_LIBS ${COREAUDIO_LIBRARY} ${AUDIOUNIT_LIBRARY} ${AVFOUNDATION_LIBRARY})
endif()

# Generate gRPC/Protobuf sources
set(PROTO_PATH "${CMAKE_SOURCE_DIR}/proto")
set(GENERATED_PROTOBUF_PATH "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY ${GENERATED_PROTOBUF_PATH})

set(CALL_PROTO "${PROTO_PATH}/call.proto")
set(CALL_PB_CPP "${GENERATED_PROTOBUF_PATH}/call.pb.cc")
set(CALL_PB_H "${GENERATED_PROTOBUF_PATH}/call.pb.h")
set(CALL_GRPC_PB_CPP "${GENERATED_PROTOBUF_PATH}/call.grpc.pb.cc")
set(CALL_GRPC_PB_H "${GENERATED_PROTOBUF_PATH}/call.grpc.pb.h")

add_custom_command(
    OUTPUT "${CALL_PB_CPP}" "${CALL_PB_H}" "${CALL_GRPC_PB_CPP}" "${CALL_GRPC_PB_H}"
    COMMAND ${PROTOBUF_PROTOC_EXECUTABLE}
    ARGS --grpc_out "${GENERATED_PROTOBUF_PATH}"
         --cpp_out "${GENERATED_PROTOBUF_PATH}"
         -I "${PROTO_PATH}"
         --plugin=protoc-gen-grpc="${GRPC_CPP_PLUGIN_EXECUTABLE}"
         "${CALL_PROTO}"
    DEPENDS "${CALL_PROTO}"
    COMMENT "Generating gRPC/Protobuf C++ sources"
)

# Include directories
include_directories(
    ${CMAKE_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/src
    ${GENERATED_PROTOBUF_PATH}
    ${PROTOBUF_INCLUDE_DIRS}
    ${GRPC_INCLUDE_DIRS}
    ${GSTREAMER_INCLUDE_DIRS}
    ${NICE_INCLUDE_DIRS}
    ${GNUTLS_INCLUDE_DIRS}
    ${SRTP_INCLUDE_DIRS}
)

# Source files
set(SOURCES
    src/main.cc
    src/logger.cc
    src/call_server.cc
    src/session.cc
    src/ice_agent.cc
    src/srtp_session.cc
    src/dtls_srtp_handler.cc
    src/audio_track.cc
    src/video_track.cc
    src/device_manager.cc
    src/stats_collector.cc
    ${CALL_PB_CPP}
    ${CALL_GRPC_PB_CPP}
)

# Platform-specific sources
if(UNIX AND NOT APPLE)
    list(APPEND SOURCES src/platform/devices_linux.cc)
elseif(WIN32)
    list(APPEND SOURCES src/platform/devices_windows.cc)
elseif(APPLE)
    list(APPEND SOURCES src/platform/devices_darwin.cc)
endif()

# Executable
add_executable(drunk-call-service-${PLATFORM_NAME} ${SOURCES})

# Link libraries
target_link_libraries(drunk-call-service-${PLATFORM_NAME}
    gRPC::grpc++
    ${PROTOBUF_LIBRARIES}
    spdlog::spdlog
    ${GSTREAMER_LIBRARIES}
    ${NICE_LIBRARIES}
    ${GNUTLS_LIBRARIES}
    ${SRTP_LIBRARIES}
    ${CMAKE_THREAD_LIBS_INIT}
    ${PLATFORM_LIBS}
)

# Install
install(TARGETS drunk-call-service-${PLATFORM_NAME}
    RUNTIME DESTINATION ${CMAKE_SOURCE_DIR}/bin
)

# Unit tests (optional, if gtest available)
option(BUILD_TESTS "Build unit tests" ON)
if(BUILD_TESTS)
    find_package(GTest QUIET)
    if(GTest_FOUND)
        enable_testing()
        add_subdirectory(tests)
    else()
        message(STATUS "GTest not found, skipping tests")
    endif()
endif()
