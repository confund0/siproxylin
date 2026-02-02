"""
Incoming Call Dialog

Shows when receiving an audio/video call.
User can Accept, Reject, or Silence (ignore) the call.
"""

import asyncio
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


logger = logging.getLogger('siproxylin.incoming_call_dialog')


class IncomingCallDialog(QDialog):
    """
    Dialog shown when receiving an incoming call.

    Three actions:
    - Accept: Answer the call (creates WebRTC connection)
    - Reject: Decline the call (sends session-terminate)
    - Silence: Ignore the call (no XMPP response, peer times out)
    """

    # Signals
    call_accepted = Signal()  # User clicked Accept
    call_rejected = Signal()  # User clicked Reject
    call_silenced = Signal()  # User clicked Silence

    def __init__(self, parent, account_id: int, session_id: str,
                 caller_jid: str, media_types: list):
        """
        Initialize incoming call dialog.

        Args:
            parent: Parent widget (MainWindow)
            account_id: Account receiving the call
            session_id: Jingle session ID
            caller_jid: JID of caller
            media_types: List of media types (['audio'] or ['audio', 'video'])
        """
        super().__init__(parent)

        self.account_id = account_id
        self.session_id = session_id
        self.caller_jid = caller_jid
        self.media_types = media_types

        self.setWindowTitle("Incoming Call")
        self.setModal(False)  # Allow interaction with main window while dialog is open
        self.setFixedSize(400, 200)

        # Prevent closing via X button (must use one of the three buttons)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        self._setup_ui()

        logger.info(f"Incoming call dialog shown: {caller_jid} ({media_types})")

    def _setup_ui(self):
        """Setup dialog UI."""
        layout = QVBoxLayout()
        layout.setSpacing(20)

        # Media type icon (audio or video)
        media_icon = "📹" if 'video' in self.media_types else "📞"
        media_label = "Video" if 'video' in self.media_types else "Audio"

        # Header: Incoming call icon
        header = QLabel(f"{media_icon} Incoming {media_label} Call")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # Caller JID
        caller_label = QLabel(f"From: {self.caller_jid}")
        caller_font = QFont()
        caller_font.setPointSize(12)
        caller_label.setFont(caller_font)
        caller_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(caller_label)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        # Accept button (green)
        accept_button = QPushButton("✓ Accept")
        accept_button.setObjectName("acceptCallButton")
        accept_button.setStyleSheet("""
            QPushButton#acceptCallButton {
                background-color: #2ecc71;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton#acceptCallButton:hover {
                background-color: #27ae60;
            }
        """)
        accept_button.clicked.connect(self._on_accept)
        button_layout.addWidget(accept_button)

        # Reject button (red)
        reject_button = QPushButton("✗ Reject")
        reject_button.setObjectName("rejectCallButton")
        reject_button.setStyleSheet("""
            QPushButton#rejectCallButton {
                background-color: #e74c3c;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton#rejectCallButton:hover {
                background-color: #c0392b;
            }
        """)
        reject_button.clicked.connect(self._on_reject)
        button_layout.addWidget(reject_button)

        # Silence button (gray)
        silence_button = QPushButton("🔕 Silence")
        silence_button.setObjectName("silenceCallButton")
        silence_button.setStyleSheet("""
            QPushButton#silenceCallButton {
                background-color: #95a5a6;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 5px;
            }
            QPushButton#silenceCallButton:hover {
                background-color: #7f8c8d;
            }
        """)
        silence_button.clicked.connect(self._on_silence)
        silence_button.setToolTip(
            "Ignore this call - no response sent to caller\n"
            "(Call will auto-reject after 60 seconds if not answered)"
        )
        button_layout.addWidget(silence_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _on_accept(self):
        """User clicked Accept - answer the call."""
        logger.info(f"Call accepted: {self.session_id}")
        self.call_accepted.emit()
        self.accept()  # Close dialog with accept status

    def _on_reject(self):
        """User clicked Reject - decline the call."""
        logger.info(f"Call rejected: {self.session_id}")
        self.call_rejected.emit()
        self.accept()  # Close dialog (we still use accept() to close cleanly)

    def _on_silence(self):
        """User clicked Silence - ignore the call (no XMPP response)."""
        logger.info(f"Call silenced (ignored): {self.session_id}")
        self.call_silenced.emit()
        self.accept()  # Close dialog

    def closeEvent(self, event):
        """
        Override close event to prevent accidental closure.
        User MUST click one of the three buttons.
        """
        # If dialog is closing via accept() (from button clicks), allow it
        if not hasattr(self, '_closing'):
            event.ignore()  # Prevent closing
            logger.debug("Incoming call dialog cannot be closed via X - use buttons")
