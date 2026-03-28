"""
Settings dialog for Siproxylin.
"""

import json
import asyncio
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget,
    QFormLayout, QComboBox, QDialogButtonBox, QLabel, QCheckBox,
    QLineEdit, QPushButton, QHBoxLayout
)
from PySide6.QtCore import Qt

from ..utils.logger import setup_main_logger
from ..utils.paths import get_paths
from ..db.database import get_db

logger = setup_main_logger()


class SettingsDialog(QDialog):
    """Settings dialog with multiple tabs."""

    def __init__(self, parent=None, call_bridge=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(575, 400)  # 15% wider (500 * 1.15 = 575)

        self.paths = get_paths()
        self.settings_path = self.paths.config_dir / 'calls.json'
        self.logging_settings_path = self.paths.config_dir / 'logging.json'
        self.gstreamer_settings_path = self.paths.config_dir / 'gstreamer.json'
        self.call_bridge = call_bridge
        self.db = get_db()
        self.parent_window = parent  # Keep reference to main window

        # Load existing settings
        self.settings = self._load_settings()
        self.logging_settings = self._load_logging_settings()
        self.gstreamer_settings = self._load_gstreamer_settings()

        # Setup UI
        self._setup_ui()

        # Populate device lists from call service
        # If call_bridge wasn't passed, try to find one from available accounts
        if not self.call_bridge and hasattr(parent, 'account_manager'):
            for account in parent.account_manager.accounts.values():
                if hasattr(account, 'call_bridge') and account.call_bridge:
                    self.call_bridge = account.call_bridge
                    logger.debug(f"Found call_bridge from account {account.account_id}")
                    break

        if self.call_bridge:
            # Start device population - _load_current_settings() will be called when it completes
            asyncio.create_task(self._populate_devices())
        else:
            logger.warning("No call_bridge available - device list will show Default only")
            # Update info label to explain why devices aren't listed
            self.devices_info_label.setText(
                "⚠ Device enumeration unavailable. Connect an account to see available devices.\n"
                "You can still save settings - they will apply when you make calls."
            )
            self.devices_info_label.setStyleSheet("color: #cc6600; font-size: 10pt;")
            # Load current settings even without device enumeration (will show saved values in other tabs)
            self._load_current_settings()

    def _setup_ui(self):
        """Setup the UI layout."""
        layout = QVBoxLayout(self)

        # Tab widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Calls tab
        self.calls_tab = QWidget()
        self._setup_calls_tab()
        self.tabs.addTab(self.calls_tab, "Calls")

        # Video tab (placeholder)
        self.video_tab = QWidget()
        self._setup_video_tab()
        self.tabs.addTab(self.video_tab, "Video")
        self.tabs.setTabEnabled(self.tabs.indexOf(self.video_tab), False)

        # Notifications tab
        self.notifications_tab = QWidget()
        self._setup_notifications_tab()
        self.tabs.addTab(self.notifications_tab, "Notifications")

        # Logging tab
        self.logging_tab = QWidget()
        self._setup_logging_tab()
        self.tabs.addTab(self.logging_tab, "Logging")

        # GStreamer tab
        self.gstreamer_tab = QWidget()
        self._setup_gstreamer_tab()
        self.tabs.addTab(self.gstreamer_tab, "GStreamer")

        # Advanced tab
        self.advanced_tab = QWidget()
        self._setup_advanced_tab()
        self.tabs.addTab(self.advanced_tab, "Advanced")

        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _setup_calls_tab(self):
        """Setup the Calls tab with audio device pickers."""
        layout = QFormLayout(self.calls_tab)

        # Audio Devices section
        devices_label = QLabel("Audio Devices")
        devices_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(devices_label)

        # Microphone picker
        self.microphone_combo = QComboBox()
        self.microphone_combo.addItem("Default (System)", "")
        layout.addRow("Microphone:", self.microphone_combo)

        # Speakers picker
        self.speakers_combo = QComboBox()
        self.speakers_combo.addItem("Default (System)", "")
        layout.addRow("Speakers:", self.speakers_combo)

        # Info label (will be updated based on device availability)
        self.devices_info_label = QLabel("Devices are enumerated from GStreamer/PulseAudio.")
        self.devices_info_label.setWordWrap(True)
        self.devices_info_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addRow("", self.devices_info_label)

        layout.addRow("", QLabel(""))  # Spacer

        # Audio Processing section
        processing_label = QLabel("Audio Processing")
        processing_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(processing_label)

        # Echo Cancellation checkbox
        self.echo_cancel_checkbox = QCheckBox("Echo Cancellation")
        self.echo_cancel_checkbox.setChecked(True)
        self.echo_cancel_checkbox.toggled.connect(self._on_echo_cancel_toggled)
        layout.addRow("", self.echo_cancel_checkbox)

        # Echo suppression level
        self.echo_level_combo = QComboBox()
        self.echo_level_combo.addItem("Low", 0)
        self.echo_level_combo.addItem("Moderate", 1)
        self.echo_level_combo.addItem("High", 2)
        self.echo_level_combo.setCurrentIndex(1)  # Default: Moderate
        layout.addRow("  Suppression Level:", self.echo_level_combo)

        # Noise Suppression checkbox
        self.noise_suppression_checkbox = QCheckBox("Noise Suppression")
        self.noise_suppression_checkbox.setChecked(True)
        self.noise_suppression_checkbox.toggled.connect(self._on_noise_suppression_toggled)
        layout.addRow("", self.noise_suppression_checkbox)

        # Noise suppression level
        self.noise_level_combo = QComboBox()
        self.noise_level_combo.addItem("Low", 0)
        self.noise_level_combo.addItem("Moderate", 1)
        self.noise_level_combo.addItem("High", 2)
        self.noise_level_combo.addItem("Very High", 3)
        self.noise_level_combo.setCurrentIndex(1)  # Default: Moderate
        layout.addRow("  Suppression Level:", self.noise_level_combo)

        # Automatic Gain Control checkbox
        self.gain_control_checkbox = QCheckBox("Automatic Gain Control")
        self.gain_control_checkbox.setChecked(True)
        layout.addRow("", self.gain_control_checkbox)

        # Processing info label
        processing_info = QLabel("Settings apply when starting a new call. Restart active calls to apply changes.")
        processing_info.setWordWrap(True)
        processing_info.setStyleSheet("color: gray; font-size: 9pt; font-style: italic;")
        layout.addRow("", processing_info)

        logger.debug("Calls tab setup complete")

    def _setup_video_tab(self):
        """Setup the Video tab (placeholder for future camera/video settings)."""
        layout = QFormLayout(self.video_tab)

        # Placeholder info
        placeholder_label = QLabel("Video settings will be available in a future release.")
        placeholder_label.setStyleSheet("color: gray; font-size: 11pt; font-style: italic;")
        layout.addRow(placeholder_label)

        info_label = QLabel(
            "This tab will contain:\n"
            "• Camera device selection\n"
            "• Video resolution settings\n"
            "• Video encoding options"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addRow("", info_label)

        logger.debug("Video tab setup complete (placeholder)")

    def _setup_logging_tab(self):
        """Setup the Logging tab with global logging controls."""
        layout = QFormLayout(self.logging_tab)

        # Main Application Log section
        main_log_label = QLabel("Main Application Log")
        main_log_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(main_log_label)

        self.main_log_enabled_checkbox = QCheckBox("Write main log")
        self.main_log_enabled_checkbox.setChecked(True)
        layout.addRow("", self.main_log_enabled_checkbox)

        self.main_log_level_combo = QComboBox()
        self.main_log_level_combo.addItems(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
        self.main_log_level_combo.setCurrentText('WARNING')
        layout.addRow("Level:", self.main_log_level_combo)

        main_log_path_label = QLabel(str(self.paths.main_log_path()))
        main_log_path_label.setStyleSheet("color: gray; font-size: 10pt;")
        main_log_path_label.setWordWrap(True)
        layout.addRow("Path:", main_log_path_label)

        layout.addRow("", QLabel(""))  # Spacer

        # XMPP Protocol Log section
        xml_log_label = QLabel("XMPP Protocol Log")
        xml_log_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(xml_log_label)

        self.xml_log_enabled_checkbox = QCheckBox("Write XMPP protocol log")
        self.xml_log_enabled_checkbox.setChecked(False)
        layout.addRow("", self.xml_log_enabled_checkbox)

        xml_log_path_label = QLabel(str(self.paths.log_dir / 'xmpp-protocol.log'))
        xml_log_path_label.setStyleSheet("color: gray; font-size: 10pt;")
        xml_log_path_label.setWordWrap(True)
        layout.addRow("Path:", xml_log_path_label)

        xml_note_label = QLabel("XML logs are always DEBUG level and shared by all accounts.")
        xml_note_label.setStyleSheet("color: gray; font-size: 9pt; font-style: italic;")
        xml_note_label.setWordWrap(True)
        layout.addRow("", xml_note_label)

        layout.addRow("", QLabel(""))  # Spacer

        # Call Service Log section
        call_service_log_label = QLabel("Call Service Log")
        call_service_log_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(call_service_log_label)

        self.call_service_log_level_combo = QComboBox()
        self.call_service_log_level_combo.addItems(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
        self.call_service_log_level_combo.setCurrentText('WARNING')
        layout.addRow("Level:", self.call_service_log_level_combo)

        call_service_paths_label = QLabel(
            f"Call service logs:\n"
            f"- {self.paths.call_service_stdout_log_path()}\n"
            f"- {self.paths.call_service_log_path()}\n"
            f"- {self.paths.call_service_stderr_log_path()} (see GStreamer tab)"
        )
        call_service_paths_label.setStyleSheet("color: gray; font-size: 10pt;")
        call_service_paths_label.setWordWrap(True)
        layout.addRow("Paths:", call_service_paths_label)

        layout.addRow("", QLabel(""))  # Spacer

        # Restart notice
        restart_label = QLabel("⚠ Changes take effect after restart")
        restart_label.setStyleSheet("color: orange; font-weight: bold;")
        layout.addRow("", restart_label)

        logger.debug("Logging tab setup complete")

    def _setup_notifications_tab(self):
        """Setup the Notifications tab with privacy controls."""
        layout = QFormLayout(self.notifications_tab)

        # Enable/Disable section
        enable_label = QLabel("Enable Notifications")
        enable_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(enable_label)

        self.notification_chat_enabled_checkbox = QCheckBox("Notify on chat messages")
        self.notification_chat_enabled_checkbox.setChecked(True)
        layout.addRow("", self.notification_chat_enabled_checkbox)

        self.notification_calls_enabled_checkbox = QCheckBox("Notify on incoming calls")
        self.notification_calls_enabled_checkbox.setChecked(True)
        layout.addRow("", self.notification_calls_enabled_checkbox)

        layout.addRow("", QLabel(""))  # Spacer

        # Privacy section
        privacy_label = QLabel("Privacy")
        privacy_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(privacy_label)

        self.notification_show_sender_checkbox = QCheckBox("Show sender name in notifications")
        self.notification_show_sender_checkbox.setChecked(True)
        layout.addRow("", self.notification_show_sender_checkbox)

        self.notification_show_body_checkbox = QCheckBox("Show message content in notifications")
        self.notification_show_body_checkbox.setChecked(True)
        layout.addRow("", self.notification_show_body_checkbox)

        privacy_note = QLabel("When both disabled, notifications will show 'Siproxylin: New message' for maximum privacy.")
        privacy_note.setStyleSheet("color: gray; font-size: 9pt; font-style: italic;")
        privacy_note.setWordWrap(True)
        layout.addRow("", privacy_note)

        logger.debug("Notifications tab setup complete")

    def _setup_advanced_tab(self):
        """Setup the Advanced tab with admin tools."""
        layout = QFormLayout(self.advanced_tab)

        # Admin Tools section
        admin_tools_label = QLabel("Administrator Tools")
        admin_tools_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(admin_tools_label)

        # Admin Tools checkbox
        self.admin_tools_checkbox = QCheckBox("Enable Admin Tools")
        self.admin_tools_checkbox.setChecked(False)
        layout.addRow("", self.admin_tools_checkbox)

        # Info label
        info_label = QLabel('When enabled, adds "Disco (Service Discovery)" to context menus for debugging XMPP server/client capabilities.')
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addRow("", info_label)

        logger.debug("Advanced tab setup complete")

    def _setup_gstreamer_tab(self):
        """Setup the GStreamer tab with debug environment variable controls."""
        layout = QFormLayout(self.gstreamer_tab)

        # Header
        header_label = QLabel("GStreamer/libnice Debug Settings")
        header_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addRow(header_label)

        info_label = QLabel(
            "These settings control debug logging from the call service (GStreamer/libnice). "
            "Changes take effect for new calls. High debug levels (7-9) generate massive log output. "
            f"Output is logged to {self.paths.call_service_stderr_log_path()}"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addRow("", info_label)

        layout.addRow("", QLabel(""))  # Spacer

        # GST_DEBUG section
        self.gst_debug_input = QLineEdit()
        self.gst_debug_input.setPlaceholderText("e.g., webrtcbin:7,rtpbin:5,pulsesrc:6")
        self.gst_debug_input.setMinimumWidth(400)
        self.gst_debug_input.textChanged.connect(self._validate_gst_debug_input)
        layout.addRow("GST_DEBUG:", self.gst_debug_input)

        # Selectable categories (without levels) - no special background, use window color
        gst_categories_label = QLabel(
            "webrtcbin rtpbin dtlsdec dtlsenc srtpdec srtpenc appsrc rtpopusdepay opusdec "
            "basesrc opusenc rtpopuspay pulsesrc webrtcdsp nice"
        )
        gst_categories_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        gst_categories_label.setWordWrap(True)
        gst_categories_label.setStyleSheet("font-size: 9pt; padding: 4px;")
        layout.addRow("", gst_categories_label)

        layout.addRow("", QLabel(""))  # Spacer

        # G_MESSAGES_DEBUG section
        self.g_messages_debug_input = QLineEdit()
        self.g_messages_debug_input.setPlaceholderText("e.g., libnice,libnice-stun,libnice-socket")
        self.g_messages_debug_input.setMinimumWidth(400)
        self.g_messages_debug_input.textChanged.connect(self._validate_g_messages_debug_input)
        layout.addRow("G_MESSAGES_DEBUG:", self.g_messages_debug_input)

        # Selectable categories - no special background, use window color
        g_categories_label = QLabel("libnice libnice-stun libnice-socket libnice-pseudotcp")
        g_categories_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        g_categories_label.setWordWrap(True)
        g_categories_label.setStyleSheet("font-size: 9pt; padding: 4px;")
        layout.addRow("", g_categories_label)

        layout.addRow("", QLabel(""))  # Spacer

        # NICE_DEBUG section
        self.nice_debug_input = QLineEdit()
        self.nice_debug_input.setPlaceholderText("'all' or leave empty")
        self.nice_debug_input.setMinimumWidth(400)
        self.nice_debug_input.textChanged.connect(self._validate_nice_debug_input)
        layout.addRow("NICE_DEBUG:", self.nice_debug_input)

        # Selectable values - no special background, use window color
        nice_values_label = QLabel("all")
        nice_values_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nice_values_label.setStyleSheet("font-size: 9pt; padding: 4px;")
        layout.addRow("", nice_values_label)

        layout.addRow("", QLabel(""))  # Spacer

        # Validation status label
        self.gstreamer_validation_label = QLabel("")
        self.gstreamer_validation_label.setWordWrap(True)
        layout.addRow("", self.gstreamer_validation_label)

        logger.debug("GStreamer tab setup complete")

    def _on_echo_cancel_toggled(self, checked):
        """Enable/disable echo suppression level combo based on checkbox."""
        self.echo_level_combo.setEnabled(checked)

    def _on_noise_suppression_toggled(self, checked):
        """Enable/disable noise suppression level combo based on checkbox."""
        self.noise_level_combo.setEnabled(checked)

    async def _populate_devices(self):
        """Populate device dropdowns from call service."""
        try:
            devices = await self.call_bridge.list_audio_devices()

            # Clear existing items (except Default)
            self.microphone_combo.clear()
            self.speakers_combo.clear()

            # Re-add Default option
            self.microphone_combo.addItem("Default (System)", "")
            self.speakers_combo.addItem("Default (System)", "")

            # Add devices
            for device in devices:
                if device['device_class'] == 'Audio/Source':
                    # Microphone
                    self.microphone_combo.addItem(device['description'], device['name'])
                elif device['device_class'] == 'Audio/Sink':
                    # Speakers
                    self.speakers_combo.addItem(device['description'], device['name'])

            logger.info(f"Populated {len(devices)} audio devices")

            # Update info label to show successful enumeration
            self.devices_info_label.setText("Devices are enumerated from GStreamer/PulseAudio.")
            self.devices_info_label.setStyleSheet("color: gray; font-size: 10pt;")

            # Restore saved selections
            self._load_current_settings()

        except Exception as e:
            logger.error(f"Failed to populate devices: {e}")
            # Show error in info label
            self.devices_info_label.setText(
                f"⚠ Failed to enumerate devices: {str(e)}\n"
                "Using Default (System) only."
            )
            self.devices_info_label.setStyleSheet("color: #cc0000; font-size: 10pt;")

    def _load_settings(self):
        """Load settings from JSON file."""
        if self.settings_path.exists():
            try:
                with open(self.settings_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load settings: {e}")
                return {}
        return {}

    def _save_settings(self):
        """Save settings to JSON file."""
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_path, 'w') as f:
                json.dump(self.settings, f, indent=2)
            logger.info(f"Settings saved to {self.settings_path}")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

    def _load_logging_settings(self):
        """Load logging settings from JSON file."""
        if self.logging_settings_path.exists():
            try:
                with open(self.logging_settings_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load logging settings: {e}")
                return self._default_logging_settings()
        return self._default_logging_settings()

    def _default_logging_settings(self):
        """Return default logging settings."""
        return {
            'main_log_enabled': True,
            'main_log_level': 'WARNING',
            'xml_log_enabled': False,
            'call_service_log_level': 'WARNING'
        }

    def _load_gstreamer_settings(self):
        """Load GStreamer debug settings from JSON file."""
        if self.gstreamer_settings_path.exists():
            try:
                with open(self.gstreamer_settings_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load GStreamer settings: {e}")
                return self._default_gstreamer_settings()
        return self._default_gstreamer_settings()

    def _default_gstreamer_settings(self):
        """Return default GStreamer debug settings (empty - disabled by default)."""
        return {
            'GST_DEBUG': '',
            'G_MESSAGES_DEBUG': '',
            'NICE_DEBUG': ''
        }

    def _save_gstreamer_settings(self):
        """Save GStreamer debug settings to JSON file."""
        try:
            self.gstreamer_settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.gstreamer_settings_path, 'w') as f:
                json.dump(self.gstreamer_settings, f, indent=2)
            logger.info(f"GStreamer settings saved to {self.gstreamer_settings_path}")
        except Exception as e:
            logger.error(f"Failed to save GStreamer settings: {e}")

    def _save_logging_settings(self):
        """Save logging settings to JSON file."""
        try:
            self.logging_settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.logging_settings_path, 'w') as f:
                json.dump(self.logging_settings, f, indent=2)
            logger.info(f"Logging settings saved to {self.logging_settings_path}")
        except Exception as e:
            logger.error(f"Failed to save logging settings: {e}")

    def _validate_gst_debug_input(self, text):
        """
        Validate GST_DEBUG input field.

        Format: category:level,category:level where level is 0-9
        Spaces are automatically removed before validation.
        """
        # Strip spaces for validation
        clean_text = text.replace(' ', '')

        # Empty is valid (disabled)
        if not clean_text:
            self._set_gst_debug_valid(True, "")
            return

        # Check format: can be single digit or category:level pairs (or mix of both)
        import re

        # Validate each part separately
        parts = clean_text.split(',')
        for part in parts:
            if ':' in part:
                # Category:level format
                category, level = part.rsplit(':', 1)
                # Category must start with letter or underscore, contain alphanumeric/underscore/hyphen
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', category):
                    self._set_gst_debug_valid(False, f"Invalid category name in '{part}'")
                    return
                if not level.isdigit() or int(level) > 9:
                    self._set_gst_debug_valid(False, f"Invalid level in '{part}' (must be 0-9)")
                    return
            else:
                # Global level (single digit)
                if not part.isdigit() or int(part) > 9:
                    self._set_gst_debug_valid(False, f"Invalid global level '{part}' (must be 0-9)")
                    return

        self._set_gst_debug_valid(True, "")

    def _validate_g_messages_debug_input(self, text):
        """
        Validate G_MESSAGES_DEBUG input field.

        Format: category,category or 'all'
        Spaces are automatically removed before validation.
        """
        # Strip spaces for validation
        clean_text = text.replace(' ', '')

        # Empty is valid (disabled)
        if not clean_text:
            self._set_g_messages_debug_valid(True, "")
            return

        # Check for 'all' keyword
        if clean_text == 'all':
            self._set_g_messages_debug_valid(True, "")
            return

        # Check format: comma-separated category names
        import re
        # Category names: alphanumeric, underscore, hyphen (must start with letter)
        pattern = r'^[a-zA-Z][a-zA-Z0-9_-]*(,[a-zA-Z][a-zA-Z0-9_-]*)*$'

        if re.match(pattern, clean_text):
            self._set_g_messages_debug_valid(True, "")
        else:
            self._set_g_messages_debug_valid(False, "Invalid format. Use: category,category or 'all'")

    def _validate_nice_debug_input(self, text):
        """
        Validate NICE_DEBUG input field.

        Format: 'all' or empty
        """
        # Strip spaces for validation
        clean_text = text.strip()

        # Only 'all' or empty are valid
        if clean_text in ['all', '']:
            self._set_nice_debug_valid(True, "")
        else:
            self._set_nice_debug_valid(False, "Invalid value. Use 'all' or leave empty")

    def _set_gst_debug_valid(self, valid, message):
        """Set validation state for GST_DEBUG input."""
        if valid:
            self.gst_debug_input.setStyleSheet("")
        else:
            self.gst_debug_input.setStyleSheet("border: 2px solid red;")

        # Update validation label
        self._update_validation_label()

    def _set_g_messages_debug_valid(self, valid, message):
        """Set validation state for G_MESSAGES_DEBUG input."""
        if valid:
            self.g_messages_debug_input.setStyleSheet("")
        else:
            self.g_messages_debug_input.setStyleSheet("border: 2px solid red;")

        # Update validation label
        self._update_validation_label()

    def _set_nice_debug_valid(self, valid, message):
        """Set validation state for NICE_DEBUG input."""
        if valid:
            self.nice_debug_input.setStyleSheet("")
        else:
            self.nice_debug_input.setStyleSheet("border: 2px solid red;")

        # Update validation label
        self._update_validation_label()

    def _update_validation_label(self):
        """Update the GStreamer validation label with any errors."""
        errors = []

        # Check each input field
        if self.gst_debug_input.styleSheet():
            errors.append("GST_DEBUG has invalid format")
        if self.g_messages_debug_input.styleSheet():
            errors.append("G_MESSAGES_DEBUG has invalid format")
        if self.nice_debug_input.styleSheet():
            errors.append("NICE_DEBUG has invalid format")

        if errors:
            self.gstreamer_validation_label.setText("⚠ " + ", ".join(errors))
            self.gstreamer_validation_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.gstreamer_validation_label.setText("")
            self.gstreamer_validation_label.setStyleSheet("")

    def _load_current_settings(self):
        """Load current settings into UI."""
        # Load microphone device (handle both dict and string formats for backward compatibility)
        mic_device = self.settings.get('microphone_device', '')
        if isinstance(mic_device, dict):
            mic_device_id = mic_device.get('device_id', '')
        else:
            # Old format (string) - could be device_id or display_name
            mic_device_id = mic_device

        mic_index = self.microphone_combo.findData(mic_device_id)
        if mic_index >= 0:
            self.microphone_combo.setCurrentIndex(mic_index)
        else:
            # Device not found (USB unplugged?) - fallback to default
            logger.warning(f"Saved microphone device not found: {mic_device_id}, using default")
            self.microphone_combo.setCurrentIndex(0)  # Default (System)

        # Load speakers device (handle both dict and string formats for backward compatibility)
        speakers_device = self.settings.get('speakers_device', '')
        if isinstance(speakers_device, dict):
            speakers_device_id = speakers_device.get('device_id', '')
        else:
            # Old format (string) - could be device_id or display_name
            speakers_device_id = speakers_device

        speakers_index = self.speakers_combo.findData(speakers_device_id)
        if speakers_index >= 0:
            self.speakers_combo.setCurrentIndex(speakers_index)
        else:
            # Device not found (USB unplugged?) - fallback to default
            logger.warning(f"Saved speakers device not found: {speakers_device_id}, using default")
            self.speakers_combo.setCurrentIndex(0)  # Default (System)

        # Load audio processing settings
        audio_processing = self.settings.get('audio_processing', {})

        # Echo cancellation
        echo_cancel = audio_processing.get('echo_cancel', True)
        self.echo_cancel_checkbox.setChecked(echo_cancel)
        echo_level = audio_processing.get('echo_suppression_level', 1)
        echo_index = self.echo_level_combo.findData(echo_level)
        if echo_index >= 0:
            self.echo_level_combo.setCurrentIndex(echo_index)
        self.echo_level_combo.setEnabled(echo_cancel)

        # Noise suppression
        noise_suppression = audio_processing.get('noise_suppression', True)
        self.noise_suppression_checkbox.setChecked(noise_suppression)
        noise_level = audio_processing.get('noise_suppression_level', 1)
        noise_index = self.noise_level_combo.findData(noise_level)
        if noise_index >= 0:
            self.noise_level_combo.setCurrentIndex(noise_index)
        self.noise_level_combo.setEnabled(noise_suppression)

        # Gain control
        gain_control = audio_processing.get('gain_control', True)
        self.gain_control_checkbox.setChecked(gain_control)

        # Load logging settings
        self.main_log_enabled_checkbox.setChecked(
            self.logging_settings.get('main_log_enabled', True)
        )
        self.main_log_level_combo.setCurrentText(
            self.logging_settings.get('main_log_level', 'WARNING')
        )
        self.xml_log_enabled_checkbox.setChecked(
            self.logging_settings.get('xml_log_enabled', False)
        )
        self.call_service_log_level_combo.setCurrentText(
            self.logging_settings.get('call_service_log_level', 'WARNING')
        )

        # Load notification settings from database
        notification_chat_enabled = self.db.get_setting('notification_chat_enabled', default='true')
        self.notification_chat_enabled_checkbox.setChecked(
            notification_chat_enabled.lower() in ('true', '1', 'yes')
        )

        notification_calls_enabled = self.db.get_setting('notification_calls_enabled', default='true')
        self.notification_calls_enabled_checkbox.setChecked(
            notification_calls_enabled.lower() in ('true', '1', 'yes')
        )

        notification_show_sender = self.db.get_setting('notification_show_sender', default='true')
        self.notification_show_sender_checkbox.setChecked(
            notification_show_sender.lower() in ('true', '1', 'yes')
        )

        notification_show_body = self.db.get_setting('notification_show_body', default='true')
        self.notification_show_body_checkbox.setChecked(
            notification_show_body.lower() in ('true', '1', 'yes')
        )

        # Load advanced settings from database
        admin_tools_enabled = self.db.get_setting('admin_tools_enabled', default='false')
        self.admin_tools_checkbox.setChecked(
            admin_tools_enabled.lower() in ('true', '1', 'yes')
        )

        # Load GStreamer debug settings
        self.gst_debug_input.setText(self.gstreamer_settings.get('GST_DEBUG', ''))
        self.g_messages_debug_input.setText(self.gstreamer_settings.get('G_MESSAGES_DEBUG', ''))
        self.nice_debug_input.setText(self.gstreamer_settings.get('NICE_DEBUG', ''))

    def _on_save(self):
        """Save settings and close dialog."""
        # Get selected devices (store both ID and display name for robustness)
        mic_device_id = self.microphone_combo.currentData()
        mic_display_name = self.microphone_combo.currentText()
        speakers_device_id = self.speakers_combo.currentData()
        speakers_display_name = self.speakers_combo.currentText()

        # Store as dict with both fields (for handling USB device disconnect/reconnect)
        self.settings['microphone_device'] = {
            'device_id': mic_device_id if mic_device_id else '',
            'display_name': mic_display_name
        }
        self.settings['speakers_device'] = {
            'device_id': speakers_device_id if speakers_device_id else '',
            'display_name': speakers_display_name
        }

        # Get audio processing settings
        self.settings['audio_processing'] = {
            'echo_cancel': self.echo_cancel_checkbox.isChecked(),
            'echo_suppression_level': self.echo_level_combo.currentData(),
            'noise_suppression': self.noise_suppression_checkbox.isChecked(),
            'noise_suppression_level': self.noise_level_combo.currentData(),
            'gain_control': self.gain_control_checkbox.isChecked()
        }

        # Save to file
        self._save_settings()

        # Get logging settings
        self.logging_settings['main_log_enabled'] = self.main_log_enabled_checkbox.isChecked()
        self.logging_settings['main_log_level'] = self.main_log_level_combo.currentText()
        self.logging_settings['xml_log_enabled'] = self.xml_log_enabled_checkbox.isChecked()
        self.logging_settings['call_service_log_level'] = self.call_service_log_level_combo.currentText()

        # Save logging settings
        self._save_logging_settings()

        # Save notification settings to database
        self.db.set_setting('notification_chat_enabled',
                           'true' if self.notification_chat_enabled_checkbox.isChecked() else 'false')
        self.db.set_setting('notification_calls_enabled',
                           'true' if self.notification_calls_enabled_checkbox.isChecked() else 'false')
        self.db.set_setting('notification_show_sender',
                           'true' if self.notification_show_sender_checkbox.isChecked() else 'false')
        self.db.set_setting('notification_show_body',
                           'true' if self.notification_show_body_checkbox.isChecked() else 'false')

        # Save advanced settings to database
        self.db.set_setting('admin_tools_enabled',
                           'true' if self.admin_tools_checkbox.isChecked() else 'false')

        # Get GStreamer debug settings (strip spaces from input)
        self.gstreamer_settings['GST_DEBUG'] = self.gst_debug_input.text().replace(' ', '')
        self.gstreamer_settings['G_MESSAGES_DEBUG'] = self.g_messages_debug_input.text().replace(' ', '')
        self.gstreamer_settings['NICE_DEBUG'] = self.nice_debug_input.text().strip()

        # Validate before saving
        if self.gst_debug_input.styleSheet() or self.g_messages_debug_input.styleSheet() or self.nice_debug_input.styleSheet():
            # Validation error - don't save and don't close
            logger.warning("Cannot save settings: validation errors in GStreamer tab")
            # Switch to GStreamer tab to show errors
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "GStreamer":
                    self.tabs.setCurrentIndex(i)
                    break
            return

        # Save GStreamer settings
        self._save_gstreamer_settings()

        self.accept()
