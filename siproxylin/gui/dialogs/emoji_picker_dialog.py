"""
Emoji picker dialog - Pure UI component.

Shows a grid of emoji for the user to select.
Returns the selected emoji string (or None if cancelled).

Features:
- 1800+ emojis organized by Unicode categories
- "Recent" category showing last 10 used emojis (shown first by default)
- Search rebuilds grid with matching emojis only
- Saves emoji usage to database
"""

import logging
from typing import Optional, List, Tuple
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QLabel,
    QDialogButtonBox, QPushButton, QLineEdit, QScrollArea, QWidget, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal, QTimer

from .emoji_data import CATEGORIES, ALL_EMOJI_DATA
from ...db.database import get_db


logger = logging.getLogger('siproxylin.emoji_picker')


def show_emoji_picker_dialog(parent) -> Optional[str]:
    """
    Show emoji picker dialog.

    Args:
        parent: Parent widget

    Returns:
        Selected emoji string, or None if cancelled
    """
    dialog = EmojiPickerDialog(parent)

    # Connect signal for async emoji save (low priority)
    db = get_db()
    dialog.emoji_used.connect(
        lambda emoji: QTimer.singleShot(100, lambda: _save_emoji_usage_async(db, emoji))
    )

    result = dialog.exec()

    if result == QDialog.Accepted:
        return dialog.selected_emoji
    else:
        return None


def _save_emoji_usage_async(db, emoji: str):
    """
    Async helper to save emoji usage to database (low priority).

    Args:
        db: Database instance
        emoji: Emoji character to save
    """
    try:
        # Check if emoji exists
        existing = db.fetchone(
            "SELECT id, use_count FROM recent_emojis WHERE emoji = ?",
            (emoji,)
        )

        if existing:
            # Update existing entry
            db.execute("""
                UPDATE recent_emojis
                SET used_at = CURRENT_TIMESTAMP,
                    use_count = use_count + 1
                WHERE emoji = ?
            """, (emoji,))
            logger.debug(f"Updated emoji usage: {emoji} (count: {existing['use_count'] + 1})")
        else:
            # Insert new entry
            db.execute("""
                INSERT INTO recent_emojis (emoji, use_count)
                VALUES (?, 1)
            """, (emoji,))
            logger.debug(f"Inserted new emoji: {emoji}")

        db.commit()

    except Exception as e:
        logger.error(f"Failed to save emoji usage for {emoji}: {e}", exc_info=True)


