"""
Call Window

Separate window showing active call with controls and tech details.
"""

import asyncio
import logging
import time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QFont


logger = logging.getLogger('siproxylin.call_window')


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
                 peer_jid: str, media_types: list, direction: str, account=None):
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
        """
        super().__init__(parent)

        self.account_id = account_id
        self.session_id = session_id
        self.peer_jid = peer_jid
        self.media_types = media_types
        self.direction = direction
        self.account = account

        # Call timing
        self.call_start_time: Optional[float] = None
        self.call_connected = False

        # Size management for collapsible tech details
        self._expanded_size = None
        self._is_expanded = False

        # Setup window
        self.setWindowTitle(f"Call - {peer_jid}")
        self.setMinimumSize(500, 300)  # Minimum size (collapsed state)
        self.setMaximumSize(800, 900)  # Maximum size (reasonable limits)
        self.resize(550, 350)  # Initial size (compact, tech details collapsed)
        self.setWindowFlag(Qt.Window)  # Separate window (not dialog)

        self._setup_ui()
        self._start_timers()

        logger.info(f"Call window opened: {peer_jid} ({direction}, {media_types})")

    def _setup_ui(self):
        """Setup call window UI."""
        layout = QVBoxLayout()
        layout.setSpacing(15)

        # =====================================================================
        # Header: Media icon + Peer JID
        # =====================================================================
        header_layout = QHBoxLayout()

        media_icon = "📹" if 'video' in self.media_types else "📞"
        media_label = "Video" if 'video' in self.media_types else "Audio"

        icon_label = QLabel(media_icon)
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

        direction_label = QLabel(f"{self.direction.title()} {media_label} Call")
        direction_label.setStyleSheet("color: gray;")
        peer_layout.addWidget(direction_label)

        header_layout.addLayout(peer_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # =====================================================================
        # Status Display
        # =====================================================================
        self.status_label = QLabel("Status: Connecting...")
        status_font = QFont()
        status_font.setPointSize(12)
        self.status_label.setFont(status_font)
        layout.addWidget(self.status_label)

        # Call duration
        self.duration_label = QLabel("Duration: --:--:--")
        duration_font = QFont()
        duration_font.setPointSize(14)
        duration_font.setBold(True)
        self.duration_label.setFont(duration_font)
        layout.addWidget(self.duration_label)

        # =====================================================================
        # Video Display - GStreamer handles display in separate window
        # =====================================================================
        # Video is displayed by GStreamer autovideosink in a separate window
        # No Qt widget needed here

        layout.addStretch()

        # =====================================================================
        # Controls: Hang Up, Mute (future)
        # =====================================================================
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(15)

        # Hang Up button (red, prominent)
        self.hangup_button = QPushButton("📴 Hang Up")
        self.hangup_button.setObjectName("hangupButton")
        self.hangup_button.setStyleSheet("""
            QPushButton#hangupButton {
                background-color: #e74c3c;
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding: 15px 30px;
                border-radius: 8px;
            }
            QPushButton#hangupButton:hover {
                background-color: #c0392b;
            }
        """)
        self.hangup_button.clicked.connect(self._on_hangup)
        controls_layout.addWidget(self.hangup_button)

        # Mute button (red when active = privacy concern, gray when muted = safe)
        self.mute_button = QPushButton("🎤")
        self.mute_button.setCheckable(True)
        self.mute_button.setEnabled(True)
        self.mute_button.setToolTip("Mute/unmute microphone")
        # Default state: unmuted (microphone active = privacy concern = red)
        self.mute_button.setStyleSheet("""
            QPushButton {
                background-color: #c0392b;
                color: white;
                font-size: 18px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #e74c3c;
            }
        """)
        self.mute_button.clicked.connect(self._on_mute_toggled)
        controls_layout.addWidget(self.mute_button)

        layout.addLayout(controls_layout)

        # =====================================================================
        # Tech Details (collapsible)
        # =====================================================================
        self.tech_group = QGroupBox("Technical Details")
        self.tech_group.setCheckable(True)
        self.tech_group.setChecked(False)  # Collapsed by default

        # Create container widget for the content
        self.tech_content = QWidget()
        tech_layout = QFormLayout()
        tech_layout.setSpacing(8)
        tech_layout.setContentsMargins(0, 0, 0, 0)

        self.connection_state_label = QLabel("Unknown")
        self.ice_state_label = QLabel("Unknown")
        self.ice_gathering_label = QLabel("Unknown")
        self.bandwidth_label = QLabel("0 Kbps")
        self.bytes_sent_label = QLabel("0 B")
        self.bytes_received_label = QLabel("0 B")

        # Connection details
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

        # Add content to group box
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(10, 10, 10, 10)
        group_layout.addWidget(self.tech_content)
        self.tech_group.setLayout(group_layout)

        # Connect toggle signal to collapse/expand handler
        self.tech_group.toggled.connect(self._on_tech_details_toggled)

        # Start collapsed
        self.tech_content.setVisible(False)

        layout.addWidget(self.tech_group)

        self.setLayout(layout)

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
        if not self.call_start_time:
            self.duration_label.setText("Duration: --:--:--")
            return

        elapsed = int(time.time() - self.call_start_time)
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60
        self.duration_label.setText(f"Duration: {hours:02d}:{minutes:02d}:{seconds:02d}")

    def _request_stats_update(self):
        """Request stats update from account manager (via parent)."""
        # Emit signal to parent to fetch stats
        # Parent (MainWindow) will call update_stats() with fresh data
        if hasattr(self.parent(), 'request_call_stats'):
            self.parent().request_call_stats(self.account_id, self.session_id)

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

    def _on_hangup(self):
        """User clicked Hang Up button."""
        logger.info(f"User requested hangup: {self.session_id}")
        self.hangup_requested.emit()

    def _on_mute_toggled(self):
        """User toggled Mute button."""
        is_muted = self.mute_button.isChecked()
        logger.info(f"User toggled mute: {is_muted} (session {self.session_id})")

        # Update button style based on state
        if is_muted:
            # Muted = safe, no privacy concern = dark gray with strikethrough
            self.mute_button.setStyleSheet("""
                QPushButton {
                    background-color: #7f8c8d;
                    color: white;
                    font-size: 18px;
                    padding: 8px;
                    text-decoration: line-through;
                }
                QPushButton:hover {
                    background-color: #95a5a6;
                }
            """)
        else:
            # Unmuted = microphone active = privacy concern = dark red
            self.mute_button.setStyleSheet("""
                QPushButton {
                    background-color: #c0392b;
                    color: white;
                    font-size: 18px;
                    padding: 8px;
                }
                QPushButton:hover {
                    background-color: #e74c3c;
                }
            """)

        # Request mute state change via parent (MainWindow will call account.set_mute)
        if hasattr(self.parent(), 'request_call_mute'):
            self.parent().request_call_mute(self.account_id, self.session_id, is_muted)

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

        if state == 'connected':
            self.status_label.setText("Status: Connected 🟢")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")

            if not self.call_connected:
                # First time connecting
                self.call_start_time = time.time()
                self.call_connected = True
                logger.info(f"Call connected at {self.call_start_time}")

        elif state == 'connecting':
            self.status_label.setText("Status: Connecting...")
            self.status_label.setStyleSheet("color: orange;")

        elif state == 'failed':
            self.status_label.setText("Status: Failed ❌")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")

        elif state == 'disconnected':
            self.status_label.setText("Status: Disconnected")
            self.status_label.setStyleSheet("color: gray;")

        elif state == 'closed':
            self.status_label.setText("Status: Call Ended")
            self.status_label.setStyleSheet("color: gray;")

            # Stop timers
            self.duration_timer.stop()
            self.stats_timer.stop()

            # Close window after 2 seconds
            QTimer.singleShot(2000, self.close)

    def on_call_terminated(self, reason: str):
        """
        Handle call termination.

        Args:
            reason: Termination reason ('success', 'decline', 'busy', 'timeout', etc.)
        """
        logger.info(f"Call terminated: {reason}")

        self.status_label.setText(f"Status: Call Ended ({reason})")
        self.status_label.setStyleSheet("color: gray;")

        # Disable controls
        self.hangup_button.setEnabled(False)

        # Stop timers
        self.duration_timer.stop()
        self.stats_timer.stop()

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
        # Stop timers when closing
        if hasattr(self, 'duration_timer'):
            self.duration_timer.stop()
        if hasattr(self, 'stats_timer'):
            self.stats_timer.stop()

        logger.info(f"Call window closed: {self.session_id}")
        event.accept()
