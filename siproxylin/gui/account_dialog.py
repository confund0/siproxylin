"""
Account creation/editing dialog for Siproxylin.

Allows configuration of:
- Basic XMPP settings (JID, password, server override)
- Proxy settings (SOCKS5/HTTP per account)
- TLS settings (ignore errors, strong TLS, client cert)
- OMEMO settings (enabled, mode, blind trust)
- Feature toggles (WebRTC, carbons, typing, read receipts)
- Logging settings
"""

import logging
import base64
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QCheckBox, QComboBox, QPushButton, QLabel,
    QSpinBox, QFileDialog, QGroupBox, QWidget, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QIntValidator

from ..db.database import get_db
from ..utils.paths import get_paths
from ..core.brewery import get_account_brewery
from ..core.constants import ProxyType
from .threads.connection_test import ConnectionTestThread


logger = logging.getLogger('siproxylin.account_dialog')


class AccountDialog(QDialog):
    """
    Dialog for creating or editing an XMPP account.
    """

    # Signals
    account_saved = Signal(int, bool)  # (account_id, enabled_state)
    account_deleted = Signal(int)  # (account_id)

    def __init__(self, account_data=None, parent=None):
        """
        Initialize account dialog.

        Args:
            account_data: Dictionary of account data to edit (None for new account)
            parent: Parent widget
        """
        super().__init__(parent)

        self.account_data = account_data
        self.account_id = account_data['id'] if account_data else None
        self.db = get_db()
        self.paths = get_paths()

        # Connection test worker
        self.test_thread = None

        # Window setup
        if self.account_id:
            self.setWindowTitle(f"Edit Account {self.account_id}")
        else:
            self.setWindowTitle("New Account")

        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        # Create UI
        self._create_ui()

        # Load existing account data if editing
        if self.account_id:
            self._load_account_data()

        logger.info(f"Account dialog opened (account_id: {self.account_id})")

    def closeEvent(self, event):
        """Handle dialog close - ensure test thread is stopped."""
        if self.test_thread and self.test_thread.isRunning():
            logger.info("Dialog closing, waiting for test thread to finish...")
            self.test_thread.wait(2000)  # Wait up to 2 seconds
            if self.test_thread.isRunning():
                logger.warning("Test thread still running, terminating...")
                self.test_thread.terminate()
                self.test_thread.wait(1000)
        super().closeEvent(event)

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Tab widget for different setting categories
        self.tabs = QTabWidget()

        # Create tabs
        self._create_basic_tab()
        self._create_connection_tab()
        self._create_security_tab()
        self._create_features_tab()
        self._create_logging_tab()
        self._create_server_info_tab()

        layout.addWidget(self.tabs)

        # Bottom buttons
        buttons_layout = QHBoxLayout()

        # Delete button (only visible when editing existing account)
        if self.account_id:
            self.delete_button = QPushButton("Delete Account")
            self.delete_button.setStyleSheet("background-color: #d9534f; color: white;")
            self.delete_button.clicked.connect(self._on_delete_account)
            buttons_layout.addWidget(self.delete_button)

        buttons_layout.addStretch()

        # Test connection button with status
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self._on_test_connection)
        buttons_layout.addWidget(self.test_button)

        # Spinner (hidden by default) - using label with animation
        self.test_spinner = QLabel("⏳")
        self.test_spinner.setFixedWidth(20)
        self.test_spinner.setVisible(False)
        buttons_layout.addWidget(self.test_spinner)

        # Test status icon (fixed width to prevent button movement)
        self.test_status_icon = QLabel("")
        self.test_status_icon.setFixedWidth(20)
        self.test_status_icon.setVisible(False)
        buttons_layout.addWidget(self.test_status_icon)

        buttons_layout.addSpacing(20)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons_layout.addWidget(self.cancel_button)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._on_save)
        self.save_button.setDefault(True)
        buttons_layout.addWidget(self.save_button)

        layout.addLayout(buttons_layout)

    def _create_basic_tab(self):
        """Create basic settings tab."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # JID (required)
        self.jid_input = QLineEdit()
        self.jid_input.setPlaceholderText("user@example.com")
        layout.addRow("JID*:", self.jid_input)

        # Password (required)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Password")
        layout.addRow("Password*:", self.password_input)

        # Show password checkbox
        self.show_password_checkbox = QCheckBox("Show password")
        self.show_password_checkbox.stateChanged.connect(self._on_show_password_changed)
        layout.addRow("", self.show_password_checkbox)

        # Nickname (XEP-0172) - published to XMPP server
        self.nickname_input = QLineEdit()
        self.nickname_input.setPlaceholderText("Published to contacts (XEP-0172)")
        layout.addRow("Nickname:", self.nickname_input)

        # MUC Nickname (optional) - used for group chat joins
        self.muc_nickname_input = QLineEdit()
        self.muc_nickname_input.setPlaceholderText("For group chats (optional, uses nickname if empty)")
        layout.addRow("MUC Nickname:", self.muc_nickname_input)

        # Resource (optional)
        self.resource_input = QLineEdit()
        self.resource_input.setPlaceholderText("siproxylin.{unique} (auto-generated if empty)")
        layout.addRow("Resource:", self.resource_input)

        # Enabled
        self.enabled_checkbox = QCheckBox("Account enabled")
        self.enabled_checkbox.setChecked(True)
        layout.addRow("", self.enabled_checkbox)

        self.tabs.addTab(tab, "Basic")

    def _create_connection_tab(self):
        """Create connection settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Server override group
        server_group = QGroupBox("Server Override")
        server_layout = QFormLayout()

        self.server_override_input = QLineEdit()
        self.server_override_input.setPlaceholderText("Auto-discover via SRV (leave empty)")
        self.server_override_input.textChanged.connect(self._on_server_override_changed)
        server_layout.addRow("Server:", self.server_override_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(5223)
        self.port_input.setEnabled(False)  # Disabled by default (autodiscover mode)
        server_layout.addRow("Port:", self.port_input)

        server_group.setLayout(server_layout)
        layout.addWidget(server_group)

        # Proxy group
        proxy_group = QGroupBox("Proxy Settings")
        proxy_layout = QFormLayout()

        self.proxy_type_combo = QComboBox()
        self.proxy_type_combo.addItems(['None', 'SOCKS5', 'HTTP'])
        self.proxy_type_combo.currentTextChanged.connect(self._on_proxy_type_changed)
        proxy_layout.addRow("Proxy Type:", self.proxy_type_combo)

        self.proxy_host_input = QLineEdit()
        self.proxy_host_input.setPlaceholderText("127.0.0.1")
        self.proxy_host_input.setEnabled(False)
        proxy_layout.addRow("Proxy Host:", self.proxy_host_input)

        self.proxy_port_input = QSpinBox()
        self.proxy_port_input.setRange(1, 65535)
        self.proxy_port_input.setValue(1080)
        self.proxy_port_input.setEnabled(False)
        proxy_layout.addRow("Proxy Port:", self.proxy_port_input)

        self.proxy_username_input = QLineEdit()
        self.proxy_username_input.setPlaceholderText("Optional")
        self.proxy_username_input.setEnabled(False)
        proxy_layout.addRow("Proxy Username:", self.proxy_username_input)

        self.proxy_password_input = QLineEdit()
        self.proxy_password_input.setEchoMode(QLineEdit.Password)
        self.proxy_password_input.setPlaceholderText("Optional")
        self.proxy_password_input.setEnabled(False)
        proxy_layout.addRow("Proxy Password:", self.proxy_password_input)

        proxy_group.setLayout(proxy_layout)
        layout.addWidget(proxy_group)

        layout.addStretch()
        self.tabs.addTab(tab, "Connection")

    def _create_security_tab(self):
        """Create security settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # TLS settings group
        tls_group = QGroupBox("TLS Settings")
        tls_layout = QVBoxLayout()

        self.require_strong_tls_checkbox = QCheckBox("Require strong TLS")
        self.require_strong_tls_checkbox.setChecked(True)
        tls_layout.addWidget(self.require_strong_tls_checkbox)

        self.ignore_tls_errors_checkbox = QCheckBox("Ignore TLS certificate errors (DANGEROUS!)")
        tls_layout.addWidget(self.ignore_tls_errors_checkbox)

        # Client certificate
        cert_layout = QHBoxLayout()
        cert_label = QLabel("Client Certificate:")
        cert_layout.addWidget(cert_label)

        self.client_cert_input = QLineEdit()
        self.client_cert_input.setPlaceholderText("Path to .pem file (optional)")
        cert_layout.addWidget(self.client_cert_input)

        self.client_cert_browse_button = QPushButton("Browse...")
        self.client_cert_browse_button.clicked.connect(self._on_browse_client_cert)
        cert_layout.addWidget(self.client_cert_browse_button)

        tls_layout.addLayout(cert_layout)
        tls_group.setLayout(tls_layout)
        layout.addWidget(tls_group)

        # OMEMO settings group
        omemo_group = QGroupBox("OMEMO Encryption")
        omemo_layout = QVBoxLayout()

        self.omemo_enabled_checkbox = QCheckBox("Enable OMEMO encryption")
        self.omemo_enabled_checkbox.setChecked(True)
        self.omemo_enabled_checkbox.stateChanged.connect(self._on_omemo_enabled_changed)
        omemo_layout.addWidget(self.omemo_enabled_checkbox)

        mode_layout = QFormLayout()
        self.omemo_mode_combo = QComboBox()
        self.omemo_mode_combo.addItems(['default', 'optional', 'required', 'off'])
        mode_layout.addRow("OMEMO Mode:", self.omemo_mode_combo)
        omemo_layout.addLayout(mode_layout)

        self.omemo_blind_trust_checkbox = QCheckBox("Blind Trust Before Verification (BTBV)")
        self.omemo_blind_trust_checkbox.setChecked(True)
        omemo_layout.addWidget(self.omemo_blind_trust_checkbox)

        omemo_group.setLayout(omemo_layout)
        layout.addWidget(omemo_group)

        layout.addStretch()
        self.tabs.addTab(tab, "Security")

    def _create_features_tab(self):
        """Create features tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Feature toggles
        features_group = QGroupBox("Feature Toggles")
        features_layout = QVBoxLayout()

        self.webrtc_enabled_checkbox = QCheckBox("Enable WebRTC (audio/video calls)")
        features_layout.addWidget(self.webrtc_enabled_checkbox)

        self.carbons_enabled_checkbox = QCheckBox("Enable Message Carbons (multi-device sync)")
        self.carbons_enabled_checkbox.setChecked(True)
        features_layout.addWidget(self.carbons_enabled_checkbox)

        self.typing_notifications_checkbox = QCheckBox("Send typing notifications")
        self.typing_notifications_checkbox.setChecked(True)
        features_layout.addWidget(self.typing_notifications_checkbox)

        self.read_receipts_checkbox = QCheckBox("Send read receipts")
        self.read_receipts_checkbox.setChecked(True)
        features_layout.addWidget(self.read_receipts_checkbox)

        features_group.setLayout(features_layout)
        layout.addWidget(features_group)

        layout.addStretch()
        self.tabs.addTab(tab, "Features")

    def _create_logging_tab(self):
        """Create logging settings tab."""
        tab = QWidget()
        layout = QFormLayout(tab)

        # Log level
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
        self.log_level_combo.setCurrentText('INFO')
        layout.addRow("Log Level:", self.log_level_combo)

        # Log retention
        self.log_retention_spinbox = QSpinBox()
        self.log_retention_spinbox.setRange(0, 365)
        self.log_retention_spinbox.setValue(30)
        self.log_retention_spinbox.setSuffix(" days")
        self.log_retention_spinbox.setSpecialValueText("Forever")
        layout.addRow("Log Retention:", self.log_retention_spinbox)

        # Log toggles
        self.log_app_enabled_checkbox = QCheckBox("Enable application logging")
        self.log_app_enabled_checkbox.setChecked(True)
        layout.addRow("", self.log_app_enabled_checkbox)

        # XML protocol logging is global (see File → Settings → Logging)
        xml_note = QLabel("Note: XMPP protocol logging is configured globally in Settings.")
        xml_note.setStyleSheet("color: gray; font-size: 10pt;")
        xml_note.setWordWrap(True)
        layout.addRow("", xml_note)

        self.tabs.addTab(tab, "Logging")

    def _create_server_info_tab(self):
        """Create server information tab (read-only, queried on connect)."""
        from PySide6.QtWidgets import QTextEdit, QScrollArea

        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Info label
        info_label = QLabel(
            "Server information is queried automatically on each connection.\n"
            "This shows the current/last known server version and supported XEPs.\n"
            "If disconnected, information will show 'N/A' until reconnection."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #888; font-style: italic; padding: 10px;")
        layout.addWidget(info_label)

        # Server version section
        version_group = QGroupBox("Server Version (XEP-0092)")
        version_layout = QFormLayout(version_group)

        self.server_name_label = QLabel("N/A")
        self.server_name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        version_layout.addRow("Software:", self.server_name_label)

        self.server_version_label = QLabel("N/A")
        self.server_version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        version_layout.addRow("Version:", self.server_version_label)

        self.server_os_label = QLabel("N/A")
        self.server_os_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        version_layout.addRow("OS:", self.server_os_label)

        layout.addWidget(version_group)

        # Server features section
        features_group = QGroupBox("Server Features (XEP-0030)")
        features_layout = QVBoxLayout(features_group)

        # XEP list (scrollable text area)
        self.server_xeps_text = QTextEdit()
        self.server_xeps_text.setReadOnly(True)
        self.server_xeps_text.setPlaceholderText("Connect to see supported XEPs...")
        self.server_xeps_text.setMaximumHeight(300)
        features_layout.addWidget(self.server_xeps_text)

        layout.addWidget(features_group)

        # Spacer
        layout.addStretch()

        self.tabs.addTab(tab, "Server Info")

        # Load server info if editing existing account
        if self.account_id:
            self._load_server_info()

    def _load_server_info(self):
        """Load server info from AccountManager (in-memory data)."""
        from ..core import get_account_manager

        account_manager = get_account_manager()
        if self.account_id not in account_manager.accounts:
            return

        xmpp_account = account_manager.accounts[self.account_id]

        # Load server version
        if xmpp_account.server_version:
            version = xmpp_account.server_version
            if not version.get('error'):
                self.server_name_label.setText(version.get('name') or 'N/A')
                self.server_version_label.setText(version.get('version') or 'N/A')
                self.server_os_label.setText(version.get('os') or 'N/A')
            else:
                error_msg = f"Error: {version['error']}"
                self.server_name_label.setText(error_msg)
                self.server_version_label.setText('N/A')
                self.server_os_label.setText('N/A')

        # Load server features
        if xmpp_account.server_features:
            features = xmpp_account.server_features
            if not features.get('error'):
                xeps = features.get('xeps', [])
                if xeps:
                    xep_lines = [f"XEP-{xep['number']}: {xep['name']}" for xep in xeps]
                    self.server_xeps_text.setPlainText('\n'.join(xep_lines))
                else:
                    self.server_xeps_text.setPlainText('No recognized XEPs found.')
            else:
                self.server_xeps_text.setPlainText(f"Error querying features: {features['error']}")

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _on_show_password_changed(self, state):
        """Toggle password visibility."""
        if state == Qt.CheckState.Checked.value or state == Qt.Checked:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

    def _on_server_override_changed(self, text):
        """Enable/disable port field based on server override."""
        # Port is only used when server is manually specified
        has_server = bool(text.strip())
        self.port_input.setEnabled(has_server)
        # Theme handles enabled/disabled styling via QSpinBox and QSpinBox:disabled

    def _on_proxy_type_changed(self, proxy_type):
        """Enable/disable proxy fields based on type."""
        enabled = proxy_type != 'None'
        self.proxy_host_input.setEnabled(enabled)
        self.proxy_port_input.setEnabled(enabled)
        self.proxy_username_input.setEnabled(enabled)
        self.proxy_password_input.setEnabled(enabled)

    def _on_omemo_enabled_changed(self, state):
        """Enable/disable OMEMO mode combo."""
        enabled = state == Qt.Checked
        self.omemo_mode_combo.setEnabled(enabled)
        self.omemo_blind_trust_checkbox.setEnabled(enabled)

    def _on_browse_client_cert(self):
        """Browse for client certificate file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Client Certificate",
            str(Path.home()),
            "PEM Files (*.pem);;All Files (*)"
        )
        if file_path:
            self.client_cert_input.setText(file_path)

    def _on_test_connection(self):
        """Test XMPP connection with current settings."""
        # Validate required fields
        jid = self.jid_input.text().strip()
        password = self.password_input.text()

        if not jid or not password:
            self.test_status_icon.setText("❌")
            self.test_status_icon.setToolTip("Enter JID and password")
            self.test_status_icon.setStyleSheet("color: #d9534f;")
            self.test_status_icon.setVisible(True)
            return

        if '@' not in jid:
            self.test_status_icon.setText("❌")
            self.test_status_icon.setToolTip("Invalid JID format")
            self.test_status_icon.setStyleSheet("color: #d9534f;")
            self.test_status_icon.setVisible(True)
            return

        # Hide previous status, show spinner
        self.test_status_icon.setVisible(False)
        self.test_button.setEnabled(False)
        self.test_spinner.setVisible(True)
        self.test_spinner.setToolTip("Testing connection...")

        # Get server settings
        server = self.server_override_input.text().strip() or None
        port = self.port_input.value() if server else None

        # Get proxy settings
        proxy_type = ProxyType.to_db_value(self.proxy_type_combo.currentText())
        proxy_host = self.proxy_host_input.text().strip() or None
        proxy_port = self.proxy_port_input.value() if proxy_type else None
        proxy_username = self.proxy_username_input.text().strip() or None
        proxy_password = self.proxy_password_input.text().strip() or None

        # Create and start test thread
        self.test_thread = ConnectionTestThread(
            jid, password, server, port,
            proxy_type=proxy_type,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password
        )
        self.test_thread.test_completed.connect(self._on_test_completed)
        self.test_thread.start()

    def _on_test_completed(self, success, message):
        """Handle test connection completion."""
        # Hide spinner
        self.test_spinner.setVisible(False)
        self.test_button.setEnabled(True)

        # Show result icon only
        if success:
            self.test_status_icon.setText("✓")
            self.test_status_icon.setToolTip("Connected successfully")
            self.test_status_icon.setStyleSheet("color: #5cb85c;")
            logger.info(f"Connection test successful: {message}")
        else:
            self.test_status_icon.setText("✗")
            self.test_status_icon.setToolTip(f"Connection failed: {message}")
            self.test_status_icon.setStyleSheet("color: #d9534f;")
            logger.error(f"Connection test failed: {message}")

        self.test_status_icon.setVisible(True)

        # Clean up thread properly
        if self.test_thread:
            # Thread should be finished, but wait a moment just in case
            if not self.test_thread.isFinished():
                self.test_thread.wait(500)
            self.test_thread.deleteLater()
            self.test_thread = None

    def _on_delete_account(self):
        """Delete account with confirmation."""
        if not self.account_id:
            return

        # Get account details from stored data
        jid = self.account_data['bare_jid']
        # Decode password from database (base64 encoded)
        password = None
        if self.account_data.get('password'):
            password = base64.b64decode(self.account_data['password']).decode()

        # Create custom dialog with checkbox (following subscription dialog pattern)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Delete Account")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"Are you sure you want to delete account '{jid}'?")
        dialog.setInformativeText(
            "This will:\n"
            "- Remove the account and all its settings\n"
            "- Delete all message history for this account\n"
            "- Remove OMEMO encryption keys\n\n"
            "This action CANNOT be undone!"
        )

        # Add checkbox for server deletion
        from PySide6.QtWidgets import QCheckBox
        delete_from_server_checkbox = QCheckBox("Also delete account from server")
        delete_from_server_checkbox.setToolTip("Send deletion request to XMPP server (XEP-0077)")
        dialog.setCheckBox(delete_from_server_checkbox)

        dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dialog.setDefaultButton(QMessageBox.No)

        reply = dialog.exec()

        if reply != QMessageBox.Yes:
            return

        delete_from_server = delete_from_server_checkbox.isChecked()

        # Validate prerequisites for server deletion
        if delete_from_server and not password:
            QMessageBox.warning(
                self,
                "Password Required",
                "Cannot delete account from server: password not available.\n\n"
                "The account will only be deleted locally."
            )
            delete_from_server = False

        # Show progress dialog
        from .dialogs.deletion_progress_dialog import DeletionProgressDialog
        progress_dialog = DeletionProgressDialog(jid, delete_from_server, self)

        # When progress dialog closes, close account dialog too
        progress_dialog.finished.connect(lambda: self.accept())

        progress_dialog.show()

        # Start async deletion task (store reference to avoid "task destroyed" warnings)
        import asyncio
        self._deletion_task = asyncio.create_task(self._perform_account_deletion(
            jid, password, delete_from_server, progress_dialog
        ))

    async def _perform_account_deletion(self, jid: str, password: str,
                                       delete_from_server: bool, progress_dialog):
        """Perform complete account deletion: server (if requested) then local."""
        import asyncio

        # Step 0: Disconnect account if connected (prevents <conflict> from multiple streams)
        account_brewery = get_account_brewery()
        account = account_brewery.get_account(self.account_id)
        if account and account.is_connected():
            logger.info(f"Disconnecting account {self.account_id} before deletion...")

            # Create event to wait for disconnection
            disconnected_event = asyncio.Event()

            def on_disconnected(account_id, state):
                if account_id == self.account_id and state == 'disconnected':
                    disconnected_event.set()

            # Connect signal and disconnect
            account.connection_state_changed.connect(on_disconnected)
            account.disconnect()

            # Wait for disconnect to complete (with timeout)
            try:
                await asyncio.wait_for(disconnected_event.wait(), timeout=5.0)
                logger.info(f"Account {self.account_id} disconnected")
            except asyncio.TimeoutError:
                logger.warning(f"Disconnect timeout for account {self.account_id}, proceeding anyway")
            finally:
                account.connection_state_changed.disconnect(on_disconnected)

        # Step 1: Delete from server if requested
        if delete_from_server:
            success = await self._delete_from_server_async(jid, password)
            progress_dialog.update_server_status(
                success,
                None if success else "Server rejected deletion request or connection failed"
            )

        # Step 2: Always delete locally (even if server deletion failed)
        try:
            # Delete related data (cascading should handle most of this via foreign keys)
            self.db.execute("DELETE FROM account WHERE id = ?", (self.account_id,))
            self.db.commit()

            logger.info(f"Account {self.account_id} ({jid}) deleted locally")

            # Emit signal so main window can clean up
            self.account_deleted.emit(self.account_id)

            progress_dialog.update_local_status(True)

            # Account dialog will close when progress dialog closes
            # (progress dialog is modal, blocks until user clicks Finish/Close)

        except Exception as e:
            logger.error(f"Failed to delete account locally: {e}")
            import traceback
            logger.error(traceback.format_exc())
            progress_dialog.update_local_status(False, str(e))

    async def _delete_from_server_async(self, jid: str, password: str) -> bool:
        """Attempt to delete account from XMPP server. Returns True if successful."""
        from drunk_xmpp import delete_account

        logger.info(f"Attempting to delete {jid} from server...")

        # Get proxy settings from the account data
        proxy_settings = None
        proxy_type = self.account_data.get('proxy_type')
        if proxy_type:
            proxy_host = self.account_data.get('proxy_host')
            proxy_port = self.account_data.get('proxy_port')
            proxy_username = self.account_data.get('proxy_username')
            proxy_password = self.account_data.get('proxy_password')

            if proxy_host and proxy_port:
                proxy_settings = {
                    'proxy_type': proxy_type,
                    'proxy_host': proxy_host,
                    'proxy_port': proxy_port
                }
                if proxy_username and proxy_password:
                    # Decode proxy password if it's base64 encoded
                    try:
                        proxy_password_decoded = base64.b64decode(proxy_password).decode()
                        proxy_settings['proxy_username'] = proxy_username
                        proxy_settings['proxy_password'] = proxy_password_decoded
                    except Exception:
                        # If decode fails, use as-is
                        proxy_settings['proxy_username'] = proxy_username
                        proxy_settings['proxy_password'] = proxy_password

        # Call async deletion
        try:
            result = await delete_account(jid, password, proxy_settings)
            if result['success']:
                logger.info(f"Account {jid} deleted from server successfully")
                return True
            else:
                logger.error(f"Failed to delete {jid} from server: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"Server deletion exception: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _on_save(self):
        """Validate and save account."""
        # Validate required fields
        jid = self.jid_input.text().strip()
        password = self.password_input.text()

        if not jid:
            QMessageBox.warning(self, "Validation Error", "JID is required!")
            self.tabs.setCurrentIndex(0)  # Switch to Basic tab
            self.jid_input.setFocus()
            return

        if not password:
            QMessageBox.warning(self, "Validation Error", "Password is required!")
            self.tabs.setCurrentIndex(0)
            self.password_input.setFocus()
            return

        # Validate JID format (basic check)
        if '@' not in jid:
            QMessageBox.warning(self, "Validation Error", "Invalid JID format! Expected: user@domain")
            self.tabs.setCurrentIndex(0)
            self.jid_input.setFocus()
            return

        # Save account
        try:
            enabled = self.enabled_checkbox.isChecked()
            was_new_account = self.account_id is None

            self._save_account()

            # Emit signal for main window to handle account reload
            # (account_id is now set even for new accounts)
            self.account_saved.emit(self.account_id, enabled)
            logger.info(f"Account {self.account_id} saved (enabled={enabled}, new={was_new_account})")

            self.accept()
        except Exception as e:
            logger.error(f"Failed to save account: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save account:\n{e}")

    def _save_account(self):
        """Save account to database using AccountBrewery."""
        brewery = get_account_brewery()

        # Get JID and password
        bare_jid = self.jid_input.text().strip()
        password = self.password_input.text()  # Plain text - brewery will encode
        resource = self.resource_input.text().strip() or None

        # Build full JID with resource if provided
        if resource:
            jid = f"{bare_jid}/{resource}"
        else:
            jid = bare_jid

        # Proxy type conversion
        proxy_type = ProxyType.to_db_value(self.proxy_type_combo.currentText())

        # Extract all settings from form
        settings = {
            'nickname': self.nickname_input.text().strip() or None,
            'muc_nickname': self.muc_nickname_input.text().strip() or None,
            'resource': resource,
            'enabled': int(self.enabled_checkbox.isChecked()),
            'server_override': self.server_override_input.text().strip() or None,
            'port': self.port_input.value() if self.server_override_input.text().strip() else None,
            'proxy_type': proxy_type,
            'proxy_host': self.proxy_host_input.text().strip() or None,
            'proxy_port': self.proxy_port_input.value() if proxy_type else None,
            'proxy_username': self.proxy_username_input.text().strip() or None,
            'proxy_password': self.proxy_password_input.text().strip() or None,  # Plain text - brewery will encode
            'ignore_tls_errors': int(self.ignore_tls_errors_checkbox.isChecked()),
            'require_strong_tls': int(self.require_strong_tls_checkbox.isChecked()),
            'client_cert_path': self.client_cert_input.text().strip() or None,
            'omemo_enabled': int(self.omemo_enabled_checkbox.isChecked()),
            'omemo_mode': self.omemo_mode_combo.currentText(),
            'omemo_blind_trust': int(self.omemo_blind_trust_checkbox.isChecked()),
            'webrtc_enabled': int(self.webrtc_enabled_checkbox.isChecked()),
            'carbons_enabled': int(self.carbons_enabled_checkbox.isChecked()),
            'typing_notifications': int(self.typing_notifications_checkbox.isChecked()),
            'read_receipts': int(self.read_receipts_checkbox.isChecked()),
            'log_level': self.log_level_combo.currentText(),
            'log_retention_days': self.log_retention_spinbox.value(),
            'log_app_enabled': int(self.log_app_enabled_checkbox.isChecked()),
        }

        if self.account_id:
            # Update existing account
            settings['bare_jid'] = bare_jid  # Allow changing JID
            settings['password'] = password
            brewery.update_account(self.account_id, **settings)
            logger.info(f"Account {self.account_id} updated via AccountBrewery")
        else:
            # Create new account
            self.account_id = brewery.create_account(jid, password, **settings)
            logger.info(f"Account {self.account_id} created via AccountBrewery")

    def _load_account_data(self):
        """Load existing account data from database."""
        account = self.db.fetchone("SELECT * FROM account WHERE id = ?", (self.account_id,))

        if not account:
            logger.error(f"Account {self.account_id} not found")
            return

        # Basic tab
        self.jid_input.setText(account['bare_jid'])

        # Decode password
        if account['password']:
            password = base64.b64decode(account['password']).decode()
            self.password_input.setText(password)

        self.nickname_input.setText(account['nickname'] or '')
        self.muc_nickname_input.setText(account['muc_nickname'] or '')
        self.resource_input.setText(account['resource'] or '')
        self.enabled_checkbox.setChecked(bool(account['enabled']))

        # Connection tab
        self.server_override_input.setText(account['server_override'] or '')
        self.port_input.setValue(account['port'] or 5223)

        # Manually trigger server override handler to update port field state
        self._on_server_override_changed(account['server_override'] or '')

        if account['proxy_type']:
            self.proxy_type_combo.setCurrentText(ProxyType.for_display(account['proxy_type']))
            self.proxy_host_input.setText(account['proxy_host'] or '')
            self.proxy_port_input.setValue(account['proxy_port'] or 1080)
            self.proxy_username_input.setText(account['proxy_username'] or '')

            # Decode proxy password
            if account['proxy_password']:
                proxy_pass = base64.b64decode(account['proxy_password']).decode()
                self.proxy_password_input.setText(proxy_pass)

        # Security tab
        self.require_strong_tls_checkbox.setChecked(bool(account['require_strong_tls']))
        self.ignore_tls_errors_checkbox.setChecked(bool(account['ignore_tls_errors']))
        self.client_cert_input.setText(account['client_cert_path'] or '')

        self.omemo_enabled_checkbox.setChecked(bool(account['omemo_enabled']))
        self.omemo_mode_combo.setCurrentText(account['omemo_mode'] or 'default')
        self.omemo_blind_trust_checkbox.setChecked(bool(account['omemo_blind_trust']))

        # Features tab
        self.webrtc_enabled_checkbox.setChecked(bool(account['webrtc_enabled']))
        self.carbons_enabled_checkbox.setChecked(bool(account['carbons_enabled']))
        self.typing_notifications_checkbox.setChecked(bool(account['typing_notifications']))
        self.read_receipts_checkbox.setChecked(bool(account['read_receipts']))

        # Logging tab
        self.log_level_combo.setCurrentText(account['log_level'] or 'INFO')
        self.log_retention_spinbox.setValue(account['log_retention_days'] or 30)
        self.log_app_enabled_checkbox.setChecked(bool(account['log_app_enabled']))

        logger.info(f"Loaded account {self.account_id} data")
