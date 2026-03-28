#!/bin/bash
#
# Siproxylin Package Builder Library
# Reusable functions for creating packages across different platforms
#
# Usage: source .package-builder.sh
#

# =============================================================================
# CONFIGURATION & CONSTANTS
# =============================================================================

PKG_NAME="Siproxylin"
PKG_ID="com.siproxylin"
PYTHON_VERSION="3.11"

# Version from version.sh (mandatory for builds)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/version.sh" ]; then
    source "${SCRIPT_DIR}/version.sh"
    # Normalize: strip v, validate, add v back
    CLEAN="${SIPROXYLIN_VERSION#v}"
    if [[ "$CLEAN" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        SIPROXYLIN_VERSION="v${CLEAN}"
    elif [ "$CLEAN" != "dev" ]; then
        log_error "Invalid version format: $SIPROXYLIN_VERSION (expected X.Y.Z or 'dev')"
        return 1
    fi
else
    log_error "version.sh not found - required for builds"
    log_info "Create version.sh with SIPROXYLIN_VERSION and SIPROXYLIN_CODENAME"
    return 1
fi

# APT cache directory for Linux builds (saves ~300MB downloads)
# This is used by appimage-builder to cache downloaded .deb packages
: "${PKG_APT_CACHE_DIR:=.package-builder-apt}"

# appimage-builder executable path (can be system install or AppImage)
: "${APPIMAGE_BUILDER:=appimage-builder}"

# Language packs to include (affects spell checking dictionaries AND UI locales)
# Single source of truth for all language-related assets
# Format: "lang_code:hunspell_dict_name" (if dict name differs from locale)
PKG_LANGUAGES=(
    "en:en_US"
    "de:de_DE"
    "ru:ru_RU"
    "lt:lt_LT"
    "es:es_ES"
    "ro:ro_RO"
    "ar:ar"
)

# Colors for output
COLOR_RESET='\033[0m'
COLOR_BOLD='\033[1m'
COLOR_GREEN='\033[0;32m'
COLOR_YELLOW='\033[0;33m'
COLOR_RED='\033[0;31m'
COLOR_BLUE='\033[0;34m'

# =============================================================================
# LOGGING FUNCTIONS
# =============================================================================

log_step() {
    echo -e "${COLOR_BOLD}${COLOR_BLUE}[${1}]${COLOR_RESET} ${2}"
}

log_info() {
    echo -e "  ${COLOR_GREEN}→${COLOR_RESET} ${1}"
}

log_success() {
    echo -e "  ${COLOR_GREEN}✓${COLOR_RESET} ${1}"
}

log_warning() {
    echo -e "  ${COLOR_YELLOW}⚠${COLOR_RESET} ${1}"
}

log_warn() {
    log_warning "$1"
}

log_error() {
    echo -e "  ${COLOR_RED}✗${COLOR_RESET} ${1}"
}

print_header() {
    echo ""
    echo -e "${COLOR_BOLD}========================================${COLOR_RESET}"
    echo -e "${COLOR_BOLD}${1}${COLOR_RESET}"
    echo -e "${COLOR_BOLD}========================================${COLOR_RESET}"
    echo ""
}

print_separator() {
    echo ""
}

# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================

check_required_tools() {
    local missing=0

    log_step "TOOLS" "Checking required build tools..."

    # Critical tools (build will fail without these)
    local critical_tools=(
        "patchelf:patchelf --version:CRITICAL for portable AppImage (patches ELF interpreter):apt install patchelf"
        "python3:python3 --version:Python runtime:apt install python3"
        "pip3:pip3 --version:Python package installer:apt install python3-pip"
        "wget:wget --version:Download tool:apt install wget"
        "file:file --version:File type detection:apt install file"
    )

    # Optional but recommended tools
    local optional_tools=(
        "convert:convert --version:ImageMagick for icon conversion:apt install imagemagick"
    )

    # Check critical tools
    for tool_spec in "${critical_tools[@]}"; do
        IFS=':' read -r cmd version_cmd description install_cmd <<< "$tool_spec"

        if ! command -v "$cmd" &> /dev/null; then
            log_error "$cmd not found - $description"
            log_info "Install: $install_cmd"
            missing=1
        else
            local version_output=$($version_cmd 2>&1 | head -1 | cut -c1-60)
            log_success "$cmd: $version_output"
        fi
    done

    # Check appimage-builder separately (can be path or command)
    if ! command -v "$APPIMAGE_BUILDER" &> /dev/null; then
        log_error "appimage-builder not found: $APPIMAGE_BUILDER"
        log_info "Install: pip install appimage-builder"
        log_info "Or download: wget https://github.com/AppImageCrafters/appimage-builder/releases"
        log_info "Then set: export APPIMAGE_BUILDER=/path/to/appimage-builder.AppImage"
        missing=1
    else
        log_success "appimage-builder: $APPIMAGE_BUILDER"
    fi

    # Check optional tools (warnings only)
    for tool_spec in "${optional_tools[@]}"; do
        IFS=':' read -r cmd version_cmd description install_cmd <<< "$tool_spec"

        if ! command -v "$cmd" &> /dev/null; then
            log_warning "$cmd not found - $description (optional)"
            log_info "Install: $install_cmd"
        else
            local version_output=$($version_cmd 2>&1 | head -1 | cut -c1-60)
            log_success "$cmd: $version_output"
        fi
    done

    if [ $missing -eq 1 ]; then
        log_error "Missing required build tools - cannot continue"
        log_info ""
        log_info "Quick fix for Debian/Ubuntu:"
        log_info "  sudo apt install patchelf python3 python3-pip wget file imagemagick"
        log_info "  pip install appimage-builder"
        return 1
    fi

    log_success "All required build tools present"
    return 0
}

check_required_files() {
    local missing=0

    if [ ! -f "requirements.txt" ]; then
        log_error "requirements.txt not found"
        missing=1
    fi

    if [ ! -f "main.py" ]; then
        log_error "main.py not found"
        missing=1
    fi

    if [ ! -d "siproxylin" ]; then
        log_error "siproxylin/ directory not found"
        missing=1
    fi

    if [ ! -d "drunk_xmpp" ]; then
        log_error "drunk_xmpp/ directory not found"
        missing=1
    fi

    if [ ! -d "drunk_call_hook" ]; then
        log_error "drunk_call_hook/ directory not found"
        missing=1
    fi

    if [ $missing -eq 1 ]; then
        return 1
    fi

    return 0
}

check_go_binary() {
    local platform="${1:-linux}"  # linux, windows, darwin
    local call_binary=""

    case "$platform" in
        linux)
            call_binary="drunk_call_service/bin/drunk-call-service-linux"
            ;;
        windows)
            call_binary="drunk_call_service/bin/drunk-call-service-windows.exe"
            ;;
        darwin)
            call_binary="drunk_call_service/bin/drunk-call-service-darwin"
            ;;
        *)
            log_error "Unknown platform: $platform"
            return 1
            ;;
    esac

    if [ ! -f "$call_binary" ]; then
        log_error "Call service binary not found: $call_binary"
        log_info "Build it first: cd drunk_call_service && make clean && make && make install"
        return 1
    fi

    log_success "Call service binary found: $call_binary"
    return 0
}