class EmojiPickerDialog(QDialog):
    """Enhanced emoji picker with 1800+ emojis, recent emojis, and proper search."""

    # Signal emitted when an emoji is selected (for async saving)
    emoji_used = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = get_db()
        self.selected_emoji = None
        self.current_category = "Recent"  # Start with Recent by default
        self.search_query = ""

        self.setWindowTitle("Choose Emoji")
        self.setMinimumWidth(520)
        self.setMinimumHeight(450)
        self.setMaximumHeight(650)

        self._setup_ui()
        self._load_recent_emojis()
        self._populate_category(self.current_category)

    def _setup_ui(self):
        """Setup the UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header
        header_label = QLabel("<b>Choose an emoji:</b>")
        layout.addWidget(header_label)

        # Search field
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText(
            "Search emojis... (try ':D', 'smile', 'heart', 'fire')"
        )
        self.search_field.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_field)

        # Category navigation buttons (horizontal)
        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(3)

        # Build category list with Recent first
        self.category_names = ["Recent"] + list(CATEGORIES.keys())

        for category_name in self.category_names:
            emoji_icon = self._get_category_emoji(category_name)
            btn = QPushButton(emoji_icon)
            btn.setMinimumSize(60, 40)
            btn.setStyleSheet("font-size: 22px; padding: 4px;")
            btn.setToolTip(category_name)  # Show category name on hover
            btn.setProperty("category_name", category_name)
            btn.clicked.connect(lambda checked=False, cat=category_name: self._switch_category(cat))

            # Style active category
            if category_name == self.current_category:
                btn.setStyleSheet("font-size: 22px; padding: 4px; font-weight: bold; background-color: #d0d0d0;")

            nav_layout.addWidget(btn)

        nav_layout.addStretch()
        layout.addLayout(nav_layout)

        # Create scrollable area for emoji grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setSpacing(5)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)

        self.scroll_area.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll_area)

        # Status label showing emoji count
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray; font-size: 9pt;")
        layout.addWidget(self.status_label)

        # Cancel button
        button_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Focus search field for quick typing
        self.search_field.setFocus()

    def _get_category_emoji(self, category_name: str) -> str:
        """Get emoji icon for category buttons."""
        emoji_icons = {
            "Recent": "🕐",
            "Smileys & Emotion": "😀",
            "People & Body": "👋",
            "Animals & Nature": "🐾",
            "Food & Drink": "🍔",
            "Travel & Places": "✈️",
            "Activities": "⚽",
            "Objects": "💡",
            "Symbols": "♻️",
            "Flags": "🇸🇦",
        }
        return emoji_icons.get(category_name, "❓")

    def _load_recent_emojis(self):
        """Load recent emojis from database (last 10)."""
        try:
            # Ensure table exists (in case migration hasn't run yet)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS recent_emojis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    emoji TEXT NOT NULL,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    use_count INTEGER DEFAULT 1
                )
            """)
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recent_emojis_emoji ON recent_emojis(emoji)")
            self.db.commit()

            rows = self.db.fetchall("""
                SELECT emoji, use_count
                FROM recent_emojis
                ORDER BY used_at DESC
                LIMIT 10
            """)
            self.recent_emojis = [(row['emoji'], [], []) for row in rows]
            logger.debug(f"Loaded {len(self.recent_emojis)} recent emojis from database")
        except Exception as e:
            logger.error(f"Failed to load recent emojis: {e}", exc_info=True)
            self.recent_emojis = []


    def _switch_category(self, category_name: str):
        """Switch to a different category."""
        if category_name == self.current_category:
            return

        self.current_category = category_name
        self.search_field.clear()  # Clear search when switching categories
        self._populate_category(category_name)
        self._update_category_buttons()

    def _update_category_buttons(self):
        """Update category button styles to highlight active category."""
        for btn in self.findChildren(QPushButton):
            cat_name = btn.property("category_name")
            if cat_name:
                if cat_name == self.current_category:
                    btn.setStyleSheet("font-size: 22px; padding: 4px; font-weight: bold; background-color: #d0d0d0;")
                else:
                    btn.setStyleSheet("font-size: 22px; padding: 4px;")

    def _populate_category(self, category_name: str):
        """Populate emoji grid with emojis from the specified category."""
        # Clear existing grid
        self._clear_grid()

        # Get emojis for this category
        if category_name == "Recent":
            emojis = self.recent_emojis
            if not emojis:
                # Show message if no recent emojis
                label = QLabel("<i>No recent emojis yet. Use an emoji to see it here!</i>")
                label.setStyleSheet("color: gray; padding: 20px;")
                label.setAlignment(Qt.AlignCenter)
                self.scroll_layout.addWidget(label)
                self.status_label.setText("No recent emojis")
                return
        else:
            emojis = CATEGORIES.get(category_name, [])

        self._build_emoji_grid(emojis, category_name)

    def _on_search_changed(self, text: str):
        """Handle search field changes - rebuild grid with matching emojis."""
        query = text.lower().strip()
        self.search_query = query

        # Clear existing grid
        self._clear_grid()

        if not query:
            # No search query - show current category
            self._populate_category(self.current_category)
            return

        # Search across ALL emojis
        matching_emojis = []
        for emoji, keywords, text_reps in ALL_EMOJI_DATA:
            # Check if query matches keywords or text representations
            if any(query in kw for kw in keywords) or any(query in tr.lower() for tr in text_reps):
                matching_emojis.append((emoji, keywords, text_reps))

        if not matching_emojis:
            # No matches found
            label = QLabel(f"<i>No emojis found for '{text}'</i>")
            label.setStyleSheet("color: gray; padding: 20px;")
            label.setAlignment(Qt.AlignCenter)
            self.scroll_layout.addWidget(label)
            self.status_label.setText("No matches")
            return

        # Build grid with matching emojis
        self._build_emoji_grid(matching_emojis, f"Search: '{text}'")

    def _clear_grid(self):
        """Clear the emoji grid."""
        # Remove all widgets from scroll layout
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # Clear nested layout
                while item.layout().count():
                    nested_item = item.layout().takeAt(0)
                    if nested_item.widget():
                        nested_item.widget().deleteLater()
                item.layout().deleteLater()

    def _build_emoji_grid(self, emojis: List[Tuple[str, List[str], List[str]]], title: str):
        """
        Build emoji grid from list of emojis.

        Args:
            emojis: List of (emoji, keywords, text_reps) tuples
            title: Title to display above grid
        """
        if not emojis:
            return

        # Add title label
        title_label = QLabel(f"<b>{title}</b> ({len(emojis)} emojis)")
        title_label.setStyleSheet("padding: 5px 0px;")
        self.scroll_layout.addWidget(title_label)

        # Create grid
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)

        # Add emoji buttons in 8 columns
        for i, (emoji, keywords, text_reps) in enumerate(emojis):
            row = i // 8
            col = i % 8

            btn = QPushButton(emoji)
            btn.setFixedSize(50, 50)
            btn.setStyleSheet("""
                QPushButton {
                    font-size: 24px;
                    font-family: "Noto Color Emoji", "Apple Color Emoji", "Segoe UI Emoji", monospace;
                    padding: 2px;
                    border: 1px solid transparent;
                }
                QPushButton:hover {
                    background-color: #e0e0e0;
                    border: 1px solid #c0c0c0;
                }
            """)

            # Set tooltip with keywords
            if keywords:
                tooltip = f"{emoji} - {', '.join(keywords[:5])}"
                btn.setToolTip(tooltip)

            btn.clicked.connect(lambda checked=False, e=emoji: self._on_emoji_clicked(e))
            grid.addWidget(btn, row, col)

        self.scroll_layout.addLayout(grid)
        self.scroll_layout.addStretch()

        # Update status
        self.status_label.setText(f"Showing {len(emojis)} emojis")

    def _on_emoji_clicked(self, emoji: str):
        """Handle emoji selection."""
        self.selected_emoji = emoji

        # Emit signal for async save (low priority, non-blocking)
        self.emoji_used.emit(emoji)

        # Accept dialog immediately (don't wait for save)
        self.accept()
