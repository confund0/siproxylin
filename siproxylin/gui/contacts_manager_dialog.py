"""
Contacts Manager dialog for Siproxylin.

Shows all contacts across all accounts with ability to:
- View contact details (JID, name, account, subscription, blocked status, presence)
- Filter by blocked status
- Search by name/JID
- Block/Unblock contacts
- Edit contact details
- Remove contacts
- Add new contacts
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QLineEdit, QCheckBox, QWidget
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ..db.database import get_db
from ..core import get_account_manager


logger = logging.getLogger('siproxylin.contacts_manager')


class ContactsManagerDialog(QDialog):
    """Dialog for managing all contacts across all accounts."""

    # Signals
    contact_modified = Signal()  # Emitted when contacts are modified (need roster refresh)
    delete_contact_requested = Signal(int, str, int)  # (account_id, jid, roster_id)

    def __init__(self, show_only_blocked=False, parent=None):
        super().__init__(parent)
        self.db = get_db()
        self.account_manager = get_account_manager()
        self.show_only_blocked = show_only_blocked

        self.setWindowTitle("Contacts Manager")
        self.setMinimumSize(900, 600)

        # Main layout
        layout = QVBoxLayout(self)

        # Top controls: Search + Filter
        controls_layout = QHBoxLayout()

        # Search box
        search_label = QLabel("Search:")
        controls_layout.addWidget(search_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by name or JID...")
        self.search_box.textChanged.connect(self._apply_filters)
        controls_layout.addWidget(self.search_box)

        controls_layout.addSpacing(20)

        # Blocked filter checkbox
        self.blocked_filter = QCheckBox("Show only blocked contacts")
        self.blocked_filter.setChecked(show_only_blocked)
        self.blocked_filter.stateChanged.connect(self._apply_filters)
        controls_layout.addWidget(self.blocked_filter)

        controls_layout.addStretch()

        layout.addLayout(controls_layout)

        # Table
        self.table = self._create_table()
        layout.addWidget(self.table)

        # Bottom buttons
        button_layout = QHBoxLayout()

        self.add_button = QPushButton("Add New Contact...")
        self.add_button.clicked.connect(self._on_add_contact)
        button_layout.addWidget(self.add_button)

        button_layout.addStretch()

        self.open_chat_button = QPushButton("Open Chat")
        self.open_chat_button.clicked.connect(self._on_open_chat)
        self.open_chat_button.setEnabled(False)
        button_layout.addWidget(self.open_chat_button)

        self.edit_button = QPushButton("Edit...")
        self.edit_button.clicked.connect(self._on_edit_contact)
        self.edit_button.setEnabled(False)
        button_layout.addWidget(self.edit_button)

        self.block_button = QPushButton("Block")
        self.block_button.clicked.connect(self._on_toggle_block)
        self.block_button.setEnabled(False)
        button_layout.addWidget(self.block_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._on_remove_contact)
        self.remove_button.setEnabled(False)
        button_layout.addWidget(self.remove_button)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._load_contacts)
        button_layout.addWidget(refresh_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

        # Connect table selection
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        # Load initial data
        self._load_contacts()

        logger.debug("Contacts Manager dialog opened")

    def _create_table(self):
        """Create table widget for displaying contacts."""
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels([
            "JID", "Local Alias", "Account", "Subscription", "Blocked"
        ])

        # Configure table appearance
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)

        # Column resizing - Interactive allows manual resizing
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)  # All columns manually resizable

        return table

    def _load_contacts(self):
        """Load all contacts from database."""
        # Disable sorting while loading
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        # Query all contacts with account info (including disabled and deleted accounts)
        contacts = self.db.fetchall("""
            SELECT
                r.id as roster_id,
                r.account_id,
                j.bare_jid,
                r.name,
                r.subscription,
                r.blocked,
                a.bare_jid as account_jid,
                a.alias as account_alias,
                a.enabled as account_enabled
            FROM roster r
            JOIN jid j ON r.jid_id = j.id
            LEFT JOIN account a ON r.account_id = a.id
            ORDER BY j.bare_jid
        """)

        # Populate table
        for contact in contacts:
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)

            # Check if account exists (LEFT JOIN can result in NULL)
            account_exists = contact['account_jid'] is not None
            account_enabled = contact['account_enabled'] if account_exists else 0

            # JID
            jid_item = QTableWidgetItem(contact['bare_jid'])
            jid_item.setData(Qt.UserRole, {
                'roster_id': contact['roster_id'],
                'account_id': contact['account_id'],
                'jid': contact['bare_jid'],
                'account_exists': account_exists,
                'account_enabled': account_enabled
            })
            self.table.setItem(row_position, 0, jid_item)

            # Name
            name = contact['name'] or ""
            name_item = QTableWidgetItem(name)
            self.table.setItem(row_position, 1, name_item)

            # Account
            if account_exists:
                account_display = contact['account_alias'] or contact['account_jid']
            else:
                account_display = f"[DELETED: {contact['account_id']}]"
            account_item = QTableWidgetItem(account_display)
            self.table.setItem(row_position, 2, account_item)

            # Subscription
            subscription = contact['subscription'] or "none"
            subscription_item = QTableWidgetItem(subscription)
            self.table.setItem(row_position, 3, subscription_item)

            # Blocked status
            blocked = "Yes" if contact['blocked'] else "No"
            blocked_item = QTableWidgetItem(blocked)
            if contact['blocked']:
                blocked_item.setForeground(Qt.red)
                font = blocked_item.font()
                font.setBold(True)
                blocked_item.setFont(font)
            self.table.setItem(row_position, 4, blocked_item)

            # Gray out entire row if account is deleted or disabled
            if not account_exists or not account_enabled:
                for col in range(5):
                    item = self.table.item(row_position, col)
                    if item:
                        item.setForeground(Qt.gray)
                        font = item.font()
                        font.setItalic(True)
                        item.setFont(font)

        # Auto-size columns to show full content (especially long JIDs)
        self.table.resizeColumnsToContents()

        # Re-enable sorting
        self.table.setSortingEnabled(True)

        # Apply filters
        self._apply_filters()

        logger.debug(f"Loaded {len(contacts)} contacts")

    def _apply_filters(self):
        """Apply search and blocked filters to table."""
        search_text = self.search_box.text().lower()
        show_only_blocked = self.blocked_filter.isChecked()

        for row in range(self.table.rowCount()):
            # Get row data
            jid = self.table.item(row, 0).text().lower()
            name = self.table.item(row, 1).text().lower()
            blocked = self.table.item(row, 4).text() == "Yes"

            # Apply filters
            matches_search = search_text in jid or search_text in name
            matches_blocked = not show_only_blocked or blocked

            # Show/hide row
            self.table.setRowHidden(row, not (matches_search and matches_blocked))

    def _on_selection_changed(self):
        """Handle table selection change."""
        has_selection = len(self.table.selectedItems()) > 0
        self.open_chat_button.setEnabled(has_selection)
        self.edit_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)
        self.block_button.setEnabled(has_selection)

        if has_selection:
            # Update block button text
            row = self.table.currentRow()
            blocked = self.table.item(row, 4).text() == "Yes"
            self.block_button.setText("Unblock" if blocked else "Block")

    def _on_open_chat(self):
        """Open chat with selected contact."""
        row = self.table.currentRow()
        if row < 0:
            return

        # Get contact data
        data = self.table.item(row, 0).data(Qt.UserRole)
        account_id = data['account_id']
        jid = data['jid']
        account_exists = data.get('account_exists', False)
        account_enabled = data.get('account_enabled', 0)

        # Check if account was deleted
        if not account_exists:
            QMessageBox.warning(
                self,
                "Account Deleted",
                f"Cannot open chat: The account for this contact no longer exists.\n\n"
                f"This contact belonged to account ID {account_id}, which has been deleted.\n"
                f"You can keep this contact for reference or remove it from the list."
            )
            logger.warning(f"Attempted to open chat for deleted account {account_id}")
            return

        # Check if account is disabled
        if not account_enabled:
            account_name = self.table.item(row, 2).text()  # Account column
            QMessageBox.warning(
                self,
                "Account Disabled",
                f"Cannot open chat: Account '{account_name}' is disabled.\n\n"
                f"Please enable the account in Edit → Accounts to use it."
            )
            logger.warning(f"Attempted to open chat for disabled account {account_id}")
            return

        # Get the main window and trigger chat opening
        main_window = self.parent()
        if main_window and hasattr(main_window, '_on_contact_selected'):
            main_window._on_contact_selected(account_id, jid)
            # Close this dialog after opening chat
            self.accept()
        else:
            QMessageBox.warning(self, "Error", "Could not open chat window.")
            logger.error("Could not find main window to open chat")

    def _on_add_contact(self):
        """Open add contact dialog with account selection."""
        # Import here to avoid circular imports
        from .contact_dialog import ContactDialog

        # Check if any accounts exist
        if not self.account_manager.accounts:
            QMessageBox.warning(self, "No Accounts", "Please create an account first.")
            return

        # Use parent main window's account selection dialog (connected accounts only)
        main_window = self.parent()
        if not main_window or not hasattr(main_window, '_select_account_dialog'):
            QMessageBox.warning(self, "Error", "Cannot select account.")
            logger.error("Could not find main window for account selection")
            return

        account_id = main_window._select_account_dialog("Select Account", "Add contact to which account?")
        if account_id is None:
            return

        dialog = ContactDialog(account_id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._load_contacts()
            self.contact_modified.emit()

    def _on_edit_contact(self):
        """Open edit contact dialog."""
        row = self.table.currentRow()
        if row < 0:
            return

        # Get contact data
        data = self.table.item(row, 0).data(Qt.UserRole)
        roster_id = data['roster_id']
        account_id = data['account_id']
        jid = data['jid']

        # Load full contact data
        contact = self.db.fetchone("""
            SELECT r.id, r.name, r.subscription, r.blocked, j.bare_jid
            FROM roster r
            JOIN jid j ON r.jid_id = j.id
            WHERE r.id = ?
        """, (roster_id,))

        if not contact:
            QMessageBox.warning(self, "Error", "Contact not found in database.")
            return

        # Import here to avoid circular imports
        from .contact_dialog import ContactDialog

        dialog = ContactDialog(account_id, contact_data=dict(contact), parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._load_contacts()
            self.contact_modified.emit()

    def _on_toggle_block(self):
        """Block or unblock selected contact."""
        row = self.table.currentRow()
        if row < 0:
            return

        # Get contact data
        data = self.table.item(row, 0).data(Qt.UserRole)
        roster_id = data['roster_id']
        account_id = data['account_id']
        jid = data['jid']
        currently_blocked = self.table.item(row, 4).text() == "Yes"

        action = "Unblock" if currently_blocked else "Block"

        # Check if account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot {action.lower()} contact while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Confirm
        reply = QMessageBox.question(
            self,
            f"{action} Contact",
            f"Are you sure you want to {action.lower()} '{jid}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Send to server
            if account and account.client:
                import asyncio
                if currently_blocked:
                    asyncio.create_task(account.client.unblock_contact(jid))
                else:
                    asyncio.create_task(account.client.block_contact(jid))

            logger.info(f"Sent {action.lower()} IQ for {jid}")

            # Update database
            new_blocked = 0 if currently_blocked else 1
            self.db.execute(
                "UPDATE roster SET blocked = ? WHERE id = ?",
                (new_blocked, roster_id)
            )
            self.db.commit()

            # Reload table
            self._load_contacts()
            self.contact_modified.emit()

            QMessageBox.information(self, "Success", f"Contact {action.lower()}ed successfully")

        except Exception as e:
            logger.error(f"Failed to {action.lower()} contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to {action.lower()} contact:\n{e}")

    def _on_remove_contact(self):
        """Remove selected contact from roster."""
        row = self.table.currentRow()
        if row < 0:
            return

        # Get contact data
        data = self.table.item(row, 0).data(Qt.UserRole)
        roster_id = data['roster_id']
        account_id = data['account_id']
        jid = data['jid']

        # Emit signal to main window (handles all the removal logic)
        logger.info(f"Emitting delete_contact_requested for {jid}")
        self.delete_contact_requested.emit(account_id, jid, roster_id)

        # Reload table after signal processing
        self._load_contacts()
        self.contact_modified.emit()
