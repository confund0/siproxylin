"""
Log viewer window for Siproxylin.

Non-modal window for viewing application and XML logs with:
- Real-time tail (updates as new lines appear)
- Regex search
- Log level filtering
- Date/time filtering
"""

import logging
import re
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QLabel, QComboBox, QCheckBox
)
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat, QShortcut, QKeySequence


logger = logging.getLogger('siproxylin.log_viewer')


class LogViewer(QWidget):
    """
    Log viewer window with search and filtering capabilities.
    Non-modal - can stay open while using main window.
    """

    def __init__(self, log_path: Path, title: str = "Log Viewer", parent=None):
        """
        Initialize log viewer.

        Args:
            log_path: Path to log file
            title: Window title
            parent: Parent widget
        """
        super().__init__(parent)

        self.log_path = log_path
        self.last_position = 0  # For tail functionality
        self.search_pattern: Optional[re.Pattern] = None

        # Window setup
        self.setWindowTitle(title)
        self.setGeometry(150, 150, 1000, 600)

        # Setup UI
        self._create_ui()

        # Start tail timer (update every 1 second)
        self.tail_timer = QTimer(self)
        self.tail_timer.timeout.connect(self._tail_log)
        self.tail_timer.start(1000)  # 1000ms = 1 second

        # Initial load
        self._load_log()

        logger.info(f"Log viewer opened: {log_path}")

    def _create_ui(self):
        """Create UI components."""
        layout = QVBoxLayout(self)

        # =====================================================================
        # Top toolbar: Search and filters
        # =====================================================================
        toolbar_layout = QHBoxLayout()

        # Regex search
        search_label = QLabel("Regex:")
        toolbar_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("e.g., 2025-11-19.*peer_name")
        self.search_input.returnPressed.connect(self._on_search)
        toolbar_layout.addWidget(self.search_input, stretch=3)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._on_search)
        toolbar_layout.addWidget(self.search_button)

        self.clear_search_button = QPushButton("Clear")
        self.clear_search_button.clicked.connect(self._on_clear_search)
        toolbar_layout.addWidget(self.clear_search_button)

        toolbar_layout.addSpacing(20)

        # Log level filter
        level_label = QLabel("Level:")
        toolbar_layout.addWidget(level_label)

        self.level_filter = QComboBox()
        self.level_filter.addItems(['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
        self.level_filter.currentTextChanged.connect(self._on_filter_changed)
        toolbar_layout.addWidget(self.level_filter)

        toolbar_layout.addSpacing(20)

        # Auto-tail toggle
        self.auto_tail_checkbox = QCheckBox("Auto-tail")
        self.auto_tail_checkbox.setChecked(True)
        self.auto_tail_checkbox.stateChanged.connect(self._on_auto_tail_changed)
        toolbar_layout.addWidget(self.auto_tail_checkbox)

        toolbar_layout.addStretch()

        layout.addLayout(toolbar_layout)

        # =====================================================================
        # Log display area
        # =====================================================================
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setLineWrapMode(QTextEdit.NoWrap)
        self.log_display.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: 'Courier New', monospace;
                font-size: 10pt;
            }
        """)
        layout.addWidget(self.log_display)

        # =====================================================================
        # Bottom status bar
        # =====================================================================
        status_layout = QHBoxLayout()

        self.status_label = QLabel(f"Log: {self.log_path}")
        status_layout.addWidget(self.status_label, stretch=1)

        self.line_count_label = QLabel("Lines: 0")
        status_layout.addWidget(self.line_count_label)

        self.tail_status_label = QLabel("")
        status_layout.addWidget(self.tail_status_label)

        status_layout.addSpacing(20)

        # Reload button
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self._on_reload)
        status_layout.addWidget(self.reload_button)

        # Export button
        self.export_button = QPushButton("Export Selection")
        self.export_button.clicked.connect(self._on_export)
        status_layout.addWidget(self.export_button)

        # Quit button
        self.quit_button = QPushButton("Quit")
        self.quit_button.clicked.connect(self.close)
        status_layout.addWidget(self.quit_button)

        layout.addLayout(status_layout)

        # =====================================================================
        # Keyboard shortcuts
        # =====================================================================
        # Ctrl+W to close (Sway/i3 standard)
        QShortcut(QKeySequence("Ctrl+W"), self, self.close)
        # Ctrl+Q to close (alternative)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)
        # Escape to close
        QShortcut(QKeySequence("Escape"), self, self.close)

    def _load_log(self):
        """Load entire log file."""
        if not self.log_path.exists():
            self.log_display.setPlainText(f"Log file not found: {self.log_path}")
            self.last_position = 0
            return

        try:
            with open(self.log_path, 'rb') as f:
                # Read as bytes to get accurate file position
                content_bytes = f.read()
                self.last_position = f.tell()

            # Decode to string for display
            content = content_bytes.decode('utf-8', errors='replace')

            self._display_content(content)
            self._update_line_count()

            # Scroll to bottom after loading (use QTimer to ensure UI is rendered)
            QTimer.singleShot(100, self._scroll_to_bottom)

            logger.debug(f"Loaded log, position: {self.last_position}")

        except Exception as e:
            logger.error(f"Failed to load log: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.log_display.setPlainText(f"Error loading log: {e}")
            self.last_position = 0

    def _scroll_to_bottom(self):
        """Scroll the log display to the absolute bottom (tail position)."""
        # Move cursor to end
        cursor = self.log_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_display.setTextCursor(cursor)

        # Also set scrollbar to maximum
        scrollbar = self.log_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        # Ensure cursor is visible (forces scroll)
        self.log_display.ensureCursorVisible()

    def _tail_log(self):
        """Tail the log file (read new content since last read)."""
        if not self.auto_tail_checkbox.isChecked():
            self.tail_status_label.setText("")
            return

        if not self.log_path.exists():
            self.tail_status_label.setText("⚠ File not found")
            return

        try:
            # Get current file size to detect rotation/truncation
            file_size = self.log_path.stat().st_size

            # If file was truncated/rotated, reset position
            if file_size < self.last_position:
                logger.info(f"Log file rotated/truncated, resetting position")
                self.last_position = 0

            # If file size hasn't changed, nothing to read
            if file_size == self.last_position:
                self.tail_status_label.setText(f"✓ Tailing (pos: {self.last_position})")
                return

            with open(self.log_path, 'rb') as f:
                # Seek to last known position
                f.seek(self.last_position)
                new_content_bytes = f.read()

                if new_content_bytes:
                    self.last_position = f.tell()
                    # Decode to string
                    new_content = new_content_bytes.decode('utf-8', errors='replace')
                    self._append_content(new_content)
                    self._update_line_count()
                    self.tail_status_label.setText(f"✓ Read {len(new_content_bytes)} bytes")
                    logger.debug(f"Tailed {len(new_content_bytes)} bytes, position: {self.last_position}")

        except Exception as e:
            self.tail_status_label.setText(f"⚠ Error")
            logger.error(f"Failed to tail log: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _display_content(self, content: str):
        """
        Display content with optional filtering and search highlighting.

        Args:
            content: Log content to display
        """
        lines = content.split('\n')
        filtered_lines = self._filter_lines(lines)

        self.log_display.clear()

        if self.search_pattern:
            # Highlight search matches
            self._display_with_highlights(filtered_lines)
        else:
            self.log_display.setPlainText('\n'.join(filtered_lines))

    def _append_content(self, content: str):
        """
        Append new content to display.

        Args:
            content: New log content
        """
        lines = content.split('\n')
        filtered_lines = self._filter_lines(lines)

        if not filtered_lines or (len(filtered_lines) == 1 and not filtered_lines[0]):
            return

        # Check if scrollbar is at the bottom BEFORE appending
        scrollbar = self.log_display.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 10  # Allow 10px tolerance

        # Append to display
        cursor = self.log_display.textCursor()
        cursor.movePosition(QTextCursor.End)

        for line in filtered_lines:
            if line:  # Skip empty lines
                cursor.insertText(line + '\n')

        # Only auto-scroll to bottom if user was already at the bottom
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _filter_lines(self, lines: list[str]) -> list[str]:
        """
        Filter lines based on search pattern and log level.

        Args:
            lines: Lines to filter

        Returns:
            Filtered lines
        """
        filtered = []

        level_filter = self.level_filter.currentText()

        for line in lines:
            # Log level filtering
            if level_filter != 'ALL':
                if f' - {level_filter} - ' not in line:
                    continue

            # Search pattern filtering
            if self.search_pattern:
                if not self.search_pattern.search(line):
                    continue

            filtered.append(line)

        return filtered

    def _display_with_highlights(self, lines: list[str]):
        """
        Display lines with search highlights.

        Args:
            lines: Lines to display
        """
        cursor = self.log_display.textCursor()
        cursor.movePosition(QTextCursor.Start)

        # Highlight format
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor('#ffff00'))  # Yellow
        highlight_format.setForeground(QColor('#000000'))  # Black text

        normal_format = QTextCharFormat()

        for line in lines:
            if not line:
                continue

            # Find all matches in line
            matches = list(self.search_pattern.finditer(line))

            if matches:
                last_end = 0
                for match in matches:
                    # Insert text before match
                    if match.start() > last_end:
                        cursor.insertText(line[last_end:match.start()], normal_format)

                    # Insert highlighted match
                    cursor.insertText(match.group(), highlight_format)
                    last_end = match.end()

                # Insert remaining text
                if last_end < len(line):
                    cursor.insertText(line[last_end:], normal_format)

                cursor.insertText('\n', normal_format)
            else:
                cursor.insertText(line + '\n', normal_format)

    def _update_line_count(self):
        """Update line count label."""
        text = self.log_display.toPlainText()
        line_count = len(text.split('\n')) if text else 0
        self.line_count_label.setText(f"Lines: {line_count}")

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _on_search(self):
        """Handle search button click."""
        pattern_text = self.search_input.text().strip()

        if not pattern_text:
            self._on_clear_search()
            return

        try:
            self.search_pattern = re.compile(pattern_text)
            logger.info(f"Search pattern: {pattern_text}")
            self.status_label.setText(f"Searching: {pattern_text}")
            self._load_log()  # Reload with filter
        except re.error as e:
            logger.error(f"Invalid regex: {e}")
            self.status_label.setText(f"Invalid regex: {e}")

    def _on_clear_search(self):
        """Clear search filter."""
        self.search_input.clear()
        self.search_pattern = None
        self.status_label.setText(f"Log: {self.log_path}")
        self._load_log()
        logger.info("Search cleared")

    def _on_filter_changed(self):
        """Handle log level filter change."""
        level = self.level_filter.currentText()
        logger.info(f"Log level filter: {level}")
        self._load_log()

    def _on_auto_tail_changed(self, state):
        """Handle auto-tail checkbox change."""
        enabled = state == Qt.Checked
        logger.info(f"Auto-tail: {enabled}")

        if enabled:
            self.tail_timer.start(1000)
        else:
            self.tail_timer.stop()

    def _on_reload(self):
        """Handle reload button click."""
        logger.info("Reloading log")
        self.last_position = 0
        self._load_log()

    def _on_export(self):
        """Handle export selection button click."""
        from PySide6.QtWidgets import QFileDialog

        selected_text = self.log_display.textCursor().selectedText()

        if not selected_text:
            self.status_label.setText("No text selected")
            return

        # Open save dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Selection",
            str(Path.home() / "log_export.txt"),
            "Text Files (*.txt);;All Files (*)"
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    # Replace Qt paragraph separator with newlines
                    text = selected_text.replace('\u2029', '\n')
                    f.write(text)

                self.status_label.setText(f"Exported to: {file_path}")
                logger.info(f"Exported selection to {file_path}")
            except Exception as e:
                logger.error(f"Export failed: {e}")
                self.status_label.setText(f"Export failed: {e}")

    def closeEvent(self, event):
        """Handle window close."""
        self.tail_timer.stop()
        logger.info(f"Log viewer closed: {self.log_path}")
        super().closeEvent(event)
