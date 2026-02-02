"""
Message display widget for Siproxylin.

Displays message history with timestamps, encryption indicators, file transfers, and calls.
"""

import logging
import time
from datetime import datetime, timedelta
from PySide6.QtWidgets import QListView, QFrame, QApplication
from PySide6.QtCore import Qt, QTimer, QLocale, QObject
from PySide6.QtGui import QStandardItemModel, QStandardItem

from ....db.database import get_db
from ...widgets.message_delegate import MessageBubbleDelegate
from ....styles.theme_manager import get_theme_manager


logger = logging.getLogger('siproxylin.chat_view.messages')


def get_day_separator_text(timestamp):
    """
    Get day separator text for a given timestamp.
    Returns: "Today", "Yesterday", or locale-formatted date like "Fri, 29 Jan"
    """
    msg_date = datetime.fromtimestamp(timestamp).date()
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if msg_date == today:
        return "Today"
    elif msg_date == yesterday:
        return "Yesterday"
    else:
        # Use locale format: "Fri, 29 Jan" or locale equivalent
        dt = datetime.fromtimestamp(timestamp)
        locale = QLocale()
        # Format: short day name, day number, short month name
        return locale.toString(dt, "ddd, d MMM")


def get_bubble_timestamp(timestamp):
    """
    Get timestamp for message bubble.
    Returns: "Now", "7 min ago" (< 15 min), or "17:24" (HH:MM)

    Day context comes from day separators, so bubbles only show time.
    """
    msg_dt = datetime.fromtimestamp(timestamp)
    now = datetime.now()
    delta = now - msg_dt

    # Less than 1 minute: "Now"
    if delta < timedelta(minutes=1):
        return "Now"

    # 1-15 minutes: "7 min ago"
    if delta < timedelta(minutes=15):
        minutes = int(delta.total_seconds() / 60)
        return f"{minutes} min ago"

    # Everything else: "HH:MM"
    return msg_dt.strftime('%H:%M')


