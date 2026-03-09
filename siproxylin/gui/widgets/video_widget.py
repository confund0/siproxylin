"""
VLC Video Widget for displaying incoming video streams.

Supports WebM (VP8/VP9/H.264) video over UDP.
Uses simple udp://@:port URL for native VLC playback.
Cross-platform support for Linux, Windows, and macOS.
"""

import sys
import logging
from typing import Optional

try:
    import vlc
    VLC_AVAILABLE = True
except ImportError:
    VLC_AVAILABLE = False

from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt


class VLCVideoWidget(QWidget):
    """
    Video widget using VLC for WebM stream playback.

    Receives WebM video stream from GStreamer over UDP.
    Supports VP8, VP9, and H.264 codecs in WebM container.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize VLC video widget.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)
        self.vlc_instance: Optional['vlc.Instance'] = None
        self.player: Optional['vlc.MediaPlayer'] = None
        self.current_media: Optional['vlc.Media'] = None

        self._setup_ui()

    def _setup_ui(self):
        """Setup the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not VLC_AVAILABLE:
            # Fallback: Show error message if VLC not available
            error_label = QLabel("VLC library not available.\nInstall python-vlc to enable video.")
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: red; background-color: black;")
            layout.addWidget(error_label)
            self.logger.error("VLC library (python-vlc) not installed")
            return

        # Create VLC instance with optimized settings for low-latency streaming
        vlc_args = [
            "--network-caching=150",  # Low latency (150ms buffer)
            "--clock-jitter=0",       # Disable jitter compensation
            "--clock-synchro=0",      # Disable clock synchronization
            "--verbose=2",            # Enable verbose logging
        ]

        try:
            self.vlc_instance = vlc.Instance(vlc_args)
            self.player = self.vlc_instance.media_player_new()
        except Exception as e:
            self.logger.error(f"Failed to initialize VLC: {e}")
            error_label = QLabel(f"Failed to initialize VLC:\n{str(e)}")
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: red; background-color: black;")
            layout.addWidget(error_label)
            return

        # Create video frame for embedding VLC player
        self.video_frame = QFrame(self)
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setMinimumSize(320, 240)

        layout.addWidget(self.video_frame)

        # Platform-specific window embedding
        self._setup_platform_embedding()

    def _setup_platform_embedding(self):
        """Setup platform-specific VLC window embedding."""
        if not self.player or not self.video_frame:
            return

        try:
            if sys.platform.startswith('linux'):
                # Linux (X11/Wayland)
                self.player.set_xwindow(int(self.video_frame.winId()))
                self.logger.debug("VLC embedded using X11 window")
            elif sys.platform == 'win32':
                # Windows
                self.player.set_hwnd(int(self.video_frame.winId()))
                self.logger.debug("VLC embedded using Windows HWND")
            elif sys.platform == 'darwin':
                # macOS
                self.player.set_nsobject(int(self.video_frame.winId()))
                self.logger.debug("VLC embedded using macOS NSObject")
            else:
                self.logger.warning(f"Unsupported platform: {sys.platform}")
        except Exception as e:
            self.logger.error(f"Failed to embed VLC window: {e}")

    def play_stream(self, sdp_content: str, port: int):
        """
        Start playing WebM video stream over UDP.

        Args:
            sdp_content: Unused (kept for compatibility)
            port: UDP port for the stream
        """
        if not VLC_AVAILABLE or not self.player:
            self.logger.error("Cannot play stream: VLC not available")
            return

        try:
            self.logger.info(f"Starting WebM video playback on port {port}")

            # Use simple UDP URL - VLC natively plays WebM over UDP
            # udp://@:port listens on all interfaces on the specified port
            udp_url = f"udp://@:{port}"

            self.logger.info(f"Playing UDP stream: {udp_url}")

            # Create media from UDP URL
            self.current_media = self.vlc_instance.media_new_location(udp_url)

            # Low latency options for live streaming
            self.current_media.add_option(":network-caching=50")   # CRITICAL: Only 50ms buffer for live stream
            self.current_media.add_option(":clock-jitter=0")       # Disable jitter compensation
            self.current_media.add_option(":clock-synchro=0")      # Disable clock sync
            self.current_media.add_option(":live-caching=50")      # Force live caching to 50ms

            # Set media to player
            self.player.set_media(self.current_media)

            # Start playback
            self.player.play()

            self.logger.info(f"VLC playback started for WebM stream on port {port}")

        except Exception as e:
            self.logger.error(f"Failed to start video playback: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def stop(self):
        """Stop video playback and release resources."""
        if not self.player:
            return

        try:
            self.logger.debug("Stopping video playback")
            self.player.stop()

            # Release media
            if self.current_media:
                self.current_media.release()
                self.current_media = None

        except Exception as e:
            self.logger.error(f"Error stopping video playback: {e}")

    def is_playing(self) -> bool:
        """
        Check if video is currently playing.

        Returns:
            True if playing, False otherwise
        """
        if not self.player:
            return False

        try:
            return self.player.is_playing()
        except Exception:
            return False

    def cleanup(self):
        """Cleanup VLC resources."""
        self.stop()

        if self.player:
            try:
                self.player.release()
            except Exception as e:
                self.logger.error(f"Error releasing VLC player: {e}")
            self.player = None

        if self.vlc_instance:
            try:
                self.vlc_instance.release()
            except Exception as e:
                self.logger.error(f"Error releasing VLC instance: {e}")
            self.vlc_instance = None

    def closeEvent(self, event):
        """Handle widget close event."""
        self.cleanup()
        super().closeEvent(event)
