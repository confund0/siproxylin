"""
Main application window for Siproxylin.

Layout:
- Menu bar (File, Edit, View)
- Left sidebar: Contact list
- Right panel: Chat view
- Bottom: Input area
"""

import logging
import asyncio
import base64
import subprocess
from datetime import datetime
from typing import Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QMenuBar, QMenu, QLabel, QSplitter, QDialog, QStatusBar, QMessageBox
)
from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QAction, QActionGroup

from ..db.database import get_db
from ..utils.paths import get_paths
from .log_viewer import LogViewer
from .account_dialog import AccountDialog
from .registration_wizard import RegistrationWizard
from .contact_dialog import ContactDialog
from .join_room_dialog import JoinRoomDialog
from .omemo_keys_dialog import OMEMOKeysDialog
from .dialogs.subscription_dialog import SubscriptionDialog
from .dialogs.subscription_request_dialog import SubscriptionRequestDialog
from .dialogs import IncomingCallDialog, OutgoingCallDialog
from .call_window import CallWindow
from .contact_list import ContactListWidget
from .chat_view import ChatViewWidget
from ..core import get_account_manager
from ..core.contact_manager import get_contact_manager
from ..styles.theme_manager import get_theme_manager
from ..services.notification import get_notification_service

# Import Go call service (optional - may not be available)
try:
    from drunk_call_hook import GoCallService
    GO_CALL_SERVICE_AVAILABLE = True
except ImportError:
    GO_CALL_SERVICE_AVAILABLE = False


