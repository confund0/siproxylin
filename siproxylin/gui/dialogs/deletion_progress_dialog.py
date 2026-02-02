"""
Account deletion progress dialog for Siproxylin.

Shows real-time progress of server and local deletion operations.
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar
)
from PySide6.QtCore import Qt

logger = logging.getLogger('siproxylin.deletion_progress_dialog')


class DeletionProgressDialog(QDialog):
    """
    Dialog showing progress of account deletion operations.

    Shows two status lines:
    - Delete from server (if requested)
    - Delete locally

    Each line shows: 🔄 (in progress) → ✅ (success) / ❌ (error)
    """

    def __init__(self, jid: str, delete_from_server: bool, parent=None):
        """
        Initialize deletion progress dialog.

        Args:
            jid: Account JID being deleted
            delete_from_server: Whether server deletion was requested
            parent: Parent widget
        """
        super().__init__(parent)

        self.jid = jid
        self.delete_from_server = delete_from_server
        self.server_deletion_done = False
        self.local_deletion_done = False
        self.has_errors = False

        # Window setup
        self.setWindowTitle("Deleting Account")
        self.setMinimumWidth(500)
        self.setModal(True)

        # Prevent closing during operation
        self.can_close = False

        # Create UI
        self._create_ui()

        logger.info(f"Deletion progress dialog opened for {jid} (server={delete_from_server})")

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # Header with JID
        header_label = QLabel(f"<b>Deleting account: {self.jid}</b>")
        header_label.setStyleSheet("font-size: 11pt; padding: 5px;")
        layout.addWidget(header_label)

        layout.addSpacing(15)

        # Server deletion section
        server_label_text = "Delete from server:" if self.delete_from_server else "Delete from server: (not requested)"
        self.server_label = QLabel(server_label_text)
        self.server_label.setStyleSheet("font-size: 10pt; padding: 5px;")
        layout.addWidget(self.server_label)

        if self.delete_from_server:
            # Progress bar for server deletion (indeterminate)
            self.server_progress = QProgressBar()
            self.server_progress.setRange(0, 0)  # Indeterminate/spinner mode
            self.server_progress.setMaximumHeight(20)
            layout.addWidget(self.server_progress)

            # Status indicator
            self.server_status_label = QLabel("     ⏳ Working...")
            self.server_status_label.setStyleSheet("font-size: 9pt; color: #888;")
            layout.addWidget(self.server_status_label)
        else:
            # Grayed out indicator
            self.server_progress = None
            self.server_status_label = QLabel("     (not requested)")
            self.server_status_label.setStyleSheet("font-size: 9pt; color: #888;")
            layout.addWidget(self.server_status_label)
            self.server_deletion_done = True  # Mark as done since not requested

        layout.addSpacing(10)

        # Local deletion section
        self.local_label = QLabel("Delete locally:")
        self.local_label.setStyleSheet("font-size: 10pt; padding: 5px;")
        layout.addWidget(self.local_label)

        # Progress bar for local deletion (indeterminate)
        self.local_progress = QProgressBar()
        self.local_progress.setRange(0, 0)  # Indeterminate/spinner mode
        self.local_progress.setMaximumHeight(20)
        self.local_progress.setEnabled(False)  # Disabled until server deletion completes
        layout.addWidget(self.local_progress)

        # Status indicator
        self.local_status_label = QLabel("     ⏳ Waiting...")
        self.local_status_label.setStyleSheet("font-size: 9pt; color: #888;")
        layout.addWidget(self.local_status_label)

        layout.addSpacing(20)

        # Error details area (hidden by default)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            "color: #d9534f; font-size: 9pt; padding: 10px; "
            "background-color: #f2dede; border: 1px solid #ebccd1; border-radius: 5px;"
        )
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        layout.addSpacing(15)

        # Bottom button (initially disabled)
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        self.close_button.setEnabled(False)
        buttons_layout.addWidget(self.close_button)

        layout.addLayout(buttons_layout)

    def update_server_status(self, success: bool, error_message: str = None):
        """
        Update server deletion status.

        Args:
            success: Whether server deletion succeeded
            error_message: Error message if failed
        """
        self.server_deletion_done = True

        if self.server_progress:
            # Stop the progress bar (make it determinate)
            self.server_progress.setRange(0, 1)
            self.server_progress.setValue(1 if success else 0)

        if success:
            self.server_status_label.setText("     ✓ Success")
            self.server_status_label.setStyleSheet("font-size: 9pt; color: #5cb85c;")
            logger.info(f"Server deletion succeeded for {self.jid}")
        else:
            self.server_status_label.setText("     ⚠ Failed")
            self.server_status_label.setStyleSheet("font-size: 9pt; color: #f0ad4e;")
            self.has_errors = True

            if error_message:
                self.error_label.setText(f"<b>Server deletion error:</b><br>{error_message}")
                self.error_label.setVisible(True)

            logger.error(f"Server deletion failed for {self.jid}: {error_message}")

        # Start local deletion progress
        self.local_progress.setEnabled(True)
        self.local_status_label.setText("     ⏳ Working...")
        self.local_status_label.setStyleSheet("font-size: 9pt; color: #888;")

    def update_local_status(self, success: bool, error_message: str = None):
        """
        Update local deletion status.

        Args:
            success: Whether local deletion succeeded
            error_message: Error message if failed
        """
        self.local_deletion_done = True

        # Stop the progress bar (make it determinate)
        self.local_progress.setRange(0, 1)
        self.local_progress.setValue(1 if success else 0)

        if success:
            self.local_status_label.setText("     ✓ Success")
            self.local_status_label.setStyleSheet("font-size: 9pt; color: #5cb85c;")
            logger.info(f"Local deletion succeeded for {self.jid}")
        else:
            self.local_status_label.setText("     ⚠ Failed")
            self.local_status_label.setStyleSheet("font-size: 9pt; color: #f0ad4e;")
            self.has_errors = True

            if error_message:
                error_text = self.error_label.text()
                if error_text:
                    error_text += f"<br><br><b>Local deletion error:</b><br>{error_message}"
                else:
                    error_text = f"<b>Local deletion error:</b><br>{error_message}"
                self.error_label.setText(error_text)
                self.error_label.setVisible(True)

            logger.error(f"Local deletion failed for {self.jid}: {error_message}")

        # Enable close button and allow closing
        self.close_button.setEnabled(True)
        self.can_close = True

        # Change button text based on result
        if self.has_errors:
            self.close_button.setText("Close")
        else:
            self.close_button.setText("Finish")

    def closeEvent(self, event):
        """Prevent closing during operation."""
        if self.can_close:
            event.accept()
        else:
            event.ignore()
