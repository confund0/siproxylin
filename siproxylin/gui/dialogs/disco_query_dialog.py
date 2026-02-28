"""
Dialog for querying XMPP Service Discovery (XEP-0030) for any JID.

Part of Admin Tools feature.
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QPushButton
)
from PySide6.QtCore import Signal

from ...core import get_account_manager


logger = logging.getLogger('siproxylin.disco_query_dialog')


class DiscoQueryDialog(QDialog):
    """Dialog for selecting account and JID to query disco info."""

    # Signal emitted when user clicks Query button
    # Args: (account_id: int, target_jid: str)
    disco_query_requested = Signal(int, str)

    def __init__(self, parent=None):
        """
        Initialize disco query dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.account_manager = get_account_manager()

        self.setWindowTitle("Service Discovery")
        self.setModal(True)
        self.setMinimumWidth(500)

        self._setup_ui()
        self._populate_accounts()

    def _setup_ui(self):
        """Setup the dialog UI."""
        layout = QVBoxLayout()

        # Header
        header_label = QLabel("<b>Query XMPP Service Discovery</b>")
        header_label.setStyleSheet("font-size: 12pt; padding: 5px;")
        layout.addWidget(header_label)

        layout.addSpacing(15)

        # Account selector
        account_label = QLabel("Query from account:")
        layout.addWidget(account_label)

        self.account_combo = QComboBox()
        layout.addWidget(self.account_combo)

        layout.addSpacing(10)

        # JID input
        jid_label = QLabel("Target JID:")
        layout.addWidget(jid_label)

        self.jid_input = QLineEdit()
        self.jid_input.setPlaceholderText("user@example.com or conference.example.com")
        self.jid_input.textChanged.connect(self._on_jid_changed)
        layout.addWidget(self.jid_input)

        layout.addSpacing(10)

        # Info text
        info_label = QLabel(
            "Enter any JID to discover supported features, identities, and extended information."
        )
        info_label.setStyleSheet("color: gray; font-size: 9pt;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        layout.addSpacing(15)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        self.query_button = QPushButton("Query")
        self.query_button.setDefault(True)
        self.query_button.clicked.connect(self._on_query_clicked)
        self.query_button.setEnabled(False)
        button_layout.addWidget(self.query_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Focus JID input
        self.jid_input.setFocus()

    def _populate_accounts(self):
        """Populate the account dropdown with connected accounts."""
        self.account_combo.clear()

        # Filter for connected accounts only
        connected_accounts = [
            account for account in self.account_manager.accounts.values()
            if account.client and account.is_connected()
        ]

        if not connected_accounts:
            # No connected accounts
            self.account_combo.addItem("(No connected accounts)", None)
            self.account_combo.setEnabled(False)
            self.query_button.setEnabled(False)
            logger.debug("No connected accounts available for disco query")
            return

        # Sort accounts by bare JID for consistent ordering
        connected_accounts.sort(key=lambda a: a.account_data['bare_jid'])

        for account in connected_accounts:
            # Display format: "nickname (user@server.com)"
            nickname = account.account_data.get('nickname', '')
            bare_jid = account.account_data['bare_jid']

            if nickname:
                display_text = f"{nickname} ({bare_jid})"
            else:
                display_text = bare_jid

            # Store account_id as user data
            self.account_combo.addItem(display_text, account.account_id)

        self.account_combo.setEnabled(True)
        logger.debug(f"Populated {len(connected_accounts)} connected accounts")

    def _on_jid_changed(self, text):
        """Enable query button only if JID is entered and account is selected."""
        has_jid = len(text.strip()) > 0
        has_account = self.account_combo.currentData() is not None
        self.query_button.setEnabled(has_jid and has_account)

    def _on_query_clicked(self):
        """Handle query button click - emit signal and accept."""
        jid = self.jid_input.text().strip()
        account_id = self.account_combo.currentData()

        if not jid or account_id is None:
            return

        logger.info(f"Disco query requested: account_id={account_id}, target_jid={jid}")

        # Emit signal with query parameters
        self.disco_query_requested.emit(account_id, jid)

        # Accept and close dialog
        self.accept()
