# Makefile wrapper for CMake-based build
#
# Windows Dependency Strategy:
# - Base libs (GLib, OpenSSL, zlib, etc.) from GStreamer installation
# - gRPC stack (grpc++, protobuf, abseil, re2, cares) from vcpkg
# - spdlog from vcpkg
#
# This ensures binary compatibility between .exe and GStreamer plugins.

BUILD_DIR := build
BIN_DIR := bin
VCPKG_ROOT ?= C:/vcpkg

.PHONY: all clean debug release test install help windbg winrel

all: release

# Release build (Linux/macOS)
release:
	@mkdir -p $(BUILD_DIR)
	@cd $(BUILD_DIR) && cmake -DCMAKE_BUILD_TYPE=Release .. && $(MAKE)
	@cmake --install $(BUILD_DIR)

# Debug build (Linux/macOS with sanitizers)
debug:
	@mkdir -p $(BUILD_DIR)
	@cd $(BUILD_DIR) && cmake -DCMAKE_BUILD_TYPE=Debug .. && $(MAKE)
	@cmake --install $(BUILD_DIR)

# Windows Debug build (MSVC)
windbg:
	@mkdir -p $(BUILD_DIR)
	@cd $(BUILD_DIR) && cmake \
		-DCMAKE_TOOLCHAIN_FILE=$(VCPKG_ROOT)/scripts/buildsystems/vcpkg.cmake \
		-DCMAKE_IGNORE_PATH="/mingw64/lib;/mingw64/bin;C:/msys64/mingw64/lib;C:/msys64/mingw64/bin" \
		-A x64 .. && cmake --build . --config Debug
	@cmake --install $(BUILD_DIR) --config Debug
	@echo "Copying ONLY grpc/protobuf/spdlog vcpkg DLLs to $(BIN_DIR)/"
	@echo "  (GLib, OpenSSL, pcre2, iconv, etc. come from GStreamer)"
	@for dll in abseil_dll.dll cares.dll re2.dll libprotobuf.dll libprotobuf-lite.dll spdlog.dll zlib1.dll upb*.dll; do \
		if [ -f "$(VCPKG_ROOT)/installed/x64-windows/debug/bin/$$dll" ]; then \
			cp "$(VCPKG_ROOT)/installed/x64-windows/debug/bin/$$dll" "$(BIN_DIR)/" 2>/dev/null || true; \
		elif [ -f "$(VCPKG_ROOT)/installed/x64-windows/bin/$$dll" ]; then \
			cp "$(VCPKG_ROOT)/installed/x64-windows/bin/$$dll" "$(BIN_DIR)/" 2>/dev/null || true; \
		fi; \
	done
	@echo "Build complete: $(BIN_DIR)/ contains binary + minimal vcpkg DLLs"
	@echo "NOTE: Ensure GStreamer DLLs are in lib/gstreamer/ for runtime"

# Windows Release build (MSVC)
winrel:
	@mkdir -p $(BUILD_DIR)
	@cd $(BUILD_DIR) && cmake \
		-DCMAKE_TOOLCHAIN_FILE=$(VCPKG_ROOT)/scripts/buildsystems/vcpkg.cmake \
		-DCMAKE_IGNORE_PATH="/mingw64/lib;/mingw64/bin;C:/msys64/mingw64/lib;C:/msys64/mingw64/bin" \
		-A x64 .. && cmake --build . --config Release
	@cmake --install $(BUILD_DIR) --config Release
	@echo "Copying ONLY grpc/protobuf/spdlog vcpkg DLLs to $(BIN_DIR)/"
	@echo "  (GLib, OpenSSL, pcre2, iconv, etc. come from GStreamer)"
	@for dll in abseil_dll.dll cares.dll re2.dll libprotobuf.dll libprotobuf-lite.dll spdlog.dll zlib1.dll upb*.dll; do \
		if [ -f "$(VCPKG_ROOT)/installed/x64-windows/bin/$$dll" ]; then \
			cp "$(VCPKG_ROOT)/installed/x64-windows/bin/$$dll" "$(BIN_DIR)/" 2>/dev/null || true; \
		fi; \
	done
	@echo "Build complete: $(BIN_DIR)/ contains binary + minimal vcpkg DLLs"
	@echo "NOTE: Ensure GStreamer DLLs are in lib/gstreamer/ for runtime"

