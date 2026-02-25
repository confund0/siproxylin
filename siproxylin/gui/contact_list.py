"""
Contact list widget for Siproxylin.

Displays roster contacts with presence indicators, grouped by account.
"""

import logging
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLabel, QLineEdit, QHBoxLayout, QMenu, QPushButton, QFrame, QToolButton, QMessageBox,
    QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt, Signal, QEvent
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
    invite_to_muc_requested = Signal(int, str)  # (account_id, room_jid)
    leave_muc_requested = Signal(int, str)  # (account_id, room_jid)

    # Account-specific signals
    account_connect_requested = Signal(int)  # (account_id)
    account_disconnect_requested = Signal(int)  # (account_id)
    account_details_requested = Signal(int)  # (account_id)
    add_contact_requested = Signal(int)  # (account_id)
    join_room_requested = Signal(int)  # (account_id)

    # Service Discovery signals (Admin Tools)
    disco_contact_requested = Signal(int, str)  # (account_id, jid)
    disco_muc_requested = Signal(int, str)  # (account_id, room_jid)
    disco_account_requested = Signal(int)  # (account_id) - queries server domain

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
        self.search_box.returnPressed.connect(self._on_search_enter)  # Handle Enter key
        search_layout.addWidget(self.search_box)
        layout.addLayout(search_layout)

        # Search results dropdown (initially hidden)
        self.contact_search_dropdown = QListWidget(self)
        self.contact_search_dropdown.setObjectName("contactSearchDropdown")
        self.contact_search_dropdown.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.contact_search_dropdown.setFixedWidth(600)  # Wider to fit long JIDs
        self.contact_search_dropdown.setMaximumHeight(300)
        self.contact_search_dropdown.setFocusPolicy(Qt.NoFocus)
        self.contact_search_dropdown.setWordWrap(False)  # Prevent wrapping
        self.contact_search_dropdown.itemClicked.connect(self._on_contact_search_selected)
        self.contact_search_dropdown.hide()

        # Install event filter on search box for key handling
        self.search_box.installEventFilter(self)

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
            "SELECT id, bare_jid, nickname FROM account WHERE enabled = 1 ORDER BY id"
        )

        for account in accounts:
            account_id = account['id']
            account_label = account['nickname'] or account['bare_jid']

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

            # Get MUC rooms from bookmarks, roster, OR conversations with messages
            # A MUC can be: bookmarked, in roster, or have messages (e.g., invitations)
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
                WHERE (
                    b.id IS NOT NULL  -- In bookmarks
                    OR r.id IS NOT NULL  -- In roster
                    OR EXISTS (  -- OR has MUC conversation with messages (invitations, etc.)
                        SELECT 1 FROM conversation c
                        JOIN content_item ci ON ci.conversation_id = c.id
                        WHERE c.account_id = ? AND c.jid_id = j.id AND c.type = 1
                    )
                  )
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
            """, (account_id, account_id, account_id))

            # Get 1-to-1 contacts: roster contacts OR conversations with messages
            muc_jids = {room['bare_jid'] for room in rooms}
            contacts = self.db.fetchall("""
                SELECT DISTINCT
                    r.id,
                    COALESCE(r.account_id, ?) as account_id,
                    j.bare_jid,
                    r.name,
                    r.subscription,
                    r.blocked
                FROM jid j
                LEFT JOIN roster r ON r.jid_id = j.id AND r.account_id = ?
                WHERE (
                    r.id IS NOT NULL  -- In roster
                    OR EXISTS (       -- OR has conversation with messages
                        SELECT 1 FROM conversation c
                        JOIN content_item ci ON ci.conversation_id = c.id
                        WHERE c.account_id = ? AND c.jid_id = j.id AND c.type = 0
                    )
                )
                ORDER BY COALESCE(r.name, j.bare_jid), j.bare_jid
            """, (account_id, account_id, account_id))

            # Filter out MUCs from contacts list
            contacts = [c for c in contacts if c['bare_jid'] not in muc_jids]

            # Add MUC rooms first
            if rooms:
                for room in rooms:
                    room_item = QTreeWidgetItem(account_item)

                    # Check if room is actually joined (not just bookmarked)
                    account = self.account_manager.get_account(account_id)
                    is_joined = (account and account.client and
                                room['bare_jid'] in account.client.joined_rooms)

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
                        unread_count=unread_by_jid.get(room['bare_jid'], 0),
                        presence='available' if is_joined else 'unavailable'
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
                        # Use 3-source priority: roster.name > contact_nickname > jid
                        if contact['name']:
                            # Roster name has highest priority
                            display_name = contact['name']
                        elif contact['bare_jid'] in account.contact_nicknames:
                            # Contact's self-set nickname (XEP-0172)
                            display_name = account.contact_nicknames[contact['bare_jid']]
                        else:
                            # Fall back to JID
                            display_name = contact['bare_jid']
                    else:
                        presence = 'unavailable'
                        display_name = contact['name'] or contact['bare_jid']

                    # Create ContactDisplayData
                    contact_data = ContactDisplayData(
                        jid=contact['bare_jid'],
                        name=display_name,
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
        """Filter contacts based on search text - show dropdown with results."""
        search_text = text.strip()

        # Hide dropdown if less than 2 characters
        if len(search_text) < 2:
            self.contact_search_dropdown.hide()
            return

        # Query database for matching contacts (no limit - XMPP rosters are small)
        query = f"%{search_text}%"

        # Search across all enabled accounts
        rows = self.db.fetchall("""
            SELECT DISTINCT
                c.id as conversation_id,
                c.account_id,
                j.bare_jid as jid,
                c.type,
                COALESCE(b.name, r.name, j.bare_jid) as display_name,
                a.bare_jid as account_jid
            FROM conversation c
            JOIN account a ON c.account_id = a.id
            JOIN jid j ON c.jid_id = j.id
            LEFT JOIN roster r ON c.account_id = r.account_id AND c.jid_id = r.jid_id
            LEFT JOIN bookmark b ON c.account_id = b.account_id AND c.jid_id = b.jid_id
            WHERE a.enabled = 1
            AND (b.name LIKE ? OR r.name LIKE ? OR j.bare_jid LIKE ?)
            ORDER BY display_name, j.bare_jid
        """, (query, query, query))

        # Populate dropdown
        self.contact_search_dropdown.clear()
        if rows:
            for row in rows:
                # Format: "Contact Name (JID) - account@server"
                contact_type = "👥" if row['type'] == 1 else "👤"
                display_text = f"{contact_type} {row['display_name']}"
                if row['jid'] != row['display_name']:
                    display_text += f" ({row['jid']})"
                display_text += f" - {row['account_jid']}"

                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, row['account_id'])
                item.setData(Qt.UserRole + 1, row['jid'])
                self.contact_search_dropdown.addItem(item)

            # Position dropdown below search box
            pos = self.search_box.mapToGlobal(self.search_box.rect().bottomLeft())
            self.contact_search_dropdown.move(pos)
            self.contact_search_dropdown.show()
            self.contact_search_dropdown.setCurrentRow(0)
        else:
            self.contact_search_dropdown.hide()

    def _on_search_enter(self):
        """Handle Enter key in search box - select current result."""
        if self.contact_search_dropdown.isVisible():
            current_item = self.contact_search_dropdown.currentItem()
            if current_item:
                self._on_contact_search_selected(current_item)

    def _on_contact_search_selected(self, item):
        """Handle contact selection from search - open chat."""
        account_id = item.data(Qt.UserRole)
        jid = item.data(Qt.UserRole + 1)

        # Hide dropdown and clear search
        self.contact_search_dropdown.hide()
        self.search_box.clear()

        # Open chat via main window callback
        # Traverse up to find the actual MainWindow (parent is QSplitter)
        widget = self
        main_window = None
        while widget:
            widget = widget.parent()
            if widget and hasattr(widget, '_on_contact_selected'):
                main_window = widget
                break

        if main_window and hasattr(main_window, '_on_contact_selected'):
            main_window._on_contact_selected(account_id, jid)

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

        # Delete Chat (removes conversation entirely)
        delete_chat_action = QAction("Delete Chat", self)
        delete_chat_action.triggered.connect(lambda: self._on_delete_chat(data))
        menu.addAction(delete_chat_action)

        menu.addSeparator()

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

        # Copy JID (utility action)
        copy_jid_action = QAction("Copy JID", self)
        copy_jid_action.triggered.connect(lambda: self._copy_to_clipboard(jid))
        menu.addAction(copy_jid_action)

        # Service Discovery (Admin Tools only)
        admin_tools_enabled = self.db.get_setting('admin_tools_enabled', default='false')
        if admin_tools_enabled.lower() in ('true', '1', 'yes'):
            disco_action = QAction("Disco (Service Discovery)...", self)
            disco_action.triggered.connect(lambda: self.disco_contact_requested.emit(account_id, jid))
            menu.addAction(disco_action)

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

    def _on_delete_chat(self, contact_data: ContactDisplayData):
        """Delete chat entirely (delete messages + remove conversation row)."""
        reply = QMessageBox.question(
            self,
            "Delete Chat",
            f"Are you sure you want to delete this chat with {contact_data.name}?\n\n"
            "This will permanently delete all messages and remove the conversation. "
            "This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Get jid_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (contact_data.jid,))
            if not jid_row:
                return

            jid_id = jid_row['id']

            # Get conversation_id (don't create if doesn't exist)
            conversation_row = self.db.fetchone("""
                SELECT id FROM conversation
                WHERE account_id = ? AND jid_id = ? AND type = 0
            """, (contact_data.account_id, jid_id))

            if not conversation_row:
                logger.info(f"No conversation found for {contact_data.jid}, nothing to delete")
                QMessageBox.information(self, "No Chat", f"No conversation found with {contact_data.name}.")
                return

            conversation_id = conversation_row['id']

            # Delete the conversation (CASCADE will delete content_items and conversation_settings)
            self.db.execute("""
                DELETE FROM conversation
                WHERE id = ?
            """, (conversation_id,))

            self.db.commit()

            logger.info(f"Deleted chat with {contact_data.jid}")
            QMessageBox.information(self, "Chat Deleted", f"Chat with {contact_data.name} has been deleted.")

            # Refresh contact list to remove from UI
            self.load_roster()

        except Exception as e:
            logger.error(f"Failed to delete chat: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Failed to delete chat: {e}")

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

        # Invite Contact to Room
        invite_action = QAction("Invite Contact...", self)
        invite_action.triggered.connect(lambda: self.invite_to_muc_requested.emit(account_id, room_jid))
        menu.addAction(invite_action)

        menu.addSeparator()

        # Copy Room JID
        copy_jid_action = QAction("Copy Room JID", self)
        copy_jid_action.triggered.connect(lambda: self._copy_to_clipboard(room_jid))
        menu.addAction(copy_jid_action)

        # Service Discovery (Admin Tools only)
        admin_tools_enabled = self.db.get_setting('admin_tools_enabled', default='false')
        if admin_tools_enabled.lower() in ('true', '1', 'yes'):
            disco_action = QAction("Disco (Service Discovery)...", self)
            disco_action.triggered.connect(lambda: self.disco_muc_requested.emit(account_id, room_jid))
            menu.addAction(disco_action)

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

        # Service Discovery (Admin Tools only)
        admin_tools_enabled = self.db.get_setting('admin_tools_enabled', default='false')
        if admin_tools_enabled.lower() in ('true', '1', 'yes'):
            disco_action = QAction("Server Disco (Service Discovery)...", self)
            disco_action.triggered.connect(lambda: self.disco_account_requested.emit(account_id))
            menu.addAction(disco_action)

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

    def eventFilter(self, obj, event):
        """Handle arrow keys and ESC for search dropdown.

        IMPORTANT: This ensures Enter/ESC go to the search field, not main window.
        Event filter intercepts events BEFORE they propagate to parent widgets.
        """
        if obj == self.search_box and event.type() == QEvent.KeyPress:
            key = event.key()

            # ESC: clear and hide
            if key == Qt.Key_Escape:
                self.contact_search_dropdown.hide()
                self.search_box.clear()
                return True  # Event handled, don't propagate to main window

            # Enter: select current result
            if key == Qt.Key_Return or key == Qt.Key_Enter:
                if self.contact_search_dropdown.isVisible():
                    current_item = self.contact_search_dropdown.currentItem()
                    if current_item:
                        self._on_contact_search_selected(current_item)
                    return True  # Event handled, don't propagate

            if self.contact_search_dropdown.isVisible():
                # Down: next result
                if key == Qt.Key_Down:
                    current = self.contact_search_dropdown.currentRow()
                    if current < self.contact_search_dropdown.count() - 1:
                        self.contact_search_dropdown.setCurrentRow(current + 1)
                    return True

                # Up: previous result
                elif key == Qt.Key_Up:
                    current = self.contact_search_dropdown.currentRow()
                    if current > 0:
                        self.contact_search_dropdown.setCurrentRow(current - 1)
                    return True

        # Let other events propagate normally
        return super().eventFilter(obj, event)
