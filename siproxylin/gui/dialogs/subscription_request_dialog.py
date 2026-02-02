"""
Incoming presence subscription request dialog for Siproxylin.

Shows when someone requests to see our presence status (RFC 6121 §3.1.2).
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QLabel
)
from PySide6.QtCore import Qt


logger = logging.getLogger('siproxylin.subscription_request_dialog')


class SubscriptionRequestDialog(QDialog):
    """
    Dialog for incoming presence subscription requests.

    Simpler than the subscription management dialog - just approve/deny
    with optional mutual subscription.
    """

    def __init__(self, from_jid: str, parent=None):
        """
        Initialize subscription request dialog.

        Args:
            from_jid: JID of person requesting subscription
            parent: Parent widget
        """
        super().__init__(parent)

        self.from_jid = from_jid
        self.also_request = False  # Will be set by checkbox

        # Window setup
        self.setWindowTitle("Presence Subscription Request")
        self.setMinimumWidth(450)

        # Create UI
        self._create_ui()

        logger.info(f"Subscription request dialog shown for {from_jid}")

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Header
        header_label = QLabel("<b>Presence Subscription Request</b>")
        header_label.setStyleSheet("font-size: 11pt; padding: 5px;")
        layout.addWidget(header_label)

        layout.addSpacing(10)

        # Request message
        request_msg = QLabel(
            f"<b>{self.from_jid}</b> wants to see your presence status.\n\n"
            "Do you want to approve this request?"
        )
        request_msg.setWordWrap(True)
        request_msg.setStyleSheet("font-size: 10pt; padding: 10px;")
        layout.addWidget(request_msg)

        layout.addSpacing(10)

        # Checkbox: Also request their presence (mutual subscription)
        self.mutual_checkbox = QCheckBox("Also request their presence")
        self.mutual_checkbox.setToolTip("Send a subscription request to them as well (recommended)")
        self.mutual_checkbox.setChecked(True)  # Default to mutual subscription
        layout.addWidget(self.mutual_checkbox)

        # Description
        mutual_desc = QLabel("     (recommended for mutual subscription)")
        mutual_desc.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(mutual_desc)

        layout.addSpacing(20)

        # Info box
        info_label = QLabel(
            "💡 Approving allows them to see when you're online or offline."
        )
        info_label.setStyleSheet("color: #666; font-size: 9pt; padding: 10px; background-color: #f5f5f5; border-radius: 5px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        layout.addSpacing(20)

        # Bottom buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        deny_button = QPushButton("Deny")
        deny_button.clicked.connect(self.reject)
        buttons_layout.addWidget(deny_button)

        approve_button = QPushButton("Approve")
        approve_button.setDefault(True)
        approve_button.clicked.connect(self._on_approve)
        buttons_layout.addWidget(approve_button)

        layout.addLayout(buttons_layout)

    def _on_approve(self):
        """Handle approve button click."""
        # Store checkbox state
        self.also_request = self.mutual_checkbox.isChecked()
        logger.info(f"Subscription request approved (mutual={self.also_request})")
        self.accept()
