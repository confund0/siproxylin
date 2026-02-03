"""
Contact Details Dialog - Comprehensive contact management.

Shows contact information, OMEMO devices, conversation settings,
presence subscription, and allows renaming contacts.
"""

import logging
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QTabWidget, QWidget, QFormLayout, QCheckBox, QLineEdit,
    QGroupBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ..db.database import get_db
from ..core import get_account_manager
from ..utils.avatar import get_avatar_pixmap


logger = logging.getLogger('siproxylin.contact_details_dialog')


class ContactDetailsDialog(QDialog):
    """Dialog for viewing and managing contact details."""

    # Signal emitted when contact is saved (account_id, jid, name, can_see_theirs, they_can_see_ours)
    contact_saved = Signal(int, str, str, bool, bool)
    # Signal emitted when block status changes (account_id, jid, is_blocked)
    block_status_changed = Signal(int, str, bool)

    def __init__(self, account_id: int, jid: str, parent=None):
        super().__init__(parent)
        self.account_id = account_id
        self.jid = jid
        self.db = get_db()
        self.account_manager = get_account_manager()

        # Load subscription state early (needed for UI creation)
        self.current_we_see, self.current_they_see, self.we_requested, self.they_requested = self._load_subscription_state()

        # Track original blocked status to detect changes
        self.original_blocked = False

        # Get contact name from roster
        contact_info = self.db.fetchone("""
            SELECT r.name, j.bare_jid
            FROM roster r
            JOIN jid j ON r.jid_id = j.id
            WHERE r.account_id = ? AND j.bare_jid = ?
        """, (account_id, jid))

        contact_name = contact_info['name'] if (contact_info and contact_info['name']) else jid

        self.setWindowTitle(f"Contact Details - {contact_name}")
        self.setMinimumSize(700, 550)

        # Main layout
        layout = QVBoxLayout(self)

        # Tab widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Info tab
        self.info_tab = self._create_info_tab()
        self.tabs.addTab(self.info_tab, "Info")

        # Settings tab (second position)
        self.settings_tab = self._create_settings_tab()
        self.tabs.addTab(self.settings_tab, "Settings")

        # Presence tab (subscription management)
        self.presence_tab = self._create_presence_tab()
        self.tabs.addTab(self.presence_tab, "Presence")

        # Contact devices tab
        contact_tab = QWidget()
        contact_layout = QVBoxLayout(contact_tab)
        self.contact_table = self._create_device_table()
        contact_layout.addWidget(self.contact_table)
        self.tabs.addTab(contact_tab, "OMEMO - Peer")

        # Our devices tab
        own_tab = QWidget()
        own_layout = QVBoxLayout(own_tab)
        self.own_table = self._create_device_table()
        own_layout.addWidget(self.own_table)
        self.tabs.addTab(own_tab, "OMEMO - Own")

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save_settings)
        button_layout.addWidget(self.save_button)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._load_devices)
        button_layout.addWidget(refresh_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

        # Load initial data
        self._load_devices()
        self._load_info()
        self._load_settings()

        logger.debug(f"Contact details dialog opened for {jid}")

    def _create_device_table(self):
        """Create a table widget for displaying devices."""
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels([
            "Device ID", "Fingerprint", "Trust Level", "First Seen", "Last Seen", "Actions"
        ])

        # Configure table appearance
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        # Column stretching
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Device ID
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # Fingerprint
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Trust Level
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # First Seen
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Last Seen
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Actions

        return table

    def _load_devices(self):
        """Load OMEMO devices from database."""
        # Get contact's JID ID (normalize to lowercase for XMPP JID comparison)
        contact_jid_row = self.db.fetchone(
            "SELECT id FROM jid WHERE bare_jid = ?",
            (self.jid.lower(),)
        )

        if contact_jid_row:
            contact_jid_id = contact_jid_row['id']
            self._populate_device_table(self.contact_table, contact_jid_id)
        else:
            self.contact_table.setRowCount(0)

        # Get our own JID
        account = self.db.fetchone(
            "SELECT bare_jid FROM account WHERE id = ?",
            (self.account_id,)
        )

        if account:
            # Normalize own JID to lowercase for XMPP JID comparison
            own_jid_row = self.db.fetchone(
                "SELECT id FROM jid WHERE bare_jid = ?",
                (account['bare_jid'].lower(),)
            )

            if own_jid_row:
                own_jid_id = own_jid_row['id']
                self._populate_device_table(self.own_table, own_jid_id)
            else:
                self.own_table.setRowCount(0)
        else:
            self.own_table.setRowCount(0)

    def _populate_device_table(self, table, jid_id):
        """Populate a device table with OMEMO device data."""
        # Query devices
        devices = self.db.fetchall("""
            SELECT
                device_id,
                identity_key,
                trust_level,
                first_seen,
                last_seen,
                label
            FROM omemo_device
            WHERE account_id = ? AND jid_id = ?
            ORDER BY device_id
        """, (self.account_id, jid_id))

        table.setRowCount(len(devices))

        for row_idx, device in enumerate(devices):
            # Device ID
            device_id_item = QTableWidgetItem(str(device['device_id']))
            device_id_item.setFlags(device_id_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row_idx, 0, device_id_item)

            # Fingerprint (formatted)
            fingerprint = self._format_fingerprint(device['identity_key'])
            fingerprint_item = QTableWidgetItem(fingerprint)
            fingerprint_item.setFlags(fingerprint_item.flags() & ~Qt.ItemIsEditable)
            fingerprint_item.setFont(QFont("Monospace", 9))
            table.setItem(row_idx, 1, fingerprint_item)

            # Trust Level
            trust_level = device['trust_level']
            trust_text, trust_color = self._get_trust_display(trust_level)
            trust_item = QTableWidgetItem(trust_text)
            trust_item.setFlags(trust_item.flags() & ~Qt.ItemIsEditable)
            trust_item.setForeground(trust_color)
            table.setItem(row_idx, 2, trust_item)

            # First Seen
            first_seen = self._format_timestamp(device['first_seen'])
            first_seen_item = QTableWidgetItem(first_seen)
            first_seen_item.setFlags(first_seen_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row_idx, 3, first_seen_item)

            # Last Seen
            last_seen = self._format_timestamp(device['last_seen'])
            last_seen_item = QTableWidgetItem(last_seen)
            last_seen_item.setFlags(last_seen_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row_idx, 4, last_seen_item)

            # Actions - Trust/Distrust buttons
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 2, 2, 2)

            if trust_level == 2:  # Verified
                distrust_btn = QPushButton("Distrust")
                distrust_btn.clicked.connect(
                    lambda checked=False, did=device['device_id'], jid=jid_id:
                    self._on_change_trust(jid, did, 0)
                )
                actions_layout.addWidget(distrust_btn)
            elif trust_level == 3:  # Compromised
                trust_btn = QPushButton("Trust")
                trust_btn.clicked.connect(
                    lambda checked=False, did=device['device_id'], jid=jid_id:
                    self._on_change_trust(jid, did, 2)
                )
                actions_layout.addWidget(trust_btn)
            else:  # Untrusted or blind trust
                trust_btn = QPushButton("Verify")
                trust_btn.clicked.connect(
                    lambda checked=False, did=device['device_id'], jid=jid_id:
                    self._on_change_trust(jid, did, 2)
                )
                actions_layout.addWidget(trust_btn)

                compromised_btn = QPushButton("Mark Compromised")
                compromised_btn.setStyleSheet("color: red;")
                compromised_btn.clicked.connect(
                    lambda checked=False, did=device['device_id'], jid=jid_id:
                    self._on_change_trust(jid, did, 3)
                )
                actions_layout.addWidget(compromised_btn)

            table.setCellWidget(row_idx, 5, actions_widget)

        if len(devices) == 0:
            # Show "No devices" message
            table.setRowCount(1)
            no_devices_item = QTableWidgetItem("No OMEMO devices found")
            no_devices_item.setFlags(no_devices_item.flags() & ~Qt.ItemIsEditable)
            no_devices_item.setForeground(Qt.gray)
            table.setItem(0, 0, no_devices_item)
            table.setSpan(0, 0, 1, 6)

    def _format_fingerprint(self, identity_key: str) -> str:
        """Format identity key as readable fingerprint."""
        if not identity_key:
            return "N/A"

        # Remove any whitespace
        key = identity_key.replace(" ", "").replace("\n", "")

        # Split into groups of 8 characters
        groups = [key[i:i+8] for i in range(0, len(key), 8)]

        # Join with spaces, 4 groups per line
        lines = []
        for i in range(0, len(groups), 4):
            line_groups = groups[i:i+4]
            lines.append(" ".join(line_groups))

        return "\n".join(lines)

    def _get_trust_display(self, trust_level: int):
        """Get display text and color for trust level."""
        if trust_level == 0:
            return "⚠️ Untrusted", Qt.darkYellow
        elif trust_level == 1:
            return "👁️ Blind Trust", Qt.blue
        elif trust_level == 2:
            return "✅ Verified", Qt.darkGreen
        elif trust_level == 3:
            return "❌ Compromised", Qt.red
        else:
            return "❓ Unknown", Qt.gray

    def _format_timestamp(self, timestamp) -> str:
        """Format Unix timestamp as human-readable date."""
        if not timestamp:
            return "Never"

        try:
            dt = datetime.fromtimestamp(int(timestamp))
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return "Invalid"

    def _on_change_trust(self, jid_id: int, device_id: int, new_trust_level: int):
        """Handle trust level change."""
        # Map trust level to user-friendly names
        trust_names = {
            0: "untrusted",
            1: "blind trust",
            2: "verified",
            3: "compromised"
        }

        # Confirm change
        action = trust_names.get(new_trust_level, "unknown")
        reply = QMessageBox.question(
            self,
            "Change Trust Level",
            f"Mark device {device_id} as {action}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        # Update database
        self.db.execute("""
            UPDATE omemo_device
            SET trust_level = ?
            WHERE account_id = ? AND jid_id = ? AND device_id = ?
        """, (new_trust_level, self.account_id, jid_id, device_id))

        logger.info(f"Changed trust level for device {device_id} to {new_trust_level}")

        # Refresh display
        self._load_devices()

        QMessageBox.information(
            self,
            "Trust Updated",
            f"Device {device_id} marked as {action}."
        )

    def _create_info_tab(self):
        """Create the Info tab showing contact details."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # Avatar and basic info
        header_layout = QHBoxLayout()

        # Avatar (80x80)
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(80, 80)
        self.avatar_label.setScaledContents(False)
        header_layout.addWidget(self.avatar_label)

        # Contact name and JID
        info_layout = QVBoxLayout()
        self.contact_name_label = QLabel()
        self.contact_name_label.setFont(QFont("", 12, QFont.Bold))
        info_layout.addWidget(self.contact_name_label)

        self.contact_jid_label = QLabel()
        self.contact_jid_label.setStyleSheet("color: gray;")
        info_layout.addWidget(self.contact_jid_label)

        info_layout.addStretch()
        header_layout.addLayout(info_layout)
        header_layout.addStretch()

        layout.addLayout(header_layout)
        layout.addSpacing(15)

        # Presence and Status
        presence_group = QGroupBox("Presence")
        presence_layout = QFormLayout(presence_group)

        self.presence_label = QLabel()
        presence_layout.addRow("Status:", self.presence_label)

        self.last_seen_label = QLabel()
        presence_layout.addRow("Last seen:", self.last_seen_label)

        layout.addWidget(presence_group)

        # Subscription
        subscription_group = QGroupBox("Subscription")
        subscription_layout = QFormLayout(subscription_group)

        self.subscription_label = QLabel()
        subscription_layout.addRow("Status:", self.subscription_label)

        layout.addWidget(subscription_group)

        layout.addStretch()
        return tab

    def _create_settings_tab(self):
        """Create the Settings tab for local preferences."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # Contact Information
        contact_group = QGroupBox("Contact Information")
        contact_layout = QFormLayout(contact_group)

        # Contact name (roster name)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Friendly display name (syncs with server roster)")
        self.name_input.setToolTip("Set a display name for this contact (synced across all your devices via XMPP roster)")
        contact_layout.addRow("Contact Name:", self.name_input)

        layout.addWidget(contact_group)

        # Conversation Settings
        conv_group = QGroupBox("Conversation Settings")
        conv_layout = QFormLayout(conv_group)

        # Notifications
        self.notifications_checkbox = QCheckBox("Enable notifications for this contact")
        conv_layout.addRow("Notifications:", self.notifications_checkbox)

        # Read receipts
        self.read_receipts_checkbox = QCheckBox("Send read receipts to this contact")
        conv_layout.addRow("Read receipts:", self.read_receipts_checkbox)

        # Typing notifications
        self.typing_send_checkbox = QCheckBox("Send typing notifications to this contact")
        conv_layout.addRow("Typing (send):", self.typing_send_checkbox)

        layout.addWidget(conv_group)

        # Contact Management
        mgmt_group = QGroupBox("Contact Management")
        mgmt_layout = QVBoxLayout(mgmt_group)

        # Block contact
        self.block_checkbox = QCheckBox("Block this contact (XEP-0191)")
        mgmt_layout.addWidget(self.block_checkbox)

        layout.addWidget(mgmt_group)

        # Actions
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)

        # Clear chat history
        clear_history_button = QPushButton("Clear Chat History")
        clear_history_button.clicked.connect(self._on_clear_history)
        actions_layout.addWidget(clear_history_button)

        # Remove contact
        remove_button = QPushButton("Remove Contact")
        remove_button.setStyleSheet("QPushButton { background-color: #d9534f; color: white; padding: 8px; }")
        remove_button.clicked.connect(self._on_remove_contact)
        actions_layout.addWidget(remove_button)

        layout.addWidget(actions_group)

        layout.addStretch()
        return tab

    def _load_subscription_state(self) -> tuple:
        """Load current subscription state from database (reusing SubscriptionDialog pattern).

        Returns:
            (we_see_theirs, they_see_ours, we_requested, they_requested) tuple of booleans
        """
        row = self.db.fetchone("""
            SELECT we_see_their_presence, they_see_our_presence,
                   we_requested_subscription, they_requested_subscription
            FROM roster
            WHERE account_id = ? AND jid_id = (SELECT id FROM jid WHERE bare_jid = ?)
        """, (self.account_id, self.jid))

        if row:
            we_see = bool(row['we_see_their_presence'])
            they_see = bool(row['they_see_our_presence'])
            we_requested = bool(row['we_requested_subscription'])
            they_requested = bool(row['they_requested_subscription'])
            return (we_see, they_see, we_requested, they_requested)
        return (False, False, False, False)

    def _create_presence_tab(self):
        """Create the Presence tab for subscription management (reusing SubscriptionDialog pattern)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)

        # Header
        header_label = QLabel("<b>Presence Subscription</b>")
        header_label.setStyleSheet("font-size: 11pt; padding-bottom: 10px;")
        layout.addWidget(header_label)

        layout.addSpacing(10)

        # Subscription Management
        subscription_group = QGroupBox("Subscription Settings")
        subscription_layout = QVBoxLayout(subscription_group)

        # Checkbox 1: We can see their presence
        self.see_theirs_checkbox = QCheckBox("I can see their presence")
        self.see_theirs_checkbox.setToolTip("Receive their online/offline status updates")
        self.see_theirs_checkbox.setChecked(self.current_we_see or self.we_requested)
        subscription_layout.addWidget(self.see_theirs_checkbox)

        # Status label for checkbox 1
        self.see_theirs_status = QLabel()
        if self.current_we_see:
            self.see_theirs_status.setText("     ✓ Active")
            self.see_theirs_status.setStyleSheet("color: #5cb85c; font-size: 9pt; margin-left: 20px;")
        elif self.we_requested:
            self.see_theirs_status.setText("     ⏳ Pending (waiting for approval)")
            self.see_theirs_status.setStyleSheet("color: #f0ad4e; font-size: 9pt; margin-left: 20px;")
        else:
            self.see_theirs_status.setText("     (not subscribed)")
            self.see_theirs_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        subscription_layout.addWidget(self.see_theirs_status)

        subscription_layout.addSpacing(10)

        # Checkbox 2: They can see our presence
        self.they_see_ours_checkbox = QCheckBox("They can see my presence")
        self.they_see_ours_checkbox.setToolTip("Share your online/offline status with them")
        self.they_see_ours_checkbox.setChecked(self.current_they_see)
        subscription_layout.addWidget(self.they_see_ours_checkbox)

        # Status label for checkbox 2
        self.they_see_ours_status = QLabel()
        if self.current_they_see:
            self.they_see_ours_status.setText("     ✓ Active")
            self.they_see_ours_status.setStyleSheet("color: #5cb85c; font-size: 9pt; margin-left: 20px;")
        elif self.they_requested:
            self.they_see_ours_status.setText("     ⚠ Pending request from contact")
            self.they_see_ours_status.setStyleSheet("color: #f0ad4e; font-size: 9pt; margin-left: 20px;")
        else:
            self.they_see_ours_status.setText("     (not authorized)")
            self.they_see_ours_status.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        subscription_layout.addWidget(self.they_see_ours_status)

        layout.addWidget(subscription_group)

        layout.addSpacing(20)

        # Info box
        info_label = QLabel(
            "💡 <b>How it works:</b><br>"
            "• Checking a box sends a request to the contact or server<br>"
            "• Unchecking a box revokes/cancels the subscription<br>"
            "• Changes are applied when you click Save"
        )
        info_label.setStyleSheet("color: #666; font-size: 9pt; padding: 10px; background-color: #f5f5f5; border-radius: 5px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        layout.addStretch()
        return tab

    def _load_info(self):
        """Load and display contact info."""
        # Get contact info from roster
        contact = self.db.fetchone("""
            SELECT r.name, r.subscription, r.we_see_their_presence, r.they_see_our_presence,
                   r.we_requested_subscription, r.they_requested_subscription, j.bare_jid
            FROM roster r
            JOIN jid j ON r.jid_id = j.id
            WHERE r.account_id = ? AND j.bare_jid = ?
        """, (self.account_id, self.jid))

        if contact:
            name = contact['name'] or contact['bare_jid']
            self.contact_name_label.setText(name)
            self.contact_jid_label.setText(contact['bare_jid'])

            # Subscription - two-line status with reality indicators
            we_see = bool(contact['we_see_their_presence'])
            they_see = bool(contact['they_see_our_presence'])
            we_requested = bool(contact['we_requested_subscription'])
            they_requested = bool(contact['they_requested_subscription'])

            # Line 1: I can see their presence
            if we_see:
                line1 = "✓ I can see their presence (Active)"
            elif we_requested:
                line1 = "⏳ I can see their presence (Pending approval)"
            else:
                line1 = "✗ I cannot see their presence"

            # Line 2: They can see my presence
            if they_see:
                line2 = "✓ They can see my presence (Active)"
            elif they_requested:
                line2 = "⚠ They can see my presence (Pending request)"
            else:
                line2 = "✗ They cannot see my presence"

            self.subscription_label.setText(f"{line1}\n{line2}")
        else:
            self.contact_name_label.setText(self.jid)
            self.contact_jid_label.setText(self.jid)
            self.subscription_label.setText("Not in roster")

        # Load avatar
        try:
            avatar_pixmap = get_avatar_pixmap(
                account_id=self.account_id,
                jid=self.jid,
                size=80
            )
            self.avatar_label.setPixmap(avatar_pixmap)
        except Exception as e:
            logger.error(f"Failed to load avatar: {e}")

        # Get presence
        account = self.account_manager.get_account(self.account_id)
        if account:
            presence = account.get_contact_presence(self.jid)
            presence_map = {
                'available': '🟢 Available',
                'away': '🟡 Away',
                'xa': '🟠 Extended Away',
                'dnd': '🔴 Do Not Disturb',
                'unavailable': '⚫ Offline'
            }
            self.presence_label.setText(presence_map.get(presence, '❓ Unknown'))
        else:
            self.presence_label.setText('❓ Unknown')

        # Get last seen from entity table
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.jid,))
        if jid_row:
            entity = self.db.fetchone("""
                SELECT last_seen
                FROM entity
                WHERE account_id = ? AND jid_id = ?
                ORDER BY last_seen DESC
                LIMIT 1
            """, (self.account_id, jid_row['id']))

            if entity and entity['last_seen']:
                last_seen_dt = datetime.fromtimestamp(entity['last_seen'])
                self.last_seen_label.setText(last_seen_dt.strftime("%Y-%m-%d %H:%M"))
            else:
                self.last_seen_label.setText("Never")
        else:
            self.last_seen_label.setText("Unknown")

    def _load_settings(self):
        """Load contact settings from database."""
        # Get conversation
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.jid,))
        if not jid_row:
            return

        jid_id = jid_row['id']
        conv = self.db.fetchone("""
            SELECT
                id,
                notification,
                send_typing,
                send_marker
            FROM conversation
            WHERE account_id = ? AND jid_id = ? AND type = 0
        """, (self.account_id, jid_id))

        if conv:
            conversation_id = conv['id']
            # Load conversation settings
            self.notifications_checkbox.setChecked(bool(conv['notification']))
            self.read_receipts_checkbox.setChecked(bool(conv['send_marker']))
            self.typing_send_checkbox.setChecked(bool(conv['send_typing']))

        # Get roster name and blocked status
        roster = self.db.fetchone("""
            SELECT name, blocked
            FROM roster
            WHERE account_id = ? AND jid_id = ?
        """, (self.account_id, jid_id))

        if roster:
            # Load contact name from roster
            self.name_input.setText(roster['name'] or '')
            # Load and store original blocked status
            self.original_blocked = bool(roster['blocked'])
            self.block_checkbox.setChecked(self.original_blocked)

    def _save_settings(self):
        """Save contact settings to database."""
        try:
            # Get jid_id and conversation_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.jid,))
            if not jid_row:
                QMessageBox.warning(self, "Error", "Contact not found in database")
                return

            jid_id = jid_row['id']
            conversation_id = self.db.get_or_create_conversation(self.account_id, jid_id, 0)

            # Update conversation settings
            self.db.execute("""
                UPDATE conversation
                SET notification = ?, send_typing = ?, send_marker = ?
                WHERE id = ?
            """, (
                1 if self.notifications_checkbox.isChecked() else 0,
                1 if self.typing_send_checkbox.isChecked() else 0,
                1 if self.read_receipts_checkbox.isChecked() else 0,
                conversation_id
            ))

            # Get contact name and subscription states
            new_name = self.name_input.text().strip()
            can_see_theirs = self.see_theirs_checkbox.isChecked()
            they_can_see_ours = self.they_see_ours_checkbox.isChecked()

            # Check if block status changed
            new_blocked = self.block_checkbox.isChecked()
            blocked_changed = (new_blocked != self.original_blocked)

            self.db.commit()

            # Emit unified signal for all contact changes (name + subscription)
            # This will be handled by main_window._on_contact_saved() which syncs to server
            self.contact_saved.emit(self.account_id, self.jid, new_name, can_see_theirs, they_can_see_ours)

            # If block status changed, emit signal
            # This will be handled by main_window._apply_block_status() which updates DB and chat view
            if blocked_changed:
                self.block_status_changed.emit(self.account_id, self.jid, new_blocked)
                logger.info(f"Block status changed for {self.jid}: blocked={new_blocked}")

            logger.info(f"Saved settings for contact {self.jid} (name='{new_name}', can_see_theirs={can_see_theirs}, they_can_see_ours={they_can_see_ours})")
            QMessageBox.information(self, "Settings Saved", "Contact settings have been saved.")

        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")

    def _on_clear_history(self):
        """Handle Clear Chat History button click."""
        reply = QMessageBox.question(
            self,
            "Clear Chat History",
            f"Are you sure you want to clear all chat history with {self.jid}?\n\n"
            "This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Get jid_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.jid,))
            if not jid_row:
                return

            jid_id = jid_row['id']

            # Get conversation_id
            conversation_id = self.db.get_or_create_conversation(self.account_id, jid_id, 0)

            # Mark all content items as hidden instead of deleting (preserves integrity)
            self.db.execute("""
                UPDATE content_item
                SET hide = 1
                WHERE conversation_id = ?
            """, (conversation_id,))

            self.db.commit()

            logger.info(f"Cleared chat history for {self.jid}")
            QMessageBox.information(self, "History Cleared", f"Chat history with {self.jid} has been cleared.")

        except Exception as e:
            logger.error(f"Failed to clear history: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Failed to clear history: {e}")

    def _on_remove_contact(self):
        """Handle Remove Contact button click."""
        reply = QMessageBox.question(
            self,
            "Remove Contact",
            f"Are you sure you want to remove {self.jid} from your contacts?\n\n"
            "This will also clear all chat history. This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Get jid_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.jid,))
            if not jid_row:
                return

            jid_id = jid_row['id']

            # Remove from roster (database cascade will handle rest)
            self.db.execute("""
                DELETE FROM roster
                WHERE account_id = ? AND jid_id = ?
            """, (self.account_id, jid_id))

            self.db.commit()

            # TODO: Send roster removal IQ to server (RFC 6121)

            logger.info(f"Removed contact {self.jid}")
            QMessageBox.information(self, "Contact Removed", f"{self.jid} has been removed from your contacts.")

            # Close dialog
            self.accept()

        except Exception as e:
            logger.error(f"Failed to remove contact: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Failed to remove contact: {e}")
