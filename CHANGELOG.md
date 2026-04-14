# Changelog

All notable changes to Siproxylin are documented in this file.

---

## [0.0.29 - Glass bottle] - 2026-04-14

> (137e1c05ff)

    Merge branch 'video'

> (a29c9587c9)

    Push d3dvideosink window in front for OFFERER (babysit windows)

> (f7c06cc9f9)

    Push d3dvideosink window in front (babysit windows)

> (0d2db71ecc)

    Change handling of d3dvideosink (babysit windows)

> (0e7bee20ed)

    Adding missing lib (babysit windows)

> (80fe5ce5b5)

    Adding missing include (babysit windows)

> (ead1cae5ca)

    Better handling of GStreamer d3dvideosink (babysit windows)

> (31088a60a6)

    Fix Service Discovery feature broken on Windows
    
    Root cause: slixmpp's DiscoInfo object on Windows doesn't implement
    __contains__() method, causing 'identities' in disco_info to always
    return False even when data exists.
    
    The code was checking disco data availability using Python's 'in'
    operator (e.g., 'identities' in disco_info), which relies on
    __contains__(). This worked on Linux where DiscoInfo implements
    __contains__, but silently failed on Windows where it doesn't,
    resulting in empty disco results despite successful XMPP queries.
    
    Fix: Check disco_info.interfaces set instead of using 'in' operator
    directly. The interfaces attribute exists on both platforms and
    contains the same data ('identities', 'features', 'form').
    
    Changed from:
      if disco_info and 'identities' in disco_info:
    
    To:
      has_identities = 'identities' in getattr(disco_info, 'interfaces', set())
      if disco_info and has_identities:
    
    This works consistently across both Linux and Windows versions of
    slixmpp, regardless of __contains__ implementation.

> (744a8914cc)

    Add more logging for discovery (disco) features

> (67fc496e39)

    Add more logging for discovery (disco) features

> (ce64fb44bc)

    Add more logging for discovery (disco) features

> (9ed72928e5)

    Fixed main.log to respect selected log level

> (c038219c74)

    Add more logging for discovery (disco) features

> (b531961e68)

    Fix async call for discovery (disco) features (babysit windows)

> (2763138fa4)

    Add more logging for discovery (disco) features

> (08596eccd6)

    Add more logging for discovery (disco) features

> (3e88aa69a3)

    Add more logging for discovery (disco) features

> (11a29b2852)

    Add more logging for discovery (disco) features

> (5f9b3ad699)

    An attempt to maximize video call viewer (babysit windows)

> (13c3438ef1)

    Added cleaning GStreamed cache on app startup

> (bc1ce54df9)

    Added slim mode for call window. Call window always "on-top" to not cover video. Call window detached from the parent to be visible on top of Direct3d video.

> (4658f6d1ae)

    Updated docs

> (a735e22c61)

    Updated docs

> (3baecfaf77)

    Switching to "d3dvideosink" (babysit windows)

> (21b79aa555)

    Updated CLAUDE.md

> (3b29795e7a)

    Split src/webrtc_session.cpp monolith monster into manageable chunks

> (48d539d396)

    Fix video call race condition and latency
    
    - Create self-view window immediately when video pipeline starts (both
      offerer and answerer modes), eliminating race condition where self-view
      would fail to link when remote video arrived late
    
    - Configure low-latency queues for all video paths (max-size-buffers=5,
      leaky=downstream) reducing buffering from ~6.7 seconds to ~166ms at 30fps,
      following WebRTC industry standards
    
    - Refactor incoming video handler to add remote video to existing compositor
      instead of creating new one, repositioning self-view from fullscreen to
      PiP corner when remote video arrives

> (f07aecf563)

    Inserted sink_convert into video output pipeline (babysit windows)

> (b2863eeb91)

    Making video sources OS dependent

> (4f71ba41db)

    Adding very primitive "self-view" overlay

> (fbf8b638ec)

    Add RTCP feedback capabilities
    
    Problem: Incoming video from Conversations/Quicksy showed periodic pixelation
    during longer calls - video would degrade to unrecognizable quality, stay
    pixelated for 10-20 seconds, then suddenly clear up and repeat the cycle.
    
    The Fix:
    Added RTCP feedback properties to video codec-preferences caps in 3 locations:
    1. parse_video_codec_from_offer() - answerer mode (incoming calls)
    2. create_offer() - offerer mode codec-preferences (outgoing calls)
    
    Now includes:
    - "rtcp-fb-nack-pli": TRUE (request keyframes on packet loss)
    - "rtcp-fb-ccm-fir": TRUE (full intra request for severe errors)
    - "rtcp-fb-transport-cc": TRUE (transport-wide congestion control)
    
    Result: SDP answer now advertises proper RTCP feedback capabilities, enabling:
    - Fast recovery from packet loss (phone can request keyframes)
    - Proper bandwidth adaptation via TWCC feedback
    - Smooth incoming video quality without periodic pixelation

> (f775e33a07)

    Updated docs

> (df455b62ed)

    Updated CLAUDE.md

> (f337630cf7)

    Added video device (camera) choice to File -> Settings dialog

> (a2dd288d5d)

    Update README.md

> (6a9025261e)

    Fix incoming video calls from Conversations
    
    Two critical bugs preventing Conversations→SP video calls:
    
    1. Transceiver order mismatch: Code assumed audio-first m-line ordering (Dino
       style), but Conversations sends video-first SDP offers. Added dynamic
       m-line detection and request pads in correct order based on offer.
    
    2. Payload type mismatch: setup_answerer_video_pipeline() hardcoded payload=98,
       but Conversations' offer specifies payload=96. Phone received PT=98 packets
       when expecting PT=96 per SDP answer → couldn't decode → black screen.
       Now parse and use negotiated payload from offer SDP.
    
    Result: calls in all directions with Dino and Conversations work fine.

> (0bf8cdfa2f)

    Updated docs

> (d86d27b6cb)

    Updated CLAUDE.md

> (d435d70f1e)

    Fix outgoing video calls (dynamic bundle-policy)
    
    This commit fixes the race condition in outgoing video call setup and
    implements role-specific bundle policies to support both Dino (unbundled)
    and Conversations (bundled) peers.
    
    Changes:
    
    1. Fix v4l2src race condition in setup_offerer_video_pipeline():
       - Remove manual pause/resume of pipeline
       - Move sync_state_with_parent() AFTER all linking is complete
       - Prevents v4l2src from starting before webrtcbin is ready
       - Fixes "code should not be reached" error at gstwebrtcbin.c:5236
    
    2. Implement dynamic bundle-policy based on call direction:
       - OFFERER (create_offer): Uses BALANCED to adapt to peer's response
       - ANSWERER (create_answer): Uses MAX_COMPAT to match peer's offer
       - Remove static bundle-policy from configure_webrtcbin()
    
    3. Remove broken transceiver 'mid' property assignments:
       - Property is read-only, assignments were failing silently
       - Let webrtcbin manage mid values automatically
    
    Fixes the "Internal data stream error, not-linked (-1)" crash that
    prevented outgoing video calls to Dino from working.
    
    Test Results:
    - ✅ SP → Conversations: Works perfectly
    - ✅ SP → Dino: Works (fixed!)
    - ✅ Dino → SP: Works
    - ⚠️  Conversations → SP: Partial (video one-way, to be fixed)

> (3dbe32f10c)

    Fix video calls - add transceiver mid assignment
    
    - Set explicit mid values on webrtcbin transceivers (audio0, video1)
    - Fix Python media list to pass ["audio", "video"] for video calls
    - Fix Jingle ICE candidate mline_index mapping
    - Make cal service stdout log truncate on each restart
    
    Partial fix:
    - Incoming from Dino video works on both ends but or GStreamer video window
      is very slow to start.
    - Outgoing to Dino currently does not work at all (no video on neither side).
    - Incoming from Conversations - we see the video comingh from the phone,
      but phone does not show our video.
    - Outgoing to Conversations connects fast, opens video on both ends of a
      good quality

> (579f6807d5)

    Some progress with video performance on Dino end; Conversations still does not show any

> (1175758fee)

    Initial working incoming video stream without VLC (GStreamer native window)

> (ec1aebbb06)

    Got the video call connecting after the merge of main with Windows changes

> (cf8a971d2e)

    Merge branch 'main' into video

> (54b1f5596b)

    Updated docs

> (7ff52d36cb)

    Documents cleanup

> (adce5b80ce)

    Updated docs

> (ddb1b54eaa)

    Updated docs

> (682861fbbc)

    Updated docs

> (6177af8bd0)

    Updated docs

> (462b6c4b50)

    Documents cleanup

> (c545f9818e)

    Update README.md

> (a486462d66)

    Update README.md

> (52c66e3518)

    First buggy clumsy dirty frozen and pretty much ueseless... but a working video call

## [0.0.28 - Firewater] - 2026-03-26

> (eef90be15c)

    Releasing first version which supports Windows

> (80a74d559c)

    Merging branch "win" into "main"
    
    - Updated C++ call service code to compile under MS Windows.
    - Fixed paths of app data to use Windows standard locations.
    - Cherry-picked required GStreamer libraries.
    - Cherry-picked required VCPKG libraries.
    - Created a separate release of dependencies (bunch of zipped dll
      files).
    - Created code which generates bundled Inno (ISS) installer, with
      embedded GST/VCPKG libraries and Python.
    - Lots of small bugfixes.
    
    Status:
    
    - Application seems to be functional on MS Windows.
    - Release to GitHub process still requires manual actions.
    
    TODO: Automate Windows builds work with GitHub actions.

## [deps-v0.0.27 - Fermented] - 2026-03-13

> (dc9527d2ba)

    Update README.md

> (722fa50035)

    Update README.md

> (75c78afdd9)

    Update WINDOWS.md

> (d3d81e8ebf)

    Create docs/WINDOWS.md

## [0.0.27 - Fermented] - 2026-03-11

> (767e6c784f)

    VLC player fixes and AppImage debloat (removed some unnecessary libs)

> (0d57d4f093)

    VLC fixes

> (0e2d8f17d8)

    Release: add python3-vlc (fix attachments), multi-line code blocks, URL handling

> (b183cc2213)

    Add clickable URLs and multi-line code blocks
    
    Major improvements to message rendering:
    
    - Replace QSyntaxHighlighter with direct HTML conversion for synchronous rendering
    - Add clickable URL detection with theme-aware colors
    - Implement URL context menu (Open Link, Copy Link Address)
    - Support inline code spans (`code`) with gray background
    - Add multi-line code block support (```language\n...\n```)
    - Preserve whitespace (spaces and tabs) in code blocks
    - Apply XEP-0393 formatting: *bold*, _italic_, ~strikethrough~, `monospace`
    - Fix bubble width calculation to account for monospace font width
    - Protect URLs inside code blocks from being converted to links
    
    All message formatting is now rendered synchronously via HTML,
    eliminating async rendering issues and bubble overflow.

