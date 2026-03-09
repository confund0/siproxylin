# Video Implementation - Quick Reference

**For AI coding**: Essential info only, file:line references

---

## Current Audio Pipeline Pattern (COPY THIS)

### Offerer (Outgoing Call)

```
webrtc_session.cpp:1006 setup_offerer_audio_pipeline()
  ↓
1. Pause pipeline
2. Create: pulsesrc → queue → audioconvert → audioresample → opusenc → rtpopuspay → capsfilter
3. Request pad: gst_element_request_pad_simple(webrtc_, "sink_%u")
4. Link: capsfilter → webrtcbin sink pad
5. Resume PLAYING
```

### Answerer (Incoming Call)

```
webrtc_session.cpp:826 setup_answerer_audio_pipeline()
  ↓
1. Pause pipeline
2. Create same chain as offerer
3. Reuse negotiated_pad_ (from set_remote_description)
4. Link chain to negotiated_pad_
5. Resume PLAYING
```

### Receive (Both)

```
webrtc_session.cpp:1889 on_incoming_stream()
  ↓
Detect encoding: const char *encoding = gst_structure_get_string(s, "encoding-name")
If OPUS:
  rtpopusdepay → opusdec → queue → autoaudiosink
  Link webrtcbin src pad → rtpopusdepay
```

---

## Video Pipeline (TO ADD)

### Offerer Video

**File**: webrtc_session.cpp (new method after line 1129)

```cpp
bool WebRTCSession::setup_offerer_video_pipeline() {
    // Pause pipeline
    gst_element_set_state(pipeline_, GST_STATE_PAUSED);

    // Create chain
    GstElement *videosrc = gst_element_factory_make("v4l2src", NULL);
    GstElement *queue = gst_element_factory_make("queue", NULL);
    GstElement *convert = gst_element_factory_make("videoconvert", NULL);
    GstElement *scale = gst_element_factory_make("videoscale", NULL);
    GstElement *encoder = gst_element_factory_make("vp8enc", NULL);
    GstElement *payloader = gst_element_factory_make("rtpvp8pay", NULL);
    GstElement *capsfilter = gst_element_factory_make("capsfilter", NULL);

    // Set caps: application/x-rtp,media=video,encoding-name=VP8,clock-rate=90000,payload=96
    GstCaps *caps = parse_video_codec_from_offer(offer_sdp);
    g_object_set(capsfilter, "caps", caps, NULL);

    // Link
    gst_bin_add_many(GST_BIN(pipeline_), videosrc, queue, convert, scale, encoder, payloader, capsfilter, NULL);
    gst_element_link_many(videosrc, queue, convert, scale, encoder, payloader, capsfilter, NULL);

    // Request webrtcbin video pad (sink_1)
    GstPad *sink_pad = gst_element_request_pad_simple(webrtc_, "sink_%u");
    GstPad *src_pad = gst_element_get_static_pad(capsfilter, "src");
    gst_pad_link(src_pad, sink_pad);

    // Resume
    gst_element_set_state(pipeline_, GST_STATE_PLAYING);
}
```

### Answerer Video

**Same as offerer but reuse `negotiated_video_pad_`** (store in set_remote_description)

### Receive Video (UDP Stream)

**File**: webrtc_session.cpp (extend on_incoming_stream after line 1974)

```cpp
void WebRTCSession::setup_video_sink_chain(GstPad *pad) {
    // Decode
    GstElement *depay = gst_element_factory_make("rtpvp8depay", NULL);
    GstElement *decoder = gst_element_factory_make("vp8dec", NULL);
    GstElement *convert = gst_element_factory_make("videoconvert", NULL);

    // Encode to JPEG (for UDP streaming)
    GstElement *jpegenc = gst_element_factory_make("jpegenc", NULL);
    g_object_set(jpegenc, "quality", 85, NULL);

    // Mux and stream
    GstElement *muxer = gst_element_factory_make("multipartmux", NULL);
    GstElement *udpsink = gst_element_factory_make("udpsink", NULL);
    g_object_set(udpsink,
        "host", config_.video_udp_host.c_str(),  // "127.127.69.69"
        "port", config_.video_udp_port,
        "sync", TRUE,  // CRITICAL for A/V sync
        NULL);

    // Link
    gst_bin_add_many(GST_BIN(pipeline_), depay, decoder, convert, jpegenc, muxer, udpsink, NULL);
    gst_element_link_many(depay, decoder, convert, jpegenc, muxer, udpsink, NULL);

    // Link webrtcbin → depay
    GstPad *sink_pad = gst_element_get_static_pad(depay, "sink");
    gst_pad_link(pad, sink_pad);
}
```