# =============================================================================
# BUILD FUNCTIONS
# =============================================================================

build_call_service() {
    local platform="${1:-linux}"

    log_step "BUILD" "Building C++ call service for $platform..."

    if [ ! -d "drunk_call_service" ]; then
        log_error "drunk_call_service/ directory not found"
        return 1
    fi

    cd drunk_call_service

    case "$platform" in
        linux)
            log_info "Building for Linux (amd64)..."
            make clean && make release
            ;;
        windows)
            log_info "Building for Windows (amd64)..."
            log_error "Windows build not yet supported for C++ version"
            cd ..
            return 1
            ;;
        darwin)
            log_info "Building for macOS (amd64)..."
            log_error "macOS build not yet supported for C++ version"
            cd ..
            return 1
            ;;
        *)
            log_error "Unknown platform: $platform"
            cd ..
            return 1
            ;;
    esac

    cd ..
    log_success "C++ call service built successfully"
    return 0
}

# =============================================================================
# DIRECTORY STRUCTURE FUNCTIONS
# =============================================================================

create_base_structure() {
    local dest_dir="$1"
    local layout="${2:-fhs}"  # fhs (Filesystem Hierarchy Standard) or custom

    log_step "STRUCTURE" "Creating base directory structure ($layout)..."

    case "$layout" in
        fhs)
            # Standard Linux FHS layout
            mkdir -p "$dest_dir/usr/bin"
            mkdir -p "$dest_dir/usr/lib/python${PYTHON_VERSION}/site-packages"
            mkdir -p "$dest_dir/usr/share/$PKG_ID"
            mkdir -p "$dest_dir/usr/share/icons/hicolor/scalable/apps"
            mkdir -p "$dest_dir/usr/share/applications"
            mkdir -p "$dest_dir/usr/local/bin"
            log_success "FHS structure created"
            ;;
        windows)
            # Windows-style layout
            mkdir -p "$dest_dir/bin"
            mkdir -p "$dest_dir/lib"
            mkdir -p "$dest_dir/share"
            mkdir -p "$dest_dir/python"
            log_success "Windows structure created"
            ;;
        macos)
            # macOS app bundle layout
            mkdir -p "$dest_dir/Contents/MacOS"
            mkdir -p "$dest_dir/Contents/Resources"
            mkdir -p "$dest_dir/Contents/Frameworks"
            log_success "macOS bundle structure created"
            ;;
        *)
            log_error "Unknown layout: $layout"
            return 1
            ;;
    esac

    return 0
}

