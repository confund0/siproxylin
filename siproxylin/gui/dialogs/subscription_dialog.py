"""
Presence subscription management dialog for Siproxylin.

Allows managing presence subscription state with a contact (RFC 6121).
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QLabel
)
from PySide6.QtCore import Qt, Signal

from ...db.database import get_db


logger = logging.getLogger('siproxylin.subscription_dialog')


class SubscriptionDialog(QDialog):
    """
    Dialog for managing presence subscription with a contact.

    Subscription states (RFC 6121 §3):
    - 'none': No subscription in either direction
    - 'to': We can see their presence (they approved our request)
    - 'from': They can see our presence (we approved their request)
    - 'both': Mutual subscription (most common)

    The dialog uses 2 checkboxes to represent the 4 possible states:
    [ ] [ ] = 'none'
    [✓] [ ] = 'to'
    [ ] [✓] = 'from'
    [✓] [✓] = 'both'
    """

    # Signals
    subscription_changed = Signal(int, str, bool, bool)  # (account_id, jid, can_see_theirs, they_can_see_ours)

    def __init__(self, account_id: int, jid: str, roster_id: int, parent=None):
        """
        Initialize subscription dialog.

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
            parent: Parent widget
        """
        super().__init__(parent)

        self.account_id = account_id
        self.jid = jid
        self.roster_id = roster_id
        self.db = get_db()

        # Window setup
        self.setWindowTitle("Presence Subscription")
        self.setMinimumWidth(450)

        # Load current subscription state
        self.current_we_see, self.current_they_see, self.we_requested, self.they_requested = self._load_subscription_state()

        # Create UI
        self._create_ui()

        logger.info(f"Subscription dialog opened for {jid} (we_see={self.current_we_see}, they_see={self.current_they_see}, we_requested={self.we_requested}, they_requested={self.they_requested})")

    def _load_subscription_state(self) -> tuple:
        """Load current subscription state from database.

        Returns:
            (we_see_theirs, they_see_ours, we_requested, they_requested) tuple of booleans
        """
        row = self.db.fetchone(
            "SELECT we_see_their_presence, they_see_our_presence, we_requested_subscription, they_requested_subscription FROM roster WHERE id = ?",
            (self.roster_id,)
        )
        if row:
            we_see = bool(row['we_see_their_presence'])
            they_see = bool(row['they_see_our_presence'])
            we_requested = bool(row['we_requested_subscription'])
            they_requested = bool(row['they_requested_subscription'])
            return (we_see, they_see, we_requested, they_requested)
        return (False, False, False, False)

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Header with JID
        header_label = QLabel(f"<b>{self.jid}</b>")
        header_label.setStyleSheet("font-size: 11pt; padding: 5px;")
        layout.addWidget(header_label)

        layout.addSpacing(10)

        # Checkbox 1: We can see their presence
        self.see_theirs_checkbox = QCheckBox("I can see their presence")
        self.see_theirs_checkbox.setToolTip("Receive their online/offline status updates")
        self.see_theirs_checkbox.setChecked(self.current_we_see or self.we_requested)

        layout.addWidget(self.see_theirs_checkbox)

        # Status label for checkbox 1
        see_theirs_status = QLabel()
        if self.current_we_see:
            see_theirs_status.setText("     ✓ Active")
            see_theirs_status.setStyleSheet("color: #5cb85c; font-size: 9pt; margin-left: 20px;")
        elif self.we_requested:
            see_theirs_status.setText("     ⏳ Pending (waiting for approval)")
            see_theirs_status.setStyleSheet("color: #f0ad4e; font-size: 9pt; margin-left: 20px;")
        else:
            see_theirs_status.setText("     (not subscribed)")
            see_theirs_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        layout.addWidget(see_theirs_status)

        layout.addSpacing(10)

        # Checkbox 2: They can see our presence
        self.they_see_ours_checkbox = QCheckBox("They can see my presence")
        self.they_see_ours_checkbox.setToolTip("Share your online/offline status with them")
        self.they_see_ours_checkbox.setChecked(self.current_they_see)

        layout.addWidget(self.they_see_ours_checkbox)

        # Status label for checkbox 2
        they_see_ours_status = QLabel()
        if self.current_they_see:
            they_see_ours_status.setText("     ✓ Active")
            they_see_ours_status.setStyleSheet("color: #5cb85c; font-size: 9pt; margin-left: 20px;")
        elif self.they_requested:
            they_see_ours_status.setText("     ⚠ Pending request from contact")
            they_see_ours_status.setStyleSheet("color: #f0ad4e; font-size: 9pt; margin-left: 20px;")
        else:
            they_see_ours_status.setText("     (not authorized)")
            they_see_ours_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        layout.addWidget(they_see_ours_status)

        layout.addSpacing(20)

        # Info box explaining what will happen
        info_label = QLabel(
            "💡 <b>How it works:</b><br>"
            "• Checking a box sends a request to the contact or server<br>"
            "• Unchecking a box revokes/cancels the subscription<br>"
            "• Changes take effect immediately"
        )
        info_label.setStyleSheet("color: #666; font-size: 9pt; padding: 10px; background-color: #f5f5f5; border-radius: 5px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        layout.addSpacing(20)

        # Bottom buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_button)

        apply_button = QPushButton("Apply")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self._on_apply)
        buttons_layout.addWidget(apply_button)

        layout.addLayout(buttons_layout)

    def _on_apply(self):
        """Apply subscription changes."""
        # Get checkbox states
        can_see_theirs = self.see_theirs_checkbox.isChecked()
        they_can_see_ours = self.they_see_ours_checkbox.isChecked()

        # Check if anything changed
        if can_see_theirs == self.current_we_see and they_can_see_ours == self.current_they_see:
            logger.info("No subscription changes detected, closing dialog")
            self.reject()
            return

        logger.info(f"Subscription changes: see_theirs={can_see_theirs}, they_see_ours={they_can_see_ours}")

        # Emit signal with new state
        self.subscription_changed.emit(
            self.account_id,
            self.jid,
            can_see_theirs,
            they_can_see_ours
        )

        self.accept()