**Detect in on_incoming_stream**:

```cpp
if (g_strcmp0(encoding, "OPUS") == 0) {
    setup_audio_sink_chain(pad);
} else if (g_strcmp0(encoding, "VP8") == 0 ||
           g_strcmp0(encoding, "VP9") == 0) {
    setup_video_sink_chain(pad);
}
```

---

## Codec Parsing (TO ADD)

**File**: webrtc_session.cpp (after line 133)

```cpp
static GstCaps* parse_video_codec_from_offer(GstSDPMessage *offer) {
    // Find m=video line
    const GstSDPMedia *media = nullptr;
    for (guint i = 0; i < gst_sdp_message_medias_len(offer); i++) {
        media = gst_sdp_message_get_media(offer, i);
        if (strcmp(gst_sdp_media_get_media(media), "video") == 0) break;
        media = nullptr;
    }
    if (!media) return nullptr;

    // Get first payload
    int payload = atoi(gst_sdp_media_get_format(media, 0));

    // Find rtpmap (e.g., "96 VP8/90000")
    for (guint i = 0; i < gst_sdp_media_attributes_len(media); i++) {
        const GstSDPAttribute *attr = gst_sdp_media_get_attribute(media, i);
        if (strcmp(attr->key, "rtpmap") == 0) {
            int attr_payload;
            char codec_name[32];
            int rate;
            if (sscanf(attr->value, "%d %31[^/]/%d", &attr_payload, codec_name, &rate) == 3) {
                if (attr_payload == payload) {
                    // Uppercase (GStreamer requirement)
                    for (char *p = codec_name; *p; p++) *p = toupper(*p);

                    return gst_caps_new_simple("application/x-rtp",
                        "media", G_TYPE_STRING, "video",
                        "encoding-name", G_TYPE_STRING, codec_name,
                        "clock-rate", G_TYPE_INT, rate,
                        nullptr);
                }
            }
        }
    }
    return nullptr;
}
```

---

## SessionConfig (TO ADD)

**File**: src/media_session.h (after audio config fields)

```cpp
struct SessionConfig {
    // ... existing audio fields ...

    // Video
    bool enable_video_receive = false;
    std::string video_udp_host;
    int video_udp_port = 0;
    std::string video_device;  // Camera (future)
};
```

---

## Protobuf (TO ADD)

**File**: drunk_call_service/proto/call.proto

```protobuf
message CreateSessionRequest {
    // ... existing 1-18 ...
    bool enable_video_receive = 19;
    string video_udp_host = 20;      // "127.127.69.69"
    int32 video_udp_port = 21;       // From Python
}
```

**Regenerate**:
```bash
cd drunk_call_service/proto
protoc --cpp_out=. --grpc_out=. --plugin=protoc-gen-grpc=`which grpc_cpp_plugin` call.proto
cd ../../drunk_call_hook/proto
python -m grpc_tools.protoc -I../../drunk_call_service/proto --python_out=. --grpc_python_out=. ../../drunk_call_service/proto/call.proto
```

---

## Python Bridge (TO MODIFY)

**File**: drunk_call_hook/bridge.py

**Line 403** - Extend create_session:

```python
async def create_session(self, peer_jid: str, session_id: str,
                        # ... existing params ...
                        enable_video: bool = False) -> tuple[bool, Optional[int]]:

    video_port = None
    video_host = ""

    if enable_video:
        video_port = self.video_manager.allocate_video_port()
        video_host = "127.127.69.69"

    request = call_pb2.CreateSessionRequest(
        # ... existing ...
        enable_video_receive=enable_video,
        video_udp_host=video_host,
        video_udp_port=video_port or 0,
    )

    response = await self._stub.CreateSession(request)
    return response.success, video_port
```

**New file**: drunk_call_hook/video_manager.py

