"""
DialogManager - Manages dialog creation and launching.

Extracted from MainWindow to improve maintainability.
"""

import logging
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog


logger = logging.getLogger('siproxylin.dialog_manager')


class DialogManager:
    """
    Manages creation and launching of various application dialogs.

    Responsibilities:
    - Launch account dialogs (add, edit)
    - Launch contact dialogs (add, edit)
    - Launch room dialogs (join, details)
    - Launch settings and about dialogs
    - Connect dialog signals back to MainWindow handlers
    """

    def __init__(self, main_window):
        """
        Initialize DialogManager.

        Args:
            main_window: MainWindow instance (for accessing widgets and services)
        """
        self.main_window = main_window

        logger.debug("DialogManager initialized")

    def show_new_account_dialog(self):
        """Show dialog to create a new account."""
        logger.debug("New Account requested")

        from ..account_dialog import AccountDialog

        dialog = AccountDialog(parent=self.main_window)
        dialog.account_saved.connect(self.main_window._on_account_saved)
        dialog.account_deleted.connect(self.main_window._on_account_deleted)
        dialog.show()

    def show_create_account_wizard(self):
        """Show XEP-0077 registration wizard."""
        logger.debug("Create Account (XEP-0077) requested")

        from ..registration_wizard import RegistrationWizard

        wizard = RegistrationWizard(parent=self.main_window)
        wizard.account_registered.connect(self.main_window._on_account_registered)
        wizard.exec()

    def show_edit_account_dialog(self, account_id: int):
        """
        Show dialog to edit an account.

        Args:
            account_id: Account ID to edit
        """
        logger.debug(f"Edit Account {account_id} requested")

        from ..account_dialog import AccountDialog

        # Load account data
        account = self.main_window.db.fetchone("SELECT * FROM account WHERE id = ?", (account_id,))
        if not account:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self.main_window, "Error", f"Account {account_id} not found.")
            return

        # Open account dialog in edit mode
        dialog = AccountDialog(parent=self.main_window, account_data=dict(account))
        dialog.account_saved.connect(self.main_window._on_account_saved)
        dialog.account_deleted.connect(self.main_window._on_account_deleted)
        dialog.show()

    def show_new_contact_dialog(self, account_id: int):
        """
        Show dialog to add a new contact.

        Args:
            account_id: Account to add contact to
        """
        logger.debug(f"New Contact requested for account {account_id}")

        from ..contact_dialog import ContactDialog

        dialog = ContactDialog(account_id=account_id, parent=self.main_window)
        dialog.contact_saved.connect(self.main_window._on_contact_saved)
        dialog.accepted.connect(lambda: logger.debug("Contact saved successfully"))
        dialog.show()

    def show_edit_contact_dialog(self, account_id: int, jid: str, roster_id: int):
        """
        Show dialog to edit a contact.

        Args:
            account_id: Account ID
            jid: Contact JID
            roster_id: Roster entry ID
        """
        logger.debug(f"Edit contact requested: {jid}")

        from ..contact_dialog import ContactDialog

        dialog = ContactDialog(account_id=account_id, jid=jid, parent=self.main_window)
        dialog.contact_saved.connect(self.main_window._on_contact_saved)
        dialog.show()

    def show_join_room_dialog(self, account_id: int):
        """
        Show dialog to join a MUC room.

        Args:
            account_id: Account to join room with
        """
        logger.debug(f"Join room requested for account {account_id}")

        from ..join_room_dialog import JoinRoomDialog
        import asyncio

        dialog = JoinRoomDialog(account_id=account_id, parent=self.main_window)

        def on_accepted():
            # Get joined room info
            room_jid = dialog.room_jid
            nick = dialog.nick
            password = dialog.password if dialog.password else None

            logger.info(f"Joining room: {room_jid} as {nick}")

            # Add room to client configuration and join
            account = self.main_window.account_manager.get_account(account_id)
            if account and account.client:
                asyncio.create_task(account.add_and_join_room(room_jid, nick, password))
                logger.debug(f"Room join initiated: {room_jid}")

                # Refresh contact list to show new room
                self.main_window.contact_list.load_roster()
            else:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self.main_window, "Error", "Account not connected.")

        # Connect and show (non-blocking)
        dialog.accepted.connect(on_accepted)
        dialog.show()

    def show_settings_dialog(self):
        """Show application settings dialog."""
        logger.debug("Settings requested")

        from ..settings_dialog import SettingsDialog

        # Get call bridge from first available account (call settings are app-wide)
        call_bridge = None
        for account in self.main_window.account_manager.accounts.values():
            if hasattr(account, 'call_bridge'):
                call_bridge = account.call_bridge
                break

        dialog = SettingsDialog(self.main_window, call_bridge=call_bridge)

        # Refresh Tools menu when settings are saved (admin_tools_enabled might have changed)
        dialog.accepted.connect(self._on_settings_saved)

        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _on_settings_saved(self):
        """Handle settings dialog accepted - refresh dynamic menus."""
        logger.debug("Settings saved, refreshing Tools menu")
        self.main_window.menu_manager.populate_tools_menu()

    def show_about_dialog(self):
        """Show about dialog."""
        logger.debug("About dialog requested")

        from ..about_dialog import AboutDialog

        dialog = AboutDialog(parent=self.main_window)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def show_muc_details_dialog(self, account_id: int, room_jid: str):
        """
        Show MUC room details dialog.

        Args:
            account_id: Account ID
            room_jid: Room JID
        """
        logger.debug(f"View MUC details requested: {room_jid}")

        from ..muc_details_dialog import MUCDetailsDialog

        dialog = MUCDetailsDialog(
            account_id=account_id,
            room_jid=room_jid,
            parent=self.main_window
        )
        # Connect dialog's signals to MainWindow methods
        dialog.leave_room_requested.connect(self.main_window.leave_muc)
        dialog.destroy_room_requested.connect(self.main_window.destroy_muc)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
