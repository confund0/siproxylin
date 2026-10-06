/**
 * WebRTC Session Implementation - Video Pipeline
 *
 * Video pipeline setup for answerer and offerer modes
 */

#include "webrtc_session.h"
#include "logger.h"
#include <gst/webrtc/webrtc.h>

#if defined(__linux__) || defined(_WIN32)
#include "video_shm.h"
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#endif

namespace drunk_call {

// Camera caps before the tee. Linux (v4l2src) takes the first structure:
// 640x480 first, any raw size as fallback. Windows (mfvideosrc) keeps its
// own mode order and fixates to the largest size, so only a limit works there.
#ifdef _WIN32
static const char *kCameraCaps = "video/x-raw,width=[1,640],height=[1,480]";
#else
static const char *kCameraCaps = "video/x-raw,width=640,height=480; video/x-raw";
#endif

#if defined(__linux__) || defined(_WIN32)
// Copy one RGBx sample of the appsink into the shared memory stream for the
// app (streaming thread). owner is the session.
static GstFlowReturn write_sample_to_shm(GstAppSink *appsink, uint32_t stream, gpointer owner) {
    GstSample *sample = gst_app_sink_pull_sample(appsink);
    if (!sample) {
        return GST_FLOW_OK;
    }

    VideoShm *shm = VideoShm::instance();
    GstCaps *caps = gst_sample_get_caps(sample);
    GstBuffer *buffer = gst_sample_get_buffer(sample);
    GstVideoInfo info;
    if (shm && caps && buffer && gst_video_info_from_caps(&info, caps)
        && GST_VIDEO_INFO_FORMAT(&info) == GST_VIDEO_FORMAT_RGBx) {
        GstVideoFrame frame;
        if (gst_video_frame_map(&frame, &info, buffer, GST_MAP_READ)) {
            int width = GST_VIDEO_FRAME_WIDTH(&frame);
            int height = GST_VIDEO_FRAME_HEIGHT(&frame);
            int stride = GST_VIDEO_FRAME_PLANE_STRIDE(&frame, 0);
            const uint8_t *data = static_cast<const uint8_t*>(GST_VIDEO_FRAME_PLANE_DATA(&frame, 0));
            uint64_t pts = GST_BUFFER_PTS_IS_VALID(buffer) ? GST_BUFFER_PTS(buffer) : 0;
            if (width > 0 && height > 0 && stride > 0) {
                shm->write_frame(stream, owner, data,
                                 static_cast<uint32_t>(width), static_cast<uint32_t>(height),
                                 static_cast<uint32_t>(stride), pts);
            }
            gst_video_frame_unmap(&frame);
        }
    }

    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

// appsink new-sample callback for the decoded remote video. user_data is the session.
static GstFlowReturn on_remote_video_sample(GstAppSink *appsink, gpointer user_data) {
    return write_sample_to_shm(appsink, VideoShm::kStreamRemote, user_data);
}

// appsink new-sample callback for the camera self-view. user_data is the session.
static GstFlowReturn on_self_view_sample(GstAppSink *appsink, gpointer user_data) {
    return write_sample_to_shm(appsink, VideoShm::kStreamSelf, user_data);
}

// Self-view branch on the camera tee:
// tee → queue → videoconvert → videoscale → videoflip (mirror) → caps (RGBx 320x240) → appsink.
// Call it after the encoder branch is linked and before the source is synced,
// so no camera frame flows yet. On any failure it logs and returns: the call
// goes on without self-view.
void WebRTCSession::add_self_view_branch(const char *mode) {
    VideoShm *shm = VideoShm::instance();
    if (!shm || !video_tee_) {
        return;
    }

    GstElement *queue = gst_element_factory_make("queue", "self_view_queue");
    GstElement *convert = gst_element_factory_make("videoconvert", "self_view_convert");
    GstElement *scale = gst_element_factory_make("videoscale", "self_view_scale");
    GstElement *flip = gst_element_factory_make("videoflip", "self_view_flip");
    GstElement *caps = gst_element_factory_make("capsfilter", "self_view_caps");
    GstElement *sink = gst_element_factory_make("appsink", "self_view_sink");
    if (!queue || !convert || !scale || !flip || !caps || !sink) {
        LOG_WARN("[WebRTCSession] [{}] Failed to create self-view elements, no self-view", mode);
        if (queue) gst_object_unref(queue);
        if (convert) gst_object_unref(convert);
        if (scale) gst_object_unref(scale);
        if (flip) gst_object_unref(flip);
        if (caps) gst_object_unref(caps);
        if (sink) gst_object_unref(sink);
        return;
    }

    // One frame at most: a slow self-view drops frames and never blocks the encoder branch
    g_object_set(queue, "max-size-buffers", 1, "max-size-bytes", 0, "max-size-time", G_GUINT64_CONSTANT(0),
                 "leaky", 2, nullptr);
    // Mirror image, as users expect for a self-view
    gst_util_set_object_arg(G_OBJECT(flip), "method", "horizontal-flip");
    // Same 4:3 shape as the send branch (640x480)
    GstCaps *self_caps = gst_caps_from_string(
        "video/x-raw,format=RGBx,width=320,height=240,pixel-aspect-ratio=1/1");
    g_object_set(caps, "caps", self_caps, nullptr);
    gst_caps_unref(self_caps);
    // async=FALSE: the sink does not hold up pipeline state changes
    g_object_set(sink,
        "sync", FALSE,
        "async", FALSE,
        "max-buffers", 1,
        "drop", TRUE,
        "emit-signals", FALSE,
        nullptr);
    GstAppSinkCallbacks callbacks = {};
    callbacks.new_sample = on_self_view_sample;
    gst_app_sink_set_callbacks(GST_APP_SINK(sink), &callbacks, this, nullptr);

    // Elements are still in NULL state here: removing them from the bin frees them
    gst_bin_add_many(GST_BIN(pipeline_), queue, convert, scale, flip, caps, sink, nullptr);
    // Scale first: converting the full camera frame to RGBx would cost CPU next to vp8enc
    if (!gst_element_link_many(queue, scale, convert, flip, caps, sink, nullptr)) {
        LOG_WARN("[WebRTCSession] [{}] Failed to link self-view chain, no self-view", mode);
        gst_bin_remove_many(GST_BIN(pipeline_), queue, convert, scale, flip, caps, sink, nullptr);
        return;
    }

    GstPad *tee_pad = gst_element_request_pad_simple(video_tee_, "src_%u");
    if (!tee_pad) {
        LOG_WARN("[WebRTCSession] [{}] Failed to request tee pad for self-view, no self-view", mode);
        gst_bin_remove_many(GST_BIN(pipeline_), queue, convert, scale, flip, caps, sink, nullptr);
        return;
    }
    GstPad *queue_sink = gst_element_get_static_pad(queue, "sink");
    GstPadLinkReturn link_ret = gst_pad_link(tee_pad, queue_sink);
    gst_object_unref(queue_sink);
    if (link_ret != GST_PAD_LINK_OK) {
        LOG_WARN("[WebRTCSession] [{}] Failed to link tee to self-view queue: {}, no self-view",
                 mode, static_cast<int>(link_ret));
        gst_element_release_request_pad(video_tee_, tee_pad);
        gst_object_unref(tee_pad);
        gst_bin_remove_many(GST_BIN(pipeline_), queue, convert, scale, flip, caps, sink, nullptr);
        return;
    }
    gst_object_unref(tee_pad);

    shm->begin_stream(VideoShm::kStreamSelf, this);

    // Sink first, queue last: each element is ready before data reaches it
    gst_element_sync_state_with_parent(sink);
    gst_element_sync_state_with_parent(caps);
    gst_element_sync_state_with_parent(flip);
    gst_element_sync_state_with_parent(scale);
    gst_element_sync_state_with_parent(convert);
    gst_element_sync_state_with_parent(queue);
    LOG_INFO("[WebRTCSession] [{}] ✓ Self-view branch: tee→queue→convert→scale→flip→caps(RGBx 320x240)→appsink (shared memory stream 1)", mode);
}
#endif

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

        // Camera size before the tee (kCameraCaps). Without it the self-view
        // caps (320x240) win on Linux and the camera opens small.
        GstElement *camera_caps = gst_element_factory_make("capsfilter", "camera_caps");
        if (!camera_caps) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create camera capsfilter");
            return false;
        }
        GstCaps *camera_pref_caps = gst_caps_from_string(kCameraCaps);
        g_object_set(camera_caps, "caps", camera_pref_caps, nullptr);
        gst_caps_unref(camera_pref_caps);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Camera caps: {}", kCameraCaps);

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
        // Fixed raw format before the encoder (I420 640x480 at 15 fps)
        GstElement *scale = gst_element_factory_make("videoscale", "videoscale");
        GstElement *rate = gst_element_factory_make("videorate", "videorate");
        GstElement *raw_caps = gst_element_factory_make("capsfilter", "raw_video_caps");
        if (!scale || !rate || !raw_caps) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to create video scale/rate elements");
            return false;
        }
        GstCaps *send_raw_caps = gst_caps_from_string(
            "video/x-raw,format=I420,width=640,height=480,pixel-aspect-ratio=1/1");
        g_object_set(raw_caps, "caps", send_raw_caps, nullptr);
        gst_caps_unref(send_raw_caps);
        // Only drop frames down to 15 fps; never fill the gap before the first camera frame
        g_object_set(rate, "drop-only", TRUE, "skip-to-first", TRUE, "max-rate", 15, nullptr);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Raw caps: I420 640x480, max 15 fps (drop-only)");

        // CRITICAL: Configure low-latency queues for real-time video (WebRTC industry standard)
        // max-size-buffers=5: Limit buffering to ~166ms at 30fps (prevents 3-5 sec delay)
        // leaky=2 (downstream): Drop old frames when full, keep pipeline flowing
        g_object_set(tee_queue, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue1, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue2, "max-size-buffers", 5, "leaky", 2, nullptr);
        LOG_INFO("[WebRTCSession] ✓ Configured low-latency queues (max-size-buffers=5, leaky=downstream)");

        // Lower rate for 640x480 at 15 fps, error resilient stream
        g_object_set(encoder,
            "deadline", G_GINT64_CONSTANT(1),        // Realtime encoding (lowest latency)
            "cpu-used", 8,                            // Max speed (lowest latency)
            "target-bitrate", 600000,                 // 600 kbps
            "keyframe-max-dist", 30,                  // Keyframe every 30 frames (2 seconds at 15fps)
            nullptr);
        gst_util_set_object_arg(G_OBJECT(encoder), "error-resilient", "default");
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Configured vp8enc (deadline=1, cpu-used=8, keyframe-max-dist=30, 600kbps, error-resilient=default)");

        // CRITICAL: Configure payloader with picture-id-mode=15-bit
        // Official example comment: "This improves TWCC stats behavior and fixes stuttery video playback in Chrome"
        g_object_set(payloader,
            "picture-id-mode", 2,  // 2 = 15-bit mode (enum value from gst-inspect-1.0 rtpvp8pay)
            "ssrc", video_ssrc_,   // Same SSRC as in our SDP
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
        gst_bin_add_many(GST_BIN(pipeline_), video_src_, camera_caps, video_tee_, tee_queue, convert, scale, rate, raw_caps, queue1, encoder, payloader, queue2, capsfilter, nullptr);

        // Link camera source to tee
        if (!gst_element_link_many(video_src_, camera_caps, video_tee_, nullptr)) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link video_src → camera caps → tee");
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

        // Link tee encoder branch: tee→queue→convert→scale→rate→caps→queue→encoder→payloader→queue→capsfilter
        GstPad *tee_queue_sink = gst_element_get_static_pad(tee_queue, "sink");
        if (gst_pad_link(tee_encode_pad, tee_queue_sink) != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link tee pad to queue");
            gst_object_unref(tee_encode_pad);
            gst_object_unref(tee_queue_sink);
            return false;
        }
        gst_object_unref(tee_encode_pad);
        gst_object_unref(tee_queue_sink);

        if (!gst_element_link_many(tee_queue, convert, scale, rate, raw_caps, queue1, encoder, payloader, queue2, capsfilter, nullptr)) {
            LOG_ERROR("[WebRTCSession] [ANSWERER] Failed to link video encoder chain");
            return false;
        }
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Linked encoder chain: tee→queue→convert→scale→rate→caps→queue→vp8enc→rtpvp8pay→queue→capsfilter");

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

#if defined(__linux__) || defined(_WIN32)
        // Second tee branch: camera self-view to the app (shared memory).
        // Before the source is synced, so no frame flows yet.
        add_self_view_branch("ANSWERER");
#endif

        // NOW sync all elements to PLAYING state - AFTER all linking is complete
        // This ensures v4l2src only starts capturing when pipeline is fully ready
        gst_element_sync_state_with_parent(camera_caps);
        gst_element_sync_state_with_parent(video_src_);
        gst_element_sync_state_with_parent(video_tee_);
        gst_element_sync_state_with_parent(tee_queue);
        gst_element_sync_state_with_parent(convert);
        gst_element_sync_state_with_parent(scale);
        gst_element_sync_state_with_parent(rate);
        gst_element_sync_state_with_parent(raw_caps);
        gst_element_sync_state_with_parent(queue1);
        gst_element_sync_state_with_parent(encoder);
        gst_element_sync_state_with_parent(payloader);
        gst_element_sync_state_with_parent(queue2);
        gst_element_sync_state_with_parent(capsfilter);
        LOG_INFO("[WebRTCSession] [ANSWERER] ✓ Synced video elements (incl. tee) to PLAYING (after all linking complete)");

        // Other systems: no compositor (Linux: self-view branch above when the
        // shared memory exists). The video sink is created when the incoming
        // video arrives (handle_incoming_video_stream).
        LOG_INFO("[WebRTCSession] [ANSWERER] Video sink waits for incoming video");

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

        // Camera size before the tee (kCameraCaps). Without it the self-view
        // caps (320x240) win on Linux and the camera opens small.
        GstElement *camera_caps = gst_element_factory_make("capsfilter", "camera_caps");
        if (!camera_caps) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create camera capsfilter");
            return false;
        }
        GstCaps *camera_pref_caps = gst_caps_from_string(kCameraCaps);
        g_object_set(camera_caps, "caps", camera_pref_caps, nullptr);
        gst_caps_unref(camera_pref_caps);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Camera caps: {}", kCameraCaps);

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
        // Fixed raw format before the encoder (I420 640x480 at 15 fps)
        GstElement *scale = gst_element_factory_make("videoscale", "videoscale");
        GstElement *rate = gst_element_factory_make("videorate", "videorate");
        GstElement *raw_caps = gst_element_factory_make("capsfilter", "raw_video_caps");
        if (!scale || !rate || !raw_caps) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to create video scale/rate elements");
            return false;
        }
        GstCaps *send_raw_caps = gst_caps_from_string(
            "video/x-raw,format=I420,width=640,height=480,pixel-aspect-ratio=1/1");
        g_object_set(raw_caps, "caps", send_raw_caps, nullptr);
        gst_caps_unref(send_raw_caps);
        // Only drop frames down to 15 fps; never fill the gap before the first camera frame
        g_object_set(rate, "drop-only", TRUE, "skip-to-first", TRUE, "max-rate", 15, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Raw caps: I420 640x480, max 15 fps (drop-only)");

        // CRITICAL: Configure low-latency queues for real-time video (WebRTC industry standard)
        // max-size-buffers=5: Limit buffering to ~166ms at 30fps (prevents 3-5 sec delay)
        // leaky=2 (downstream): Drop old frames when full, keep pipeline flowing
        g_object_set(tee_queue, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue1, "max-size-buffers", 5, "leaky", 2, nullptr);
        g_object_set(queue2, "max-size-buffers", 5, "leaky", 2, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured low-latency queues (max-size-buffers=5, leaky=downstream)");

        // Lower rate for 640x480 at 15 fps, error resilient stream
        g_object_set(encoder,
            "deadline", G_GINT64_CONSTANT(1),        // Realtime encoding (lowest latency)
            "cpu-used", 8,                            // Max speed (lowest latency)
            "target-bitrate", 600000,                 // 600 kbps
            "keyframe-max-dist", 30,                  // Keyframe every 30 frames (2 seconds at 15fps)
            nullptr);
        gst_util_set_object_arg(G_OBJECT(encoder), "error-resilient", "default");
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Configured vp8enc (deadline=1, cpu-used=8, keyframe-max-dist=30, 600kbps, error-resilient=default)");

        // CRITICAL: Configure payloader with picture-id-mode=15-bit
        // Official example comment: "This improves TWCC stats behavior and fixes stuttery video playback in Chrome"
        g_object_set(payloader,
            "picture-id-mode", 2,  // 2 = 15-bit mode (enum value from gst-inspect-1.0 rtpvp8pay)
            "ssrc", video_ssrc_,   // Same SSRC as in our SDP
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
        gst_bin_add_many(GST_BIN(pipeline_), video_src_, camera_caps, video_tee_, tee_queue, convert, scale, rate, raw_caps, queue1, encoder, payloader, queue2, capsfilter, nullptr);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Added video elements to pipeline in PAUSED state");

        // Link camera source to tee
        if (!gst_element_link_many(video_src_, camera_caps, video_tee_, nullptr)) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link video_src → camera caps → tee");
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

        // Link tee encoder branch: tee→queue→convert→scale→rate→caps→queue→encoder→payloader→queue→capsfilter
        GstPad *tee_queue_sink = gst_element_get_static_pad(tee_queue, "sink");
        if (gst_pad_link(tee_encode_pad, tee_queue_sink) != GST_PAD_LINK_OK) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link tee pad to queue");
            gst_object_unref(tee_encode_pad);
            gst_object_unref(tee_queue_sink);
            return false;
        }
        gst_object_unref(tee_encode_pad);
        gst_object_unref(tee_queue_sink);

        if (!gst_element_link_many(tee_queue, convert, scale, rate, raw_caps, queue1, encoder, payloader, queue2, capsfilter, nullptr)) {
            LOG_ERROR("[WebRTCSession] [OFFERER] Failed to link video encoder chain");
            return false;
        }
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Linked encoder chain: tee→queue→convert→scale→rate→caps→queue→vp8enc→rtpvp8pay→queue→capsfilter");

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

#if defined(__linux__) || defined(_WIN32)
        // Second tee branch: camera self-view to the app (shared memory).
        // Before the source is synced, so no frame flows yet.
        add_self_view_branch("OFFERER");
#endif

        // NOW sync all elements to PLAYING state - AFTER all linking is complete
        // This ensures v4l2src only starts capturing when pipeline is fully ready
        gst_element_sync_state_with_parent(camera_caps);
        gst_element_sync_state_with_parent(video_src_);
        gst_element_sync_state_with_parent(video_tee_);
        gst_element_sync_state_with_parent(tee_queue);
        gst_element_sync_state_with_parent(convert);
        gst_element_sync_state_with_parent(scale);
        gst_element_sync_state_with_parent(rate);
        gst_element_sync_state_with_parent(raw_caps);
        gst_element_sync_state_with_parent(queue1);
        gst_element_sync_state_with_parent(encoder);
        gst_element_sync_state_with_parent(payloader);
        gst_element_sync_state_with_parent(queue2);
        gst_element_sync_state_with_parent(capsfilter);
        LOG_INFO("[WebRTCSession] [OFFERER] ✓ Synced video elements (incl. tee) to PLAYING (after all linking complete)");

        // Other systems: no compositor (Linux: self-view branch above when the
        // shared memory exists). The video sink is created when the incoming
        // video arrives (handle_incoming_video_stream).
        LOG_INFO("[WebRTCSession] [OFFERER] Video sink waits for incoming video");

        LOG_INFO("[WebRTCSession] [OFFERER] Video source pipeline created and linked");
        return true;

    } catch (const std::exception &e) {
        LOG_ERROR("[WebRTCSession] [OFFERER] setup_offerer_video_pipeline exception: {}", e.what());
        return false;
    }
}