# =============================================================================
# FILE COPY FUNCTIONS
# =============================================================================

copy_python_code() {
    local dest_dir="$1"
    local target_path="${2:-usr/share/$PKG_ID}"

    log_step "COPY" "Copying Python application code..."

    local full_target="$dest_dir/$target_path"
    mkdir -p "$full_target"

    log_info "Copying siproxylin/"
    cp -r siproxylin "$full_target/"

    log_info "Copying drunk_xmpp/"
    cp -r drunk_xmpp "$full_target/"

    log_info "Copying drunk_call_hook/"
    cp -r drunk_call_hook "$full_target/"

    log_info "Copying main.py"
    cp main.py "$full_target/"

    log_info "Copying version.sh"
    cp version.sh "$full_target/"

    log_success "Python code copied to $target_path"
    return 0
}

copy_go_binary() {
    local dest_dir="$1"
    local platform="${2:-linux}"
    local target_path="${3:-usr/local/bin}"

    log_step "COPY" "Copying C++ call service binary..."

    local call_binary=""
    local binary_name=""

    case "$platform" in
        linux)
            call_binary="drunk_call_service/bin/drunk-call-service-linux"
            binary_name="drunk-call-service-linux"
            ;;
        windows)
            call_binary="drunk_call_service/bin/drunk-call-service-windows.exe"
            binary_name="drunk-call-service.exe"
            ;;
        darwin)
            call_binary="drunk_call_service/bin/drunk-call-service-darwin"
            binary_name="drunk-call-service"
            ;;
        *)
            log_error "Unknown platform: $platform"
            return 1
            ;;
    esac

    if [ ! -f "$call_binary" ]; then
        log_error "Call service binary not found: $call_binary"
        return 1
    fi

    local full_target="$dest_dir/$target_path"
    mkdir -p "$full_target"

    cp "$call_binary" "$full_target/$binary_name"
    chmod +x "$full_target/$binary_name"

    log_success "Call service binary copied to $target_path/$binary_name"
    return 0
}

