"""
Context menu manager for Siproxylin chat view.

Handles right-click menus for messages and input field.
"""

import logging
from PySide6.QtWidgets import QMenu, QFileDialog, QApplication
from PySide6.QtGui import QAction, QTextCursor

from ...widgets.message_delegate import MessageBubbleDelegate
from ...widgets.spell_highlighter import SpellingBlockData
from ...dialogs.message_info_dialog import show_message_info_dialog
from ...dialogs.file_properties_dialog import show_file_properties_dialog
from ...dialogs.emoji_picker_dialog import show_emoji_picker_dialog


logger = logging.getLogger('siproxylin.chat_view.context_menus')


class ContextMenuManager:
    """
    Manages context menus for chat view.

    Handles:
    - Message context menu (Reply, React, Edit, Info, Save As, Properties)
    - Input field context menu (Spell check suggestions, Cut/Copy/Paste)
    """

    def __init__(self, parent_widget, db, account_manager, spell_check_manager):
        """
        Initialize context menu manager.

        Args:
            parent_widget: Parent ChatViewWidget (for callbacks and state)
            db: Database connection
            account_manager: Account manager instance
            spell_check_manager: Spell check manager instance
        """
        self.parent = parent_widget
        self.db = db
        self.account_manager = account_manager
        self.spell_check_manager = spell_check_manager

    def show_message_context_menu(self, position, message_area, current_account_id, current_jid, last_sent_messages):
        """
        Show context menu for message items.

        Args:
            position: Click position
            message_area: QListView containing messages
            current_account_id: Current account ID
            current_jid: Current conversation JID
            last_sent_messages: Dict of last sent messages (for Edit option)
        """
        # Get item at position
        index = message_area.indexAt(position)
        if not index.isValid():
            return

        # Check direction (0=received, 1=sent)
        direction = index.data(MessageBubbleDelegate.ROLE_DIRECTION)
        is_carbon = index.data(MessageBubbleDelegate.ROLE_IS_CARBON)

        # Check if this is a file attachment or text message
        file_path = index.data(MessageBubbleDelegate.ROLE_FILE_PATH)
        body = index.data(MessageBubbleDelegate.ROLE_BODY)
        message_id = index.data(MessageBubbleDelegate.ROLE_MESSAGE_ID)

        # Create menu
        menu = QMenu(self.parent)

        # For all text messages: add Reply option
        if body and message_id:
            reply_action = QAction("Reply", self.parent)
            reply_action.triggered.connect(lambda: self.parent._start_reply(message_id, body))
            menu.addAction(reply_action)

        # For all messages with message_id: add React option
        if message_id:
            react_action = QAction("React...", self.parent)
            react_action.triggered.connect(lambda: self._show_emoji_picker(message_id, current_account_id, current_jid))
            menu.addAction(react_action)

        # For all messages: add Copy option
        if body or file_path:
            copy_action = QAction("Copy Message", self.parent)
            if body:
                # Copy message text
                copy_action.triggered.connect(lambda: self._copy_to_clipboard(body))
            elif file_path:
                # Copy file name
                file_name = index.data(MessageBubbleDelegate.ROLE_FILE_NAME) or "file"
                copy_action.triggered.connect(lambda: self._copy_to_clipboard(file_name))
            menu.addAction(copy_action)

        # For sent text messages: add Edit option ONLY for last sent message (not carbons)
        # Check if this is the last sent message we have tracked
        if direction == 1 and body and message_id and not is_carbon:
            encrypted = bool(index.data(MessageBubbleDelegate.ROLE_ENCRYPTED))

            # Only show Edit if this is the last message we sent in this conversation
            key = (current_account_id, current_jid)
            last_msg = last_sent_messages.get(key)

            if last_msg and last_msg['message_id'] == message_id:
                edit_action = QAction("Edit", self.parent)
                edit_action.triggered.connect(lambda: self.parent._edit_message_from_context(message_id, body, encrypted))
                menu.addAction(edit_action)

        # Info option for all messages
        if body or file_path:
            # Extract all data NOW before index becomes invalid
            content_item_id = index.data(MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID)

            # Query all IDs from database (origin_id, stanza_id, message_id)
            origin_id = None
            stanza_id = None
            db_message_id = None

            if content_item_id and self.db:
                if body:  # Text message
                    msg_row = self.db.fetchone("""
                        SELECT m.origin_id, m.stanza_id, m.message_id
                        FROM message m
                        JOIN content_item ci ON m.id = ci.foreign_id
                        WHERE ci.id = ? AND ci.content_type = 0
                    """, (content_item_id,))
                    if msg_row:
                        origin_id = msg_row['origin_id']
                        stanza_id = msg_row['stanza_id']
                        db_message_id = msg_row['message_id']
                elif file_path:  # File transfer
                    ft_row = self.db.fetchone("""
                        SELECT ft.origin_id, ft.stanza_id, ft.message_id
                        FROM file_transfer ft
                        JOIN content_item ci ON ft.id = ci.foreign_id
                        WHERE ci.id = ? AND ci.content_type = 2
                    """, (content_item_id,))
                    if ft_row:
                        origin_id = ft_row['origin_id']
                        stanza_id = ft_row['stanza_id']
                        db_message_id = ft_row['message_id']

            # Get raw Unix timestamp and format it with full locale date+time
            raw_timestamp = index.data(MessageBubbleDelegate.ROLE_TIMESTAMP_RAW)
            if raw_timestamp:
                from datetime import datetime
                from PySide6.QtCore import QLocale
                dt = datetime.fromtimestamp(raw_timestamp)
                locale = QLocale()
                # Full locale format: "Friday, 28 January 2026, 17:56:30"
                full_timestamp = locale.toString(dt, QLocale.LongFormat)
            else:
                full_timestamp = index.data(MessageBubbleDelegate.ROLE_TIMESTAMP) or ""

            info_data = {
                'direction': direction,
                'body': body,
                'timestamp': full_timestamp,
                'encrypted': index.data(MessageBubbleDelegate.ROLE_ENCRYPTED) or False,
                'marked': index.data(MessageBubbleDelegate.ROLE_MARKED) or 0,
                'msg_type': index.data(MessageBubbleDelegate.ROLE_TYPE) or 0,
                'is_carbon': is_carbon,
                'message_id': message_id,  # The "selected" ID used by UI
                'origin_id': origin_id,
                'stanza_id': stanza_id,
                'db_message_id': db_message_id,
                'content_item_id': content_item_id,
                'file_path': file_path,
                'file_name': index.data(MessageBubbleDelegate.ROLE_FILE_NAME),
                'mime_type': index.data(MessageBubbleDelegate.ROLE_MIME_TYPE)
            }
            info_action = QAction("Info", self.parent)
            info_action.triggered.connect(lambda: self._show_message_info(info_data, current_account_id))
            menu.addAction(info_action)

        # For file attachments: add file-specific options
        if file_path:
            file_name = index.data(MessageBubbleDelegate.ROLE_FILE_NAME) or "file"
            mime_type = index.data(MessageBubbleDelegate.ROLE_MIME_TYPE) or ""
            file_size = index.data(MessageBubbleDelegate.ROLE_FILE_SIZE) or 0

            # Save As... action
            save_action = QAction("Save As...", self.parent)
            save_action.triggered.connect(lambda: self._save_file_as(file_path, file_name))
            menu.addAction(save_action)

            # Properties action
            properties_action = QAction("Properties", self.parent)
            properties_action.triggered.connect(lambda: self._show_file_properties(file_path, file_name, mime_type, file_size))
            menu.addAction(properties_action)

        # Only show menu if we have actions
        if menu.actions():
            menu.exec_(message_area.viewport().mapToGlobal(position))

    def show_input_context_menu(self, position, input_field, spell_highlighter):
        """
        Show context menu for input field with spelling suggestions.

        Args:
            position: Click position
            input_field: MessageInputField widget
            spell_highlighter: EnchantHighlighter instance
        """
        # Get cursor at click position
        cursor = input_field.cursorForPosition(position)

        # Get position in block BEFORE selecting (this is the actual click position)
        pos_in_block = cursor.positionInBlock()
        block = cursor.block()

        # NOW select to get the word text
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()

        # Check if we have spell checking and if this is a misspelled word
        misspelled_word = None
        if spell_highlighter and spell_highlighter.is_available():
            # Get block data
            block_data = block.userData()

            if isinstance(block_data, SpellingBlockData):
                # Check if cursor is within a misspelled word
                for start_pos, length, misspelled in block_data.misspellings:
                    if start_pos <= pos_in_block < start_pos + length:
                        misspelled_word = misspelled
                        break

        # Create context menu
        menu = QMenu(self.parent)

        if misspelled_word:
            # Add spelling suggestions
            suggestions = spell_highlighter.suggest(misspelled_word, max_suggestions=10)

            if suggestions:
                # Add suggestion actions
                for suggestion in suggestions:
                    action = QAction(suggestion, self.parent)
                    # Store cursor in closure
                    action.triggered.connect(
                        lambda checked=False, s=suggestion, c=cursor: self.spell_check_manager.replace_word(c, s)
                    )
                    menu.addAction(action)
            else:
                # No suggestions available
                no_suggestions = QAction("(no suggestions)", self.parent)
                no_suggestions.setEnabled(False)
                menu.addAction(no_suggestions)

            menu.addSeparator()

            # Add to dictionary
            add_to_dict = QAction(f"Add '{misspelled_word}' to dictionary", self.parent)
            add_to_dict.triggered.connect(
                lambda: self.spell_check_manager.add_word_to_dictionary(misspelled_word)
            )
            menu.addAction(add_to_dict)

            menu.addSeparator()

        # Add standard editing actions
        text_cursor = input_field.textCursor()
        if text_cursor.hasSelection():
            cut_action = menu.addAction("Cut")
            cut_action.triggered.connect(input_field.cut)
            copy_action = menu.addAction("Copy")
            copy_action.triggered.connect(input_field.copy)

        paste_action = menu.addAction("Paste")
        paste_action.triggered.connect(input_field.paste)

        menu.addSeparator()

        select_all = menu.addAction("Select All")
        select_all.triggered.connect(input_field.selectAll)

        # Show menu at cursor position
        menu.exec_(input_field.mapToGlobal(position))

    def _save_file_as(self, source_path, default_name):
        """Open file save dialog and copy file from internal storage to chosen location."""
        import shutil
        from pathlib import Path

        # Use our timestamped internal filename instead of original random server name
        internal_filename = Path(source_path).name

        # Open file save dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self.parent,
            "Save File As",
            internal_filename,  # Use YYYY-mm-dd_HHMMSS.ext format
            "All Files (*)"
        )

        if file_path:
            try:
                # Copy file from internal storage to chosen location
                shutil.copy2(source_path, file_path)
                logger.info(f"File saved to: {file_path}")
            except Exception as e:
                logger.error(f"Failed to save file: {e}")
                # TODO: Show error dialog to user

    def _show_file_properties(self, file_path, file_name, mime_type, file_size):
        """Show file properties dialog."""
        show_file_properties_dialog(self.parent, file_path, file_name, mime_type, file_size)

    def _copy_to_clipboard(self, text):
        """Copy text to system clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        logger.debug(f"Copied to clipboard: {text[:50]}..." if len(text) > 50 else f"Copied to clipboard: {text}")

    def _show_message_info(self, info_data, current_account_id):
        """Show message info dialog with metadata and reactions table."""
        show_message_info_dialog(self.parent, info_data, self.db, current_account_id)

    def _show_emoji_picker(self, message_id, current_account_id, current_jid):
        """Show emoji picker dialog for reacting to a message."""
        show_emoji_picker_dialog(
            self.parent,
            message_id,
            self.account_manager,
            current_account_id,
            current_jid,
            self.parent  # Pass chat_view for immediate refresh
        )