## [0.0.26 - Fermented] - 2026-03-11

> (ac6170b06c)

    Added video player, GStreamer settings tab, improved logging settings

> (64ad3d5971)

    Add video thumbnail with play button overlay
    
    - VLC-based thumbnail generation at 1 second mark
    - Aspect ratio preserved (320px width, proportional height)
    - Play button drawn with QPainter for cross-platform reliability
    - Thumbnails cached in ~/.siproxylin/cache/video_thumbnails/
    - Tested on Wayland (Sway)
    - Should work on X11, Windows, macOS

> (46405d4022)

    Adding a video player for view video attachments

> (934399d405)

    Default log level WARNING and logs menus
    
    Reduce default logging verbosity to minimize disk usage and improve
    performance for regular users. Logs are now behind the Admin Tools
    setting to keep the UI clean for non-technical users.
    
    Logging Changes:
    - Set default log level to WARNING for all logs (main, call service)
    - Disable XMPP protocol log by default (xml_log_enabled: false)
    - Reduces INFO-level noise while preserving warnings and errors
    
    UI Changes:
    - Hide View → Logs menu and separator when Admin Tools is disabled
    
    Default behavior:
    - Main log: enabled, WARNING level
    - XMPP protocol log: disabled
    - Call service log: WARNING level
    - Logs menu: hidden (unless admin tools enabled)
    
    Users can still access full logging by enabling Admin Tools in settings.

> (1fc55cc3cf)

    Add GStreamer debug tab with configurable logging
    
    Add a new "GStreamer" tab to the settings dialog allowing advanced users
    to configure GStreamer/libnice debug environment variables for call
    troubleshooting. The tab provides input validation and some
    category hints.
    
    Settings Dialog Changes:
    - Add GStreamer tab with inputs for GST_DEBUG, G_MESSAGES_DEBUG, NICE_DEBUG
    - Add Video tab placeholder (disabled) for future camera settings
    - Add call service log level configuration in Logging tab
    
    Path Management:
    - Add call_service_log_path(), call_service_stdout_log_path(),
      call_service_stderr_log_path() methods to paths.py
    - Centralize all call service log paths for consistency
    
    Call Bridge Integration:
    - Read GStreamer debug settings from gstreamer.json config file
    - Read call service log level from logging.json config file
    - Replace hardcoded calls "DEBUG" log level with configurable setting
    
    Input Validation:
    - GST_DEBUG: validates category:level format (levels 0-9)
    - G_MESSAGES_DEBUG: validates comma-separated categories or "all"
    - NICE_DEBUG: validates "all" or empty
    - Real-time validation with visual feedback (red border on error)
    - Spaces automatically removed before validation

## [0.0.25 - Single malt] - 2026-03-10

> (ef94cca263)

    Adding python3-gi / gir1.2-glib-2.0 / libgirepository-1.0-1 to the AppImage (dependencies for GIO file operations)

> (bccb221d7e)

    Release:
    
    - Add image viewer and "Open With" for attachments
    - Message info dialog now allows copy-paste message properties
    - Look and feel improvements
    - Added libxcb-cursor0 to AppImage to reduce OS dependencies

> (4315d95183)

    Add image viewer and OS-native file operations
    
    Features:
    - Image viewer dialog with zoom controls (in/out/fit/actual size)
    - Click images in chat to open viewer (pointer cursor on hover)
    - Ctrl+scroll and Ctrl+/-/0 keyboard shortcuts for zoom
    
    Context menu improvements:
    - Reorganized image context menu with separators
    - Added "Open With..." for OS-native app chooser
      - Windows: Native "Open With" dialog
      - macOS: Reveal in Finder
      - Linux: Custom GIO-based application chooser dialog
    
    Refactoring:
    - Centralized file operations in utils/file_utils.py
      - open_file_with_external_app() for "Open With" functionality
      - save_file_as() for file save dialog + copy
    - Removed code duplication between viewer and context menu
    
    Technical:
    - Linux: Uses GIO (PyGObject) for application discovery by MIME type
    - Requires system python3-gi (already present for GStreamer)
    - Non-blocking viewer dialog (can open multiple simultaneously)
    - Proper cleanup with Qt.WA_DeleteOnClose

> (03a9d47256)

    Adding libxcb-cursor0 to AppImage (depencency library for Qt)

> (8b88cd64b0)

    Look and feel improvements
    
    - Picture copy/paste (e.g. screenshot) does not dissapear with cleanup
    - Picture copy/paste resets the input field styling (kept the red font)
    - Unencrypted messages only displayed with pink background where OMEMO
      is available and not used. In OMEMO incapable chats colours remain
    default
    - Input field got 🛡️🔒 and 🛡️❌  markers respectively to warn user about
      unavailable / disabled OMEMO

> (f596137de5)

    Message info dialog now allows copy-paste message properties

> (df1676a1ef)

    DST debug vars set

> (d9fe93a590)

    Updated docs

## [0.0.24 - Oak barrel] - 2026-03-10

> (ea3a981b7a)

    Release: fixed echo/noise/auto_gain controls, fixed Jingle issue of SP->SP calls, fixed call window mute button and network stastics

> (b66d32f0af)

    Wired echo/noise/auto_gain controls to the call service

> (aaaf2936f0)

    Updated Jingle issue causing Siproxylin->Siproxylin call connection issues

> (71dff8a7d4)

    Fix call window stats and mute button
    
    After migration from Go Pion to C++ GStreamer we faced a limitation of
    incomplete cnnectivity data exposed by API. As we are trying to keep
    this app as privacy oriented we want to be able to show all call candidates
    exposed during a call negotation.
    
    Issues fixed:
    1. Mute button non-functional - added volume element to audio pipelines
    2. Connection state stuck on "new" - moved ICE state retrieval before parse_stats
    3. Incomplete peer IPs - now shows all received candidates (not just tested ones)
    4. "Connected via" showing "--" - added candidate collection and matching system
    
    Implementation:
    - Added CollectedCandidate struct to store all ICE candidates as they arrive
    - Implemented parse_ice_candidate() to parse RFC 5245 candidate strings
    - Modified on_ice_candidate() to collect local candidates during ICE gathering
    - Modified add_remote_ice_candidate() to collect remote candidates as received
    - Rewrote parse_stats() with two-pass approach:
      * Pass 1: Collect stats data from webrtcbin (may be incomplete)
      * Pass 2: Use our collected candidates for complete display
    - Added fallback matching by IP when candidate ID mismatch occurs
    - Added volume element to both answerer/offerer audio pipeline chains

## [0.0.23 - Sober morning] - 2026-03-10

> (dc2e6cc9b6)

    Release: MAM for MUC fixes, nicer background colors in dark theme tables, improved reply handling

> (bb74b960d0)

    Quick update to use slixmpp XEP-0428

> (a0ea417b30)

    Add XEP-0428 replies and fix message error logging
    
    Implements XEP-0428 to properly handle reply fallback text using
    character position markers instead of string parsing. This fixes
    nested reply chains and enables future visual quote rendering.
    
    Also fixes spurious warning logs for auto-response failures.
    
    XEP-0428 Implementation:
    - Add drunk_xmpp/xep_0428.py implementing XEP-0428 spec
    - Store fallback markers in new database table (migration v16→v17)
    - Extract markers from incoming stanzas (MUC, private, carbons)
    - Format outgoing replies with proper nested quoting (>> not > >)
    - Fix quote detection to work with any quote level (not just "> ")
    - Use format_reply_with_fallback as single source of truth
    - Fix format mismatch between slixmpp and custom formatter
    
    Message Error Logging Fix:
    - Downgrade auto-response errors to DEBUG level
    - slixmpp auto-acks chat state notifications (XEP-0184)
    - When remote session ends, receipt fails with "User session not found"
    - This is expected behavior - not a real error
    - Errors without origin-id are auto-responses, not user messages
    - Clean logs: only show warnings for actual message failures
    
    Key features:
    - Nested quotes work correctly: ">> " instead of "> > "
    - Marker positions accurate for fallback stripping
    - Consistent formatting between GUI storage and XMPP sending
    - Backward compatible with legacy clients
    - Clean logs without spurious warnings
    
    Related: XEP-0461 (Message Replies), XEP-0184 (Delivery Receipts), XEP-0085 (Chat States)
    Database: Schema version 16 → 17

> (d4857c82f6)

    Fixing MAM retrieval for MUC. Adding message IDs to logging of MAM message processing

> (d789003aa6)

    Added alternate background color property to QSS for darker themes to avoid snowy white contrast

> (6b2837d7d2)

    Update README.md

> (8b3e0369d6)

    Update README.md

> (9ae0d06641)

    Update README.md

## [0.0.22 - Acetaldehyde] - 2026-03-07

> (84515e5af8)

    Improved handling audio devices

> (91a26e3b8d)

    Add audio profile fixing for Linux USB devices
    
    - Create audio_profiles.py utility to auto-switch USB audio cards from
      input-only or output-only profiles to duplex profiles on startup
    - Makes Linux behave like Windows/Mac where USB headsets expose both
      mic and speakers simultaneously without manual configuration
    - Integrates into app startup (Linux-only, no-op on other platforms)
    - Uses pactl JSON API to query and switch PulseAudio/PipeWire profiles
    
    Users can now mix and match any mic/speaker combo (e.g., USB mic with
    laptop speakers) without manually switching PulseAudio profiles.

> (63a7d09c49)

    Fix audio device enumeration
    
    - Enable show_all_devices for GStreamer device monitor to expose devices
      from hidden providers (fixes PipeWire/ALSA compatibility layer issues)
    - Filter monitor devices (software loopbacks)
    - Filter card-level generic devices without proper device IDs
    - Filter by media.class to exclude wrong device types (show_all_devices
      returns both sources and sinks regardless of filter)
    - Add deduplication by device ID to prevent duplicate entries in UI
    - Fixes inconsistent device counts and missing devices

> (0023f64a57)

    Improve audio device settings dialog
    
    - Find CallBridge from available accounts if not passed
    - Add user-friendly messages when service unavailable
    - Fix timing: load saved settings after devices are enumerated
    - Update info labels to show enumeration status

> (82eddcc389)

    Enhance call logging with device display names (Part 2 - forgotten places)

> (f544b26041)

    Added casting to C++ code (Raspberry compiler issues)

> (6b91978d4a)

    Enhance call logging with device display names
    
    - Add optional microphone_display_name and speakers_display_name parameters to bridge.create_session()
    - Update _load_audio_settings() to return both device IDs and display names from JSON
    - Format logs to show: "Device Name (device_id)" for better disambiguation
    - Example: "mic=Family 17h HD Audio (alsa_input.pci-0000_05_00.6.analog-stereo)"
    - C++ call service still receives only device IDs (no breaking changes)
    - Backward compatible: display names are optional, defaults to showing ID only