```python
import socket

class VideoStreamManager:
    VIDEO_IP = "127.127.69.69"

    def __init__(self):
        self.video_port = None
        self.video_socket = None

    def allocate_video_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.VIDEO_IP, 0))
        self.video_port = sock.getsockname()[1]
        self.video_socket = sock
        return self.video_port

    def release_video_port(self):
        if self.video_socket:
            self.video_socket.close()
            self.video_socket = None
```

**Import in bridge.py**:
```python
from .video_manager import VideoStreamManager

class CallBridge:
    def __init__(self, ...):
        # ...
        self.video_manager = VideoStreamManager()
```

---

## Jingle Video Parsing (TO MODIFY)

**File**: drunk_call_hook/protocol/jingle_sdp_converter.py

**Line 396-462** - Extend jingle_to_sdp() codec parsing:

```python
# Inside jingle_to_sdp(), after audio codec parsing:

if media_type == 'video':
    for payload in description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type'):
        codec_id = payload.get('id')
        codec_name = payload.get('name')  # VP8, VP9, H264
        clock_rate = payload.get('clockrate', '90000')

        sdp_lines.append(f"a=rtpmap:{codec_id} {codec_name}/{clock_rate}")

        # Parse fmtp parameters
        params = []
        for param in payload.findall('{urn:xmpp:jingle:apps:rtp:1}parameter'):
            name = param.get('name')
            value = param.get('value')
            params.append(f"{name}={value}")

        if params:
            sdp_lines.append(f"a=fmtp:{codec_id} {';'.join(params)}")
```

**Line 581** - Detect video in incoming call:

**File**: siproxylin/core/barrels/calls.py

```python
def _on_jingle_incoming_call(self, session_id, peer_jid, sdp_offer, media):
    has_video = 'video' in media  # media = ['audio'] or ['audio', 'video']

    success, video_port = await self.call_bridge.create_session(
        peer_jid=peer_jid,
        session_id=session_id,
        enable_video=has_video,
        # ... other params ...
    )

    # Store video_port for GUI
    if has_video and video_port:
        self.active_calls[session_id]['video_port'] = video_port
```

---

## GUI Video Widget (TO ADD)

**New file**: siproxylin/gui/widgets/video_widget.py

```python
import sys
import vlc
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout

class VLCVideoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vlc_instance = vlc.Instance(["--network-caching=150"])
        self.player = self.vlc_instance.media_player_new()

        self.video_frame = QFrame(self)
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setMinimumSize(320, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_frame)

        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winId())
        elif sys.platform == 'win32':
            self.player.set_hwnd(self.video_frame.winId())
        elif sys.platform == 'darwin':
            self.player.set_nsobject(int(self.video_frame.winId()))

    def play_stream(self, udp_url: str):
        media = self.vlc_instance.media_new(udp_url)
        self.player.set_media(media)
        self.player.play()

    def stop(self):
        self.player.stop()
```

---

## CallWindow Integration (TO MODIFY)

**File**: siproxylin/gui/call_window.py

**Line 39** - Already has media_types, just use it:

```python
def __init__(self, parent, account_id, session_id, peer_jid, media_types, direction):
    # ... existing __init__ ...

    self.has_video = 'video' in media_types
    self.video_widget = None

    if self.has_video:
        from .widgets.video_widget import VLCVideoWidget
        self.video_widget = VLCVideoWidget(self)
        self.main_layout.addWidget(self.video_widget)  # Add after existing widgets

def start_video_playback(self, video_port: int):
    """Called by parent after ICE connects"""
    if self.video_widget:
        url = f"udp://@127.127.69.69:{video_port}"
        self.video_widget.play_stream(url)

def closeEvent(self, event):
    if self.video_widget:
        self.video_widget.stop()
    # ... existing close ...
```

**Parent (MainWindow) calls**:

```python
# After CallBarrel emits incoming_call signal with video_port:
call_window = CallWindow(...)
call_window.show()

if video_port:
    QTimer.singleShot(2000, lambda: call_window.start_video_playback(video_port))
```

---

## Implementation Order

1. **C++ video pipeline** (webrtc_session.cpp) - can test with loopback
2. **Protobuf + Python bridge** (call.proto, bridge.py, video_manager.py)
3. **Jingle parsing** (jingle_sdp_converter.py) - test with Conversations XML
4. **GUI** (video_widget.py, call_window.py)

---

**Last Updated**: 2026-03-07