class MessageDisplayWidget(QObject):
    """
    Message display manager for chat view.

    Manages:
    - Message area (QListView)
    - Message model (QStandardItemModel)
    - Message delegate (MessageBubbleDelegate)
    - Message loading and rendering

    This is a manager class, not a widget. The parent widget
    is responsible for adding message_area to its layout.

    Note: Inherits from QObject to support event filtering for search highlight management.
    Alternative design: Separate SearchEventFilter(QObject) class with a reference to this widget.
    Current approach is simpler and keeps related functionality together.
    """

    def __init__(self, db, account_manager, parent=None):
        """Initialize message display with dependencies."""
        super().__init__(parent)

        # Store dependencies
        self.db = db
        self.account_manager = account_manager
        self.parent = parent

        # Track current conversation state
        self.current_account_id = None
        self.current_jid = None
        self.current_conversation_id = None
        self.current_is_muc = False

        # Scroll manager will be set later (after message_container is created)
        self.scroll_manager = None

        # Infinite scroll state
        self.oldest_loaded_time = None  # Timestamp of oldest loaded message
        self.is_loading_more = False     # Prevent multiple simultaneous loads
        self.has_more_messages = True    # Whether there are older messages to load
        self.total_loaded_count = 0      # Track items in memory
        self.last_load_time = 0          # Timestamp of last load (for cooldown)
        self.last_separator_date = None  # Track last inserted separator date (for day changes)

        # Zone tracking for smart polling control (Phase 2)
        self.in_live_zone = True         # True = live zone (>50% scroll), False = history zone (<=50%)
        self.zone_locked = False         # When True, prevent auto-zone changes (e.g., during search)
        self.main_window = None          # Reference to MainWindow for polling control

        # Setup UI
        self._setup_ui()

    def _setup_ui(self):
        """Create message display widgets."""
        # Message display area (using QListView for proper bubble rendering)
        self.message_area = QListView(self.parent)
        self.message_area.setObjectName("messageArea")
        self.message_area.setFrameShape(QFrame.NoFrame)
        self.message_area.setSelectionMode(QListView.NoSelection)
        self.message_area.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.message_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.message_area.setSpacing(0)

        # Setup model and delegate
        self.message_model = QStandardItemModel()
        self.message_area.setModel(self.message_model)

        # Create delegate with current theme and database
        theme_manager = get_theme_manager()
        self.message_delegate = MessageBubbleDelegate(theme_name=theme_manager.current_theme, db=self.db)
        self.message_area.setItemDelegate(self.message_delegate)

        # Connect scrollbar to detect when user scrolls to top (infinite scroll)
        scrollbar = self.message_area.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll_changed)

    def set_scroll_manager(self, scroll_manager):
        """Set the scroll manager (must be called after initialization)."""
        self.scroll_manager = scroll_manager

    def _on_scroll_changed(self, value):
        """
        Handle scroll position changes to trigger infinite scroll and zone detection.

        When user scrolls near the top, load more older messages.
        Zone detection controls polling: disable when viewing history, enable when at bottom.
        """
        scrollbar = self.message_area.verticalScrollBar()

        # Calculate scroll percentage (used for both infinite scroll and zone detection)
        max_value = scrollbar.maximum()
        if max_value > 0:
            percentage = (value / max_value) * 100
        else:
            percentage = 100  # No scrollbar = at bottom = live zone

        # Debug logging for scroll position
        # logger.debug(f"Scroll: value={value}, max={max_value}, pct={percentage:.1f}%")

        # 1. Infinite scroll: Load more when scrolled within top 25% of content
        # Using percentage instead of fixed pixels to handle fast scrolling and give time buffer
        if percentage < 25 and not self.is_loading_more and self.has_more_messages:
            # Add cooldown to prevent rapid re-triggers from mouse wheel spinning
            now = time.time()
            cooldown_seconds = 0.5  # 500ms cooldown between loads (300 messages = larger query)

            if (now - self.last_load_time) < cooldown_seconds:
                logger.debug(f"Cooldown active, skipping load ({now - self.last_load_time:.2f}s < {cooldown_seconds}s)")
            else:
                logger.info(f"Near top ({percentage:.1f}% < 25%), triggering load more (oldest_time={self.oldest_loaded_time})")
                self.last_load_time = now
                self._load_more_messages()

        # 2. Zone detection: Calculate scroll percentage for polling control
        # Live zone = > 50% (near bottom), History zone = <= 50% (scrolled up)
        # BUT: Only if zone is not locked (locked during search views)
        if not self.zone_locked:
            in_live_zone = (percentage > 50)

            # Debug logging for zone detection
            # logger.debug(f"Zone check: pct={percentage:.1f}%, in_live={in_live_zone}, was_live={self.in_live_zone}")

            # Detect zone changes
            if in_live_zone != self.in_live_zone:
                self.in_live_zone = in_live_zone
                zone_name = "LIVE" if in_live_zone else "HISTORY"
                logger.info(f"Zone changed: {zone_name} (scroll position: {percentage:.1f}%)")

                # Control polling via MainWindow
                if self.main_window:
                    self.main_window.set_chat_polling_enabled(in_live_zone)
                else:
                    logger.warning("Cannot control polling - main_window reference is None!")
        else:
            logger.debug(f"Zone locked, ignoring scroll position {percentage:.1f}%")

    def load_messages(self, account_id: int, jid: str, is_muc: bool, conversation_id: int):
        """
        Load messages for a conversation.

        Args:
            account_id: Account ID
            jid: Contact/room JID
            is_muc: True if this is a MUC room
            conversation_id: Conversation ID
        """
        self.current_account_id = account_id
        self.current_jid = jid
        self.current_is_muc = is_muc
        self.current_conversation_id = conversation_id

        logger.debug(f"load_messages: account={account_id}, jid={jid}, is_muc={is_muc}, conv_id={conversation_id}")

        # Load and display messages
        self._load_messages()

    def _load_messages(self, before_time=None):
        """
        Load and display messages from database.

        Args:
            before_time: If None, load last 300 messages (initial load).
                        If set, load 300 messages older than this timestamp (scroll-to-load).
        """
        if not self.current_account_id or not self.current_jid:
            logger.debug("_load_messages: No account or JID set")
            return

        # Check if we were near bottom before reload
        was_near_bottom = self.scroll_manager._is_near_bottom()

        # Clear existing messages and caches (only on initial load, not when loading more)
        if before_time is None:
            self.message_model.clear()
            # Reset infinite scroll state
            self.oldest_loaded_time = None
            self.has_more_messages = True
            self.total_loaded_count = 0
            self.last_separator_date = None  # Reset separator tracking

        # Update delegate with current account for reactions
        self.message_delegate.set_account(self.current_account_id)

        # Get jid_id
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.current_jid,))
        if not jid_row:
            logger.warning(f"_load_messages: JID {self.current_jid} not found in database")
            # TODO: Show empty state
            return

        jid_id = jid_row['id']
        # logger.debug(f"_load_messages: Loading messages for jid_id={jid_id}, account={self.current_account_id}")

        # Get conversation ID (type=0 for chat, type=1 for MUC)
        conv_type = 1 if self.current_is_muc else 0
        conversation_id = self.db.get_or_create_conversation(self.current_account_id, jid_id, conv_type)

        # OPTIMIZED: Single JOIN query to fetch ALL data at once (no N+1 queries!)
        # This replaces: 1 content_item query + 100 individual message/file/call queries
        # Performance: ~10-50x faster, especially noticeable with large conversations
        if before_time is None:
            # Initial load: Get the LAST 100 items (most recent)
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
                LIMIT 300
            """, (conversation_id,))
        else:
            # Load more: Get 300 items OLDER than before_time
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
                LIMIT 300
            """, (conversation_id, before_time))

        # Reverse to display oldest-to-newest in UI
        rows = list(reversed(rows))

        # logger.debug(f"_load_messages: Found {len(rows)} items in database")

        if not rows:
            logger.debug("No content items found for this conversation")
            # TODO: Show empty state
            return

        # When loading more (before_time is set), we need to PREPEND items (insert at top)
        # When initial load (before_time is None), we APPEND items (normal behavior)
        insert_at_top = (before_time is not None)

        # Process rows and add to model
        self._populate_model_with_rows(rows, insert_at_top=insert_at_top)

        # Update infinite scroll state
        if rows:
            # Track oldest loaded timestamp for next load
            if before_time is None:
                # Initial load: oldest is the first item (after reversal)
                self.oldest_loaded_time = rows[0]['time']
            else:
                # Loading more: update to new oldest
                self.oldest_loaded_time = min(self.oldest_loaded_time, rows[0]['time'])

            # Update total count
            self.total_loaded_count += len(rows)

            # Check if there might be more messages
            if len(rows) < 100:
                self.has_more_messages = False
                # logger.debug("No more messages to load (got < 100)")

            # logger.debug(f"Loaded {len(rows)} items. Total: {self.total_loaded_count}, Oldest: {self.oldest_loaded_time}, Has more: {self.has_more_messages}")
        else:
            self.has_more_messages = False
            logger.debug("No content items loaded")

        # Only auto-scroll if we were near bottom before (and this is not a load-more operation)
        if was_near_bottom and before_time is None:
            self.message_area.scrollToBottom()

        # logger.debug(f"Loaded {len(rows)} content items")

    def _load_more_messages(self):
        """
        Load older messages when scrolling to top (infinite scroll).

        Called when user scrolls near the top and there are more messages to load.
        Prepends 300 older messages to the top of the message list.
        """
        # Prevent concurrent loads
        if self.is_loading_more:
            logger.debug("Already loading more messages, skipping")
            return

        # Check if there are more messages to load
        if not self.has_more_messages:
            logger.debug("No more messages to load")
            return

        # Check if we have an oldest timestamp
        if self.oldest_loaded_time is None:
            logger.debug("No oldest timestamp, cannot load more")
            return

        logger.info(f"Loading more messages (before {self.oldest_loaded_time})")
        self.is_loading_more = True

        try:
            # Remember current scroll position to preserve it
            scrollbar = self.message_area.verticalScrollBar()
            old_value = scrollbar.value()
            old_max = scrollbar.maximum()

            # Count items before load
            old_row_count = self.message_model.rowCount()

            # Load 300 messages older than oldest_loaded_time
            self._load_messages(before_time=self.oldest_loaded_time)

            # Count new items added
            new_row_count = self.message_model.rowCount()
            items_added = new_row_count - old_row_count

            # AGGRESSIVE scroll position fixing to avoid "upper corner syndrome"
            # Multiple techniques to force Qt to update properly

            # 1. Block scroll signals to prevent re-triggering during adjustment
            scrollbar.blockSignals(True)

            # 2. Force geometry updates in multiple ways
            self.message_area.updateGeometries()
            QApplication.processEvents()  # Process pending layout events
            self.message_area.viewport().update()  # Force viewport repaint

            # 3. Get the NEW max value after forced layout
            new_max = scrollbar.maximum()

            # 4. Calculate scroll adjustment
            delta = new_max - old_max
            new_value = old_value + delta

            logger.debug(f"Scroll math: old_value={old_value}, old_max={old_max}, new_max={new_max}, delta={delta}, new_value={new_value}")

            # 5. Set new position IMMEDIATELY
            scrollbar.setValue(new_value)

            # 6. Unblock signals
            scrollbar.blockSignals(False)

            # 7. Defer a SECOND adjustment to next event loop (insurance policy)
            # Sometimes Qt needs one more cycle to get it right
            def final_adjustment():
                final_max = scrollbar.maximum()
                if final_max != new_max:
                    # Max changed AGAIN after we thought we were done - recalculate
                    final_delta = final_max - old_max
                    final_value = old_value + final_delta
                    scrollbar.setValue(final_value)
                    logger.debug(f"Final scroll adjustment: {new_value} → {final_value} (max changed: {new_max} → {final_max})")

            QTimer.singleShot(0, final_adjustment)

            logger.info(f"Loaded {items_added} messages, scroll adjusted: {old_value} → {new_value} (max: {old_max} → {new_max})")

        except Exception as e:
            logger.error(f"Failed to load more messages: {e}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            self.is_loading_more = False

    def _populate_model_with_rows(self, rows, insert_at_top=False):
        """
        Populate model with rows from database query.

        Args:
            rows: List of database rows (content_item with JOINed data)
            insert_at_top: If True, prepend items (for load-more), else append
        """
        insert_position = 0

        for row in rows:
            content_item_id = row['ci_id']
            content_type = row['content_type']
            row_timestamp = row['time']

            # Check if we need to insert a day separator
            row_date = datetime.fromtimestamp(row_timestamp).date()
            if self.last_separator_date is None or row_date != self.last_separator_date:
                # Date changed - insert separator
                separator_text = get_day_separator_text(row_timestamp)

                separator_item = QStandardItem()
                separator_item.setData(True, MessageBubbleDelegate.ROLE_IS_SEPARATOR)
                separator_item.setData(separator_text, MessageBubbleDelegate.ROLE_SEPARATOR_TEXT)

                # Add separator to model
                if insert_at_top:
                    self.message_model.insertRow(insert_position, separator_item)
                    insert_position += 1
                else:
                    self.message_model.appendRow(separator_item)

                # Update last separator date
                self.last_separator_date = row_date
                # logger.debug(f"Inserted day separator: {separator_text}")

            if content_type == 0:
                # Message - data already loaded from JOIN
                if not row['msg_id']:
                    continue

                direction = row['msg_direction']
                body = row['body'] or ''
                timestamp = get_bubble_timestamp(row_timestamp)
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

                # Create item
                item = QStandardItem()
                item.setData(direction, MessageBubbleDelegate.ROLE_DIRECTION)
                item.setData(body, MessageBubbleDelegate.ROLE_BODY)
                item.setData(timestamp, MessageBubbleDelegate.ROLE_TIMESTAMP)
                item.setData(row_timestamp, MessageBubbleDelegate.ROLE_TIMESTAMP_RAW)
                item.setData(encrypted, MessageBubbleDelegate.ROLE_ENCRYPTED)
                item.setData(marked, MessageBubbleDelegate.ROLE_MARKED)
                item.setData(msg_type, MessageBubbleDelegate.ROLE_TYPE)
                item.setData(nickname, MessageBubbleDelegate.ROLE_NICKNAME)
                item.setData(is_carbon, MessageBubbleDelegate.ROLE_IS_CARBON)
                item.setData(message_id, MessageBubbleDelegate.ROLE_MESSAGE_ID)
                item.setData(quoted_body, MessageBubbleDelegate.ROLE_QUOTED_BODY)
                item.setData(content_item_id, MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID)

                # Add to model (prepend if loading more, append if initial load)
                if insert_at_top:
                    self.message_model.insertRow(insert_position, item)
                    insert_position += 1
                else:
                    self.message_model.appendRow(item)

            elif content_type == 2:
                # File transfer - data already loaded from JOIN
                if not row['ft_id']:
                    continue

                direction = row['ft_direction']
                file_path = row['path']
                file_name = row['file_name'] or 'file'
                mime_type = row['mime_type'] or ''
                file_size = row['size'] or 0
                timestamp = get_bubble_timestamp(row_timestamp)
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
                file_icon = self.message_delegate._get_file_icon(mime_type)
                file_size_text = self.message_delegate._format_file_size(file_size) if file_size else "Unknown size"

                # Create item for file
                item = QStandardItem()
                item.setData(direction, MessageBubbleDelegate.ROLE_DIRECTION)
                item.setData(file_path, MessageBubbleDelegate.ROLE_FILE_PATH)
                item.setData(file_name, MessageBubbleDelegate.ROLE_FILE_NAME)
                item.setData(mime_type, MessageBubbleDelegate.ROLE_MIME_TYPE)
                item.setData(file_size, MessageBubbleDelegate.ROLE_FILE_SIZE)
                item.setData(file_icon, MessageBubbleDelegate.ROLE_FILE_ICON)
                item.setData(file_size_text, MessageBubbleDelegate.ROLE_FILE_SIZE_TEXT)
                item.setData(timestamp, MessageBubbleDelegate.ROLE_TIMESTAMP)
                item.setData(row_timestamp, MessageBubbleDelegate.ROLE_TIMESTAMP_RAW)
                item.setData(encrypted, MessageBubbleDelegate.ROLE_ENCRYPTED)
                item.setData(0, MessageBubbleDelegate.ROLE_MARKED)  # Files don't have markers
                item.setData(0, MessageBubbleDelegate.ROLE_TYPE)
                item.setData("", MessageBubbleDelegate.ROLE_NICKNAME)
                item.setData(is_carbon, MessageBubbleDelegate.ROLE_IS_CARBON)
                item.setData(message_id, MessageBubbleDelegate.ROLE_MESSAGE_ID)
                item.setData(content_item_id, MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID)

                # Add to model (prepend if loading more, append if initial load)
                if insert_at_top:
                    self.message_model.insertRow(insert_position, item)
                    insert_position += 1
                else:
                    self.message_model.appendRow(item)

            elif content_type == 3:
                # Call - data already loaded from JOIN
                if not row['call_id']:
                    continue

                direction = row['call_direction']
                timestamp = get_bubble_timestamp(row_timestamp)
                call_state = row['call_state']
                call_type = row['call_type']

                # Calculate duration
                if row['call_end_time']:
                    duration = row['call_end_time'] - row['call_time']
                else:
                    duration = None  # Ongoing or no answer

                # Create item for call
                item = QStandardItem()
                item.setData(direction, MessageBubbleDelegate.ROLE_DIRECTION)
                item.setData(timestamp, MessageBubbleDelegate.ROLE_TIMESTAMP)
                item.setData(row_timestamp, MessageBubbleDelegate.ROLE_TIMESTAMP_RAW)
                item.setData(call_state, MessageBubbleDelegate.ROLE_CALL_STATE)
                item.setData(duration, MessageBubbleDelegate.ROLE_CALL_DURATION)
                item.setData(call_type, MessageBubbleDelegate.ROLE_CALL_TYPE)
                item.setData(content_item_id, MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID)
                # Mark as call by setting body to None (differentiate from messages/files)
                item.setData(None, MessageBubbleDelegate.ROLE_BODY)
                item.setData(None, MessageBubbleDelegate.ROLE_FILE_PATH)

                # Add to model (prepend if loading more, append if initial load)
                if insert_at_top:
                    self.message_model.insertRow(insert_position, item)
                    insert_position += 1
                else:
                    self.message_model.appendRow(item)

    def _send_displayed_markers(self):
        """
        Send 'displayed' marker for most recent received message (XEP-0333 compliant).

        Per XEP-0333:
        - Only send marker for MOST RECENT message
        - Track via conversation.read_up_to_item to prevent duplicates
        - Don't send markers for MUC (optional, not standard practice)

        For MUCs: We still update read_up_to_item locally to clear unread counters,
        but we don't send XMPP markers (MUCs don't support XEP-0333).
        """
        if not self.current_account_id or not self.current_jid:
            logger.debug("_send_displayed_markers: No account or JID")
            return

        try:
            # Get account
            account = self.account_manager.get_account(self.current_account_id)
            if not account or not account.client:
                logger.warning(f"_send_displayed_markers: No account or client for {self.current_account_id}")
                return

            # Get jid_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (self.current_jid,))
            if not jid_row:
                logger.debug(f"_send_displayed_markers: JID {self.current_jid} not found in DB")
                return
            jid_id = jid_row['id']

            # Get or create conversation (type=0 for chat, type=1 for MUC)
            conv_type = 1 if self.current_is_muc else 0
            conversation_id = self.db.get_or_create_conversation(self.current_account_id, jid_id, conv_type)

            # Get conversation state
            conv = self.db.fetchone("""
                SELECT read_up_to_item, send_marker
                FROM conversation
                WHERE id = ?
            """, (conversation_id,))

            if not conv:
                logger.warning(f"_send_displayed_markers: Conversation {conversation_id} not found")
                return

            # For 1-to-1 chats: Check if markers are enabled
            # For MUCs: Skip marker check (we'll update locally but not send XMPP markers)
            if not self.current_is_muc and conv['send_marker'] == 0:
                logger.debug(f"_send_displayed_markers: Markers disabled for {self.current_jid}")
                return

            read_up_to_item = conv['read_up_to_item']

            # Get most recent received content (message OR file) that hasn't been marked yet
            # We update read_up_to_item for ANY content to clear unread counters,
            # but only send XMPP markers for messages (files don't have stanza IDs)
            result = self.db.fetchone("""
                SELECT
                    ci.id as content_item_id,
                    ci.content_type,
                    m.message_id,
                    m.origin_id,
                    m.stanza_id
                FROM content_item ci
                LEFT JOIN message m ON ci.foreign_id = m.id AND ci.content_type = 0
                LEFT JOIN file_transfer ft ON ci.foreign_id = ft.id AND ci.content_type = 2
                WHERE ci.conversation_id = ?
                  AND (
                      (ci.content_type = 0 AND m.direction = 0) OR
                      (ci.content_type = 2 AND ft.direction = 0)
                  )
                  AND ci.id > ?
                ORDER BY ci.time DESC
                LIMIT 1
            """, (conversation_id, read_up_to_item))

            if not result:
                logger.debug(f"_send_displayed_markers: No new content to mark for {self.current_jid}")
                return

            content_item_id = result['content_item_id']
            content_type = result['content_type']

            # For 1-to-1: Try to send XMPP marker if this is a message (files don't have stanza IDs)
            # For MUC: Just update read_up_to_item locally (no XMPP marker sent)
            if not self.current_is_muc and content_type == 0:
                # This is a message - try to send XMPP marker
                message_id = result['message_id'] or result['origin_id'] or result['stanza_id']
                if message_id:
                    # Send ONE marker for the most recent message (XEP-0333 compliant)
                    # This is best-effort - if it fails (e.g., disconnected), we still update locally
                    # Next successful marker or sent message will implicitly convey read status
                    try:
                        account.client.send_marker(self.current_jid, message_id, 'displayed')
                        logger.info(f"Sent 'displayed' marker for message {message_id} to {self.current_jid}")
                    except Exception as e:
                        logger.warning(f"Failed to send marker for {message_id} (will update locally anyway): {e}")
            elif content_type == 2:
                logger.debug(f"Most recent content is a file (no XMPP marker to send, will update locally)")

            # Update conversation.read_up_to_item (for both 1-to-1 and MUC, for both messages and files)
            # This clears unread counters locally regardless of whether XMPP marker was sent
            self.db.update_conversation_read_up_to(conversation_id, content_item_id)
            if self.current_is_muc:
                logger.debug(f"Updated read_up_to_item for MUC {self.current_jid} (local only, no XMPP marker)")
            else:
                logger.debug(f"Updated read_up_to_item for {self.current_jid}")

            # Update contact list unread indicators and status bar (walk up parent chain to find main_window)
            widget = self.parent
            while widget:
                if hasattr(widget, 'contact_list'):
                    widget.contact_list.update_unread_indicators(self.current_account_id, self.current_jid)
                    logger.debug(f"Updated contact list unread indicators after marking as read")

                    # Also update status bar stats
                    if hasattr(widget, '_update_status_bar_stats'):
                        widget._update_status_bar_stats()
                        logger.debug(f"Updated status bar stats after marking as read")

                    break
                widget = widget.parent() if hasattr(widget, 'parent') and callable(widget.parent) else None

        except Exception as e:
            logger.error(f"Error in _send_displayed_markers: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def refresh(self, send_markers: bool = False):
        """
        Refresh the message display.

        Args:
            send_markers: If True, send displayed markers for received messages.
                         Should only be True when opening chat or receiving new message,
                         NOT during polling refresh for receipt updates.
        """
        # logger.debug(f"refresh() called: account={self.current_account_id}, jid={self.current_jid}, send_markers={send_markers}")
        if self.current_account_id and self.current_jid:
            # Skip refresh when in history zone (user viewing old messages)
            # This preserves loaded history and prevents scroll jumps
            if not self.in_live_zone:
                logger.debug("Skipping refresh (in history zone)")
                return

            # Clear reaction cache so reactions are re-queried from DB
            self.message_delegate.clear_reaction_cache()
            self._load_messages()
            # Only send markers when explicitly requested (chat open or new message)
            if send_markers:
                self._send_displayed_markers()
        else:
            logger.warning("refresh() called but no active conversation")

    def update_theme(self, theme_name: str):
        """
        Update bubble colors when theme changes.

        Args:
            theme_name: New theme name
        """
        self.message_delegate.set_theme(theme_name)
        # Force repaint of all visible items
        self.message_area.viewport().update()

    def clear(self):
        """Clear the message display."""
        self.current_account_id = None
        self.current_jid = None
        self.current_conversation_id = None
        self.current_is_muc = False
        self.message_model.clear()

    def load_around_message(self, content_item_id, context=50):
        """
        Load messages around a specific message (for search results).

        Args:
            content_item_id: The target content_item ID to center on
            context: Number of messages to load before/after (default 50)
        """
        if not self.current_conversation_id:
            logger.warning("Cannot load around message: no conversation selected")
            return

        logger.info(f"Loading around content_item_id={content_item_id} with context={context}")

        # Single unified query using UNION ALL to load messages around target
        # This replaces 3 separate queries (before, target, after) with 1 query
        # Performance: 1 database round trip instead of 3, easier to maintain
        rows = self.db.fetchall("""
            SELECT * FROM (
                -- Messages BEFORE target (N most recent before target_time, wrapped to allow ORDER BY)
                SELECT * FROM (
                    SELECT
                        ci.id AS ci_id,
                        ci.content_type,
                        ci.time,
                        m.id AS msg_id, m.direction AS msg_direction, m.body,
                        m.encryption AS msg_encryption, m.marked, m.type AS msg_type,
                        m.counterpart_resource, m.message_id, m.origin_id, m.stanza_id, m.is_carbon AS msg_is_carbon,
                        ft.id AS ft_id, ft.direction AS ft_direction, ft.path, ft.file_name, ft.mime_type, ft.size,
                        ft.encryption AS ft_encryption, ft.message_id AS ft_message_id, ft.origin_id AS ft_origin_id,
                        ft.stanza_id AS ft_stanza_id, ft.is_carbon AS ft_is_carbon,
                        c.id AS call_id, c.direction AS call_direction, c.state AS call_state, c.type AS call_type,
                        c.time AS call_time, c.end_time AS call_end_time,
                        quoted_m.body AS quoted_body,
                        j.bare_jid AS counterpart_jid
                    FROM content_item ci
                    LEFT JOIN message m ON ci.content_type = 0 AND ci.foreign_id = m.id
                    LEFT JOIN jid j ON m.counterpart_id = j.id
                    LEFT JOIN file_transfer ft ON ci.content_type = 2 AND ci.foreign_id = ft.id
                    LEFT JOIN call c ON ci.content_type = 3 AND ci.foreign_id = c.id
                    LEFT JOIN reply r ON r.message_id = m.id
                    LEFT JOIN message quoted_m ON r.quoted_message_id = quoted_m.id
                    WHERE ci.conversation_id = ? AND ci.hide = 0 AND ci.time < (SELECT time FROM content_item WHERE id = ?)
                    ORDER BY ci.time DESC
                    LIMIT ?
                )

                UNION ALL

                -- TARGET message itself
                SELECT
                    ci.id AS ci_id,
                    ci.content_type,
                    ci.time,
                    m.id AS msg_id, m.direction AS msg_direction, m.body,
                    m.encryption AS msg_encryption, m.marked, m.type AS msg_type,
                    m.counterpart_resource, m.message_id, m.origin_id, m.stanza_id, m.is_carbon AS msg_is_carbon,
                    ft.id AS ft_id, ft.direction AS ft_direction, ft.path, ft.file_name, ft.mime_type, ft.size,
                    ft.encryption AS ft_encryption, ft.message_id AS ft_message_id, ft.origin_id AS ft_origin_id,
                    ft.stanza_id AS ft_stanza_id, ft.is_carbon AS ft_is_carbon,
                    c.id AS call_id, c.direction AS call_direction, c.state AS call_state, c.type AS call_type,
                    c.time AS call_time, c.end_time AS call_end_time,
                    quoted_m.body AS quoted_body,
                    j.bare_jid AS counterpart_jid
                FROM content_item ci
                LEFT JOIN message m ON ci.content_type = 0 AND ci.foreign_id = m.id
                LEFT JOIN jid j ON m.counterpart_id = j.id
                LEFT JOIN file_transfer ft ON ci.content_type = 2 AND ci.foreign_id = ft.id
                LEFT JOIN call c ON ci.content_type = 3 AND ci.foreign_id = c.id
                LEFT JOIN reply r ON r.message_id = m.id
                LEFT JOIN message quoted_m ON r.quoted_message_id = quoted_m.id
                WHERE ci.id = ?

                UNION ALL

                -- Messages AFTER target (N closest after target_time, wrapped to allow ORDER BY)
                SELECT * FROM (
                    SELECT
                        ci.id AS ci_id,
                        ci.content_type,
                        ci.time,
                        m.id AS msg_id, m.direction AS msg_direction, m.body,
                        m.encryption AS msg_encryption, m.marked, m.type AS msg_type,
                        m.counterpart_resource, m.message_id, m.origin_id, m.stanza_id, m.is_carbon AS msg_is_carbon,
                        ft.id AS ft_id, ft.direction AS ft_direction, ft.path, ft.file_name, ft.mime_type, ft.size,
                        ft.encryption AS ft_encryption, ft.message_id AS ft_message_id, ft.origin_id AS ft_origin_id,
                        ft.stanza_id AS ft_stanza_id, ft.is_carbon AS ft_is_carbon,
                        c.id AS call_id, c.direction AS call_direction, c.state AS call_state, c.type AS call_type,
                        c.time AS call_time, c.end_time AS call_end_time,
                        quoted_m.body AS quoted_body,
                        j.bare_jid AS counterpart_jid
                    FROM content_item ci
                    LEFT JOIN message m ON ci.content_type = 0 AND ci.foreign_id = m.id
                    LEFT JOIN jid j ON m.counterpart_id = j.id
                    LEFT JOIN file_transfer ft ON ci.content_type = 2 AND ci.foreign_id = ft.id
                    LEFT JOIN call c ON ci.content_type = 3 AND ci.foreign_id = c.id
                    LEFT JOIN reply r ON r.message_id = m.id
                    LEFT JOIN message quoted_m ON r.quoted_message_id = quoted_m.id
                    WHERE ci.conversation_id = ? AND ci.hide = 0 AND ci.time > (SELECT time FROM content_item WHERE id = ?)
                    ORDER BY ci.time ASC
                    LIMIT ?
                )
            )
            ORDER BY time ASC
        """, (self.current_conversation_id, content_item_id, context,
              content_item_id,
              self.current_conversation_id, content_item_id, context))

        if not rows:
            logger.warning("No messages found around target")
            return

        logger.info(f"Found {len(rows)} messages around target (context={context})")

        # Clear model and populate with the windowed messages
        # This replaces the conversation view with just the search context
        self.message_model.clear()
        self.last_separator_date = None

        # Process and add rows to model (same logic as _load_messages)
        self._populate_model_with_rows(rows)

        # Enter HISTORY zone to disable polling (we're viewing old messages, not live)
        # Lock the zone so scroll events don't override this
        self._lock_zone_to_history()

        # Scroll to target content_item_id and highlight it
        # Need to find the row index in model that has this content_item_id
        logger.info(f"Load complete, searching for content_item_id={content_item_id}")

        # Defer scroll to next event loop to ensure model is fully populated
        def scroll_to_target():
            target_index = None

            # Search through model to find the target content_item_id
            for row in range(self.message_model.rowCount()):
                index = self.message_model.index(row, 0)
                item_content_id = index.data(MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID)

                if item_content_id == content_item_id:
                    target_index = index
                    logger.info(f"Found target at row {row}")
                    break

            if target_index:
                # Scroll to the target message
                self.message_area.scrollTo(target_index, QListView.PositionAtCenter)
                logger.info(f"Scrolled to target content_item_id={content_item_id}")

                # Highlight the message temporarily
                self._highlight_message(target_index)
            else:
                logger.warning(f"Target content_item_id={content_item_id} not found in loaded messages")

        QTimer.singleShot(100, scroll_to_target)

    def _lock_zone_to_history(self):
        """Lock zone to HISTORY (disable polling during search)."""
        self.zone_locked = True
        if self.in_live_zone:
            self.in_live_zone = False
            logger.info("Entered HISTORY zone (search result) - zone locked")
            if self.main_window:
                self.main_window.set_chat_polling_enabled(False)

    def _unlock_zone_to_live(self):
        """Unlock zone and return to LIVE (enable polling)."""
        self.zone_locked = False
        if not self.in_live_zone:
            self.in_live_zone = True
            logger.info("Re-entered LIVE zone (returning from search) - zone unlocked")
            if self.main_window:
                self.main_window.set_chat_polling_enabled(True)

    def _clear_highlight_only(self):
        """Clear highlight visual state without zone changes."""
        if self.message_delegate.highlighted_index is not None:
            self.message_delegate.highlighted_index = None
            self.message_area.viewport().update()
            self.message_area.removeEventFilter(self)
            if self.parent:
                self.parent.removeEventFilter(self)
            logger.debug("Cleared highlight")

    def _highlight_message(self, index):
        """
        Highlight a message until user takes action (ESC, click, or scroll-down).

        Args:
            index: QModelIndex of the message to highlight

        Note: Event filter is installed/removed dynamically per highlight session.
        Alternative: Install once in _setup_ui() and check highlight state in filter.
        Current approach ensures filter is only active when needed, reducing overhead.
        """
        # Store the target index for the delegate to use
        # The delegate will paint a highlight overlay when this is set
        self.message_delegate.highlighted_index = index

        # Install event filter on message area to catch ESC and mouse clicks
        self.message_area.installEventFilter(self)

        # Also install on parent to catch ESC even when focus is elsewhere
        if self.parent:
            self.parent.installEventFilter(self)

        # Force immediate repaint to show highlight
        self.message_area.viewport().update()
        logger.debug("Applied persistent highlight to search result")

    def clear_highlight_and_return_to_live(self):
        """
        Clear highlight and jump back to live zone (bottom of chat).
        Called by ESC key or scroll-down button.
        """
        # Clear highlight visual state
        self._clear_highlight_only()

        # Unlock zone and re-enter LIVE zone to re-enable polling
        self._unlock_zone_to_live()

        # Reload recent messages to get back to live area
        if self.current_account_id and self.current_jid:
            self.load_messages(self.current_account_id, self.current_jid, self.current_is_muc, self.current_conversation_id)

        # Jump to bottom (live zone) - do this AFTER reload so we scroll to the new messages
        self.message_area.scrollToBottom()
        logger.info("Jumped back to live zone")

    def eventFilter(self, obj, event):
        """Event filter to catch ESC and mouse clicks for clearing highlight."""
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        # Only process if we have an active highlight
        if self.message_delegate.highlighted_index is None:
            return False

        # ESC key: return to live zone (catch from any widget)
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            logger.debug(f"ESC pressed on {obj.__class__.__name__}, clearing highlight")
            self.clear_highlight_and_return_to_live()
            return True

        # Mouse click on message area: just clear highlight (don't jump, but unlock zone)
        if obj == self.message_area and event.type() == QEvent.MouseButtonPress:
            logger.debug("Mouse click on message area, clearing highlight")
            self._clear_highlight_only()
            # Unlock zone so normal zone tracking resumes
            self.zone_locked = False
            logger.debug("Zone unlocked, normal zone tracking resumed")
            return False  # Let the click through

        return False