> (21af7f31e3)

    Fix audio device choice (the call service side)

> (552ce881a0)

    Updated docs

> (a9c70fe2f5)

    Updated README

## [0.0.21 - Hangover] - 2026-03-07

> (c027c19b74)

    Updated appimage.yml to trigger build env recreation

> (a25457342d)

    Fixing build env dependencies .github/workflows/release.yml

> (3e9ef0a67a)

    Switching to C++ based called service

> (11c8ab12ab)

    Update .gitignore

> (6788f4e402)

    Cleanup tests (delete accidental binaries)

> (d237e4cbf3)

    Updates to tests

> (8ba4918f89)

    Merge branch 'main' into calls-gst

> (3d981e1825)

    Updated AppImage building code to match new C++ call service

> (314fdf57bc)

    Fix MAM retry loop for empty conversations
    
    - Track queried conversations in mam_queried_jids set
    - Skip MAM query silently if already attempted (no log spam)
    - Prevents repeated MAM requests every 2 seconds for empty chats
    - Session-scoped: one MAM attempt per conversation per app session

> (5bf65f646f)

    Updated "resource" (the part added as JID/resource) to be truly random

> (c91dd67a43)

    Fixed some users shown as MUC

> (b78408693b)

    Small tweaks to test script

> (2046d5e397)

    Rotate STDERR log of call service on app restart (holds mostly GStreamer debug messages and occasional exceptions). Increase log level of GStreamer and LibNICE debug

> (c6f5f73ae9)

    !!! Media Mid Map fix which solved incoming call from Conversations issues !!!

> (70dcffccaa)

    Incoming and outgoing calls with Dino (trickle-ICE) now sound works in all directions

> (99b98f4b3b)

    Separating audio setup functions for incoming and outgoing calls. This will introduce some code duplication however reduce bug regressions. Subject to be optimized into sigle codebase later

> (a12cd791d3)

    BREAKTHROUGH! Outgoing call with Dino works!

> (dfc0135454)

    Got "Connecting input stream to rtpbin" message in GStreamer debug, however sound is still not reaching the peer

> (a4ab2c0785)

    Tweak .gitignore and update docs

> (02dd347e2d)

    Call service state when sound from peer can be hear but no sound goes in the opposite direction

> (93db8c5e70)

    Fixed audio bandwidth stats collection

> (f7cd5f5f40)

    Add .gitignore and cleanup

> (7ba3aee874)

    Adding test script and .gitignore

> (25bac982f7)

    Adding some old tests

> (5c5d9aacbb)

    Proper handling audio devices from GUI (needed device_id to operate and device_name to display)

> (306f049868)

    Updated docs + cleanuo

> (a890ae0232)

    Simple WebRTC test added: two binaries - test_webrtc_caller test_webrtc_answerer - that handle basic audio transmission

> (49fe584538)

    IMPORTANT: Working test code which creates one transceiver and SDP answer with a=sendrecv as opposed to a=recvonly

> (fe16183fc8)

    Made Jingle respect SDP direction from the call service answer

> (3fc5238d26)

    Fix incoming call connection
    
    This commit fixes the issue where incoming calls showed "connected" on our
    side but "calling" on the peer side (Dino). The fix involves two major
    components working together:
    
    ● Python: Incoming Call State Machine (Jingle/CallBridge)
    
    Reason: Siproxylin has 4 asynchronous components: slimxpp, GUI, gRPC and
    GStreamer. WebRTC with Jingle requires certain order of messages which
    is nearly impossible to handle. Added IncomingCallState enum with 6 states
    to synchronize incoming call setup and prevent race conditions:
    
      HAVE_OFFER → RESOURCES_READY → SESSION_CREATED → REMOTE_SET
        → ANSWER_READY → ACTIVE
    
    Key features:
    - Buffers transport-info candidates until C++ session ready
    - Prevents "Session not found" errors from trickle-ICE
    - Sequential state transitions ensure correct timing
    - Memory-safe cleanup on session termination
    
    ● C++: Transceiver Direction Configuration (GStreamer webrtcbin)
    
    Fixed webrtcbin defaulting to RECVONLY direction for incoming calls by
    explicitly setting transceivers to SENDRECV at TWO critical points:
    
    1. PRE: BEFORE set-remote-description in create_answer()
       - Preserves our intent to send bidirectional audio
       - Prevents webrtcbin from changing direction when processing offer
    
    2. POST: AFTER set-remote-description in on_offer_set_for_answer()
       - Verifies transceiver direction was preserved
       - Logs direction and current-direction for debugging

> (6c132c4d55)

    Fix 4.7: GStreamer error handling improvements
    
    Implemented 4 critical error handling fixes following official GStreamer patterns:
    
    Issue #6 (SERIOUS): Add GStreamer bus error monitoring
    - Added bus watch to monitor ERROR, WARNING, EOS, and STATE_CHANGED messages
    - Logs errors with source element names and debug info
    - Foundation for future ErrorEvent propagation to Python
    - Pattern: https://gstreamer.freedesktop.org/documentation/application-development/basics/bus.html
    
    Issue #8 (SERIOUS): Graceful pipeline shutdown with timeout
    - Wait for state change completion with 5-second timeout
    - Log warnings/errors if timeout or failure occurs
    - Force cleanup anyway to prevent resource leaks
    - Pattern: https://gstreamer.freedesktop.org/documentation/application-development/basics/states.html
    
    Issue #9 (MODERATE): Cleanup zombie elements on pad link failure
    - Remove elements from pipeline and set to NULL state on link failure
    - Prevents resource leaks when incoming stream linking fails
    - Pattern: https://gstreamer.freedesktop.org/documentation/application-development/advanced/pipeline-manipulation.html
    
    Issue #7 (MODERATE): Detailed logging for partial pipeline failures
    - Per-element creation failure messages with helpful hints
    - Descriptive pad link error messages with error type decode
    - Proper cleanup on all error paths in create_pipeline()

> (655e17f93f)

    Replaced "std::cout" with "LOG_*" macros

> (ef85c4bd69)

    Reduced possible mem-leaks

> (58c63201a8)

    Update docs

> (38ef536ef2)

    Fix test_step9_session_manager for call::CallEvent
    
    Issue: Test failed to compile after Phase 4.3 namespace changes
    - Session 9 changed CallEvent from global struct to call::CallEvent (protobuf)
    - session_manager.h now forward-declares call::CallEvent and uses it in event_queue
    - test_step9_session_manager.cpp was never updated to match
    
    Root Cause:
    - Test used ThreadSafeQueue<CallEvent> (missing call:: namespace)
    - std::deque requires complete type for sizeof(), forward declaration insufficient
    - Proto headers needed for complete call::CallEvent definition

> (7eb0b6fa6f)

    PipeWire Double-Free bug fix & Phase 4.6 features
    
    Bug Fix: PipeWire Double-Free in Device Enumeration
    - Issue: AddressSanitizer detected double-free when calling ListAudioDevices
      - PipeWire plugin's internal threads conflicted with GstDeviceMonitor cleanup
      - Rapid start/stop of monitor triggered threading race condition
    - Solution: Remove gst_device_monitor_start/stop() calls entirely
      - Per GStreamer docs: get_devices() probes hardware even if not started
      - Avoids start/stop cycle that triggers PipeWire double-free bug
      - Hotplug still works (monitor queries current state each call)
    - Files: drunk_call_service/src/device_enumerator.cpp
    
    Phase 4.6: Implemented Remaining RPCs
    1. ListAudioDevices RPC
       - Enumerates microphones (Audio/Source) and speakers (Audio/Sink)
       - Uses DeviceEnumerator with PipeWire fix
       - Result: 3 inputs + 1 output detected successfully
    
    2. SetMute RPC
       - Controls microphone mute state via webrtc->set_mute()
       - Thread-safe session lookup via SessionManager
    
    3. GetStats RPC
       - Returns connection states, bandwidth, ICE candidates
       - Maps WebRTC Stats struct to proto message
       - Includes both local and remote candidate lists
    
    Test Improvements
    - Updated test_grpc_service.sh to use real SDP from CreateOffer
      - Captures SDP from CreateOffer response, uses it for CreateAnswer test
      - Fallback: Production SDP from working Jingle call (2026-03-02)
      - Fixes CreateAnswer test (was failing with handcrafted minimal SDP)
    - Proper test flow: session-1 creates offer, session-2 creates answer
    - All 11/12 RPCs now fully tested and working
    
    Results
    - CreateAnswer: Generates 650-byte answer with 12 ICE candidates ✅
    - ListAudioDevices: Enumerates devices without crashes ✅
    - All Phase 4.6 methods working correctly
    - Service ready for Phase 4.7 (error handling) and 4.8 (Python integration)

> (32c21c0a62)

    Phase 4.5: Implement AddICECandidate RPC
    
    Implemented ICE candidate handling for remote candidates:
    - Get session from SessionManager
    - Create ICECandidate struct from proto request
    - Call webrtc->add_remote_ice_candidate()
    - Return success or NOT_FOUND/INTERNAL error
    
    Changes:
    - call_service_impl.cpp:439-479 - Full AddICECandidate implementation
    - test_grpc_service.sh:143-145 - Use real ICE candidate format
    - test_ice_candidates.sh (NEW) - Comprehensive ICE test suite
      * Tests host/srflx/relay candidate types
      * Validates error handling (invalid session)
      * Stress tests rapid trickle ICE (10 candidates)
    
    Testing verified:
    - Local ICE gathering produces 12 candidates (4 interfaces × 3 transports)
    - Remote candidates flow: gRPC → WebRTCSession → webrtcbin → libnice
    - All candidate types processed correctly
    - Invalid sessions return proper NotFound error

> (0ff628f13a)

    Fix bugs in gRPC shutdown and SDP operations
    
    Bug #1: Resource leak on duplicate session creation
    - When duplicate session detected, orphaned GStreamer pipeline kept running
    - Fix: Stop pipeline before returning error
    
    Bug #2: Signal handler race condition causing shutdown hangs
    - Signal handler quit GLib loop before cleanup → webrtc->stop() hangs
    - Fix: Remove g_main_loop_quit() from signal handler, let main shutdown
      sequence quit loop AFTER session cleanup
    - Bonus: Reduce shutdown poll interval from 1s to 100ms
    
    Bug #3: Use-after-free crash in CreateOffer/CreateAnswer
    - Lambda captured stack variables by reference [&]
    - Function returns/times out → stack destroyed → callback crashes
    - Fix: Use shared_ptr<SDPCallbackState> to keep state alive
    
    All three bugs discovered during test_grpc_service.sh testing.
    Service now handles duplicate sessions, responds to Ctrl+C/SIGTERM
    correctly, and no longer crashes on SDP operations.