void WebRTCSession::handle_incoming_video_stream(GstPad *pad) {
    // Create video receive chain: rtpvp8depay → vp8dec → videoconvert → sink
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

            // Linux with shared memory: frames go to the app window (appsink).
            // Otherwise: straight to autovideosink. No compositor, no self-view.
            GstElement *scale = nullptr;
            GstElement *raw_caps = nullptr;
            bool to_app = false;
#if defined(__linux__) || defined(_WIN32)
            to_app = VideoShm::instance() != nullptr;
            if (to_app) {
                scale = gst_element_factory_make("videoscale", "video_scale_recv");
                raw_caps = gst_element_factory_make("capsfilter", "video_caps_recv");
                video_sink_ = gst_element_factory_make("appsink", "video_sink");
                if (!scale || !raw_caps || !video_sink_) {
                    LOG_ERROR("[WebRTCSession] Failed to create videoscale/capsfilter/appsink for incoming video");
                    gst_object_unref(depay);
                    gst_object_unref(decoder);
                    gst_object_unref(convert);
                    if (scale) gst_object_unref(scale);
                    if (raw_caps) gst_object_unref(raw_caps);
                    if (video_sink_) gst_object_unref(video_sink_);
                    video_sink_ = nullptr;
                    return;
                }
                // Fit into 960x960, keep the aspect ratio (square pixels)
                GstCaps *app_caps = gst_caps_from_string(
                    "video/x-raw,format=RGBx,width=[2,960],height=[2,960],pixel-aspect-ratio=1/1");
                g_object_set(raw_caps, "caps", app_caps, nullptr);
                gst_caps_unref(app_caps);
                g_object_set(video_sink_,
                    "sync", TRUE,
                    "max-buffers", 1,
                    "drop", TRUE,
                    "emit-signals", FALSE,
                    nullptr);
                GstAppSinkCallbacks callbacks = {};
                callbacks.new_sample = on_remote_video_sample;
                gst_app_sink_set_callbacks(GST_APP_SINK(video_sink_), &callbacks, this, nullptr);

                LOG_INFO("[WebRTCSession] Adding incoming video with appsink (shared memory to the app)");

                gst_bin_add_many(GST_BIN(pipeline_), depay, decoder, convert, scale, raw_caps, video_sink_, nullptr);

                // Link incoming video chain: depay → decoder → convert → scale → caps → appsink
                if (!gst_element_link_many(depay, decoder, convert, scale, raw_caps, video_sink_, nullptr)) {
                    LOG_ERROR("[WebRTCSession] Failed to link video receive chain");
                    gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, scale, raw_caps, video_sink_, nullptr);
                    video_sink_ = nullptr;
                    return;
                }
                gst_element_sync_state_with_parent(scale);
                gst_element_sync_state_with_parent(raw_caps);
            }
