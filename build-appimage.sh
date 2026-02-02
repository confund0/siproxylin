#!/bin/bash
#
# Linux AppImage Build Script for Siproxylin
# Uses the reusable package builder library
#

set -e

# Load the package builder library
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.package-builder.sh"

# =============================================================================
# CONFIGURATION
# =============================================================================

APPDIR="AppDir"
APPIMAGE_OUTPUT="Siproxylin-${SIPROXYLIN_VERSION}-x86_64.AppImage"

# =============================================================================
# APPIMAGE-SPECIFIC FUNCTIONS
# =============================================================================

create_apprun_script() {
    local appdir="$1"

    log_step "APPRUN" "Creating AppRun launcher script..."

    cat > "$appdir/AppRun" << 'APPRUN_EOF'
#!/bin/bash
# Simple AppRun for Siproxylin

# Get the AppImage directory
APPDIR="$(dirname "$(readlink -f "$0")")"

# Desktop integration (register .desktop file and icon)
if [ -n "$APPIMAGE" ]; then
    DESKTOP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
    DESKTOP_FILE="$DESKTOP_DIR/siproxylin.desktop"
    ICON_FILE="$ICON_DIR/siproxylin.png"

    # Create directories
    mkdir -p "$DESKTOP_DIR"
    mkdir -p "$ICON_DIR"

    # Update .desktop file with current AppImage path
    if [ -f "$APPDIR/siproxylin.desktop" ]; then
        sed "s|Exec=.*|Exec=\"$APPIMAGE\" %U|g" "$APPDIR/siproxylin.desktop" > "$DESKTOP_FILE"
        chmod 644 "$DESKTOP_FILE"
    fi

    # Copy icon
    if [ -f "$APPDIR/siproxylin.png" ]; then
        cp "$APPDIR/siproxylin.png" "$ICON_FILE"
        chmod 644 "$ICON_FILE"
    fi

    # Update desktop database (silently ignore errors)
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# Set environment variables
export APPDIR
export PYTHONHOME="$APPDIR/usr"
export PYTHONPATH="$APPDIR/usr/lib/python3.11/site-packages"
export LD_LIBRARY_PATH="$APPDIR/usr/lib/x86_64-linux-gnu:$APPDIR/usr/lib:$APPDIR/lib/x86_64-linux-gnu"
export GST_PLUGIN_PATH="$APPDIR/usr/lib/x86_64-linux-gnu/gstreamer-1.0"
export GST_PLUGIN_SYSTEM_PATH="$APPDIR/usr/lib/x86_64-linux-gnu/gstreamer-1.0"
export PULSE_SYSTEM="0"
export SIPROXYLIN_PATH_MODE="dot"

# Qt platform plugin path
export QT_PLUGIN_PATH="$APPDIR/usr/lib/x86_64-linux-gnu/qt6/plugins"
export QT_QPA_PLATFORM_PLUGIN_PATH="$APPDIR/usr/lib/x86_64-linux-gnu/qt6/plugins/platforms"

# XDG data dirs for enchant/hunspell dictionary discovery
export XDG_DATA_DIRS="$APPDIR/usr/share"

# Launch the application
# Use the bundled dynamic linker explicitly to ensure LD_LIBRARY_PATH is honored
exec "$APPDIR/lib64/ld-linux-x86-64.so.2" --library-path "$LD_LIBRARY_PATH" "$APPDIR/usr/bin/python3" "$APPDIR/usr/share/com.siproxylin/main.py" --dot-data-dir "$@"
APPRUN_EOF

    chmod +x "$appdir/AppRun"
    log_success "AppRun script created"
}

setup_appdir_root() {
    local appdir="$1"

    log_step "APPDIR" "Setting up AppDir root files..."

    # Copy and convert icon
    if [ -f "siproxylin/resources/icons/siproxylin.svg" ]; then
        cp siproxylin/resources/icons/siproxylin.svg "$appdir/"
        ln -sf siproxylin.svg "$appdir/.DirIcon"

        # Convert SVG to PNG for desktop integration
        if command -v convert >/dev/null 2>&1; then
            convert -background none siproxylin/resources/icons/siproxylin.svg -resize 256x256 "$appdir/siproxylin.png"
            log_success "Icon converted to PNG and copied to AppDir root"

            # NOTE: Landing page PNG conversion disabled - not used
            # if [ -f "siproxylin/resources/icons/landing.svg" ]; then
            #     convert -background none siproxylin/resources/icons/landing.svg -resize 500x500 "siproxylin/resources/icons/landing.png"
            #     log_success "Landing page artwork converted to PNG"
            # else
            #     log_warn "Landing page SVG not found: siproxylin/resources/icons/landing.svg"
            # fi
        else
            log_warn "ImageMagick 'convert' not found, PNG icon not created"
        fi
        log_success "SVG icon linked at AppDir root"
    else
        log_warn "Icon SVG not found: siproxylin/resources/icons/siproxylin.svg"
    fi

    # Copy desktop file to root
    if [ -f "siproxylin.desktop" ]; then
        cp siproxylin.desktop "$appdir/"
        log_success "Desktop file at AppDir root"
    fi
}

