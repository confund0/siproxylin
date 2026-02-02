"""
Spell-checked QTextEdit widget for Siproxylin.

Integrates EnchantHighlighter with context menu for spelling suggestions.
"""

import logging
from PySide6.QtWidgets import QTextEdit, QMenu
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QTextCursor, QAction

from .spell_highlighter import EnchantHighlighter, SpellingBlockData

logger = logging.getLogger('siproxylin.spell_text_edit')


class SpellTextEdit(QTextEdit):
    """
    QTextEdit with integrated spell checking.

    Features:
    - Red wavy underlines for misspelled words
    - Right-click context menu with suggestions
    - Add to dictionary option
    - Preserves standard QTextEdit context menu (cut/copy/paste)
    """

    def __init__(self, parent=None, language='en_US', spell_check_enabled=True):
        """
        Initialize spell-checked text edit.

        Args:
            parent: Parent widget
            language: Dictionary language code (default: en_US)
            spell_check_enabled: Enable spell checking (default: True)
        """
        super().__init__(parent)

        self._spell_check_enabled = spell_check_enabled
        self._highlighter = None

        # Initialize spell checker if enabled
        if self._spell_check_enabled:
            self._highlighter = EnchantHighlighter(self.document(), language)
            if not self._highlighter.is_available():
                logger.warning("Spell checker not available, running without spell check")
                self._spell_check_enabled = False

    def is_spell_check_available(self) -> bool:
        """Check if spell checking is available and working."""
        return self._spell_check_enabled and self._highlighter and self._highlighter.is_available()

    def set_spell_check_enabled(self, enabled: bool):
        """
        Enable or disable spell checking.

        Args:
            enabled: True to enable, False to disable
        """
        if enabled and not self._highlighter:
            # Initialize highlighter
            self._highlighter = EnchantHighlighter(self.document(), 'en_US')
            if self._highlighter.is_available():
                self._spell_check_enabled = True
                logger.info("Spell check enabled")
            else:
                self._spell_check_enabled = False
                logger.warning("Cannot enable spell check: dictionary not available")
        elif not enabled and self._highlighter:
            # Disable by removing highlighter
            self._highlighter.setDocument(None)
            self._spell_check_enabled = False
            logger.info("Spell check disabled")

    def set_language(self, language: str):
        """
        Change spell check language.

        Args:
            language: Language code (e.g., 'en_US', 'de_DE', 'fr_FR')
        """
        if self._highlighter:
            self._highlighter.set_language(language)

    def get_available_languages(self):
        """Get list of available dictionary languages."""
        if self._highlighter:
            return self._highlighter.get_available_languages()
        return []

    def contextMenuEvent(self, event):
        """
        Override context menu to add spelling suggestions.

        Args:
            event: Context menu event
        """
        if not self.is_spell_check_available():
            # No spell check - show default menu
            super().contextMenuEvent(event)
            return

        # Get cursor at click position
        cursor = self.cursorForPosition(event.pos())
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()

        # Check if we clicked on a misspelled word
        misspelled_word = self._get_misspelled_word_at_cursor(cursor)

        # Create context menu
        menu = QMenu(self)

        if misspelled_word:
            # Add spelling suggestions
            suggestions = self._highlighter.suggest(misspelled_word, max_suggestions=10)

            if suggestions:
                # Add suggestion actions
                for suggestion in suggestions:
                    action = QAction(suggestion, self)
                    action.triggered.connect(
                        lambda checked=False, s=suggestion, c=cursor: self._replace_word(c, s)
                    )
                    menu.addAction(action)
            else:
                # No suggestions available
                no_suggestions = QAction("(no suggestions)", self)
                no_suggestions.setEnabled(False)
                menu.addAction(no_suggestions)

            menu.addSeparator()

            # Add to dictionary
            add_to_dict = QAction(f"Add '{misspelled_word}' to dictionary", self)
            add_to_dict.triggered.connect(
                lambda: self._add_to_dictionary(misspelled_word)
            )
            menu.addAction(add_to_dict)

            menu.addSeparator()

        # Add standard editing actions
        if self.textCursor().hasSelection():
            cut_action = menu.addAction("Cut")
            cut_action.triggered.connect(self.cut)
            copy_action = menu.addAction("Copy")
            copy_action.triggered.connect(self.copy)

        paste_action = menu.addAction("Paste")
        paste_action.triggered.connect(self.paste)

        menu.addSeparator()

        select_all = menu.addAction("Select All")
        select_all.triggered.connect(self.selectAll)

        # Show menu
        menu.exec_(event.globalPos())

    def _get_misspelled_word_at_cursor(self, cursor):
        """
        Check if cursor is on a misspelled word.

        Args:
            cursor: Text cursor

        Returns:
            Misspelled word or None
        """
        # Get block and block data
        block = cursor.block()
        block_data = block.userData()

        if not isinstance(block_data, SpellingBlockData):
            return None

        # Get cursor position within block
        pos_in_block = cursor.positionInBlock()

        # Check if cursor is within a misspelled word
        for start_pos, length, word in block_data.misspellings:
            if start_pos <= pos_in_block < start_pos + length:
                return word

        return None

    def _replace_word(self, cursor, replacement):
        """
        Replace misspelled word with suggestion.

        Args:
            cursor: Text cursor positioned at word
            replacement: Replacement text
        """
        # Begin edit block to make it undoable as single operation
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.insertText(replacement)
        cursor.endEditBlock()

    def _add_to_dictionary(self, word):
        """
        Add word to personal dictionary.

        Args:
            word: Word to add
        """
        if self._highlighter:
            self._highlighter.add_to_dictionary(word)
