"""
Join/Create MUC room dialog for Siproxylin.
"""

import asyncio
import logging
import base64
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QCheckBox, QPushButton, QLabel, QMessageBox
)
from PySide6.QtCore import Qt

from ..db.database import get_db
from ..core import get_account_manager


logger = logging.getLogger('siproxylin.join_room_dialog')


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
        self.db = get_db()
        self.account_manager = get_account_manager()

        # Window setup
        self.setWindowTitle("Add Group")
        self.setMinimumWidth(500)

        # Create UI
        self._create_ui()

        logger.info(f"Join room dialog opened for account {account_id}")

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Form layout for inputs
        form = QFormLayout()

        # Room JID
        self.room_jid_input = QLineEdit()
        self.room_jid_input.setPlaceholderText("room@conference.example.com")
        form.addRow("Room Address:", self.room_jid_input)

        # Nickname
        self.nick_input = QLineEdit()
        self.nick_input.setPlaceholderText("YourNickname")
        form.addRow("Nickname:", self.nick_input)

        # Password (optional)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("(optional)")
        form.addRow("Password:", self.password_input)

        # Bookmark name (optional)
        self.bookmark_name_input = QLineEdit()
        self.bookmark_name_input.setPlaceholderText("(optional)")
        form.addRow("Bookmark Name:", self.bookmark_name_input)

        # Autojoin checkbox
        self.autojoin_checkbox = QCheckBox("Automatically join on startup")
        self.autojoin_checkbox.setChecked(False)
        form.addRow("", self.autojoin_checkbox)

        layout.addLayout(form)

        # Info label
        info_label = QLabel("💡 Joining a room will add it to your bookmarks.")
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
            QMessageBox.warning(self, "Error", "Please enter a room address.")
            return

        if not nick:
            QMessageBox.warning(self, "Error", "Please enter a nickname.")
            return

        # Basic JID validation
        if '@' not in room_jid or '.' not in room_jid:
            QMessageBox.warning(
                self, "Error",
                "Invalid room address. Format: room@conference.server.com"
            )
            return

        try:
            # Get or create JID entry for room
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if jid_row:
                jid_id = jid_row['id']
            else:
                cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (room_jid,))
                jid_id = cursor.lastrowid

            # Encode password if provided
            encoded_password = base64.b64encode(password.encode()).decode() if password else None

            # Store bookmark locally
            self.db.execute("""
                INSERT INTO bookmark (account_id, jid_id, name, nick, password, autojoin)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, jid_id) DO UPDATE SET
                    name = excluded.name,
                    nick = excluded.nick,
                    password = excluded.password,
                    autojoin = excluded.autojoin
            """, (
                self.account_id,
                jid_id,
                bookmark_name or None,
                nick,
                encoded_password,
                1 if autojoin else 0
            ))
            self.db.commit()

            logger.info(f"Room bookmark saved locally: {room_jid} (autojoin={autojoin})")

            # Sync bookmark to server (XEP-0402)
            account = self.account_manager.get_account(self.account_id)
            if account and account.client and account.is_connected():
                asyncio.create_task(
                    account.client.add_bookmark(
                        jid=room_jid,
                        name=bookmark_name or room_jid,
                        nick=nick,
                        password=password,
                        autojoin=autojoin
                    )
                )
                logger.info(f"Syncing bookmark to server: {room_jid} (autojoin={autojoin})")
            else:
                logger.warning(f"Cannot sync bookmark - account offline")

            # Store data for parent to access
            self.room_jid = room_jid
            self.nick = nick
            self.password = password

            self.accept()

        except Exception as e:
            logger.error(f"Failed to save bookmark: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save bookmark: {e}")