copy_assets() {
    local dest_dir="$1"
    local icon_path="${2:-usr/share/icons/hicolor/scalable/apps}"
    local desktop_path="${3:-usr/share/applications}"

    log_step "COPY" "Copying assets (icons, desktop files)..."

    # Copy icon
    if [ -f "siproxylin.svg" ]; then
        mkdir -p "$dest_dir/$icon_path"
        cp siproxylin.svg "$dest_dir/$icon_path/"
        log_success "Icon copied to $icon_path"
    else
        log_warning "siproxylin.svg not found, skipping"
    fi

    # Copy desktop file
    if [ -f "siproxylin.desktop" ]; then
        mkdir -p "$dest_dir/$desktop_path"
        cp siproxylin.desktop "$dest_dir/$desktop_path/"
        log_success "Desktop file copied to $desktop_path"
    else
        log_warning "siproxylin.desktop not found, skipping"
    fi

    return 0
}

# =============================================================================
# PYTHON DEPENDENCY INSTALLATION
# =============================================================================

install_python_deps() {
    local dest_dir="$1"
    local site_packages="${2:-usr/lib/python${PYTHON_VERSION}/site-packages}"

    log_step "DEPS" "Installing Python dependencies..."

    if [ ! -f "requirements.txt" ]; then
        log_error "requirements.txt not found"
        return 1
    fi

    local full_target="$dest_dir/$site_packages"
    mkdir -p "$full_target"

    log_info "Installing to $site_packages"
    log_info "This may take a few minutes..."

    # Use explicit /usr/bin/python3 to prevent venv pollution in shebangs
    if ! command -v /usr/bin/python3 &> /dev/null; then
        log_error "/usr/bin/python3 not found on system"
        log_info "Install with: sudo apt install python3"
        return 1
    fi

    /usr/bin/python3 -m pip install --ignore-installed \
        --target="$full_target" \
        -r requirements.txt \
        --no-warn-script-location \
        --quiet

    if [ $? -eq 0 ]; then
        # Fix shebangs in entry point scripts (remove any venv references)
        if [ -d "$full_target/bin" ]; then
            find "$full_target/bin" -type f -exec sed -i '1s|^#!.*python.*|#!/usr/bin/env python3|' {} \; 2>/dev/null || true
            log_success "Fixed shebangs in entry point scripts"
        fi
        log_success "Python dependencies installed"
        return 0
    else
        log_error "Failed to install Python dependencies"
        return 1
    fi
}

# =============================================================================
# CLEANUP FUNCTIONS
# =============================================================================

cleanup_python_cache() {
    local dest_dir="$1"

    log_step "CLEANUP" "Removing Python cache files..."

    find "$dest_dir" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$dest_dir" -type f -name "*.pyc" -delete 2>/dev/null || true
    find "$dest_dir" -type f -name "*.pyo" -delete 2>/dev/null || true

    log_success "Python cache cleaned"
    return 0
}

cleanup_development_files() {
    local dest_dir="$1"

    log_step "CLEANUP" "Removing development files..."

    find "$dest_dir" -type f -name "*.egg-info" -delete 2>/dev/null || true
    find "$dest_dir" -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
    find "$dest_dir" -type d -name ".git" -exec rm -rf {} + 2>/dev/null || true
    find "$dest_dir" -type f -name ".gitignore" -delete 2>/dev/null || true

    log_success "Development files cleaned"
    return 0
}

# =============================================================================
# SIZE OPTIMIZATION FUNCTIONS
# =============================================================================

strip_binaries() {
    local dest_dir="$1"

    log_step "OPTIMIZE" "Stripping debug symbols from binaries..."

    find "$dest_dir" -type f -executable -exec strip --strip-debug {} \; 2>/dev/null || true
    find "$dest_dir" -type f -name "*.so*" -exec strip --strip-unneeded {} \; 2>/dev/null || true

    log_success "Binaries stripped"
    return 0
}

