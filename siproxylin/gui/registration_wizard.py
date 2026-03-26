"""
Registration wizard for XMPP account creation using XEP-0077 (In-Band Registration).

Multi-step wizard:
1. ServerPage: Enter XMPP server domain
2. ProxyPage: Optional proxy settings (Skip or Next)
3. CredentialsPage: Fill registration form (dynamic fields from server) with CAPTCHA support
4. RegistrationPage: Submit registration and create account in database

Uses session-based API with asyncio.create_task() for async operations.
"""

import logging
import asyncio
import base64
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QLabel, QPushButton, QComboBox, QSpinBox,
    QGroupBox, QCheckBox, QProgressBar, QTextEdit, QMessageBox, QWidget
)
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices

from drunk_xmpp import (
    create_registration_session,
    query_registration_form,
    submit_registration,
    close_registration_session
)
from ..core.brewery import get_account_brewery
from ..core.constants import ProxyType


logger = logging.getLogger('siproxylin.registration_wizard')


class RegistrationWizard(QWizard):
    """
    Multi-step wizard for registering new XMPP accounts.

    Emits account_registered(account_id) when successful.
    """

    # Signal emitted when account is successfully registered and saved
    account_registered = Signal(int)  # account_id

    # Page IDs
    PAGE_SERVER = 0
    PAGE_PROXY = 1
    PAGE_CREDENTIALS = 2
    PAGE_REGISTRATION = 3

    def __init__(self, parent=None):
        """Initialize registration wizard."""
        # Track if size has been locked (for CAPTCHA display)
        # IMPORTANT: Initialize BEFORE super().__init__() because Qt may call resizeEvent() during construction
        self.size_locked = False

        super().__init__(parent)

        self.setWindowTitle("Register New XMPP Account")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.HaveHelpButton, False)

        # Shared data between pages
        self.server_domain = None
        self.proxy_settings = None
        self.session_id = None  # Registration session ID
        self.registration_form = None
        self.captcha_data = None
        self.form_data = None
        self.registered_jid = None
        self.registered_password = None

        # Add pages
        self.setPage(self.PAGE_SERVER, ServerPage(self))
        self.setPage(self.PAGE_PROXY, ProxyPage(self))
        self.setPage(self.PAGE_CREDENTIALS, CredentialsPage(self))
        self.setPage(self.PAGE_REGISTRATION, RegistrationPage(self))

        logger.info("Registration wizard initialized")

    def resizeEvent(self, event):
        """Override resize event to prevent shrinking when size is locked."""
        if self.size_locked and hasattr(self, '_locked_size'):
            # Prevent shrinking below locked size
            new_size = event.size()
            if new_size.width() < self._locked_size.width() or new_size.height() < self._locked_size.height():
                self.resize(self._locked_size)
                return
        super().resizeEvent(event)

    def closeEvent(self, event):
        """Cleanup session when wizard closes."""
        if self.session_id:
            logger.info(f"Cleaning up registration session: {self.session_id}")
            asyncio.create_task(close_registration_session(self.session_id))
        super().closeEvent(event)