copy_dynamic_linker() {
    local appdir="$1"

    log_step "LINKER" "Installing dynamic linker from libc6 package..."

    # Find the libc6 .deb in apt cache
    local libc6_deb=$(find "$PKG_APT_CACHE_DIR/apt/archives" -name "libc6_*.deb" | head -1)

    if [ -z "$libc6_deb" ]; then
        log_error "libc6 package not found in apt cache"
        log_info "This should have been downloaded by appimage-builder"
        return 1
    fi

    log_info "Extracting dynamic linker from: $(basename $libc6_deb)"

    # Extract dynamic linker from libc6 package (avoids host/AppDir version mismatch)
    mkdir -p "$appdir/lib/x86_64-linux-gnu"
    mkdir -p "$appdir/lib64"

    # Extract the actual binary
    dpkg -x "$libc6_deb" /tmp/libc6-extract-$$

    if [ -f "/tmp/libc6-extract-$$/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2" ]; then
        cp /tmp/libc6-extract-$$/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 "$appdir/lib/x86_64-linux-gnu/"

        # Create symlink at lib64/ (AppImage convention)
        ln -sf ../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 "$appdir/lib64/ld-linux-x86-64.so.2"

        log_success "Dynamic linker installed from libc6 package (matches AppDir glibc)"
    else
        log_error "Failed to extract dynamic linker from libc6 package"
        return 1
    fi

    # Cleanup
    rm -rf /tmp/libc6-extract-$$
}

bundle_system_libraries() {
    local appdir="$1"

    log_step "LIBS" "Bundling system libraries (GStreamer, Qt6, etc.)..."

    # Check if we already have libraries bundled
    if [ -d "$appdir/usr/lib/x86_64-linux-gnu/gstreamer-1.0" ]; then
        log_info "System libraries already bundled (incremental build)"
        log_success "Reusing existing libraries"
        return 0
    fi

    # Setup APT cache directory
    if [ ! -d "$PKG_APT_CACHE_DIR" ]; then
        log_info "Creating APT cache directory: $PKG_APT_CACHE_DIR"
        mkdir -p "$PKG_APT_CACHE_DIR"

        # Migrate from old appimage-build location if it exists
        if [ -d "appimage-build/apt" ]; then
            log_info "Migrating existing apt cache from appimage-build/..."
            cp -r appimage-build/apt "$PKG_APT_CACHE_DIR/"
            log_success "Migrated $(calculate_size appimage-build/apt) of cached packages"
        fi
    fi

    # Check if we have cached packages
    if [ -d "$PKG_APT_CACHE_DIR/apt/archives" ] && [ "$(ls -A $PKG_APT_CACHE_DIR/apt/archives/*.deb 2>/dev/null | wc -l)" -gt 0 ]; then
        local cache_size=$(calculate_size "$PKG_APT_CACHE_DIR/apt")
        log_info "Found APT cache: $cache_size of packages (no download needed!)"
    else
        log_info "No APT cache found - will download ~300MB of packages"
        log_info "Cache will be saved to $PKG_APT_CACHE_DIR for future builds"
    fi

    # Check if appimage-builder is available
    if ! command -v "$APPIMAGE_BUILDER" &> /dev/null; then
        log_error "appimage-builder not found: $APPIMAGE_BUILDER"
        log_info "Install with: pip install appimage-builder"
        log_info "Or set APPIMAGE_BUILDER=/path/to/appimage-builder-x.x.x-x86_64.AppImage"
        return 1
    fi

    log_info "Using: $APPIMAGE_BUILDER"

    # appimage-builder hardcodes its work directory to ./appimage-build/
    # If we have an existing cache, symlink it so appimage-builder reuses packages
    if [ -d "$PKG_APT_CACHE_DIR/apt" ]; then
        log_info "Symlinking existing apt cache for appimage-builder..."
        mkdir -p appimage-build
        # Remove old symlink if exists
        [ -L "appimage-build/apt" ] && rm "appimage-build/apt"
        ln -sf "../$PKG_APT_CACHE_DIR/apt" appimage-build/apt
        log_success "appimage-builder will reuse cached packages (no download)"
    fi

    # Run appimage-builder (may fail, we only need the libraries)
    log_info "Running appimage-builder to bundle system libraries..."
    log_info "Note: Uses isolated apt cache - does NOT touch system packages"
    "$APPIMAGE_BUILDER" --recipe appimage.yml --skip-test || true

    # If appimage-builder created a new apt cache (not symlinked), save it
    if [ -d "appimage-build/apt" ] && [ ! -L "appimage-build/apt" ]; then
        log_info "Saving new apt cache for future builds..."
        rm -rf "$PKG_APT_CACHE_DIR/apt"
        mv appimage-build/apt "$PKG_APT_CACHE_DIR/"

        # Update paths in apt.conf to point to new location
        local apt_conf="$PKG_APT_CACHE_DIR/apt/apt.conf"
        if [ -f "$apt_conf" ]; then
            local current_dir=$(pwd)
            sed -i "s|/home/.*/appimage-build/apt|$current_dir/$PKG_APT_CACHE_DIR/apt|g" "$apt_conf"
            sed -i "s|/home/.*/\.package-builder-apt/apt|$current_dir/$PKG_APT_CACHE_DIR/apt|g" "$apt_conf"
            log_success "Updated apt.conf paths"
        fi
    fi

    # Remove problematic components
    log_info "Removing problematic runtime/ directory..."
    rm -rf "$appdir/runtime/"

    log_success "System libraries bundled"
}