> (cf7b32a945)

    Add verbose logging and shutdown handlers
    
    Enhances DEBUG logging for all gRPC methods and implements graceful shutdown
    for all three scenarios (SIGINT, SIGTERM, gRPC Shutdown) with proper session
    cleanup. Adds test script for exercising all RPC methods.
    
    Logging improvements:
    - call_service_impl.cpp: Add "gRPC: {method}" DEBUG logs to all RPC handlers
      - Consistent format: "gRPC: CreateSession - session_id=..."
      - Makes logs easy to grep for specific RPC calls
    - Add LOG_WARN for unimplemented methods with phase numbers
      - Example: "Method not implemented: CreateOffer (Phase 4.4)"
      - Immediately visible when Python calls unready methods
    - Heartbeat uses LOG_TRACE to avoid spam (called every 5s)
    - Main shutdown sequence: Phase 8.x DEBUG logs for each step
    
    Graceful shutdown:
    - call_service_impl.h: Add cleanup_all_sessions() public method
    - call_service_impl.cpp: Implement cleanup_all_sessions()
      - Iterates all active sessions, stops WebRTC, shuts down event queues
      - Logs duration for each cleaned up session
      - Shutdown RPC: Calls cleanup + sets global g_shutdown_requested flag
    - main.cpp: Enhanced shutdown sequence (Phase 8.1 through 8.5)
      - Step 1: Cleanup all active sessions via service.cleanup_all_sessions()
      - Step 2: Shutdown gRPC server (5s graceful deadline)
      - Step 3: Stop GLib main loop (check if already stopped by signal)
      - Step 4: Deinitialize GStreamer
      - Step 5: Shutdown logger
    - main.cpp: Keep signal handler async-signal-safe
      - Only sets atomic flag + quits GLib loop (no logging/cleanup)
      - Session cleanup happens in main() to avoid safety issues
    - Proper namespace handling: extern g_shutdown_requested in global namespace
    
    Makefile improvements:
    - make clean: Show detailed output before deletion
      - File counts, directory sizes, binary sizes
      - "already clean" message if nothing to delete
    
    Testing:
    - tests/test_grpc_service.sh: Comprehensive gRPC test script
      - Tests all implemented methods (CreateSession, StreamEvents, EndSession)
      - Tests all unimplemented methods (shows WARN logs)
      - Tests session lifecycle with background StreamEvents
      - Tests graceful shutdown via gRPC Shutdown command
      - Option: --keep-running to leave service up for manual testing
      - Color-coded output, shows last 50 log lines with highlighting

> (51d1b0dd6a)

    Phase 4.3: CreateSession, StreamEvents, EndSession
    
    Implements core session lifecycle and event streaming for gRPC service.
    
    Changes:
    - call_service_impl.cpp: Implement CreateSession, StreamEvents, EndSession RPCs
      - CreateSession: Creates WebRTCSession, sets ICE/state callbacks, adds to SessionManager
      - StreamEvents: Blocks on ThreadSafeQueue, streams CallEvents to client
      - EndSession: Marks inactive, shuts down queue, stops WebRTC, removes from manager
    - session_manager.h: Update CallSession to use proto::CallEvent (forward declare call::CallEvent)
    - CMakeLists.txt: Add library sources (webrtc_session.cpp, session_manager.cpp, device_enumerator.cpp, logger.cpp)
    
    Fixes:
    - TURN config: Build URL for turn_servers vector (was accessing non-existent fields)
    - State callback: Use MediaSession::ConnectionState signature (not separate ice_state/gathering_state)
    
    Testing:
    - Verified with grpcurl: CreateSession succeeds, StreamEvents blocks/streams, EndSession cleans up
    - Threading model validated: No deadlocks, clean lifecycle, GLib→Queue→gRPC working correctly
    - Logs show proper session lifecycle with duration tracking

> (3c4ab39613)

    gRPC Service Skeleton + CLI Interface
    
    Implements gRPC server layer bridging Python (Jingle) ↔ C++ (WebRTC):
    
    Service Implementation:
    - main.cpp: GLib main loop thread + gRPC server with proper threading
    - call_service_impl.{h,cpp}: 12 RPC handlers (stub implementations)
    - All RPCs return UNIMPLEMENTED (Phase 4.3+ will implement logic)
    
    CLI Parameters (matching Go service):
    - --port <port>         gRPC server port (default: 50051)
    - --log-level <level>   DEBUG, INFO, WARN, ERROR (default: INFO)
    - --log-path <path>     Log file path (auto-creates ../app/logs/)
    - --test-devices        Test device enumeration and exit
    - --help                Show usage information
    
    Build System:
    - CMakeLists.txt: Platform-specific binary naming (drunk-call-service-linux)
    - Protobuf/gRPC code generation
    - Dependencies: GStreamer, gRPC, spdlog, GLib
    - Debug build with AddressSanitizer + leak suppression (lsan.supp)
    
    Threading Model (from docs/CALLS/GSTREAMER-THREADING.md):
    - Main thread: Initialize, start GLib thread, start gRPC server, wait
    - GLib thread: Process GStreamer callbacks, push events to queues
    - gRPC thread pool: Handle RPC calls, pop events from queues
    
    Memory Management:
    - gst_deinit() for proper cleanup
    - lsan.supp for known GLib/GStreamer internal allocations
    - Clean shutdown (no deadlocks, verified with timeout tests)
    
    Binary: drunk-call-service-linux (13MB debug, ~3MB release)
    
    Testing:
    - Service starts, listens on configurable port
    - Heartbeat RPC: Returns OK
    - All other RPCs: Return UNIMPLEMENTED with phase info
    - Custom port/log-level verified
    
    Next: Phase 4.3 (CreateSession + StreamEvents implementation)

> (d3d0f1d533)

    Phase 4.1 Complete: Thread Infrastructure + Tests
    
    Phase 4.1 - Thread Infrastructure:
    - Add ThreadSafeQueue<T> template for cross-thread event streaming
    - Add SessionManager for thread-safe session map
    - Comprehensive tests: 13 tests total (6 queue + 7 manager)
    - Critical test: validate remove-while-in-use pattern (shared_ptr safety)
    
    Test Organization:
    - Rename all tests to test_step{num}_{name} pattern
    - Add test_step1b_audio_playback (2-second 440Hz tone test)
    - Update Makefile with consistent targets
    - 21 tests total, all passing
    
    Documentation:
    - Add THREAD-INFRASTRUCTURE-USAGE.md (quick reference guide)
    - Add LOGGING-POLICY.md (STDOUT/STDERR/Logger rules)

> (7c244cad69)

    Phase 5 - Extract SSRC to SSRCHandler + Unit Tests
    
    Extract SSRC parsing/filtering logic to dedicated handler following the
    same clean pattern as RtcpMuxHandler and TrickleICEHandler.
    
    **Key Changes:**
    - Add SSRCHandler with 4 static methods (parse, filter, build, extract)
    - Update JingleSDPConverter to use SSRCHandler (~53 lines → ~7 lines)
    - Export SSRCHandler from features package
    - Add 18 comprehensive unit tests for SSRCHandler
    
    **SSRCHandler Methods:**
    - parse_ssrc_from_sdp() - Parse a=ssrc: lines into dict
    - filter_ssrc_params() - Filter to allowed parameter names
    - build_jingle_ssrc_elements() - Build Jingle <source> XML elements
    - extract_ssrc_params() - Extract param names from offer (for echo)
    
    **Tests (18 new unit tests):**
    - parse_ssrc_from_sdp: 5 tests (single/multiple params, malformed, etc.)
    - filter_ssrc_params: 3 tests (allowed/empty/no-match)
    - build_jingle_ssrc_elements: 4 tests (offer/answer/multiple/empty)
    - extract_ssrc_params: 4 tests (single/multiple/none/empty)
    - Round-trip integration: 2 tests (parse→build→extract, offer-answer)

> (df16680bc9)

    Jingle - Centralize candidate queuing (Phase 4)
    
    Centralize scattered ICE candidate queuing logic into single decision point.
    
    Key Changes:
    - Add _should_queue_candidate() - single method for queue decisions
    - Add _flush_pending_candidates() - centralized flush after session-accept
    - Update _on_ice_candidate_from_webrtc() to use centralized check
    - Update _on_bridge_ice_candidate() to use centralized check
    - Fix MAJOR BUG: Two duplicate queue checks with different state lists!
      (proposing/proceeding/pending vs proposing/.../pending/incoming/accepted)
    
    Pattern:
    - Queue candidates UNTIL session stanza exchange completes
    - Bulk-include ALL queued candidates in session-initiate/session-accept
    - After exchange, send new candidates individually via transport-info

> (466c286cb9)

    Session 3: Jingle Refactor Phase 2 Complete
    
    RtcpMuxHandler:
    - Created dedicated handler for rtcp-mux negotiation (RFC 5761, XEP-0167)
    - 5 static methods for different negotiation scenarios
    - Comprehensive documentation and Conversations.im compatibility notes
    
    JingleSDPConverter Integration:
    - Integrated RtcpMuxHandler into sdp_to_jingle() and jingle_to_sdp()
    - Explicit rtcp-mux logic for offers and answers
    - Removed implicit "messy" behavior
    
    Features Package:
    - New drunk_call_hook/protocol/features/ directory
    - Structure ready for future handlers (TrickleICE, SSRC, etc.)

> (0258a64eee)

    Integrate JingleSDPConverter with SSRC support
    
    Session 2 Part 2: Jingle Refactor Phase 2
    
    SSRC Support (completing Phase 1):
    - Add SSRC parsing to sdp_to_jingle() method
    - Implement SSRC filtering: offers include all params, answers filter to match offer
    - Add 3 comprehensive SSRC tests (offer, answer filtered, answer no-SSRC)
    - All 12/12 core tests passing
    
    Integration (Phase 2):
    - Import and initialize JingleSDPConverter in JingleAdapter.__init__
    - Replace _jingle_to_sdp() calls (2 locations: lines 225, 307)
    - Replace _extract_offer_details() → extract_offer_context() (line 253)
    - Replace _sdp_to_jingle() calls (2 locations: lines 765-772, 851-860)
      - Changed from in-place modification to functional pattern
      - Copy content/group elements from converter output to wrapper
      - Pass offer_context for answer feature echoing
    - Delete 4 old methods: _extract_offer_details, _echo_offer_features,
      _sdp_to_jingle, _jingle_to_sdp (560 lines removed)

