"""
Contact management dialog for Siproxylin.

Allows adding new contacts to roster and managing existing contacts.
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QCheckBox, QPushButton, QLabel, QMessageBox, QTextEdit
)
from PySide6.QtCore import Qt, Signal

from ..db.database import get_db


logger = logging.getLogger('siproxylin.contact_dialog')


class ContactDialog(QDialog):
    """
    Dialog for adding or editing contacts in roster.
    """

    # Signals
    contact_saved = Signal(int, str, str, bool, bool)  # (account_id, jid, name, can_see_theirs, they_can_see_ours)

    def __init__(self, account_id: int, contact_data=None, parent=None):
        """
        Initialize contact dialog.

        Args:
            account_id: Account ID to add contact to
            contact_data: Dictionary of contact data to edit (None for new contact)
            parent: Parent widget
        """
        super().__init__(parent)

        self.account_id = account_id
        self.contact_data = contact_data
        self.roster_id = contact_data['id'] if contact_data else None
        self.db = get_db()

        # Get account name for window title
        account_row = self.db.fetchone(
            "SELECT bare_jid, nickname FROM account WHERE id = ?",
            (self.account_id,)
        )
        account_name = account_row['nickname'] or account_row['bare_jid'] if account_row else f"Account {account_id}"

        # Window setup
        if self.roster_id:
            self.setWindowTitle(f"Edit Contact - {account_name}")
        else:
            self.setWindowTitle(f"New Contact - {account_name}")

        self.setMinimumWidth(500)

        # Create UI
        self._create_ui()

        # Load existing contact data if editing
        if self.roster_id:
            self._load_contact_data()

        logger.info(f"Contact dialog opened (account_id: {account_id}, roster_id: {self.roster_id})")

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Form layout for inputs
        form = QFormLayout()

        # JID (required)
        self.jid_input = QLineEdit()
        self.jid_input.setPlaceholderText("contact@example.com")
        if self.roster_id:
            # JID cannot be changed when editing
            self.jid_input.setReadOnly(True)
            self.jid_input.setStyleSheet("background-color: #f0f0f0;")
        form.addRow("JID*:", self.jid_input)

        # Display Name (optional)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Friendly display name (optional)")
        form.addRow("Display Name:", self.name_input)

        layout.addLayout(form)

        layout.addSpacing(10)

        # Presence subscription section header
        subscription_header = QLabel("<b>Presence Subscription:</b>")
        layout.addWidget(subscription_header)

        # Checkbox 1: We can see their presence
        self.see_theirs_checkbox = QCheckBox("I can see their presence")
        self.see_theirs_checkbox.setToolTip("Receive their online/offline status updates")
        layout.addWidget(self.see_theirs_checkbox)

        # Status label for checkbox 1 (only shown when editing)
        self.see_theirs_status = QLabel()
        self.see_theirs_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        layout.addWidget(self.see_theirs_status)
        if not self.roster_id:
            self.see_theirs_status.setText("     (receive their status updates)")

        layout.addSpacing(5)

        # Checkbox 2: They can see our presence
        self.they_see_ours_checkbox = QCheckBox("They can see my presence")
        self.they_see_ours_checkbox.setToolTip("Share your online/offline status with them")
        layout.addWidget(self.they_see_ours_checkbox)

        # Status label for checkbox 2 (only shown when editing)
        self.they_see_ours_status = QLabel()
        self.they_see_ours_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        layout.addWidget(self.they_see_ours_status)
        if not self.roster_id:
            self.they_see_ours_status.setText("     (share my status with them)")

        # Load current subscription state if editing
        if self.roster_id:
            self._load_subscription_state()

            # Block/Unblock button (only when editing)
            self.block_button = QPushButton()
            self.block_button.clicked.connect(self._on_toggle_block)
            layout.addWidget(self.block_button)
        else:
            # Default for new contacts: request both directions
            self.see_theirs_checkbox.setChecked(True)
            self.they_see_ours_checkbox.setChecked(True)

        layout.addSpacing(10)

        # Notes section (optional, for future enhancement)
        notes_label = QLabel("Notes:")
        layout.addWidget(notes_label)

        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Optional notes about this contact...")
        self.notes_input.setMaximumHeight(80)
        layout.addWidget(self.notes_input)

        layout.addSpacing(10)

        # Bottom buttons
        buttons_layout = QHBoxLayout()

        # Delete button (only visible when editing)
        if self.roster_id:
            self.delete_button = QPushButton("Remove Contact")
            self.delete_button.setStyleSheet("background-color: #d9534f; color: white;")
            self.delete_button.clicked.connect(self._on_delete_contact)
            buttons_layout.addWidget(self.delete_button)

        buttons_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_button)

        save_button = QPushButton("Save" if self.roster_id else "Add Contact")
        save_button.setDefault(True)
        save_button.clicked.connect(self._on_save)
        buttons_layout.addWidget(save_button)

        layout.addLayout(buttons_layout)

    def _on_toggle_block(self):
        """Toggle contact blocking status."""
        if not self.roster_id:
            return

        current_blocked = self.contact_data.get('blocked', 0)
        new_blocked = 0 if current_blocked else 1

        action = "Block" if new_blocked else "Unblock"
        jid = self.jid_input.text().strip()

        reply = QMessageBox.question(
            self,
            f"{action} Contact",
            f"Are you sure you want to {action.lower()} '{jid}'?\n\n"
            f"{'Blocked contacts cannot send you messages or see your presence.' if new_blocked else 'This contact will be able to send you messages again.'}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Update blocked status in database
            self.db.execute(
                "UPDATE roster SET blocked = ? WHERE id = ?",
                (new_blocked, self.roster_id)
            )
            self.db.commit()

            # Update local state
            self.contact_data['blocked'] = new_blocked

            # Update UI
            self._update_block_button()

            # Emit signal to notify account manager to block/unblock via XMPP
            self.contact_saved.emit(
                self.account_id,
                jid,
                self.name_input.text().strip(),
                False  # Don't send subscription request
            )

            logger.info(f"Contact {jid} {'blocked' if new_blocked else 'unblocked'}")

            QMessageBox.information(
                self,
                "Success",
                f"Contact '{jid}' has been {action.lower()}ed."
            )

        except Exception as e:
            logger.error(f"Failed to toggle block status: {e}")
            QMessageBox.critical(self, "Error", f"Failed to {action.lower()} contact:\n{e}")

    def _update_block_button(self):
        """Update block button text based on current status."""
        if not self.roster_id:
            return

        blocked = self.contact_data.get('blocked', 0)
        if blocked:
            self.block_button.setText("Unblock Contact")
            self.block_button.setStyleSheet("background-color: #5cb85c; color: white;")
        else:
            self.block_button.setText("Block Contact")
            self.block_button.setStyleSheet("background-color: #f0ad4e; color: white;")

    def _load_subscription_state(self):
        """Load and set subscription checkbox states with status indicators from database."""
        if not self.roster_id:
            return

        row = self.db.fetchone(
            "SELECT we_see_their_presence, they_see_our_presence, we_requested_subscription, they_requested_subscription FROM roster WHERE id = ?",
            (self.roster_id,)
        )
        if row:
            we_see = bool(row['we_see_their_presence'])
            they_see = bool(row['they_see_our_presence'])
            we_requested = bool(row['we_requested_subscription'])
            they_requested = bool(row['they_requested_subscription'])

            # Checkbox 1: "I can see their presence"
            self.see_theirs_checkbox.setChecked(we_see or we_requested)
            if we_see:
                self.see_theirs_status.setText("     ✓ Active")
                self.see_theirs_status.setStyleSheet("color: #5cb85c; font-size: 9pt; margin-left: 20px;")
            elif we_requested:
                self.see_theirs_status.setText("     ⏳ Pending (waiting for approval)")
                self.see_theirs_status.setStyleSheet("color: #f0ad4e; font-size: 9pt; margin-left: 20px;")
            else:
                self.see_theirs_status.setText("     (not subscribed)")
                self.see_theirs_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")

            # Checkbox 2: "They can see my presence"
            self.they_see_ours_checkbox.setChecked(they_see)
            if they_see:
                self.they_see_ours_status.setText("     ✓ Active")
                self.they_see_ours_status.setStyleSheet("color: #5cb85c; font-size: 9pt; margin-left: 20px;")
            elif they_requested:
                self.they_see_ours_status.setText("     ⚠ Pending request from contact")
                self.they_see_ours_status.setStyleSheet("color: #f0ad4e; font-size: 9pt; margin-left: 20px;")
            else:
                self.they_see_ours_status.setText("     (not authorized)")
                self.they_see_ours_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")

    def _on_delete_contact(self):
        """Delete contact with confirmation."""
        if not self.roster_id:
            return

        jid = self.jid_input.text().strip()

        reply = QMessageBox.warning(
            self,
            "Remove Contact",
            f"Are you sure you want to remove '{jid}' from your contacts?\n\n"
            f"This will:\n"
            f"- Remove the contact from your roster\n"
            f"- Revoke presence subscription\n"
            f"- Keep message history (but contact won't be in your list)\n\n"
            f"This action cannot be undone!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Delete from roster
            self.db.execute("DELETE FROM roster WHERE id = ?", (self.roster_id,))
            self.db.commit()

            logger.info(f"Contact {jid} removed from roster")

            QMessageBox.information(
                self,
                "Contact Removed",
                f"'{jid}' has been removed from your contacts.\n\n"
                f"Note: Message history has been preserved."
            )

            # Signal that contact was deleted (name empty signals deletion)
            self.contact_saved.emit(self.account_id, jid, "", False, False)

            self.accept()

        except Exception as e:
            logger.error(f"Failed to delete contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to remove contact:\n{e}")

    def _on_save(self):
        """Validate and save contact."""
        # Validate JID
        jid = self.jid_input.text().strip()

        if not jid:
            QMessageBox.warning(self, "Validation Error", "JID is required!")
            self.jid_input.setFocus()
            return

        # Basic JID validation
        if '@' not in jid or '.' not in jid:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Invalid JID format! Expected: user@domain.com"
            )
            self.jid_input.setFocus()
            return

        # Get display name
        name = self.name_input.text().strip()

        # Get subscription checkbox states
        can_see_theirs = self.see_theirs_checkbox.isChecked()
        they_can_see_ours = self.they_see_ours_checkbox.isChecked()

        try:
            if self.roster_id:
                # Update existing contact
                self._update_contact(jid, name)
            else:
                # Add new contact
                self._add_contact(jid, name)

            # Emit signal for account manager to handle XMPP operations
            self.contact_saved.emit(self.account_id, jid, name, can_see_theirs, they_can_see_ours)

            logger.info(f"Contact saved: {jid} (name: {name}, can_see_theirs: {can_see_theirs}, they_can_see_ours: {they_can_see_ours})")

            self.accept()

        except Exception as e:
            logger.error(f"Failed to save contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save contact:\n{e}")

    def _add_contact(self, jid: str, name: str):
        """Add new contact to roster."""
        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Check if contact already exists
        existing = self.db.fetchone(
            "SELECT id FROM roster WHERE account_id = ? AND jid_id = ?",
            (self.account_id, jid_id)
        )

        if existing:
            QMessageBox.warning(
                self,
                "Contact Exists",
                f"'{jid}' is already in your contacts!"
            )
            raise ValueError("Contact already exists")

        # Add to roster
        self.db.execute("""
            INSERT INTO roster (account_id, jid_id, name, subscription, blocked)
            VALUES (?, ?, ?, 'none', 0)
        """, (self.account_id, jid_id, name or None))

        self.db.commit()

        logger.info(f"Contact {jid} added to roster (account_id: {self.account_id})")

    def _update_contact(self, jid: str, name: str):
        """Update existing contact."""
        # Update roster entry
        self.db.execute("""
            UPDATE roster SET name = ?
            WHERE id = ?
        """, (name or None, self.roster_id))

        self.db.commit()

        logger.info(f"Contact {jid} updated (roster_id: {self.roster_id})")

    def _load_contact_data(self):
        """Load existing contact data from database."""
        if not self.roster_id:
            return

        # Contact data should already be passed in contact_data
        if not self.contact_data:
            logger.error(f"No contact data provided for roster_id {self.roster_id}")
            return

        # Load values into UI
        self.jid_input.setText(self.contact_data.get('bare_jid', ''))
        self.name_input.setText(self.contact_data.get('name', '') or '')

        # Update block button
        self._update_block_button()

        logger.info(f"Loaded contact data for roster_id {self.roster_id}")
