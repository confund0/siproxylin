"""
Contact list widget for Siproxylin.

Displays roster contacts with presence indicators, grouped by account.
"""

import logging
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLabel, QLineEdit, QHBoxLayout, QMenu, QPushButton, QFrame, QToolButton
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QAction, QColor

from ..db.database import get_db
from ..core import get_account_manager
from ..styles.theme_manager import get_theme_manager
from .models import ContactDisplayData, AccountDisplayData
from .utils import TooltipEventFilter


logger = logging.getLogger('siproxylin.contact_list')


class ContactListWidget(QWidget):
    """Contact list widget displaying roster contacts."""

    # Signal emitted when a contact is clicked
    contact_selected = Signal(int, str)  # (account_id, jid)

    # Signal emitted when HOME button is clicked
    home_requested = Signal()

    # Signal emitted when call log button is clicked
    call_log_requested = Signal()

    # Signal emitted when settings button is clicked
    settings_requested = Signal()

    # Signal emitted when contacts button is clicked
    contacts_requested = Signal()

    # Signals for context menu actions
    edit_contact_requested = Signal(int, str, int)  # (account_id, jid, roster_id)
    view_omemo_keys_requested = Signal(int, str)  # (account_id, jid)
    manage_subscription_requested = Signal(int, str, int)  # (account_id, jid, roster_id)
    block_contact_requested = Signal(int, str, int, bool)  # (account_id, jid, roster_id, currently_blocked)
    delete_chat_requested = Signal(int, str)  # (account_id, jid)
    delete_contact_requested = Signal(int, str, int)  # (account_id, jid, roster_id)
    delete_and_block_requested = Signal(int, str, int)  # (account_id, jid, roster_id)

    # MUC-specific signals
    view_muc_details_requested = Signal(int, str)  # (account_id, room_jid)
    leave_muc_requested = Signal(int, str)  # (account_id, room_jid)

    # Account-specific signals
    account_connect_requested = Signal(int)  # (account_id)
    account_disconnect_requested = Signal(int)  # (account_id)
    account_details_requested = Signal(int)  # (account_id)
    add_contact_requested = Signal(int)  # (account_id)
    join_room_requested = Signal(int)  # (account_id)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.db = get_db()
        self.account_manager = get_account_manager()

        # Track typing states: {(account_id, jid): state}
        self.typing_states = {}

        # Setup UI
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search box
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(5, 5, 5, 5)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search contacts...")
        self.search_box.textChanged.connect(self._on_search)
        search_layout.addWidget(self.search_box)
        layout.addLayout(search_layout)

        # Contact tree
        self.contact_tree = QTreeWidget()
        self.contact_tree.setHeaderHidden(True)
        self.contact_tree.setIndentation(15)
        self.contact_tree.setRootIsDecorated(True)
        self.contact_tree.itemClicked.connect(self._on_item_clicked)

        # Enable context menu
        self.contact_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.contact_tree.customContextMenuRequested.connect(self._on_context_menu)

        layout.addWidget(self.contact_tree, 1)  # Stretch factor 1

        # Navigation bar at bottom - no frame styling for seamless look (just buttons floating at bottom)
        nav_bar = QFrame()
        nav_bar.setFrameShape(QFrame.NoFrame)  # No border/separator
        nav_bar.setStyleSheet("background: transparent;")  # Transparent background to blend with roster
        nav_bar_layout = QHBoxLayout(nav_bar)
        nav_bar_layout.setContentsMargins(8, 8, 8, 8)
        nav_bar_layout.setSpacing(10)

        # Evenly spread buttons across the area
        nav_bar_layout.addStretch()

        # Call log button
        self.call_log_button = QToolButton()
        self.call_log_button.setObjectName("callLogButton")
        self.call_log_button.setText("📞")
        self.call_log_button.setToolTip("View call log")
        self.call_log_button.setFixedSize(32, 32)
        self.call_log_button.clicked.connect(self.call_log_requested.emit)
        nav_bar_layout.addWidget(self.call_log_button)

        nav_bar_layout.addStretch()

        # Contacts button
        self.contacts_button = QToolButton()
        self.contacts_button.setObjectName("contactsButton")
        self.contacts_button.setText("📒")
        self.contacts_button.setToolTip("Manage contacts")
        self.contacts_button.setFixedSize(32, 32)
        self.contacts_button.clicked.connect(self.contacts_requested.emit)
        nav_bar_layout.addWidget(self.contacts_button)

        nav_bar_layout.addStretch()

        # HOME button
        self.home_button = QToolButton()
        self.home_button.setObjectName("homeButton")
        self.home_button.setText("🏠")
        self.home_button.setToolTip("Return to home page")
        self.home_button.setFixedSize(32, 32)
        self.home_button.clicked.connect(self.home_requested.emit)
        nav_bar_layout.addWidget(self.home_button)

        nav_bar_layout.addStretch()

        # Settings button
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setText("⚙")
        self.settings_button.setToolTip("Settings")
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        nav_bar_layout.addWidget(self.settings_button)

        nav_bar_layout.addStretch()

        layout.addWidget(nav_bar)

        # Install tooltip event filter for contact tree (1500ms delay)
        self.tooltip_filter = TooltipEventFilter(delay_ms=1500, parent=self)
        self.contact_tree.installEventFilter(self.tooltip_filter)

        # Install tooltip event filter for bottom bar buttons (700ms delay, same as header)
        self.button_tooltip_filter = TooltipEventFilter(delay_ms=700, parent=self)
        self.call_log_button.installEventFilter(self.button_tooltip_filter)
        self.contacts_button.installEventFilter(self.button_tooltip_filter)
        self.home_button.installEventFilter(self.button_tooltip_filter)
        self.settings_button.installEventFilter(self.button_tooltip_filter)

        logger.debug("Contact list widget created")

    def load_roster(self):
        """Load roster from database and populate tree."""
        self.contact_tree.clear()

        # Get all accounts
        accounts = self.db.fetchall(
            "SELECT id, bare_jid, alias FROM account WHERE enabled = 1 ORDER BY id"
        )

        for account in accounts:
            account_id = account['id']
            account_label = account['alias'] or account['bare_jid']

            # Get unread counts for this account
            unread_by_jid = {}
            unread_conversations = self.db.get_unread_conversations_for_account(account_id)
            for conv in unread_conversations:
                unread_by_jid[conv['jid']] = conv['unread_count']

            account_total_unread = self.db.get_total_unread_for_account(account_id)

            # Create account node with data model
            account_item = QTreeWidgetItem(self.contact_tree)

            # Get connection status
            account_obj = self.account_manager.get_account(account_id)
            is_connected = account_obj.is_connected() if account_obj else False

            # Create AccountDisplayData model
            account_data = AccountDisplayData(
                account_id=account_id,
                bare_jid=account['bare_jid'],
                name=account_label,
                is_connected=is_connected,
                total_unread=account_total_unread
            )

            # Store data model and update display
            account_item.setData(0, Qt.UserRole, account_data)
            self._update_account_item(account_item, account_data)
            account_item.setExpanded(True)

            # Get MUC rooms from both bookmarks AND roster (deduplicated by JID)
            # A MUC can be in roster (from server roster), bookmark (user bookmarked), or both
            rooms = self.db.fetchall("""
                SELECT
                    j.bare_jid,
                    COALESCE(NULLIF(b.name, ''), NULLIF(r.name, ''), j.bare_jid) as name,
                    b.id as bookmark_id,
                    b.autojoin,
                    r.id as roster_id
                FROM jid j
                LEFT JOIN bookmark b ON b.jid_id = j.id AND b.account_id = ?
                LEFT JOIN roster r ON r.jid_id = j.id AND r.account_id = ?
                WHERE (b.id IS NOT NULL OR r.id IS NOT NULL)
                  AND j.bare_jid LIKE '%@%'
                  AND (
                    -- MUC JIDs typically contain conference/chat/muc keywords
                    j.bare_jid LIKE '%conference%'
                    OR j.bare_jid LIKE '%chat.%'
                    OR j.bare_jid LIKE '%muc.%'
                    OR j.bare_jid LIKE '%groups.%'
                    OR b.id IS NOT NULL  -- Or has a bookmark (definite MUC)
                  )
                GROUP BY j.bare_jid
                ORDER BY name, j.bare_jid
            """, (account_id, account_id))

            # Get 1-to-1 contacts from roster (exclude MUCs)
            muc_jids = {room['bare_jid'] for room in rooms}
            contacts = self.db.fetchall("""
                SELECT
                    r.id,
                    r.account_id,
                    j.bare_jid,
                    r.name,
                    r.subscription,
                    r.blocked
                FROM roster r
                JOIN jid j ON r.jid_id = j.id
                WHERE r.account_id = ?
                ORDER BY r.name, j.bare_jid
            """, (account_id,))

            # Filter out MUCs from contacts list
            contacts = [c for c in contacts if c['bare_jid'] not in muc_jids]

            # Add MUC rooms first
            if rooms:
                for room in rooms:
                    room_item = QTreeWidgetItem(account_item)

                    # Create ContactDisplayData for MUC
                    room_data = ContactDisplayData(
                        jid=room['bare_jid'],
                        name=room['name'],
                        account_id=account_id,
                        item_type='muc',
                        is_muc=True,
                        roster_id=room['roster_id'],
                        bookmark_id=room['bookmark_id'],
                        autojoin=bool(room['autojoin']) if room['autojoin'] is not None else False,
                        unread_count=unread_by_jid.get(room['bare_jid'], 0)
                    )

                    # Store data model and update display
                    room_item.setData(0, Qt.UserRole, room_data)
                    self._update_item_from_data(room_item, room_data)

            if not contacts and not rooms:
                # No contacts or rooms yet
                no_contacts_item = QTreeWidgetItem(account_item)
                no_contacts_item.setText(0, "(No contacts)")
                no_contacts_item.setForeground(0, Qt.gray)
                # No data needed for placeholder item
            elif contacts:
                # Add contacts
                for contact in contacts:
                    contact_item = QTreeWidgetItem(account_item)

                    # Get presence from account manager
                    account = self.account_manager.get_account(account_id)
                    if account:
                        presence = account.get_contact_presence(contact['bare_jid'])
                    else:
                        presence = 'unavailable'

                    # Create ContactDisplayData
                    contact_data = ContactDisplayData(
                        jid=contact['bare_jid'],
                        name=contact['name'] or contact['bare_jid'],
                        account_id=contact['account_id'],
                        item_type='contact',
                        is_muc=False,
                        roster_id=contact['id'],
                        presence=presence,
                        subscription=contact['subscription'] or 'none',
                        blocked=bool(contact['blocked']),
                        unread_count=unread_by_jid.get(contact['bare_jid'], 0)
                    )

                    # Store data model and update display
                    contact_item.setData(0, Qt.UserRole, contact_data)
                    self._update_item_from_data(contact_item, contact_data)

        logger.info(f"Loaded roster for {len(accounts)} accounts")

    def _on_search(self, text: str):
        """Filter contacts based on search text."""
        # TODO: Implement search filtering
        pass

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle contact/room item click."""
        data = item.data(0, Qt.UserRole)
        if not data or not isinstance(data, ContactDisplayData):
            return

        if data.item_type not in ('contact', 'muc'):
            return

        account_id = data.account_id
        jid = data.jid
        item_type = "Room" if data.is_muc else "Contact"

        logger.info(f"{item_type} selected: {jid} (account {account_id})")
        self.contact_selected.emit(account_id, jid)

    def refresh(self):
        """Refresh contact list from database (full rebuild)."""
        self.load_roster()

    def refresh_display(self):
        """
        Refresh display of all roster items without reloading from database.

        Useful for updating colors after theme change or roster mode change.
        """
        # Iterate through all top-level items (accounts)
        for i in range(self.contact_tree.topLevelItemCount()):
            account_item = self.contact_tree.topLevelItem(i)
            account_data = account_item.data(0, Qt.UserRole)

            # Refresh account item
            if account_data and isinstance(account_data, AccountDisplayData):
                self._update_account_item(account_item, account_data)

            # Refresh all children (contacts and MUCs)
            for j in range(account_item.childCount()):
                child_item = account_item.child(j)
                child_data = child_item.data(0, Qt.UserRole)

                if child_data and isinstance(child_data, ContactDisplayData):
                    self._update_item_from_data(child_item, child_data)

        logger.debug("Roster display refreshed (theme-aware colors updated)")

    def select_contact(self, account_id: int, jid: str):
        """
        Programmatically select a contact in the roster.

        Args:
            account_id: Account ID
            jid: Contact JID
        """
        # Use helper method to find item
        item = self._find_contact_item(account_id, jid)
        if item:
            # Expand parent account node
            parent = item.parent()
            if parent:
                parent.setExpanded(True)

            # Select and scroll to item
            self.contact_tree.setCurrentItem(item)
            self.contact_tree.scrollToItem(item)
            logger.debug(f"Selected contact {jid} in roster")
        else:
            logger.debug(f"Contact {jid} not found in account {account_id} roster")

    # === Helper Methods for Data Model ===

    def _get_muc_participant_count(self, account_id: int, room_jid: str) -> Optional[int]:
        """
        Get live participant count for a MUC room.

        Args:
            account_id: Account ID
            room_jid: Room JID

        Returns:
            Participant count or None if unavailable (room not joined)
        """
        try:
            account = self.account_manager.get_account(account_id)
            if not account or not account.client:
                logger.debug(f"Cannot get participant count for {room_jid}: account not available or not connected")
                return None

            # Get room roster from XEP-0045 plugin
            xep_0045 = account.client.plugin['xep_0045']
            if not xep_0045:
                logger.debug(f"Cannot get participant count for {room_jid}: XEP-0045 plugin not available")
                return None

            # Access rooms dictionary (only has rooms we're actively joined to)
            if room_jid not in xep_0045.rooms:
                logger.debug(f"Cannot get participant count for {room_jid}: room not joined (not in xep_0045.rooms)")
                return None

            # Count participants (same logic as chat_view.py)
            room_roster = xep_0045.rooms[room_jid]
            count = len(room_roster)
            logger.debug(f"MUC {room_jid} has {count} participants")
            return count

        except Exception as e:
            logger.error(f"Failed to get participant count for {room_jid}: {e}")
            return None

    def _find_contact_item(self, account_id: int, jid: str):
        """
        Find contact/MUC tree item by (account_id, bare_jid).

        Args:
            account_id: Account ID
            jid: Contact JID (bare or full - will be normalized)

        Returns:
            QTreeWidgetItem if found, None otherwise
        """
        # Normalize to bare JID (strip resource if present)
        bare_jid = jid.split('/')[0] if '/' in jid else jid

        # Traverse tree to find matching item
        root = self.contact_tree.invisibleRootItem()
        for i in range(root.childCount()):
            account_item = root.child(i)
            account_data = account_item.data(0, Qt.UserRole)

            if not account_data or not isinstance(account_data, AccountDisplayData):
                continue

            if account_data.account_id != account_id:
                continue

            # Search children for contact/MUC
            for j in range(account_item.childCount()):
                child_item = account_item.child(j)
                child_data = child_item.data(0, Qt.UserRole)

                if not child_data or not isinstance(child_data, ContactDisplayData):
                    continue

                if child_data.jid == bare_jid:
                    return child_item

        return None

    def _update_item_from_data(self, item, data: ContactDisplayData):
        """
        Update tree item text and font from ContactDisplayData.

        Args:
            item: QTreeWidgetItem to update
            data: ContactDisplayData with current state
        """
        # Get complete roster styling from theme manager
        theme_manager = get_theme_manager()
        roster_style = theme_manager.get_roster_style() if theme_manager else None
        if not roster_style:
            return  # Can't style without theme manager

        # Update text from data model (uses_emoji determines icon vs text)
        item.setText(0, data.to_display_string(uses_emoji=roster_style.uses_emoji))

        # Update font style
        style = data.get_font_style()
        font = item.font(0)
        font.setBold(style['bold'])
        font.setItalic(style['italic'])
        font.setUnderline(style['underline'])
        item.setFont(0, font)

        # Apply text color (roster style handles steady vs dynamic)
        color = roster_style.get_text_color_for_contact(
            presence=data.presence,
            is_muc=data.is_muc,
            call_state=data.call_state
        )
        item.setForeground(0, color)

        # Fetch live participant count for MUCs
        if data.is_muc:
            participant_count = self._get_muc_participant_count(data.account_id, data.jid)
            if participant_count is not None:
                data.participant_count = participant_count

        # Set tooltip
        item.setToolTip(0, data.get_tooltip())

    def _update_account_item(self, item, data: AccountDisplayData):
        """
        Update account tree item text and font from AccountDisplayData.

        Args:
            item: QTreeWidgetItem to update
            data: AccountDisplayData with current state
        """
        # Get complete roster styling from theme manager
        theme_manager = get_theme_manager()
        roster_style = theme_manager.get_roster_style() if theme_manager else None
        if not roster_style:
            return  # Can't style without theme manager

        # Update text from data model (uses_emoji determines icon vs text)
        item.setText(0, data.to_display_string(uses_emoji=roster_style.uses_emoji))

        # Update font style
        style = data.get_font_style()
        font = item.font(0)
        font.setBold(style['bold'])
        font.setItalic(style['italic'])
        font.setUnderline(style['underline'])
        item.setFont(0, font)

        # Apply text color (roster style handles steady vs dynamic)
        color = roster_style.get_text_color_for_account(connected=data.is_connected)
        item.setForeground(0, color)

        # Set tooltip
        item.setToolTip(0, data.get_tooltip())

    # === Update Methods ===

    def update_presence_single(self, account_id: int, jid: str, presence: str):
        """
        Update presence indicator for a single contact (event-driven).

        Args:
            account_id: Account ID
            jid: Contact JID
            presence: Presence show value ('available', 'away', 'xa', 'dnd', 'unavailable')
        """
        item = self._find_contact_item(account_id, jid)
        if not item:
            logger.debug(f"Contact {jid} not found for presence update")
            return

        data = item.data(0, Qt.UserRole)
        if not data or not isinstance(data, ContactDisplayData):
            return

        # Update data model
        data.presence = presence
        self._update_item_from_data(item, data)
        logger.debug(f"Updated presence for {jid}: {presence}")

    def update_unread_indicators(self, account_id: int = None, jid: str = None):
        """
        Update unread message indicators without rebuilding tree.

        Args:
            account_id: Account ID to update (or None for all accounts)
            jid: Specific JID to update (or None for all in account)
        """
        root = self.contact_tree.invisibleRootItem()

        # Iterate through account nodes
        for i in range(root.childCount()):
            account_item = root.child(i)
            account_data = account_item.data(0, Qt.UserRole)

            if not account_data or not isinstance(account_data, AccountDisplayData):
                continue

            acc_id = account_data.account_id

            # Skip if specific account requested and this isn't it
            if account_id is not None and acc_id != account_id:
                continue

            # Get unread counts for this account
            unread_by_jid = {}
            unread_conversations = self.db.get_unread_conversations_for_account(acc_id)
            for conv in unread_conversations:
                unread_by_jid[conv['jid']] = conv['unread_count']

            account_total_unread = self.db.get_total_unread_for_account(acc_id)

            # Update account item with new unread count
            account_data.total_unread = account_total_unread
            self._update_account_item(account_item, account_data)

            # Iterate through contacts/MUCs under this account
            for j in range(account_item.childCount()):
                child_item = account_item.child(j)
                child_data = child_item.data(0, Qt.UserRole)

                if not child_data or not isinstance(child_data, ContactDisplayData):
                    continue

                if child_data.item_type not in ('contact', 'muc'):
                    continue

                child_jid = child_data.jid

                # Skip if specific jid requested and this isn't it
                if jid is not None and child_jid != jid:
                    continue

                # Get unread count for this contact/MUC
                unread_count = unread_by_jid.get(child_jid, 0)

                # Update data model
                child_data.unread_count = unread_count
                self._update_item_from_data(child_item, child_data)

        logger.debug(f"Updated unread indicators (account_id={account_id}, jid={jid})")

    def _on_context_menu(self, position):
        """Show context menu for contact list items."""
        item = self.contact_tree.itemAt(position)
        if not item:
            return

        # Get item data
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        # Determine item type from data model
        if isinstance(data, AccountDisplayData):
            self._show_account_context_menu(position, data)
        elif isinstance(data, ContactDisplayData):
            if data.item_type == 'contact':
                self._show_contact_context_menu(position, data)
            elif data.item_type == 'muc':
                self._show_muc_context_menu(position, data)

    def _show_contact_context_menu(self, position, data: ContactDisplayData):
        """Show context menu for a contact."""
        account_id = data.account_id
        jid = data.jid
        roster_id = data.roster_id
        is_blocked = data.blocked

        # Create menu
        menu = QMenu(self)

        # Open Chat (default action)
        open_chat_action = QAction("Open Chat", self)
        open_chat_action.triggered.connect(lambda: self.contact_selected.emit(account_id, jid))
        menu.addAction(open_chat_action)

        # View Details (Edit Contact + OMEMO keys)
        view_details_action = QAction("View Details...", self)
        view_details_action.triggered.connect(lambda: self.edit_contact_requested.emit(account_id, jid, roster_id))
        menu.addAction(view_details_action)

        menu.addSeparator()

        # Presence Subscription management
        subscription_action = QAction("Presence Subscription...", self)
        subscription_action.triggered.connect(lambda: self.manage_subscription_requested.emit(account_id, jid, roster_id))
        menu.addAction(subscription_action)

        menu.addSeparator()

        # Delete History (wipe messages, keep contact)
        delete_history_action = QAction("Delete History", self)
        delete_history_action.triggered.connect(lambda: self.delete_chat_requested.emit(account_id, jid))
        menu.addAction(delete_history_action)

        # Remove from Contacts (full deletion)
        delete_contact_action = QAction("Remove from Contacts", self)
        delete_contact_action.triggered.connect(lambda: self._on_delete_contact_clicked(account_id, jid, roster_id))
        menu.addAction(delete_contact_action)

        # Block/Unblock (management action)
        if is_blocked:
            block_action = QAction("Unblock Contact", self)
        else:
            block_action = QAction("Block Contact", self)
        block_action.triggered.connect(lambda: self.block_contact_requested.emit(account_id, jid, roster_id, is_blocked))
        menu.addAction(block_action)

        menu.addSeparator()

        # Delete & Block (nuclear option - only show when not already blocked)
        if not is_blocked:
            delete_and_block_action = QAction("Delete && Block", self)
            delete_and_block_action.triggered.connect(lambda: self.delete_and_block_requested.emit(account_id, jid, roster_id))
            font = delete_and_block_action.font()
            font.setBold(True)
            delete_and_block_action.setFont(font)
            menu.addAction(delete_and_block_action)

        # Show menu
        menu.exec_(self.contact_tree.viewport().mapToGlobal(position))

    def _on_delete_contact_clicked(self, account_id: int, jid: str, roster_id: int):
        """Handle delete contact menu click with debug logging."""
        logger.debug(f"Delete contact menu clicked: account_id={account_id}, jid={jid}, roster_id={roster_id}")
        logger.debug(f"Emitting delete_contact_requested signal...")
        self.delete_contact_requested.emit(account_id, jid, roster_id)
        logger.debug(f"Signal emitted successfully")

    def _show_muc_context_menu(self, position, data: ContactDisplayData):
        """Show context menu for a MUC room."""
        account_id = data.account_id
        room_jid = data.jid

        # Create menu
        menu = QMenu(self)

        # Open Chat (default action)
        open_chat_action = QAction("Open Chat", self)
        open_chat_action.triggered.connect(lambda: self.contact_selected.emit(account_id, room_jid))
        menu.addAction(open_chat_action)

        menu.addSeparator()

        # View Details (MUC details dialog = settings gear in header)
        view_details_action = QAction("View Details...", self)
        view_details_action.triggered.connect(lambda: self.view_muc_details_requested.emit(account_id, room_jid))
        menu.addAction(view_details_action)

        menu.addSeparator()

        # Copy Room JID
        copy_jid_action = QAction("Copy Room JID", self)
        copy_jid_action.triggered.connect(lambda: self._copy_to_clipboard(room_jid))
        menu.addAction(copy_jid_action)

        menu.addSeparator()

        # Leave Room
        leave_room_action = QAction("Leave Room...", self)
        leave_room_action.triggered.connect(lambda: self.leave_muc_requested.emit(account_id, room_jid))
        font = leave_room_action.font()
        font.setBold(True)
        leave_room_action.setFont(font)
        menu.addAction(leave_room_action)

        # Show menu
        menu.exec_(self.contact_tree.viewport().mapToGlobal(position))

    def _show_account_context_menu(self, position, data: AccountDisplayData):
        """Show context menu for an account."""
        account_id = data.account_id

        # Get account info to check connection state
        account = self.account_manager.get_account(account_id)
        is_connected = account.is_connected() if account else False

        # Create menu
        menu = QMenu(self)

        # Add Contact (matches File -> Add Contact)
        add_contact_action = QAction("Add Contact...", self)
        add_contact_action.triggered.connect(lambda: self.add_contact_requested.emit(account_id))
        menu.addAction(add_contact_action)

        # Add Group (matches File -> Add Group, renamed from "Join Room")
        add_group_action = QAction("Add Group...", self)
        add_group_action.triggered.connect(lambda: self.join_room_requested.emit(account_id))
        menu.addAction(add_group_action)

        menu.addSeparator()

        # View Details (Edit Account) - includes alias editing
        view_details_action = QAction("View Details...", self)
        view_details_action.triggered.connect(lambda: self.account_details_requested.emit(account_id))
        menu.addAction(view_details_action)

        # Copy Account JID
        copy_jid_action = QAction("Copy Account JID", self)
        copy_jid_action.triggered.connect(lambda: self._copy_to_clipboard(data.bare_jid))
        menu.addAction(copy_jid_action)

        menu.addSeparator()

        # Connect/Disconnect (moved to bottom, bold)
        if is_connected:
            connect_action = QAction("Disconnect", self)
            connect_action.triggered.connect(lambda: self.account_disconnect_requested.emit(account_id))
        else:
            connect_action = QAction("Connect", self)
            connect_action.triggered.connect(lambda: self.account_connect_requested.emit(account_id))
        font = connect_action.font()
        font.setBold(True)
        connect_action.setFont(font)
        menu.addAction(connect_action)

        # Show menu
        menu.exec_(self.contact_tree.viewport().mapToGlobal(position))

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        logger.info(f"Copied to clipboard: {text}")

    def update_call_indicator(self, account_id: int, jid: str, call_state: Optional[str]):
        """
        Update call state indicator for a contact.

        Args:
            account_id: Account ID
            jid: Contact JID
            call_state: Call state ('incoming', 'outgoing', 'active', or None to clear)
        """
        item = self._find_contact_item(account_id, jid)
        if not item:
            logger.debug(f"Contact {jid} not found for call indicator update")
            return

        data = item.data(0, Qt.UserRole)
        if not data or not isinstance(data, ContactDisplayData):
            return

        # Update data model
        data.call_state = call_state
        self._update_item_from_data(item, data)
        logger.debug(f"Updated call indicator for {jid}: {call_state}")

    def update_typing_indicator(self, account_id: int, jid: str, state: str):
        """
        Update typing indicator for a contact.

        Args:
            account_id: Account ID
            jid: Contact JID
            state: Chat state ('active', 'composing', 'paused', 'inactive', 'gone')
        """
        key = (account_id, jid)

        # Update typing state tracker
        if state == 'composing':
            self.typing_states[key] = state
        else:
            # Clear typing state for non-composing states
            self.typing_states.pop(key, None)

        # Find contact item
        item = self._find_contact_item(account_id, jid)
        if not item:
            logger.debug(f"Contact {jid} not found for typing indicator update")
            return

        data = item.data(0, Qt.UserRole)
        if not data or not isinstance(data, ContactDisplayData):
            return

        # Update data model
        data.typing = (state == 'composing')
        self._update_item_from_data(item, data)
        logger.debug(f"Updated typing indicator for {jid}: {state}")