> (08b701a6ae)

    Add JingleSDPConverter - SDP ↔ Jingle (Phase 1)
    
    Session 2: Jingle Refactor Phase 1 Complete
    
    Implementation:
    - Add JingleSDPConverter class (drunk_call_hook/protocol/jingle_sdp_converter.py)
      - extract_offer_context(): Extract BUNDLE, rtcp-mux, codecs from offer
      - sdp_to_jingle(): Convert SDP → Jingle XML (257 lines from jingle.py)
      - jingle_to_sdp(): Convert Jingle XML → SDP (126 lines from jingle.py)
      - Pure conversion, NO business logic, NO session state
    
    Testing:
    - Add comprehensive test suite (tests/test_jingle_sdp_converter.py)
      - 9/9 core tests passing, 3 skipped (future features)
      - Tests for: basic conversion, rtcp-mux, BUNDLE, round-trip, error handling
      - TDD approach: tests written first, implementation made them pass
    
    Documentation:
    - Add SESSION-LOG.md: Multi-session progress tracker
    - Add SESSION-1-SUMMARY.md: Session 1 recap
    - Add JINGLE-REFACTOR-PLAN.md: 5-phase Python cleanup plan
    - Add PROTO-IMPROVEMENTS.md: Proto buffer improvements needed
    - Add GSTREAMER-THREADING.md: Threading model verification
    - Update 4-GRPC-PLAN.md: Complete gRPC integration architecture
    
    Status:
    - Phase 1 complete: Converter extracted, tested, ready for integration
    - NO behavior change yet (converter not integrated into JingleAdapter)
    - Next: Phase 2 - Integrate converter into jingle.py

> (7fc5a383f9)

    Implement stats, video enumeration, and logger
    
    Add comprehensive statistics collection with bandwidth tracking:
    - MediaSession::Stats struct with connection states, bandwidth, quality metrics
    - WebRTCSession::get_stats() using GStreamer stats API and webrtcbin properties
    - ICE states from webrtcbin properties (not stats structure)
    - Bandwidth calculation: delta-based (bytes * 8) / time_ms in Kbps
    - Stats use gst_structure_get_enum() for GstWebRTCStatsType
    - Test: test_stats.cpp validates stats collection
    
    Add video device enumeration:
    - VideoDevice struct with device_path field
    - DeviceEnumerator::list_video_sources() and get_default_video_source()
    - Cross-platform: Linux/V4L2, Windows/KsVideo, macOS/AVFoundation
    - Updated test_device_enumeration.cpp with video device listing
    
    Add spdlog logger with file rotation:
    - Logger class with LOG_* macros (TRACE/DEBUG/INFO/WARN/ERROR/CRITICAL)
    - File output: ~/.siproxylin/logs/drunk-call-service.log (configurable)
    - Rotating file sink: 10MB max, 3 files
    - CLI args: -log-level, -log-path (for future gRPC service)
    - Channel separation: user logs→file, libnice→STDOUT, exceptions→STDERR
    - Test: test_logger.cpp validates all log levels and file creation

> (fbc79ed804)

    Addding HTTP/SOCKS5 proxy support

> (15c2d372e6)

    Step 3: ICE Candidate Handling & Connectivity
    
    Implements full ICE candidate gathering, exchange, and connectivity
    establishment between WebRTC peers using GStreamer webrtcbin.
    
    Implementation (docs/CALLS/3-ICE-PLAN.md):
    - Add ICE gathering state monitoring (NEW → GATHERING → COMPLETE)
    - Implement candidate filtering for relay-only mode (privacy)
    - Add comprehensive ICE connectivity test with trickle ICE simulation
    - Verify ICE state transitions: NEW → CHECKING → CONNECTED → COMPLETED
    
    WebRTCSession enhancements:
    - on_ice_gathering_state() handler for gathering state transitions
    - Candidate filtering in on_ice_candidate() (filters host/srflx in relay mode)
    - Connected signals for notify::ice-gathering-state
    
    Test results (test_step3_ice_connectivity):
    - 15 ICE candidates per session (12 host + 3 srflx via STUN)
    - Full trickle ICE exchange with background thread
    - Both sessions reach COMPLETED state (~6.3s)
    - pad-added signal fires, incoming media streams linked
    - Clean shutdown, no memory leaks

> (e44c526c92)

    Step 2: SDP Negotiation (docs/CALLS/2-SDP-PLAN.md)
    
    - Full offer/answer negotiation between two WebRTCSession instances
    - Signaling state monitoring: STABLE → HAVE_LOCAL_OFFER/HAVE_REMOTE_OFFER → STABLE
    - SDP validation: BUNDLE, trickle ICE, DTLS fingerprints all present
    - Promise-based async SDP operations (create-offer, create-answer, set-remote-description)
    - Incoming media handling via dynamic pad-added signal
    
    Test Results:
    - test_step0_gstreamer_basic: All plugins available, basic pipeline works
    - test_step1_pipeline: SDP offer 501 bytes, 15 ICE candidates, mute works, signaling states correct
    - test_step2_sdp_negotiation: Offer + answer negotiation, both sides 15 candidates each

> (315a41c364)

    Update docs and Implement WebRTCSession class
    
    Completed all tasks from docs/CALLS/1-PIPELINE-PLAN.md:
    - WebRTCSession class implementing MediaSession interface (698 lines)
    - Full pipeline creation: audio_src → volume → queue → opusenc → rtpopuspay → capsfilter → webrtcbin
    - Webrtcbin configuration: bundle-policy=max-bundle, STUN/TURN support, ICE transport policy
    - Signal handlers: on-negotiation-needed, on-ice-candidate, pad-added, ice-connection-state
    - SDP operations: create_offer, create_answer, set_remote_description (async with callbacks)
    - ICE candidate handling (local and remote)
    - Incoming media stream handling (dynamic pad-added)
    - Mute control via volume element
    - SessionFactory with plugin detection for dual-path support (webrtcbin/rtpbin)

> (bca3936c6a)

    Adding initial planning documents

> (74b6563667)

    Cleanup of call service

> (c9f066f947)

    Adding debug logs to drunk_call_hook/bridge.py

> (e64cb70c90)

    Cleanup of call service (Go code moved)

> (5a6c08dd85)

    Fix MAM loading for 1:1 chats without UI blocking
    
    1. Add on-demand MAM loading for 1:1 conversations
       - Automatically loads history when opening chats with no local messages
       - MUC rooms already had this, now 1:1 chats work the same way
       - Prevents duplicate queries with loading state tracking
    
    2. Convert MAM to non-blocking async generator
       - retrieve_history() now yields pages instead of collecting all messages
       - Page size reduced from 300 to 25 messages
       - 1-second async sleep between pages to keep UI responsive
       - Per-page database commits prevent long-running transactions
    
    3. Update all MAM callers for page-based processing
       - messages.py: load_private_chat_history_on_demand() and _retrieve_private_chat_history()
       - muc.py: _retrieve_muc_history()
       - Emit UI refresh signals after each page for incremental display

> (0f08595d52)

    Improve MAM sync and fix message delivery issues
    
    Key improvements:
    - Reduced overlap window from 1 hour to 5 minutes (92% fewer duplicates)
    - Added early duplicate detection: stops after 10 consecutive duplicates
    - Removed artificial 500-message limit (now unlimited, XMPP handles safely)
    - Fixed session resume gap: trigger MAM catchup on XEP-0198 resume
      (solves "messages only appear after sending or restart" issue)
    - Added start_id parameter support for future efficient catchup
    
    Database changes:
    - Created v15_to_v16 migration for mam_catchup state tracking table
    - Added mam_catchup table to schema.sql (single-range approach)
    
    Expected impact:
    - 90-95% reduction in redundant MAM queries per connection
    - Near-zero duplicate spam in logs after initial sync
    - Big improvement to message delivery reliability

## [0.0.20 - High-Proof-Moonshine] - 2026-02-28

> (35968b6a6e)

    Releasing v0.0.20: 1. More admin tools; 2. Emoji picker history

> (795b2bfabc)

    Improvements to emoji picker

> (1986bcb9a3)

    Adding Tools->Disco for admins to query on arbitrary JIDs

> (3a8052fd65)

    Merge pull request #1 from weiss/fix/app-image-url
    
    README.md: Fix link to latest AppImage

> (e59baa9209)

    README.md: Fix link to latest AppImage

## [0.0.19 - High-Proof-Moonshine] - 2026-02-24

> (6855a245ea)

    Added feature to quickly query "disco"

> (d0ea28bb83)

    Added PyYAML to requirements.txt

> (65feb070dc)

    Add Service Discovery (Disco) admin tool
    
    - New checkbox Settings → Advanced → Enable Admin Tools
    - Context menu 'Disco' on contacts/MUCs/accounts (when Admin Tools enabled)
    - Shows original server XML response, pretty-formatted
    - Has YAML/XML toggle display modes

## [0.0.18 - High-Proof-Moonshine] - 2026-02-24

> (f419566c12)

    Releasing v0.0.18 (critical bugfix)

> (2ae7bad05b)

    Important fix of misuse of slixmpp API causing spurious XML elements and potentially leading to message loss

> (6d807d8dda)

    Registered git hook which creates CHANGELOG.md automatically

## [0.0.17 - Double-Distilled] - 2026-02-24

> (0c01ad0264)

    Releasing v0.0.17

> (406c748a60)

    Wired up "mute" button (call window)

> (abbaca5e8e)

    Added "Copy JID" context menu to contacts

> (b0241c6ea5)

    Fix "OMEMO key trust" UI interaction with backend

> (bb6c224ecf)

    Adding a document that explains OMEMO use with Siproxylin

> (7e58476153)

    Fixed a regression bug introduced by dialog refactoring which prevented OMEMO keys from being displayed in the contact details

> (139608c19c)

    Add image paste feature with EXIF stripping
    
    - Intercept image pastes from clipboard (Ctrl+V)
    - Preserve original format (PNG→PNG, JPEG→JPEG, WebP, BMP)
    - Strip EXIF metadata automatically for privacy (Qt behavior)
    - Safe temp file cleanup via tracking list
    - Support quality=95 for JPEG to minimize compression artifacts
    - Red paperclip indicator with filename display (like attached with
      button)
    - Cleanup on success, failure, cancel, and conversation switch

## [0.0.16 - Double-Distilled] - 2026-02-23

> (df8b6a6224)

    MUC handling improvements

> (079709076e)

    Add MUC room destruction feature
    
    Implements XEP-0045 §10.9 room destruction for room owners.
    
    Changes:
    - Add destroy_room() wrapper in DrunkXMPP client (drunk_xmpp/client.py)
    - Add destroy_room() method in MucBarrel with full cleanup
      - Destroys room on server
      - Removes bookmark from server (XEP-0402)
      - Cleans local DB (bookmark, roster, conversation with CASCADE)
      - Emits roster_updated signal
    - Add "Destroy Room" button in MUC Details Dialog Settings tab
      - Dark red styling, visible only for owners of persistent rooms
      - Two-stage confirmation: warning + optional reason
    - Add destroy_muc() handler in MUCManager
      - Permission and connection checks
      - User-friendly error messages for common XMPP errors
      - Closes chat view if room was open
    - Wire signal: dialog → DialogManager → MainWindow → MUCManager
    
    Pattern follows barrel architecture: business logic in barrel,
    GUI handles only confirmation and error display.