remove_documentation() {
    local dest_dir="$1"

    log_step "OPTIMIZE" "Removing documentation..."

    rm -rf "$dest_dir/usr/share/doc" 2>/dev/null || true
    rm -rf "$dest_dir/usr/share/man" 2>/dev/null || true
    rm -rf "$dest_dir/usr/share/info" 2>/dev/null || true

    log_success "Documentation removed (~8MB saved)"
    return 0
}

remove_build_artifacts() {
    local dest_dir="$1"

    log_step "OPTIMIZE" "Removing build artifacts..."

    # Remove headers (not needed for runtime)
    rm -rf "$dest_dir/usr/include" 2>/dev/null || true

    # Remove pkg-config files
    find "$dest_dir" -type d -name "pkgconfig" -exec rm -rf {} + 2>/dev/null || true

    # Remove cmake files
    find "$dest_dir" -type d -name "cmake" -exec rm -rf {} + 2>/dev/null || true

    log_success "Build artifacts removed (~2MB saved)"
    return 0
}

remove_unnecessary_sounds() {
    local dest_dir="$1"

    log_step "OPTIMIZE" "Removing system sounds..."

    # Keep only minimal sounds, remove theme sounds
    if [ -d "$dest_dir/usr/share/sounds" ]; then
        find "$dest_dir/usr/share/sounds" -type f -name "*.ogg" -delete 2>/dev/null || true
        find "$dest_dir/usr/share/sounds" -type f -name "*.oga" -delete 2>/dev/null || true
    fi

    log_success "System sounds removed (~5MB saved)"
    return 0
}

remove_unused_locales() {
    local dest_dir="$1"

    log_step "OPTIMIZE" "Removing unused locale translations..."

    if [ ! -d "$dest_dir/usr/share/locale" ]; then
        log_warning "Locale directory not found, skipping"
        return 0
    fi

    # Build list of locale prefixes to keep from PKG_LANGUAGES
    local keep_locales=()
    for lang_spec in "${PKG_LANGUAGES[@]}"; do
        local locale_code="${lang_spec%%:*}"  # Extract before colon
        keep_locales+=("$locale_code")
    done

    log_info "Keeping locales: ${keep_locales[*]}"

    # Remove all locales not in keep list
    cd "$dest_dir/usr/share/locale"
    local removed_count=0
    for locale_dir in */; do
        locale_dir="${locale_dir%/}"  # Remove trailing slash

        # Check if this locale should be kept
        local keep=0
        for lang in "${keep_locales[@]}"; do
            if [[ "$locale_dir" == ${lang}* ]]; then
                keep=1
                break
            fi
        done

        # Remove if not in keep list
        if [ $keep -eq 0 ]; then
            rm -rf "$locale_dir" 2>/dev/null || true
            removed_count=$((removed_count + 1))
        fi
    done
    cd - > /dev/null

    local remaining=$(du -sh "$dest_dir/usr/share/locale" 2>/dev/null | cut -f1)
    log_success "Removed $removed_count unused locales (now: $remaining | ~42MB saved)"
    return 0
}

