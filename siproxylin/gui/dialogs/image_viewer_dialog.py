"""
Image viewer dialog for displaying enlarged images.
"""

import logging
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy,
    QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QKeyEvent, QWheelEvent, QShowEvent

logger = logging.getLogger('siproxylin.image_viewer')


class ApplicationChooserDialog(QDialog):
    """Dialog to choose an application to open a file (Linux)."""

    def __init__(self, file_path, mime_type, parent=None):
        """
        Initialize application chooser dialog.

        Args:
            file_path: Path to the file to open
            mime_type: MIME type of the file
            parent: Parent widget
        """
        super().__init__(parent)
        self.file_path = file_path
        self.mime_type = mime_type
        self.selected_app = None

        self._setup_ui()
        self._load_applications()

    def _setup_ui(self):
        """Setup dialog UI."""
        self.setWindowTitle("Open With")
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        # Info label
        filename = Path(self.file_path).name
        info_label = QLabel(f"Choose an application to open:\n{filename}")
        layout.addWidget(info_label)

        # Application list
        self.app_list = QListWidget()
        self.app_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.app_list)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._on_ok_clicked)
        button_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _load_applications(self):
        """Load applications that can open this MIME type."""
        try:
            # Try using GIO to get applications for MIME type
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio

            apps = Gio.AppInfo.get_all_for_type(self.mime_type)

            if apps:
                for app in apps:
                    name = app.get_name()
                    description = app.get_description() or ""

                    item = QListWidgetItem(f"{name}")
                    if description:
                        item.setToolTip(description)
                    item.setData(Qt.UserRole, app)
                    self.app_list.addItem(item)
            else:
                # No apps found for this MIME type
                self.app_list.addItem("No applications found for this file type")

        except ImportError:
            # GIO not available, show error
            logger.warning("GIO not available for application chooser")
            self.app_list.addItem("Application chooser not available (GIO required)")

    def _on_item_double_clicked(self, item):
        """Handle double-click on application item."""
        app = item.data(Qt.UserRole)
        if app:
            self.selected_app = app
            self.accept()

    def _on_ok_clicked(self):
        """Handle OK button click."""
        current_item = self.app_list.currentItem()
        if current_item:
            app = current_item.data(Qt.UserRole)
            if app:
                self.selected_app = app
                self.accept()


