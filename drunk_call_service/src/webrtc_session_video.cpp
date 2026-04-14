/**
 * WebRTC Session Implementation - Video Pipeline
 *
 * Video pipeline setup for answerer and offerer modes
 */

#include "webrtc_session.h"
#include "logger.h"
#include <gst/webrtc/webrtc.h>

#ifdef _WIN32
#include <windows.h>  // For FindWindowExW, ShowWindow, SetForegroundWindow
#endif

namespace drunk_call {

bool WebRTCSession::setup_answerer_video_pipeline() {
    try {
        LOG_DEBUG("[WebRTCSession] [ANSWERER] Creating video source pipeline...");

        // CRITICAL: Pause pipeline before adding elements to avoid FLUSHING state
        GstState current_state, pending_state;
        gst_element_get_state(pipeline_, &current_state, &pending_state, 0);
        LOG_INFO("[WebRTCSession] Pipeline state before pause: current={}, pending={}",
                 gst_element_state_get_name(current_state),
                 gst_element_state_get_name(pending_state));

        LOG_DEBUG("[WebRTCSession] Pausing pipeline to add video elements...");
        GstStateChangeReturn ret = gst_element_set_state(pipeline_, GST_STATE_PAUSED);
        LOG_DEBUG("[WebRTCSession] Pause state change result: {}", static_cast<int>(ret));
        gst_element_get_state(pipeline_, &current_state, nullptr, GST_CLOCK_TIME_NONE);
        LOG_INFO("[WebRTCSession] Pipeline state after pause: {}", gst_element_state_get_name(current_state));

        // Create video source - platform-specific
        // Linux: v4l2src (tested, reliable), others: autovideosrc (GStreamer auto-detection)
#ifdef __linux__
        video_src_ = gst_element_factory_make("v4l2src", "video_src");
        const char* source_name = "v4l2src (Linux V4L2)";
#else
        video_src_ = gst_element_factory_make("autovideosrc", "video_src");
        const char* source_name = "autovideosrc";
#endif
        if (!video_src_) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create video source: {}", source_name);
            return false;
        }
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Using {}", source_name);

        // Set camera device if specified (e.g., "/dev/video0")
        if (!config_.camera_device.empty()) {
            g_object_set(video_src_, "device", config_.camera_device.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Using camera device: {}", config_.camera_device);
        } else {
            LOG_INFO("[WebRTCSession] [ANSWERER] Using default camera device");
        }

        // CRITICAL: Enable do-timestamp for proper timestamps (v4l2src only)
#ifdef __linux__
        g_object_set(video_src_, "do-timestamp", TRUE, nullptr);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Set do-timestamp=TRUE on v4l2src");
#endif

        // Create tee to split camera feed (one branch for encoding, one for self-view PiP)
        video_tee_ = gst_element_factory_make("tee", "video_tee");
        if (!video_tee_) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create tee element");
            return false;
        }

        // CRITICAL: Set allow-not-linked=TRUE to allow encoder branch to work
        // even if self-view branch isn't connected yet (compositor created on incoming video)
        g_object_set(video_tee_, "allow-not-linked", TRUE, nullptr);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Created tee for camera feed splitting (PiP self-view, allow-not-linked=TRUE)");

        // Create rest of pipeline elements
        GstElement *tee_queue = gst_element_factory_make("queue", "tee_queue_encode");
        GstElement *convert = gst_element_factory_make("videoconvert", "videoconvert");
        GstElement *queue1 = gst_element_factory_make("queue", "queue_pre_encode");
        GstElement *encoder = gst_element_factory_make("vp8enc", "vp8enc");
        GstElement *payloader = gst_element_factory_make("rtpvp8pay", "rtpvp8pay");
        GstElement *queue2 = gst_element_factory_make("queue", "queue_post_pay");
        GstElement *capsfilter = gst_element_factory_make("capsfilter", "rtp_video_caps");

        if (!tee_queue || !convert || !queue1 || !encoder || !payloader || !queue2 || !capsfilter) {
            LOG_ERROR("[WebRTCSession] Failed to create video elements");
            return false;
        }

        // CRITICAL: Configure low-latency queues for real-time video (WebRTC industry standard)
        // max-size-buffers=5: Limit buffering to ~166ms at 30fps (prevents 3-5 sec delay)
        // leaky=2 (downstream): Drop old frames when full, keep pipeline flowing
        g_object_set(tee_queue, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue1, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue2, "max-size-buffers", 5, "leaky", 2, nullptr);
        LOG_INFO("[WebRTCSession] ✓ Configured low-latency queues (max-size-buffers=5, leaky=downstream)");

        // Configure encoder - realtime with reasonable keyframes for calls
        g_object_set(encoder,
            "deadline", G_GINT64_CONSTANT(1),        // Realtime encoding (lowest latency)
            "cpu-used", 8,                            // Max speed (lowest latency)
            "target-bitrate", 1500000,                // 1.5Mbps
            "keyframe-max-dist", 60,                  // Keyframe every 60 frames (2 seconds at 30fps)
            nullptr);
        LOG_INFO("[WebRTCSession] ✓ Configured vp8enc (deadline=1, keyframe-max-dist=60, 1.5Mbps)");

        // CRITICAL: Configure payloader with picture-id-mode=15-bit
        // Official example comment: "This improves TWCC stats behavior and fixes stuttery video playback in Chrome"
        g_object_set(payloader,
            "picture-id-mode", 2,  // 2 = 15-bit mode (enum value from gst-inspect-1.0 rtpvp8pay)
            nullptr);
        LOG_INFO("[WebRTCSession] ✓ Configured rtpvp8pay with picture-id-mode=15-bit (fixes stuttering!)");

        // CRITICAL: Use payload from negotiated video codec (parsed from offer SDP)
        // This MUST match what we advertised in our answer SDP
        if (negotiated_video_payload_ < 0) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] No negotiated video payload available!");
            return false;
        }
        int payload = negotiated_video_payload_;
        LOG_INFO("[WebRTCSession] ✓ Using negotiated video payload type: {}", payload);

        GstCaps *rtp_caps = gst_caps_new_simple("application/x-rtp",
            "media", G_TYPE_STRING, "video",
            "encoding-name", G_TYPE_STRING, "VP8",
            "payload", G_TYPE_INT, payload,
            nullptr);
        g_object_set(capsfilter, "caps", rtp_caps, nullptr);
        gst_caps_unref(rtp_caps);
        LOG_INFO("[WebRTCSession] ✓ Set RTP caps: application/x-rtp,media=video,encoding-name=VP8,payload={}", payload);

        // Add all elements to pipeline
        gst_bin_add_many(GST_BIN(pipeline_), video_src_, video_tee_, tee_queue, convert, queue1, encoder, payloader, queue2, capsfilter, nullptr);

        // Link camera source to tee
        if (!gst_element_link(video_src_, video_tee_)) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link video_src → tee");
            return false;
        }
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Linked camera source to tee");

        // Request tee source pad for encoder branch
        GstPad *tee_encode_pad = gst_element_request_pad_simple(video_tee_, "src_%u");
        if (!tee_encode_pad) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to request tee source pad");
            return false;
        }
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Requested tee source pad for encoder branch");

        // Link tee encoder branch: tee→queue→convert→queue→encoder→payloader→queue→capsfilter
        GstPad *tee_queue_sink = gst_element_get_static_pad(tee_queue, "sink");
        if (gst_pad_link(tee_encode_pad, tee_queue_sink) != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link tee pad to queue");
            gst_object_unref(tee_encode_pad);
            gst_object_unref(tee_queue_sink);
            return false;
        }
        gst_object_unref(tee_encode_pad);
        gst_object_unref(tee_queue_sink);

        if (!gst_element_link_many(tee_queue, convert, queue1, encoder, payloader, queue2, capsfilter, nullptr)) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link video encoder chain");
            return false;
        }
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Linked encoder chain: tee→queue→convert→queue→vp8enc→rtpvp8pay→queue→capsfilter");

        // Get webrtcbin sink pad - ANSWERER MODE
        // Reuse the pad we created during set-remote-description
        // This ensures video pipeline connects to the same transceiver used for SDP negotiation
        if (!negotiated_video_pad_) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] No negotiated video pad available!");
            return false;
        }

        LOG_INFO("[WebRTCSession] [ANSWERER] Using negotiated video pad from set-remote-description");

        // Link capsfilter to the negotiated pad
        GstPad *caps_src = gst_element_get_static_pad(capsfilter, "src");
        GstPadLinkReturn link_ret = gst_pad_link(caps_src, negotiated_video_pad_);
        if (link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] Failed to link video capsfilter to negotiated pad: {}", static_cast<int>(link_ret));
            gst_object_unref(caps_src);
            return false;
        }
        LOG_INFO("[WebRTCSession] ✓ Linked video capsfilter to negotiated webrtcbin pad");

        gst_object_unref(caps_src);

        // NOW sync all elements to PLAYING state - AFTER all linking is complete
        // This ensures v4l2src only starts capturing when pipeline is fully ready
        gst_element_sync_state_with_parent(video_src_);
        gst_element_sync_state_with_parent(video_tee_);
        gst_element_sync_state_with_parent(tee_queue);
        gst_element_sync_state_with_parent(convert);
        gst_element_sync_state_with_parent(queue1);
        gst_element_sync_state_with_parent(encoder);
        gst_element_sync_state_with_parent(payloader);
        gst_element_sync_state_with_parent(queue2);
        gst_element_sync_state_with_parent(capsfilter);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Synced video elements (incl. tee) to PLAYING (after all linking complete)");

        // CRITICAL: Create self-view branch immediately (don't wait for incoming video)
        // This prevents race conditions and gives instant self-view feedback
        LOG_INFO("[WebRTCSession] [ANSWERER] Creating immediate self-view (before incoming video)...");

        // Create compositor for self-view (will add incoming video later)
        compositor_ = gst_element_factory_make("compositor", "video_compositor");
        if (!compositor_) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create compositor for self-view");
            return false;
        }
        g_object_set(compositor_, "background", 1, nullptr);  // 1 = black background

        // Create video sink
