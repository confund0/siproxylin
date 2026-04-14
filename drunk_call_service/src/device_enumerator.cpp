/**
 * Device Enumeration Implementation
 *
 * Cross-platform audio device enumeration using GStreamer's GstDeviceMonitor
 * Works on Linux (PulseAudio), Windows (WASAPI), macOS (CoreAudio)
 */

#include "media_session.h"
#include "logger.h"
#include <gst/gst.h>
#include <algorithm>
#include <cstring>
#include <unordered_set>

#ifdef _WIN32
    #define OS_FAMILY "Windows"
#elif __APPLE__
    #define OS_FAMILY "macOS"
#else
    #define OS_FAMILY "Linux"
#endif

namespace drunk_call {

/**
 * Platform-specific device class filters
 * GStreamer automatically selects the appropriate provider per platform
 */
/**
 * Check if device should be filtered out (monitors, card-level devices, wrong type)
 */
static bool should_skip_device(GstDevice *device, bool is_input) {
    GstStructure *props = gst_device_get_properties(device);
    if (!props) {
        return false;  // No properties - let it through
    }

    bool skip = false;
    const gchar *device_class = gst_structure_get_string(props, "device.class");
    const gchar *media_class = gst_structure_get_string(props, "media.class");

    // Skip monitor devices (software loopbacks that capture playback)
    if (device_class && strcmp(device_class, "monitor") == 0) {
        LOG_DEBUG("[DeviceEnumerator] Skipping monitor device");
        skip = true;
    }

    // Skip devices with wrong media class (show_all_devices returns both sources and sinks)
    // This is critical: when show_all_devices=true, GStreamer ignores our filter
    if (!skip && media_class) {
        const char *expected_class = is_input ? "Audio/Source" : "Audio/Sink";
        if (strcmp(media_class, expected_class) != 0) {
            LOG_DEBUG("[DeviceEnumerator] Skipping device with wrong media.class: '{}' (expected '{}')",
                     media_class, expected_class);
            skip = true;
        }
    }

    // Skip card-level devices (too generic, no proper device ID)
    // These don't have node.name or device.name on Linux
    if (!skip && OS_FAMILY[0] == 'L') {  // Linux only
        const gchar *node_name = gst_structure_get_string(props, "node.name");
        const gchar *device_name = gst_structure_get_string(props, "device.name");

        // If neither node.name nor device.name exists, it's a card-level fallback
        if (!node_name && !device_name) {
            LOG_DEBUG("[DeviceEnumerator] Skipping card-level device (no node.name/device.name)");
            skip = true;
        }
    }

    gst_structure_free(props);
    return skip;
}

static const char* get_audio_source_classes() {
    // GStreamer picks the right provider:
    // - Linux: PulseAudio (pulsesrc)
    // - Windows: WASAPI (wasapisrc)
    // - macOS: CoreAudio (osxaudiosrc)
    return "Audio/Source";
}

static const char* get_audio_sink_classes() {
    // GStreamer picks the right provider:
    // - Linux: PulseAudio (pulsesink)
    // - Windows: WASAPI (wasapisink)
    // - macOS: CoreAudio (osxaudiosink)
    return "Audio/Sink";
}

static const char* get_video_source_classes() {
    // GStreamer picks the right provider:
    // - Linux: V4L2 (v4l2src)
    // - Windows: KsVideo (ksvideosrc)
    // - macOS: AVFoundation (avfvideosrc)
    return "Video/Source";
}

/**
 * Extract device ID from GstDevice
 * Platform-specific device ID formats:
 * - Linux (PulseAudio): "alsa_input.pci-..." or device index
 * - Windows (WASAPI): "{GUID}"
 * - macOS (CoreAudio): numeric ID
 */
static std::string extract_device_id(GstDevice *device) {
    std::string device_id;

    // Get device properties
    GstStructure *props = gst_device_get_properties(device);
    if (props) {
        // DEBUG: Dump all available properties to find the right one
        gchar *props_str = gst_structure_to_string(props);
        LOG_DEBUG("[DeviceEnumerator] GStreamer device properties: {}", props_str ? props_str : "(null)");
        g_free(props_str);

        const gchar *id = nullptr;

        // Platform-specific property names
        switch (OS_FAMILY[0]) {  // Cheap trick: 'L' = Linux, 'W' = Windows, 'm' = macOS
            case 'L':  // Linux - PulseAudio/PipeWire
                // node.name contains the PulseAudio device name (e.g., "alsa_input.pci-...")
                id = gst_structure_get_string(props, "node.name");
                if (!id) id = gst_structure_get_string(props, "device.name");
                if (!id) id = gst_structure_get_string(props, "device");
                LOG_DEBUG("[DeviceEnumerator] Linux: node.name='{}', device.name='{}', device='{}'",
                         gst_structure_get_string(props, "node.name") ? gst_structure_get_string(props, "node.name") : "(null)",
                         gst_structure_get_string(props, "device.name") ? gst_structure_get_string(props, "device.name") : "(null)",
                         gst_structure_get_string(props, "device") ? gst_structure_get_string(props, "device") : "(null)");
                break;

            case 'W':  // Windows - WASAPI
                id = gst_structure_get_string(props, "device.id");
                if (!id) id = gst_structure_get_string(props, "device");
                break;

            case 'm':  // macOS - CoreAudio
                id = gst_structure_get_string(props, "device.id");
                if (!id) id = gst_structure_get_string(props, "device");
                break;

            default:
                id = gst_structure_get_string(props, "device");
                break;
        }

        if (id) {
            device_id = id;
            LOG_DEBUG("[DeviceEnumerator] Extracted device ID: '{}'", device_id);
        }

        gst_structure_free(props);
    }

    // Fallback: use device name as ID
    if (device_id.empty()) {
        gchar *name = gst_device_get_display_name(device);
        if (name) {
            device_id = name;
            LOG_WARN("[DeviceEnumerator] FALLBACK: Using display name as device ID: '{}'", device_id);
            g_free(name);
        }
    }

    return device_id;
}

/**
 * Check if device is the default device
 */
static bool is_default_device(GstDevice *device) {
    GstStructure *props = gst_device_get_properties(device);
    if (!props) {
        return false;
    }

    bool is_default = false;

    // Platform-specific default detection
    switch (OS_FAMILY[0]) {
        case 'L':  // Linux - PulseAudio
            {
                const gchar *is_def = gst_structure_get_string(props, "is-default");
                is_default = (is_def && g_strcmp0(is_def, "true") == 0);
            }
            break;

        case 'W':  // Windows - WASAPI
            {
                gboolean def = FALSE;
                gst_structure_get_boolean(props, "device.default", &def);
                is_default = def;
            }
            break;

        case 'm':  // macOS - CoreAudio
            {
                gboolean def = FALSE;
                gst_structure_get_boolean(props, "device.default", &def);
                is_default = def;
            }
            break;

        default:
            break;
    }

    gst_structure_free(props);
    return is_default;
}

/**
 * Enumerate devices using GstDeviceMonitor
 */
std::vector<AudioDevice> DeviceEnumerator::enumerate_devices(const char *classes, bool is_input) {
    std::vector<AudioDevice> devices;

    try {
        LOG_INFO("[DeviceEnumerator] Enumerating {} devices on {}",
                 is_input ? "input" : "output", OS_FAMILY);

        // Create device monitor - but DON'T start/stop it
        // Per GStreamer docs: get_devices() will probe hardware even if not started
        GstDeviceMonitor *monitor = gst_device_monitor_new();
        if (!monitor) {
            LOG_ERROR("[DeviceEnumerator] Failed to create device monitor");
            return devices;
        }

        // Add filter for this specific device class
        gst_device_monitor_add_filter(monitor, classes, nullptr);

        // Show all devices, including those from hidden providers (fixes PipeWire compatibility)
        // This ensures devices from hidden providers (PipeWire, ALSA compat layers) are visible
        gst_device_monitor_set_show_all_devices(monitor, TRUE);

        // Get devices WITHOUT start/stop - avoids PipeWire double-free bug
        // Docs say: "may actually probe the hardware if the monitor is not currently started"
        GList *device_list = gst_device_monitor_get_devices(monitor);

        // Track seen device IDs to deduplicate (show_all_devices can return duplicates)
        std::unordered_set<std::string> seen_ids;

        for (GList *l = device_list; l != nullptr; l = l->next) {
            GstDevice *device = GST_DEVICE(l->data);

            // Filter out monitors, card-level devices, and wrong device types
            if (should_skip_device(device, is_input)) {
                gst_object_unref(device);
                continue;
            }

            AudioDevice audio_dev;
            audio_dev.is_input = is_input;

            // Get device ID
            audio_dev.id = extract_device_id(device);

            // Skip duplicate devices (show_all_devices returns same device from multiple providers)
            if (seen_ids.count(audio_dev.id) > 0) {
                LOG_DEBUG("[DeviceEnumerator] Skipping duplicate device ID: '{}'", audio_dev.id);
                gst_object_unref(device);
                continue;
            }
            seen_ids.insert(audio_dev.id);

            // Get display name (human-readable name from PulseAudio)
            gchar *display_name = gst_device_get_display_name(device);
            if (display_name) {
                audio_dev.name = display_name;
                audio_dev.description = display_name;  // Use same for description (shown in UI)
                g_free(display_name);
            }

            // Check if default
            audio_dev.is_default = is_default_device(device);

            LOG_INFO("[DeviceEnumerator]   {}{} (id: {})",
                     audio_dev.is_default ? "✓ " : "  ", audio_dev.name, audio_dev.id);
            LOG_DEBUG("[DeviceEnumerator]     → id='{}', name='{}', description='{}'",
                     audio_dev.id, audio_dev.name, audio_dev.description);

            devices.push_back(audio_dev);
            gst_object_unref(device);
        }

        g_list_free(device_list);

        // Cleanup - no start/stop needed, just unref
        gst_object_unref(monitor);

        LOG_INFO("[DeviceEnumerator] Found {} devices", devices.size());

    } catch (const std::exception &e) {
        LOG_ERROR("[DeviceEnumerator] Exception: {}", e.what());
    }

    return devices;
}

std::vector<AudioDevice> DeviceEnumerator::list_audio_inputs() {
    return enumerate_devices(get_audio_source_classes(), true);
}

std::vector<AudioDevice> DeviceEnumerator::list_audio_outputs() {
    return enumerate_devices(get_audio_sink_classes(), false);
}

AudioDevice DeviceEnumerator::get_default_input() {
    auto devices = list_audio_inputs();

    // Find default device
    auto it = std::find_if(devices.begin(), devices.end(),
                          [](const AudioDevice &dev) { return dev.is_default; });

    if (it != devices.end()) {
        return *it;
    }

    // Fallback: return first device or empty
    if (!devices.empty()) {
        LOG_INFO("[DeviceEnumerator] No default input found, using first device");
        return devices[0];
    }

    LOG_ERROR("[DeviceEnumerator] No input devices found!");
    return AudioDevice();
}

AudioDevice DeviceEnumerator::get_default_output() {
    auto devices = list_audio_outputs();

    // Find default device
    auto it = std::find_if(devices.begin(), devices.end(),
                          [](const AudioDevice &dev) { return dev.is_default; });

    if (it != devices.end()) {
        return *it;
    }

    // Fallback: return first device or empty
    if (!devices.empty()) {
        LOG_INFO("[DeviceEnumerator] No default output found, using first device");
        return devices[0];
    }

    LOG_ERROR("[DeviceEnumerator] No output devices found!");
    return AudioDevice();
}

/**
 * Enumerate video devices
 */
std::vector<VideoDevice> DeviceEnumerator::enumerate_video_devices(const char *classes) {
    std::vector<VideoDevice> devices;

    try {
        LOG_INFO("[DeviceEnumerator] Enumerating video devices on {}", OS_FAMILY);

        // Create device monitor - but DON'T start/stop it
        GstDeviceMonitor *monitor = gst_device_monitor_new();
        if (!monitor) {
            LOG_ERROR("[DeviceEnumerator] Failed to create device monitor");
            return devices;
        }

        // Add filter for video sources
        gst_device_monitor_add_filter(monitor, classes, nullptr);

        // Show all devices, including those from hidden providers (fixes PipeWire compatibility)
        // This ensures devices from hidden providers (PipeWire, ALSA compat layers) are visible
        gst_device_monitor_set_show_all_devices(monitor, TRUE);

        // Get devices WITHOUT start/stop - avoids PipeWire double-free bug
        GList *device_list = gst_device_monitor_get_devices(monitor);

        // Track seen device paths to deduplicate (show_all_devices can return duplicates)
        std::unordered_set<std::string> seen_paths;

        for (GList *l = device_list; l != nullptr; l = l->next) {
            GstDevice *device = GST_DEVICE(l->data);

            VideoDevice video_dev;

            // Get device ID (driver name for video devices)
            video_dev.id = extract_device_id(device);

            // Get display name
            gchar *display_name = gst_device_get_display_name(device);
            if (display_name) {
                video_dev.name = display_name;
                g_free(display_name);
            }

            // Get device class
            gchar *device_class = gst_device_get_device_class(device);
            if (device_class) {
                video_dev.description = device_class;
                g_free(device_class);
            }

            // Get device path (Linux: /dev/video0, Windows: device path, macOS: device index)
            GstStructure *props = gst_device_get_properties(device);
            if (props) {
                const gchar *path = gst_structure_get_string(props, "device.path");
                if (path) {
                    video_dev.device_path = path;
                }
                gst_structure_free(props);
            }

            // Skip devices without a device path (invalid/virtual devices)
            if (video_dev.device_path.empty()) {
                LOG_DEBUG("[DeviceEnumerator] Skipping video device without device.path: '{}'", video_dev.name);
                gst_object_unref(device);
                continue;
            }

            // Skip duplicate devices (show_all_devices returns same device from multiple providers)
            if (seen_paths.count(video_dev.device_path) > 0) {
                LOG_DEBUG("[DeviceEnumerator] Skipping duplicate video device: '{}'", video_dev.device_path);
                gst_object_unref(device);
                continue;
            }
            seen_paths.insert(video_dev.device_path);

            // Check if default
            video_dev.is_default = is_default_device(device);

            LOG_INFO("[DeviceEnumerator]   {}{} (path: {}, id: {})",
                     video_dev.is_default ? "✓ " : "  ", video_dev.name, video_dev.device_path, video_dev.id);

            devices.push_back(video_dev);
            gst_object_unref(device);
        }

        g_list_free(device_list);

        // Cleanup - no start/stop needed, just unref
        gst_object_unref(monitor);

        LOG_INFO("[DeviceEnumerator] Found {} video devices", devices.size());

    } catch (const std::exception &e) {
        LOG_ERROR("[DeviceEnumerator] Exception: {}", e.what());
    }

    return devices;
}

std::vector<VideoDevice> DeviceEnumerator::list_video_sources() {
    return enumerate_video_devices(get_video_source_classes());
}

VideoDevice DeviceEnumerator::get_default_video_source() {
    auto devices = list_video_sources();

    // Find default device
    auto it = std::find_if(devices.begin(), devices.end(),
                          [](const VideoDevice &dev) { return dev.is_default; });

    if (it != devices.end()) {
        return *it;
    }

    // Fallback: return first device or empty
    if (!devices.empty()) {
        LOG_INFO("[DeviceEnumerator] No default video source found, using first device");
        return devices[0];
    }

    LOG_ERROR("[DeviceEnumerator] No video devices found!");
    return VideoDevice();
}

} // namespace drunk_call
