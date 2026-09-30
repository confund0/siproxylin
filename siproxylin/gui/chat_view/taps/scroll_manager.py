"""
Scroll Manager - Auto-scroll and scroll button management.

Handles:
- Scroll-to-bottom floating button
- Auto-scroll detection
- Button visibility based on scroll position
"""

import logging
from PySide6.QtWidgets import QPushButton


logger = logging.getLogger('siproxylin.chat_view.scroll_manager')

# Distance from the bottom in pixels that still counts as "at bottom".
# Small, so that the newest message is visible when it is marked as read.
AT_BOTTOM_PX = 20


class ScrollManager:
    """
    Manages scroll behavior and scroll-to-bottom button.

    Args:
        message_area: QListView displaying messages
        message_container: Parent widget for floating button
    """

    def __init__(self, message_area, message_container, message_widget=None):
        """Initialize scroll manager with message area and container."""
        self.message_area = message_area
        self.message_container = message_container
        self.message_widget = message_widget  # Reference to MessageDisplayWidget for clearing highlights

        # Create scroll-to-bottom button (floating)
        self.scroll_to_bottom_btn = QPushButton("⬇")
        self.scroll_to_bottom_btn.setObjectName("scrollToBottomButton")
        self.scroll_to_bottom_btn.setFixedSize(40, 40)
        self.scroll_to_bottom_btn.clicked.connect(self._scroll_to_bottom)
        self.scroll_to_bottom_btn.hide()  # Hidden by default

        # Position button at bottom-right using geometry (will be set in resizeEvent)
        self.scroll_to_bottom_btn.setParent(message_container)

        # Connect scroll bar to check position (range changes too: a resize can make content fit)
        scrollbar = self.message_area.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll_changed)
        scrollbar.rangeChanged.connect(self._on_scroll_changed)

    def is_at_bottom(self):
        """
        Check if the view shows the newest messages (at the bottom).

        Same rule for button visibility, auto-scroll, reload and read state.

        Returns:
            True if at most AT_BOTTOM_PX from the bottom, or all content fits
        """
        scrollbar = self.message_area.verticalScrollBar()
        maximum = scrollbar.maximum()
        if maximum == 0:
            return True  # All content fits = at bottom

        return (maximum - scrollbar.value()) <= AT_BOTTOM_PX

    def update_button(self):
        """Show the scroll-to-bottom button when not at bottom; always in a search view."""
        in_search = self.message_widget is not None and getattr(self.message_widget, 'view_mode', 'live') == 'search'
        if in_search or not self.is_at_bottom():
            self.scroll_to_bottom_btn.show()
            self._position_scroll_button()
        else:
            self.scroll_to_bottom_btn.hide()

    def set_unread(self, unread: bool):
        """Mark the button when the chat has unread messages (styled by the theme, property "unread")."""
        if self.scroll_to_bottom_btn.property("unread") == unread:
            return
        self.scroll_to_bottom_btn.setProperty("unread", unread)
        # Re-apply the style sheet for the new property value
        self.scroll_to_bottom_btn.style().unpolish(self.scroll_to_bottom_btn)
        self.scroll_to_bottom_btn.style().polish(self.scroll_to_bottom_btn)

    def _on_scroll_changed(self, *args):
        """Handle scroll position and range changes to show/hide scroll-to-bottom button."""
        self.update_button()

    def _scroll_to_bottom(self):
        """Scroll to bottom when button is clicked - also leaves a search view and marks as read."""
        if self.message_widget and hasattr(self.message_widget, 'return_to_live'):
            self.message_widget.return_to_live()
        else:
            # Fallback: just scroll to bottom
            self.message_area.scrollToBottom()

    def _position_scroll_button(self):
        """Position scroll-to-bottom button at bottom-right of message area."""
        # Position button 10px from bottom-right
        container_width = self.message_area.width()
        container_height = self.message_area.height()

        x = container_width - self.scroll_to_bottom_btn.width() - 10
        y = container_height - self.scroll_to_bottom_btn.height() - 10

        self.scroll_to_bottom_btn.move(x, y)
        self.scroll_to_bottom_btn.raise_()  # Bring to front

    def on_resize(self):
        """Called when parent widget is resized to reposition button."""
        self._position_scroll_button()