# Run tests
test: release
	@cd $(BUILD_DIR) && ctest --output-on-failure

# Install to bin/
install: release
	@echo "Binary installed to $(BIN_DIR)/"

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	@if [ -d "$(BUILD_DIR)" ]; then \
		echo "Removing build directory: $(BUILD_DIR)/"; \
		find $(BUILD_DIR) -type f | wc -l | xargs -I {} echo "  {} files in build/"; \
		du -sh $(BUILD_DIR) | awk '{print "  Size: " $$1}'; \
		rm -rf $(BUILD_DIR); \
		echo "  ✓ $(BUILD_DIR)/ removed"; \
	else \
		echo "  $(BUILD_DIR)/ does not exist (already clean)"; \
	fi
	@if [ -d "$(BIN_DIR)" ]; then \
		echo "Removing binaries: $(BIN_DIR)/"; \
		ls -lh $(BIN_DIR)/ 2>/dev/null | grep -v "^total" | awk '{print "  " $$9 " (" $$5 ")"}' || true; \
		rm -rf $(BIN_DIR); \
		echo "  ✓ $(BIN_DIR)/ removed"; \
	else \
		echo "  $(BIN_DIR)/ does not exist (already clean)"; \
	fi
	@echo "Clean complete"

# Download LibWebRTC prebuilt binaries (placeholder - manual step for now)
deps:
	@echo "Downloading LibWebRTC prebuilt binaries..."
	@echo "TODO: Implement automatic download from https://github.com/webrtc-sdk/libwebrtc/releases"
	@echo "For now, manually download and extract to third_party/libwebrtc/"

# Check dependencies
check-deps:
	@echo "Checking build dependencies..."
	@command -v cmake >/dev/null 2>&1 || { echo "ERROR: cmake not found"; exit 1; }
	@command -v protoc >/dev/null 2>&1 || { echo "ERROR: protoc not found"; exit 1; }
	@command -v grpc_cpp_plugin >/dev/null 2>&1 || { echo "ERROR: grpc_cpp_plugin not found"; exit 1; }
	@pkg-config --exists grpc++ || { echo "ERROR: libgrpc++-dev not found"; exit 1; }
	@pkg-config --exists protobuf || { echo "ERROR: libprotobuf-dev not found"; exit 1; }
	@echo "All dependencies found!"

# Help
help:
	@echo "Drunk Call Service C++ Build System"
	@echo ""
	@echo "Targets:"
	@echo "  make           - Build release version (Linux/macOS)"
	@echo "  make release   - Build optimized release binary (Linux/macOS)"
	@echo "  make debug     - Build with debug symbols and sanitizers (Linux/macOS)"
	@echo "  make winrel    - Build optimized release binary (Windows/MSVC)"
	@echo "  make windbg    - Build debug binary (Windows/MSVC)"
	@echo "  make test      - Run unit tests"
	@echo "  make clean     - Remove build artifacts"
	@echo "  make deps      - Download LibWebRTC prebuilt binaries"
	@echo "  make check-deps - Verify all build dependencies"
	@echo "  make install   - Install binary to bin/"
	@echo ""
	@echo "Build outputs:"
	@echo "  Binary: bin/drunk-call-service-{linux|windows|darwin}"
	@echo "  Build dir: build/"
	@echo ""
	@echo "Windows builds:"
	@echo "  Set VCPKG_ROOT if not in C:/vcpkg: make winrel VCPKG_ROOT=D:/vcpkg"
