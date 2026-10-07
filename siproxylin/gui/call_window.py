"""
Call Window

Separate window showing active call with controls and tech details.
"""

import asyncio
import logging
import platform
import time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSizePolicy, QGridLayout
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QFont, QColor, QPalette

from .widgets.video_view import VideoView, SelfView
from ..db.database import get_db


logger = logging.getLogger('siproxylin.call_window')

# Mute button of the audio window: colors come from the app theme (generic
# QPushButton rule), checked state from the palette, never red.
TOGGLE_STYLE = """
    QPushButton {
        font-size: 20px;
        padding: %s;
        border-radius: %dpx;
    }
    QPushButton:checked {
        background-color: palette(highlight);
        color: palette(highlighted-text);
    }
"""

# Round buttons of the control bar. Colors come from the bar background:
# darker than the bar, brighter on hover. Checked state from the palette.
BAR_BUTTON_STYLE = """
    QPushButton {
        background-color: %(normal)s;
        %(color)s
        font-size: %(font)dpx;
        padding: 0px;
        border: 1px solid %(normal)s;
        border-radius: %(radius)dpx;
    }
    QPushButton:hover {
        background-color: %(hover)s;
    }
    QPushButton:pressed {
        background-color: %(pressed)s;
    }
    QPushButton:checked {
        background-color: palette(highlight);
        color: palette(highlighted-text);
    }
    QPushButton:disabled {
        background-color: %(bar)s;
    }
"""

# Change of the HSV value against the bar background
BAR_BUTTON_DARKER = 35
BAR_BUTTON_BRIGHTER = 40

# Hang up: gray button with a red cross
HANGUP_STYLE = """
    QPushButton {
        background-color: #c8c8c8;
        color: #d32f2f;
        font-size: %dpx;
        font-weight: bold;
        padding: %s;
        border: none;
        border-radius: %dpx;
    }
    QPushButton:hover {
        background-color: #b0b0b0;
    }
    QPushButton:pressed {
        background-color: #9e9e9e;
    }
    QPushButton:disabled {
        background-color: #e0e0e0;
        color: #9e9e9e;
    }
"""

TOGGLE_SIZE = 52
HANGUP_SIZE = 64


