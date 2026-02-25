"""
Service Discovery Information Dialog.

Displays XMPP Service Discovery (XEP-0030) information in YAML format.
"""

import yaml
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton,
    QHBoxLayout, QLabel, QCheckBox
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


class DiscoInfoDialog(QDialog):
    """Dialog to display Service Discovery information."""

    def __init__(self, parent=None, jid=None, disco_data=None, raw_xml=None):
        """
        Initialize the disco info dialog.

        Args:
            parent: Parent widget
            jid: The JID that was queried
            disco_data: Dictionary with disco#info results
            raw_xml: Raw XML from server (pretty-printed)
        """
        super().__init__(parent)

        self.jid = jid
        self.disco_data = disco_data
        self.raw_xml = raw_xml

        self.setWindowTitle(f"Service Discovery: {jid}")
        self.setMinimumSize(700, 500)

        self._setup_ui()
        self._populate_data()

    def _setup_ui(self):
        """Setup the UI layout."""
        layout = QVBoxLayout(self)

        # Header label
        header = QLabel(f"<b>Service Discovery Information</b>")
        header.setStyleSheet("font-size: 12pt; padding: 5px;")
        layout.addWidget(header)

        # JID label
        jid_label = QLabel(f"JID: <code>{self.jid}</code>")
        jid_label.setStyleSheet("color: gray; padding: 2px;")
        layout.addWidget(jid_label)

        # Text display
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.NoWrap)

        # Use monospace font for better YAML display
        font = QFont("Monospace")
        font.setStyleHint(QFont.TypeWriter)
        font.setPointSize(10)
        self.text_edit.setFont(font)

        layout.addWidget(self.text_edit)

        # Buttons and controls
        button_layout = QHBoxLayout()

        # Readable checkbox
        self.readable_checkbox = QCheckBox("Readable (YAML)")
        self.readable_checkbox.setChecked(True)
        self.readable_checkbox.toggled.connect(self._on_readable_toggled)
        button_layout.addWidget(self.readable_checkbox)

        button_layout.addStretch()

        # Copy to Clipboard button
        copy_button = QPushButton("Copy to Clipboard")
        copy_button.clicked.connect(self._copy_to_clipboard)
        button_layout.addWidget(copy_button)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

    def _populate_data(self, use_yaml=True):
        """
        Populate the text edit with disco data.

        Args:
            use_yaml: If True, format as YAML; if False, format as XML
        """
        if not self.disco_data:
            self.text_edit.setPlainText("No data available.")
            return

        if use_yaml:
            try:
                # Convert to YAML format
                yaml_text = yaml.dump(
                    self.disco_data,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True
                )
                self.text_edit.setPlainText(yaml_text)
            except Exception as e:
                # Fallback to simple list if YAML fails
                text = self._format_as_simple_list(self.disco_data)
                self.text_edit.setPlainText(text)
        else:
            # Show raw XML from server
            if self.raw_xml:
                self.text_edit.setPlainText(self.raw_xml)
            else:
                # Fallback: reconstruct XML from parsed data
                text = self._format_as_xml(self.disco_data)
                self.text_edit.setPlainText(text)

    def _on_readable_toggled(self, checked):
        """Handle Readable checkbox toggle."""
        self._populate_data(use_yaml=checked)

    def _format_as_simple_list(self, data, indent=0):
        """
        Format data as a simple list (fallback if YAML fails).

        Args:
            data: Data to format
            indent: Current indentation level

        Returns:
            Formatted string
        """
        lines = []
        prefix = "  " * indent

        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.append(self._format_as_simple_list(value, indent + 1))
                else:
                    lines.append(f"{prefix}{key}: {value}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    lines.append(self._format_as_simple_list(item, indent))
                else:
                    lines.append(f"{prefix}- {item}")
        else:
            lines.append(f"{prefix}{data}")

        return "\n".join(lines)

    def _format_as_xml(self, data):
        """
        Format data as XML-like structure.

        Args:
            data: Data to format

        Returns:
            Formatted XML string
        """
        try:
            import xml.etree.ElementTree as ET
            from xml.dom import minidom

            # Build XML from data
            root = ET.Element("disco_info")

            if isinstance(data, dict):
                for key, value in data.items():
                    if key == 'identities' and isinstance(value, list):
                        for identity in value:
                            id_elem = ET.SubElement(root, "identity")
                            if isinstance(identity, dict):
                                for k, v in identity.items():
                                    if v:  # Only add non-empty values
                                        id_elem.set(k, str(v))
                    elif key == 'features' and isinstance(value, list):
                        features_elem = ET.SubElement(root, "features")
                        for feature in value:
                            feat_elem = ET.SubElement(features_elem, "feature")
                            feat_elem.set("var", str(feature))
                    elif key == 'extended_info':
                        # Extended info might already be XML string or dict
                        if isinstance(value, str):
                            # If it's already formatted XML, use it as-is
                            if value.strip().startswith('<'):
                                return value
                            else:
                                # Plain text, wrap in element
                                ext_elem = ET.SubElement(root, "extended_info")
                                ext_elem.text = str(value)
                        elif isinstance(value, dict):
                            ext_elem = ET.SubElement(root, "extended_info")
                            for k, v in value.items():
                                field = ET.SubElement(ext_elem, "field")
                                field.set("var", str(k))
                                if isinstance(v, list):
                                    for item in v:
                                        val_elem = ET.SubElement(field, "value")
                                        val_elem.text = str(item)
                                else:
                                    val_elem = ET.SubElement(field, "value")
                                    val_elem.text = str(v)
                    else:
                        # Generic key-value
                        elem = ET.SubElement(root, key)
                        elem.text = str(value)

            # Pretty-print the XML
            xml_str = ET.tostring(root, encoding='unicode')
            dom = minidom.parseString(xml_str)
            pretty_xml = dom.toprettyxml(indent="  ")

            # Remove empty lines and XML declaration
            lines = [line for line in pretty_xml.split('\n') if line.strip()]
            if lines and lines[0].startswith('<?xml'):
                lines = lines[1:]

            return '\n'.join(lines)

        except Exception as e:
            # Fallback to simple list
            return self._format_as_simple_list(data)

    def _copy_to_clipboard(self):
        """Copy the displayed text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_edit.toPlainText())

        # Briefly change button text to show feedback
        sender = self.sender()
        if sender:
            original_text = sender.text()
            sender.setText("✓ Copied!")
            sender.setEnabled(False)

            # Reset after 1.5 seconds
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self._reset_copy_button(sender, original_text))

    def _reset_copy_button(self, button, original_text):
        """Reset the copy button text."""
        if button:
            button.setText(original_text)
            button.setEnabled(True)
