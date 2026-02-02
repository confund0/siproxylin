"""
Outgoing Call Dialog

Shows when initiating an audio/video call.
User can Cancel the call before peer answers.
"""

import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


logger = logging.getLogger('siproxylin.outgoing_call_dialog')


class OutgoingCallDialog(QDialog):
    """
    Dialog shown when initiating an outgoing call.

    Shows "Calling..." status while waiting for peer to accept.
    User can cancel before peer answers.
    """

    # Signals
    call_cancelled = Signal()  # User clicked Cancel

    def __init__(self, parent, account_id: int, session_id: str,
                 peer_jid: str, media_types: list):
        """
        Initialize outgoing call dialog.

        Args:
            parent: Parent widget (MainWindow)
            account_id: Account initiating the call
            session_id: Jingle session ID
            peer_jid: JID being called
            media_types: List of media types (['audio'] or ['audio', 'video'])
        """
        super().__init__(parent)

        self.account_id = account_id
        self.session_id = session_id
        self.peer_jid = peer_jid
        self.media_types = media_types

        self.setWindowTitle("Outgoing Call")
        self.setModal(False)  # Allow interaction with main window while dialog is open
        self.setFixedSize(400, 200)

        # Prevent closing via X button (must use Cancel button)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        self._setup_ui()

        logger.info(f"Outgoing call dialog shown: {peer_jid} ({media_types})")

    def _setup_ui(self):
        """Setup dialog UI."""
        layout = QVBoxLayout()
        layout.setSpacing(20)

        # Media type icon (audio or video)
        media_icon = "📹" if 'video' in self.media_types else "📞"
        media_label = "Video" if 'video' in self.media_types else "Audio"

        # Header: Calling... icon
        header = QLabel(f"{media_icon} Calling...")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Peer JID
        peer_label = QLabel(f"To: {self.peer_jid}")
        peer_font = QFont()
        peer_font.setPointSize(12)
        peer_label.setFont(peer_font)
        peer_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(peer_label)

        # Status label
        status_label = QLabel("Waiting for answer...")
        status_font = QFont()
        status_font.setPointSize(10)
        status_font.setItalic(True)
        status_label.setFont(status_font)
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(status_label)

        layout.addStretch()

        # Cancel button (red)
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        cancel_button = QPushButton("✗ Cancel")
        cancel_button.setObjectName("cancelCallButton")
        cancel_button.setStyleSheet("""
            QPushButton#cancelCallButton {
                background-color: #e74c3c;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 30px;
                border-radius: 5px;
            }
            QPushButton#cancelCallButton:hover {
                background-color: #c0392b;
            }
        """)
        cancel_button.clicked.connect(self._on_cancel)
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addStretch()

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _on_cancel(self):
        """User clicked Cancel - abort the call."""
        logger.info(f"User cancelled outgoing call: {self.session_id}")
        self.call_cancelled.emit()
        self.accept()  # Close dialog

    def update_on_status_change(self, reason: str):
        """
        Update dialog to show call status (rejected, timeout, connectivity error, etc.).

        Args:
            reason: Termination reason ('rejected', 'timeout', 'connectivity-error', etc.)
        """
        logger.info(f"Updating outgoing call dialog for status: {reason}")

        # Determine header and status text based on reason
        if reason == 'timeout':
            header_text = "⏱️ No Answer"
            status_text = f"{self.peer_jid} did not answer"
        elif reason in ['decline', 'reject', 'rejected', 'busy']:
            header_text = "❌ Call Declined"
            status_text = f"{self.peer_jid} declined the call"
        elif reason == 'cancel':
            header_text = "❌ Call Cancelled"
            status_text = f"{self.peer_jid} cancelled the call"
        elif reason in ['connectivity-error', 'failed']:
            header_text = "⚠️ Connection Failed"
            status_text = "Could not establish connection"
        else:
            header_text = "❌ Call Ended"
            status_text = f"Call ended: {reason}"

        # Update header label (first QLabel with large font)
        labels = self.findChildren(QLabel)
        if len(labels) >= 1:
            labels[0].setText(header_text)

        # Update status label (last QLabel with italic/gray)
        if len(labels) >= 3:
            labels[2].setText(status_text)

        # Update Cancel button to Close
        buttons = self.findChildren(QPushButton)
        if buttons:
            button = buttons[0]
            button.setText("Close")
            # Disconnect old cancel signal, connect simple close
            try:
                button.clicked.disconnect()
            except:
                pass
            button.clicked.connect(lambda: self.accept())

    def closeEvent(self, event):
        """
        Override close event to prevent accidental closure.
        User MUST click Cancel/Close button.
        """
        # If dialog is closing via accept() (from button click), allow it
        if not hasattr(self, '_closing'):
            event.ignore()  # Prevent closing
            logger.debug("Outgoing call dialog cannot be closed via X - use button")
