"""
Settings dialog for Siproxylin.
"""

import json
import asyncio
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget,
    QFormLayout, QComboBox, QDialogButtonBox, QLabel, QCheckBox
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
        self.setMinimumSize(500, 400)

        self.paths = get_paths()
        self.settings_path = self.paths.config_dir / 'calls.json'
        self.logging_settings_path = self.paths.config_dir / 'logging.json'
        self.call_bridge = call_bridge
        self.db = get_db()

        # Load existing settings
        self.settings = self._load_settings()
        self.logging_settings = self._load_logging_settings()

        # Setup UI
        self._setup_ui()

        # Load current values
        self._load_current_settings()

        # Populate device lists from Go service
        if self.call_bridge:
            asyncio.create_task(self._populate_devices())

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

        # Logging tab
        self.logging_tab = QWidget()
        self._setup_logging_tab()
        self.tabs.addTab(self.logging_tab, "Logging")

        # Notifications tab
        self.notifications_tab = QWidget()
        self._setup_notifications_tab()
        self.tabs.addTab(self.notifications_tab, "Notifications")

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

        # Info label
        info_label = QLabel("Devices are enumerated from GStreamer/PulseAudio.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 10pt;")
        layout.addRow("", info_label)

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
        self.main_log_level_combo.setCurrentText('INFO')
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
        self.xml_log_enabled_checkbox.setChecked(True)
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

    def _on_echo_cancel_toggled(self, checked):
        """Enable/disable echo suppression level combo based on checkbox."""
        self.echo_level_combo.setEnabled(checked)

    def _on_noise_suppression_toggled(self, checked):
        """Enable/disable noise suppression level combo based on checkbox."""
        self.noise_level_combo.setEnabled(checked)

    async def _populate_devices(self):
        """Populate device dropdowns from Go service."""
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

            # Restore saved selections
            self._load_current_settings()

        except Exception as e:
            logger.error(f"Failed to populate devices: {e}")

    def _load_settings(self):
        """Load settings from JSON file."""
        if self.settings_path.exists():
            try:
                with open(self.settings_path, 'r') as f:
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
                with open(self.logging_settings_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load logging settings: {e}")
                return self._default_logging_settings()
        return self._default_logging_settings()

    def _default_logging_settings(self):
        """Return default logging settings."""
        return {
            'main_log_enabled': True,
            'main_log_level': 'INFO',
            'xml_log_enabled': True
        }

    def _save_logging_settings(self):
        """Save logging settings to JSON file."""
        try:
            self.logging_settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.logging_settings_path, 'w') as f:
                json.dump(self.logging_settings, f, indent=2)
            logger.info(f"Logging settings saved to {self.logging_settings_path}")
        except Exception as e:
            logger.error(f"Failed to save logging settings: {e}")

    def _load_current_settings(self):
        """Load current settings into UI."""
        # Load microphone device
        mic_device = self.settings.get('microphone_device', '')
        mic_index = self.microphone_combo.findData(mic_device)
        if mic_index >= 0:
            self.microphone_combo.setCurrentIndex(mic_index)

        # Load speakers device
        speakers_device = self.settings.get('speakers_device', '')
        speakers_index = self.speakers_combo.findData(speakers_device)
        if speakers_index >= 0:
            self.speakers_combo.setCurrentIndex(speakers_index)

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
            self.logging_settings.get('main_log_level', 'INFO')
        )
        self.xml_log_enabled_checkbox.setChecked(
            self.logging_settings.get('xml_log_enabled', True)
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

    def _on_save(self):
        """Save settings and close dialog."""
        # Get selected devices
        self.settings['microphone_device'] = self.microphone_combo.currentData()
        self.settings['speakers_device'] = self.speakers_combo.currentData()

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

        self.accept()
