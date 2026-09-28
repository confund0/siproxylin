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

# Patched GStreamer dtls plugin (Linux only, GStreamer 1.26).
# GStreamer before 1.28 makes an RSA default DTLS certificate. Peers that offer
# only ECDHE-ECDSA suites (for example Conversations) then fail with
# "no shared cipher". The patch makes the default certificate ECDSA P-256,
# the same as GStreamer 1.28. We build the plugin from the Debian source of
# the installed gst-plugins-bad, because the service links against Debian's
# GStreamer. Only GStreamer 1.26 is patched: 1.28 has the fix, and older
# versions (1.22 on Debian 12) build as before the patch. Set SKIP_GST_DTLS=1
# to skip this step (calls with ECDSA-only peers then fail).
GST_DTLS_PKG := gstreamer1.0-plugins-bad
GST_DTLS_PATCH := patches/gstreamer/0001-dtls-use-ECDSA-default-certificate.patch
GST_DTLS_SRC_DIR := $(BUILD_DIR)/gst-plugins-bad-src
GST_DTLS_BUILD_DIR := $(GST_DTLS_SRC_DIR)/build-dtls
GST_DTLS_OUT_DIR := $(BIN_DIR)/gst-plugins
GST_DTLS_OUT := $(GST_DTLS_OUT_DIR)/libgstdtls.so
GST_DTLS_STAMP := $(GST_DTLS_OUT_DIR)/libgstdtls.so.stamp
GST_DTLS_MARKER := ECDSA P-256 private key
SKIP_GST_DTLS ?= 0

.PHONY: all clean debug release test install help windbg winrel gst-dtls deps check-deps

all: release

# Release build (Linux/macOS)
release: gst-dtls
	@mkdir -p $(BUILD_DIR)
	@cd $(BUILD_DIR) && cmake -DCMAKE_BUILD_TYPE=Release .. && $(MAKE)
	@cmake --install $(BUILD_DIR)

# Debug build (Linux/macOS with sanitizers)
debug: gst-dtls
	@mkdir -p $(BUILD_DIR)
	@cd $(BUILD_DIR) && cmake -DCMAKE_BUILD_TYPE=Debug .. && $(MAKE)
	@cmake --install $(BUILD_DIR)