#endif
            if (!to_app) {
                video_sink_ = gst_element_factory_make("autovideosink", "video_sink");
                if (!video_sink_) {
                    LOG_ERROR("[WebRTCSession] Failed to create autovideosink for incoming video");
                    gst_object_unref(depay);
                    gst_object_unref(decoder);
                    gst_object_unref(convert);
                    return;
                }
                g_object_set(video_sink_, "sync", TRUE, nullptr);

                LOG_INFO("[WebRTCSession] Adding incoming video with direct autovideosink (no self-view)");

                gst_bin_add_many(GST_BIN(pipeline_), depay, decoder, convert, video_sink_, nullptr);

                // Link incoming video chain: depay → decoder → convert → sink
                if (!gst_element_link_many(depay, decoder, convert, video_sink_, nullptr)) {
                    LOG_ERROR("[WebRTCSession] Failed to link video receive chain");
                    gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, video_sink_, nullptr);
                    video_sink_ = nullptr;
                    return;
                }
            }

            GstPadLinkReturn link_ret = GST_PAD_LINK_OK;
            gst_element_sync_state_with_parent(video_sink_);

#if defined(__linux__) || defined(_WIN32)
            if (to_app) {
                VideoShm::instance()->begin_stream(VideoShm::kStreamRemote, this);
            }