remove_unused_pyside6_modules() {
    local dest_dir="$1"
    local pyside_dir="$dest_dir/usr/lib/python${PYTHON_VERSION}/site-packages/PySide6"

    log_step "OPTIMIZE" "Removing unused PySide6/Qt modules..."

    if [ ! -d "$pyside_dir" ]; then
        log_warning "PySide6 directory not found, skipping"
        return 0
    fi

    # We only use: QtCore, QtGui, QtWidgets (and shiboken6)
    # Remove everything else (NOTE: QtSvg removed - landing page uses pre-converted PNG)

    # Remove unused Qt modules (keep only Core, Gui, Widgets)
    local unused_modules=(
        "Qt3D*" "QtCharts*" "QtDataVisualization*" "QtGraphs*"
        "QtQuick*" "QtQml*" "QtMultimedia*" "QtNetwork*"
        "QtOpenGL*" "QtPositioning*" "QtPrintSupport*"
        "QtRemoteObjects*" "QtSensors*" "QtSerialPort*"
        "QtSql*" "QtSvg*" "QtTest*" "QtWebChannel*"
        "QtWebEngine*" "QtWebSockets*" "QtXml*"
    )

    for module in "${unused_modules[@]}"; do
        rm -rf "$pyside_dir/$module" 2>/dev/null || true
    done

    # Remove Qt development tools
    rm -rf "$pyside_dir/assistant" 2>/dev/null || true
    rm -rf "$pyside_dir/designer" 2>/dev/null || true
    rm -rf "$pyside_dir/linguist" 2>/dev/null || true
    rm -rf "$pyside_dir/lupdate" 2>/dev/null || true
    rm -rf "$pyside_dir/qmlformat" 2>/dev/null || true
    rm -rf "$pyside_dir/qmlls" 2>/dev/null || true

    # Remove type stubs and include files
    rm -rf "$pyside_dir/include" 2>/dev/null || true
    rm -rf "$pyside_dir/typesystems" 2>/dev/null || true
    find "$pyside_dir" -name "*.pyi" -delete 2>/dev/null || true

    # Clean up Qt directory - remove unused plugins and libraries
    if [ -d "$pyside_dir/Qt" ]; then
        # Remove QML directory (we don't use QML/Quick)
        rm -rf "$pyside_dir/Qt/qml" 2>/dev/null || true

        # Remove unused Qt libraries (keep only Core, Gui, Widgets, DBus)
        if [ -d "$pyside_dir/Qt/lib" ]; then
            # Remove WebEngine (193MB!)
            rm -f "$pyside_dir/Qt/lib/libQt6WebEngine"* 2>/dev/null || true

            # Remove QML/Quick libraries
            rm -f "$pyside_dir/Qt/lib/libQt6Qml"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Quick"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6QmlCompiler"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6QuickTemplates"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6QuickControls"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6QuickDialogs"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Quick3D"* 2>/dev/null || true

            # Remove 3D libraries
            rm -f "$pyside_dir/Qt/lib/libQt63D"* 2>/dev/null || true

            # Remove Charts/Graphs
            rm -f "$pyside_dir/Qt/lib/libQt6Charts"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Graphs"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6DataVisualization"* 2>/dev/null || true

            # Remove Designer/Dev tools
            rm -f "$pyside_dir/Qt/lib/libQt6Designer"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6UiTools"* 2>/dev/null || true

            # Remove PDF/Multimedia (we use GStreamer)
            rm -f "$pyside_dir/Qt/lib/libQt6Pdf"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Multimedia"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libavcodec.so"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libavformat.so"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libavutil.so"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libswscale.so"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libswresample.so"* 2>/dev/null || true

            # Remove other unused modules
            rm -f "$pyside_dir/Qt/lib/libQt6Network"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6OpenGL"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Positioning"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6PrintSupport"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Sensors"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6SerialPort"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Sql"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Svg"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Test"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6Xml"* 2>/dev/null || true
            rm -f "$pyside_dir/Qt/lib/libQt6ShaderTools"* 2>/dev/null || true
        fi

        # Remove 3D plugins
        rm -rf "$pyside_dir/Qt/plugins/assetimporters" 2>/dev/null || true
        rm -rf "$pyside_dir/Qt/plugins/sceneparsers" 2>/dev/null || true
        rm -rf "$pyside_dir/Qt/plugins/renderers" 2>/dev/null || true

        # Remove SQL drivers except sqlite (just in case)
        if [ -d "$pyside_dir/Qt/plugins/sqldrivers" ]; then
            find "$pyside_dir/Qt/plugins/sqldrivers" -type f ! -name "*sqlite*" -delete 2>/dev/null || true
        fi
    fi

    log_success "Unused PySide6 modules removed (~150-200MB saved)"
    return 0
}