#ifdef _WIN32
        video_sink_ = gst_element_factory_make("d3dvideosink", "video_sink");
#else
        video_sink_ = gst_element_factory_make("autovideosink", "video_sink");
#endif
        if (!video_sink_) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create video sink for self-view");
            gst_object_unref(compositor_);
            compositor_ = nullptr;
            return false;
        }
        g_object_set(video_sink_, "sync", TRUE, nullptr);
#ifdef _WIN32
        // D3D9 stability settings for VM/RDP compatibility
        g_object_set(video_sink_,
            "force-aspect-ratio", TRUE,           // Maintain aspect ratio
            "enable-navigation-events", FALSE,    // Reduce event overhead
            "stream-stop-on-close", FALSE,        // Don't stop stream if window closes accidentally
            nullptr);
#endif

        // Create format conversion for compositor → sink
        GstElement *sink_convert = gst_element_factory_make("videoconvert", "video_convert_sink");
        if (!sink_convert) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create sink videoconvert");
            gst_object_unref(compositor_);
            gst_object_unref(video_sink_);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Add compositor and sink to pipeline
        gst_bin_add_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, nullptr);

        // Link compositor → convert → sink
        if (!gst_element_link_many(compositor_, sink_convert, video_sink_, nullptr)) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link compositor → sink");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Create self-view branch: tee → queue → convert → scale → flip → compositor
        GstElement *self_queue = gst_element_factory_make("queue", "self_view_queue");
        GstElement *self_convert = gst_element_factory_make("videoconvert", "self_view_convert");
        GstElement *self_scale = gst_element_factory_make("videoscale", "self_view_scale");
        GstElement *self_flip = gst_element_factory_make("videoflip", "self_view_flip");

        if (!self_queue || !self_convert || !self_scale || !self_flip) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create self-view elements");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, nullptr);
            if (self_queue) gst_object_unref(self_queue);
            if (self_convert) gst_object_unref(self_convert);
            if (self_scale) gst_object_unref(self_scale);
            if (self_flip) gst_object_unref(self_flip);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Configure low-latency queue for self-view
        g_object_set(self_queue, "max-size-buffers", 5, "leaky", 2, nullptr);

        // Configure flip for mirror effect
        g_object_set(self_flip, "method", 4, nullptr);  // 4 = horizontal flip

        // Add self-view elements to pipeline
        gst_bin_add_many(GST_BIN(pipeline_), self_queue, self_convert, self_scale, self_flip, nullptr);

        // Link self-view chain
        if (!gst_element_link_many(self_queue, self_convert, self_scale, self_flip, nullptr)) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link self-view chain");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Request compositor sink pad for self-view (layer 0 initially - will be background when remote video arrives)
        GstPad *comp_sink0 = gst_element_request_pad_simple(compositor_, "sink_%u");
        if (!comp_sink0) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to request compositor sink pad for self-view");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Configure self-view as fullscreen initially (will be repositioned when remote video arrives)
        g_object_set(comp_sink0,
            "xpos", 0,
            "ypos", 0,
            "zorder", 0,  // Background layer initially
            nullptr);

        // Link tee → self_queue
        GstPad *tee_self_src = gst_element_request_pad_simple(video_tee_, "src_%u");
        GstPad *self_queue_sink = gst_element_get_static_pad(self_queue, "sink");
        GstPadLinkReturn self_link_ret = gst_pad_link(tee_self_src, self_queue_sink);
        gst_object_unref(tee_self_src);
        gst_object_unref(self_queue_sink);

        if (self_link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link tee → self_queue: {}", static_cast<int>(self_link_ret));
            gst_object_unref(comp_sink0);
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Link self_flip → compositor
        GstPad *flip_src = gst_element_get_static_pad(self_flip, "src");
        self_link_ret = gst_pad_link(flip_src, comp_sink0);
        gst_object_unref(flip_src);
        gst_object_unref(comp_sink0);

        if (self_link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link self_flip → compositor: {}", static_cast<int>(self_link_ret));
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Sync all new elements to PLAYING
        gst_element_sync_state_with_parent(self_queue);
        gst_element_sync_state_with_parent(self_convert);
        gst_element_sync_state_with_parent(self_scale);
        gst_element_sync_state_with_parent(self_flip);
        gst_element_sync_state_with_parent(compositor_);
        gst_element_sync_state_with_parent(sink_convert);
        gst_element_sync_state_with_parent(video_sink_);

        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Self-view created immediately (fullscreen until remote video arrives)");

        // Resume pipeline to PLAYING
        LOG_DEBUG("[WebRTCSession] Resuming pipeline to PLAYING...");
        ret = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
        LOG_DEBUG("[WebRTCSession] Resume state change result: {}", static_cast<int>(ret));
        gst_element_get_state(pipeline_, &current_state, nullptr, GST_CLOCK_TIME_NONE);
        LOG_INFO("[WebRTCSession] Pipeline state after resume: {}", gst_element_state_get_name(current_state));

        LOG_INFO("[WebRTCSession] [ANSWERER] Video source pipeline created and linked");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] [ANSWERER] setup_answerer_video_pipeline exception: {}", e.what());
        return false;
    }
}
bool WebRTCSession::setup_offerer_video_pipeline() {
    try {
        LOG_DEBUG("[WebRTCSession] [OFFERER] Creating video source pipeline...");

        // Add video elements to running pipeline without pausing
        // Use gst_element_sync_state_with_parent() to let GStreamer manage state transitions
        LOG_INFO("[WebRTCSession] [OFFERER] Adding video elements to running pipeline...");

        // Create video source - platform-specific
        // Linux: v4l2src (tested, reliable), others: autovideosrc (GStreamer auto-detection)
#ifdef __linux__
        video_src_ = gst_element_factory_make("v4l2src", "video_src");
        const char* source_name = "v4l2src (Linux V4L2)";
#else
        video_src_ = gst_element_factory_make("autovideosrc", "video_src");
        const char* source_name = "autovideosrc";
#endif
        if (!video_src_) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create video source: {}", source_name);
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Using {}", source_name);

        // Set camera device if specified (e.g., "/dev/video0")
        if (!config_.camera_device.empty()) {
            g_object_set(video_src_, "device", config_.camera_device.c_str(), nullptr);
            LOG_INFO("[WebRTCSession] [OFFERER] ✓ Using camera device: {}", config_.camera_device);
        } else {
            LOG_INFO("[WebRTCSession] [OFFERER] Using default camera device");
        }

        // CRITICAL: Enable do-timestamp for proper timestamps (v4l2src only)
#ifdef __linux__
        g_object_set(video_src_, "do-timestamp", TRUE, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Set do-timestamp=TRUE on v4l2src");
#endif

        // Create tee to split camera feed (one branch for encoding, one for self-view PiP)
        video_tee_ = gst_element_factory_make("tee", "video_tee");
        if (!video_tee_) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create tee element");
            return false;
        }

        // CRITICAL: Set allow-not-linked=TRUE to allow encoder branch to work
        // even if self-view branch isn't connected yet (compositor created on incoming video)
        g_object_set(video_tee_, "allow-not-linked", TRUE, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Created tee for camera feed splitting (PiP self-view, allow-not-linked=TRUE)");

        // Create rest of pipeline elements
        GstElement *tee_queue = gst_element_factory_make("queue", "tee_queue_encode");
        GstElement *convert = gst_element_factory_make("videoconvert", "videoconvert");
        GstElement *queue1 = gst_element_factory_make("queue", "queue_pre_encode");
        GstElement *encoder = gst_element_factory_make("vp8enc", "vp8enc");
        GstElement *payloader = gst_element_factory_make("rtpvp8pay", "rtpvp8pay");
        GstElement *queue2 = gst_element_factory_make("queue", "queue_post_pay");
        GstElement *capsfilter = gst_element_factory_make("capsfilter", "rtp_video_caps");

        if (!tee_queue || !convert || !queue1 || !encoder || !payloader || !queue2 || !capsfilter) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create video elements");
            return false;
        }

        // CRITICAL: Configure low-latency queues for real-time video (WebRTC industry standard)
        // max-size-buffers=5: Limit buffering to ~166ms at 30fps (prevents 3-5 sec delay)
        // leaky=2 (downstream): Drop old frames when full, keep pipeline flowing
        g_object_set(tee_queue, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue1, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue2, "max-size-buffers", 5, "leaky", 2, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured low-latency queues (max-size-buffers=5, leaky=downstream)");

        // Configure encoder - realtime with reasonable keyframes for calls
        g_object_set(encoder,
            "deadline", G_GINT64_CONSTANT(1),        // Realtime encoding (lowest latency)
            "cpu-used", 8,                            // Max speed (lowest latency)
            "target-bitrate", 1500000,                // 1.5Mbps
            "keyframe-max-dist", 60,                  // Keyframe every 60 frames (2 seconds at 30fps)
            nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured vp8enc (deadline=1, keyframe-max-dist=60, 1.5Mbps)");

        // CRITICAL: Configure payloader with picture-id-mode=15-bit
        // Official example comment: "This improves TWCC stats behavior and fixes stuttery video playback in Chrome"
        g_object_set(payloader,
            "picture-id-mode", 2,  // 2 = 15-bit mode (enum value from gst-inspect-1.0 rtpvp8pay)
            nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured rtpvp8pay with picture-id-mode=15-bit (fixes stuttering!)");

        // Use payload=96 (standard for VP8)
        GstCaps *rtp_caps = gst_caps_new_simple("application/x-rtp",
            "media", G_TYPE_STRING, "video",
            "encoding-name", G_TYPE_STRING, "VP8",
            "payload", G_TYPE_INT, 96,
            nullptr);
        g_object_set(capsfilter, "caps", rtp_caps, nullptr);
        gst_caps_unref(rtp_caps);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Set RTP caps: application/x-rtp,media=video,encoding-name=VP8,payload=96");

        // Add all elements to pipeline (they will be in PAUSED state, not PLAYING yet)
        gst_bin_add_many(GST_BIN(pipeline_), video_src_, video_tee_, tee_queue, convert, queue1, encoder, payloader, queue2, capsfilter, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Added video elements to pipeline in PAUSED state");

        // Link camera source to tee
        if (!gst_element_link(video_src_, video_tee_)) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link video_src → tee");
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked camera source to tee");

        // Request tee source pad for encoder branch
        GstPad *tee_encode_pad = gst_element_request_pad_simple(video_tee_, "src_%u");
        if (!tee_encode_pad) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to request tee source pad");
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Requested tee source pad for encoder branch");

        // Link tee encoder branch: tee→queue→convert→queue→encoder→payloader→queue→capsfilter
        GstPad *tee_queue_sink = gst_element_get_static_pad(tee_queue, "sink");
        if (gst_pad_link(tee_encode_pad, tee_queue_sink) != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link tee pad to queue");
            gst_object_unref(tee_encode_pad);
            gst_object_unref(tee_queue_sink);
            return false;
        }
        gst_object_unref(tee_encode_pad);
        gst_object_unref(tee_queue_sink);

        if (!gst_element_link_many(tee_queue, convert, queue1, encoder, payloader, queue2, capsfilter, nullptr)) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link video encoder chain");
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked encoder chain: tee→queue→convert→queue→vp8enc→rtpvp8pay→queue→capsfilter");

        // Get webrtcbin sink pad - OFFERER MODE
        // Create new pad (will auto-create transceiver)
        GstPad *webrtc_sink = gst_element_request_pad_simple(webrtc_, "sink_%u");
        if (!webrtc_sink) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to request video sink pad from webrtcbin!");
            return false;
        }

        gchar *pad_name = gst_pad_get_name(webrtc_sink);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Created new video pad: {}", pad_name);
        g_free(pad_name);

        // Set transceiver direction to SENDRECV
        GValue val = G_VALUE_INIT;
        g_object_get_property(G_OBJECT(webrtc_sink), "transceiver", &val);
        GstWebRTCRTPTransceiver *trans = GST_WEBRTC_RTP_TRANSCEIVER(g_value_get_object(&val));

        if (trans) {
            LOG_INFO("[WebRTCSession] [OFFERER] Setting video transceiver direction to SENDRECV...");
            g_object_set(trans, "direction", GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV, nullptr);
            LOG_INFO("[WebRTCSession] [OFFERER] ✓ Video transceiver direction set to SENDRECV");
        } else {
            LOG_WARN("[WebRTCSession] [OFFERER] Could not get transceiver from video pad");
        }
        g_value_unset(&val);

        // Link capsfilter to webrtcbin
        GstPad *caps_src = gst_element_get_static_pad(capsfilter, "src");
        GstPadLinkReturn link_ret = gst_pad_link(caps_src, webrtc_sink);
        if (link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link video capsfilter to webrtcbin: {}", static_cast<int>(link_ret));
            gst_object_unref(caps_src);
            gst_object_unref(webrtc_sink);
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked video capsfilter to webrtcbin");

        gst_object_unref(caps_src);
        gst_object_unref(webrtc_sink);

        // NOW sync all elements to PLAYING state - AFTER all linking is complete
        // This ensures v4l2src only starts capturing when pipeline is fully ready
        gst_element_sync_state_with_parent(video_src_);
        gst_element_sync_state_with_parent(video_tee_);
        gst_element_sync_state_with_parent(tee_queue);
        gst_element_sync_state_with_parent(convert);
        gst_element_sync_state_with_parent(queue1);
        gst_element_sync_state_with_parent(encoder);
        gst_element_sync_state_with_parent(payloader);
        gst_element_sync_state_with_parent(queue2);
        gst_element_sync_state_with_parent(capsfilter);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Synced video elements (incl. tee) to PLAYING (after all linking complete)");

        // CRITICAL: Create self-view branch immediately (don't wait for incoming video)
        // This prevents race conditions and gives instant self-view feedback
        LOG_INFO("[WebRTCSession] [OFFERER] Creating immediate self-view (before incoming video)...");

        // Create compositor for self-view (will add incoming video later)
        compositor_ = gst_element_factory_make("compositor", "video_compositor");
        if (!compositor_) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create compositor for self-view");
            return false;
        }
        g_object_set(compositor_, "background", 1, nullptr);  // 1 = black background

        // Create video sink
#ifdef _WIN32
        video_sink_ = gst_element_factory_make("d3dvideosink", "video_sink");
#else
        video_sink_ = gst_element_factory_make("autovideosink", "video_sink");
#endif
        if (!video_sink_) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create video sink for self-view");
            gst_object_unref(compositor_);
            compositor_ = nullptr;
            return false;
        }
        g_object_set(video_sink_, "sync", TRUE, nullptr);
#ifdef _WIN32
        // D3D9 stability settings for VM/RDP compatibility
        g_object_set(video_sink_,
            "force-aspect-ratio", TRUE,           // Maintain aspect ratio
            "enable-navigation-events", FALSE,    // Reduce event overhead
            "stream-stop-on-close", FALSE,        // Don't stop stream if window closes accidentally
            nullptr);
#endif

        // Create format conversion for compositor → sink
        GstElement *sink_convert = gst_element_factory_make("videoconvert", "video_convert_sink");
        if (!sink_convert) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create sink videoconvert");
            gst_object_unref(compositor_);
            gst_object_unref(video_sink_);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Add compositor and sink to pipeline
        gst_bin_add_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, nullptr);

        // Link compositor → convert → sink
        if (!gst_element_link_many(compositor_, sink_convert, video_sink_, nullptr)) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link compositor → sink");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Create self-view branch: tee → queue → convert → scale → flip → compositor
        GstElement *self_queue = gst_element_factory_make("queue", "self_view_queue");
        GstElement *self_convert = gst_element_factory_make("videoconvert", "self_view_convert");
        GstElement *self_scale = gst_element_factory_make("videoscale", "self_view_scale");
        GstElement *self_flip = gst_element_factory_make("videoflip", "self_view_flip");

        if (!self_queue || !self_convert || !self_scale || !self_flip) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create self-view elements");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, nullptr);
            if (self_queue) gst_object_unref(self_queue);
            if (self_convert) gst_object_unref(self_convert);
            if (self_scale) gst_object_unref(self_scale);
            if (self_flip) gst_object_unref(self_flip);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Configure low-latency queue for self-view
        g_object_set(self_queue, "max-size-buffers", 5, "leaky", 2, nullptr);

        // Configure flip for mirror effect
        g_object_set(self_flip, "method", 4, nullptr);  // 4 = horizontal flip

        // Add self-view elements to pipeline
        gst_bin_add_many(GST_BIN(pipeline_), self_queue, self_convert, self_scale, self_flip, nullptr);

        // Link self-view chain
        if (!gst_element_link_many(self_queue, self_convert, self_scale, self_flip, nullptr)) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link self-view chain");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Request compositor sink pad for self-view (layer 0 initially - will be background when remote video arrives)
        GstPad *comp_sink0 = gst_element_request_pad_simple(compositor_, "sink_%u");
        if (!comp_sink0) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to request compositor sink pad for self-view");
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Configure self-view as fullscreen initially (will be repositioned when remote video arrives)
        g_object_set(comp_sink0,
            "xpos", 0,
            "ypos", 0,
            "zorder", 0,  // Background layer initially
            nullptr);

        // Link tee → self_queue
        GstPad *tee_self_src = gst_element_request_pad_simple(video_tee_, "src_%u");
        GstPad *self_queue_sink = gst_element_get_static_pad(self_queue, "sink");
        GstPadLinkReturn self_link_ret = gst_pad_link(tee_self_src, self_queue_sink);
        gst_object_unref(tee_self_src);
        gst_object_unref(self_queue_sink);

        if (self_link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link tee → self_queue: {}", static_cast<int>(self_link_ret));
            gst_object_unref(comp_sink0);
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Link self_flip → compositor
        GstPad *flip_src = gst_element_get_static_pad(self_flip, "src");
        self_link_ret = gst_pad_link(flip_src, comp_sink0);
        gst_object_unref(flip_src);
        gst_object_unref(comp_sink0);

        if (self_link_ret != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link self_flip → compositor: {}", static_cast<int>(self_link_ret));
            gst_bin_remove_many(GST_BIN(pipeline_), compositor_, sink_convert, video_sink_, self_queue, self_convert, self_scale, self_flip, nullptr);
            compositor_ = nullptr;
            video_sink_ = nullptr;
            return false;
        }

        // Sync all new elements to PLAYING
        gst_element_sync_state_with_parent(self_queue);
        gst_element_sync_state_with_parent(self_convert);
        gst_element_sync_state_with_parent(self_scale);
        gst_element_sync_state_with_parent(self_flip);
        gst_element_sync_state_with_parent(compositor_);
        gst_element_sync_state_with_parent(sink_convert);
        gst_element_sync_state_with_parent(video_sink_);

        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Self-view created immediately (fullscreen until remote video arrives)");

        LOG_INFO("[WebRTCSession] [OFFERER] Video source pipeline created and linked");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] [OFFERER] setup_offerer_video_pipeline exception: {}", e.what());
        return false;
    }
}

void WebRTCSession::handle_incoming_video_stream(GstPad *pad) {
    // Create video receive chain: rtpvp8depay → vp8dec → videoconvert → compositor
        // Compositor and sink were already created with self-view (immediate on call start)
        GstElement *depay = gst_element_factory_make("rtpvp8depay", "video_depay");
            GstElement *decoder = gst_element_factory_make("vp8dec", "video_decoder");
            GstElement *convert = gst_element_factory_make("videoconvert", "video_convert_recv");

            if (!depay || !decoder || !convert) {
                LOG_ERROR("[WebRTCSession] Failed to create video receive elements");
                if (depay) gst_object_unref(depay);
                if (decoder) gst_object_unref(decoder);
                if (convert) gst_object_unref(convert);
                return;
            }

            // Compositor should already exist (created with self-view)
            if (!compositor_) {
                LOG_ERROR("[WebRTCSession] Compositor does not exist! Self-view should have created it.");
                gst_object_unref(depay);
                gst_object_unref(decoder);
                gst_object_unref(convert);
                return;
            }

            LOG_INFO("[WebRTCSession] Adding incoming video to existing compositor (with self-view)");

            // Add receive elements to pipeline
            gst_bin_add_many(GST_BIN(pipeline_), depay, decoder, convert, nullptr);

            // Link incoming video chain: depay → decoder → convert
            if (!gst_element_link_many(depay, decoder, convert, nullptr)) {
                LOG_ERROR("[WebRTCSession] Failed to link video receive chain");
                gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, nullptr);
                return;
            }

            // Request NEW compositor sink pad for incoming video (layer 1 = background behind self-view)
            // Self-view is on layer 0 (created first), incoming video goes to layer 1 (created now)
            // We'll use zorder to control layering: incoming=0 (background), self-view=1 (overlay)
            GstPad *comp_sink_incoming = gst_element_request_pad_simple(compositor_, "sink_%u");
            if (!comp_sink_incoming) {
                LOG_ERROR("[WebRTCSession] Failed to request compositor sink pad for incoming video");
                gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, nullptr);
                return;
            }

            // Configure incoming video as fullscreen background (zorder=0, behind self-view)
            // Repositioning self-view to corner will happen here
            g_object_set(comp_sink_incoming,
                "xpos", 0,
                "ypos", 0,
                "zorder", 0,  // Background layer (incoming video fullscreen)
                nullptr);

            // Link convert → compositor
            GstPad *convert_src = gst_element_get_static_pad(convert, "src");
            GstPadLinkReturn link_ret = gst_pad_link(convert_src, comp_sink_incoming);
            gst_object_unref(convert_src);

            if (link_ret != GST_PAD_LINK_OK) {
                LOG_ERROR("[WebRTCSession] Failed to link convert → compositor: {}", static_cast<int>(link_ret));
                gst_object_unref(comp_sink_incoming);
                gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, nullptr);
                return;
            }

            // NOW reposition self-view from fullscreen to PiP corner (bottom-right)
            // Get the first compositor sink pad (self-view, created earlier)
            GstPad *comp_sink_self = gst_element_get_static_pad(compositor_, "sink_0");
            if (comp_sink_self) {
                // Reposition self-view to bottom-right corner as PiP overlay
                g_object_set(comp_sink_self,
                    "xpos", 480,    // Right side (assuming 640x480 base resolution)
                    "ypos", 360,    // Bottom (480 - 120 = 360)
                    "width", 160,   // Thumbnail width
                    "height", 120,  // Thumbnail height
                    "zorder", 1,    // Overlay on top of incoming video
                    nullptr);
                gst_object_unref(comp_sink_self);
                LOG_INFO("[WebRTCSession] ✓ Repositioned self-view to bottom-right corner (PiP overlay)");
            } else {
                LOG_WARN("[WebRTCSession] Could not get self-view pad to reposition it");
            }

            gst_object_unref(comp_sink_incoming);

            // Sync state with parent
            gst_element_sync_state_with_parent(depay);
            gst_element_sync_state_with_parent(decoder);
            gst_element_sync_state_with_parent(convert);

#ifdef _WIN32
            // Re-trigger window maximize after incoming video linked (fixes outgoing calls)
            // When remote video arrives on outgoing calls, pipeline state changes can
            // reset window z-order. Re-applying positioning ensures window stays in front.
            maximize_d3dvideosink_window();
#endif

            // Link webrtcbin pad to depay
            GstPad *sink_pad = gst_element_get_static_pad(depay, "sink");
            link_ret = gst_pad_link(pad, sink_pad);
            gst_object_unref(sink_pad);

            if (link_ret != GST_PAD_LINK_OK) {
                LOG_ERROR("[WebRTCSession] Failed to link incoming video pad to depay: {}", static_cast<int>(link_ret));
                gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, nullptr);
                return;
            }

            LOG_INFO("[WebRTCSession] ✓ Incoming video added to compositor (fullscreen background, self-view in corner)");
}

} // namespace drunk_call