# Patched GStreamer dtls plugin (Linux only, see GST_DTLS_* above)
# Steps: check version, get Debian source, apply patch, build only ext/dtls,
# check the ECDSA marker, copy to bin/gst-plugins/. A stamp file with the
# Debian source version and the patch checksum skips all of this when
# nothing changed.
gst-dtls:
	@set -e; \
	if [ "$(SKIP_GST_DTLS)" = "1" ]; then \
		echo "gst-dtls: skipped (SKIP_GST_DTLS=1). ECDSA-only peers will fail DTLS."; \
		exit 0; \
	fi; \
	if [ "$$(uname -s)" != "Linux" ]; then \
		echo "gst-dtls: not Linux, skipped"; \
		exit 0; \
	fi; \
	if ! command -v pkg-config >/dev/null 2>&1 || ! pkg-config --exists gstreamer-1.0; then \
		echo "ERROR: gst-dtls: pkg-config or gstreamer-1.0 not found."; \
		echo "  Install the packages pkg-config and libgstreamer1.0-dev."; \
		exit 1; \
	fi; \
	gst_ver=$$(pkg-config --modversion gstreamer-1.0); \
	case "$$gst_ver" in \
	1.26.*) ;; \
	*) \
		echo "gst-dtls: GStreamer $$gst_ver, the patch is only for 1.26, the system plugin is used"; \
		if [ -e "$(GST_DTLS_OUT)" ]; then \
			echo "gst-dtls: removing old $(GST_DTLS_OUT)"; \
		fi; \
		rm -f "$(GST_DTLS_OUT)" "$(GST_DTLS_STAMP)"; \
		exit 0 ;; \
	esac; \
	if ! command -v dpkg-query >/dev/null 2>&1 || ! command -v apt-get >/dev/null 2>&1; then \
		echo "ERROR: gst-dtls: dpkg-query/apt-get not found. This step needs a Debian system."; \
		echo "  GStreamer $$gst_ver needs the patched dtls plugin for ECDSA-only peers."; \
		echo "  Run 'make SKIP_GST_DTLS=1' to build without it."; \
		exit 1; \
	fi; \
	src_pkg=$$(dpkg-query -W -f='$${source:Package}' $(GST_DTLS_PKG) 2>/dev/null || true); \
	src_ver=$$(dpkg-query -W -f='$${source:Version}' $(GST_DTLS_PKG) 2>/dev/null || true); \
	if [ -z "$$src_pkg" ] || [ -z "$$src_ver" ]; then \
		echo "ERROR: gst-dtls: package $(GST_DTLS_PKG) is not installed."; \
		echo "  Install $(GST_DTLS_PKG). The plugin is built from its Debian source."; \
		exit 1; \
	fi; \
	patch_sum=$$(sha256sum "$(GST_DTLS_PATCH)" | cut -d' ' -f1); \
	stamp="$$src_pkg $$src_ver $$patch_sum"; \
	if [ -f "$(GST_DTLS_OUT)" ] && [ -f "$(GST_DTLS_STAMP)" ] && \
	   [ "$$(cat "$(GST_DTLS_STAMP)")" = "$$stamp" ]; then \
		echo "gst-dtls: $(GST_DTLS_OUT) is up to date ($$src_pkg $$src_ver)"; \
		exit 0; \
	fi; \
	missing=""; \
	command -v dpkg-source >/dev/null 2>&1 || missing="$$missing dpkg-dev"; \
	command -v meson >/dev/null 2>&1 || missing="$$missing meson"; \
	command -v ninja >/dev/null 2>&1 || missing="$$missing ninja-build"; \
	command -v patch >/dev/null 2>&1 || missing="$$missing patch"; \
	command -v cc >/dev/null 2>&1 || missing="$$missing build-essential"; \
	pkg-config --exists openssl || missing="$$missing libssl-dev"; \
	pkg-config --exists gstreamer-plugins-base-1.0 || missing="$$missing libgstreamer-plugins-base1.0-dev"; \
	if [ -n "$$missing" ]; then \
		echo "ERROR: gst-dtls: missing build tools or libraries. Install these packages:"; \
		echo " $$missing"; \
		exit 1; \
	fi; \
	echo "gst-dtls: GStreamer $$gst_ver, building patched dtls plugin from Debian source $$src_pkg $$src_ver"; \
	src_stamp="$(GST_DTLS_SRC_DIR)/.source-stamp"; \
	if [ ! -f "$$src_stamp" ] || [ "$$(cat "$$src_stamp")" != "$$stamp" ]; then \
		if ! apt-get source --print-uris --only-source "$$src_pkg=$$src_ver" >/dev/null 2>&1; then \
			echo "ERROR: gst-dtls: apt cannot find the source package $$src_pkg=$$src_ver."; \
			echo "  For each 'deb' line in the apt sources, add a 'deb-src' line with the"; \
			echo "  same URL, suite and components (deb822 format: add 'deb-src' to"; \
			echo "  'Types:' in debian.sources). Then run 'apt-get update' as root."; \
			echo "  If the installed version is older than the one in the archive,"; \
			echo "  upgrade $(GST_DTLS_PKG) first."; \
			exit 1; \
		fi; \
		rm -rf "$(GST_DTLS_SRC_DIR)"; \
		mkdir -p "$(GST_DTLS_SRC_DIR)"; \
		if ! (cd "$(GST_DTLS_SRC_DIR)" && apt-get source --only-source "$$src_pkg=$$src_ver"); then \
			echo "ERROR: gst-dtls: 'apt-get source $$src_pkg=$$src_ver' failed (see above)."; \
			exit 1; \
		fi; \
		srcdir=$$(find "$(GST_DTLS_SRC_DIR)" -mindepth 1 -maxdepth 1 -type d | head -n 1); \
		if [ -z "$$srcdir" ]; then \
			echo "ERROR: gst-dtls: no unpacked source in $(GST_DTLS_SRC_DIR)"; \
			exit 1; \
		fi; \
		echo "gst-dtls: applying $(GST_DTLS_PATCH)"; \
		if ! patch -d "$$srcdir" -p1 --forward --batch < "$(GST_DTLS_PATCH)"; then \
			echo "ERROR: gst-dtls: the patch does not apply to $$src_pkg $$src_ver."; \
			echo "  Update $(GST_DTLS_PATCH) for this version,"; \
			echo "  or run 'make SKIP_GST_DTLS=1' to build without it."; \
			exit 1; \
		fi; \
		echo "$$stamp" > "$$src_stamp"; \
	fi; \
	srcdir=$$(find "$(GST_DTLS_SRC_DIR)" -mindepth 1 -maxdepth 1 -type d ! -name build-dtls | head -n 1); \
	rm -rf "$(GST_DTLS_BUILD_DIR)"; \
	meson setup "$(GST_DTLS_BUILD_DIR)" "$$srcdir" \
		--buildtype=release \
		-Dauto_features=disabled \
		-Ddtls=enabled \
		-Dtests=disabled \
		-Dexamples=disabled \
		-Ddoc=disabled \
		-Dintrospection=disabled \
		-Dnls=disabled \
		-Dorc=disabled \
		"-Dpackage-name=GStreamer Bad Plug-ins (Debian source, Siproxylin ECDSA patch)" \
		"-Dpackage-origin=https://tracker.debian.org/pkg/$$src_pkg"; \
	ninja -C "$(GST_DTLS_BUILD_DIR)" ext/dtls/libgstdtls.so; \
	built="$(GST_DTLS_BUILD_DIR)/ext/dtls/libgstdtls.so"; \
	if ! grep -q -a -F "$(GST_DTLS_MARKER)" "$$built"; then \
		echo "ERROR: gst-dtls: $$built does not contain '$(GST_DTLS_MARKER)'."; \
		echo "  The patch did not reach the build. The plugin is not installed."; \
		exit 1; \
	fi; \
	mkdir -p "$(GST_DTLS_OUT_DIR)"; \
	cp "$$built" "$(GST_DTLS_OUT).tmp"; \
	mv -f "$(GST_DTLS_OUT).tmp" "$(GST_DTLS_OUT)"; \
	echo "$$stamp" > "$(GST_DTLS_STAMP)"; \
	echo "gst-dtls: installed $(GST_DTLS_OUT) (ECDSA default certificate)"

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
	@echo ""
	@echo "Patched dtls plugin (target gst-dtls, Debian, GStreamer 1.26):"
	@echo "  1. For each 'deb' line in the apt sources, add a 'deb-src' line with the"
	@echo "     same URL, suite and components, for example:"
	@echo "       deb-src https://deb.debian.org/debian stable main contrib"
	@echo "     In the deb822 format (debian.sources), add 'deb-src' to 'Types:'."
	@echo "  2. Install: dpkg-dev meson ninja-build pkg-config libssl-dev"
	@echo "              libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev"
	@echo "     (build-essential and gstreamer1.0-plugins-bad are needed as well)"
	@echo "  3. Run 'apt-get update'."