> (48066c18da)

    Fix MUC join button for non-bookmarked rooms
    
    Fixes issue where "Join Room" button didn't appear or work for MUC rooms
    that exist in the conversation table but have no bookmark (e.g., after
    leaving a room via right-click "Leave" on old versions).

> (c8e0cafc9d)

    Add reverse MUC invite flow from Contact Manager
    
    Implements ability to invite contacts to MUC rooms from the Contact Manager
    dialog, complementing the existing room-first invite flow.
    
    User Flow:
    1. Open Contact Manager (Tools → Contacts)
    2. Select a contact
    3. Click "Invite to Room..." button
    4. SelectMucDialog opens:
       - Step 1: Select MUC room (shows ALL joined rooms across all accounts)
       - Step 2: Select which account to send invite from (filtered by room membership)
       - Step 3: Optional invitation message
    5. Invite sent via XMPP from chosen account
    
    Implementation:
    - NEW: SelectMucDialog with multi-account MUC selection
    - Added "Invite to Room..." button to Contact Manager (after "Open Chat")
    - Added MUCManager.invite_contact_to_room() method
    - Dynamic account selector based on selected room
    - Shows only accounts that are members of selected MUC
    
    Key Features:
    - Multi-account aware (can invite from any account joined to the room)
    - Permission-based sorting (highest permissions first)
    - Runtime state using client.joined_rooms (accurate)
    - Offline account detection

> (ba9a765479)

    Add MUC invite sending functionality
    
    Implements mediated MUC invitations (XEP-0045 §7.8.2) allowing users to
    invite contacts to group chats from the roster context menu.
    
    Implementation:
    - Added DrunkXMPP.send_muc_invite() wrapper for slixmpp's invite() method
    - Added MucBarrel.invite_to_room() API with connection checking
    - Created InviteContactDialog for JID input and optional reason field
    - Added "Invite Contact..." menu item to MUC context menu in roster
    - Integrated with MUCManager and MainWindow signal routing
    
    User Flow:
    1. Right-click MUC room in roster
    2. Click "Invite Contact..."
    3. Enter contact's JID and optional invitation message
    4. Invite sent via XMPP mediated invitation
    
    Technical Notes:
    - Passwords are NOT sent in invites (standard XMPP behavior per XEP-0045)
    - For password-protected rooms, communicate password separately
    - Dialog auto-enables Send button when JID entered
    - Error handling for offline accounts

> (3db20d4025)

    Fix MUC invite handling
    
    Fixes four critical bugs in MUC invitation flow:
    
    1. Missing signal registration (siproxylin/core/brewery.py)
       - Added muc_invite_received and muc_role_changed to _signals dict
       - Fixes KeyError crash when invite callback tried to emit signal
    
    2. Invite double-processing (drunk_xmpp/client.py)
       - Messages with <body> + MUC invite extension processed twice
       - Added check to skip MUC invites in _on_private_message handler
       - Prevents wrong conversation type (type=0 instead of type=1)
    
    3. Unwanted auto-join behavior (siproxylin/gui/managers/muc_manager.py)
       - Removed blocking dialog that auto-joined with autojoin=1
       - Changed to create bookmark with autojoin=0
       - Removed auto-join logic and unused helper method
       - User can now join manually via "Join Group" button
    
    4. Join button stuck in "Joining..." (siproxylin/gui/chat_view/taps/header.py)
       - Race condition between join success handler and refresh timer
       - Reset button text/state before hiding on successful join
       - Fixes stuck state in leave→re-invite→join scenario

> (d4c404d3c8)

    Fixed MUC Dialog Reason Label Duplication

## [0.0.15 - Double-Distilled] - 2026-02-23

> (ec0ed0fa54)

    Remove stale MUC cache from DB and clean dead code
    
    Remove obsolete muc_nonanonymous and muc_membersonly columns that were
    caching MUC OMEMO compatibility. These caused stale data bugs and are
    now replaced by live disco_cache reads.
    
    Code cleanup:
    - Remove unused encryption field read in _update_encryption_button_visibility()
    - Remove redundant DB write that duplicated header toggle handler
    - Update docstring to reflect actual behavior (uses disco_cache, not DB)
    
    Database migration v13→v14:
    - Drop conversation.muc_nonanonymous column
    - Drop conversation.muc_membersonly column
    - Update schema version to 14
    
    All MUC feature checks now use in-memory disco_cache exclusively,
    ensuring button state matches current room capabilities.

> (c7c736f07f)

    Make MUC config updates reflect in GUI properly
    
    Problem: When room configuration changed (e.g., toggling anonymity for
    OMEMO support), the encryption button didn't update for non-owner
    accounts and showed stale state based on database cache.
    
    Root causes:
    1. Wrong event handler - listening to groupchat_message instead of
       groupchat_config_status for MUC status codes
    2. Missing status codes - only handled 104, but servers send 173/174
       for privacy/anonymity changes (whois, logging, etc.)
    3. Stale database field - button visibility checked DB encryption field
       instead of live disco_cache, showing button even when room no
       longer supported OMEMO
    4. Signal timing - roster_updated emitted only after bookmark DB update,
       not immediately after disco_cache refresh
    
    Changes:
    - drunk_xmpp/client.py: Add groupchat_config_status handler for status
      codes 104, 172, 173, 174; update disco_cache in
      _handle_room_config_changed()
    - siproxylin/core/barrels/muc.py: Emit roster_updated immediately after
      disco_cache update; read muc_nonanonymous/membersonly from disco_cache
      in get_room_info()
    - siproxylin/gui/chat_view/chat_view.py: Read MUC features from
      disco_cache instead of DB encryption field for button visibility
    - siproxylin/gui/managers/roster_manager.py: Update button on
      roster_updated signal
    - siproxylin/gui/muc_details_dialog.py: Add 1.5s delay for server disco
      propagation after config save

## [0.0.14 - Double-Distilled] - 2026-02-12

> (69631abbd1)

    Fix MUC details dialog when disconnected
    
    Tab visibility based on joined status:
    - Not joined (gray MUC): Only show Settings tab (local bookmark settings)
    - Joined (blue MUC): Show Info + Participants + Settings + (Config if owner)
    - Settings tab always visible for editing bookmark details (name, nick, password)
    
    Disconnect handling fixes:
    - Clear joined_rooms on disconnect (fixes MUC staying blue after disconnect)
    - Skip live data fetch when disconnected (prevents timeouts)
    - Load cached data only when offline
    
    Async crash prevention:
    - Added _destroyed flag to prevent UI updates after dialog closed
    - Check flag before all async UI updates (disco fetch, config fetch, refresh)
    - Fixes crash: "Internal C++ object already deleted" when closing during operations

> (ef1e2c0603)

    Fix MUC visibility and joined/not-joined UX
    
    Chat list improvements:
    - Show MUCs with pending invitations (not just bookmarked/roster MUCs)
    - Show roster contacts even without messages (fixes new contact visibility)
    - Show conversations with messages even if not in roster (server messages)
    
    MUC color coding (theme-aware):
    - Blue: joined MUCs (presence='available')
    - Gray: not-joined MUCs (presence='unavailable')
    - Matches offline contact styling for consistency
    
    Leave Room behavior (privacy-focused):
    - Now deletes conversation + all messages (not just bookmark)
    - Updated confirmation dialog to reflect permanent deletion
    - Room disappears from chat list after leaving
    
    MUC details dialog:
    - Fix: Works for bookmarked MUCs even before joining
    - Query changed from conversation-based to bookmark-based with LEFT JOIN
    
    Fixes issues where:
    - New contacts didn't appear until messages exchanged
    - MUC invitations were invisible (unread counter with no chat)
    - Left MUCs remained in chat list due to lingering conversations
    - MUC details dialog failed for non-joined bookmarked rooms

> (673170dc6c)

    Adding few screenshots to README

## [0.0.13 - Double-Distilled] - 2026-02-11

> (d51cec6fc3)

    Bump version

> (a1924fef23)

    Fixes 1. Forgotten signal connections after main window refactioring; 2. Display messages from contacts that are not in roster;

> (91f881ec99)

    Update README.md

## [0.0.12 - Double-Distilled] - 2026-02-11

> (6af424762f)

    Few last tweaks to docs and chat visibility

## [0.0.11 - Bottled] - 2026-02-11

> (478c2416ad)

    Adding emoji and fallback fonts to the AppImage

## [0.0.10 - Bottled] - 2026-02-11

> (8b26de4b74)

    Apply patchelf for Python to point to OS glibc

## [0.0.9 - Bottled] - 2026-02-11

> (7a214f6d96)

    Bump the version

> (d8d396ef22)

    Fixed to Python paths in AppImage builder

> (303781ecb3)

    Refactor emoji picker
    
      Problem (message reactions implementation mostly):
      - Emoji picker dialog contained 147 lines of database logic
      - Direct XMPP calls and account/client dependencies in UI layer
      - Violated separation of concerns (ADR rule)
    
      Changes:
      - Created message_reactions.py: extracted reaction business logic
        to dedicated MessageReactions class (~400 lines)
      - Simplified emoji_picker_dialog.py: 595→351 lines, now pure UI
        that returns Optional[str], zero business logic
      - Updated MessageBarrel: 1160→1057 lines, delegates to reactions
      - Added XMPPAccount.send_reaction() / remove_reaction() public API
      - Updated context_menus.py: handles business logic after picker
      - Updated chat_view.py: handles emoji insertion after picker
      - Added "Remove Reaction" as separate context menu item

> (86b2ec0d11)

    Add TLS client certificate support (passwordless)
    
    Implement support for TLS client certificates using SASL EXTERNAL mechanism.
    Only unencrypted (passwordless) client certificates are supported.
    
    GUI Layer:
    - Add cert_validator.py utility for validating certificates
    - Update account_dialog.py:
      - Add certificate file browser and path input
      - Validate on file selection, test connection, and save
      - Show inline error label and test status icon
    
    Connection Layer:
    - Pass client_cert_path through to DrunkXMPP
    - Add connection_failed event handler for SSL/TLS errors
    - Filter errors: show dialog only for cert/SSL issues, not transient failures
    
    DrunkXMPP:
    - Add client_cert_path parameter to DrunkXMPP constructor
    - Set self.certfile and self.keyfile for slixmpp
    - Add cert_stdin_prevention.py slixmpp patch:
      - Prevents OpenSSL from prompting stdin for passwords
      - Emits connection_failed event on cert loading errors
      - Provides helpful error messages
    
    Database:
    - Bump schema version 12 → 13
    - Add client_cert_path and client_cert_password fields to account table
    - client_cert_password reserved for future use
    
    Error Handling:
    - Use non-blocking QMessageBox.show() to avoid breaking async loop
    - Smart filtering: SSL/cert errors show dialog, network errors logged only
    
    NOTES:
    - Works with both STARTTLS and direct TLS (xmpps)
    - Certificates must be in PEM format with unencrypted private key
    - Validation happens in GUI before connection attempt for immediate feedback

