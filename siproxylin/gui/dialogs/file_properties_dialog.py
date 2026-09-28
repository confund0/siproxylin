"""
File properties dialog.

Shows file metadata and properties.
"""

from html import escape
from pathlib import Path
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox


def _format_file_size(size_bytes):
    """Format file size in human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def show_file_properties_dialog(parent, file_path, file_name, mime_type, file_size):
    """
    Show file properties dialog.

    Args:
        parent: Parent widget
        file_path: Internal path to file
        file_name: Original filename
        mime_type: MIME type of file
        file_size: Size in bytes
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("File Properties")
    dialog.setMinimumWidth(400)

    layout = QVBoxLayout(dialog)

    # File not downloaded yet: there is no local file to show
    if not file_path:
        layout.addWidget(QLabel(f"<b>Original Name:</b> {escape(file_name or '')}"))
        layout.addWidget(QLabel("<b>Status:</b> Not downloaded"))
        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)
        dialog.exec_()
        return

    # Original filename (from sender/server)
    layout.addWidget(QLabel(f"<b>Original Name:</b> {escape(file_name or '')}"))

    # Internal timestamped filename
    internal_filename = Path(file_path).name
    layout.addWidget(QLabel(f"<b>Saved As:</b> {escape(internal_filename)}"))

    # File type
    layout.addWidget(QLabel(f"<b>Type:</b> {escape(mime_type or 'Unknown')}"))

    # File size
    size_text = _format_file_size(file_size) if file_size else "Unknown"
    layout.addWidget(QLabel(f"<b>Size:</b> {size_text}"))

    # Internal path
    layout.addWidget(QLabel(f"<b>Internal Path:</b><br>{escape(str(file_path))}"))

    # File exists check
    if Path(file_path).exists():
        layout.addWidget(QLabel(f"<b>Status:</b> <span style='color: green;'>File exists</span>"))
    else:
        layout.addWidget(QLabel(f"<b>Status:</b> <span style='color: red;'>File not found</span>"))

    # OK button
    button_box = QDialogButtonBox(QDialogButtonBox.Ok)
    button_box.accepted.connect(dialog.accept)
    layout.addWidget(button_box)

    dialog.exec_()