#endif

            // Sync state with parent
            gst_element_sync_state_with_parent(depay);
            gst_element_sync_state_with_parent(decoder);
            gst_element_sync_state_with_parent(convert);

            // Link webrtcbin pad to depay
            GstPad *sink_pad = gst_element_get_static_pad(depay, "sink");
            link_ret = gst_pad_link(pad, sink_pad);
            gst_object_unref(sink_pad);

            if (link_ret != GST_PAD_LINK_OK) {
                LOG_ERROR("[WebRTCSession] Failed to link incoming video pad to depay: {}", static_cast<int>(link_ret));
                gst_element_set_state(video_sink_, GST_STATE_NULL);
                gst_bin_remove(GST_BIN(pipeline_), video_sink_);
                video_sink_ = nullptr;
                if (scale) {
                    gst_element_set_state(scale, GST_STATE_NULL);
                    gst_bin_remove(GST_BIN(pipeline_), scale);
                }
                if (raw_caps) {
                    gst_element_set_state(raw_caps, GST_STATE_NULL);
                    gst_bin_remove(GST_BIN(pipeline_), raw_caps);
                }
#if defined(__linux__) || defined(_WIN32)
                if (to_app) {
                    VideoShm::instance()->end_stream(VideoShm::kStreamRemote, this);
                }
#endif
                gst_bin_remove_many(GST_BIN(pipeline_), depay, decoder, convert, nullptr);
                return;
            }

            LOG_INFO("[WebRTCSession] ✓ Incoming video linked to {}", to_app ? "appsink (shared memory)" : "autovideosink");
}

} // namespace drunk_call
