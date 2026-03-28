"""
File utility functions.
"""

import logging
import subprocess
import sys
import mimetypes
import shutil
from pathlib import Path

logger = logging.getLogger('siproxylin.utils.file_utils')


def open_file_with_external_app(file_path, mime_type=None, parent_widget=None):
    """
    Open file with OS-native application chooser.

    Args:
        file_path: Path to the file to open
        mime_type: Optional MIME type (will be detected if not provided)
        parent_widget: Optional parent widget for Linux dialog

    Returns:
        bool: True if operation succeeded, False otherwise
    """
    try:
        resolved_path = str(Path(file_path).resolve())

        if sys.platform == 'win32':
            # Windows: Show "Open With" dialog
            subprocess.Popen(['rundll32.exe', 'shell32.dll,OpenAs_RunDLL', resolved_path])
            logger.info(f"Opened 'Open With' dialog for: {resolved_path}")
            return True

        elif sys.platform == 'darwin':
            # macOS: Show in Finder with file selected, user can right-click "Open With"
            subprocess.Popen(['open', '-R', resolved_path])
            logger.info(f"Revealed file in Finder: {resolved_path}")
            return True

        else:
            # Linux: Show custom application chooser dialog
            from PySide6.QtWidgets import QDialog
            from ..gui.dialogs.image_viewer_dialog import ApplicationChooserDialog

            # Detect MIME type if not provided
            if not mime_type:
                detected_mime, _ = mimetypes.guess_type(resolved_path)
                mime_type = detected_mime or 'application/octet-stream'

            # Show application chooser dialog
            chooser = ApplicationChooserDialog(resolved_path, mime_type, parent_widget)
            if chooser.exec_() == QDialog.Accepted and chooser.selected_app:
                # Launch selected application with the file
                try:
                    import gi
                    gi.require_version('Gio', '2.0')
                    from gi.repository import Gio

                    gfile = Gio.File.new_for_path(resolved_path)
                    chooser.selected_app.launch([gfile], None)
                    logger.info(f"Opened with {chooser.selected_app.get_name()}: {resolved_path}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to launch application: {e}")
                    return False
            else:
                # User cancelled
                return False

    except Exception as e:
        logger.error(f"Error opening with external application: {e}")
        return False


def save_file_as(source_path, default_filename=None, parent_widget=None):
    """
    Show file save dialog and copy file to user-selected location.

    Args:
        source_path: Path to source file to copy
        default_filename: Optional default filename (will use source filename if not provided)
        parent_widget: Optional parent widget for the dialog

    Returns:
        str: Path where file was saved, or None if cancelled/failed
    """
    from PySide6.QtWidgets import QFileDialog

    try:
        # Get default filename
        if not default_filename:
            default_filename = Path(source_path).name

        # Open file save dialog
        save_path, _ = QFileDialog.getSaveFileName(
            parent_widget,
            "Save File As",
            default_filename,
            "All Files (*)"
        )

        if save_path:
            try:
                # Copy file to chosen location
                shutil.copy2(source_path, save_path)
                logger.info(f"File saved to: {save_path}")
                return save_path
            except Exception as e:
                logger.error(f"Failed to save file: {e}")
                return None
        else:
            # User cancelled
            return None

    except Exception as e:
        logger.error(f"Error in save file dialog: {e}")
        return None
