"""
Video viewer dialog with control panel.

On Linux (especially Wayland/Sway), VLC embedding in Qt widgets is problematic.
Instead, we show a control panel dialog that controls VLC's video playback.
VLC opens its own video window, and our dialog provides controls.
"""

import logging
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QMessageBox
)
from PySide6.QtCore import Qt, QTimer

logger = logging.getLogger('siproxylin.video_viewer')


class VideoViewerDialog(QDialog):
    """Control panel dialog for VLC video playback."""

    def __init__(self, video_path, parent=None):
        """
        Initialize video control panel.

        Args:
            video_path: Path to video file
            parent: Parent widget
        """
        super().__init__(parent)

        self.video_path = video_path
        self.vlc_instance = None
        self.media_player = None
        self.is_playing = False
        self.is_seeking = False
        self.position_timer = None  # Initialize to None in case VLC setup fails

        # Try to initialize VLC
        if not self._setup_vlc():
            return

        self._setup_ui()
        self._load_video()
        self._start_playback()

    def _setup_vlc(self):
        """Initialize VLC instance and media player."""
        try:
            import vlc

            # VLC args - minimal, no GUI (video window will still show)
            vlc_args = [
                '--no-video-title-show',  # Don't show filename overlay
            ]

            self.vlc_instance = vlc.Instance(vlc_args)
            if not self.vlc_instance:
                raise RuntimeError("Failed to create VLC instance")

            self.media_player = self.vlc_instance.media_player_new()
            if not self.media_player:
                raise RuntimeError("Failed to create media player")

            logger.info("VLC initialized successfully")
            return True

        except ImportError:
            logger.error("python-vlc not installed")
            QMessageBox.warning(
                self,
                "VLC Not Available",
                "python-vlc is not installed.\n\n"
                "Install with: pip install python-vlc"
            )
            self.reject()
            return False

        except Exception as e:
            logger.error(f"VLC initialization failed: {e}")
            QMessageBox.warning(
                self,
                "VLC Error",
                f"Failed to initialize VLC: {e}\n\n"
                "Try opening with external application instead."
            )
            self.reject()
            return False

    def _setup_ui(self):
        """Setup control panel UI - single line at bottom of screen."""
        filename = Path(self.video_path).name
        self.setWindowTitle(f"Video: {filename}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        # Single horizontal layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Play/Pause button with icon
        self.play_pause_btn = QPushButton("⏸")  # Pause icon (starts playing)
        self.play_pause_btn.setToolTip("Play/Pause (Space)")
        self.play_pause_btn.setFixedSize(40, 40)
        self.play_pause_btn.clicked.connect(self._play_pause)
        layout.addWidget(self.play_pause_btn)

        # Stop button with icon
        self.stop_btn = QPushButton("⏹")  # Stop icon
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.setFixedSize(40, 40)
        self.stop_btn.clicked.connect(self._stop)
        layout.addWidget(self.stop_btn)

        # Time label
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(100)
        layout.addWidget(self.time_label)

        # Position slider
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setValue(0)
        self.position_slider.setToolTip("Seek")
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self.position_slider, 1)

        # Volume label
        vol_label = QLabel("Vol:")
        layout.addWidget(vol_label)

        # Volume slider
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(80)
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.valueChanged.connect(self._set_volume)
        layout.addWidget(self.volume_slider)

        # Save button
        save_btn = QPushButton("Save")
        save_btn.setToolTip("Save As...")
        save_btn.clicked.connect(self._save_video)
        layout.addWidget(save_btn)

        # Open With button
        open_btn = QPushButton("Open")
        open_btn.setToolTip("Open With...")
        open_btn.clicked.connect(self._open_with_external)
        layout.addWidget(open_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close (Esc)")
        close_btn.setFixedSize(40, 40)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        # Position update timer
        self.position_timer = QTimer(self)
        self.position_timer.timeout.connect(self._update_position)
        self.position_timer.start(100)

        # Set initial volume
        self._set_volume(80)

    def _load_video(self):
        """Load video file."""
        if not self.media_player:
            return

        if not Path(self.video_path).exists():
            logger.error(f"Video file not found: {self.video_path}")
            QMessageBox.warning(
                self,
                "File Not Found",
                f"Video file not found:\n{self.video_path}"
            )
            return

        try:
            media = self.vlc_instance.media_new(str(self.video_path))
            if not media:
                raise RuntimeError("Failed to create media")

            self.media_player.set_media(media)
            media.release()

            logger.info(f"Loaded video: {self.video_path}")

        except Exception as e:
            logger.error(f"Failed to load video: {e}")
            QMessageBox.warning(
                self,
                "Video Load Error",
                f"Failed to load video:\n{self.video_path}\n\nError: {e}"
            )

    def _start_playback(self):
        """Start video playback automatically."""
        if self.media_player:
            self.media_player.play()
            self.is_playing = True
            self.play_pause_btn.setText("⏸")  # Pause icon
            logger.info("Started playback")

    def _play_pause(self):
        """Toggle playback."""
        if not self.media_player:
            return

        if self.is_playing:
            self.media_player.pause()
            self.is_playing = False
            self.play_pause_btn.setText("▶")  # Play icon
            logger.debug("Paused")
        else:
            self.media_player.play()
            self.is_playing = True
            self.play_pause_btn.setText("⏸")  # Pause icon
            logger.debug("Playing")

    def _stop(self):
        """Stop playback."""
        if self.media_player:
            self.media_player.stop()
            self.is_playing = False
            self.play_pause_btn.setText("▶")  # Play icon
            self.position_slider.setValue(0)
            self.time_label.setText("00:00 / 00:00")
            logger.debug("Stopped")

    def _update_position(self):
        """Update position slider and time display."""
        if not self.media_player or not self.is_playing or self.is_seeking:
            return

        # Update slider
        position = self.media_player.get_position()
        if position >= 0:
            slider_pos = int(position * 1000)
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(slider_pos)
            self.position_slider.blockSignals(False)

        # Update time
        current_time = self.media_player.get_time()
        total_time = self.media_player.get_length()

        if current_time >= 0 and total_time > 0:
            current_str = self._format_time(current_time)
            total_str = self._format_time(total_time)
            self.time_label.setText(f"{current_str} / {total_str}")

        # Check if ended
        import vlc
        if self.media_player.get_state() == vlc.State.Ended:
            self.is_playing = False
            self.play_pause_btn.setText("▶")  # Play icon
            self._stop()
            logger.debug("Playback ended")

    def _format_time(self, milliseconds):
        """Format time as MM:SS."""
        seconds = milliseconds // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _on_slider_pressed(self):
        """Slider pressed - pause updates."""
        self.is_seeking = True

    def _on_slider_released(self):
        """Slider released - seek to position."""
        self.is_seeking = False
        if self.media_player:
            position = self.position_slider.value() / 1000.0
            self.media_player.set_position(position)
            logger.debug(f"Seeked to {position:.2f}")

    def _set_volume(self, volume):
        """Set volume."""
        if self.media_player:
            self.media_player.audio_set_volume(volume)

    def _save_video(self):
        """Save video file."""
        from ...utils import save_file_as
        save_file_as(self.video_path, parent_widget=self)

    def _open_with_external(self):
        """Open with external app."""
        from ...utils import open_file_with_external_app
        open_file_with_external_app(self.video_path, mime_type="video/mp4", parent_widget=self)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key_Space:
            self._play_pause()
        elif event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """Clean up VLC resources gracefully."""
        logger.info("Closing video viewer")

        # Stop timer first
        if self.position_timer:
            self.position_timer.stop()

        # Stop playback gracefully before releasing
        if self.media_player:
            try:
                # Stop playback first
                if self.is_playing:
                    self.media_player.stop()
                    # Give VLC time to stop cleanly
                    import time
                    time.sleep(0.1)

                # Now release resources
                self.media_player.release()
                logger.debug("Media player released")
            except Exception as e:
                logger.error(f"Error releasing media player: {e}")

        # Release VLC instance
        if self.vlc_instance:
            try:
                self.vlc_instance.release()
                logger.debug("VLC instance released")
            except Exception as e:
                logger.error(f"Error releasing VLC instance: {e}")

        super().closeEvent(event)