> (ec35ee9a9c)

    Updated contact search to pick MUC names properly

> (e292960a6a)

    Implement roster contact search with dropdown
    
    - Add search dropdown (QListWidget) under roster search box
    - Triggers after 2 characters, searches contact names and JIDs
    - Shows contact type emoji (👤 for contacts, 🏠 for MUCs)
    - Displays account JID for multi-account disambiguation
    - Enter key or mouse click opens chat with selected contact
    - Arrow keys navigate results, ESC clears search
    - Event filter prevents Enter/ESC from propagating to main window
    - Traverse widget hierarchy to find MainWindow (parent is QSplitter)
    - Dropdown width: 600px to accommodate long JIDs
    - No result limit (XMPP rosters are typically small)

## [0.0.8 - Bottled] - 2026-02-10

> (52e93b1d32)

    Bumped version

> (c36d688114)

    Fix AppImage forward compatibility and build
    
    - Exclude glibc from bundling to fix Debian 13+ compatibility
      (bundled glibc 2.35 caused GLIBC_PRIVATE symbol errors)
    - Fix pip shebang pollution using /usr/bin/python3 -m pip
    - Remove duplicate file copying from appimage.yml after_bundle
    - Update icon name to match app ID (com.siproxylin)
    - Use system glibc for forward compatibility (requires glibc 2.35+)

> (70d8e9e3bd)

    Fix blocking dialogs causing asyncio errors
    
    Replace all blocking .exec() and .warning() calls with non-blocking
    .show() pattern to prevent "Cannot enter into task while another task
    is being executed" errors during async XMPP operations.
    
    Pattern: dialog.exec() → signal-based handlers + dialog.show()
    
    All dialogs now use signal connections (accepted/rejected/buttonClicked)
    to handle user actions asynchronously.

> (6a7d4cd11c)

    Added MUC membership request (client-side only)
    
    Add ability for users to request membership in members-only rooms via
    XEP-0077 in-band registration (XEP-0045 §7.10).
    
    Changes:
    - Apply xep_0045_membership patch at startup
    - Register xep_0077 plugin in DrunkXMPP
    - Add request_room_membership() wrapper in DrunkXMPP client
    - Fix blocking dialogs in membership request flow (use .show())
    
    Flow: User clicks "Request Membership" → enters nickname/reason →
    request sent to server. Server may auto-approve, queue for admin
    approval, or reject.
    
    Note: Admin notification/approval UI not implemented as the protocol
    is not standardized across servers. Most servers either auto-approve
    or require manual affiliation management.

> (a90f1cc18d)

    Update README

> (f6c975b601)

    MUC details: unify Participants tab
    
    Replace separate "Users Online" and "All Members" tabs with a single
    unified Participants tab featuring checkbox filters (Online/Offline/
    Owners/Admins/Members/Banned/Others).
    
    Changes:
    - Single 5-column table: Nickname | JID | Status | Role | Affiliation
    - Merged data fetching: online participants + all affiliations
    - Context menu adapts: role changes (online only) vs affiliations (both)
    - Color-coded status and affiliations for better visibility
    
    Benefits:
    - Consistent UI/UX for all participant types
    - Single unified view reduces cognitive load

> (a71a89d20b)

    Phase 7#2: Subject editing + disco/config refresh
    
    - Add Edit Subject button (moderators + participants if allowed)
    - Parse allow_subject_change from disco#info (XEP-0128)
    - Track room subjects in real-time
    - Fetch fresh disco#info and config when dialog opens
    - Add Tier 2 config fields (allow_invites, allow_subject_change, whois)
    
    Fixes stale cache issue where config changes weren't reflected
    without restart.

> (0a3e28ea5c)

    Improve MUC join UX
    
    1. Nickname now optional in "Add Group" dialog
       - Uses JID localpart as default
       - User can override by typing custom nickname
    
    2. Added "Show" checkbox to password fields
       - "Add Group" dialog: toggle password visibility
       - Password prompt dialog: toggle password visibility
    
    3. Removed redundant MUC nickname field from Account Settings
       - Previously had separate "MUC Nickname" setting

> (08ab2892ca)

    Fix MUC password prompt not appearing
    
    When joining a password-protected MUC room without a password, the
    password dialog would only appear on the first attempt after app restart.
    Subsequent attempts (leave → join) would get stuck in "Joining..." state.
    
    Root cause: slixmpp's wildcard event handlers (muc::*::presence-error)
    stop working after first invocation - a slixmpp bug where wildcard MUC
    event handlers become inactive.
    
    Solution: Register per-room disposable error handlers dynamically in
    join_room() instead of relying on wildcards. Using disposable=True
    ensures handlers auto-remove after first use, preventing duplicates
    on password retry attempts.
    
    Additional fixes:
    - Move room to self.rooms dict BEFORE join attempt (fixes race condition)
    - Skip auto-rejoin when user explicitly leaves room
    - Remove context check in header.py so password dialogs work for rooms
      not currently displayed
    - Add muc_join_success signal to hide "Joining..." button on success
    
    Also includes: Room configuration UI (XEP-0045 owner settings) - allows
    owners to configure room name, description, password, members-only,
    moderation, max users, persistence, public listing, and message archiving.

> (134c03d6b1)

    Step 7 of main_window.py refactoring
    
    - Created MUCManager: MUC invites, joins, role changes, leaving
    - Extracted on_muc_invite_received, _execute_room_join_from_invite, on_muc_role_changed, leave_muc
    - Total: 62% reduction in size

> (d0d10158fe)

    Step 6 of main_window.py refactoring
    
    - Created RosterManager: roster updates, presence, typing, avatars
    - Size reduced by 54%
    - Modular architecture ready for future development

> (5b25a4d88a)

    Step 5 of main_window.py refactoring
    
    - Created DialogManager: account/contact/room/settings dialogs
    - Total: 49% reduction in size
    - 6 managers extracted: Call, Notification, Menu, Subscription, Message, Dialog

> (d034ffafb5)

    Step 4 of main_window.py refactoring
    
    - Created MessageManager: send/edit messages, file uploads, replies
    - Total: 47% size reduction

> (4c0d18e4b4)

    Step 3 of main_window.py refactoring
    
    - Created SubscriptionManager: subscription dialogs, contacts blocking (XEP-0191)
    - Total: 33% reduction in size

> (42959d0e4a)

    Step 2 of main_window.py refactoring
    
    - Created MenuManager (471 lines): menu bar, font/theme/roster, log viewers
    - Total: 3,335 → 2,424 lines (27% reduction)

> (7fe8c8cbfc)

    Step 1 of main_window.py refactoring (15% reduced)
    
      - Created managers/ directory for subsystems
      - CallManager (446 lines): call windows, dialogs, Go service
      - NotificationManager (212 lines): OS notifications

> (44d5cffa79)

    Voice request real-time throttling, feedback
    
    Polishes Phase 6 voice request feature with production-ready improvements:
    
    1. Real-time Role Change Monitoring (drunk_xmpp/client.py)
    2. Signal Propagation (siproxylin/core/brewery.py, connection.py)
    3. Auto-Update UI on Role Change (siproxylin/gui/main_window.py, chat_view.py)
       - Overlay now disappears instantly when voice granted
       - Overlay reappears with fresh state if voice revoked
    4. Request Throttling - 1 Hour Cooldown (siproxylin/core/barrels/muc.py)
    5. Timer Reset on Role Changes (siproxylin/core/barrels/muc.py, chat_view.py)
    6. User Feedback via Overlay Updates (siproxylin/gui/chat_view/taps/input.py)

> (11516aba69)

    Add voice request feature for moderated MUCs
    
    Implements XEP-0045 voice requests for visitors in moderated rooms.
    When a user is a visitor (no voice), they see an overlay with a
    clickable link to request participant role from moderators.
    
    Components:
    
    1. MUC Barrel API (siproxylin/core/barrels/muc.py)
       - Add request_voice() method wrapping xep_0045.request_voice()
       - Sends voice request to room moderators per XEP-0045 §8.6
    
    2. Visitor Overlay Widget (siproxylin/gui/chat_view/taps/input.py)
       - Add QLabel overlay anchored inside MessageInputField
       - Positioned via resizeEvent() for correct sizing
       - Clickable link emits voice_request_clicked signal
    
    3. Chat View Integration (siproxylin/gui/chat_view/chat_view.py)
       - Add _update_muc_input_state() to check visitor role
       - Connect voice_request_clicked to _handle_voice_request()
       - Call _update_muc_input_state() when loading MUC conversations
    
    4. DrunkXMPP Role Change Detection (drunk_xmpp/client.py)
       - Track role changes in _on_muc_presence handler
       - Add on_muc_role_changed_callback for future real-time updates
       - Log role transitions (visitor → participant, etc.)
    
    Known limitations (polish remaining for next session):
    - No request throttling (can spam requests)
    - No user feedback after sending request
    - Overlay doesn't auto-hide when promoted (requires chat switch)
    - No retry countdown/timer

## [0.0.7 - Bottled] - 2026-02-08

> (2898bcbe7a)

    Fixes to AppImage builder
    
    1. Added patchelf (was missing and binaries kept pointing to OS libs)
    2. Added a step to check for the tool chain
    3. Bumped the version

> (8e2add5b5c)

    Add Delete Chat feature and refactor chat list
    
    PRIVACY-FIRST DESIGN:
    - "Clear History" now uses hard DELETE (not hide=1) - messages truly deleted
    - "Delete Chat" deletes conversation + messages, removes from chat list
    - Both operations irreversible for privacy (no soft-delete lingering data)
    - Kept hide field for future selective hiding feature (e.g., sensitive content)
    
    CHAT LIST vs CONTACT LIST SEPARATION:
    - Left side = "Chat List" showing only conversations with messages
    - Roster contacts without messages not shown (use Contacts menu to start chat)
    - "Delete Chat" → deletes messages → conversation disappears (privacy fixed!)
    - New messages from unknown contacts appear immediately in chat list
    
    DELETE CHAT FEATURE:
    - Added "Delete Chat" button in Contact Details Dialog (red, destructive)
    - Added "Delete Chat" in contact list context menu
    - Deletes conversation row → CASCADE deletes all content_items
    - Immediately removes from UI and refreshes chat list
    
    MENU REORGANIZATION:
    - Created new "Contacts" top-level menu (File, Edit, View, Contacts, Help)
    - Moved from File: "Add Contact", "Add Group"
    - Moved from Edit: "Contacts" (now "Manage Contacts" with Ctrl+Shift+C)
    - Edit → Accounts submenu (cleaner, groups account management)
    
    OPEN CHAT FIX:
    - "Open Chat" from Contacts manager now shows conversation in chat list
    - Added load_roster() after load_conversation() to refresh UI
    - Fixes issue where newly opened chats didn't appear until restart

