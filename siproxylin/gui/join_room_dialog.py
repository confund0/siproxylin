"""
Join/Create MUC room dialog for Siproxylin.
"""

import asyncio
import logging
from typing import Optional, Tuple
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QCheckBox, QPushButton, QLabel, QMessageBox
)
from PySide6.QtCore import Qt
from slixmpp import JID
from slixmpp.jid import InvalidJID

from ..core import get_account_manager


logger = logging.getLogger('siproxylin.join_room_dialog')

# Characters a room name cannot have (XEP-0106 list); white space is also not allowed
ROOM_NAME_BAD_CHARS = set(' "&\'/:<>@')


def resolve_room_jid(text: str, service: Optional[str], domain: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Make the full room JID from the Room field.

    A text with '@' is a full address: it must be a valid bare JID
    (room@service, no resource, no white space). It is used in the
    normal form of the JID (lowercase).
    A text without '@' is a room name: the name in lowercase, '@' and the
    group chat service of the server.

    Args:
        text: Text of the Room field
        service: Group chat service JID, or None if not found
        domain: Domain of the account (for the error text)

    Returns:
        (room_jid, None) if OK, (None, error text) if not
    """
    text = text.strip()
    if not text:
        return None, "Please enter a room name or address."

    if '@' in text:
        bad_address = "Invalid room address. Format: room@conference.server.com"
        if '.' not in text or '/' in text or any(c.isspace() for c in text):
            return None, bad_address
        try:
            jid = JID(text)
        except InvalidJID:
            return None, bad_address
        if not jid.local or jid.resource:
            return None, bad_address
        return jid.bare, None

    if any(c in ROOM_NAME_BAD_CHARS or c.isspace() for c in text):
        return None, "Room name cannot have spaces or any of these characters: \" & ' / : < > @"
    if not service:
        return None, f"No group chat service found on {domain}: enter the full address"
    return f"{text.lower()}@{service}", None


class JoinRoomDialog(QDialog):
    """Dialog for joining or creating a MUC room."""

    def __init__(self, account_id: int, parent=None):
        """
        Initialize join room dialog.

        Args:
            account_id: Account ID to join room with
            parent: Parent widget
        """
        super().__init__(parent)

        self.account_id = account_id
        self.account_manager = get_account_manager()

        # Group chat service of the server, for a room name without '@'
        self.muc_service = None
        self.muc_service_pending = True
        account = self.account_manager.get_account(account_id)
        bare_jid = account.account_data.get('bare_jid', '') if account else ''
        self.domain = bare_jid.split('@')[-1]

        # Window setup
        self.setWindowTitle("Add Group")
        self.setMinimumWidth(500)

        # Create UI
        self._create_ui()

        logger.info(f"Join room dialog opened for account {account_id}")

        # Look up the group chat service in the background
        lookup = self._lookup_muc_service()
        try:
            self._lookup_task = asyncio.create_task(lookup)
        except RuntimeError as e:
            lookup.close()
            logger.warning(f"Cannot look up the group chat service: {e}")
            self.muc_service_pending = False
            self._update_hint()

    async def _lookup_muc_service(self):
        """Find the group chat service of the account's server."""
        try:
            account = self.account_manager.get_account(self.account_id)
            if account and account.client:
                self.muc_service = await account.client.get_muc_service()
        except Exception as e:
            logger.warning(f"Group chat service lookup failed: {e}")
        self.muc_service_pending = False
        logger.debug(f"Group chat service of {self.domain}: {self.muc_service}")
        try:
            self._update_hint()
        except RuntimeError:
            pass  # Dialog already closed

    def _update_hint(self):
        """Show the full room JID for a room name without '@'."""
        text = self.room_jid_input.text().strip()
        if not text or '@' in text:
            self.room_hint_label.setText("")
            return
        if self.muc_service_pending:
            self.room_hint_label.setText("Looking up the group chat service...")
            return
        room_jid, error = resolve_room_jid(text, self.muc_service, self.domain)
        self.room_hint_label.setText(f"→ {room_jid}" if room_jid else error)

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Form layout for inputs
        form = QFormLayout()

        # Room JID
        self.room_jid_input = QLineEdit()
        self.room_jid_input.setPlaceholderText("name or room@service")
        self.room_jid_input.textChanged.connect(self._update_hint)
        form.addRow("Room:", self.room_jid_input)

        # Full room JID for a room name without '@'
        self.room_hint_label = QLabel("")
        self.room_hint_label.setStyleSheet("color: #888; font-size: 9pt;")
        form.addRow("", self.room_hint_label)

        # Nickname (optional - will use JID localpart as default)
        self.nick_input = QLineEdit()
        # Default nickname is localpart of JID (part before @)
        account = self.account_manager.get_account(self.account_id)
        default_nick = None
        if account:
            bare_jid = account.account_data.get('bare_jid', '')
            default_nick = bare_jid.split('@')[0] if '@' in bare_jid else bare_jid

        if default_nick:
            self.nick_input.setPlaceholderText(f"{default_nick} (default)")
        else:
            self.nick_input.setPlaceholderText("Nickname")
        form.addRow("Nickname:", self.nick_input)

        # Password (optional)
        password_layout = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("(optional)")
        password_layout.addWidget(self.password_input)

        self.show_password_checkbox = QCheckBox("Show")
        self.show_password_checkbox.toggled.connect(
            lambda checked: self.password_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        password_layout.addWidget(self.show_password_checkbox)
        form.addRow("Password:", password_layout)

        # Bookmark name (optional)
        self.bookmark_name_input = QLineEdit()
        self.bookmark_name_input.setPlaceholderText("(optional)")
        form.addRow("Bookmark Name:", self.bookmark_name_input)

        # Autojoin checkbox
        self.autojoin_checkbox = QCheckBox("Automatically join on startup")
        self.autojoin_checkbox.setChecked(True)
        form.addRow("", self.autojoin_checkbox)

        layout.addLayout(form)

        # Info label
        info_label = QLabel("💡 The room is added to your bookmarks when the join works.\n"
                            "A room that does not exist yet is created.")
        info_label.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(info_label)

        layout.addSpacing(10)

        # Bottom buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_button)

        join_button = QPushButton("Add Group")
        join_button.setDefault(True)
        join_button.clicked.connect(self._on_join)
        buttons_layout.addWidget(join_button)

        layout.addLayout(buttons_layout)

    def _on_join(self):
        """Handle Join button click."""
        room_jid = self.room_jid_input.text().strip()
        nick = self.nick_input.text().strip()
        password = self.password_input.text().strip()
        bookmark_name = self.bookmark_name_input.text().strip()
        autojoin = self.autojoin_checkbox.isChecked()

        # Validate inputs
        if not room_jid:
            QMessageBox.warning(self, "Error", "Please enter a room name or address.")
            return

        if '@' not in room_jid and self.muc_service_pending:
            QMessageBox.warning(self, "Error", "Still looking up the group chat service. Try again in a moment.")
            return

        # If nickname is empty, use the default (JID localpart)
        if not nick:
            account = self.account_manager.get_account(self.account_id)
            if account:
                bare_jid = account.account_data.get('bare_jid', '')
                nick = bare_jid.split('@')[0] if '@' in bare_jid else bare_jid
                logger.info(f"Using default nickname: {nick}")

            if not nick:
                QMessageBox.warning(self, "Error", "Could not determine default nickname.")
                return

        # Room name without '@': add the group chat service
        room_jid, error = resolve_room_jid(room_jid, self.muc_service, self.domain)
        if error:
            QMessageBox.warning(self, "Error", error)
            return

        # Store data for parent to access.
        # The bookmark is written after the join works (MucBarrel.on_muc_joined).
        self.room_jid = room_jid
        self.nick = nick
        self.password = password
        self.bookmark_name = bookmark_name
        self.autojoin = autojoin

        logger.info(f"Room to join: {room_jid} (autojoin={autojoin})")
        self.accept()
