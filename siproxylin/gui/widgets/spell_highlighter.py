"""
Spell checking highlighter for Siproxylin.

Uses PyEnchant to highlight misspelled words with red wavy underlines.
"""

import logging
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QSyntaxHighlighter, QTextCharFormat, QColor,
    QTextBlockUserData
)

try:
    import enchant
    ENCHANT_AVAILABLE = True
except ImportError:
    ENCHANT_AVAILABLE = False

logger = logging.getLogger('siproxylin.spell_highlighter')


class SpellingBlockData(QTextBlockUserData):
    """Store misspelling positions for a text block."""

    def __init__(self):
        super().__init__()
        self.misspellings = []  # List of (start_pos, length, word) tuples


class EnchantHighlighter(QSyntaxHighlighter):
    """
    Syntax highlighter that underlines misspelled words using PyEnchant.

    Based on proven Qt + PyEnchant integration patterns.
    """

    def __init__(self, document, language='en_US'):
        """
        Initialize highlighter.

        Args:
            document: QTextDocument to highlight
            language: Language code for dictionary (default: en_US)
        """
        super().__init__(document)

        self.dict = None
        self.language = language
        self._spell_format = QTextCharFormat()
        self._spell_format.setUnderlineColor(QColor(Qt.red))
        self._spell_format.setUnderlineStyle(QTextCharFormat.WaveUnderline)

        if ENCHANT_AVAILABLE:
            try:
                # Try to load dictionary
                self.dict = enchant.Dict(language)
                logger.debug(f"Spell checker initialized with language: {language}")
            except enchant.errors.DictNotFoundError:
                logger.warning(f"Dictionary not found for language '{language}', spell checking disabled")
                logger.info("Install language dictionaries: apt install aspell aspell-en aspell-de ...")
            except Exception as e:
                logger.error(f"Failed to initialize spell checker: {e}")
        else:
            logger.warning("PyEnchant not available, spell checking disabled")
            logger.info("Install: pip install pyenchant && apt install aspell aspell-en")

    def is_available(self) -> bool:
        """Check if spell checking is available."""
        return self.dict is not None

    def set_language(self, language: str):
        """
        Change dictionary language.

        Args:
            language: Language code (e.g., 'en_US', 'de_DE', 'fr_FR')
        """
        if not ENCHANT_AVAILABLE:
            logger.warning("Cannot set language: PyEnchant not available")
            return

        try:
            self.dict = enchant.Dict(language)
            self.language = language
            logger.debug(f"Spell checker language changed to: {language}")
            # Re-highlight document
            self.rehighlight()
        except enchant.errors.DictNotFoundError:
            logger.warning(f"Dictionary not found for language '{language}'")
        except Exception as e:
            logger.error(f"Failed to set language '{language}': {e}")

    def get_available_languages(self):
        """Get list of available dictionary languages."""
        if not ENCHANT_AVAILABLE:
            return []
        try:
            return enchant.list_languages()
        except Exception as e:
            logger.error(f"Failed to get language list: {e}")
            return []

    def highlightBlock(self, text):
        """
        Highlight misspelled words in a text block.

        Args:
            text: Text block to highlight
        """
        if not self.dict or not text:
            return

        # Create block data to store misspellings
        block_data = SpellingBlockData()

        # Simple word tokenization (split on whitespace and common punctuation)
        # This is basic but works well for chat messages
        import re
        # Match words (alphanumeric + apostrophes for contractions)
        word_pattern = re.compile(r"\b[a-zA-Z']+\b")

        for match in word_pattern.finditer(text):
            word = match.group()
            start_pos = match.start()
            length = len(word)

            # Skip single-letter words and words with numbers
            if length <= 1:
                continue

            # Check spelling
            try:
                if not self.dict.check(word):
                    # Misspelled - apply red wavy underline
                    self.setFormat(start_pos, length, self._spell_format)
                    # Store for context menu
                    block_data.misspellings.append((start_pos, length, word))
            except Exception as e:
                # Ignore errors for individual words
                logger.debug(f"Error checking word '{word}': {e}")

        # Store block data
        self.setCurrentBlockUserData(block_data)

    def suggest(self, word: str, max_suggestions: int = 10):
        """
        Get spelling suggestions for a misspelled word.

        Args:
            word: Misspelled word
            max_suggestions: Maximum number of suggestions to return

        Returns:
            List of suggested corrections
        """
        if not self.dict:
            return []

        try:
            suggestions = self.dict.suggest(word)
            return suggestions[:max_suggestions]
        except Exception as e:
            logger.error(f"Error getting suggestions for '{word}': {e}")
            return []

    def add_to_dictionary(self, word: str):
        """
        Add word to personal dictionary.

        Args:
            word: Word to add
        """
        if not self.dict:
            return

        try:
            self.dict.add(word)
            logger.debug(f"Added '{word}' to dictionary")
            # Re-highlight to remove underlines
            self.rehighlight()
        except Exception as e:
            logger.error(f"Failed to add '{word}' to dictionary: {e}")