> (5fdacc8c64)

    MUC join error handle and membership request flow
    
    - Added error callback from DrunkXMPP to MucBarrel to GUI
    - Re-enables join button after error
    - Errors handled: registration-required, forbidden, not-authorized,
      conflict, service-unavailable, item-not-found, not-allowed, jid-malformed
    
    - Created slixmpp patch adding request_room_membership() to XEP-0045
    - Uses XEP-0077 (In-Band Registration) + XEP-0004 (Data Forms)
    - Detects registration-required error and shows membership dialog
    - Nickname editable (pre-filled from account.muc_nickname || nickname || JID)
    - Saves chosen nickname to bookmark after successful request
    
    Contact List Fix: Show Non-Roster Conversations
    - Updated query to show conversations even if JID not in roster
    - Fixes phantom unread count from server messages (e.g., conversations.im)
    - Enables "Delete Chat" feature (see DELETE-CHAT.md for next session)
    
    Test Tool Enhancements
      - Added /pep-nodes, /pep-get, /pep-delete commands
      - Added /pep-subscriptions, /pep-unsubscribe commands
      - Used for OMEMO phantom subscription investigation

> (6846de6372)

    Add MUC join error feedback messages
    
    Implements Phase 4 of MUC features roadmap - provides clear error messages
    when joining MUC rooms fails due to server rejection (members-only, banned,
    password incorrect, etc.).
    
    IMPLEMENTATION:
    
    1. DrunkXMPP Layer (drunk_xmpp/client.py)
       - Added on_muc_join_error_callback parameter to __init__
       - Implemented _on_muc_error() handler for presence_error events
       - Filters for MUC rooms only (checks self.rooms dict)
       - Calls callback with (room_jid, error_condition, error_text)
       - Uses direct await (not asyncio.create_task) following codebase pattern
    
    2. MUC Barrel Layer (siproxylin/core/barrels/muc.py)
       - Added on_muc_join_error() async method
       - Maps 8 XMPP error conditions to user-friendly messages:
         * registration-required → "Membership required to join this room"
         * forbidden → "You are banned from this room"
         * not-authorized → "Password incorrect or authorization failed"
         * conflict → "Nickname already in use"
         * service-unavailable → "Room does not exist or is unavailable"
         * item-not-found → "Room does not exist"
         * not-allowed → "You are not allowed to join this room"
         * jid-malformed → "Invalid room address"
       - Fallback: "Failed to join room" for unknown errors
       - Emits Qt signal directly (thread-safe, no QTimer wrapper needed)
       - Formats server details: "Server message: error-code: server text"
    
    3. Signal Wiring
       - Added muc_join_error signal to XMPPAccount (brewery.py)
       - Added muc_join_error signal to Account stub (barrels/account.py)
       - Registered callback in brewery signal dictionary
       - Wired callback in ConnectionBarrel
    
    4. GUI Layer (siproxylin/gui/chat_view/taps/header.py)
       - Added _on_muc_join_error() slot with proper signal tracking
       - Tracks _muc_error_account_id to disconnect from previous account
       - Stops MUC roster refresh timer on error (prevents 30s timeout)
       - Re-enables join button with "Join Room" text
       - Shows NON-BLOCKING error dialog (critical fix for async contexts)
       - Dialog format: friendly message + server details in parentheses
       - Hides join button properly for 1-1 chats (not MUC)

> (ba8a6b241c)

    MUC join flow and status, affiliation tracking
    
      PHASE 1: Code Deduplication
      - Created _perform_room_join() helper (eliminates 70 lines duplication)
      - Moved config/features fetch to on_muc_joined callback (fixes timing bug)
      - Added auto-refresh on status code 104 (room config changed event)
    
      PHASE 2: Affiliation Tracking
      - Track own affiliation/role per room (owner, admin, member, etc.)
      - Added MucBarrel API: get_own_affiliation(), is_room_owner()
      - Show affiliation in MUC details dialog with emoji icons
      - Disable refresh button for non-owners with helpful tooltip
    
      PHASE 3: Join Status Fix
      - Contact list shows proper joined/not-joined status
      - Chat header shows "Not joined" instead of "Joining..." forever
      - Added green "Join Room" button for bookmarked-but-not-joined rooms
      - Button hides automatically after successful join
    
      KNOWN ISSUE: Members-only room errors not shown to user yet
      - Server correctly rejects non-members with registration-required
      - Error logged but not propagated to UI
      - Next: Add error callback + membership request flow

> (b2e8b4b381)

    MUC room configdisplay with in-memory caching
    
    Implements XEP-0045 room configuration discovery (session-scoped in-memory storage).
    
    Changes:
    - Add get_room_config() to DrunkXMPP for querying owner config form
    - Add in-memory cache in MucBarrel (self.room_configs dict)
    - Auto-fetch config on room join (non-blocking, owner-only)
    - Update RoomInfo dataclass with 4 new fields (max_users, allow_invites,
      allow_subject_change, enable_logging)
    - Update MUC details dialog to display config values or "Unknown"
    - Add /room-config test command for manual testing
    - Fix migration race condition by moving db.initialize() to main.py startup
    
     MUC config auto-refreshed on each app launch when rooms are rejoined.

> (d1cf5ca798)

    Refactor MUC dialogs to use barrel API pattern
    
    - Add MUC service layer with data classes (RoomInfo, RoomSettings, Participant, Bookmark)
    - Add 6 new barrel methods: get_room_info, get_room_settings, update_room_settings,
      get_participants, get_bookmark, create_or_update_bookmark
    - Refactor muc_details_dialog.py: Remove all direct DB access (16 instances)
    - Refactor join_room_dialog.py: Remove all direct DB access
    - Result: Clean separation of concerns (GUI → Barrel → DB), improved testability

> (7d6f86011d)

    Fixed "FIRST-OMEMO-NOT-DELIVERED" bug

> (eafc5b024f)

    Updated .gitignore

> (a36e9a35a3)

    Fixing leftovers for nickname and muc_nickname

> (320d7994e8)

    XEP-0172 nickname publishing + fix ghost messages
    
    Part 1: XEP-0172 User Nickname Publishing
    - Database migration v11→v12: Rename account.alias to account.nickname
    - Add account.muc_nickname field for separate MUC room nickname
    - Implement nickname publishing via plugin['xep_0172'].publish_nick()
    - Auto-publish on connection and reconnection (XEP-0172 requirement)
    - Add publish_nickname() method to DrunkXMPP client
    - Add publish_own_nickname() wrapper in Brewery for GUI access
    - Update AccountDialog: "Alias" → "Nickname (XEP-0172)", add "MUC Nickname"
    - Nickname fallback: muc_nickname → nickname → JID localpart
    - Update all SQL queries across 16 files: alias → nickname
    - Fix roster_name undefined bug in chat_view for MUC conversations
    
    Part 2: Carbon OMEMO Filtering (Ghost Message Fix)
    - Fix ghost "[Failed to decrypt OMEMO message]" entries in conversations
    - Root cause: Carbons of messages encrypted for other recipients were stored
    - Solution: Parse OMEMO header and check device IDs BEFORE decryption
    - Extract recipient device IDs (rid) from <key> elements in OMEMO header
    - Compare with our own device ID from session_manager
    - Skip storing carbon if our device ID is not in recipient list
    - Preserves error visibility: Real OMEMO failures still logged/stored
    - Matches Conversations app behavior (no ghost messages)
    - Applied to both _on_carbon_sent() and _on_carbon_received()

## [0.0.6 - Bottled] - 2026-02-03

> (8a267e0c9f)

    Bump version

> (90967e6065)

    Add XEP-0172 nickname + refactor contact dialogs
    
    Part 1: XEP-0172 User Nickname Support
    - Register XEP-0172 plugin and handle user_nick_publish events
    - Implement 3-source display name priority: roster.name > nickname > JID
    - Store nicknames in-memory (server provides on startup via PEP)
    - Add unified _refresh_contact_display_name() for consistent UI updates
    - Update all UI components (contact list, chat view, header)
    
    Bug fixes:
    - Fix drunk-xmpp.client logger name (was silent due to config mismatch)
    - Add missing on_nickname_update_callback in ConnectionBarrel
    
    Known limitation: Nickname clearing only works after app restart.
    
    Part 2: Contact Details Dialog Refactoring
    - Replace OMEMOKeysDialog with unified ContactDetailsDialog
    - Add typing notifications checkbox to dialog Settings tab
    - Remove typing button from chat header (functionality still works)
    - Connect ContactDetailsDialog to all entry points:
      * Right-click contact → "View Details"
      * Chat header gear button
      * Edit → Contacts → Edit button
    - Delete omemo_keys_dialog.py (730 lines removed)

> (d969a26578)

    Removed the double "v" in the version on welcome page

## [0.0.5 - Bottled] - 2026-02-03

> (86dc7f9dec)

    Fix main.py to respect path mode from parameters

> (163f753708)

    Made call dialog non-modal to not block main window

> (dd462d989f)

    Fixed GH double tagging

> (f29e1711c6)

    Fixing the copying of version.sh into right place to provide build information

> (821d203e2d)

    Permit old codename, just show warning

> (59bc82dbd9)

    Update version number

> (ee1d94dee4)

    Fixed window title names

> (cbb4a7f561)

    Updated docs

> (238e81ad9c)

    Added DrunkXMPP test tool

## [0.0.4 - Bottled] - 2026-02-01

> (f203b730b2)

    Adding caching drunk_call_service Go binary

> (bd4dd66497)

    Update README.md

> (7cebcdd623)

    Update ARCHITECTURE.md

> (2d03adc24e)

    Tweaks to GitHub builder cache

> (f3ea6b2d47)

    Adding proper version handling for builds

## [0.0.3] - 2026-02-01

> (7efa84c008)

    GH release fixes

> (9c0ec2f08a)

    Bump go to 1.24.12 and fix repo parameter

> (000dc0155d)

    Replace deprecated create-release action with gh CLI

> (0421368673)

    Adding APPIMAGE_EXTRACT_AND_RUN=1 to avoide fuse requirement

> (91da8625b2)

    Fix build version variable

> (fd6d1f12a1)

    Replace deprecated create-release action with gh CLI

> (0a07d01cde)

    Fix GOPATH - use /usr/local/go-tools instead of $HOME/go

> (d3b7cfb3e9)

    Add python3-protobuf for gRPC Python bindings

> (d7957d80ab)

    Use Debian container's native Python and manually install Go

> (0bf0ec1541)

    Fix GitHub Actions for Debian container - remove sudo, fix Go cache

> (ed193efbd4)

    Updates to GitHub workflow

> (41ac51073f)

    Update README

> (44a6299602)

    v0.0.3 Bottled - First more or less stable release

