"""
Message actions manager for chat view.

Handles message sending, editing, replying, and formatting operations.
"""

import logging
from PySide6.QtGui import QTextCursor


logger = logging.getLogger('siproxylin.chat_view.message_actions')


class MessageActionsManager:
    """Manager for message sending, editing, and reply operations."""

    def __init__(self, parent_widget, db, account_manager, input_field, header):
        """
        Initialize message actions manager.

        Args:
            parent_widget: Parent ChatViewWidget (for signals and state)
            db: Database instance
            account_manager: AccountManager instance
            input_field: MessageInputField widget
            header: ChatHeaderWidget instance
        """
        self.parent = parent_widget
        self.db = db
        self.account_manager = account_manager
        self.input_field = input_field
        self.header = header

        # Track last sent message for editing (per conversation)
        # Format: {(account_id, jid): {'message_id': str, 'body': str, 'encrypted': bool}}
        self.last_sent_messages = {}

        # Track if we're currently editing a message
        self.editing_message_id = None
        self.editing_encrypted = False

        # Track if we're currently composing a reply
        self.replying_to_message_id = None
        self.replying_to_body = None

    def on_send_clicked(self, current_account_id, current_jid):
        """
        Handle Send button click (or Enter key) - sends new message, reply, or saves edit.

        Args:
            current_account_id: Current account ID
            current_jid: Current contact/room JID

        Returns:
            bool: True if message was sent/saved, False otherwise
        """
        message = self.input_field.toPlainText().strip()

        if not current_account_id or not current_jid:
            logger.warning("Cannot send: no active conversation")
            return False

        # Check if we're in reply mode
        if self.replying_to_message_id:
            if not message:
                logger.warning("Cannot send reply: message is empty")
                return False

            # Extract reply body (everything after the quoted text)
            # The quoted text ends with "\n", so we split on that
            lines = self.input_field.toPlainText().split('\n')

            # Find where quoted text ends (lines starting with ">")
            # Must check for ">" (any quote level), not just "> " (first level)
            reply_start_idx = 0
            for i, line in enumerate(lines):
                if not line.startswith('>'):
                    reply_start_idx = i
                    break

            # Get just the reply text (skip quoted lines)
            reply_body = '\n'.join(lines[reply_start_idx:]).strip()

            if not reply_body:
                logger.warning("Cannot send reply: reply body is empty")
                return False

            logger.info(f"Sending reply to message {self.replying_to_message_id}: {reply_body[:50]}...")

            encrypted = self.header.header_encryption_button.isChecked()

            # Emit reply signal
            self.parent.send_reply.emit(
                current_account_id,
                current_jid,
                self.replying_to_message_id,
                reply_body,
                self.replying_to_body,  # fallback_body (original quoted text)
                encrypted
            )

            # Clear input and exit reply mode
            self.input_field.clear()
            self.cancel_reply()

            # Send 'active' state and reset chat state (XEP-0085)
            self.input_field.reset_chat_state()
            self.parent._send_active_state()
            return True

        # Check if we're in editing mode
        if self.editing_message_id:
            if not message:
                logger.warning("Cannot save edit: message is empty")
                return False

            logger.info(f"Saving edit for message {self.editing_message_id}: {message[:50]}...")

            # Emit edit signal
            self.parent.edit_message.emit(
                current_account_id,
                current_jid,
                self.editing_message_id,
                message,
                self.editing_encrypted
            )

            # Clear input and exit editing mode
            self.input_field.clear()
            self.cancel_editing()

            # Send 'active' state and reset chat state (XEP-0085)
            self.input_field.reset_chat_state()
            self.parent._send_active_state()
            return True

        encrypted = self.header.header_encryption_button.isChecked()

        # Check if we're sending a file
        if self.parent.selected_file_path:
            logger.info(f"Sending file to {current_jid}: {self.parent.selected_file_path} (encrypted={encrypted})")

            # Emit signal for main window to handle file sending
            self.parent.send_file.emit(current_account_id, current_jid, self.parent.selected_file_path, encrypted)

            # Clear file selection (file is already in permanent location, no cleanup needed)
            self.parent._clear_file_selection()

            # Also clear text if any (though typically not used with file)
            self.input_field.clear()

            # Send 'active' state and reset chat state (XEP-0085)
            self.input_field.reset_chat_state()
            self.parent._send_active_state()
            return True
        elif message:
            # Regular text message
            logger.info(f"Sending message to {current_jid}: {message[:50]}... (encrypted={encrypted})")

            # Emit signal for main window to handle
            self.parent.send_message.emit(current_account_id, current_jid, message, encrypted)

            # Clear input field
            self.input_field.clear()

            # Send 'active' state and reset chat state (XEP-0085)
            self.input_field.reset_chat_state()
            self.parent._send_active_state()
            return True
        else:
            # Nothing to send
            return False

    def track_sent_message(self, account_id, jid, message_id: str, body: str, encrypted: bool):
        """
        Track a sent message for potential editing.
        Call this after successfully sending a message.

        Args:
            account_id: Account ID
            jid: Contact/room JID
            message_id: XMPP message ID (stanza-id, origin-id, or server-id)
            body: Message text
            encrypted: Whether message was OMEMO encrypted
        """
        if not account_id or not jid:
            return

        key = (account_id, jid)
        self.last_sent_messages[key] = {
            'message_id': message_id,
            'body': body,
            'encrypted': encrypted
        }
        logger.debug(f"Tracked sent message {message_id} for editing (encrypted={encrypted})")

    def load_last_message_for_editing(self, current_account_id, current_jid):
        """
        Load last sent message into input field for editing (arrow-up handler).

        Args:
            current_account_id: Current account ID
            current_jid: Current contact/room JID
        """
        if not current_account_id or not current_jid:
            return

        key = (current_account_id, current_jid)
        last_msg = self.last_sent_messages.get(key)

        if not last_msg:
            logger.debug("No last sent message to edit")
            return

        # Enter editing mode
        self.editing_message_id = last_msg['message_id']
        self.editing_encrypted = last_msg['encrypted']

        # Load message into input field
        self.input_field.setPlainText(last_msg['body'])

        # Move cursor to end of text
        cursor = self.input_field.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.input_field.setTextCursor(cursor)

        # Update UI to show editing mode
        self.input_field.setPlaceholderText(f"✏️ Editing message (Esc to cancel)...")
        self.parent.send_button.setText("Save")

        # Set encryption button to match original message (and disable toggling)
        self.header.header_encryption_button.setChecked(self.editing_encrypted)
        # Encryption button in header is always enabled

        logger.info(f"Loaded message {self.editing_message_id} for editing")

    def cancel_editing(self):
        """Cancel editing mode and restore normal send mode."""
        self.editing_message_id = None
        self.editing_encrypted = False

        # Clear input
        self.input_field.clear()

        # Restore UI with shield indicator
        self.parent._update_input_placeholder()
        self.parent.send_button.setText("Send")
        # Encryption button in header is always enabled

        logger.debug("Cancelled editing mode")

    def start_reply(self, message_id: str, quoted_body: str):
        """
        Start composing a reply to a message.

        Args:
            message_id: XMPP message ID being replied to
            quoted_body: Text of the original message
        """
        # Cancel any pending edit
        if self.editing_message_id:
            self.cancel_editing()

        # Enter reply mode
        self.replying_to_message_id = message_id
        self.replying_to_body = quoted_body

        # Format quoted text with "> " prefix (each line)
        # For lines already starting with ">", add ">" (no space) to increase quote level
        # For other lines, add "> " (with space) to start quoting
        quoted_lines = quoted_body.split('\n')
        formatted_lines = []
        for line in quoted_lines:
            if line.startswith('>'):
                # Already quoted - add another level without space: ">> " not "> > "
                formatted_lines.append(f">{line}")
            else:
                # Not quoted yet - add first level with space
                formatted_lines.append(f"> {line}")
        quoted_text = '\n'.join(formatted_lines)

        # Load into input field with cursor after the quoted text + newline
        self.input_field.setPlainText(f"{quoted_text}\n")

        # Move cursor to end (after the newline)
        cursor = self.input_field.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.input_field.setTextCursor(cursor)

        # Update UI
        self.input_field.setPlaceholderText(f"💬 Replying (Esc to cancel)...")
        self.parent.send_button.setText("Reply")

        # Focus input field
        self.input_field.setFocus()

        logger.info(f"Started reply to message {message_id}")

    def cancel_reply(self):
        """Cancel reply mode and restore normal send mode."""
        self.replying_to_message_id = None
        self.replying_to_body = None

        # Clear input
        self.input_field.clear()

        # Restore UI with shield indicator
        self.parent._update_input_placeholder()
        self.parent.send_button.setText("Send")

        logger.debug("Cancelled reply mode")

    def edit_message_from_context(self, message_id: str, body: str, encrypted: bool):
        """
        Load a message for editing from right-click context menu.

        Args:
            message_id: XMPP message ID
            body: Message text
            encrypted: Whether message was encrypted
        """
        # Enter editing mode
        self.editing_message_id = message_id
        self.editing_encrypted = encrypted

        # Load message into input field
        self.input_field.setPlainText(body)

        # Move cursor to end of text
        cursor = self.input_field.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.input_field.setTextCursor(cursor)

        # Update UI to show editing mode
        self.input_field.setPlaceholderText(f"✏️ Editing message (Esc to cancel)...")
        self.parent.send_button.setText("Save")

        # Set encryption button to match original message (and disable toggling)
        self.header.header_encryption_button.setChecked(encrypted)
        # Encryption button in header is always enabled

        # Focus input field
        self.input_field.setFocus()

        logger.info(f"Loaded message {message_id} for editing from context menu")

    def format_selection(self, prefix: str, suffix: str):
        """
        Wrap selected text with formatting markers (e.g., **bold**, __italic__).

        Args:
            prefix: Text to insert before selection
            suffix: Text to insert after selection
        """
        cursor = self.input_field.textCursor()

        if cursor.hasSelection():
            # Wrap selected text
            selected_text = cursor.selectedText()
            formatted_text = f"{prefix}{selected_text}{suffix}"

            cursor.beginEditBlock()
            cursor.removeSelectedText()
            cursor.insertText(formatted_text)
            cursor.endEditBlock()

            # Select the formatted text (excluding markers) for easy re-edit
            cursor.setPosition(cursor.position() - len(suffix) - len(selected_text))
            cursor.setPosition(cursor.position() + len(selected_text), QTextCursor.MoveMode.KeepAnchor)
            self.input_field.setTextCursor(cursor)
        else:
            # No selection - insert markers and place cursor between them
            cursor.beginEditBlock()
            cursor.insertText(f"{prefix}{suffix}")
            cursor.endEditBlock()

            # Move cursor between markers
            cursor.setPosition(cursor.position() - len(suffix))
            self.input_field.setTextCursor(cursor)