# Check dependencies
check-deps:
	@echo "Checking build dependencies..."
	@command -v cmake >/dev/null 2>&1 || { echo "ERROR: cmake not found"; exit 1; }
	@command -v protoc >/dev/null 2>&1 || { echo "ERROR: protoc not found"; exit 1; }
	@command -v grpc_cpp_plugin >/dev/null 2>&1 || { echo "ERROR: grpc_cpp_plugin not found"; exit 1; }
	@pkg-config --exists grpc++ || { echo "ERROR: libgrpc++-dev not found"; exit 1; }
	@pkg-config --exists protobuf || { echo "ERROR: libprotobuf-dev not found"; exit 1; }
	@if [ "$(SKIP_GST_DTLS)" != "1" ] && [ "$$(uname -s)" = "Linux" ] && \
	    pkg-config --exists gstreamer-1.0 && case "$$(pkg-config --modversion gstreamer-1.0)" in 1.26.*) true;; *) false;; esac; then \
		echo "GStreamer $$(pkg-config --modversion gstreamer-1.0): checking gst-dtls dependencies..."; \
		command -v apt-get >/dev/null 2>&1 || { echo "ERROR: apt-get not found (gst-dtls needs Debian, or use SKIP_GST_DTLS=1)"; exit 1; }; \
		command -v dpkg-source >/dev/null 2>&1 || { echo "ERROR: dpkg-source not found (install dpkg-dev)"; exit 1; }; \
		command -v meson >/dev/null 2>&1 || { echo "ERROR: meson not found (install meson)"; exit 1; }; \
		command -v ninja >/dev/null 2>&1 || { echo "ERROR: ninja not found (install ninja-build)"; exit 1; }; \
		command -v patch >/dev/null 2>&1 || { echo "ERROR: patch not found (install patch)"; exit 1; }; \
		pkg-config --exists openssl || { echo "ERROR: libssl-dev not found"; exit 1; }; \
		pkg-config --exists gstreamer-plugins-base-1.0 || { echo "ERROR: libgstreamer-plugins-base1.0-dev not found"; exit 1; }; \
		src_pkg=$$(dpkg-query -W -f='$${source:Package}' $(GST_DTLS_PKG) 2>/dev/null || true); \
		src_ver=$$(dpkg-query -W -f='$${source:Version}' $(GST_DTLS_PKG) 2>/dev/null || true); \
		[ -n "$$src_ver" ] || { echo "ERROR: $(GST_DTLS_PKG) not installed"; exit 1; }; \
		apt-get source --print-uris --only-source "$$src_pkg=$$src_ver" >/dev/null 2>&1 || \
			{ echo "ERROR: no source for $$src_pkg=$$src_ver (add deb-src lines, run apt-get update)"; exit 1; }; \
	fi
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
	@echo "  make deps      - Show dependency notes (patched dtls plugin: packages, deb-src)"
	@echo "  make check-deps - Verify all build dependencies"
	@echo "  make install   - Install binary to bin/"
	@echo "  make gst-dtls  - Build patched GStreamer dtls plugin (Linux, GStreamer 1.26;"
	@echo "                   part of release and debug; needs Debian deb-src)"
	@echo ""
	@echo "Build outputs:"
	@echo "  Binary: bin/drunk-call-service-{linux|windows|darwin}"
	@echo "  Patched dtls plugin: bin/gst-plugins/libgstdtls.so"
	@echo "  Build dir: build/ (dtls plugin source: build/gst-plugins-bad-src/)"
	@echo ""
	@echo "Options:"
	@echo "  SKIP_GST_DTLS=1 - Do not build the patched dtls plugin"
	@echo ""
	@echo "Windows builds:"
	@echo "  Set VCPKG_ROOT if not in C:/vcpkg: make winrel VCPKG_ROOT=D:/vcpkg"