class ImageViewerDialog(QDialog):
    """Dialog for viewing enlarged images with zoom controls."""

    def __init__(self, image_path, parent=None):
        """
        Initialize image viewer dialog.

        Args:
            image_path: Path to image file
            parent: Parent widget
        """
        super().__init__(parent)

        self.image_path = image_path
        self.zoom_level = 1.0  # 100%
        self.original_pixmap = None
        self.zoom_history = []  # Track zoom levels for back functionality
        self.initial_fit_done = False  # Track if initial fit has been done

        self._setup_ui()
        self._load_image()

    def _setup_ui(self):
        """Setup dialog UI."""
        # Window setup
        filename = Path(self.image_path).name
        self.setWindowTitle(f"Image Viewer - {filename}")
        self.resize(1200, 800)  # Larger default size

        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Scroll area for image
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)  # Changed to False for proper zoom scrolling
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Image label
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setScaledContents(False)
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area, 1)  # Stretch factor 1

        # Control buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(5)

        zoom_out_btn = QPushButton("Zoom Out (25%)")
        zoom_out_btn.clicked.connect(self._zoom_out)
        button_layout.addWidget(zoom_out_btn)

        zoom_in_btn = QPushButton("Zoom In (25%)")
        zoom_in_btn.clicked.connect(self._zoom_in)
        button_layout.addWidget(zoom_in_btn)

        fit_btn = QPushButton("Fit to Window")
        fit_btn.clicked.connect(self._fit_to_window)
        button_layout.addWidget(fit_btn)

        actual_size_btn = QPushButton("Actual Size")
        actual_size_btn.clicked.connect(self._actual_size)
        button_layout.addWidget(actual_size_btn)

        button_layout.addStretch()

        # Save button
        save_btn = QPushButton("Save As...")
        save_btn.clicked.connect(self._save_image)
        button_layout.addWidget(save_btn)

        # Open with external app button
        open_btn = QPushButton("Open With...")
        open_btn.clicked.connect(self._open_with_external)
        button_layout.addWidget(open_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        # Install event filter on scroll area for wheel events
        self.scroll_area.installEventFilter(self)

    def _load_image(self):
        """Load image from file path."""
        self.original_pixmap = QPixmap(self.image_path)

        if self.original_pixmap.isNull():
            self.image_label.setText(f"Failed to load image:\n{self.image_path}")
        else:
            # Display at 100% initially (will be scaled in showEvent)
            self._update_display()

    def _push_zoom_history(self):
        """Save current zoom level to history."""
        self.zoom_history.append(self.zoom_level)

    def _update_display(self):
        """Update image display with current zoom level."""
        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        # Scale pixmap based on zoom level
        scaled_size = self.original_pixmap.size() * self.zoom_level
        scaled_pixmap = self.original_pixmap.scaled(
            scaled_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.image_label.setPixmap(scaled_pixmap)
        self.image_label.adjustSize()

    def _zoom_in(self):
        """Zoom in (increase by 25%)."""
        self._push_zoom_history()
        self.zoom_level *= 1.25
        self._update_display()

    def _zoom_out(self):
        """Zoom out (decrease by 25%)."""
        self._push_zoom_history()
        self.zoom_level *= 0.8
        self._update_display()

    def _fit_to_window(self):
        """Scale image to fit window with 2.5% padding, scaling up or down as needed."""
        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        self._push_zoom_history()

        # Calculate zoom to fit in current scroll area
        available_size = self.scroll_area.viewport().size()

        # Apply 2.5% padding on each side (5% total = 95% usable)
        available_width = available_size.width() * 0.95
        available_height = available_size.height() * 0.95

        # Calculate zoom level to fit (scale up or down as needed)
        width_ratio = available_width / self.original_pixmap.width()
        height_ratio = available_height / self.original_pixmap.height()

        # Use whichever axis is more constrained (removed 1.0 limit to allow scaling up)
        self.zoom_level = min(width_ratio, height_ratio)
        self._update_display()

    def _actual_size(self):
        """Display image at actual size (100%)."""
        self._push_zoom_history()
        self.zoom_level = 1.0
        self._update_display()

    def _save_image(self):
        """Save image to user-selected location."""
        from ...utils import save_file_as
        save_file_as(self.image_path, parent_widget=self)

    def _open_with_external(self):
        """Open image with external application."""
        from ...utils import open_file_with_external_app
        open_file_with_external_app(self.image_path, parent_widget=self)

    def eventFilter(self, obj, event):
        """Handle wheel events for zoom on scroll area."""
        if obj == self.scroll_area and event.type() == event.Type.Wheel:
            wheel_event = event
            modifiers = wheel_event.modifiers()

            # Check for Ctrl modifier or touchpad pinch gesture
            if modifiers & Qt.ControlModifier:
                # Zoom in/out with Ctrl+Scroll
                delta = wheel_event.angleDelta().y()
                if delta > 0:
                    self._zoom_in()
                elif delta < 0:
                    self._zoom_out()
                return True  # Event handled

        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent):
        """Handle wheel events for zooming with Ctrl modifier."""
        modifiers = event.modifiers()

        # Check for Ctrl modifier
        if modifiers & Qt.ControlModifier:
            # Zoom in/out with Ctrl+Scroll
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom_in()
            elif delta < 0:
                self._zoom_out()
            event.accept()
        else:
            # Let default scroll behavior handle it
            super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        """Handle key presses."""
        modifiers = event.modifiers()

        if event.key() == Qt.Key_Escape:
            self.close()
        elif modifiers & Qt.ControlModifier:
            # Ctrl+Plus or Ctrl+Equal
            if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
                self._zoom_in()
            # Ctrl+Minus
            elif event.key() == Qt.Key_Minus:
                self._zoom_out()
            # Ctrl+0 for actual size
            elif event.key() == Qt.Key_0:
                self._actual_size()
            else:
                super().keyPressEvent(event)
        elif event.key() == Qt.Key_Plus or event.key() == Qt.Key_Equal:
            self._zoom_in()
        elif event.key() == Qt.Key_Minus or event.key() == Qt.Key_Underscore:
            self._zoom_out()
        elif event.key() == Qt.Key_0:
            self._actual_size()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event: QShowEvent):
        """Handle show event - fit image to window on first show."""
        super().showEvent(event)

        # On first show, fit the image to the window
        if not self.initial_fit_done and self.original_pixmap and not self.original_pixmap.isNull():
            self.initial_fit_done = True
            # Use QTimer to ensure viewport size is correct after window is fully shown
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._fit_to_window)
