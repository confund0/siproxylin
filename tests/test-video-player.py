#!/usr/bin/python3
"""
Test Video Player for Video Call Development

This player receives RTP/VP8 video stream over TCP from the Go service
and displays it in a window using GStreamer.

Usage:
    python tests/test-video-player.py [host] [port]

Default: tcp://localhost:5004

Purpose:
    - Test video encoding/streaming pipeline independently of Jingle
    - Quick validation that Go service video works
    - Development/debugging tool
    - Will be adapted for GUI video widget later

Requirements:
    - GStreamer 1.0+ with VP8 decoder
    - PyGObject (gi)
    - python-gst-1.0

Test with VLC alternative:
    vlc tcp://localhost:5004
"""

import sys
import signal
import gi

gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')

from gi.repository import Gst, Gtk, GLib

# Initialize GStreamer
Gst.init(None)


class VideoPlayerWindow(Gtk.Window):
    """Simple video player window using GStreamer."""

    def __init__(self, host="localhost", port=5004):
        super().__init__(title=f"Test Video Player - {host}:{port}")

        self.host = host
        self.port = port

        # Window setup
        self.set_default_size(640, 480)
        self.connect("destroy", self._on_destroy)

        # Create video widget
        self.video_widget = Gtk.DrawingArea()
        self.video_widget.set_size_request(640, 480)
        self.add(self.video_widget)

        # Status label (overlay)
        self.status_label = Gtk.Label()
        self.status_label.set_markup("<b>Connecting...</b>")

        # Create GStreamer pipeline
        self._create_pipeline()

        # Show window
        self.show_all()

        print(f"[VideoPlayer] Window created, connecting to {host}:{port}")

    def _create_pipeline(self):
        """Create GStreamer pipeline for RTP/VP8 over UDP."""

        # Pipeline: UDP → RTP depay → VP8 decode → video sink
        pipeline_str = (
            f"udpsrc port={self.port} "
            "caps=\"application/x-rtp,media=video,clock-rate=90000,encoding-name=VP8\" "
            "! queue "
            "! rtpvp8depay "
            "! queue "
            "! vp8dec "
            "! videoconvert "
            "! autovideosink sync=false"
        )

        print(f"[VideoPlayer] Pipeline: {pipeline_str}")

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
        except Exception as e:
            print(f"[VideoPlayer] ERROR: Failed to create pipeline: {e}")
            self._show_error(f"Pipeline creation failed:\n{e}")
            return

        # Get video sink and set window handle
        try:
            video_sink = self.pipeline.get_by_name("autovideosink0")
            if video_sink:
                # Enable sync to avoid frame drops
                video_sink.set_property("sync", True)
        except Exception as e:
            print(f"[VideoPlayer] Warning: Could not configure video sink: {e}")

        # Connect to bus for messages
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_gst_message)

        # Set window handle for video overlay
        bus.enable_sync_message_emission()
        bus.connect("sync-message::element", self._on_sync_message)

        # Start pipeline
        print("[VideoPlayer] Starting pipeline...")
        ret = self.pipeline.set_state(Gst.State.PLAYING)

        if ret == Gst.StateChangeReturn.FAILURE:
            print("[VideoPlayer] ERROR: Failed to start pipeline")
            self._show_error("Failed to start pipeline\nIs Go service running?")

    def _on_sync_message(self, bus, message):
        """Handle video overlay sync messages."""
        if message.get_structure() is None:
            return

        message_name = message.get_structure().get_name()
        if message_name == "prepare-window-handle":
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)
            # Try to embed video in widget (not critical if it fails)
            try:
                if hasattr(imagesink, 'set_window_handle'):
                    imagesink.set_window_handle(self.video_widget.get_property('window').get_xid())
                elif hasattr(imagesink, 'set_xwindow_id'):
                    imagesink.set_xwindow_id(self.video_widget.get_property('window').get_xid())
            except Exception as e:
                # Video will open in separate window (acceptable for testing)
                pass

    def _on_gst_message(self, bus, message):
        """Handle GStreamer bus messages."""
        t = message.type

        if t == Gst.MessageType.EOS:
            print("[VideoPlayer] End-of-stream")
            self._show_error("Stream ended")
            self.pipeline.set_state(Gst.State.NULL)

        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[VideoPlayer] ERROR: {err.message}")
            print(f"[VideoPlayer] Debug: {debug}")
            self._show_error(f"Stream error:\n{err.message}")
            self.pipeline.set_state(Gst.State.NULL)

        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"[VideoPlayer] WARNING: {warn.message}")

        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                print(f"[VideoPlayer] State: {old_state.value_nick} → {new_state.value_nick}")

                if new_state == Gst.State.PLAYING:
                    print("[VideoPlayer] ✅ Playing video stream")
                    self.status_label.set_markup("<b>Connected ✅</b>")

        elif t == Gst.MessageType.STREAM_START:
            print("[VideoPlayer] Stream started")

        elif t == Gst.MessageType.ASYNC_DONE:
            print("[VideoPlayer] Async done")

    def _show_error(self, message):
        """Show error message in window."""
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Video Player Error"
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def _on_destroy(self, widget):
        """Clean up on window close."""
        print("[VideoPlayer] Closing...")
        if hasattr(self, 'pipeline'):
            self.pipeline.set_state(Gst.State.NULL)
        Gtk.main_quit()


def main():
    """Main entry point."""

    # Parse command line arguments
    host = "localhost"
    port = 5004

    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print(f"ERROR: Invalid port: {sys.argv[2]}")
            sys.exit(1)

    print("=" * 60)
    print("Test Video Player for Video Call Development")
    print("=" * 60)
    print(f"Connecting to: {host}:{port}")
    print()
    print("Expected Go service pipeline:")
    print("  videotestsrc → vp8enc → rtpvp8pay → tcpserversink port=5004")
    print()
    print("Press Ctrl+C to quit")
    print("=" * 60)
    print()

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Create player window
    try:
        player = VideoPlayerWindow(host, port)
    except Exception as e:
        print(f"ERROR: Failed to create player: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Run GTK main loop
    try:
        Gtk.main()
    except KeyboardInterrupt:
        print("\n[VideoPlayer] Interrupted by user")

    print("[VideoPlayer] Exiting")


if __name__ == "__main__":
    main()