remove_unused_video_encoders() {
    local dest_dir="$1"
    local lib_dir="$dest_dir/usr/lib/x86_64-linux-gnu"

    log_step "OPTIMIZE" "Removing unused video encoders..."

    if [ ! -d "$lib_dir" ]; then
        log_warning "Library directory not found, skipping"
        return 0
    fi

    # Remove H.265/HEVC encoder (not used in WebRTC)
    rm -f "$lib_dir/libx265"* 2>/dev/null || true

    # Remove speech codec for amateur radio (completely unrelated)
    rm -f "$lib_dir/libcodec2"* 2>/dev/null || true

    # Remove Intel hardware encoders (not used)
    rm -f "$lib_dir/libmfxhw64"* 2>/dev/null || true
    rm -f "$lib_dir/libmfx-tracer"* 2>/dev/null || true
    rm -rf "$lib_dir/mfx" 2>/dev/null || true

    # Remove AV1 encoders (not used in WebRTC, VP8/VP9 already available)
    rm -f "$lib_dir/libSvtAv1Enc"* 2>/dev/null || true
    rm -f "$lib_dir/libaom"* 2>/dev/null || true

    # IMPORTANT: Keep these codecs (needed for WebRTC)
    # - libopenh264* (H.264 codec)
    # - libx264* (x264 encoder, may be needed)
    # - libvpx* (VP8/VP9 codec)
    # - libavcodec*, libavformat* (FFmpeg, used by GStreamer)

    log_success "Unused video encoders removed (~60MB saved)"
    return 0
}

remove_flite_tts() {
    local dest_dir="$1"
    local lib_dir="$dest_dir/usr/lib/x86_64-linux-gnu"

    log_step "OPTIMIZE" "Removing Flite text-to-speech libraries..."

    if [ ! -d "$lib_dir" ]; then
        log_warning "Library directory not found, skipping"
        return 0
    fi

    # Remove all Flite text-to-speech libraries
    # These are pulled in as dependencies but not used by Siproxylin
    rm -f "$lib_dir/libflite"*.so* 2>/dev/null || true

    # Also remove lintian overrides if present
    if [ -d "$dest_dir/usr/share/lintian/overrides" ]; then
        rm -f "$dest_dir/usr/share/lintian/overrides/libflite"* 2>/dev/null || true
    fi

    log_success "Flite TTS libraries removed (~25MB saved)"
    return 0
}

calculate_size() {
    local path="$1"

    if [ -d "$path" ]; then
        du -sh "$path" 2>/dev/null | cut -f1
    elif [ -f "$path" ]; then
        du -h "$path" 2>/dev/null | cut -f1
    else
        echo "unknown"
    fi
}

# =============================================================================
# SUMMARY FUNCTIONS
# =============================================================================

print_build_summary() {
    local package_path="$1"
    local package_type="${2:-unknown}"

    print_separator
    print_header "Build Summary"

    echo "Package Type:    $package_type"
    echo "Package Name:    $PKG_NAME"
    echo "Version:         $SIPROXYLIN_VERSION"
    echo "Codename:        $SIPROXYLIN_CODENAME"

    if [ -f "$package_path" ]; then
        echo "Package File:    $package_path"
        echo "Package Size:    $(calculate_size "$package_path")"
    elif [ -d "$package_path" ]; then
        echo "Package Dir:     $package_path"
        echo "Package Size:    $(calculate_size "$package_path")"
    fi

    print_separator
}

# =============================================================================
# INITIALIZATION
# =============================================================================

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    log_error "This file should be sourced, not executed directly"
    echo "Usage: source .package-builder.sh"
    exit 1
fi

log_success "Package builder library loaded"
log_info "Package: $PKG_NAME v$SIPROXYLIN_VERSION ($SIPROXYLIN_CODENAME)"
