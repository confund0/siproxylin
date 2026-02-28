"""
MenuManager - Manages application menu bar and menu actions.

Extracted from MainWindow to improve maintainability.
"""

import logging
from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QAction, QActionGroup


logger = logging.getLogger('siproxylin.menu_manager')


class MenuManager:
    """
    Manages menu bar creation, population, and menu action handlers.

    Responsibilities:
    - Create and populate all menus (File, Edit, View, Contacts, Help)
    - Handle menu actions (font size, theme, roster mode)
    - Manage log viewer windows
    - Update dynamic menus (accounts list)
    """

    def __init__(self, main_window):
        """
        Initialize MenuManager.

        Args:
            main_window: MainWindow instance (for accessing widgets and services)
        """
        self.main_window = main_window
        self.db = main_window.db
        self.paths = main_window.paths
        self.theme_manager = main_window.theme_manager
        self.contact_list = main_window.contact_list
        self.chat_view = main_window.chat_view
        self.log_viewers = main_window.log_viewers

        # Menu references (will be set during creation)
        self.edit_menu = None
        self.view_menu = None
        self.tools_menu = None
        self.theme_actions = {}  # {theme_name: QAction}
        self.roster_mode_actions = {}  # {mode_name: QAction}

        logger.debug("MenuManager initialized")

    def create_menu_bar(self):
        """Create menu bar with File, Edit, View, Contacts, Help menus."""
        menubar = self.main_window.menuBar()

        # =====================================================================
        # File Menu
        # =====================================================================
        file_menu = menubar.addMenu("&File")

        # File -> Add Account
        add_account_action = QAction("&Add Account...", self.main_window)
        add_account_action.setShortcut("Ctrl+Shift+A")
        add_account_action.triggered.connect(self.main_window._on_new_account)
        file_menu.addAction(add_account_action)

        # File -> Create Account (XEP-0077)
        create_account_action = QAction("&Create Account...", self.main_window)
        create_account_action.setToolTip("Register a new XMPP account using in-band registration (XEP-0077)")
        create_account_action.triggered.connect(self.main_window._on_create_account)
        file_menu.addAction(create_account_action)

        file_menu.addSeparator()

        # File -> Settings
        settings_action = QAction("&Settings...", self.main_window)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.main_window._on_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        # File -> Quit
        quit_action = QAction("&Quit", self.main_window)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.main_window.close)
        file_menu.addAction(quit_action)

        # =====================================================================
        # Edit Menu
        # =====================================================================
        self.edit_menu = menubar.addMenu("&Edit")
        self.populate_edit_menu()

        # =====================================================================
        # View Menu (with per-account submenus)
        # =====================================================================
        self.view_menu = menubar.addMenu("&View")
        self.populate_view_menu()

        # =====================================================================
        # Contacts Menu
        # =====================================================================
        contacts_menu = menubar.addMenu("&Contacts")

        # Contacts -> Add Contact
        add_contact_action_menu = QAction("&Add Contact...", self.main_window)
        add_contact_action_menu.setShortcut("Ctrl+N")
        add_contact_action_menu.triggered.connect(self.main_window._on_new_contact)
        contacts_menu.addAction(add_contact_action_menu)

        # Contacts -> Add Group
        add_group_action_menu = QAction("Add &Group...", self.main_window)
        add_group_action_menu.triggered.connect(self.main_window._on_new_group)
        contacts_menu.addAction(add_group_action_menu)

        contacts_menu.addSeparator()

        # Contacts -> Manage Contacts
        manage_contacts_action = QAction("&Manage Contacts...", self.main_window)
        manage_contacts_action.setShortcut("Ctrl+Shift+C")
        manage_contacts_action.triggered.connect(self.main_window._on_manage_contacts)
        contacts_menu.addAction(manage_contacts_action)

        # =====================================================================
        # Tools Menu (Admin Tools)
        # =====================================================================
        self.tools_menu = menubar.addMenu("&Tools")
        self.populate_tools_menu()

        # =====================================================================
        # Help Menu
        # =====================================================================
        help_menu = menubar.addMenu("&Help")

        # Help -> About
        about_action = QAction("&About...", self.main_window)
        about_action.triggered.connect(self.main_window._on_about)
        help_menu.addAction(about_action)

        logger.debug("Menu bar created")

    def populate_edit_menu(self):
        """Populate Edit menu with account-specific entries."""
        self.edit_menu.clear()

        # Edit -> Copy
        copy_action = QAction("&Copy", self.main_window)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self.main_window._on_copy)
        self.edit_menu.addAction(copy_action)

        # Edit -> Paste
        paste_action = QAction("&Paste", self.main_window)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(self.main_window._on_paste)
        self.edit_menu.addAction(paste_action)

        self.edit_menu.addSeparator()

        # Edit -> Accounts (submenu)
        accounts_menu = self.edit_menu.addMenu("&Accounts")

        # Get all accounts from database
        accounts = self.db.fetchall("SELECT id, bare_jid, nickname FROM account ORDER BY id")

        if not accounts:
            # No accounts yet
            no_accounts_action = QAction("(No accounts)", self.main_window)
            no_accounts_action.setEnabled(False)
            accounts_menu.addAction(no_accounts_action)
        else:
            # Add menu item for each account
            for account in accounts:
                account_id = account['id']
                account_label = account['nickname'] or account['bare_jid']

                edit_account_action = QAction(f"{account_id}: {account_label}...", self.main_window)
                edit_account_action.triggered.connect(
                    lambda checked, aid=account_id: self.main_window._on_edit_account(aid)
                )
                accounts_menu.addAction(edit_account_action)

        logger.debug(f"Edit menu populated with {len(accounts)} accounts")

    def populate_view_menu(self):
        """Populate View menu with account-specific submenus."""
        self.view_menu.clear()

        # View -> Font Size
        font_size_menu = self.view_menu.addMenu("Font &Size")

        increase_font_action = QAction("&Increase", self.main_window)
        increase_font_action.setShortcut("Ctrl++")
        increase_font_action.triggered.connect(self.on_increase_font)
        font_size_menu.addAction(increase_font_action)

        decrease_font_action = QAction("&Decrease", self.main_window)
        decrease_font_action.setShortcut("Ctrl+-")
        decrease_font_action.triggered.connect(self.on_decrease_font)
        font_size_menu.addAction(decrease_font_action)

        reset_font_action = QAction("&Reset", self.main_window)
        reset_font_action.setShortcut("Ctrl+0")
        reset_font_action.triggered.connect(self.on_reset_font)
        font_size_menu.addAction(reset_font_action)

        # View -> Theme (with checkmarks for active theme)
        theme_menu = self.view_menu.addMenu("&Theme")

        # Create action group for exclusive selection (checkmarks)
        theme_action_group = QActionGroup(self.main_window)
        theme_action_group.setExclusive(True)

        # Store theme actions for later reference
        self.theme_actions = {}

        light_theme_action = QAction("&Light", self.main_window)
        light_theme_action.setCheckable(True)
        light_theme_action.triggered.connect(lambda: self.on_change_theme('light'))
        theme_menu.addAction(light_theme_action)
        theme_action_group.addAction(light_theme_action)
        self.theme_actions['light'] = light_theme_action

        light_gray_theme_action = QAction("Light &Gray", self.main_window)
        light_gray_theme_action.setCheckable(True)
        light_gray_theme_action.triggered.connect(lambda: self.on_change_theme('light_gray'))
        theme_menu.addAction(light_gray_theme_action)
        theme_action_group.addAction(light_gray_theme_action)
        self.theme_actions['light_gray'] = light_gray_theme_action

        dark_theme_action = QAction("&Dark", self.main_window)
        dark_theme_action.setCheckable(True)
        dark_theme_action.triggered.connect(lambda: self.on_change_theme('dark'))
        theme_menu.addAction(dark_theme_action)
        theme_action_group.addAction(dark_theme_action)
        self.theme_actions['dark'] = dark_theme_action

        terminal_theme_action = QAction("&Terminal", self.main_window)
        terminal_theme_action.setCheckable(True)
        terminal_theme_action.triggered.connect(lambda: self.on_change_theme('terminal'))
        theme_menu.addAction(terminal_theme_action)
        theme_action_group.addAction(terminal_theme_action)
        self.theme_actions['terminal'] = terminal_theme_action

        gruvbox_theme_action = QAction("&Gruvbox", self.main_window)
        gruvbox_theme_action.setCheckable(True)
        gruvbox_theme_action.triggered.connect(lambda: self.on_change_theme('gruvbox'))
        theme_menu.addAction(gruvbox_theme_action)
        theme_action_group.addAction(gruvbox_theme_action)
        self.theme_actions['gruvbox'] = gruvbox_theme_action

        # View -> Roster
        roster_menu = self.view_menu.addMenu("&Roster")

        classic_roster_action = QAction("&Classic (Emoji)", self.main_window)
        classic_roster_action.setCheckable(True)
        classic_roster_action.setChecked(self.theme_manager.roster_mode == 'classic')
        classic_roster_action.triggered.connect(lambda: self.on_change_roster_mode('classic'))
        roster_menu.addAction(classic_roster_action)

        ascii_roster_action = QAction("&ASCII (Text-only)", self.main_window)
        ascii_roster_action.setCheckable(True)
        ascii_roster_action.setChecked(self.theme_manager.roster_mode == 'ascii')
        ascii_roster_action.triggered.connect(lambda: self.on_change_roster_mode('ascii'))
        roster_menu.addAction(ascii_roster_action)

        # Store references to toggle mutual exclusivity
        self.roster_mode_actions = {
            'classic': classic_roster_action,
            'ascii': ascii_roster_action
        }

        self.view_menu.addSeparator()

        # View -> Calls (renamed from Call Log)
        calls_action = QAction("Call&s...", self.main_window)
        calls_action.triggered.connect(self.on_view_call_log)
        self.view_menu.addAction(calls_action)

        self.view_menu.addSeparator()

        # View -> Logs (reorganized submenu)
        logs_menu = self.view_menu.addMenu("&Logs")

        # View -> Logs -> Main Log
        main_log_action = QAction("&Main Log...", self.main_window)
        main_log_action.triggered.connect(self.on_view_main_log)
        logs_menu.addAction(main_log_action)

        # View -> Logs -> XML Protocol Log
        xml_log_action = QAction("&XML Protocol Log...", self.main_window)
        xml_log_action.triggered.connect(self.on_view_xml_log)
        logs_menu.addAction(xml_log_action)

        logs_menu.addSeparator()

        # View -> Logs -> Accounts (submenu)
        accounts_log_menu = logs_menu.addMenu("&Accounts")

        # Get all accounts from database
        accounts = self.db.fetchall("SELECT id, bare_jid, nickname FROM account ORDER BY id")

        if not accounts:
            # No accounts yet
            no_accounts_action = QAction("(No accounts)", self.main_window)
            no_accounts_action.setEnabled(False)
            accounts_log_menu.addAction(no_accounts_action)
        else:
            # Add app log entry for each account
            for account in accounts:
                account_id = account['id']
                account_label = account['nickname'] or account['bare_jid']

                # View -> Logs -> Accounts -> {account_label}
                app_log_action = QAction(f"{account_label}", self.main_window)
                app_log_action.triggered.connect(
                    lambda checked, aid=account_id: self.on_view_app_log(aid)
                )
                accounts_log_menu.addAction(app_log_action)

        logger.debug(f"View menu populated with {len(accounts)} accounts")

    def populate_tools_menu(self):
        """Populate Tools menu with admin tools."""
        self.tools_menu.clear()

        # Check if Admin Tools is enabled
        admin_tools_enabled = self.db.get_setting('admin_tools_enabled', default='false')

        if admin_tools_enabled.lower() in ('true', '1', 'yes'):
            # Tools -> Disco (Service Discovery)
            disco_action = QAction("&Disco (Service Discovery)...", self.main_window)
            disco_action.setToolTip("Query XMPP Service Discovery information for any JID")
            disco_action.triggered.connect(self.main_window._on_disco_tool)
            self.tools_menu.addAction(disco_action)

            logger.debug("Tools menu populated with admin tools")
        else:
            # Show message when admin tools are disabled
            no_tools_action = QAction("(Enable Admin Tools in Settings → Advanced)", self.main_window)
            no_tools_action.setEnabled(False)
            self.tools_menu.addAction(no_tools_action)

            logger.debug("Tools menu populated (admin tools disabled)")

    # =========================================================================
    # Font Size Actions
    # =========================================================================

    def on_increase_font(self):
        """Handle View -> Font Size -> Increase."""
        logger.debug("Increase font size requested")
        self.theme_manager.increase_font_size()

    def on_decrease_font(self):
        """Handle View -> Font Size -> Decrease."""
        logger.debug("Decrease font size requested")
        self.theme_manager.decrease_font_size()

    def on_reset_font(self):
        """Handle View -> Font Size -> Reset."""
        logger.debug("Reset font size requested")
        self.theme_manager.reset_font_size()

    # =========================================================================
    # Theme Actions
    # =========================================================================

    def on_change_theme(self, theme: str):
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

    def on_change_roster_mode(self, mode: str):
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

    # =========================================================================
    # Log Viewer Actions
    # =========================================================================

    def on_view_call_log(self):
        """Handle View -> Calls."""
        logger.debug("View Call Log requested")

        from ..call_log_dialog import CallLogDialog

        # Create dialog if it doesn't exist or was closed
        if not hasattr(self.main_window, '_call_log_dialog') or not self.main_window._call_log_dialog.isVisible():
            self.main_window._call_log_dialog = CallLogDialog(parent=self.main_window)
            self.main_window._call_log_dialog.show()
        else:
            # Bring existing dialog to front
            self.main_window._call_log_dialog.raise_()
            self.main_window._call_log_dialog.activateWindow()

    def on_view_app_log(self, account_id: int):
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
        from ..log_viewer import LogViewer
        log_path = self.paths.account_app_log_path(account_id)
        viewer = LogViewer(
            log_path=log_path,
            title=f"Account {account_id} - Application Log",
            parent=None  # Non-modal, independent window
        )
        viewer.show()

        # Track it
        self.log_viewers[key] = viewer

    def on_view_main_log(self):
        """Handle View -> Logs -> Main Log."""
        logger.debug("View Main Log requested")

        # Check if window is already open
        key = ('global', 'main')
        if key in self.log_viewers and self.log_viewers[key].isVisible():
            self.log_viewers[key].raise_()
            self.log_viewers[key].activateWindow()
            return

        # Open new log viewer for main log
        from ..log_viewer import LogViewer
        log_path = self.paths.main_log_path()
        viewer = LogViewer(
            log_path=log_path,
            title="Main Application Log",
            parent=None  # Non-modal, independent window
        )
        viewer.show()
        self.log_viewers[key] = viewer

        logger.debug(f"Main log viewer opened: {log_path}")

    def on_view_xml_log(self):
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
        from ..log_viewer import LogViewer
        log_path = self.paths.log_dir / 'xmpp-protocol.log'
        viewer = LogViewer(
            log_path=log_path,
            title="XMPP Protocol Log (All Accounts)",
            parent=None  # Non-modal, independent window
        )
        viewer.show()

        # Track it
        self.log_viewers[key] = viewer