class ServerPage(QWizardPage):
    """Page 1: Enter XMPP server domain."""

    def __init__(self, wizard):
        """Initialize server page."""
        super().__init__()

        self.wizard = wizard

        self.setTitle("Select XMPP Server")
        self.setSubTitle("Enter the domain of the XMPP server where you want to register an account.")

        layout = QVBoxLayout()

        # Server input
        form_layout = QFormLayout()
        self.server_input = QLineEdit()
        self.server_input.setPlaceholderText("e.g., yax.im, conversations.im, xmpp.org")
        self.server_input.textChanged.connect(self.completeChanged)
        form_layout.addRow("Server domain:", self.server_input)

        layout.addLayout(form_layout)

        # Info label
        info_label = QLabel(
            "Note: Not all XMPP servers allow in-band registration. "
            "The server must support XEP-0077 (In-Band Registration)."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addWidget(info_label)

        # Server list links
        links_label = QLabel(
            'Find XMPP servers: '
            '<a href="https://providers.xmpp.net/">providers.xmpp.net</a> • '
            '<a href="https://jabberworld.info/servers/">jabberworld.info</a>'
        )
        links_label.setOpenExternalLinks(True)
        links_label.setWordWrap(True)
        links_label.setStyleSheet("color: #3498db; font-size: 10pt; margin-top: 10px;")
        layout.addWidget(links_label)

        layout.addStretch()
        self.setLayout(layout)

        # Register field for wizard to track
        self.registerField("server*", self.server_input)

    def isComplete(self):
        """Validate that server domain is entered."""
        server = self.server_input.text().strip()
        return len(server) > 0 and '.' in server

    def validatePage(self):
        """Store server domain when moving to next page."""
        self.wizard.server_domain = self.server_input.text().strip()
        logger.info(f"Server selected: {self.wizard.server_domain}")
        return True


class ProxyPage(QWizardPage):
    """Page 2: Optional proxy settings (Skip or Next)."""

    def __init__(self, wizard):
        """Initialize proxy page."""
        super().__init__()

        self.wizard = wizard

        self.setTitle("Proxy Settings (Optional)")
        self.setSubTitle("Configure proxy settings if needed, or skip to continue without a proxy.")

        layout = QVBoxLayout()

        # Proxy group
        proxy_group = QGroupBox("Proxy Configuration")
        proxy_layout = QFormLayout()

        # Proxy enabled checkbox
        self.proxy_enabled = QCheckBox("Use proxy")
        self.proxy_enabled.stateChanged.connect(self._on_proxy_toggled)
        proxy_layout.addRow("", self.proxy_enabled)

        # Proxy type
        self.proxy_type = QComboBox()
        self.proxy_type.addItems(["SOCKS5", "HTTP"])
        self.proxy_type.setEnabled(False)
        proxy_layout.addRow("Proxy type:", self.proxy_type)

        # Proxy host
        self.proxy_host = QLineEdit()
        self.proxy_host.setPlaceholderText("e.g., 127.0.0.1")
        self.proxy_host.setEnabled(False)
        proxy_layout.addRow("Host:", self.proxy_host)

        # Proxy port
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(1080)
        self.proxy_port.setEnabled(False)
        proxy_layout.addRow("Port:", self.proxy_port)

        # Proxy auth
        self.proxy_auth = QCheckBox("Requires authentication")
        self.proxy_auth.setEnabled(False)
        self.proxy_auth.stateChanged.connect(self._on_auth_toggled)
        proxy_layout.addRow("", self.proxy_auth)

        # Proxy username/password
        self.proxy_username = QLineEdit()
        self.proxy_username.setEnabled(False)
        proxy_layout.addRow("Username:", self.proxy_username)

        self.proxy_password = QLineEdit()
        self.proxy_password.setEchoMode(QLineEdit.Password)
        self.proxy_password.setEnabled(False)
        proxy_layout.addRow("Password:", self.proxy_password)

        proxy_group.setLayout(proxy_layout)
        layout.addWidget(proxy_group)

        layout.addStretch()
        self.setLayout(layout)

    def _on_proxy_toggled(self, state):
        """Enable/disable proxy fields based on checkbox."""
        enabled = self.proxy_enabled.isChecked()
        self.proxy_type.setEnabled(enabled)
        self.proxy_host.setEnabled(enabled)
        self.proxy_port.setEnabled(enabled)
        self.proxy_auth.setEnabled(enabled)
        if enabled:
            self._on_auth_toggled(self.proxy_auth.checkState())

    def _on_auth_toggled(self, state):
        """Enable/disable auth fields based on checkbox."""
        enabled = self.proxy_auth.isChecked() and self.proxy_enabled.isChecked()
        self.proxy_username.setEnabled(enabled)
        self.proxy_password.setEnabled(enabled)

    def validatePage(self):
        """Store proxy settings when moving to next page."""
        if self.proxy_enabled.isChecked():
            self.wizard.proxy_settings = {
                'proxy_type': ProxyType.to_db_value(self.proxy_type.currentText()),
                'proxy_host': self.proxy_host.text().strip(),
                'proxy_port': self.proxy_port.value()
            }

            if self.proxy_auth.isChecked():
                self.wizard.proxy_settings['proxy_username'] = self.proxy_username.text().strip()
                self.wizard.proxy_settings['proxy_password'] = self.proxy_password.text()

            logger.info(f"Proxy configured: {self.wizard.proxy_settings['proxy_type']} "
                       f"{self.wizard.proxy_settings['proxy_host']}:{self.wizard.proxy_settings['proxy_port']}")
        else:
            self.wizard.proxy_settings = None
            logger.info("No proxy configured")

        return True


class CredentialsPage(QWizardPage):
    """Page 3: Fill registration form with dynamic fields from server, including CAPTCHA support."""

    def __init__(self, wizard):
        """Initialize credentials page."""
        super().__init__()

        self.wizard = wizard
        self.form_fields = {}  # Will store QLineEdit widgets

        self.setTitle("Registration Form")
        self.setSubTitle("Fill in the registration details.")

        # Main layout (will be populated dynamically)
        self.main_layout = QVBoxLayout()

        # Progress indicator (shown while querying server)
        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout()
        self.progress_label = QLabel("Connecting to server...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        self.progress_widget.setLayout(progress_layout)

        # Form widget (shown after query completes)
        self.form_widget = QWidget()
        self.form_layout = QFormLayout()
        self.form_widget.setLayout(self.form_layout)
        self.form_widget.setVisible(False)

        self.main_layout.addWidget(self.progress_widget)
        self.main_layout.addWidget(self.form_widget)

        self.setLayout(self.main_layout)

    def initializePage(self):
        """Query registration form from server when page is shown."""
        logger.info(f"Creating registration session for {self.wizard.server_domain}...")

        # Reset state
        self.progress_widget.setVisible(True)
        self.form_widget.setVisible(False)
        self.form_fields.clear()
        self.progress_label.setText("Connecting to server...")

        # Clear existing form
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Start async query using create_task (no threads!)
        asyncio.create_task(self._query_form())

    async def _query_form(self):
        """Async task to create session and query form."""
        try:
            # Step 1: Create session
            session_result = await create_registration_session(
                self.wizard.server_domain,
                self.wizard.proxy_settings
            )

            if not session_result['success']:
                self._on_query_error(f"Failed to connect: {session_result['error']}")
                return

            self.wizard.session_id = session_result['session_id']
            logger.info(f"Session created: {self.wizard.session_id[:8]}...")

            # Step 2: Query form
            self.progress_label.setText("Querying registration form...")
            result = await query_registration_form(self.wizard.session_id)

            if result['success']:
                self._on_form_received(result)
            else:
                self._on_query_error(result['error'])

        except Exception as e:
            logger.error(f"Form query error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_query_error(str(e))

    def _on_form_received(self, result: Dict[str, Any]):
        """Handle successful form query."""
        logger.info(f"Registration form received with {len(result.get('fields', {}))} fields")

        self.wizard.registration_form = result['fields']
        self.wizard.captcha_data = result.get('captcha_data')

        # Debug: Log captcha_data details
        if self.wizard.captcha_data:
            logger.info(f"CAPTCHA data present: {list(self.wizard.captcha_data.keys())}")
            logger.info(f"CAPTCHA has image_data: {bool(self.wizard.captcha_data.get('image_data'))}")
            if self.wizard.captcha_data.get('image_data'):
                logger.info(f"CAPTCHA image_data length: {len(self.wizard.captcha_data['image_data'])} bytes")
        else:
            logger.info("No CAPTCHA data in result")

        # Hide progress, show form
        self.progress_widget.setVisible(False)
        self.form_widget.setVisible(True)

        form_data = result['fields']

        # Show instructions if present (display as informational text)
        if result.get('instructions'):
            instruction_text = f"Information: {result['instructions']}"
            label = QLabel(instruction_text)
            label.setWordWrap(True)
            label.setStyleSheet("font-style: italic; color: #555; margin-bottom: 10px;")
            self.form_layout.addRow(label)

        # Fields to hide from user (CAPTCHA internal metadata that shouldn't be edited)
        # We keep captcha-fallback-url visible as fallback option
        hidden_fields = {
            'captcha-fallback-text',  # Redundant text description
            'challenge',               # Internal CAPTCHA challenge ID (hidden field)
            'sid',                     # Internal stanza ID (hidden field)
            'from'                     # Internal 'from' attribute (hidden field)
        }

        # Build field list with preferred ordering (username, password, captcha-fallback-url, ocr, email, then others)
        preferred_order = ['username', 'password', 'captcha-fallback-url', 'ocr', 'email']
        ordered_fields = []

        # Add preferred fields first (excluding hidden ones)
        for field_name in preferred_order:
            if field_name in form_data and field_name not in hidden_fields:
                ordered_fields.append(field_name)

        # Add remaining fields (excluding hidden ones)
        for field_name in form_data.keys():
            if field_name not in ordered_fields and field_name not in hidden_fields:
                ordered_fields.append(field_name)

        # Track if we've displayed the CAPTCHA image
        captcha_image_displayed = False

        # Build dynamic form with ordered fields
        for field_name in ordered_fields:
            field_info = form_data[field_name]

            # Create input field
            input_field = QLineEdit()

            # Set password mode for password fields
            if 'password' in field_name.lower():
                input_field.setEchoMode(QLineEdit.Password)

            # Make captcha-fallback-url read-only but selectable (for copying)
            if field_name == 'captcha-fallback-url':
                input_field.setReadOnly(True)
                # Use disabled visual style but keep it selectable
                # This respects the theme's disabled color palette
                input_field.setStyleSheet("QLineEdit:read-only { color: palette(disabled-text); background-color: palette(disabled-base); }")
                # Pre-fill with URL if available, otherwise show placeholder
                if 'value' in field_info and field_info['value']:
                    input_field.setText(field_info['value'])
                    # Ensure cursor at beginning to show start of URL
                    input_field.setCursorPosition(0)
                else:
                    input_field.setPlaceholderText("Not provided")

            # Add placeholder for CAPTCHA field
            if field_name == 'ocr':
                input_field.setPlaceholderText("Enter text from image above")

            # Mark required fields
            label_text = field_info.get('label', field_name)
            if field_info.get('required', False):
                label_text += " *"

            self.form_layout.addRow(label_text + ":", input_field)
            self.form_fields[field_name] = input_field

            # Connect to validation
            input_field.textChanged.connect(self.completeChanged)

            # Display CAPTCHA image after password field (or after captcha-fallback-url if present)
            # Only display once, even if we process both fields
            should_display_captcha = (
                not captcha_image_displayed and
                self.wizard.captcha_data and
                (field_name == 'captcha-fallback-url' or
                 (field_name == 'password' and 'captcha-fallback-url' not in ordered_fields))
            )

            if should_display_captcha:
                captcha = self.wizard.captcha_data
                # CAPTCHA structure: {'media': [{'type': 'image/png', 'data': bytes, 'cid': '...'}], 'challenge': '...', 'sid': '...'}
                if captcha.get('media'):
                    try:
                        # Find first image in media array
                        image_media = None
                        for media in captcha['media']:
                            if media.get('type', '').startswith('image/'):
                                image_media = media
                                break

                        if image_media:
                            # Load image bytes directly (already binary, not base64)
                            image_bytes = image_media['data']
                            pixmap = QPixmap()
                            pixmap.loadFromData(image_bytes)

                            if not pixmap.isNull():
                                # Get image dimensions
                                img_width = pixmap.width()
                                img_height = pixmap.height()
                                logger.info(f"CAPTCHA image size: {img_width}x{img_height}")

                                # Create container for image with white background
                                image_container = QLabel()
                                image_container.setPixmap(pixmap)
                                image_container.setStyleSheet(
                                    "background-color: white; "
                                    "border: 2px solid #ccc; "
                                    "padding: 10px;"
                                )
                                image_container.setAlignment(Qt.AlignCenter)

                                # Set fixed size to ensure image is fully visible
                                # Add padding (20px) + border (2px * 2) = 24px extra
                                image_container.setFixedSize(img_width + 24, img_height + 24)

                                # Add image to form (no label, just the image in the right column)
                                self.form_layout.addRow("", image_container)

                                # Calculate required wizard size to accommodate CAPTCHA image
                                # Width: form label column (~200) + image width + padding + margins (~100)
                                required_width = max(600, 200 + img_width + 100)
                                # Height: title bar (~80) + form fields (~250) + image + buttons (~100) + margins
                                required_height = max(500, 80 + 250 + img_height + 100 + 50)

                                # Resize wizard and lock the size to prevent shrinking
                                from PySide6.QtCore import QSize
                                self.wizard.resize(required_width, required_height)
                                self.wizard._locked_size = QSize(required_width, required_height)
                                self.wizard.size_locked = True

                                logger.info(f"Wizard resized and locked to {required_width}x{required_height}")

                                captcha_image_displayed = True
                                logger.info(f"CAPTCHA image displayed inline after '{field_name}' field (type: {image_media['type']})")
                            else:
                                logger.error("Failed to load CAPTCHA image: pixmap is null")
                                error_label = QLabel("Failed to load CAPTCHA image (invalid format)")
                                error_label.setStyleSheet("color: red;")
                                self.form_layout.addRow("", error_label)
                        else:
                            logger.warning("CAPTCHA has no image media")
                    except Exception as e:
                        logger.error(f"Failed to display CAPTCHA image: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        error_label = QLabel(f"Failed to load CAPTCHA image: {e}")
                        error_label.setStyleSheet("color: red;")
                        self.form_layout.addRow("", error_label)

        # Add required fields note
        note_label = QLabel("* Required fields")
        note_label.setStyleSheet("color: gray; font-size: 9pt; margin-top: 10px;")
        self.form_layout.addRow(note_label)

        self.completeChanged.emit()

    def _on_query_error(self, error_message: str):
        """Handle form query error."""
        logger.error(f"Failed to query registration form: {error_message}")

        # Hide progress, show form widget (to display error)
        self.progress_widget.setVisible(False)
        self.form_widget.setVisible(True)

        # Show error message
        error_label = QLabel(f"Failed to query registration form:\n\n{error_message}")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("color: red;")
        self.form_layout.addRow(error_label)

        # Can't proceed
        self.setFinalPage(True)

    def isComplete(self):
        """Validate that all required fields are filled."""
        if not self.wizard.registration_form:
            return False

        # Only check fields that are actually displayed (in form_fields)
        for field_name, widget in self.form_fields.items():
            field_info = self.wizard.registration_form.get(field_name, {})
            if field_info.get('required', False):
                if not widget.text().strip():
                    return False

        return True

    def validatePage(self):
        """Store form data when moving to next page."""
        self.wizard.form_data = {}

        for field_name, widget in self.form_fields.items():
            value = widget.text().strip()
            if value:
                self.wizard.form_data[field_name] = value

        # Store credentials for account creation
        self.wizard.registered_password = self.wizard.form_data.get('password', '')

        logger.info(f"Form data collected: {list(self.wizard.form_data.keys())}")
        return True


class RegistrationPage(QWizardPage):
    """Page 4: Submit registration and create account in database."""

    def __init__(self, wizard):
        """Initialize registration page."""
        super().__init__()

        self.wizard = wizard
        self.registration_successful = False

        self.setTitle("Registering Account")
        self.setSubTitle("Please wait while your account is being registered...")

        layout = QVBoxLayout()

        # Status label
        self.status_label = QLabel("Submitting registration to server...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        layout.addWidget(self.progress_bar)

        # Details text (for errors or success messages)
        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setVisible(False)
        self.details_text.setMaximumHeight(150)
        layout.addWidget(self.details_text)

        layout.addStretch()
        self.setLayout(layout)

        # Make this the final page
        self.setFinalPage(True)

    def initializePage(self):
        """Submit registration when page is shown."""
        logger.info("Starting registration submission...")

        # Reset state
        self.registration_successful = False
        self.status_label.setText("Submitting registration to server...")
        self.progress_bar.setRange(0, 0)
        self.details_text.setVisible(False)
        self.details_text.clear()

        # Start async registration using create_task (no threads!)
        asyncio.create_task(self._submit_registration())

    async def _submit_registration(self):
        """Async task to submit registration."""
        try:
            result = await submit_registration(
                self.wizard.session_id,
                self.wizard.form_data
            )

            if result['success']:
                self._on_registration_complete(result['jid'])
            else:
                self._on_registration_error(result['error'])

        except Exception as e:
            logger.error(f"Registration error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_registration_error(str(e))

    def _on_registration_complete(self, jid: str):
        """Handle successful registration."""
        logger.info(f"Registration successful: {jid}")

        self.wizard.registered_jid = jid
        self.registration_successful = True

        # Update UI
        self.status_label.setText(f"✓ Registration successful!")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)

        # Create account in database
        self._create_account_in_database()

    def _on_registration_error(self, error_message: str):
        """Handle registration error."""
        logger.error(f"Registration failed: {error_message}")

        # Update UI
        self.status_label.setText("✗ Registration failed")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

        self.details_text.setPlainText(f"Error: {error_message}")
        self.details_text.setVisible(True)
        self.details_text.setStyleSheet("color: red;")

        # Allow user to go back and try again
        self.wizard.button(QWizard.BackButton).setEnabled(True)

    def _create_account_in_database(self):
        """Create account in database using AccountBrewery."""
        try:
            self.status_label.setText("Creating account in database...")

            account_brewery = get_account_brewery()

            # Pass proxy settings to account if configured
            account_settings = {}
            if self.wizard.proxy_settings:
                account_settings['proxy_type'] = self.wizard.proxy_settings.get('proxy_type')
                account_settings['proxy_host'] = self.wizard.proxy_settings.get('proxy_host')
                account_settings['proxy_port'] = self.wizard.proxy_settings.get('proxy_port')
                if 'proxy_username' in self.wizard.proxy_settings:
                    account_settings['proxy_username'] = self.wizard.proxy_settings.get('proxy_username')
                if 'proxy_password' in self.wizard.proxy_settings:
                    account_settings['proxy_password'] = self.wizard.proxy_settings.get('proxy_password')

            account_id = account_brewery.create_account(
                jid=self.wizard.registered_jid,
                password=self.wizard.registered_password,
                **account_settings
            )

            logger.info(f"Account {account_id} created in database: {self.wizard.registered_jid}")

            # Update UI
            self.status_label.setText(f"✓ Account created successfully!\n\nJID: {self.wizard.registered_jid}")
            self.details_text.setPlainText(
                f"Your account has been registered and saved.\n\n"
                f"JID: {self.wizard.registered_jid}\n"
                f"Account ID: {account_id}\n\n"
                f"Click Finish to close this wizard."
            )
            self.details_text.setVisible(True)
            self.details_text.setStyleSheet("color: green;")

            # Emit signal for main window to refresh account list
            self.wizard.account_registered.emit(account_id)

            # Notify wizard that page is complete (enables Finish button)
            self.completeChanged.emit()

        except Exception as e:
            logger.error(f"Failed to create account in database: {e}")
            import traceback
            logger.error(traceback.format_exc())

            self.status_label.setText("✗ Failed to save account")
            self.details_text.setPlainText(
                f"Registration was successful, but failed to save account to database:\n\n{e}\n\n"
                f"You may need to add the account manually."
            )
            self.details_text.setVisible(True)
            self.details_text.setStyleSheet("color: red;")

    def isComplete(self):
        """Only allow finish if registration was successful."""
        return self.registration_successful