package_appimage() {
    local appdir="$1"
    local output="$2"

    log_step "PACKAGE" "Creating AppImage package..."

    # Download appimagetool if needed
    if [ ! -f "appimagetool-x86_64.AppImage" ]; then
        log_info "Downloading appimagetool..."
        wget -q https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
        chmod +x appimagetool-x86_64.AppImage
        log_success "appimagetool downloaded"
    fi

    # Package
    log_info "Running appimagetool..."
    # NOTE: --no-appstream disables .zsync file generation (for delta updates)
    # Remove this flag when going live for public distribution
    # APPIMAGE_EXTRACT_AND_RUN=1 enables running in containers without FUSE
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 ./appimagetool-x86_64.AppImage --no-appstream "$appdir" "$output"

    if [ -f "$output" ]; then
        log_success "AppImage created: $output"
        return 0
    else
        log_error "Failed to create AppImage"
        return 1
    fi
}

# =============================================================================
# MAIN BUILD PROCESS
# =============================================================================

main() {
    print_header "$PKG_NAME AppImage Builder"

    # Step 1: Validate
    log_step "1/10" "Validating build environment..."
    if ! check_required_files; then
        log_error "Required files missing"
        exit 1
    fi
    log_success "All required files present"
    print_separator

    # Step 2: Build Go service
    log_step "2/10" "Checking Go call service binary..."
    if ! check_go_binary "linux"; then
        log_info "Building Go service..."
        if ! build_go_service "linux"; then
            log_error "Failed to build Go service"
            exit 1
        fi
    fi
    print_separator

    # Step 3: Clean previous build (optional - keep for incremental builds)
    log_step "3/10" "Cleaning previous AppImage output..."
    rm -f Siproxylin-*.AppImage
    log_success "Cleaned old AppImage files"
    print_separator

    # Step 4: Create directory structure
    if [ ! -d "$APPDIR" ]; then
        if ! create_base_structure "$APPDIR" "fhs"; then
            exit 1
        fi
    else
        log_step "4/10" "Reusing existing AppDir structure..."
        log_info "Delete AppDir/ to force clean rebuild"
    fi
    print_separator

    # Step 5: Bundle system libraries (one-time with appimage-builder)
    log_step "5/10" "Bundling system libraries..."
    if ! bundle_system_libraries "$APPDIR"; then
        exit 1
    fi
    print_separator

    # Step 6: Install Python dependencies
    log_step "6/10" "Installing Python dependencies..."
    if ! install_python_deps "$APPDIR"; then
        exit 1
    fi
    print_separator

    # Step 7: Copy application files
    log_step "7/10" "Copying application files..."

    # Verify version.sh exists (will be copied by copy_python_code)
    if [ ! -f "version.sh" ]; then
        log_error "version.sh not found"
        exit 1
    fi

    copy_python_code "$APPDIR" "usr/share/$PKG_ID"
    copy_go_binary "$APPDIR" "linux" "usr/local/bin"
    # Note: Icon and desktop file handled by setup_appdir_root() in Step 8
    print_separator

    # Step 8: Create AppImage-specific files
    log_step "8/10" "Creating AppImage-specific files..."
    create_apprun_script "$APPDIR"
    setup_appdir_root "$APPDIR"
    copy_dynamic_linker "$APPDIR"
    print_separator

    # Step 9: Cleanup & Optimization
    log_step "9/10" "Cleaning up and optimizing size..."
    cleanup_python_cache "$APPDIR"
    cleanup_development_files "$APPDIR"
    remove_documentation "$APPDIR"
    remove_build_artifacts "$APPDIR"
    remove_unnecessary_sounds "$APPDIR"
    remove_unused_locales "$APPDIR"
    remove_unused_pyside6_modules "$APPDIR"
    # strip_binaries "$APPDIR"  # DISABLED: Breaks appimage-builder's ELF patching
    log_info "Skipping strip (breaks patched binaries)"
    print_separator

    # Step 10: Package
    log_step "10/10" "Packaging AppImage..."
    if ! package_appimage "$APPDIR" "$APPIMAGE_OUTPUT"; then
        exit 1
    fi
    print_separator

    # Summary
    print_build_summary "$APPIMAGE_OUTPUT" "Linux AppImage"

    echo "Test with: ./$APPIMAGE_OUTPUT"
    print_separator
}

# Run main
main
