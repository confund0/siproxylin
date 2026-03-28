"""
MessageManager - Manages sending, editing, and replying to messages.

Extracted from MainWindow to improve maintainability.
"""

import logging
import asyncio
import uuid
import os
import mimetypes
from pathlib import Path
from datetime import datetime
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Slot, QTimer


logger = logging.getLogger('siproxylin.message_manager')


class MessageManager:
    """
    Manages outgoing message operations.

    Responsibilities:
    - Send text messages (regular and MUC)
    - Send file attachments
    - Edit messages (XEP-0308)
    - Send replies (XEP-0461)
    - Handle encryption (OMEMO)
    - Update database and UI
    """

    def __init__(self, main_window):
        """
        Initialize MessageManager.

        Args:
            main_window: MainWindow instance (for accessing widgets and services)
        """
        self.main_window = main_window
        self.account_manager = main_window.account_manager
        self.db = main_window.db
        self.chat_view = main_window.chat_view

        logger.debug("MessageManager initialized")

    def on_send_message(self, account_id: int, jid: str, message: str, encrypted: bool):
        """
        Handle send message signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            message: Message text
            encrypted: Whether to use OMEMO encryption
        """
        logger.info(f"Sending message to {jid} from account {account_id} (encrypted={encrypted})")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return

        # Send message asynchronously
        asyncio.create_task(self._send_message_async(account, jid, message, encrypted))

    async def _send_message_async(self, account, jid: str, message: str, encrypted: bool):
        """
        Send message asynchronously.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID (contact or MUC room)
            message: Message text
            encrypted: Whether to use OMEMO encryption
        """
        # Check if this is a MUC room (check bookmarks in DB)
        muc_check = self.db.fetchone("""
            SELECT 1 FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account.account_id, jid))

        is_muc = muc_check is not None
        message_type = 1 if is_muc else 0

        logger.debug(f"Sending to {jid}: is_muc={is_muc}, encrypted={encrypted}")

        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Store message in DB FIRST with temporary ID (so it shows in UI immediately)
        timestamp = int(datetime.now().timestamp())
        temp_origin_id = f"temp-{uuid.uuid4()}"

        conversation_id = self.db.get_or_create_conversation(account.account_id, jid_id, message_type)
        result = self.db.insert_message_atomic(
            account_id=account.account_id,
            counterpart_id=jid_id,
            conversation_id=conversation_id,
            direction=1,  # direction=1 (sent)
            msg_type=message_type,
            time=timestamp,
            local_time=timestamp,
            body=message,
            encryption=1 if encrypted else 0,
            marked=0,  # marked=0 (pending - will show hourglass)
            is_carbon=0,  # User's own new message, not a carbon
            origin_id=temp_origin_id  # Temporary ID until reflection comes back
        )

        db_message_id, _ = result  # Should never be (None, None) for new messages

        self.db.commit()

        logger.debug(f"Message stored in DB (id={db_message_id}, will attempt send)")

        # Refresh chat view immediately (shows message with hourglass)
        self.chat_view.refresh(send_markers=False)  # Just show our sent message, don't send markers

        # Check if account is connected before attempting to send
        if not account.is_connected():
            logger.debug(f"Account {account.account_id} not connected, message will be retried on reconnect")
            # Message stays marked=0 with hourglass, will be retried when connection restored
            return

        try:
            # Now try to send message
            if is_muc:
                if encrypted:
                    message_id = await account.client.send_encrypted_to_muc(jid, message)
                else:
                    message_id = await account.client.send_to_muc(jid, message)
            else:
                message_id = await account.send_message(jid, message, encrypted)

            # Update message with real origin_id
            self.db.execute("""
                UPDATE message SET origin_id = ? WHERE id = ?
            """, (message_id, db_message_id))
            self.db.commit()

            logger.debug(f"Message sent successfully (origin_id: {message_id})")

            # Track sent message for editing (arrow-up key)
            self.chat_view.track_sent_message(message_id, message, encrypted)

            # Server ACK will update marked=1 via receipt_handler

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            logger.error(f"Message remains in DB (id={db_message_id}) with marked=0 for retry")
            import traceback
            logger.error(traceback.format_exc())
            # Message stays marked=0 and will be retried on reconnect

    @Slot(int, str, str, bool)
    def on_send_file(self, account_id: int, jid: str, file_path: str, encrypted: bool):
        """
        Handle send file signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            file_path: Path to file to send
            encrypted: Whether to use OMEMO encryption
        """
        logger.debug(f"Sending file to {jid} from account {account_id}: {file_path} (encrypted={encrypted})")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            QMessageBox.critical(self.main_window, "Error", f"Account {account_id} not found")
            return

        # Check if account is connected
        if not account.is_connected():
            logger.error(f"Account {account_id} is not connected")
            QMessageBox.warning(self.main_window, "Not Connected", "Account is not connected. Please wait for connection.")
            return

        # Verify file exists
        if not os.path.isfile(file_path):
            logger.error(f"File not found: {file_path}")
            QMessageBox.critical(self.main_window, "Error", f"File not found: {file_path}")
            return

        # Send file asynchronously
        asyncio.create_task(self._send_file_async(account, jid, file_path, encrypted))

    async def _send_file_async(self, account, jid: str, file_path: str, encrypted: bool):
        """
        Send file asynchronously.

        Pattern matches message sending: store in DB first, then send, then update state.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID
            file_path: Path to file to send
            encrypted: Whether to use OMEMO encryption
        """
        file = Path(file_path)
        filename = file.name
        file_size = file.stat().st_size

        # Check if this is a MUC room
        muc_check = self.db.fetchone("""
            SELECT 1 FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account.account_id, jid))

        is_muc = muc_check is not None
        message_type = 1 if is_muc else 0

        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Guess MIME type
        mime_type, _ = mimetypes.guess_type(filename)

        # Store file_transfer in DB FIRST with state=1 (uploading)
        timestamp = int(datetime.now().timestamp())

        # Generate temporary origin_id for deduplication (same pattern as text messages)
        # This allows us to deduplicate when the server reflects the message back
        temp_origin_id = f"temp-{uuid.uuid4()}"

        # Get or create conversation
        conversation_id = self.db.get_or_create_conversation(account.account_id, jid_id, message_type)

        # Insert file_transfer + content_item atomically
        file_transfer_id, content_item_id = self.db.insert_file_transfer_atomic(
            account_id=account.account_id,
            counterpart_id=jid_id,
            conversation_id=conversation_id,
            direction=1,  # direction=1 (sent)
            time=timestamp,
            local_time=timestamp,
            file_name=filename,
            path=str(file),  # Local path to file being sent
            mime_type=mime_type,
            size=file_size,
            state=1,  # state=1 (uploading)
            encryption=1 if encrypted else 0,
            provider=0,  # provider=0 (HTTP Upload)
            is_carbon=0,  # Not a carbon (original send)
            url=None,  # URL not known yet (will be set after upload)
            origin_id=temp_origin_id  # Temporary ID for deduplication
        )

        logger.debug(f"File transfer record created (id={file_transfer_id}, state=uploading)")

        # Refresh chat view immediately (shows file with uploading state)
        QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

        try:
            logger.debug(f"Starting file upload: {file_path} to {jid} (encrypted={encrypted})")

            # Use appropriate DrunkXMPP method based on recipient type and encryption
            # These methods return the message_id (origin_id) for deduplication
            message_id = None
            if is_muc:
                if encrypted:
                    message_id = await account.client.send_encrypted_attachment_to_muc(jid, file_path)
                else:
                    message_id = await account.client.send_attachment_to_muc(jid, file_path)
            else:
                if encrypted:
                    message_id = await account.client.send_encrypted_file(jid, file_path)
                else:
                    message_id = await account.client.send_attachment_to_user(jid, file_path)

            # Update file_transfer with real origin_id and mark as complete
            # This allows duplicate detection when server reflects the message back
            self.db.execute("""
                UPDATE file_transfer SET state = ?, origin_id = ? WHERE id = ?
            """, (2, message_id, file_transfer_id))  # state=2 (complete)
            self.db.commit()

            logger.debug(f"✓ File '{filename}' sent to {jid} (id={file_transfer_id}, origin_id={message_id})")

            # Refresh to update state indicator
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send file: {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

            # Update file_transfer state to failed
            self.db.execute("""
                UPDATE file_transfer SET state = ? WHERE id = ?
            """, (3, file_transfer_id))  # state=3 (failed)
            self.db.commit()

            # Refresh to show error state
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

            # Show error dialog safely using QTimer to call from main thread
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self.main_window,
                "File Send Failed",
                f"Failed to send '{filename}':\n\n{error_msg}"
            ))

    @Slot(int, str, str, str, bool)
    def on_edit_message(self, account_id: int, jid: str, message_id: str, new_body: str, encrypted: bool):
        """
        Handle edit message signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            message_id: XMPP message ID to edit
            new_body: New message text
            encrypted: Whether message was encrypted (must match original)
        """
        logger.debug(f"Editing message {message_id} to {jid} from account {account_id}")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return

        # Check if account is connected
        if not account.is_connected():
            logger.error(f"Account {account_id} is not connected")
            QMessageBox.warning(self.main_window, "Not Connected", "Cannot edit message: account is not connected.")
            return

        # Send edit asynchronously
        asyncio.create_task(self._edit_message_async(account, jid, message_id, new_body, encrypted))

    async def _edit_message_async(self, account, jid: str, message_id: str, new_body: str, encrypted: bool):
        """
        Edit message asynchronously using XEP-0308.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID
            message_id: XMPP message ID to edit
            new_body: New message text
            encrypted: Whether message was encrypted
        """
        try:
            # Send message correction via DrunkXMPP
            await account.client.edit_message(jid, message_id, new_body, encrypt=encrypted)

            logger.debug(f"Message {message_id} edited successfully")

            # Update message in database
            self.db.execute("""
                UPDATE message
                SET body = ?
                WHERE (message_id = ? OR origin_id = ? OR stanza_id = ?)
                AND account_id = ?
            """, (new_body, message_id, message_id, message_id, account.account_id))
            self.db.commit()

            # Refresh chat view to show edited message
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

            # Update tracked message body for future edits
            self.chat_view.track_sent_message(message_id, new_body, encrypted)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to edit message: {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

            # Show error dialog safely using QTimer
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self.main_window,
                "Edit Failed",
                f"Failed to edit message:\n\n{error_msg}"
            ))

    def on_send_reply(self, account_id: int, jid: str, reply_to_id: str, reply_body: str, fallback_body: str, encrypted: bool):
        """
        Handle send reply signal from chat view.

        Args:
            account_id: Account ID
            jid: Recipient JID
            reply_to_id: XMPP message ID being replied to
            reply_body: Reply text (without quoted text)
            fallback_body: Original quoted message text for fallback
            encrypted: Whether to encrypt the reply
        """
        logger.debug(f"Sending reply to message {reply_to_id} in {jid} from account {account_id}")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return

        # Check if account is connected
        if not account.is_connected():
            logger.error(f"Account {account_id} is not connected")
            QMessageBox.warning(self.main_window, "Not Connected", "Cannot send reply: account is not connected.")
            return

        # Send reply asynchronously
        asyncio.create_task(self._send_reply_async(account, jid, reply_to_id, reply_body, fallback_body, encrypted))

    async def _send_reply_async(self, account, jid: str, reply_to_id: str, reply_body: str, fallback_body: str, encrypted: bool):
        """
        Send reply asynchronously using XEP-0461.

        Args:
            account: XMPPAccount instance
            jid: Recipient JID
            reply_to_id: XMPP message ID being replied to
            reply_body: Reply text
            fallback_body: Original quoted message text
            encrypted: Whether to encrypt the reply
        """
        # Check if this is a MUC room (check bookmarks in DB)
        muc_check = self.db.fetchone("""
            SELECT 1 FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?
        """, (account.account_id, jid))

        is_muc = muc_check is not None
        message_type = 1 if is_muc else 0

        # Get or create JID entry
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']
        else:
            cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (jid,))
            jid_id = cursor.lastrowid

        # Build full message body (quoted + reply) using shared formatting function
        from drunk_xmpp import xep_0428

        fallback_markers = None
        if fallback_body:
            # Use shared function (single source of truth for XEP-0461 formatting)
            full_body, fallback_marker = xep_0428.format_reply_with_fallback(reply_body, fallback_body)
            fallback_markers = [fallback_marker.to_dict()]
        else:
            full_body = reply_body

        # Store message in DB FIRST (optimistic - shows in UI immediately)
        timestamp = int(datetime.now().timestamp())
        temp_origin_id = f"temp-{uuid.uuid4()}"

        conversation_id = self.db.get_or_create_conversation(account.account_id, jid_id, message_type)
        result = self.db.insert_message_atomic(
            account_id=account.account_id,
            counterpart_id=jid_id,
            conversation_id=conversation_id,
            direction=1,  # direction=1 (sent)
            msg_type=message_type,
            time=timestamp,
            local_time=timestamp,
            body=full_body,
            encryption=1 if encrypted else 0,
            marked=0,  # marked=0 (pending - will show hourglass)
            is_carbon=0,  # User's own new message, not a carbon
            origin_id=temp_origin_id,  # Temporary ID until reflection comes back
            reply_to_id=reply_to_id,
            reply_to_jid=jid,
            fallbacks=fallback_markers  # XEP-0428 markers for stripping quoted text
        )

        db_message_id, _ = result  # Should never be (None, None) for new messages

        self.db.commit()

        logger.debug(f"Reply stored in DB (id={db_message_id}, will attempt send)")

        # Refresh chat view immediately (shows reply with hourglass)
        QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

        # Check if account is connected before attempting to send
        if not account.is_connected():
            logger.debug(f"Account {account.account_id} not connected, reply will be retried on reconnect")
            return

        try:
            # Send reply via DrunkXMPP (XEP-0461)
            message_id = await account.client.send_reply(jid, reply_to_id, reply_body, fallback_body=fallback_body, encrypt=encrypted)

            logger.debug(f"Reply sent successfully to {jid} (message_id={message_id})")

            # Update message with real origin_id
            self.db.execute("""
                UPDATE message SET origin_id = ? WHERE id = ?
            """, (message_id, db_message_id))
            self.db.commit()

            # Track this message for future edits
            self.chat_view.track_sent_message(message_id, full_body, encrypted)

            # Server ACK will update marked=1, then delivery receipt will update to marked=2

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to send reply: {error_msg}")
            import traceback
            logger.error(traceback.format_exc())

            # Mark message as error (marked=8)
            self.db.execute("""
                UPDATE message SET marked = 8 WHERE id = ?
            """, (db_message_id,))
            self.db.commit()

            # Refresh to show error state
            QTimer.singleShot(0, lambda: self.chat_view.refresh(send_markers=False))

            # Show error dialog safely using QTimer
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                self.main_window,
                "Reply Failed",
                f"Failed to send reply:\n\n{error_msg}"
            ))
