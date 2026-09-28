"""
Message list model for chat view using Qt's QAbstractListModel.

This model implements Qt's canFetchMore/fetchMore pattern for infinite scrolling,
replacing the previous QStandardItemModel approach with better performance.

Key improvements:
- Single JOIN query instead of N+1 queries
- Qt handles scroll detection automatically via canFetchMore/fetchMore
- One beginInsertRows() call instead of 100x insertRow()
"""

import logging
from datetime import datetime
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from ....db.database import get_db
from ...widgets.message_delegate import MessageBubbleDelegate
from .messages import parse_download_error


logger = logging.getLogger('siproxylin.chat_view.message_list_model')


class MessageListModel(QAbstractListModel):
    """
    List model for chat messages with infinite scroll support.

    Uses Qt's canFetchMore/fetchMore pattern to load messages on demand.
    Stores messages as a list of dicts internally, fetched via single JOIN query.
    """

    def __init__(self, db, account_manager, message_delegate=None, parent=None):
        """Initialize the model."""
        super().__init__(parent)

        # Dependencies
        self.db = db
        self.account_manager = account_manager
        self.message_delegate = message_delegate  # For file icon/size formatting

        # Conversation state
        self.current_account_id = None
        self.current_jid = None
        self.current_conversation_id = None
        self.current_is_muc = False

        # Message storage (list of dicts)
        self.messages = []  # Each dict contains all fields needed for display

        # Infinite scroll state
        self.oldest_loaded_time = None  # Timestamp of oldest loaded message
        self.is_loading_more = False     # Prevent concurrent loads
        self.has_more_messages = True    # Whether there are older messages to load

    def rowCount(self, parent=QModelIndex()):
        """Return number of messages in the model."""
        if parent.isValid():
            return 0
        return len(self.messages)

    def data(self, index, role=Qt.DisplayRole):
        """
        Return data for a given index and role.

        This is called by the delegate to render each message bubble.
        We return the appropriate field from the message dict based on the role.
        """
        if not index.isValid() or index.row() >= len(self.messages):
            return None

        msg = self.messages[index.row()]

        # Return data based on role
        if role == MessageBubbleDelegate.ROLE_DIRECTION:
            return msg.get('direction')
        elif role == MessageBubbleDelegate.ROLE_BODY:
            return msg.get('body')
        elif role == MessageBubbleDelegate.ROLE_TIMESTAMP:
            return msg.get('timestamp')
        elif role == MessageBubbleDelegate.ROLE_ENCRYPTED:
            return msg.get('encrypted')
        elif role == MessageBubbleDelegate.ROLE_MARKED:
            return msg.get('marked')
        elif role == MessageBubbleDelegate.ROLE_TYPE:
            return msg.get('msg_type')
        elif role == MessageBubbleDelegate.ROLE_NICKNAME:
            return msg.get('nickname')
        elif role == MessageBubbleDelegate.ROLE_FILE_PATH:
            return msg.get('file_path')
        elif role == MessageBubbleDelegate.ROLE_FILE_NAME:
            return msg.get('file_name')
        elif role == MessageBubbleDelegate.ROLE_MIME_TYPE:
            return msg.get('mime_type')
        elif role == MessageBubbleDelegate.ROLE_FILE_SIZE:
            return msg.get('file_size')
        elif role == MessageBubbleDelegate.ROLE_FILE_ICON:
            return msg.get('file_icon')
        elif role == MessageBubbleDelegate.ROLE_FILE_SIZE_TEXT:
            return msg.get('file_size_text')
        elif role == MessageBubbleDelegate.ROLE_IS_CARBON:
            return msg.get('is_carbon')
        elif role == MessageBubbleDelegate.ROLE_MESSAGE_ID:
            return msg.get('message_id')
        elif role == MessageBubbleDelegate.ROLE_QUOTED_BODY:
            return msg.get('quoted_body')
        elif role == MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID:
            return msg.get('content_item_id')
        elif role == MessageBubbleDelegate.ROLE_CALL_STATE:
            return msg.get('call_state')
        elif role == MessageBubbleDelegate.ROLE_CALL_DURATION:
            return msg.get('call_duration')
        elif role == MessageBubbleDelegate.ROLE_CALL_TYPE:
            return msg.get('call_type')
        elif role == MessageBubbleDelegate.ROLE_FILE_TRANSFER_ID:
            return msg.get('file_transfer_id')
        elif role == MessageBubbleDelegate.ROLE_FILE_STATE:
            return msg.get('file_state')
        elif role == MessageBubbleDelegate.ROLE_DOWNLOAD_ERROR:
            return msg.get('download_error')

        return None

    def canFetchMore(self, parent=QModelIndex()):
        """
        Return True if there are more messages to load.

        Qt calls this automatically when scrolling near the top.
        """
        if parent.isValid():
            return False

        return self.has_more_messages and not self.is_loading_more

    def fetchMore(self, parent=QModelIndex()):
        """
        Load more older messages when scrolling to top.

        Qt calls this automatically when canFetchMore() returns True
        and the user scrolls near the top of the list.
        """
        if parent.isValid() or not self.has_more_messages or self.is_loading_more:
            return

        logger.info(f"fetchMore() called - loading older messages (before {self.oldest_loaded_time})")
        self.is_loading_more = True

        try:
            # Load 100 messages older than oldest_loaded_time
            new_messages = self._fetch_messages(before_time=self.oldest_loaded_time)

            if new_messages:
                # Prepend messages at the top
                self.beginInsertRows(QModelIndex(), 0, len(new_messages) - 1)
                self.messages = new_messages + self.messages
                self.endInsertRows()

                # Update state
                self.oldest_loaded_time = new_messages[0]['time']

                if len(new_messages) < 100:
                    self.has_more_messages = False
                    logger.debug("No more messages to load (got < 100)")

                logger.info(f"Loaded {len(new_messages)} older messages. Total: {len(self.messages)}")
            else:
                self.has_more_messages = False
                logger.debug("No more messages to load (query returned 0)")

        except Exception as e:
            logger.error(f"Failed to fetch more messages: {e}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            self.is_loading_more = False

    def load_initial(self, account_id, jid, is_muc, conversation_id):
        """
        Load initial messages for a conversation (last 100).

        Args:
            account_id: Account ID
            jid: Contact/room JID
            is_muc: True if this is a MUC room
            conversation_id: Conversation ID
        """
        logger.info(f"load_initial() called for account={account_id}, jid={jid}, conversation_id={conversation_id}")

        # Update conversation state
        self.current_account_id = account_id
        self.current_jid = jid
        self.current_is_muc = is_muc
        self.current_conversation_id = conversation_id

        # Clear existing data
        self.clear()

        # Load last 100 messages
        new_messages = self._fetch_messages(before_time=None)

        if new_messages:
            self.beginInsertRows(QModelIndex(), 0, len(new_messages) - 1)
            self.messages = new_messages
            self.endInsertRows()

            # Set oldest loaded time
            self.oldest_loaded_time = new_messages[0]['time']

            if len(new_messages) < 100:
                self.has_more_messages = False
            else:
                self.has_more_messages = True

            logger.info(f"Loaded {len(new_messages)} initial messages")
        else:
            logger.debug("No messages found for this conversation")
            self.has_more_messages = False

    def clear(self):
        """Clear all messages from the model."""
        if self.messages:
            self.beginRemoveRows(QModelIndex(), 0, len(self.messages) - 1)
            self.messages = []
            self.endRemoveRows()

        # Reset state
        self.oldest_loaded_time = None
        self.has_more_messages = True
        self.is_loading_more = False

    def refresh(self):
        """
        Refresh the message list (reload last 100 messages).

        Called by polling timer when in live zone.
        This replaces the entire list with the latest 100 messages.
        """
        if not self.current_conversation_id:
            return

        logger.debug(f"refresh() called for conversation_id={self.current_conversation_id}")

        # Clear and reload
        self.clear()

        # Load last 100 messages
        new_messages = self._fetch_messages(before_time=None)

        if new_messages:
            self.beginInsertRows(QModelIndex(), 0, len(new_messages) - 1)
            self.messages = new_messages
            self.endInsertRows()

            # Update state
            self.oldest_loaded_time = new_messages[0]['time']

            if len(new_messages) < 100:
                self.has_more_messages = False
            else:
                self.has_more_messages = True

    def _fetch_messages(self, before_time=None):
        """
        Fetch messages from database using single JOIN query.

        Args:
            before_time: If None, load last 100 messages.
                        If set, load 100 messages older than this timestamp.

        Returns:
            List of message dicts (oldest first)
        """
        if not self.current_conversation_id:
            return []

        # Build query with single JOIN to get all data at once (no N+1!)
        if before_time is None:
            logger.info(f"SELECTING last 100 messages for conversation_id={self.current_conversation_id}")
        else:
            logger.info(f"SELECTING 100 earlier messages starting from time={before_time} (conversation_id={self.current_conversation_id})")

        # Single JOIN query to fetch ALL data for messages, file transfers, and calls
        # This replaces the N+1 query pattern (1 query + 100 individual queries)
        if before_time is None:
            # Initial load: last 100 content items
            rows = self.db.fetchall("""
                SELECT
                    ci.id AS ci_id,
                    ci.content_type,
                    ci.time,
                    -- Message fields
                    m.id AS msg_id,
                    m.body,
                    m.direction AS msg_direction,
                    m.encryption AS msg_encryption,
                    m.marked,
                    m.type AS msg_type,
                    m.counterpart_resource,
                    m.is_carbon AS msg_is_carbon,
                    m.message_id,
                    m.origin_id,
                    m.stanza_id,
                    j.bare_jid AS counterpart_jid,
                    quoted_m.body AS quoted_body,
                    -- File transfer fields
                    ft.id AS ft_id,
                    ft.direction AS ft_direction,
                    ft.file_name,
                    ft.path,
                    ft.mime_type,
                    ft.size,
                    ft.encryption AS ft_encryption,
                    ft.is_carbon AS ft_is_carbon,
                    ft.message_id AS ft_message_id,
                    ft.origin_id AS ft_origin_id,
                    ft.stanza_id AS ft_stanza_id,
                    ft.state AS ft_state,
                    ft.info AS ft_info,
                    ft.counterpart_resource AS ft_counterpart_resource,
                    -- Call fields
                    c.id AS call_id,
                    c.direction AS call_direction,
                    c.time AS call_time,
                    c.end_time AS call_end_time,
                    c.state AS call_state,
                    c.type AS call_type
                FROM content_item ci
                LEFT JOIN message m ON ci.content_type = 0 AND ci.foreign_id = m.id
                LEFT JOIN jid j ON m.counterpart_id = j.id
                LEFT JOIN reply r ON r.message_id = m.id
                LEFT JOIN message quoted_m ON r.quoted_message_id = quoted_m.id
                LEFT JOIN file_transfer ft ON ci.content_type = 2 AND ci.foreign_id = ft.id
                LEFT JOIN call c ON ci.content_type = 3 AND ci.foreign_id = c.id
                WHERE ci.conversation_id = ? AND ci.hide = 0
                ORDER BY ci.time DESC
                LIMIT 100
            """, (self.current_conversation_id,))
        else:
            # Load more: 100 items older than before_time
            rows = self.db.fetchall("""
                SELECT
                    ci.id AS ci_id,
                    ci.content_type,
                    ci.time,
                    -- Message fields
                    m.id AS msg_id,
                    m.body,
                    m.direction AS msg_direction,
                    m.encryption AS msg_encryption,
                    m.marked,
                    m.type AS msg_type,
                    m.counterpart_resource,
                    m.is_carbon AS msg_is_carbon,
                    m.message_id,
                    m.origin_id,
                    m.stanza_id,
                    j.bare_jid AS counterpart_jid,
                    quoted_m.body AS quoted_body,
                    -- File transfer fields
                    ft.id AS ft_id,
                    ft.direction AS ft_direction,
                    ft.file_name,
                    ft.path,
                    ft.mime_type,
                    ft.size,
                    ft.encryption AS ft_encryption,
                    ft.is_carbon AS ft_is_carbon,
                    ft.message_id AS ft_message_id,
                    ft.origin_id AS ft_origin_id,
                    ft.stanza_id AS ft_stanza_id,
                    ft.state AS ft_state,
                    ft.info AS ft_info,
                    ft.counterpart_resource AS ft_counterpart_resource,
                    -- Call fields
                    c.id AS call_id,
                    c.direction AS call_direction,
                    c.time AS call_time,
                    c.end_time AS call_end_time,
                    c.state AS call_state,
                    c.type AS call_type
                FROM content_item ci
                LEFT JOIN message m ON ci.content_type = 0 AND ci.foreign_id = m.id
                LEFT JOIN jid j ON m.counterpart_id = j.id
                LEFT JOIN reply r ON r.message_id = m.id
                LEFT JOIN message quoted_m ON r.quoted_message_id = quoted_m.id
                LEFT JOIN file_transfer ft ON ci.content_type = 2 AND ci.foreign_id = ft.id
                LEFT JOIN call c ON ci.content_type = 3 AND ci.foreign_id = c.id
                WHERE ci.conversation_id = ? AND ci.hide = 0 AND ci.time < ?
                ORDER BY ci.time DESC
                LIMIT 100
            """, (self.current_conversation_id, before_time))

        if not rows:
            return []

        # Convert rows to message dicts
        # Process each row based on content_type and format for delegate
        messages = []
        for row in rows:
            content_type = row['content_type']
            content_item_id = row['ci_id']
            time = row['time']

            if content_type == 0:
                # Message
                msg_dict = self._process_message_row(row, content_item_id)
                if msg_dict:
                    messages.append(msg_dict)

            elif content_type == 2:
                # File transfer
                ft_dict = self._process_file_transfer_row(row, content_item_id)
                if ft_dict:
                    messages.append(ft_dict)

            elif content_type == 3:
                # Call
                call_dict = self._process_call_row(row, content_item_id)
                if call_dict:
                    messages.append(call_dict)

        # Query returns newest-first (DESC order)
        # We need oldest-first for display (oldest at top, newest at bottom)
        # Reverse the list so index 0 = oldest, index 99 = newest
        messages.reverse()

        logger.info(f"Updating area message buffer with {len(messages)} messages (oldest-first)")
        return messages

    def _process_message_row(self, row, content_item_id):
        """Process a message row from JOIN query into message dict."""
        if not row['msg_id']:
            return None

        # Extract fields
        direction = row['msg_direction']
        body = row['body'] or ''
        timestamp = datetime.fromtimestamp(row['time']).strftime('%H:%M')
        encrypted = bool(row['msg_encryption'])
        marked = row['marked']
        msg_type = row['msg_type']
        nickname = row['counterpart_resource'] or ''
        is_carbon = bool(row['msg_is_carbon'])
        quoted_body = row['quoted_body'] or ''

        # Get message ID for reactions/editing
        # XEP-0444: MUC reactions MUST use stanza_id (server-assigned)
        # 1-1 chats prefer message_id or origin_id (client-assigned)
        if msg_type == 1:  # MUC
            message_id = row['stanza_id'] or row['origin_id'] or row['message_id']
        else:  # 1-1 chat
            message_id = row['message_id'] or row['origin_id'] or row['stanza_id']

        return {
            'content_item_id': content_item_id,
            'time': row['time'],
            'direction': direction,
            'body': body,
            'timestamp': timestamp,
            'encrypted': encrypted,
            'marked': marked,
            'msg_type': msg_type,
            'nickname': nickname,
            'is_carbon': is_carbon,
            'message_id': message_id,
            'quoted_body': quoted_body,
            # File transfer fields (None for messages)
            'file_path': None,
            'file_name': None,
            'mime_type': None,
            'file_size': None,
            'file_icon': None,
            'file_size_text': None,
            # Call fields (None for messages)
            'call_state': None,
            'call_duration': None,
            'call_type': None,
        }

    def _process_file_transfer_row(self, row, content_item_id):
        """Process a file transfer row from JOIN query into message dict."""
        if not row['ft_id']:
            return None

        # Extract fields
        direction = row['ft_direction']
        file_path = row['path']
        file_name = row['file_name'] or 'file'
        mime_type = row['mime_type'] or ''
        file_size = row['size'] or 0
        timestamp = datetime.fromtimestamp(row['time']).strftime('%H:%M')
        encrypted = bool(row['ft_encryption'])
        is_carbon = bool(row['ft_is_carbon'])

        # Get message ID for reactions
        # XEP-0444: MUC reactions MUST use stanza_id (server-assigned)
        # 1-1 chats prefer message_id or origin_id (client-assigned)
        if self.current_is_muc:  # MUC
            message_id = row['ft_stanza_id'] or row['ft_origin_id'] or row['ft_message_id']
        else:  # 1-1 chat
            message_id = row['ft_message_id'] or row['ft_origin_id'] or row['ft_stanza_id']

        # Pre-compute display values (calculate once, not on every paint!)
        if self.message_delegate:
            file_icon = self.message_delegate._get_file_icon(mime_type)
            file_size_text = self.message_delegate._format_file_size(file_size) if file_size else "Unknown size"
        else:
            file_icon = None
            file_size_text = None

        return {
            'content_item_id': content_item_id,
            'time': row['time'],
            'direction': direction,
            'timestamp': timestamp,
            'encrypted': encrypted,
            'is_carbon': is_carbon,
            'message_id': message_id,
            # File transfer specific
            'file_path': file_path,
            'file_name': file_name,
            'mime_type': mime_type,
            'file_size': file_size,
            'file_icon': file_icon,
            'file_size_text': file_size_text,
            'file_transfer_id': row['ft_id'],
            'file_state': row['ft_state'],
            'download_error': parse_download_error(row['ft_info']),
            # Message fields (defaults for files)
            'body': None,
            'marked': 0,  # Files don't have markers
            'msg_type': 0,
            'nickname': '',
            'quoted_body': '',
            # Call fields (None for files)
            'call_state': None,
            'call_duration': None,
            'call_type': None,
        }

    def _process_call_row(self, row, content_item_id):
        """Process a call row from JOIN query into message dict."""
        if not row['call_id']:
            return None

        # Extract fields
        direction = row['call_direction']
        timestamp = datetime.fromtimestamp(row['time']).strftime('%H:%M')
        call_state = row['call_state']
        call_type = row['call_type']

        # Calculate duration
        if row['call_end_time']:
            call_duration = row['call_end_time'] - row['time']
        else:
            call_duration = None  # Ongoing or no answer

        return {
            'content_item_id': content_item_id,
            'time': row['time'],
            'direction': direction,
            'timestamp': timestamp,
            # Call specific
            'call_state': call_state,
            'call_duration': call_duration,
            'call_type': call_type,
            # Message fields (None for calls)
            'body': None,
            'encrypted': False,
            'marked': 0,
            'msg_type': 0,
            'nickname': '',
            'is_carbon': False,
            'message_id': None,
            'quoted_body': '',
            # File transfer fields (None for calls)
            'file_path': None,
            'file_name': None,
            'mime_type': None,
            'file_size': None,
            'file_icon': None,
            'file_size_text': None,
        }
