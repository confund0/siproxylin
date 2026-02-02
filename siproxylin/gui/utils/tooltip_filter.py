"""
Tooltip event filter for Siproxylin.

Provides custom tooltip delay behavior.
"""

import logging
from PySide6.QtCore import QObject, QEvent, QTimer
from PySide6.QtWidgets import QToolTip


logger = logging.getLogger('siproxylin.tooltip_filter')


class TooltipEventFilter(QObject):
    """
    Global event filter to control tooltip show delay.

    Qt's default tooltip behavior shows after ~700ms of hovering.
    This filter increases that delay for better UX.
    """

    def __init__(self, delay_ms: int = 1200, parent=None):
        """
        Initialize tooltip event filter.

        Args:
            delay_ms: Delay in milliseconds before showing tooltip (default: 1200)
            parent: Parent QObject
        """
        super().__init__(parent)
        self.delay_ms = delay_ms
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._show_tooltip)

        self.pending_widget = None
        self.pending_pos = None
        self.pending_text = None

    def eventFilter(self, obj, event):
        """
        Filter events to implement custom tooltip delay.

        Args:
            obj: QObject receiving the event
            event: QEvent

        Returns:
            bool: True to filter out event, False to pass through
        """
        # We'll intercept HoverEnter events and delay tooltip display
        if event.type() == QEvent.ToolTip:
            # Cancel any pending tooltip
            self.timer.stop()

            # Get tooltip text
            tooltip_text = obj.toolTip() if hasattr(obj, 'toolTip') else None

            if tooltip_text and tooltip_text.strip():
                # Store tooltip info
                self.pending_widget = obj
                self.pending_text = tooltip_text

                # Get cursor position from help event
                if hasattr(event, 'globalPos'):
                    self.pending_pos = event.globalPos()
                else:
                    self.pending_pos = None

                # Start timer to show tooltip after delay
                self.timer.start(self.delay_ms)

                # Filter out the event (prevent immediate tooltip)
                return True

        elif event.type() == QEvent.Leave:
            # Cancel pending tooltip when mouse leaves
            if obj == self.pending_widget:
                self.timer.stop()
                self.pending_widget = None

        return False  # Pass through other events

    def _show_tooltip(self):
        """Show the pending tooltip after delay."""
        if self.pending_widget and self.pending_text:
            if self.pending_pos:
                QToolTip.showText(self.pending_pos, self.pending_text, self.pending_widget)
            else:
                # Fallback if we don't have position
                logger.debug("Showing tooltip without position (fallback)")

        # Clear pending state
        self.pending_widget = None
        self.pending_pos = None
        self.pending_text = None