logger = logging.getLogger('siproxylin.main_window')


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()

        self.db = get_db()
        self.paths = get_paths()
        self.account_manager = get_account_manager()
        self.contact_manager = get_contact_manager()
        self.theme_manager = get_theme_manager(config_dir=self.paths.config_dir)
        self.notification_service = get_notification_service()

        # Track open log viewer windows
        self.log_viewers = {}  # {(account_id, log_type): LogViewer}

        # Track open call windows
        self.call_windows = {}  # {session_id: CallWindow}
        self.incoming_call_dialogs = {}  # {session_id: IncomingCallDialog}
        self.outgoing_call_dialogs = {}  # {session_id: OutgoingCallDialog}
        self.call_session_map = {}  # {session_id: (account_id, jid)} for roster indicators

        # Go call service (app-level, shared by all accounts)
        self.go_call_service = None
        if GO_CALL_SERVICE_AVAILABLE:
            self.go_call_service = GoCallService(logger=logger)

        # Shutdown flag (set by signal handler to skip gRPC shutdown)
        self._signal_shutdown = False

        # Track app start time for uptime
        import time
        self.app_start_time = time.time()

        # Window setup
        self.setWindowTitle("Siproxylin")
        self.setGeometry(100, 100, 1200, 800)

        # Setup UI
        self._create_menu_bar()
        self._create_central_widget()
        self._setup_status_bar()

        # Load saved theme (or default to dark)
        self.theme_manager.load_theme(self.theme_manager.current_theme, save=False)

        # Set initial theme checkmark in menu
        current_theme = self.theme_manager.current_theme
        if current_theme in self.theme_actions:
            self.theme_actions[current_theme].setChecked(True)

        logger.debug("Main window created")

    def setup_accounts(self):
        """Setup after accounts are loaded."""
        # Start Go call service (use ensure_future - works with set but not-yet-running loop)
        if self.go_call_service:
            asyncio.ensure_future(self._start_go_call_service())

        # Start timer to refresh chat view for receipt updates every 2 seconds
        self.receipt_timer = QTimer(self)
        self.receipt_timer.timeout.connect(self._update_chat_receipts)
        self.receipt_timer.start(2000)  # Update every 2 seconds

        # Connect signals from all accounts
        for account_id, account in self.account_manager.accounts.items():
            account.connection_state_changed.connect(self._on_connection_state_changed)
            account.roster_updated.connect(self._on_roster_updated)
            account.message_received.connect(self._on_message_received)
            account.chat_state_changed.connect(self._on_chat_state_changed)
            account.presence_changed.connect(self._on_presence_changed)
            account.muc_invite_received.connect(self._on_muc_invite_received)
            account.avatar_updated.connect(self._on_avatar_updated)
            account.subscription_request_received.connect(self._on_subscription_request_received)
            account.subscription_changed.connect(self._on_subscription_changed)

            # Call signals (DrunkCALL integration)
            account.call_incoming.connect(self._on_call_incoming)
            account.call_initiated.connect(self._on_call_initiated)
            account.call_accepted.connect(self._on_call_accepted)
            account.call_terminated.connect(self._on_call_terminated)
            account.call_state_changed.connect(self._on_call_state_changed)

        # Load roster into contact list
        self.contact_list.load_roster()

        logger.debug("Setup complete")

    def _create_menu_bar(self):
        """Create menu bar with File, Edit, View menus."""
        menubar = self.menuBar()

        # =====================================================================
        # File Menu
        # =====================================================================
        file_menu = menubar.addMenu("&File")

        # File -> Add Account
        add_account_action = QAction("&Add Account...", self)
        add_account_action.setShortcut("Ctrl+Shift+A")
        add_account_action.triggered.connect(self._on_new_account)
        file_menu.addAction(add_account_action)

        # File -> Create Account (XEP-0077)
        create_account_action = QAction("&Create Account...", self)
        create_account_action.setToolTip("Register a new XMPP account using in-band registration (XEP-0077)")
        create_account_action.triggered.connect(self._on_create_account)
        file_menu.addAction(create_account_action)

        file_menu.addSeparator()

        # File -> Add Contact
        add_contact_action = QAction("Add C&ontact...", self)
        add_contact_action.setShortcut("Ctrl+N")
        add_contact_action.triggered.connect(self._on_new_contact)
        file_menu.addAction(add_contact_action)

        # File -> Add Group
        add_group_action = QAction("Add &Group...", self)
        add_group_action.triggered.connect(self._on_new_group)
        file_menu.addAction(add_group_action)

        file_menu.addSeparator()

        # File -> Settings
        settings_action = QAction("&Settings...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._on_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        # File -> Quit
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # =====================================================================
        # Edit Menu
        # =====================================================================
        self.edit_menu = menubar.addMenu("&Edit")
        self._populate_edit_menu()

        # =====================================================================
        # View Menu (with per-account submenus)
        # =====================================================================
        self.view_menu = menubar.addMenu("&View")
        self._populate_view_menu()

        # =====================================================================
        # Help Menu
        # =====================================================================
        help_menu = menubar.addMenu("&Help")

        # Help -> About
        about_action = QAction("&About...", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

        logger.debug("Menu bar created")

    def _populate_edit_menu(self):
        """Populate Edit menu with account-specific entries."""
        self.edit_menu.clear()

        # Edit -> Copy
        copy_action = QAction("&Copy", self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self._on_copy)
        self.edit_menu.addAction(copy_action)

        # Edit -> Paste
        paste_action = QAction("&Paste", self)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(self._on_paste)
        self.edit_menu.addAction(paste_action)

        self.edit_menu.addSeparator()

        # Edit -> Contacts (opens Manage Contacts dialog)
        contacts_action = QAction("&Contacts...", self)
        contacts_action.triggered.connect(self._on_manage_contacts)
        self.edit_menu.addAction(contacts_action)

        self.edit_menu.addSeparator()

        # Get all accounts from database
        accounts = self.db.fetchall("SELECT id, bare_jid, alias FROM account ORDER BY id")

        if not accounts:
            # No accounts yet
            no_accounts_action = QAction("(No accounts)", self)
            no_accounts_action.setEnabled(False)
            self.edit_menu.addAction(no_accounts_action)
        else:
            # Add menu item for each account
            for account in accounts:
                account_id = account['id']
                account_label = account['alias'] or account['bare_jid']

                edit_account_action = QAction(f"Account {account_id}: {account_label}...", self)
                edit_account_action.triggered.connect(
                    lambda checked, aid=account_id: self._on_edit_account(aid)
                )
                self.edit_menu.addAction(edit_account_action)

        logger.debug(f"Edit menu populated with {len(accounts)} accounts")

    def _populate_view_menu(self):
        """Populate View menu with account-specific submenus."""
        self.view_menu.clear()

        # View -> Font Size
        font_size_menu = self.view_menu.addMenu("Font &Size")

        increase_font_action = QAction("&Increase", self)
        increase_font_action.setShortcut("Ctrl++")
        increase_font_action.triggered.connect(self._on_increase_font)
        font_size_menu.addAction(increase_font_action)

        decrease_font_action = QAction("&Decrease", self)
        decrease_font_action.setShortcut("Ctrl+-")
        decrease_font_action.triggered.connect(self._on_decrease_font)
        font_size_menu.addAction(decrease_font_action)

        reset_font_action = QAction("&Reset", self)
        reset_font_action.setShortcut("Ctrl+0")
        reset_font_action.triggered.connect(self._on_reset_font)
        font_size_menu.addAction(reset_font_action)

        # View -> Theme (with checkmarks for active theme)
        theme_menu = self.view_menu.addMenu("&Theme")

        # Create action group for exclusive selection (checkmarks)
        theme_action_group = QActionGroup(self)
        theme_action_group.setExclusive(True)

        # Store theme actions for later reference
        self.theme_actions = {}

        light_theme_action = QAction("&Light", self)
        light_theme_action.setCheckable(True)
        light_theme_action.triggered.connect(lambda: self._on_change_theme('light'))
        theme_menu.addAction(light_theme_action)
        theme_action_group.addAction(light_theme_action)
        self.theme_actions['light'] = light_theme_action

        light_gray_theme_action = QAction("Light &Gray", self)
        light_gray_theme_action.setCheckable(True)
        light_gray_theme_action.triggered.connect(lambda: self._on_change_theme('light_gray'))
        theme_menu.addAction(light_gray_theme_action)
        theme_action_group.addAction(light_gray_theme_action)
        self.theme_actions['light_gray'] = light_gray_theme_action

        dark_theme_action = QAction("&Dark", self)
        dark_theme_action.setCheckable(True)
        dark_theme_action.triggered.connect(lambda: self._on_change_theme('dark'))
        theme_menu.addAction(dark_theme_action)
        theme_action_group.addAction(dark_theme_action)
        self.theme_actions['dark'] = dark_theme_action

        terminal_theme_action = QAction("&Terminal", self)
        terminal_theme_action.setCheckable(True)
        terminal_theme_action.triggered.connect(lambda: self._on_change_theme('terminal'))
        theme_menu.addAction(terminal_theme_action)
        theme_action_group.addAction(terminal_theme_action)
        self.theme_actions['terminal'] = terminal_theme_action

        gruvbox_theme_action = QAction("&Gruvbox", self)
        gruvbox_theme_action.setCheckable(True)
        gruvbox_theme_action.triggered.connect(lambda: self._on_change_theme('gruvbox'))
        theme_menu.addAction(gruvbox_theme_action)
        theme_action_group.addAction(gruvbox_theme_action)
        self.theme_actions['gruvbox'] = gruvbox_theme_action

        # View -> Roster
        roster_menu = self.view_menu.addMenu("&Roster")

        classic_roster_action = QAction("&Classic (Emoji)", self)
        classic_roster_action.setCheckable(True)
        classic_roster_action.setChecked(self.theme_manager.roster_mode == 'classic')
        classic_roster_action.triggered.connect(lambda: self._on_change_roster_mode('classic'))
        roster_menu.addAction(classic_roster_action)

        ascii_roster_action = QAction("&ASCII (Text-only)", self)
        ascii_roster_action.setCheckable(True)
        ascii_roster_action.setChecked(self.theme_manager.roster_mode == 'ascii')
        ascii_roster_action.triggered.connect(lambda: self._on_change_roster_mode('ascii'))
        roster_menu.addAction(ascii_roster_action)

        # Store references to toggle mutual exclusivity
        self.roster_mode_actions = {
            'classic': classic_roster_action,
            'ascii': ascii_roster_action
        }

        self.view_menu.addSeparator()

        # View -> Calls (renamed from Call Log)
        calls_action = QAction("Call&s...", self)
        calls_action.triggered.connect(self._on_view_call_log)
        self.view_menu.addAction(calls_action)

        self.view_menu.addSeparator()

        # View -> Logs (reorganized submenu)
        logs_menu = self.view_menu.addMenu("&Logs")

        # View -> Logs -> Main Log
        main_log_action = QAction("&Main Log...", self)
        main_log_action.triggered.connect(self._on_view_main_log)
        logs_menu.addAction(main_log_action)

        # View -> Logs -> XML Protocol Log
        xml_log_action = QAction("&XML Protocol Log...", self)
        xml_log_action.triggered.connect(self._on_view_xml_log)
        logs_menu.addAction(xml_log_action)

        logs_menu.addSeparator()

        # View -> Logs -> Accounts (submenu)
        accounts_log_menu = logs_menu.addMenu("&Accounts")

        # Get all accounts from database
        accounts = self.db.fetchall("SELECT id, bare_jid, alias FROM account ORDER BY id")

        if not accounts:
            # No accounts yet
            no_accounts_action = QAction("(No accounts)", self)
            no_accounts_action.setEnabled(False)
            accounts_log_menu.addAction(no_accounts_action)
        else:
            # Add app log entry for each account
            for account in accounts:
                account_id = account['id']
                account_label = account['alias'] or account['bare_jid']

                # View -> Logs -> Accounts -> {account_label}
                app_log_action = QAction(f"{account_label}", self)
                app_log_action.triggered.connect(
                    lambda checked, aid=account_id: self._on_view_app_log(aid)
                )
                accounts_log_menu.addAction(app_log_action)

        logger.debug(f"View menu populated with {len(accounts)} accounts")

    def _create_central_widget(self):
        """Create central widget with contact list and chat view."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QHBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Main splitter (contact list | chat view)
        self.splitter = QSplitter(Qt.Horizontal)

        # Left panel: Contact list
        self.contact_list = ContactListWidget()
        self.contact_list.contact_selected.connect(self._on_contact_selected)
        self.contact_list.home_requested.connect(self._on_home_requested)
        self.contact_list.call_log_requested.connect(self._on_call_log_requested)
        self.contact_list.contacts_requested.connect(self._on_contacts_requested)
        self.contact_list.settings_requested.connect(self._on_settings_requested)
        self.contact_list.edit_contact_requested.connect(self._on_edit_contact_from_roster)
        self.contact_list.view_omemo_keys_requested.connect(self._on_view_omemo_keys)
        self.contact_list.manage_subscription_requested.connect(self._on_manage_subscription)
        self.contact_list.block_contact_requested.connect(self._on_block_contact_from_roster)
        self.contact_list.delete_chat_requested.connect(self._on_delete_chat)
        self.contact_list.delete_contact_requested.connect(self._on_delete_contact_from_roster)
        self.contact_list.delete_and_block_requested.connect(self._on_delete_and_block)

        # MUC context menu signals
        self.contact_list.view_muc_details_requested.connect(self._on_view_muc_details)
        self.contact_list.leave_muc_requested.connect(self.leave_muc)

        # Account context menu signals
        self.contact_list.account_connect_requested.connect(self._on_account_connect)
        self.contact_list.account_disconnect_requested.connect(self._on_account_disconnect)
        self.contact_list.account_details_requested.connect(self._on_edit_account)  # Reuse existing method
        self.contact_list.add_contact_requested.connect(self._on_add_contact_for_account)
        self.contact_list.join_room_requested.connect(self._on_join_room_for_account)
        # Set minimum width to prevent collapsing (150px minimum)
        self.contact_list.setMinimumWidth(150)

        # Right panel: Chat view (pass self as parent for main_window reference)
        self.chat_view = ChatViewWidget(parent=self)
        self.chat_view.send_message.connect(self._on_send_message)
        self.chat_view.send_file.connect(self._on_send_file)
        self.chat_view.edit_message.connect(self._on_edit_message)
        self.chat_view.send_reply.connect(self._on_send_reply)
        self.chat_view.add_account_requested.connect(self._on_new_account)
        self.chat_view.create_account_requested.connect(self._on_create_account)
        # Set minimum width to prevent collapsing (400px minimum)
        self.chat_view.setMinimumWidth(400)

        self.splitter.addWidget(self.contact_list)
        self.splitter.addWidget(self.chat_view)

        # Prevent widgets from collapsing completely
        self.splitter.setCollapsible(0, False)  # Contact list can't collapse
        self.splitter.setCollapsible(1, False)  # Chat view can't collapse

        # Set initial sizes (contact list: 250px, chat view: rest)
        self.splitter.setSizes([250, 950])

        layout.addWidget(self.splitter)

    def _setup_status_bar(self):
        """Setup status bar with stats."""
        status_bar = self.statusBar()
        status_bar.show()

        # Create labels for each stat (font size controlled by theme)
        self.status_uptime_label = QLabel()
        self.status_accounts_label = QLabel()
        self.status_messages_label = QLabel()
        self.status_calls_label = QLabel()

        # Create separators
        sep1 = QLabel(" | ")
        sep2 = QLabel(" | ")
        sep3 = QLabel(" | ")

        # Add labels to status bar (left to right: uptime first)
        status_bar.addWidget(self.status_uptime_label)
        status_bar.addWidget(sep1)
        status_bar.addWidget(self.status_accounts_label)
        status_bar.addWidget(sep2)
        status_bar.addWidget(self.status_messages_label)
        status_bar.addWidget(sep3)
        status_bar.addWidget(self.status_calls_label)

        # Update stats initially
        self._update_status_bar_stats()

        # Setup timer for uptime updates (every 60 seconds)
        from PySide6.QtCore import QTimer
        self.status_bar_timer = QTimer(self)
        self.status_bar_timer.timeout.connect(self._update_status_bar_uptime)
        self.status_bar_timer.start(60000)  # Update every minute

    def _count_connection_states(self) -> dict:
        """Count connection states for loaded (enabled) accounts."""
        states = {'connected': 0, 'connecting': 0, 'disconnected': 0}

        for account in self.account_manager.accounts.values():
            status = account.connection._status
            if status in states:
                states[status] += 1

        return states

    def _update_status_bar_stats(self):
        """Update all status bar statistics."""
        # Get all statistics via unified API
        stats = self.db.get_global_statistics()

        # Get connection states from AccountManager (in-memory)
        conn_states = self._count_connection_states()

        # Update status bar labels
        self.status_accounts_label.setText(
            f"Accounts: {stats['accounts']['total']} total, {stats['accounts']['enabled']} enabled "
            f"({conn_states['connected']} connected)"
        )

        self.status_messages_label.setText(
            f"Messages: {stats['messages']['total']} total, "
            f"{stats['messages']['unread']} unread, {stats['messages']['unsent']} unsent"
        )

        self.status_calls_label.setText(
            f"Calls: {stats['calls']['total']} total, "
            f"{stats['calls']['incoming']} in, {stats['calls']['outgoing']} out, {stats['calls']['missed']} missed"
        )

        # Update uptime
        self._update_status_bar_uptime()

    def _update_status_bar_uptime(self):
        """Update only the uptime in status bar."""
        import time
        uptime_seconds = int(time.time() - self.app_start_time)
        hours = uptime_seconds // 3600
        minutes = (uptime_seconds % 3600) // 60
        self.status_uptime_label.setText(f"Online: {hours}h {minutes}m")

    # =========================================================================
    # Account Management Handlers
    # =========================================================================

    def _on_account_saved(self, account_id: int, enabled: bool):
        """
        Handle account save from AccountDialog.

        Args:
            account_id: Account ID that was saved
            enabled: Whether the account is enabled
        """
        logger.info(f"Account {account_id} saved (enabled={enabled})")

        # Check if this is a new account or existing
        is_new = account_id not in self.account_manager.accounts

        if is_new:
            logger.debug("Account saved successfully")
            # New account - load and connect if enabled
            if enabled:
                logger.debug(f"Loading new account {account_id}")
                account_data = self.db.fetchone("SELECT * FROM account WHERE id = ?", (account_id,))
                if account_data:
                    from ..core import XMPPAccount
                    account = XMPPAccount(account_id, dict(account_data))

                    # Connect signals
                    account.roster_updated.connect(self._on_roster_updated)
                    account.message_received.connect(self._on_message_received)
                    account.chat_state_changed.connect(self._on_chat_state_changed)
                    account.muc_invite_received.connect(self._on_muc_invite_received)
                    account.avatar_updated.connect(self._on_avatar_updated)
                    account.subscription_request_received.connect(self._on_subscription_request_received)
                    account.subscription_changed.connect(self._on_subscription_changed)

                    self.account_manager.accounts[account_id] = account
                    account.connect()
                    logger.info(f"Account {account_id} connected")
            else:
                logger.debug(f"New account {account_id} is disabled, not connecting")
        else:
            logger.debug(f"Account {account_id} updated successfully")
            # Existing account - handle enable/disable
            account = self.account_manager.get_account(account_id)
            if account:
                if enabled:
                    # Reload account settings (disconnects and reconnects if connected)
                    logger.debug(f"Reloading account {account_id}")
                    account.reload_and_reconnect()
                else:
                    # Disconnect disabled account
                    logger.debug(f"Disconnecting disabled account {account_id}")
                    account.disconnect()

        # Update UI components
        self._populate_edit_menu()
        self._populate_view_menu()
        self.contact_list.load_roster()

        logger.debug(f"UI updated after account {account_id} save")

    def _on_account_deleted(self, account_id: int):
        """
        Handle account deletion from AccountDialog.

        Args:
            account_id: Account ID that was deleted
        """
        logger.info(f"Account {account_id} deleted, cleaning up UI")

        # Disconnect and remove account from AccountManager
        account = self.account_manager.get_account(account_id)
        if account:
            logger.debug(f"Disconnecting account {account_id}")
            account.disconnect()
            del self.account_manager.accounts[account_id]

        # Clear chat view if showing deleted account
        if hasattr(self, 'current_account_id') and self.current_account_id == account_id:
            logger.debug(f"Clearing chat view (was showing deleted account {account_id})")
            self.chat_view.clear()
            self.current_account_id = None
            self.current_jid = None

        # Update UI components (same as account save)
        self._populate_edit_menu()
        self._populate_view_menu()
        self.contact_list.load_roster()

        logger.debug(f"UI cleaned up after account {account_id} deletion")

    def _on_contact_saved(self, account_id: int, jid: str, name: str, can_see_theirs: bool, they_can_see_ours: bool):
        """
        Handle contact save from ContactDialog.

        Args:
            account_id: Account ID
            jid: Contact JID
            name: Contact display name (can be empty)
            can_see_theirs: Whether to request seeing their presence
            they_can_see_ours: Whether to allow them to see our presence
        """
        logger.info(f"Contact saved: {jid} for account {account_id} (name='{name}', can_see_theirs={can_see_theirs}, they_can_see_ours={they_can_see_ours})")

        # Get account and check if connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot modify contact while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Handle contact operations via contact manager
        try:
            # Check for deletion: empty name AND both subscriptions False
            if name == "" and not can_see_theirs and not they_can_see_ours:
                # Delete contact
                logger.debug(f"Removing contact {jid}")
                asyncio.create_task(
                    self.contact_manager.remove_contact(account_id, account.client, jid)
                )
            else:
                # Update contact name (for both new and existing contacts)
                if name:
                    asyncio.create_task(
                        self.contact_manager.update_contact_name(account_id, account.client, jid, name)
                    )

                # Handle subscription changes using shared method
                self._update_subscription(account_id, jid, can_see_theirs, they_can_see_ours)

            # Refresh contact list
            self.contact_list.load_roster()

        except Exception as e:
            logger.error(f"Failed to save contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save contact:\n{e}")

    def _select_account_dialog(self, title: str, message: str, connected_only: bool = True) -> Optional[int]:
        """
        Show account selection dialog.

        Args:
            title: Dialog title
            message: Dialog message
            connected_only: If True, only show connected accounts (default: True)

        Returns:
            Selected account ID or None if cancelled
        """
        from PySide6.QtWidgets import QInputDialog

        # Build list of accounts (filter by connection if requested)
        accounts = []
        account_ids = []
        for account_id in sorted(self.account_manager.accounts.keys()):
            # Filter by connection status if needed
            if connected_only:
                account = self.account_manager.get_account(account_id)
                if not account or not account.is_connected():
                    continue

            account_data = self.db.fetchone("SELECT bare_jid, alias FROM account WHERE id = ?", (account_id,))
            if account_data:
                display_name = account_data['alias'] or account_data['bare_jid']
                accounts.append(f"Account {account_id}: {display_name}")
                account_ids.append(account_id)

        if not accounts:
            return None

        # If only one account, return it directly
        if len(accounts) == 1:
            return account_ids[0]

        # Show selection dialog
        item, ok = QInputDialog.getItem(self, title, message, accounts, 0, False)
        if ok and item:
            # Extract account ID from selection
            idx = accounts.index(item)
            return account_ids[idx]

        return None

    # =========================================================================
    # Menu Action Handlers
    # =========================================================================

    def _on_new_account(self):
        """Handle File -> New Account."""
        logger.debug("New Account requested")

        dialog = AccountDialog(parent=self)
        dialog.account_saved.connect(self._on_account_saved)
        dialog.account_deleted.connect(self._on_account_deleted)
        dialog.show()

    def _on_create_account(self):
        """Handle File -> Create Account (XEP-0077 registration wizard)."""
        logger.debug("Create Account (XEP-0077) requested")

        wizard = RegistrationWizard(parent=self)
        wizard.account_registered.connect(self._on_account_registered)
        wizard.exec()

    def _on_account_registered(self, account_id):
        """Handle account registered via wizard."""
        logger.info(f"Account {account_id} registered via wizard")

        # Reload account manager to include new account
        # load_accounts() already connects enabled accounts
        self.account_manager.load_accounts()

        # Setup signals for new account
        self.setup_accounts()

        # Refresh UI components (including Edit menu)
        self._populate_edit_menu()
        self._populate_view_menu()
        self.contact_list.load_roster()

    def _on_new_contact(self):
        """Handle File -> New Contact."""
        logger.debug("New Contact requested")

        # Check if any accounts exist
        if not self.account_manager.accounts:
            QMessageBox.warning(self, "No Accounts", "Please create an account first.")
            return

        # Let user select account (connected accounts only)
        account_id = self._select_account_dialog("Select Account", "Add contact to which account?")
        if account_id is None:
            return

        dialog = ContactDialog(account_id=account_id, parent=self)
        dialog.contact_saved.connect(self._on_contact_saved)
        if dialog.exec() == QDialog.Accepted:
            logger.debug("Contact saved successfully")

    def _on_new_group(self):
        """Handle File -> New Group (Join MUC room)."""
        logger.debug("New Group (MUC) requested")

        # Check if any accounts exist
        if not self.account_manager.accounts:
            QMessageBox.warning(self, "No Accounts", "Please create an account first.")
            return

        # Let user select account if multiple accounts
        account_id = self._select_account_dialog("Select Account", "Join room with which account?")
        if account_id is None:
            return

        # Check if selected account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot join room while offline.\n\n"
                f"Please connect the account first."
            )
            return

        dialog = JoinRoomDialog(account_id=account_id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            # Get joined room info
            room_jid = dialog.room_jid
            nick = dialog.nick
            password = dialog.password if dialog.password else None

            logger.info(f"Joining room: {room_jid} as {nick}")

            # Add room to client configuration and join
            account = self.account_manager.get_account(account_id)
            if account and account.client:
                asyncio.create_task(account.add_and_join_room(room_jid, nick, password))
                logger.debug(f"Room join initiated: {room_jid}")

                # Refresh contact list to show new room
                self.contact_list.load_roster()
            else:
                QMessageBox.warning(self, "Error", "Account not connected.")

    def _on_settings(self):
        """Handle File -> Settings."""
        logger.debug("Settings requested")
        from .settings_dialog import SettingsDialog

        # Get call bridge from first available account (call settings are app-wide)
        call_bridge = None
        for account in self.account_manager.accounts.values():
            if hasattr(account, 'call_bridge'):
                call_bridge = account.call_bridge
                break

        dialog = SettingsDialog(self, call_bridge=call_bridge)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _on_copy(self):
        """Handle Edit -> Copy."""
        pass  # Handled by Qt automatically

    def _on_paste(self):
        """Handle Edit -> Paste."""
        pass  # Handled by Qt automatically

    def _on_edit_account(self, account_id: int):
        """
        Handle Edit -> Account X.

        Args:
            account_id: Account ID to edit
        """
        logger.debug(f"Edit Account {account_id} requested")

        # Load account data
        account = self.db.fetchone("SELECT * FROM account WHERE id = ?", (account_id,))
        if not account:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Account {account_id} not found.")
            return

        # Open account dialog in edit mode
        dialog = AccountDialog(parent=self, account_data=dict(account))
        dialog.account_saved.connect(self._on_account_saved)
        dialog.account_deleted.connect(self._on_account_deleted)
        dialog.show()

    def _on_edit_bookmarks(self):
        """Handle Edit -> Bookmarks."""
        logger.debug("Edit Bookmarks requested")
        # TODO: Open bookmarks management dialog

    def _on_increase_font(self):
        """Handle View -> Font Size -> Increase."""
        logger.debug("Increase font size requested")
        self.theme_manager.increase_font_size()

    def _on_decrease_font(self):
        """Handle View -> Font Size -> Decrease."""
        logger.debug("Decrease font size requested")
        self.theme_manager.decrease_font_size()

    def _on_reset_font(self):
        """Handle View -> Font Size -> Reset."""
        logger.debug("Reset font size requested")
        self.theme_manager.reset_font_size()

    def _on_change_theme(self, theme: str):
        """
        Handle View -> Theme -> Light/Dark/Terminal/Gruvbox/Light Gray.

        Args:
            theme: Theme name ('light', 'dark', 'terminal', 'gruvbox', 'light_gray')
        """
        logger.debug(f"Change theme to '{theme}' requested")
        self.theme_manager.load_theme(theme)

        # Update checkmark in menu
        if theme in self.theme_actions:
            self.theme_actions[theme].setChecked(True)

        # Update chat bubble colors
        self.chat_view.update_theme(theme)

        # Refresh roster colors (theme-aware in ASCII mode)
        self.contact_list.refresh_display()

    def _on_change_roster_mode(self, mode: str):
        """
        Handle View -> Roster -> Classic/ASCII.

        Args:
            mode: Roster mode ('classic' or 'ascii')
        """
        logger.debug(f"Change roster mode to '{mode}' requested")
        self.theme_manager.set_roster_mode(mode)

        # Update menu checkmarks
        for mode_name, action in self.roster_mode_actions.items():
            action.setChecked(mode_name == mode)

        # Reload roster with new mode
        self.contact_list.load_roster()

    def _on_view_call_log(self):
        """Handle View -> Calls."""
        logger.debug("View Call Log requested")

        from .call_log_dialog import CallLogDialog

        # Create dialog if it doesn't exist or was closed
        if not hasattr(self, '_call_log_dialog') or not self._call_log_dialog.isVisible():
            self._call_log_dialog = CallLogDialog(parent=self)
            self._call_log_dialog.show()
        else:
            # Bring existing dialog to front
            self._call_log_dialog.raise_()
            self._call_log_dialog.activateWindow()

    def _on_view_app_log(self, account_id: int):
        """
        Handle View -> Account X -> App Log.

        Args:
            account_id: Account ID
        """
        logger.debug(f"View App Log requested for account {account_id}")

        # Check if already open
        key = (account_id, 'app')
        if key in self.log_viewers and self.log_viewers[key].isVisible():
            # Bring to front
            self.log_viewers[key].raise_()
            self.log_viewers[key].activateWindow()
            return

        # Open new log viewer
        log_path = self.paths.account_app_log_path(account_id)
        viewer = LogViewer(
            log_path=log_path,
            title=f"Account {account_id} - Application Log",
            parent=None  # Non-modal, independent window
        )
        viewer.show()

        # Track it
        self.log_viewers[key] = viewer

    def _on_view_main_log(self):
        """Handle View -> Logs -> Main Log."""
        logger.debug("View Main Log requested")

        # Check if window is already open
        key = ('global', 'main')
        if key in self.log_viewers and self.log_viewers[key].isVisible():
            self.log_viewers[key].raise_()
            self.log_viewers[key].activateWindow()
            return

        # Open new log viewer for main log
        log_path = self.paths.main_log_path()
        viewer = LogViewer(
            log_path=log_path,
            title="Main Application Log",
            parent=None  # Non-modal, independent window
        )
        viewer.show()
        self.log_viewers[key] = viewer

        logger.debug(f"Main log viewer opened: {log_path}")

    def _on_view_xml_log(self):
        """Handle View -> Logs -> XML Protocol Log."""
        logger.debug("View XML Protocol Log requested")

        # Check if already open
        key = ('global', 'xml')
        if key in self.log_viewers and self.log_viewers[key].isVisible():
            # Bring to front
            self.log_viewers[key].raise_()
            self.log_viewers[key].activateWindow()
            return

        # Open new log viewer for global XML log
        log_path = self.paths.log_dir / 'xmpp-protocol.log'
        viewer = LogViewer(
            log_path=log_path,
            title="XMPP Protocol Log (All Accounts)",
            parent=None  # Non-modal, independent window
        )
        viewer.show()

        # Track it
        self.log_viewers[key] = viewer

    def _on_contact_selected(self, account_id: int, jid: str):
        """
        Handle contact selection from contact list.

        Args:
            account_id: Account ID
            jid: Contact JID
        """
        logger.debug(f"Contact selected: {jid} (account {account_id})")
        self.chat_view.load_conversation(account_id, jid)

        # Select the contact in the roster (useful when opened from dialog)
        self.contact_list.select_contact(account_id, jid)

        # Dismiss notification for this conversation (with elegant delay)
        QTimer.singleShot(500, lambda: self.notification_service.dismiss_notification(account_id, jid))

        # Update unread indicators after opening chat
        # Use a small delay to ensure displayed markers are sent and DB is updated
        QTimer.singleShot(100, lambda: self.contact_list.update_unread_indicators(account_id, jid))

    def _on_home_requested(self):
        """Handle HOME button click - return to welcome page."""
        logger.debug("HOME button clicked - returning to welcome page")

        # Stop typing notifications timers
        self.chat_view.input_field.reset_chat_state()

        # Switch to welcome page (Page 0) - keeps state for draft preservation
        self.chat_view.stack.setCurrentIndex(0)

        # Clear roster selection
        self.contact_list.contact_tree.clearSelection()

    def _on_call_log_requested(self):
        """Handle call log button click - open View -> Calls dialog."""
        logger.debug("Call log button clicked")
        self._on_view_call_log()

    def _on_contacts_requested(self):
        """Handle contacts button click - open Contacts -> Manage Contacts dialog."""
        logger.debug("Contacts button clicked")
        self._on_manage_contacts()

    def _on_settings_requested(self):
        """Handle settings button click - open File -> Settings dialog."""
        logger.debug("Settings button clicked")
        self._on_settings()

    def _on_edit_contact_from_roster(self, account_id: int, jid: str, roster_id: int):
        """
        Handle View Details from contact context menu.
        Opens contact details dialog with OMEMO keys (same as chat header gear icon).

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
        """
        logger.debug(f"View details requested: {jid} (roster_id: {roster_id})")

        # Sync OMEMO devices to database before showing dialog (if connected)
        account = self.account_manager.get_account(account_id)
        if account and account.client:
            asyncio.create_task(self._sync_and_show_omemo_dialog(account, jid))
        else:
            # Offline - show what's in DB (devices synced from previous session)
            from .omemo_keys_dialog import OMEMOKeysDialog
            dialog = OMEMOKeysDialog(account_id, jid, self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()

    def _on_view_omemo_keys(self, account_id: int, jid: str):
        """
        Handle View OMEMO Keys from context menu.

        Args:
            account_id: Account ID
            jid: Contact JID
        """
        logger.debug(f"View OMEMO keys requested for {jid}")

        # Sync OMEMO devices to database before showing dialog
        account = self.account_manager.get_account(account_id)
        if account and account.client:
            # Sync devices for contact and own devices
            asyncio.create_task(self._sync_and_show_omemo_dialog(account, jid))
        else:
            # No connection, just show what's in DB
            dialog = OMEMOKeysDialog(account_id, jid, self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()

    def _on_manage_contacts(self):
        """Handle Contacts -> Manage Contacts."""
        logger.debug("Manage contacts requested")

        from .contacts_manager_dialog import ContactsManagerDialog

        dialog = ContactsManagerDialog(show_only_blocked=False, parent=self)
        dialog.contact_modified.connect(self.contact_list.refresh)
        dialog.delete_contact_requested.connect(self._on_delete_contact_from_roster)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _on_view_blocked_contacts(self):
        """Handle Contacts -> View Blocked Contacts."""
        logger.debug("View blocked contacts requested")

        from .contacts_manager_dialog import ContactsManagerDialog

        dialog = ContactsManagerDialog(show_only_blocked=True, parent=self)
        dialog.contact_modified.connect(self.contact_list.refresh)
        dialog.delete_contact_requested.connect(self._on_delete_contact_from_roster)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _on_about(self):
        """Handle Help -> About."""
        logger.debug("About dialog requested")

        from .about_dialog import AboutDialog

        dialog = AboutDialog(parent=self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    async def _sync_and_show_omemo_dialog(self, account, jid: str):
        """Sync OMEMO devices and show dialog."""
        try:
            # Sync contact's devices (normalize JID to lowercase for OMEMO)
            await account.sync_omemo_devices_to_db(jid.lower())

            # Sync our own devices (normalize JID to lowercase for OMEMO)
            own_jid = account.account_data['bare_jid'].lower()
            await account.sync_omemo_devices_to_db(own_jid)

        except Exception as e:
            logger.error(f"Failed to sync OMEMO devices: {e}")

        # Show dialog with synced data
        # Use .show() instead of .exec() to avoid blocking asyncio event loop
        dialog = OMEMOKeysDialog(account.account_id, jid, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _on_block_contact_from_roster(self, account_id: int, jid: str, roster_id: int, currently_blocked: bool):
        """
        Handle Block/Unblock Contact from context menu.

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
            currently_blocked: Current blocked status
        """
        action = "Unblock" if currently_blocked else "Block"
        logger.debug(f"{action} contact requested: {jid}")

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

        # Confirm action
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
            # Send XEP-0191 block/unblock to server
            if currently_blocked:
                asyncio.create_task(account.client.unblock_contact(jid))
            else:
                asyncio.create_task(account.client.block_contact(jid))

            logger.debug(f"Sent {action.lower()} IQ for {jid}")

            # Update database
            new_blocked = 0 if currently_blocked else 1
            self.db.execute(
                "UPDATE roster SET blocked = ? WHERE id = ?",
                (new_blocked, roster_id)
            )
            self.db.commit()

            # Refresh contact list to update UI
            self.contact_list.refresh()

            # If this contact's chat is currently open, update its UI state
            if self.chat_view.current_account_id == account_id and self.chat_view.current_jid == jid:
                self.chat_view.update_blocked_status(new_blocked == 1)

            QMessageBox.information(self, "Success", f"Contact {action.lower()}ed successfully")

        except Exception as e:
            logger.error(f"Failed to {action.lower()} contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to {action.lower()} contact:\n{e}")

    def _on_delete_chat(self, account_id: int, jid: str):
        """
        Handle Delete History from context menu.
        Deletes all messages (local DB + server MAM), keeps contact.

        Args:
            account_id: Account ID
            jid: Contact JID
        """
        logger.debug(f"Delete history requested: {jid}")

        # Confirm deletion
        reply = QMessageBox.warning(
            self,
            "Delete History",
            f"Delete all message history with '{jid}'?\n\n"
            f"This will:\n"
            f"- DELETE all local messages\n"
            f"- Try to delete from server (MAM)\n"
            f"- Keep contact in roster\n"
            f"- Keep chat window open (empty)\n\n"
            f"This action cannot be undone!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Get JID ID for message deletion
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
            jid_id = jid_row['id'] if jid_row else None

            if not jid_id:
                QMessageBox.warning(self, "Error", f"Contact {jid} not found in database")
                return

            # Delete message history (local)
            deleted_msgs = self.db.execute(
                "DELETE FROM message WHERE account_id = ? AND counterpart_id = ?",
                (account_id, jid_id)
            ).rowcount

            # Delete file transfers (attachments)
            deleted_files = self.db.execute(
                "DELETE FROM file_transfer WHERE account_id = ? AND counterpart_id = ?",
                (account_id, jid_id)
            ).rowcount

            self.db.commit()
            logger.info(f"Deleted {deleted_msgs} messages and {deleted_files} file transfers for {jid}")

            # TODO: Try to delete from server MAM (XEP-0313, best effort)
            # Most servers don't support this yet

            # Clear the chat view if currently open
            if hasattr(self, 'chat_view') and self.chat_view.current_account_id == account_id and self.chat_view.current_jid == jid:
                self.chat_view.clear()
                # Reload empty conversation
                self.chat_view.load_conversation(account_id, jid)
                logger.debug(f"Chat view cleared and reloaded (empty) for {jid}")

            QMessageBox.information(
                self,
                "Success",
                f"Deleted {deleted_msgs} messages and {deleted_files} file transfers with '{jid}'\n\n"
                f"Contact remains in roster.\n"
                f"Note: Server MAM deletion not yet implemented."
            )
            logger.info(f"History deleted for {jid} ({deleted_msgs} messages, {deleted_files} files)")

        except Exception as e:
            logger.error(f"Failed to delete history: {e}")
            QMessageBox.critical(self, "Error", f"Failed to delete history:\n{e}")

    def _on_delete_contact_from_roster(self, account_id: int, jid: str, roster_id: int):
        """
        Handle Delete Contact from context menu.

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
        """
        logger.debug(f"Delete contact requested: {jid}")

        # Check if account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot remove contact while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Confirm deletion
        reply = QMessageBox.warning(
            self,
            "Delete Contact",
            f"Are you sure you want to delete '{jid}'?\n\n"
            f"This will:\n"
            f"- Remove contact from your roster\n"
            f"- Revoke presence subscriptions\n"
            f"- DELETE ALL MESSAGE HISTORY (local)\n\n"
            f"This action cannot be undone!",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Get JID ID for message deletion
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
            jid_id = jid_row['id'] if jid_row else None

            # Send roster removal IQ to server
            asyncio.create_task(account.client.remove_roster_item(jid))
            logger.debug(f"Sent roster removal IQ for {jid}")

            # Delete message history (local)
            if jid_id:
                deleted_msgs = self.db.execute(
                    "DELETE FROM message WHERE account_id = ? AND counterpart_id = ?",
                    (account_id, jid_id)
                ).rowcount
                deleted_files = self.db.execute(
                    "DELETE FROM file_transfer WHERE account_id = ? AND counterpart_id = ?",
                    (account_id, jid_id)
                ).rowcount
                logger.debug(f"Deleted {deleted_msgs} messages and {deleted_files} file transfers for {jid}")

            # Delete from roster
            self.db.execute("DELETE FROM roster WHERE id = ?", (roster_id,))
            self.db.commit()

            # Close chat window if open
            if hasattr(self, 'chat_view') and self.chat_view.current_account_id == account_id and self.chat_view.current_jid == jid:
                self.chat_view.clear()

            self.contact_list.refresh()
            QMessageBox.information(self, "Success", f"Contact '{jid}' and all message history deleted")
            logger.info(f"Contact {jid} removed from roster (history deleted)")

        except Exception as e:
            logger.error(f"Failed to remove contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to remove contact:\n{e}")

    def _on_delete_and_block(self, account_id: int, jid: str, roster_id: int):
        """
        Handle Delete & Block from context menu.
        Nuclear option: Delete all history + Block + Remove from roster.

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
        """
        logger.debug(f"Delete & Block requested: {jid}")

        # Check if account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot delete & block while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Confirm deletion with severe warning
        reply = QMessageBox.warning(
            self,
            "Delete & Block Contact",
            f"⚠️ NUCLEAR OPTION ⚠️\n\n"
            f"This will IMMEDIATELY:\n"
            f"- DELETE ALL MESSAGE HISTORY (local)\n"
            f"- BLOCK '{jid}' (XEP-0191)\n"
            f"- REMOVE from roster\n"
            f"- REVOKE presence subscriptions\n\n"
            f"This action CANNOT be undone!\n\n"
            f"Are you absolutely sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Get JID ID for message deletion
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
            jid_id = jid_row['id'] if jid_row else None

            # 1. Block contact (XEP-0191)
            asyncio.create_task(account.client.block_contact(jid))
            logger.debug(f"Sent block IQ for {jid}")

            # 2. Remove from roster
            asyncio.create_task(account.client.remove_roster_item(jid))
            logger.debug(f"Sent roster removal IQ for {jid}")

            # 3. Delete message history (local)
            deleted_msgs = 0
            deleted_files = 0
            if jid_id:
                deleted_msgs = self.db.execute(
                    "DELETE FROM message WHERE account_id = ? AND counterpart_id = ?",
                    (account_id, jid_id)
                ).rowcount
                deleted_files = self.db.execute(
                    "DELETE FROM file_transfer WHERE account_id = ? AND counterpart_id = ?",
                    (account_id, jid_id)
                ).rowcount
                logger.debug(f"Deleted {deleted_msgs} messages and {deleted_files} file transfers for {jid}")

            # 4. Update roster DB (mark as blocked + delete)
            self.db.execute("UPDATE roster SET blocked = 1 WHERE id = ?", (roster_id,))
            self.db.execute("DELETE FROM roster WHERE id = ?", (roster_id,))
            self.db.commit()

            # 5. Close chat window if open
            if hasattr(self, 'chat_view') and self.chat_view.current_account_id == account_id and self.chat_view.current_jid == jid:
                self.chat_view.clear()

            # 6. Refresh UI
            self.contact_list.refresh()

            QMessageBox.information(
                self,
                "Success",
                f"Contact '{jid}' has been:\n"
                f"- Blocked (XEP-0191)\n"
                f"- Removed from roster\n"
                f"- History deleted ({deleted_msgs} messages, {deleted_files} files)"
            )
            logger.info(f"DELETE & BLOCK complete for {jid}: blocked, removed, history deleted")

        except Exception as e:
            logger.error(f"Failed to delete & block contact: {e}")
            QMessageBox.critical(self, "Error", f"Failed to delete & block contact:\n{e}")

    def _on_view_muc_details(self, account_id: int, room_jid: str):
        """
        Handle View Details for MUC from context menu.

        Args:
            account_id: Account ID
            room_jid: Room JID
        """
        logger.debug(f"View MUC details requested: {room_jid}")

        # Import here to avoid circular imports
        from .muc_details_dialog import MUCDetailsDialog

        dialog = MUCDetailsDialog(
            account_id=account_id,
            room_jid=room_jid,
            parent=self
        )
        # Connect dialog's leave signal to our centralized leave_muc method
        dialog.leave_room_requested.connect(self.leave_muc)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def leave_muc(self, account_id: int, room_jid: str):
        """
        Leave a MUC room (centralized handler for all leave operations).

        Handles:
        - User confirmation
        - Sending XMPP leave
        - Removing bookmark from server
        - Removing bookmark from database
        - Updating UI (contact list, chat view)

        Args:
            account_id: Account ID
            room_jid: Room JID
        """
        logger.debug(f"Leave MUC requested: {room_jid}")

        # Check if account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot leave room while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Get room name for confirmation dialog
        room_info = self.db.fetchone("""
            SELECT b.name FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account_id, room_jid))

        room_name = room_info['name'] if (room_info and room_info['name']) else room_jid

        # Confirm leaving
        reply = QMessageBox.question(
            self,
            "Leave Room",
            f"Leave room '{room_name}'?\n\n"
            f"This will remove it from your bookmarks.\n"
            f"Message history will be preserved.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Leave room via XMPP
            account.client.leave_room(room_jid)
            logger.debug(f"Sent leave room request for {room_jid}")

            # Remove bookmark from server (XEP-0402)
            asyncio.create_task(account.client.remove_bookmark(room_jid))
            logger.debug(f"Syncing bookmark removal to server: {room_jid}")

            # Remove bookmark and roster entry from local database
            # (Some clients add MUCs to server roster, so clean both tables)
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if jid_row:
                jid_id = jid_row['id']

                # Delete from bookmark table
                self.db.execute("DELETE FROM bookmark WHERE account_id = ? AND jid_id = ?",
                               (account_id, jid_id))

                # Delete from roster table (if present)
                self.db.execute("DELETE FROM roster WHERE account_id = ? AND jid_id = ?",
                               (account_id, jid_id))

                self.db.commit()
                logger.debug(f"Removed MUC from bookmark and roster tables: {room_jid}")

            # Refresh contact list to remove MUC entry
            self.contact_list.refresh()

            # Close chat view if currently viewing this room
            if self.chat_view.current_account_id == account_id and self.chat_view.current_jid == room_jid:
                self.chat_view.clear()

            # Log to both main logger and account-specific app logger
            logger.info(f"Left room {room_jid} and removed bookmark")
            if account.app_logger:
                account.app_logger.info(f"Left room '{room_name}' ({room_jid})")

        except Exception as e:
            logger.error(f"Failed to leave room: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Failed to leave room:\n{e}")

    def _on_account_connect(self, account_id: int):
        """Handle Connect from account context menu."""
        logger.debug(f"Connect requested for account {account_id}")

        account = self.account_manager.get_account(account_id)
        if not account:
            QMessageBox.warning(self, "Error", f"Account {account_id} not found")
            return

        if account.is_connected():
            logger.warning(f"Account {account_id} is already connected")
            return

        try:
            account.connect()
            # Refresh after a short delay to allow connection event to complete
            QTimer.singleShot(1000, self.contact_list.refresh)
            logger.debug(f"Account {account_id} connecting...")
        except Exception as e:
            logger.error(f"Failed to connect account {account_id}: {e}")
            QMessageBox.critical(self, "Error", f"Failed to connect:\n{e}")

    def _on_account_disconnect(self, account_id: int):
        """Handle Disconnect from account context menu."""
        logger.debug(f"Disconnect requested for account {account_id}")

        account = self.account_manager.get_account(account_id)
        if not account:
            QMessageBox.warning(self, "Error", f"Account {account_id} not found")
            return

        if not account.is_connected():
            logger.warning(f"Account {account_id} is already disconnected")
            return

        try:
            account.disconnect()
            # Refresh after a short delay to allow disconnection event to complete
            QTimer.singleShot(500, self.contact_list.refresh)
            logger.debug(f"Account {account_id} disconnect initiated")
        except Exception as e:
            logger.error(f"Failed to disconnect account {account_id}: {e}")
            QMessageBox.critical(self, "Error", f"Failed to disconnect:\n{e}")

    def _on_add_contact_for_account(self, account_id: int):
        """Handle Add Contact for specific account from context menu."""
        logger.debug(f"Add Contact requested for account {account_id}")

        # Check if selected account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot add contact while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Open contact dialog with pre-selected account
        dialog = ContactDialog(account_id=account_id, parent=self)
        dialog.contact_saved.connect(self._on_contact_saved)
        if dialog.exec() == QDialog.Accepted:
            logger.debug("Contact saved successfully")

    def _on_join_room_for_account(self, account_id: int):
        """Handle Add Group for specific account from context menu."""
        logger.debug(f"Add Group requested for account {account_id}")

        # Check if selected account is connected
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Perform Operation",
                f"Cannot add group while offline.\n\n"
                f"Please connect the account first."
            )
            return

        # Open join room dialog with pre-selected account
        dialog = JoinRoomDialog(account_id=account_id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            # Get joined room info
            room_jid = dialog.room_jid
            nick = dialog.nick
            password = dialog.password if dialog.password else None

            logger.debug(f"Joining room: {room_jid} as {nick}")

            # Add room to client configuration and join
            if account and account.client:
                asyncio.create_task(account.add_and_join_room(room_jid, nick, password))
                logger.debug(f"Room join initiated: {room_jid}")

                # Refresh contact list to show new room
                self.contact_list.load_roster()
            else:
                QMessageBox.warning(self, "Error", "Account not connected.")

    @Slot(int)
    @Slot(int, str)
    def _on_connection_state_changed(self, account_id: int, state: str):
        """
        Handle connection state change signal from account.

        Args:
            account_id: Account ID whose connection state changed
            state: New connection state ('connecting'|'connected'|'disconnected'|'error')
        """
        logger.info(f"Connection state changed for account {account_id}: {state}")
        # Refresh contact list to update account connection indicator
        self.contact_list.refresh()
        # Update status bar to reflect new connection state
        self._update_status_bar_stats()

    def _on_roster_updated(self, account_id: int):
        """
        Handle roster update signal from account.

        Args:
            account_id: Account ID whose roster was updated
        """
        logger.debug(f"Roster updated for account {account_id}, refreshing contact list")
        self.contact_list.refresh()

    @Slot(int, str, bool)
    def _on_message_received(self, account_id: int, from_jid: str, is_marker: bool = False):
        """
        Handle incoming message signal from account.

        Args:
            account_id: Account ID
            from_jid: Sender JID
            is_marker: True if marker/receipt update, False if actual new message
        """
        event_type = "marker/receipt" if is_marker else "message"
        logger.debug(f"{event_type.capitalize()} received from {from_jid} on account {account_id}")

        # Check if this message is for the currently open chat
        is_current_chat = (self.chat_view.current_account_id == account_id and
                          self.chat_view.current_jid == from_jid)

        if is_current_chat:
            # Refresh the chat view
            # Only send markers for actual new messages, not for marker/receipt updates
            self.chat_view.refresh(send_markers=(not is_marker))
            logger.debug(f"Chat view refreshed for {event_type}")

        # Update unread indicators in contact list (always, even if chat is open)
        self.contact_list.update_unread_indicators(account_id, from_jid)

        # Update status bar stats for actual new messages (not marker/receipt updates)
        if not is_marker:
            self._update_status_bar_stats()

        # Send OS notification for actual new messages (not if chat is open or if it's a marker)
        if not is_marker and not is_current_chat:
            self._send_os_notification(account_id, from_jid)

    def _send_os_notification(self, account_id: int, from_jid: str):
        """
        Send OS notification for new message.

        Args:
            account_id: Account ID
            from_jid: Sender JID
        """
        try:
            # Get sender display name
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (from_jid,))
            if not jid_row:
                logger.warning(f"Cannot send notification: JID {from_jid} not in database")
                return

            jid_id = jid_row['id']

            # Check if notifications are enabled for this conversation
            # Determine conversation type (0=chat, 1=groupchat/MUC)
            # For now, assume 0 (1-1 chat) unless we detect MUC
            # TODO: Properly detect MUC conversations (check bookmark or conversation table)
            conv_type = 0  # Default to 1-1 chat

            # Get or create conversation
            conversation_id = self.db.get_or_create_conversation(account_id, jid_id, conv_type)

            # Check notification setting
            conv = self.db.fetchone("""
                SELECT notification FROM conversation WHERE id = ?
            """, (conversation_id,))

            if conv and conv['notification'] == 0:
                logger.debug(f"Notifications disabled for {from_jid}, skipping OS notification")
                return

            # Try to get display name from roster
            roster_row = self.db.fetchone("""
                SELECT name FROM roster
                WHERE account_id = ? AND jid_id = ?
            """, (account_id, jid_id))

            if roster_row and roster_row['name']:
                sender_name = roster_row['name']
            else:
                # Fall back to bare JID
                sender_name = from_jid

            # Get most recent message from this sender
            message_row = self.db.fetchone("""
                SELECT body FROM message
                WHERE account_id = ? AND counterpart_id = ? AND direction = 0
                ORDER BY time DESC
                LIMIT 1
            """, (account_id, jid_id))

            if message_row and message_row['body']:
                message_body = message_row['body']
            else:
                message_body = "New message"

            # Send notification via notification service
            # Get icon path (vodka bottle logo) - works in both dev and AppImage
            import os
            icon_path = os.path.join(
                os.path.dirname(__file__),
                "..", "resources", "icons", "siproxylin.svg"
            )
            # Resolve to absolute path for notify-send
            icon_path = os.path.abspath(icon_path)

            self.notification_service.send_notification(
                account_id=account_id,
                jid=from_jid,
                title=sender_name,
                body=message_body,
                icon=icon_path
            )

            logger.debug(f"OS notification sent for message from {from_jid}")

        except Exception as e:
            logger.error(f"Failed to send OS notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _send_call_os_notification(self, account_id: int, from_jid: str, media_types: list):
        """
        Send OS notification for incoming call.

        Args:
            account_id: Account ID
            from_jid: Caller JID
            media_types: List of media types (['audio'] or ['audio', 'video'])
        """
        try:
            # Get caller display name from roster (same pattern as _send_os_notification)
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (from_jid,))
            if not jid_row:
                logger.warning(f"Cannot send call notification: JID {from_jid} not in database")
                caller_name = from_jid  # Fallback to JID
            else:
                jid_id = jid_row['id']

                # Try to get display name from roster
                roster_row = self.db.fetchone("""
                    SELECT name FROM roster
                    WHERE account_id = ? AND jid_id = ?
                """, (account_id, jid_id))

                if roster_row and roster_row['name']:
                    caller_name = roster_row['name']
                else:
                    # Fall back to bare JID
                    caller_name = from_jid

            # Get icon path (vodka bottle logo) - works in both dev and AppImage
            import os
            icon_path = os.path.join(
                os.path.dirname(__file__),
                "..", "resources", "icons", "siproxylin.svg"
            )
            # Resolve to absolute path for notify-send
            icon_path = os.path.abspath(icon_path)

            # Send notification via notification service
            self.notification_service.send_call_notification(
                account_id=account_id,
                jid=from_jid,
                caller_name=caller_name,
                media_types=media_types,
                icon=icon_path
            )

            logger.debug(f"OS notification sent for incoming call from {from_jid}")

        except Exception as e:
            logger.error(f"Failed to send call OS notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _send_missed_call_notification(self, account_id: int, from_jid: str):
        """
        Send OS notification for missed call (timeout).

        This replaces the ringing notification with a persistent missed call notification.

        Args:
            account_id: Account ID
            from_jid: Caller JID
        """
        try:
            # Get caller display name from roster
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (from_jid,))
            if not jid_row:
                logger.warning(f"Cannot send missed call notification: JID {from_jid} not in database")
                caller_name = from_jid  # Fallback to JID
            else:
                jid_id = jid_row['id']

                # Try to get display name from roster
                roster_row = self.db.fetchone("""
                    SELECT name FROM roster
                    WHERE account_id = ? AND jid_id = ?
                """, (account_id, jid_id))

                if roster_row and roster_row['name']:
                    caller_name = roster_row['name']
                else:
                    # Fall back to bare JID
                    caller_name = from_jid

            # Get icon path
            import os
            icon_path = os.path.join(
                os.path.dirname(__file__),
                "..", "resources", "icons", "siproxylin.svg"
            )
            icon_path = os.path.abspath(icon_path)

            # Send missed call notification (uses call_notification_ids, replaces ringing notification)
            # media_types = ['missed'] to indicate missed call
            self.notification_service.send_call_notification(
                account_id=account_id,
                jid=from_jid,
                caller_name=caller_name,
                media_types=['missed'],  # Special marker for missed call
                icon=icon_path
            )

            logger.debug(f"OS notification sent for missed call from {from_jid}")

        except Exception as e:
            logger.error(f"Failed to send missed call notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _on_chat_state_changed(self, account_id: int, from_jid: str, state: str):
        """
        Handle chat state change (typing indicators).

        Args:
            account_id: Account ID
            from_jid: Contact JID
            state: Chat state ('active', 'composing', 'paused', 'inactive', 'gone')
        """
        logger.debug(f"Chat state from {from_jid}: {state}")

        # Update typing indicator in contact list
        self.contact_list.update_typing_indicator(account_id, from_jid, state)

        # Also update typing indicator in chat header if this is the current chat
        if (self.chat_view.current_account_id == account_id and
            self.chat_view.current_jid == from_jid):
            self.chat_view.update_typing_indicator(state)

    def _on_presence_changed(self, account_id: int, jid: str, presence: str):
        """
        Handle presence change (contact status updates).

        Args:
            account_id: Account ID
            jid: Contact JID
            presence: Presence show value ('available', 'away', 'xa', 'dnd', 'unavailable')
        """
        logger.debug(f"Presence change for {jid}: {presence}")

        # Update presence indicator in contact list (event-driven, not polling!)
        self.contact_list.update_presence_single(account_id, jid, presence)

    @Slot(int, str)
    def _on_avatar_updated(self, account_id: int, jid: str):
        """
        Handle avatar update signal from account.

        Args:
            account_id: Account ID
            jid: JID whose avatar was updated
        """
        logger.debug(f"Avatar updated for {jid} on account {account_id}")

        # If this is the currently open chat, refresh the avatar
        if (self.chat_view.current_account_id == account_id and
            self.chat_view.current_jid == jid):
            logger.debug(f"Refreshing avatar for current chat: {jid}")
            # Invalidate cache for this JID
            from ..utils.avatar import get_avatar_cache
            get_avatar_cache().invalidate(jid)
            # Refresh avatar display in header
            self.chat_view.header._update_avatar()

    def _on_muc_invite_received(self, account_id: int, room_jid: str, inviter_jid: str, reason: str, password: str):
        """
        Handle MUC invitation.

        Args:
            account_id: Account ID
            room_jid: MUC room JID
            inviter_jid: JID of person who sent the invite
            reason: Invitation reason (may be empty)
            password: Room password (may be empty)
        """
        logger.info(f"MUC invite received: {room_jid} from {inviter_jid} (account {account_id})")

        # Extract room name from JID localpart (before @)
        room_name = room_jid.split('@')[0] if '@' in room_jid else room_jid

        # Build invitation message
        invite_msg = f"{inviter_jid} invited you to join:\n\n{room_name} ({room_jid})"
        if reason:
            invite_msg += f"\n\nReason: {reason}"
        if password:
            invite_msg += f"\n\n(Room is password protected)"

        # Show dialog
        reply = QMessageBox.question(
            self,
            "MUC Invitation",
            invite_msg + "\n\nDo you want to join this room?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            # Get account
            account = self.account_manager.get_account(account_id)
            if not account:
                QMessageBox.warning(self, "Error", "Account not found.")
                return

            # Check if account is connected
            if not account.is_connected():
                QMessageBox.warning(
                    self,
                    "Cannot Add Group",
                    "Cannot add group while offline.\n\n"
                    "Please connect the account first."
                )
                return

            # Get account nickname for MUC (with fallbacks)
            account_data = self.db.fetchone("SELECT alias, bare_jid FROM account WHERE id = ?", (account_id,))
            if account_data and account_data['alias']:
                nick = account_data['alias']
            elif account_data and account_data['bare_jid']:
                # Fallback: use localpart of JID
                nick = account_data['bare_jid'].split('@')[0]
            else:
                nick = 'User'

            # Save bookmark and trigger join (all sync, delegate async work)
            try:
                # Get or create JID entry
                jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
                if jid_row:
                    jid_id = jid_row['id']
                else:
                    cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (room_jid,))
                    jid_id = cursor.lastrowid

                # Store bookmark locally (use JID as name for now, will be updated after join)
                encoded_password = base64.b64encode(password.encode()).decode() if password else None
                self.db.execute("""
                    INSERT INTO bookmark (account_id, jid_id, name, nick, password, autojoin)
                    VALUES (?, ?, ?, ?, ?, 1)
                    ON CONFLICT (account_id, jid_id) DO UPDATE SET
                        name = excluded.name,
                        nick = excluded.nick,
                        password = excluded.password,
                        autojoin = excluded.autojoin
                """, (account_id, jid_id, room_jid, nick, encoded_password))
                self.db.commit()
                logger.debug(f"Bookmark saved for {room_jid}")

                # Refresh roster to show new bookmark
                self.contact_list.load_roster()

                # Defer async operations to escape XMPP callback context
                # Using QTimer.singleShot(0) to queue for next event loop iteration
                QTimer.singleShot(0, lambda: self._execute_room_join_from_invite(
                    account_id, room_jid, nick, password
                ))
                logger.debug(f"Queued room join for next event loop: {room_jid}")

            except Exception as e:
                logger.error(f"Failed to save bookmark: {e}")
                QMessageBox.critical(self, "Error", f"Failed to join room: {e}")

    def _execute_room_join_from_invite(self, account_id: int, room_jid: str, nick: str, password: str):
        """
        Execute room join operations after escaping XMPP callback context.
        Called via QTimer.singleShot(0) to defer to next event loop iteration.

        Args:
            account_id: Account ID
            room_jid: MUC room JID
            nick: Nickname to use
            password: Room password (may be empty string)
        """
        logger.debug(f"Executing deferred room join: {room_jid}")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account or not account.client:
            logger.error(f"Cannot join room - account {account_id} not available")
            QMessageBox.warning(self, "Error", "Account not available.")
            return

        # Check if still connected
        if not account.is_connected():
            logger.error(f"Cannot join room - account {account_id} disconnected")
            QMessageBox.warning(self, "Error", "Account disconnected. Room will auto-join on next connection.")
            return

        try:
            # Sync bookmark to server (XEP-0402)
            asyncio.create_task(
                account.client.add_bookmark(
                    jid=room_jid,
                    name=room_jid,  # Will be updated via disco#info after join
                    nick=nick,
                    password=password,
                    autojoin=True
                )
            )
            logger.debug(f"Syncing bookmark to server: {room_jid}")

            # Join the room immediately
            asyncio.create_task(account.add_and_join_room(room_jid, nick, password))
            logger.debug(f"Joining room from invite: {room_jid}")

        except Exception as e:
            logger.error(f"Failed to join room: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Error", f"Failed to join room: {e}")

    def _on_manage_subscription(self, account_id: int, jid: str, roster_id: int):
        """
        Handle subscription management request from context menu.

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
        """
        logger.debug(f"Opening subscription management dialog for {jid}")

        # Show subscription dialog
        dialog = SubscriptionDialog(account_id, jid, roster_id, parent=self)
        dialog.subscription_changed.connect(self._on_subscription_dialog_changed)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _update_subscription(self, account_id: int, jid: str, can_see_theirs: bool, they_can_see_ours: bool):
        """
        Update presence subscription for a contact (shared method for both dialogs).

        Args:
            account_id: Account ID
            jid: Contact JID
            can_see_theirs: Whether we want to see their presence
            they_can_see_ours: Whether they can see our presence

        Returns:
            bool: True if successful, False otherwise
        """
        logger.debug(f"Updating subscription for {jid}: see_theirs={can_see_theirs}, they_see_ours={they_can_see_ours}")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            QMessageBox.warning(self, "Error", "Account not found.")
            return False

        # Check connection
        if not account.is_connected():
            QMessageBox.warning(
                self,
                "Cannot Update Subscription",
                "Cannot update subscription while offline.\n\nPlease connect the account first."
            )
            return False

        # Get current subscription state from boolean fields
        roster_row = self.db.fetchone(
            "SELECT we_see_their_presence, they_see_our_presence FROM roster WHERE account_id = ? AND jid_id = (SELECT id FROM jid WHERE bare_jid = ?)",
            (account_id, jid)
        )
        current_can_see = bool(roster_row['we_see_their_presence']) if roster_row else False
        current_they_see = bool(roster_row['they_see_our_presence']) if roster_row else False

        try:
            # Handle changes for "I can see their presence"
            if can_see_theirs and not current_can_see:
                # Send subscribe request
                asyncio.create_task(account.request_subscription(jid))
                logger.debug(f"Sent subscription request to {jid}")
            elif not can_see_theirs and current_can_see:
                # Cancel our subscription
                asyncio.create_task(account.cancel_subscription(jid))
                logger.debug(f"Cancelled subscription to {jid}")

            # Handle changes for "They can see my presence"
            if they_can_see_ours and not current_they_see:
                # Send subscription approval/pre-approval (RFC 6121 §3.4)
                asyncio.create_task(account.approve_subscription(jid))
                logger.debug(f"Sent subscription approval/pre-approval for {jid}")
            elif not they_can_see_ours and current_they_see:
                # Revoke their subscription
                asyncio.create_task(account.revoke_subscription(jid))
                logger.debug(f"Revoked subscription for {jid}")

            return True

        except Exception as e:
            logger.error(f"Failed to update subscription: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update subscription: {e}")
            return False

    def _on_subscription_dialog_changed(self, account_id: int, jid: str, can_see_theirs: bool, they_can_see_ours: bool):
        """
        Handle subscription changes from SubscriptionDialog.

        Args:
            account_id: Account ID
            jid: Contact JID
            can_see_theirs: Whether we want to see their presence
            they_can_see_ours: Whether they can see our presence
        """
        self._update_subscription(account_id, jid, can_see_theirs, they_can_see_ours)

    def _on_subscription_request_received(self, account_id: int, from_jid: str):
        """
        Handle incoming subscription request.

        Args:
            account_id: Account ID
            from_jid: JID requesting subscription
        """
        logger.info(f"Subscription request from {from_jid} on account {account_id}")

        # Defer dialog to avoid asyncio reentrancy issues (same as MUC invites)
        def show_dialog():
            # Show approval dialog
            dialog = SubscriptionRequestDialog(from_jid, parent=self)
            result = dialog.exec()

            if result == QDialog.Accepted:
                # User approved
                also_request = dialog.also_request

                # Get account
                account = self.account_manager.get_account(account_id)
                if not account:
                    logger.error(f"Account {account_id} not found")
                    return

                # Check connection
                if not account.is_connected():
                    logger.warning(f"Account {account_id} not connected, cannot approve subscription")
                    return

                try:
                    # Approve their request
                    asyncio.create_task(account.approve_subscription(from_jid))
                    logger.debug(f"Approved subscription request from {from_jid}")

                    # Also request their subscription if checkbox was checked
                    if also_request:
                        asyncio.create_task(account.request_subscription(from_jid))
                        logger.debug(f"Also requested subscription from {from_jid} (mutual)")

                except Exception as e:
                    logger.error(f"Failed to handle subscription request: {e}")
            else:
                # User denied
                logger.debug(f"User denied subscription request from {from_jid}")

                # Get account
                account = self.account_manager.get_account(account_id)
                if account and account.is_connected():
                    try:
                        asyncio.create_task(account.deny_subscription(from_jid))
                        logger.debug(f"Sent denial to {from_jid}")
                    except Exception as e:
                        logger.error(f"Failed to deny subscription: {e}")

        # Defer to next event loop iteration
        QTimer.singleShot(0, show_dialog)

    def _on_subscription_changed(self, account_id: int, from_jid: str, change_type: str):
        """
        Handle subscription state change notification.

        Args:
            account_id: Account ID
            from_jid: JID whose subscription changed
            change_type: Type of change
        """
        logger.debug(f"Subscription changed for {from_jid}: {change_type}")
        # Roster is already refreshed by roster_updated signal, nothing more to do

    # =========================================================================
    # Call Signal Handlers (DrunkCALL Integration)
    # =========================================================================

    def _on_call_incoming(self, account_id: int, session_id: str, from_jid: str, media: list):
        """
        Handle incoming call - show IncomingCallDialog.

        Args:
            account_id: Account receiving the call
            session_id: Jingle session ID
            from_jid: Caller JID
            media: Media types (['audio'] or ['audio', 'video'])
        """
        logger.info(f"Incoming call from {from_jid}: {media} (session {session_id})")

        # Track session → jid mapping for roster indicators
        self.call_session_map[session_id] = (account_id, from_jid)

        # Update roster indicator: incoming call (ringing)
        self.contact_list.update_call_indicator(account_id, from_jid, 'incoming')

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Cannot handle incoming call: account {account_id} not found")
            return

        # Send OS notification for incoming call
        self._send_call_os_notification(account_id, from_jid, media)

        # Show incoming call dialog
        dialog = IncomingCallDialog(self, account_id, session_id, from_jid, media)

        # Track dialog so we can close it on timeout
        self.incoming_call_dialogs[session_id] = dialog

        # Connect dialog signals
        # Use QTimer.singleShot to defer execution outside signal handler context
        # This avoids "Cannot enter into task while another task is being executed" error
        def schedule_accept():
            def do_accept():
                asyncio.ensure_future(self._accept_call(account_id, session_id, from_jid, media))
            QTimer.singleShot(0, do_accept)

        def schedule_reject():
            def do_reject():
                asyncio.ensure_future(account.hangup_call(session_id))
            QTimer.singleShot(0, do_reject)

        dialog.call_accepted.connect(schedule_accept)
        dialog.call_rejected.connect(schedule_reject)
        dialog.call_silenced.connect(lambda: logger.info(
            f"Call silenced (ignored): {session_id} from {from_jid}"
        ))

        # Show dialog (non-blocking)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        # Clean up tracking when dialog closes
        dialog.finished.connect(lambda: self.incoming_call_dialogs.pop(session_id, None))

        dialog.show()

    async def _accept_call(self, account_id: int, session_id: str, from_jid: str, media: list):
        """
        Accept incoming call and open call window.

        Args:
            account_id: Account accepting the call
            session_id: Jingle session ID
            from_jid: Caller JID
            media: Media types
        """
        logger.debug(f"Accepting call from {from_jid} (session {session_id})")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Cannot accept call: account {account_id} not found")
            return

        try:
            # Accept the call (creates WebRTC connection)
            await account.accept_call(session_id)

            # Open call window
            self._open_call_window(account_id, session_id, from_jid, media, 'incoming')

        except Exception as e:
            logger.error(f"Failed to accept call: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                "Call Failed",
                f"Could not accept call: {e}"
            )

    def _on_call_initiated(self, account_id: int, session_id: str, peer_jid: str, media: list):
        """
        Handle outgoing call initiated - show OutgoingCallDialog.

        Args:
            account_id: Account initiating the call
            session_id: Jingle session ID
            peer_jid: JID being called
            media: Media types (['audio'] or ['audio', 'video'])
        """
        logger.info(f"Outgoing call initiated to {peer_jid}: {media} (session {session_id})")

        # Track session → jid mapping for roster indicators
        self.call_session_map[session_id] = (account_id, peer_jid)

        # Update roster indicator: outgoing call (ringing)
        self.contact_list.update_call_indicator(account_id, peer_jid, 'outgoing')

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Cannot handle outgoing call: account {account_id} not found")
            return

        # Show outgoing call dialog
        dialog = OutgoingCallDialog(self, account_id, session_id, peer_jid, media)

        # Track dialog so we can close it when call is answered/rejected/timeout
        self.outgoing_call_dialogs[session_id] = dialog

        # Connect cancel signal
        def schedule_cancel():
            def do_cancel():
                asyncio.ensure_future(account.hangup_call(session_id))
            QTimer.singleShot(0, do_cancel)

        dialog.call_cancelled.connect(schedule_cancel)

        # Show dialog (non-blocking)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        # Clean up tracking when dialog closes
        dialog.finished.connect(lambda: self.outgoing_call_dialogs.pop(session_id, None))

        dialog.show()

    def _on_call_accepted(self, account_id: int, session_id: str):
        """
        Handle call accepted (outgoing call accepted by peer) - open call window.

        Args:
            account_id: Account that initiated the call
            session_id: Jingle session ID
        """
        logger.debug(f"Handler _on_call_accepted called: account_id={account_id}, session_id={session_id}")
        logger.debug(f"Outgoing call accepted (session {session_id})")

        # Update roster indicator: call active (connected)
        if session_id in self.call_session_map:
            acc_id, jid = self.call_session_map[session_id]
            self.contact_list.update_call_indicator(acc_id, jid, 'active')

        # Get account and session info
        account = self.account_manager.get_account(account_id)
        if not account or not account.jingle_adapter:
            logger.error(f"Cannot handle call acceptance: account or adapter not found")
            return

        # Use JingleAdapter's public API (encapsulation)
        session_info = account.jingle_adapter.get_session_info(session_id)
        if not session_info:
            logger.error(f"Cannot handle call acceptance: session {session_id} not found")
            return

        peer_jid = session_info['peer_jid']
        media = session_info['media']

        # Close outgoing call dialog (peer accepted - transition to call window)
        if session_id in self.outgoing_call_dialogs:
            dialog = self.outgoing_call_dialogs.pop(session_id)
            dialog.accept()  # Close dialog
            logger.debug(f"Closed outgoing call dialog - peer accepted: {session_id}")

        # Open call window
        self._open_call_window(account_id, session_id, peer_jid, media, 'outgoing')

    def _open_call_window(self, account_id: int, session_id: str, peer_jid: str,
                          media: list, direction: str):
        """
        Open call window for active call.

        Args:
            account_id: Account in call
            session_id: Jingle session ID
            peer_jid: Peer JID
            media: Media types
            direction: 'incoming' or 'outgoing'
        """
        logger.debug(f"Opening call window: {peer_jid} ({direction}, {media})")

        # Create call window
        call_window = CallWindow(self, account_id, session_id, peer_jid, media, direction)

        # Connect signals
        account = self.account_manager.get_account(account_id)
        if account:
            # Update call window when state changes
            account.call_state_changed.connect(
                lambda aid, sid, state: (
                    call_window.on_call_state_changed(state)
                    if sid == session_id else None
                )
            )

            # Update call window when call terminates
            account.call_terminated.connect(
                lambda aid, sid, reason: (
                    call_window.on_call_terminated(reason)
                    if sid == session_id else None
                )
            )

            # Handle hangup button click
            call_window.hangup_requested.connect(
                lambda: asyncio.create_task(account.hangup_call(session_id))
            )

        # Track window
        self.call_windows[session_id] = call_window

        # Show window
        call_window.show()

    def _on_call_state_changed(self, account_id: int, session_id: str, state: str):
        """
        Handle call state change (WebRTC connection state).

        Args:
            account_id: Account in call
            session_id: Jingle session ID
            state: Connection state
        """
        logger.debug(f"Call state changed: {state} (session {session_id})")
        # State updates are already forwarded to call window via signal connections
        # in _open_call_window, so nothing more to do here

    def _on_call_terminated(self, account_id: int, session_id: str, reason: str, peer_jid: str):
        """
        Handle call termination - cleanup call window and dialogs.

        Args:
            account_id: Account that was in call
            session_id: Jingle session ID
            reason: Termination reason
            peer_jid: JID of the peer
        """
        logger.info(f"Call terminated: {reason} (session {session_id}, peer {peer_jid})")

        # Update roster indicator: clear call state
        if session_id in self.call_session_map:
            acc_id, jid = self.call_session_map.pop(session_id)
            self.contact_list.update_call_indicator(acc_id, jid, None)

        # Handle call notification based on termination reason
        if reason == 'timeout':
            # Missed call - replace ringing notification with persistent missed call notification
            self._send_missed_call_notification(account_id, peer_jid)
        else:
            # Call was answered, rejected, or ended - just dismiss the notification
            self.notification_service.dismiss_notification(account_id, peer_jid, is_call=True)

        # Close incoming call dialog if still open (e.g., timeout)
        if session_id in self.incoming_call_dialogs:
            dialog = self.incoming_call_dialogs.pop(session_id)
            dialog.reject()  # Close dialog
            logger.debug(f"Closed incoming call dialog due to {reason}: {session_id}")

        # Update outgoing call dialog if still open (e.g., peer rejected or timeout)
        if session_id in self.outgoing_call_dialogs:
            dialog = self.outgoing_call_dialogs[session_id]
            # Update dialog to show rejection/timeout/error status
            # Dialog stays open, user reads and clicks Close
            dialog.update_on_status_change(reason)
            logger.debug(f"Updated outgoing call dialog for {reason}: {session_id}")

        # Call window will auto-close after 2 seconds (handled in CallWindow itself)
        # Just clean up our tracking
        if session_id in self.call_windows:
            # Don't delete immediately - let the window auto-close
            # We'll clean up when it's actually closed
            logger.debug(f"Call window for {session_id} will auto-close")

        # Update status bar to reflect call stats
        self._update_status_bar_stats()

    def request_call_stats(self, account_id: int, session_id: str):
        """
        Request call statistics update for a session.

        Called by CallWindow every 2 seconds to update tech details.

        Args:
            account_id: Account ID
            session_id: Jingle session ID
        """
        async def fetch_and_update():
            try:
                # Get account manager for this account
                account = self.account_manager.get_account(account_id)
                if not account or not hasattr(account, 'calls'):
                    return

                # Get stats from CallBarrel
                stats = await account.calls.get_call_stats(session_id)

                # Update call window if still open
                if session_id in self.call_windows:
                    call_window = self.call_windows[session_id]
                    call_window.update_stats(stats)

            except Exception as e:
                logger.error(f"Error fetching call stats: {e}")

        # Schedule async task
        asyncio.ensure_future(fetch_and_update())

    # =========================================================================
    # Other Signal Handlers
    # =========================================================================

    def _update_chat_receipts(self):
        """Poll and update receipt indicators in chat view."""
        # Only refresh if a chat is open AND the account still exists
        if self.chat_view.current_account_id and self.chat_view.current_jid:
            # Check if account still exists (could have been deleted)
            if self.account_manager.get_account(self.chat_view.current_account_id):
                self.chat_view.refresh(send_markers=False)  # Just update UI, don't send markers

    def set_chat_polling_enabled(self, enabled: bool):
        """
        Enable/disable chat polling timer based on scroll zone.

        Called by MessageDisplayWidget when user scrolls between live and history zones.
        Live zone (>50% scroll) = enable polling for real-time updates.
        History zone (<=50% scroll) = disable polling for better performance.

        Args:
            enabled: True to enable polling (live zone), False to disable (history zone)
        """
        if enabled:
            if not self.receipt_timer.isActive():
                self.receipt_timer.start(2000)
                logger.debug("Chat polling ENABLED (live zone - bottom 50%)")
        else:
            if self.receipt_timer.isActive():
                self.receipt_timer.stop()
                logger.debug("Chat polling DISABLED (history zone - top 50%)")

    @Slot(int, str, str, bool)
    def _on_send_message(self, account_id: int, jid: str, message: str, encrypted: bool):
        """
        Handle send message signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            message: Message text
            encrypted: Whether to use OMEMO encryption
        """
        logger.info(f"Sending message to {jid} from account {account_id} (encrypted={encrypted})")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return

        # Send message asynchronously
        asyncio.create_task(self._send_message_async(account, jid, message, encrypted))

    async def _send_message_async(self, account, jid: str, message: str, encrypted: bool):
        """
        Send message asynchronously.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID (contact or MUC room)
            message: Message text
            encrypted: Whether to use OMEMO encryption
        """
        # Check if this is a MUC room (check bookmarks in DB)
        muc_check = self.db.fetchone("""
            SELECT 1 FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account.account_id, jid))

        is_muc = muc_check is not None
        message_type = 1 if is_muc else 0

        logger.debug(f"Sending to {jid}: is_muc={is_muc}, encrypted={encrypted}")

        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Store message in DB FIRST with temporary ID (so it shows in UI immediately)
        timestamp = int(datetime.now().timestamp())
        import uuid
        temp_origin_id = f"temp-{uuid.uuid4()}"

        conversation_id = self.db.get_or_create_conversation(account.account_id, jid_id, message_type)
        result = self.db.insert_message_atomic(
            account_id=account.account_id,
            counterpart_id=jid_id,
            conversation_id=conversation_id,
            direction=1,  # direction=1 (sent)
            msg_type=message_type,
            time=timestamp,
            local_time=timestamp,
            body=message,
            encryption=1 if encrypted else 0,
            marked=0,  # marked=0 (pending - will show hourglass)
            is_carbon=0,  # User's own new message, not a carbon
            origin_id=temp_origin_id  # Temporary ID until reflection comes back
        )

        db_message_id, _ = result  # Should never be (None, None) for new messages

        self.db.commit()

        logger.debug(f"Message stored in DB (id={db_message_id}, will attempt send)")

        # Refresh chat view immediately (shows message with hourglass)
        self.chat_view.refresh(send_markers=False)  # Just show our sent message, don't send markers

        # Check if account is connected before attempting to send
        if not account.is_connected():
            logger.debug(f"Account {account.account_id} not connected, message will be retried on reconnect")
            # Message stays marked=0 with hourglass, will be retried when connection restored
            return

        try:
            # Now try to send message
            if is_muc:
                if encrypted:
                    message_id = await account.client.send_encrypted_to_muc(jid, message)
                else:
                    message_id = await account.client.send_to_muc(jid, message)
            else:
                message_id = await account.send_message(jid, message, encrypted)

            # Update message with real origin_id
            self.db.execute("""
                UPDATE message SET origin_id = ? WHERE id = ?
            """, (message_id, db_message_id))
            self.db.commit()

            logger.debug(f"Message sent successfully (origin_id: {message_id})")

            # Track sent message for editing (arrow-up key)
            self.chat_view.track_sent_message(message_id, message, encrypted)

            # Server ACK will update marked=1 via receipt_handler

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            logger.error(f"Message remains in DB (id={db_message_id}) with marked=0 for retry")
            import traceback
            logger.error(traceback.format_exc())
            # Message stays marked=0 and will be retried on reconnect

    @Slot(int, str, str, bool)
    def _on_send_file(self, account_id: int, jid: str, file_path: str, encrypted: bool):
        """
        Handle send file signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            file_path: Path to file to send
            encrypted: Whether to use OMEMO encryption
        """
        logger.debug(f"Sending file to {jid} from account {account_id}: {file_path} (encrypted={encrypted})")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            QMessageBox.critical(self, "Error", f"Account {account_id} not found")
            return

        # Check if account is connected
        if not account.is_connected():
            logger.error(f"Account {account_id} is not connected")
            QMessageBox.warning(self, "Not Connected", "Account is not connected. Please wait for connection.")
            return

        # Verify file exists
        import os
        if not os.path.isfile(file_path):
            logger.error(f"File not found: {file_path}")
            QMessageBox.critical(self, "Error", f"File not found: {file_path}")
            return

        # Send file asynchronously
        asyncio.create_task(self._send_file_async(account, jid, file_path, encrypted))

    async def _send_file_async(self, account, jid: str, file_path: str, encrypted: bool):
        """
        Send file asynchronously.

        Pattern matches message sending: store in DB first, then send, then update state.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID
            file_path: Path to file to send
            encrypted: Whether to use OMEMO encryption
        """
        import os
        import mimetypes
        from pathlib import Path
        from datetime import datetime

        file = Path(file_path)
        filename = file.name
        file_size = file.stat().st_size

        # Check if this is a MUC room
        muc_check = self.db.fetchone("""
            SELECT 1 FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account.account_id, jid))

        is_muc = muc_check is not None
        message_type = 1 if is_muc else 0

        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Guess MIME type
        mime_type, _ = mimetypes.guess_type(filename)

        # Store file_transfer in DB FIRST with state=1 (uploading)
        timestamp = int(datetime.now().timestamp())

        # Generate temporary origin_id for deduplication (same pattern as text messages)
        # This allows us to deduplicate when the server reflects the message back
        import uuid
        temp_origin_id = f"temp-{uuid.uuid4()}"

        # Get or create conversation
        conversation_id = self.db.get_or_create_conversation(account.account_id, jid_id, message_type)

        # Insert file_transfer + content_item atomically
        file_transfer_id, content_item_id = self.db.insert_file_transfer_atomic(
            account_id=account.account_id,
            counterpart_id=jid_id,
            conversation_id=conversation_id,
            direction=1,  # direction=1 (sent)
            time=timestamp,
            local_time=timestamp,
            file_name=filename,
            path=str(file),  # Local path to file being sent
            mime_type=mime_type,
            size=file_size,
            state=1,  # state=1 (uploading)
            encryption=1 if encrypted else 0,
            provider=0,  # provider=0 (HTTP Upload)
            is_carbon=0,  # Not a carbon (original send)
            url=None,  # URL not known yet (will be set after upload)
            origin_id=temp_origin_id  # Temporary ID for deduplication
        )

        logger.debug(f"File transfer record created (id={file_transfer_id}, state=uploading)")

        # Refresh chat view immediately (shows file with uploading state)
        QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

        try:
            logger.debug(f"Starting file upload: {file_path} to {jid} (encrypted={encrypted})")

            # Use appropriate DrunkXMPP method based on recipient type and encryption
            # These methods return the message_id (origin_id) for deduplication
            message_id = None
            if is_muc:
                if encrypted:
                    message_id = await account.client.send_encrypted_attachment_to_muc(jid, file_path)
                else:
                    message_id = await account.client.send_attachment_to_muc(jid, file_path)
            else:
                if encrypted:
                    message_id = await account.client.send_encrypted_file(jid, file_path)
                else:
                    message_id = await account.client.send_attachment_to_user(jid, file_path)

            # Update file_transfer with real origin_id and mark as complete
            # This allows duplicate detection when server reflects the message back
            self.db.execute("""
                UPDATE file_transfer SET state = ?, origin_id = ? WHERE id = ?
            """, (2, message_id, file_transfer_id))  # state=2 (complete)
            self.db.commit()

            logger.debug(f"✓ File '{filename}' sent to {jid} (id={file_transfer_id}, origin_id={message_id})")

            # Refresh to update state indicator
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send file: {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

            # Update file_transfer state to failed
            self.db.execute("""
                UPDATE file_transfer SET state = ? WHERE id = ?
            """, (3, file_transfer_id))  # state=3 (failed)
            self.db.commit()

            # Refresh to show error state
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

            # Show error dialog safely using QTimer to call from main thread
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self,
                "File Send Failed",
                f"Failed to send '{filename}':\n\n{error_msg}"
            ))

    @Slot(int, str, str, str, bool)
    def _on_edit_message(self, account_id: int, jid: str, message_id: str, new_body: str, encrypted: bool):
        """
        Handle edit message signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            message_id: XMPP message ID to edit
            new_body: New message text
            encrypted: Whether message was encrypted (must match original)
        """
        logger.debug(f"Editing message {message_id} to {jid} from account {account_id}")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return

        # Check if account is connected
        if not account.is_connected():
            logger.error(f"Account {account_id} is not connected")
            QMessageBox.warning(self, "Not Connected", "Cannot edit message: account is not connected.")
            return

        # Send edit asynchronously
        asyncio.create_task(self._edit_message_async(account, jid, message_id, new_body, encrypted))

    async def _edit_message_async(self, account, jid: str, message_id: str, new_body: str, encrypted: bool):
        """
        Edit message asynchronously using XEP-0308.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID
            message_id: XMPP message ID to edit
            new_body: New message text
            encrypted: Whether message was encrypted
        """
        try:
            # Send message correction via DrunkXMPP
            await account.client.edit_message(jid, message_id, new_body, encrypt=encrypted)

            logger.debug(f"Message {message_id} edited successfully")

            # Update message in database
            self.db.execute("""
                UPDATE message
                SET body = ?
                WHERE (message_id = ? OR origin_id = ? OR stanza_id = ?)
                AND account_id = ?
            """, (new_body, message_id, message_id, message_id, account.account_id))
            self.db.commit()

            # Refresh chat view to show edited message
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

            # Update tracked message body for future edits
            self.chat_view.track_sent_message(message_id, new_body, encrypted)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to edit message: {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

            # Show error dialog safely using QTimer
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self,
                "Edit Failed",
                f"Failed to edit message:\n\n{error_msg}"
            ))

    def _on_send_reply(self, account_id: int, jid: str, reply_to_id: str, reply_body: str, fallback_body: str, encrypted: bool):
        """
        Handle send reply signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            reply_to_id: XMPP message ID being replied to
            reply_body: Reply text (without quoted text)
            fallback_body: Original quoted message text for fallback
            encrypted: Whether to encrypt the reply
        """
        logger.debug(f"Sending reply to message {reply_to_id} in {jid} from account {account_id}")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return

        # Check if account is connected
        if not account.is_connected():
            logger.error(f"Account {account_id} is not connected")
            QMessageBox.warning(self, "Not Connected", "Cannot send reply: account is not connected.")
            return

        # Send reply asynchronously
        asyncio.create_task(self._send_reply_async(account, jid, reply_to_id, reply_body, fallback_body, encrypted))

    async def _send_reply_async(self, account, jid: str, reply_to_id: str, reply_body: str, fallback_body: str, encrypted: bool):
        """
        Send reply asynchronously using XEP-0461.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID
            reply_to_id: XMPP message ID being replied to
            reply_body: Reply text
            fallback_body: Original quoted message text
            encrypted: Whether to encrypt the reply
        """
        # Check if this is a MUC room (check bookmarks in DB)
        muc_check = self.db.fetchone("""
            SELECT 1 FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account.account_id, jid))

        is_muc = muc_check is not None
        message_type = 1 if is_muc else 0

        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Build full message body (quoted + reply) for display
        full_body = f"> {fallback_body}\n{reply_body}" if fallback_body else reply_body

        # Store message in DB FIRST (optimistic - shows in UI immediately)
        timestamp = int(datetime.now().timestamp())
        import uuid
        temp_origin_id = f"temp-{uuid.uuid4()}"

        conversation_id = self.db.get_or_create_conversation(account.account_id, jid_id, message_type)
        result = self.db.insert_message_atomic(
            account_id=account.account_id,
            counterpart_id=jid_id,
            conversation_id=conversation_id,
            direction=1,  # direction=1 (sent)
            msg_type=message_type,
            time=timestamp,
            local_time=timestamp,
            body=full_body,
            encryption=1 if encrypted else 0,
            marked=0,  # marked=0 (pending - will show hourglass)
            is_carbon=0,  # User's own new message, not a carbon
            origin_id=temp_origin_id,  # Temporary ID until reflection comes back
            reply_to_id=reply_to_id,
            reply_to_jid=jid
        )

        db_message_id, _ = result  # Should never be (None, None) for new messages

        self.db.commit()

        logger.debug(f"Reply stored in DB (id={db_message_id}, will attempt send)")

        # Refresh chat view immediately (shows reply with hourglass)
        QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

        # Check if account is connected before attempting to send
        if not account.is_connected():
            logger.debug(f"Account {account.account_id} not connected, reply will be retried on reconnect")
            return

        try:
            # Send reply via DrunkXMPP (XEP-0461)
            message_id = await account.client.send_reply(jid, reply_to_id, reply_body, fallback_body=fallback_body, encrypt=encrypted)

            logger.debug(f"Reply sent successfully to {jid} (message_id={message_id})")

            # Update message with real origin_id
            self.db.execute("""
                UPDATE message SET origin_id = ? WHERE id = ?
            """, (message_id, db_message_id))
            self.db.commit()

            # Track this message for future edits
            self.chat_view.track_sent_message(message_id, full_body, encrypted)

            # Server ACK will update marked=1, then delivery receipt will update to marked=2

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send reply: {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

            # Mark message as error (marked=8)
            self.db.execute("""
                UPDATE message SET marked = 8 WHERE id = ?
            """, (db_message_id,))
            self.db.commit()

            # Refresh to show error state
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

            # Show error dialog safely using QTimer
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self,
                "Reply Failed",
                f"Failed to send reply:\n\n{error_msg}"
            ))

    async def _start_go_call_service(self):
        """Start Go call service asynchronously."""
        if not self.go_call_service:
            return

        try:
            logger.debug("Starting Go call service...")
            success = await self.go_call_service.start()
            if success:
                logger.debug("Go call service started successfully")
            else:
                logger.error("Failed to start Go call service")
        except Exception as e:
            logger.error(f"Error starting Go call service: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def closeEvent(self, event):
        """Handle window close event - cleanup all services."""
        logger.debug("Main window closing...")

        # Stop status bar timer
        if hasattr(self, 'status_bar_timer'):
            self.status_bar_timer.stop()

        # Step 1: Disconnect all XMPP accounts (fire and forget)
        try:
            logger.debug("Disconnecting XMPP accounts...")
            self.account_manager.disconnect_all()
            logger.debug("XMPP disconnect initiated")
        except Exception as e:
            logger.error(f"Error disconnecting XMPP accounts: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # Step 2: Stop Go call service
        if self.go_call_service:
            try:
                # If shutdown triggered by signal (Ctrl+C), skip gRPC shutdown
                # Go service receives SIGINT directly and exits on its own
                if self._signal_shutdown:
                    logger.debug("Signal-triggered shutdown - skipping Go RPC, letting Go handle signal")
                    # Just wait for process to exit (it's handling SIGINT itself)
                    if self.go_call_service._process:
                        try:
                            self.go_call_service._process.wait(timeout=2.0)
                            logger.debug("Go service exited")
                        except subprocess.TimeoutExpired:
                            logger.warning("Go service didn't exit, terminating")
                            self.go_call_service._process.terminate()
                else:
                    # Normal shutdown (File->Quit): use gRPC for graceful shutdown
                    logger.debug("Sending shutdown to Go call service...")
                    # Use QEventLoop to wait for async stop() to complete
                    from PySide6.QtCore import QEventLoop
                    loop = QEventLoop()
                    future = asyncio.ensure_future(self.go_call_service.stop())
                    future.add_done_callback(lambda _: loop.quit())
                    loop.exec()  # Wait for stop() to complete
                    logger.debug("Go call service shutdown request completed")
            except Exception as e:
                logger.error(f"Error stopping Go call service: {e}")

        # Accept close event
        event.accept()