class CallWindow(QWidget):
    """
    Separate window showing active call.

    Features:
    - Peer JID display
    - Call status (Connecting, Connected, Disconnected)
    - Call duration timer
    - Hang Up button
    - Mute button (future)
    - Tech details (collapsible): connection state, packets, bytes
    """

    # Signal to request hangup (connected to account.hangup_call)
    hangup_requested = Signal()

    def __init__(self, parent, account_id: int, session_id: str,
                 peer_jid: str, media_types: list, direction: str, account=None,
                 video_reader=None):
        """
        Initialize call window.

        Args:
            parent: Parent widget (MainWindow)
            account_id: Account making/receiving call
            session_id: Jingle session ID
            peer_jid: JID of peer
            media_types: List of media types (['audio'] or ['audio', 'video'])
            direction: 'outgoing' or 'incoming'
            account: Account instance (for accessing video port)
            video_reader: Shared memory frame reader of the call service
                (Linux and Windows, else None)
        """
        # Platform-specific parent handling:
        # - Windows: No parent (prevents hiding when main window minimizes)
        # - Linux/macOS: Use parent (ensures floating in Sway/i3 tiling WMs)
        # - Video window: No parent (a parent makes it transient, so Sway
        #   floats it; without one a tiling WM tiles it)
        if platform.system() == 'Windows':
            super().__init__(None)
            logger.debug("CallWindow: No parent (Windows - independent window)")
        elif 'video' in media_types and video_reader is not None:
            super().__init__(None)
            logger.debug("CallWindow: No parent (video window - top-level window)")
        else:
            super().__init__(parent)
            logger.debug("CallWindow: Using parent (Linux/macOS - floating window)")

        # Store parent reference for callbacks
        self._parent_window = parent

        self.account_id = account_id
        self.session_id = session_id
        self.peer_jid = peer_jid
        self.media_types = media_types
        self.direction = direction
        self.account = account

        # Call timing
        self.call_start_time: Optional[float] = None
        self.call_connected = False
        # True when the call has ended (closing the window then does not hang up)
        self._call_ended = False

        # Video calls with a reader: video in this window, controls docked under it
        self.video_view = None
        self.self_view = None
        self.has_video_view = 'video' in media_types and video_reader is not None
        self._video_reader = video_reader if self.has_video_view else None

        # Slim mode for video calls (compact control bar)
        self.is_slim_mode = 'video' in media_types and not self.has_video_view
        self._expanded_size = None
        self._is_expanded = False

        # Setup window
        self.setWindowTitle(f"Call - {peer_jid}")

        if self.has_video_view:
            # Video window: normal decorated window, not on top
            self.setWindowFlags(Qt.Window)
            self.setMinimumSize(320, 300)
            self.resize(800, 600)
        elif self.is_slim_mode:
            # Slim mode: Compact horizontal bar, always-on-top
            self.setWindowFlags(
                Qt.Window |              # Independent window
                Qt.WindowStaysOnTopHint  # Always on top of video
            )
            self.setMinimumSize(400, 80)   # Compact bar minimum
            self.setMaximumSize(600, 150)  # Allow some expansion
            self.resize(450, 100)          # Initial slim size
        else:
            # Normal mode (audio calls): Full window with frame, always on top
            self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
            self.setMinimumSize(500, 300)
            self.setMaximumSize(800, 900)
            self.resize(550, 350)

        self._setup_ui()
        # Tooltips in the font size of the window (the theme sets a smaller
        # fixed size for all tooltips)
        tooltip_size = self.font().pointSizeF()
        if tooltip_size > 0:
            self.setStyleSheet(f"QToolTip {{ font-size: {tooltip_size:g}pt; }}")
        if self.has_video_view:
            # Not narrower than the control bar, so its center stays centered
            self.setMinimumWidth(max(self.minimumWidth(), self.layout().minimumSize().width()))
        self._start_timers()

        logger.info(f"Call window opened: {peer_jid} ({direction}, {media_types})")

    def _setup_ui(self):
        """Setup call window UI - always create both slim and full containers."""
        # Always create both UI modes (slim and full)
        # Video calls start in slim mode, audio calls start in full mode
        self._setup_dual_mode_ui()

    def _setup_dual_mode_ui(self):
        """Setup both slim and full UI modes (all calls can toggle)."""
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(10, 10, 10, 10)

        # Video on top, fills the free space
        if self.has_video_view:
            self.video_view = VideoView(self._video_reader, self.peer_jid)
            layout.addWidget(self.video_view, 1)
            # Own camera in a corner over the video
            self.self_view = SelfView(self._video_reader, self.video_view, settings=get_db())

        # Window background of the theme, for the bar button colors
        self.ensurePolished()
        self._bar_background = self.palette().color(QPalette.Window)

        # Control bar: status and time left, Mute | Hang up | Camera in the
        # center, self-view and details right. The two side columns have the
        # same stretch, so the center stays in the middle of the bar.
        controls_layout = QGridLayout()
        controls_layout.setHorizontalSpacing(10)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setColumnStretch(0, 1)
        controls_layout.setColumnStretch(2, 1)

        left_layout = QHBoxLayout()
        left_layout.setSpacing(10)
        left_layout.setContentsMargins(0, 0, 0, 0)
        center_layout = QHBoxLayout()
        center_layout.setSpacing(16)
        right_layout = QHBoxLayout()
        right_layout.setSpacing(10)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Status label (status text with a status emoji). It can shrink, so a
        # long text does not push the center buttons.
        self.status_label = QLabel("Connecting...")
        self.status_label.setStyleSheet("color: gray;")
        self.status_label.setMinimumWidth(1)
        left_layout.addWidget(self.status_label)

        # Duration label
        self.duration_label = QLabel("--:--")
        self.duration_label.setStyleSheet("font-weight: bold; font-size: 18px;")
        left_layout.addWidget(self.duration_label)

        # Error label (mic, speaker or camera failed) between the time and the
        # center buttons, hidden until an error comes. It can shrink and wraps.
        self._error_names = []    # short error names shown, in order
        self._error_details = []  # full error texts for the tooltip
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #e05050;")
        self.error_label.setWordWrap(True)
        self.error_label.setMinimumWidth(1)
        self.error_label.setVisible(False)
        left_layout.addWidget(self.error_label, 1)
        left_layout.addStretch()

        # Mute button
        self.mute_button = QPushButton("🎤")
        self.mute_button.setCheckable(True)
        self.mute_button.setToolTip("Mute/unmute microphone")
        self.mute_button.setFixedSize(QSize(TOGGLE_SIZE, TOGGLE_SIZE))
        self.mute_button.setStyleSheet(self._bar_button_style(TOGGLE_SIZE, 22))
        self.mute_button.clicked.connect(self._on_mute_toggled)
        center_layout.addWidget(self.mute_button)

        # Hangup button
        self.hangup_button = QPushButton("✕")
        self.hangup_button.setToolTip("Hang up")
        self.hangup_button.setFixedSize(QSize(HANGUP_SIZE, HANGUP_SIZE))
        self.hangup_button.setStyleSheet(
            self._bar_button_style(HANGUP_SIZE, 30, "color: #d32f2f; font-weight: bold;"))
        self.hangup_button.clicked.connect(self._on_hangup)
        center_layout.addWidget(self.hangup_button)

        # Camera on/off button (not implemented yet)
        self.camera_button = QPushButton("📷")
        self.camera_button.setCheckable(True)
        self.camera_button.setEnabled(False)
        self.camera_button.setToolTip("Camera on/off (not available yet)")
        self.camera_button.setFixedSize(QSize(TOGGLE_SIZE, TOGGLE_SIZE))
        self.camera_button.setStyleSheet(self._bar_button_style(TOGGLE_SIZE, 22))
        center_layout.addWidget(self.camera_button)

        right_layout.addStretch()

        # Self-view show/hide button (video window only)
        if self.self_view:
            self.self_view_button = QPushButton("🪞")
            self.self_view_button.setCheckable(True)
            self.self_view_button.setChecked(not self.self_view.is_user_hidden())
            self.self_view_button.setFixedSize(QSize(TOGGLE_SIZE, TOGGLE_SIZE))
            self.self_view_button.setStyleSheet(self._bar_button_style(TOGGLE_SIZE, 22))
            self._update_self_view_tooltip()
            self.self_view_button.toggled.connect(self._toggle_self_view)
            right_layout.addWidget(self.self_view_button)

        # Expand/details toggle button
        self.expand_button = QPushButton("⤢")
        self.expand_button.setCheckable(True)
        self.expand_button.setToolTip("Show full window")
        self.expand_button.setFixedSize(QSize(TOGGLE_SIZE, TOGGLE_SIZE))
        self.expand_button.setStyleSheet(self._bar_button_style(TOGGLE_SIZE, 22))
        if self.has_video_view:
            # Video window: the button shows the technical details under the video
            self.expand_button.setText("{}")
            self.expand_button.setToolTip("Show technical details")
            self.expand_button.clicked.connect(self._toggle_video_details)
        else:
            self.expand_button.clicked.connect(self._toggle_expanded_mode)
        right_layout.addWidget(self.expand_button)

        # The side widgets ignore their size hints and get the same minimum
        # width, so both side columns are always the same width.
        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        self.duration_label.ensurePolished()  # font size of the stylesheet
        side_width = max(left_layout.minimumSize().width(),
                         right_layout.minimumSize().width())
        for side in (left_widget, right_widget):
            side.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            side.setMinimumWidth(side_width)
        controls_layout.addWidget(left_widget, 0, 0)
        controls_layout.addLayout(center_layout, 0, 1)
        controls_layout.addWidget(right_widget, 0, 2)

        # Save slim controls as container for toggling
        slim_controls_widget = QWidget()
        slim_controls_widget.setLayout(controls_layout)
        self.slim_container = slim_controls_widget
        layout.addWidget(self.slim_container)

        # Create full normal UI container
        self.full_container = QWidget()
        self._create_full_ui_in_container(self.full_container)
        layout.addWidget(self.full_container)

        # Set initial visibility based on call type
        if self.has_video_view:
            # Video window: only the control bar under the video. The technical
            # details move out of the full container, between video and controls,
            # hidden until the button is checked. No window size change.
            self.slim_container.setVisible(True)
            self.full_container.setVisible(False)
            self.full_container.layout().removeWidget(self.tech_group)
            self.tech_group.setCheckable(False)
            self.tech_content.setVisible(True)
            self.tech_group.setVisible(False)
            layout.insertWidget(layout.indexOf(self.slim_container), self.tech_group)
        elif self.is_slim_mode:
            # Video calls: start slim
            self.slim_container.setVisible(True)
            self.full_container.setVisible(False)
        else:
            # Audio calls: start full
            self.slim_container.setVisible(False)
            self.full_container.setVisible(True)

        self.setLayout(layout)

    def _bar_button_style(self, size: int, font_px: int, color: str = '') -> str:
        """Style of a round control bar button, colors from the bar background."""
        bg = self._bar_background
        hue, sat, value = bg.hsvHue(), bg.hsvSaturation(), bg.value()
        # A bar that is almost black gets lighter buttons, else they cannot be seen.
        # On a white bar hover cannot be brighter: it is then only brighter than the button.
        if value >= BAR_BUTTON_DARKER:
            normal_value = value - BAR_BUTTON_DARKER
        else:
            normal_value = value + BAR_BUTTON_DARKER
        normal = QColor.fromHsv(hue, sat, normal_value)
        hover = QColor.fromHsv(hue, sat, min(255, max(value, normal_value) + BAR_BUTTON_BRIGHTER))
        return BAR_BUTTON_STYLE % {
            'bar': bg.name(), 'normal': normal.name(), 'hover': hover.name(),
            'pressed': normal.darker(120).name(), 'color': color,
            'font': font_px, 'radius': size // 2,
        }

    def _create_full_ui_in_container(self, container):
        """Create full normal UI inside a container widget (for Details expansion)."""
        full_layout = QVBoxLayout()
        full_layout.setSpacing(15)

        # Header: Media icon + Peer JID
        header_layout = QHBoxLayout()
        icon_label = QLabel("📹")
        icon_font = QFont()
        icon_font.setPointSize(32)
        icon_label.setFont(icon_font)
        header_layout.addWidget(icon_label)

        peer_layout = QVBoxLayout()
        peer_layout.setSpacing(5)
        peer_label = QLabel(self.peer_jid)
        peer_font = QFont()
        peer_font.setPointSize(14)
        peer_font.setBold(True)
        peer_label.setFont(peer_font)
        peer_layout.addWidget(peer_label)

        direction_label = QLabel(f"{self.direction.title()} Video Call")
        direction_label.setStyleSheet("color: gray;")
        peer_layout.addWidget(direction_label)

        header_layout.addLayout(peer_layout)
        header_layout.addStretch()
        full_layout.addLayout(header_layout)

        # Status Display
        self.full_status_label = QLabel("Status: Connecting...")
        status_font = QFont()
        status_font.setPointSize(12)
        self.full_status_label.setFont(status_font)
        full_layout.addWidget(self.full_status_label)

        # Error text in the full mode (the control bar with error_label is hidden there)
        self.full_error_label = QLabel("")
        self.full_error_label.setStyleSheet("color: #e05050;")
        self.full_error_label.setWordWrap(True)
        self.full_error_label.setVisible(False)
        full_layout.addWidget(self.full_error_label)

        # Call duration
        self.full_duration_label = QLabel("Duration: --:--:--")
        duration_font = QFont()
        duration_font.setPointSize(14)
        duration_font.setBold(True)
        self.full_duration_label.setFont(duration_font)
        full_layout.addWidget(self.full_duration_label)

        full_layout.addStretch()

        # Controls: Hang Up, Mute
        controls_layout_full = QHBoxLayout()
        controls_layout_full.setSpacing(15)

        # Hang Up button
        hangup_full = QPushButton("✕ Hang Up")
        hangup_full.setStyleSheet(HANGUP_STYLE % (22, '15px 30px', 8))
        hangup_full.clicked.connect(self._on_hangup)
        controls_layout_full.addWidget(hangup_full)

        # Mute button
        mute_full = QPushButton("🎤 Mute")
        mute_full.setCheckable(True)
        mute_full.setToolTip("Mute/unmute microphone")
        mute_full.setStyleSheet(TOGGLE_STYLE % ('8px', 8))
        mute_full.clicked.connect(self._on_mute_toggled)
        controls_layout_full.addWidget(mute_full)
        self.mute_button_full = mute_full  # Save reference

        full_layout.addLayout(controls_layout_full)

        # Tech Details (collapsible)
        self.tech_group = QGroupBox("Technical Details")
        self.tech_group.setCheckable(True)
        self.tech_group.setChecked(False)

        self.tech_content = QWidget()
        tech_layout = QFormLayout()
        tech_layout.setSpacing(8)
        tech_layout.setContentsMargins(0, 0, 0, 0)

        # Create tech labels
        self.connection_state_label = QLabel("Unknown")
        self.ice_state_label = QLabel("Unknown")
        self.ice_gathering_label = QLabel("Unknown")
        self.bandwidth_label = QLabel("0 Kbps")
        self.bytes_sent_label = QLabel("0 B")
        self.bytes_received_label = QLabel("0 B")
        self.our_ips_label = QLabel("--")
        self.our_ips_label.setWordWrap(True)
        self.peer_ips_label = QLabel("--")
        self.peer_ips_label.setWordWrap(True)
        self.connection_type_label = QLabel("--")
        self.connection_type_label.setWordWrap(True)

        tech_layout.addRow("Connection State:", self.connection_state_label)
        tech_layout.addRow("ICE State:", self.ice_state_label)
        tech_layout.addRow("ICE Gathering:", self.ice_gathering_label)
        tech_layout.addRow("Bandwidth:", self.bandwidth_label)
        tech_layout.addRow("Bytes Sent:", self.bytes_sent_label)
        tech_layout.addRow("Bytes Received:", self.bytes_received_label)
        tech_layout.addRow("Our IPs:", self.our_ips_label)
        tech_layout.addRow("Peer IPs:", self.peer_ips_label)
        tech_layout.addRow("Connected via:", self.connection_type_label)

        self.tech_content.setLayout(tech_layout)
        self.tech_content.setVisible(False)

        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(10, 10, 10, 10)
        group_layout.addWidget(self.tech_content)
        self.tech_group.setLayout(group_layout)
        self.tech_group.toggled.connect(self._on_tech_details_toggled)

        full_layout.addWidget(self.tech_group)

        # Collapse button (video calls without a video view only; audio calls
        # always use the full window)
        if self.is_slim_mode:
            collapse_btn = QPushButton("↓ Slim Mode")
            collapse_btn.clicked.connect(self._toggle_expanded_mode)
            full_layout.addWidget(collapse_btn)

        container.setLayout(full_layout)

    def _start_timers(self):
        """Start timers for duration and stats updates."""
        # Duration timer (update every second)
        self.duration_timer = QTimer(self)
        self.duration_timer.timeout.connect(self._update_duration)
        self.duration_timer.start(1000)

        # Stats timer (update every 2 seconds)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._request_stats_update)
        self.stats_timer.start(2000)

    def _update_duration(self):
        """Update call duration display."""
        try:
            if not self.call_start_time:
                # Update slim mode duration
                if hasattr(self, 'duration_label') and self.duration_label:
                    self.duration_label.setText("--:--")
                # Update full mode duration
                if hasattr(self, 'full_duration_label') and self.full_duration_label:
                    self.full_duration_label.setText("Duration: --:--:--")
                return

            elapsed = int(time.time() - self.call_start_time)
            hours = elapsed // 3600
            minutes = (elapsed % 3600) // 60
            seconds = elapsed % 60

            # Update slim mode duration (compact format)
            if hasattr(self, 'duration_label') and self.duration_label:
                self.duration_label.setText(f"{minutes:02d}:{seconds:02d}")

            # Update full mode duration (full format)
            if hasattr(self, 'full_duration_label') and self.full_duration_label:
                self.full_duration_label.setText(f"Duration: {hours:02d}:{minutes:02d}:{seconds:02d}")

        except RuntimeError:
            # Widget was deleted, stop the timer
            if hasattr(self, 'duration_timer'):
                self.duration_timer.stop()

    def _request_stats_update(self):
        """Request stats update from account manager (via parent)."""
        # Emit signal to parent to fetch stats
        # Parent (MainWindow) will call update_stats() with fresh data
        if self._parent_window and hasattr(self._parent_window, 'request_call_stats'):
            self._parent_window.request_call_stats(self.account_id, self.session_id)

    def _on_tech_details_toggled(self, checked: bool):
        """Handle tech details checkbox toggle - show/hide content and resize window."""
        self.tech_content.setVisible(checked)
        self._is_expanded = checked

        if checked:
            # EXPANDING: Calculate actual content size needed

            # Force layout update to get accurate size hints
            self.tech_content.updateGeometry()
            self.tech_group.updateGeometry()

            # Calculate required height: current height + tech content height
            tech_content_height = self.tech_content.sizeHint().height()
            group_margins = 20  # QGroupBox margins (top/bottom)
            required_height = self.height() + tech_content_height + group_margins

            # Resize to fit content (respecting maximum)
            new_height = min(required_height, self.maximumHeight())
            self.resize(self.width(), new_height)

            # Store the expanded size and update minimum size to prevent shrinking
            self._expanded_size = QSize(self.width(), new_height)
            self.setMinimumSize(500, new_height)

        else:
            # COLLAPSING: Reset to compact size

            # Reset minimum size to collapsed state
            self.setMinimumSize(500, 300)

            # Force layout recalculation after hiding content
            self.tech_content.updateGeometry()
            self.tech_group.updateGeometry()
            self.updateGeometry()

            # Use adjustSize() to let Qt calculate the optimal collapsed size
            # This ensures proper layout recalculation after content is hidden
            self.adjustSize()

            # Ensure we're at least at the initial compact size
            if self.height() < 350:
                self.resize(self.width(), 350)

            self._expanded_size = None

    def _toggle_expanded_mode(self):
        """Toggle between slim and full window mode (all calls)."""
        # Save current position BEFORE changing window flags
        saved_pos = self.pos()

        if self._is_expanded:
            # Collapse to slim mode
            logger.debug("Collapsing to slim mode")
            self._is_expanded = False
            self.expand_button.setChecked(False)

            # Hide full UI, show slim UI
            self.full_container.setVisible(False)
            self.slim_container.setVisible(True)

            # Restore slim window flags
            self.setWindowFlags(
                Qt.Window |
                Qt.WindowStaysOnTopHint
            )

            # Resize to slim size
            self.setMinimumSize(400, 80)
            self.setMaximumSize(600, 150)
            self.resize(450, 100)

            # Restore position before show()
            self.move(saved_pos)
            self.show()  # Required after flag change

        else:
            # Expand to full window
            logger.debug("Expanding to full window mode")
            self._is_expanded = True
            self.expand_button.setChecked(True)

            # Hide slim UI, show full UI
            self.slim_container.setVisible(False)
            self.full_container.setVisible(True)

            # Change to normal window flags (with frame, always on top)
            self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)

            # Resize to normal size
            self.setMinimumSize(500, 300)
            self.setMaximumSize(800, 900)
            self.resize(550, 400)

            # Restore position before show()
            self.move(saved_pos)
            self.show()  # Required after flag change

    def _toggle_video_details(self, checked: bool):
        """Video window: show or hide the technical details panel."""
        self.tech_group.setVisible(checked)

    def _toggle_self_view(self, checked: bool):
        """Video window: show or hide the self-view (choice is saved)."""
        self.self_view.set_user_hidden(not checked)
        self._update_self_view_tooltip()

    def _update_self_view_tooltip(self):
        hidden = self.self_view.is_user_hidden()
        self.self_view_button.setToolTip("Show self-view" if hidden else "Hide self-view")

    def _on_hangup(self):
        """User clicked Hang Up button."""
        logger.info(f"User requested hangup: {self.session_id}")
        self.hangup_requested.emit()

    def _on_mute_toggled(self):
        """User toggled Mute button."""
        # Get state from whichever button was clicked
        sender = self.sender()
        is_muted = sender.isChecked()
        logger.info(f"User toggled mute: {is_muted} (session {self.session_id})")

        # Sync both mute buttons (slim and full)
        if hasattr(self, 'mute_button') and self.mute_button:
            self.mute_button.setChecked(is_muted)
        if hasattr(self, 'mute_button_full') and self.mute_button_full:
            self.mute_button_full.setChecked(is_muted)
            self.mute_button_full.setText("🎤 Unmute" if is_muted else "🎤 Mute")

        # Request mute state change via parent (MainWindow will call account.set_mute)
        if self._parent_window and hasattr(self._parent_window, 'request_call_mute'):
            self._parent_window.request_call_mute(self.account_id, self.session_id, is_muted)

    def resizeEvent(self, event):
        """Override resize event to prevent unwanted shrinking when expanded."""
        if self._is_expanded and self._expanded_size:
            # When expanded, prevent shrinking below the expanded size
            new_size = event.size()
            if new_size.height() < self._expanded_size.height():
                # Force resize to maintain expanded height
                self.resize(self.width(), self._expanded_size.height())
                return

        super().resizeEvent(event)

    # =========================================================================
    # Public methods for updating UI from signals
    # =========================================================================

    def on_call_state_changed(self, state: str):
        """
        Update call status display.

        Args:
            state: Connection state ('new', 'connecting', 'connected', 'disconnected', 'failed', 'closed')
        """
        logger.debug(f"Call state changed: {state}")

        try:
            # Update slim mode status label
            if hasattr(self, 'status_label') and self.status_label:
                if state == 'connected':
                    self.status_label.setText("Connected 🟢")
                    self.status_label.setStyleSheet("color: green; font-weight: bold;")
                elif state == 'connecting':
                    self.status_label.setText("Connecting...")
                    self.status_label.setStyleSheet("color: orange;")
                elif state == 'failed':
                    self.status_label.setText("Failed ❌")
                    self.status_label.setStyleSheet("color: red; font-weight: bold;")
                elif state == 'disconnected':
                    self.status_label.setText("Disconnected")
                    self.status_label.setStyleSheet("color: gray;")
                elif state == 'closed':
                    self.status_label.setText("Call Ended")
                    self.status_label.setStyleSheet("color: gray;")

            # Update full mode status label
            if hasattr(self, 'full_status_label') and self.full_status_label:
                if state == 'connected':
                    self.full_status_label.setText("Status: Connected 🟢")
                    self.full_status_label.setStyleSheet("color: green; font-weight: bold;")
                elif state == 'connecting':
                    self.full_status_label.setText("Status: Connecting...")
                    self.full_status_label.setStyleSheet("color: orange;")
                elif state == 'failed':
                    self.full_status_label.setText("Status: Failed ❌")
                    self.full_status_label.setStyleSheet("color: red; font-weight: bold;")
                elif state == 'disconnected':
                    self.full_status_label.setText("Status: Disconnected")
                    self.full_status_label.setStyleSheet("color: gray;")
                elif state == 'closed':
                    self.full_status_label.setText("Status: Call Ended")
                    self.full_status_label.setStyleSheet("color: gray;")

            # Handle connected state timing
            if state == 'connected' and not self.call_connected:
                self.call_start_time = time.time()
                self.call_connected = True
                logger.info(f"Call connected at {self.call_start_time}")

            # Handle closed state
            if state == 'closed':
                self._call_ended = True
                if hasattr(self, 'duration_timer'):
                    self.duration_timer.stop()
                if hasattr(self, 'stats_timer'):
                    self.stats_timer.stop()
                if self.video_view:
                    self.video_view.show_call_ended()
                if self.self_view:
                    self.self_view.stop()
                QTimer.singleShot(2000, self.close)

        except RuntimeError:
            # Widget was deleted
            pass

    def on_call_error(self, message: str):
        """
        Show a call error (mic, speaker or camera failed). The call stays up.

        Args:
            message: Error text from the call service
        """
        logger.warning(f"Call error: {message}")
        try:
            # Short name in the label ("Camera error"), each name once on one
            # line; the full GStreamer text goes to the tooltip
            name = message.split(':', 1)[0].strip()
            if name not in self._error_names:
                self._error_names.append(name)
            self._error_details.append(message)
            for label in (self.error_label, getattr(self, 'full_error_label', None)):
                if label is None:
                    continue
                label.setText(", ".join(self._error_names))
                label.setToolTip("\n".join(self._error_details))
                label.setVisible(True)
        except RuntimeError:
            # Widget was deleted
            pass

    def on_call_terminated(self, reason: str):
        """
        Handle call termination.

        Args:
            reason: Termination reason ('success', 'decline', 'busy', 'timeout', etc.)
        """
        logger.info(f"Call terminated: {reason}")
        self._call_ended = True

        # Safety: Check if widgets still exist
        try:
            if hasattr(self, 'status_label') and self.status_label:
                self.status_label.setText(f"Status: Call Ended ({reason})")
                self.status_label.setStyleSheet("color: gray;")

            # Disable controls
            if hasattr(self, 'hangup_button') and self.hangup_button:
                self.hangup_button.setEnabled(False)
        except RuntimeError:
            # Widgets already deleted
            pass

        # Stop timers
        if hasattr(self, 'duration_timer'):
            self.duration_timer.stop()
        if hasattr(self, 'stats_timer'):
            self.stats_timer.stop()
        if self.video_view:
            self.video_view.show_call_ended()
        if self.self_view:
            self.self_view.stop()

        # Close window after 2 seconds
        QTimer.singleShot(2000, self.close)

    def update_stats(self, stats: dict):
        """
        Update tech details with call statistics.

        Args:
            stats: Statistics dict from account.get_call_stats()
        """
        self.connection_state_label.setText(stats.get('connection_state', 'Unknown'))
        self.ice_state_label.setText(stats.get('ice_connection_state', 'Unknown'))
        self.ice_gathering_label.setText(stats.get('ice_gathering_state', 'Unknown'))

        # Format bandwidth
        bandwidth_kbps = stats.get('bandwidth_kbps', 0)
        self.bandwidth_label.setText(self._format_bandwidth(bandwidth_kbps))

        # Format bytes
        bytes_sent = stats.get('bytes_sent', 0)
        bytes_received = stats.get('bytes_received', 0)
        self.bytes_sent_label.setText(self._format_bytes(bytes_sent))
        self.bytes_received_label.setText(self._format_bytes(bytes_received))

        # Connection details
        our_ips = stats.get('local_candidates', [])
        peer_ips = stats.get('remote_candidates', [])
        connection_type = stats.get('connection_type', '--')

        # Format IP lists (show ALL IPs, sorted for stability)
        if our_ips:
            our_ips_text = ', '.join(our_ips)
            self.our_ips_label.setText(our_ips_text)
        else:
            self.our_ips_label.setText('--')

        if peer_ips:
            peer_ips_text = ', '.join(peer_ips)
            self.peer_ips_label.setText(peer_ips_text)
        else:
            self.peer_ips_label.setText('--')

        self.connection_type_label.setText(connection_type)

    def _format_bandwidth(self, kbps: int) -> str:
        """Format bandwidth as human-readable string."""
        if kbps < 1000:
            return f"{kbps} Kbps"
        else:
            return f"{kbps / 1000:.1f} Mbps"

    def _format_bytes(self, bytes_count: int) -> str:
        """Format bytes as human-readable string."""
        if bytes_count < 1024:
            return f"{bytes_count} B"
        elif bytes_count < 1024 * 1024:
            return f"{bytes_count / 1024:.1f} KB"
        else:
            return f"{bytes_count / (1024 * 1024):.1f} MB"

    def _start_video_if_available(self):
        """
        Start video playback if video port is available in session.

        NOTE: Video is now displayed by GStreamer autovideosink in a separate window.
        This method is kept for API compatibility but does nothing.
        """
        logger.debug("_start_video_if_available called - video handled by GStreamer autovideosink")
        pass

    def start_video_playback(self, video_port: int):
        """
        Start video playback from RTP stream.

        NOTE: Video is now displayed by GStreamer autovideosink in a separate window.
        This method is kept for API compatibility but does nothing.

        Args:
            video_port: UDP port where RTP stream is being sent (unused)
        """
        logger.debug("start_video_playback called - video handled by GStreamer autovideosink")
        pass

    def closeEvent(self, event):
        """Handle window close event."""
        # Closing the window during a call hangs up the call
        if not self._call_ended:
            self._call_ended = True
            logger.info(f"Call window closed during call, hanging up: {self.session_id}")
            self.hangup_requested.emit()

        # Stop timers when closing
        if hasattr(self, 'duration_timer'):
            self.duration_timer.stop()
        if hasattr(self, 'stats_timer'):
            self.stats_timer.stop()
        if self.video_view:
            self.video_view.stop()
        if self.self_view:
            self.self_view.stop()

        logger.info(f"Call window closed: {self.session_id}")
        event.accept()
