"""
Chat header widget for Siproxylin.

Displays contact/room information and conversation controls.
"""

import logging
import asyncio
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QToolButton, QMessageBox, QLineEdit, QListWidget, QListWidgetItem,
    QDialog, QPushButton
)
from PySide6.QtCore import Qt, Signal, QTimer, QRect
from PySide6.QtGui import QShortcut, QKeySequence, QPainter, QFont
from PySide6.QtWidgets import QStyledItemDelegate, QStyle

from ....utils.avatar import get_avatar_pixmap, get_avatar_cache
from ...utils import TooltipEventFilter


logger = logging.getLogger('siproxylin.chat_view.header')


# Search feature constants
SEARCH_MIN_CHARS = 3                # Minimum characters to trigger search
SEARCH_DROPDOWN_LIMIT = 100         # Max results shown in dropdown
SEARCH_MODAL_BATCH_SIZE = 100       # Results loaded per batch in modal
SEARCH_DROPDOWN_TRUNCATE = 60       # Body truncation length in dropdown
SEARCH_MODAL_TRUNCATE = 80          # Body truncation length in modal
SEARCH_LAZY_LOAD_THRESHOLD = 0.9    # Scroll percentage to trigger next batch (90%)


class SearchResultDelegate(QStyledItemDelegate):
    """
    Custom delegate for search results that renders message body and timestamp
    with the timestamp in a smaller font and uses theme colors.
    """

    def __init__(self, theme_name='dark', parent=None):
        """
        Initialize delegate with theme colors.

        Args:
            theme_name: Initial theme name (default 'dark')
            parent: Parent widget
        """
        super().__init__(parent)

        # Load and cache theme colors (updated via set_theme() when theme changes)
        self.set_theme(theme_name)

    def set_theme(self, theme_name: str):
        """
        Update cached theme colors.

        Called on initialization and when user changes theme.

        Args:
            theme_name: Theme name ('light', 'dark', 'terminal', 'gruvbox', 'light_gray')
        """
        from ....styles.bubble_themes import get_bubble_colors

        colors = get_bubble_colors(theme_name)

        # Cache colors as instance variables (used in paint())
        self.bg_color = colors['received_bg']
        self.text_color = colors['received_text']
        self.timestamp_color = colors['timestamp']

    def paint(self, painter, option, index):
        """Paint the search result item with body and smaller timestamp."""
        painter.save()

        # Get the text (format: "body\ntimestamp")
        text = index.data(Qt.DisplayRole)
        if not text or '\n' not in text:
            # Fallback to default painting if format is wrong
            super().paint(painter, option, index)
            painter.restore()
            return

        # Split into body and timestamp
        parts = text.split('\n', 1)
        body = parts[0]
        timestamp = parts[1] if len(parts) > 1 else ""

        # Use cached theme colors (no lookup on every paint!)
        text_color = self.text_color
        timestamp_color = self.timestamp_color

        # Draw selection/hover background using palette for proper theme support
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            # Use palette's highlighted text color
            text_color = option.palette.highlightedText().color()
            timestamp_color = option.palette.highlightedText().color()
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, option.palette.alternateBase())

        # Draw body text (normal size)
        painter.setPen(text_color)
        body_font = option.font
        painter.setFont(body_font)

        body_rect = QRect(option.rect.left() + 8, option.rect.top() + 8,
                         option.rect.width() - 16, option.rect.height() // 2)
        painter.drawText(body_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, body)

        # Draw timestamp (2 sizes smaller)
        timestamp_font = QFont(body_font)
        # Reduce font size by 2 points
        timestamp_font.setPointSize(max(6, body_font.pointSize() - 2))
        painter.setFont(timestamp_font)

        # Use timestamp color from theme
        painter.setPen(timestamp_color)

        timestamp_rect = QRect(option.rect.left() + 8, option.rect.top() + body_rect.height() + 4,
                              option.rect.width() - 16, option.rect.height() // 2)
        painter.drawText(timestamp_rect, Qt.AlignLeft | Qt.AlignTop, timestamp)

        painter.restore()

    def sizeHint(self, option, index):
        """Return size hint for the item."""
        # Make items taller to accommodate two lines with different font sizes
        return super().sizeHint(option, index)


class SearchResultsModal(QDialog):
    """
    Modal dialog showing all search results with lazy loading.

    Loads results in batches of 100 as user scrolls to bottom.
    Emits result_clicked signal when user selects a result.
    """

    result_clicked = Signal(int, int)  # (message_id, content_item_id)

    def __init__(self, db, conversation_id, search_query, total_count, parent=None, theme_name='dark'):
        """
        Initialize modal with search parameters.

        Args:
            db: Database connection
            conversation_id: Conversation ID to search in
            search_query: SQL LIKE pattern for search
            total_count: Total number of search results
            parent: Parent widget
            theme_name: Theme name for delegate (default 'dark')
        """
        super().__init__(parent)

        self.db = db
        self.conversation_id = conversation_id
        self.search_query = search_query
        self.total_count = total_count
        self.theme_name = theme_name

        # Track loaded results
        self.loaded_count = 0
        self.is_loading = False
        self.all_loaded = False

        # Setup UI
        self._setup_ui()

        # Load first batch
        self._load_more_results()

    def _setup_ui(self):
        """Create modal UI."""
        self.setWindowTitle("Search Results")
        self.setModal(True)
        self.resize(800, 500)

        # Main layout
        layout = QVBoxLayout(self)

        # Title label
        search_term = self.search_query.strip('%')
        title_label = QLabel(f"Search results for \"{search_term}\" ({self.total_count} total)")
        title_label.setObjectName("searchResultsTitle")
        layout.addWidget(title_label)

        # Results list
        self.results_list = QListWidget()
        self.results_list.setSpacing(2)
        # Create delegate with theme and store reference for updates
        self.search_modal_delegate = SearchResultDelegate(theme_name=self.theme_name)
        self.results_list.setItemDelegate(self.search_modal_delegate)
        self.results_list.itemClicked.connect(self._on_result_clicked)

        # Connect scroll event for lazy loading
        scrollbar = self.results_list.verticalScrollBar()
        scrollbar.valueChanged.connect(self._on_scroll)

        layout.addWidget(self.results_list)

        # Close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)

    def _load_more_results(self):
        """Load next batch of results."""
        if self.is_loading or self.all_loaded:
            return

        self.is_loading = True
        logger.debug(f"Loading results: offset={self.loaded_count}, limit={SEARCH_MODAL_BATCH_SIZE}")

        # Query next batch using shared helper
        rows = _execute_search_query(
            self.db,
            self.conversation_id,
            self.search_query,
            limit=SEARCH_MODAL_BATCH_SIZE,
            offset=self.loaded_count
        )

        if not rows:
            self.all_loaded = True
            logger.debug("All results loaded")
            self.is_loading = False
            return

        # Add results to list
        from datetime import datetime
        from PySide6.QtCore import QLocale
        locale = QLocale()

        for row in rows:
            body = row['body']
            timestamp = row['time']

            # Format timestamp
            dt = datetime.fromtimestamp(timestamp)
            time_str = locale.toString(dt, "ddd, d MMM yyyy, HH:mm")

            # Truncate body if too long
            display_body = body if len(body) < SEARCH_MODAL_TRUNCATE else body[:SEARCH_MODAL_TRUNCATE] + "..."

            # Create list item
            item = QListWidgetItem(f"{display_body}\n{time_str}")
            item.setData(Qt.UserRole, row['message_id'])
            item.setData(Qt.UserRole + 1, row['content_item_id'])
            self.results_list.addItem(item)

            self.loaded_count += 1

        # Check if all loaded
        if self.loaded_count >= self.total_count:
            self.all_loaded = True
            logger.debug(f"All {self.total_count} results loaded")
        else:
            logger.debug(f"Loaded {self.loaded_count}/{self.total_count} results")

        self.is_loading = False

    def _on_scroll(self, value):
        """Handle scroll event - trigger lazy loading when near bottom."""
        scrollbar = self.results_list.verticalScrollBar()

        # Trigger loading when scrolled to 90% of max
        if scrollbar.maximum() == 0:
            return

        percentage = value / scrollbar.maximum()

        if percentage > SEARCH_LAZY_LOAD_THRESHOLD and not self.all_loaded and not self.is_loading:
            logger.debug(f"Scroll trigger at {percentage:.0%} - loading more results")
            self._load_more_results()

    def _on_result_clicked(self, item):
        """Handle result click - emit signal and close modal."""
        message_id = item.data(Qt.UserRole)
        content_item_id = item.data(Qt.UserRole + 1)

        logger.info(f"Modal result clicked: message_id={message_id}, content_item_id={content_item_id}")

        # Emit signal
        self.result_clicked.emit(message_id, content_item_id)

        # Close modal
        self.accept()


def _execute_search_query(db, conversation_id, search_query, limit, offset=0):
    """
    Execute search query for messages in a conversation.

    Shared by both dropdown search and modal lazy loading to avoid duplication.

    Args:
        db: Database connection
        conversation_id: Conversation ID to search in
        search_query: SQL LIKE pattern (e.g., "%search%")
        limit: Maximum number of results to return
        offset: Number of results to skip (for pagination)

    Returns:
        List of result rows with message_id, content_item_id, body, and time
    """
    return db.fetchall("""
        SELECT m.id as message_id, ci.id as content_item_id, m.body, ci.time
        FROM message m
        JOIN content_item ci ON m.id = ci.foreign_id
        WHERE ci.conversation_id = ? AND ci.content_type = 0 AND m.body LIKE ?
        ORDER BY ci.time DESC
        LIMIT ? OFFSET ?
    """, (conversation_id, search_query, limit, offset))


class ChatHeaderWidget(QFrame):
    """
    Chat header with contact info and controls.

    Displays:
    - Avatar and presence indicator
    - Contact/room name and status
    - Call buttons, OMEMO toggle, settings

    Signals:
    - call_requested(call_type: str)  # "audio" or "video"
    - encryption_toggled(enabled: bool)
    - info_clicked()
    """

    # Define signals
    call_requested = Signal(str)  # "audio" or "video"
    encryption_toggled = Signal(bool)
    info_clicked = Signal()
    search_message_clicked = Signal(int, int)  # (message_id, content_item_id) to load around

    def __init__(self, db, account_manager, spell_check_manager, parent=None, theme_name='dark'):
        """
        Initialize header with dependencies.

        Args:
            db: Database connection
            account_manager: Account manager instance
            spell_check_manager: Spell check manager instance
            parent: Parent widget
            theme_name: Initial theme name (default 'dark')
        """
        super().__init__(parent)

        # Store dependencies
        self.db = db
        self.account_manager = account_manager
        self.spell_check_manager = spell_check_manager
        self.theme_name = theme_name

        # Track current conversation state
        self.current_account_id = None
        self.current_jid = None
        self.current_conversation_id = None
        self.current_is_muc = False

        # Track typing indicator state
        self.is_contact_typing = False
        self.base_contact_name = ""

        # Reference to message widget (set later for search highlight management)
        self.message_widget = None

        # MUC info refresh timer (for participant count)
        self.muc_info_refresh_timer = QTimer(self)
        self.muc_info_refresh_timer.timeout.connect(self._refresh_muc_info_once)
        self.muc_refresh_attempts = 0

        # Track MUC join error signal connection
        self._muc_error_connection = None
        self._muc_error_account_id = None  # Track which account we're connected to

        # Search result delegates (created in _setup_ui, stored for theme updates)
        self.search_dropdown_delegate = None
        self.search_modal_delegate = None

        # Setup UI
        self._setup_ui()

    def _setup_ui(self):
        """Create and layout all header widgets."""
        self.setObjectName("chatHeader")
        self.setFrameShape(QFrame.NoFrame)

        header_layout = QHBoxLayout(self)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(12)

        # Left section: Avatar + Contact name (in VBox for better alignment)
        left_section = QWidget()
        left_layout = QHBoxLayout(left_section)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Avatar label (40x40 circular)
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(40, 40)
        self.avatar_label.setScaledContents(False)
        left_layout.addWidget(self.avatar_label)

        # Presence indicator (for 1-1 chats only, hidden for MUC)
        self.presence_indicator = QLabel()
        self.presence_indicator.setFixedSize(12, 12)
        self.presence_indicator.setAlignment(Qt.AlignCenter)
        self.presence_indicator.hide()  # Hidden by default
        left_layout.addWidget(self.presence_indicator)

        # Contact/Room name and details container (VBox for name + optional subtitle)
        name_container = QWidget()
        name_layout = QVBoxLayout(name_container)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(2)

        # Contact name/jid label
        self.contact_label = QLabel("Select a contact to start chatting")
        self.contact_label.setObjectName("contactLabel")
        name_layout.addWidget(self.contact_label)

        # Account label (show which account is being used) - moved from right section
        self.account_label = QLabel("")
        self.account_label.setObjectName("accountLabel")
        self.account_label.setStyleSheet("color: gray; font-size: 10pt;")
        name_layout.addWidget(self.account_label)

        # Participant count (MUC only) and subject line
        subtitle_container = QWidget()
        subtitle_layout = QHBoxLayout(subtitle_container)
        subtitle_layout.setContentsMargins(0, 0, 0, 0)
        subtitle_layout.setSpacing(8)

        # Participant count label (MUC only)
        self.participant_count_label = QLabel()
        self.participant_count_label.setStyleSheet("color: gray; font-size: 10pt;")
        self.participant_count_label.hide()
        subtitle_layout.addWidget(self.participant_count_label)

        # Join Room button (MUC only, shown when not joined)
        self.join_room_button = QPushButton("Join Room")
        self.join_room_button.setStyleSheet("QPushButton { background-color: #5cb85c; color: white; padding: 4px 12px; border-radius: 3px; }")
        self.join_room_button.clicked.connect(self._on_join_room_clicked)
        self.join_room_button.hide()
        subtitle_layout.addWidget(self.join_room_button)

        # Blocked indicator (XEP-0191, 1-1 chats only)
        self.blocked_indicator = QLabel("🚫 Blocked")
        self.blocked_indicator.setStyleSheet("color: #ff6b6b; font-weight: bold; font-size: 10pt;")
        self.blocked_indicator.setToolTip("This contact is blocked. Messages cannot be sent or received.")
        self.blocked_indicator.hide()  # Hidden by default
        subtitle_layout.addWidget(self.blocked_indicator)

        # Room subject label (MUC only)
        self.room_subject_label = QLabel()
        self.room_subject_label.setStyleSheet("color: gray; font-size: 10pt; font-style: italic;")
        self.room_subject_label.setWordWrap(False)
        self.room_subject_label.hide()
        subtitle_layout.addWidget(self.room_subject_label)
        subtitle_layout.addStretch()

        name_layout.addWidget(subtitle_container)

        left_layout.addWidget(name_container)

        header_layout.addWidget(left_section)

        # Stretch to push search to center
        header_layout.addStretch()

        # Search input (centered, Google-style)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("🔍 Search messages...")
        self.search_input.setFixedWidth(300)  # 20% wider (250 * 1.2 = 300)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(self._on_search_enter_pressed)
        self.search_input.installEventFilter(self)  # For arrow keys and ESC
        header_layout.addWidget(self.search_input)

        # Stretch to push right section to the right
        header_layout.addStretch()

        # Search results dropdown (initially hidden)
        self.search_dropdown = QListWidget(self)
        self.search_dropdown.setObjectName("searchDropdown")
        # Use ToolTip instead of Popup - ToolTip doesn't steal keyboard focus
        self.search_dropdown.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.search_dropdown.setFixedWidth(500)  # Wider to reduce wrapping
        self.search_dropdown.setMaximumHeight(300)
        self.search_dropdown.setSpacing(2)  # Add spacing between items (separator effect)
        self.search_dropdown.setFocusPolicy(Qt.NoFocus)  # Keep focus on search input!
        # Create delegate with theme and store reference for updates
        self.search_dropdown_delegate = SearchResultDelegate(theme_name=self.theme_name)
        self.search_dropdown.setItemDelegate(self.search_dropdown_delegate)
        self.search_dropdown.itemClicked.connect(self._select_search_result)
        self.search_dropdown.hide()

        # Track search results
        self.search_results = []  # List of (message_id, content_item_id, body, timestamp)

        # Right section: Call buttons + OMEMO + Info button
        right_section = QWidget()
        right_layout = QHBoxLayout(right_section)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # Call buttons
        self.audio_call_button = QToolButton()
        self.audio_call_button.setObjectName("audioCallButton")
        self.audio_call_button.setText("📞")
        self.audio_call_button.setToolTip("Audio call")
        self.audio_call_button.setFixedSize(32, 32)
        self.audio_call_button.clicked.connect(self._on_audio_call_clicked)
        right_layout.addWidget(self.audio_call_button)

        self.video_call_button = QToolButton()
        self.video_call_button.setObjectName("videoCallButton")
        self.video_call_button.setText("🎥")
        self.video_call_button.setToolTip("Video call (coming in ROADMAP C)")
        self.video_call_button.setFixedSize(32, 32)
        self.video_call_button.setEnabled(False)  # Disabled placeholder
        right_layout.addWidget(self.video_call_button)

        # Vertical separator after call buttons
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setObjectName("headerSeparator")
        right_layout.addWidget(separator)

        # Spell check button (shows flag emoji, e.g., "🇺🇸", "🇷🇺")
        # Font size and styling controlled by theme (18pt for flag visibility)
        self.spell_check_button = QToolButton()
        self.spell_check_button.setObjectName("spellCheckButton")
        self.spell_check_button.setText("🇺🇸")
        self.spell_check_button.setToolTip("Spell check")
        self.spell_check_button.setFixedSize(32, 32)
        self.spell_check_button.clicked.connect(self._on_spell_check_button_clicked)
        right_layout.addWidget(self.spell_check_button)

        # OMEMO toggle button (moved from input area) - same size as other buttons
        self.header_encryption_button = QToolButton()
        self.header_encryption_button.setObjectName("headerEncryptionButton")
        self.header_encryption_button.setCheckable(True)
        self.header_encryption_button.setChecked(True)
        self.header_encryption_button.clicked.connect(self._on_header_encryption_clicked)
        self.header_encryption_button.setToolTip("Toggle OMEMO encryption")
        self.header_encryption_button.setText("🔒")
        self.header_encryption_button.setFixedSize(32, 32)
        right_layout.addWidget(self.header_encryption_button)

        # Info/Settings button (opens contact details or MUC settings)
        self.info_button = QToolButton()
        self.info_button.setObjectName("infoButton")
        self.info_button.setText("⚙")
        self.info_button.setToolTip("Contact/Room settings")
        self.info_button.clicked.connect(self._on_info_button_clicked)
        self.info_button.setFixedSize(32, 32)
        right_layout.addWidget(self.info_button)

        header_layout.addWidget(right_section)

        # Install tooltip event filter for header buttons (700ms delay)
        self.tooltip_filter = TooltipEventFilter(delay_ms=700, parent=self)
        self.spell_check_button.installEventFilter(self.tooltip_filter)
        self.audio_call_button.installEventFilter(self.tooltip_filter)
        self.video_call_button.installEventFilter(self.tooltip_filter)
        self.header_encryption_button.installEventFilter(self.tooltip_filter)
        self.info_button.installEventFilter(self.tooltip_filter)

    def load_contact(self, account_id, jid, is_muc, conversation_id, base_name, encryption_enabled, roster_name=None):
        """
        Load and display contact/room information.

        Args:
            account_id: Account ID
            jid: Contact/room JID
            is_muc: True if MUC room, False if 1-1 chat
            conversation_id: Conversation ID (for settings)
            base_name: Display name (without typing indicator)
            encryption_enabled: Initial encryption state
            roster_name: Roster name from database (for priority calculation)
        """
        # Store conversation state
        self.current_account_id = account_id
        self.current_jid = jid
        self.current_is_muc = is_muc
        self.current_conversation_id = conversation_id
        self.base_contact_name = base_name
        self.roster_name = roster_name

        # Reset typing state
        self.is_contact_typing = False
        self.contact_label.setText(self.base_contact_name)
        font = self.contact_label.font()
        font.setItalic(False)
        self.contact_label.setFont(font)

        # Update account label (now shown under contact name with "Via:" prefix)
        account_info = self.db.fetchone("SELECT bare_jid, nickname FROM account WHERE id = ?", (account_id,))
        if account_info:
            account_display = account_info['nickname'] or account_info['bare_jid']
            self.account_label.setText(f"Via: {account_display}")
        else:
            self.account_label.setText("")

        # Set encryption button state
        self.header_encryption_button.setChecked(encryption_enabled)
        self.set_encryption_enabled(encryption_enabled)

        # Update visibility based on conversation type
        if is_muc:
            # MUC room: show MUC-specific UI elements
            self.presence_indicator.hide()
            self.participant_count_label.show()
            # Note: join_room_button visibility is managed by _update_muc_info() based on join status
            self._update_muc_info()

            # Start refresh timer if participant count is not loaded yet
            self.muc_refresh_attempts = 0
            self.muc_info_refresh_timer.start(2000)  # Check every 2 seconds

            # Connect to MUC join error signal for this account
            # Disconnect previous signal if connected to different account
            if self._muc_error_connection and self._muc_error_account_id is not None:
                prev_account = self.account_manager.get_account(self._muc_error_account_id)
                if prev_account:
                    try:
                        prev_account.muc_join_error.disconnect(self._muc_error_connection)
                    except:
                        pass  # Signal may not be connected

            # Connect to new account's signal
            account = self.account_manager.get_account(account_id)
            if account:
                self._muc_error_connection = self._on_muc_join_error
                account.muc_join_error.connect(self._muc_error_connection)
                self._muc_error_account_id = account_id
        else:
            # 1-1 chat: show presence indicator, hide MUC elements
            self._update_presence_indicator()
            self.presence_indicator.show()
            self.participant_count_label.hide()
            self.room_subject_label.hide()
            self.join_room_button.hide()
            self.muc_info_refresh_timer.stop()

            # Disconnect MUC error signal when not in MUC
            if self._muc_error_connection and self._muc_error_account_id is not None:
                account = self.account_manager.get_account(self._muc_error_account_id)
                if account:
                    try:
                        account.muc_join_error.disconnect(self._muc_error_connection)
                    except:
                        pass
                self._muc_error_connection = None
                self._muc_error_account_id = None

        # Load and display avatar
        self._update_avatar()

        # Update spell check button to show language for this conversation
        self.update_spell_check_button()

        logger.debug(f"Header loaded: {jid} (is_muc={is_muc})")

    def update_display_name(self, new_name: str):
        """
        Update the contact display name without reloading the entire header.

        Useful for XEP-0172 nickname updates where only the name changes.

        Args:
            new_name: New display name
        """
        self.base_contact_name = new_name
        # Update label (preserve typing indicator if active)
        if self.is_contact_typing:
            self.contact_label.setText(f"{self.base_contact_name} ⌨️ typing...")
        else:
            self.contact_label.setText(self.base_contact_name)
        logger.debug(f"Updated display name to: {new_name}")

    def clear(self):
        """Clear header (no conversation selected)."""
        self.current_account_id = None
        self.current_jid = None
        self.current_conversation_id = None
        self.current_is_muc = False
        self.base_contact_name = ""
        self.is_contact_typing = False

        # Reset UI to default state
        self.avatar_label.clear()
        self.contact_label.setText("Select a contact to start chatting")
        self.account_label.setText("")
        self.presence_indicator.hide()
        self.participant_count_label.hide()
        self.blocked_indicator.hide()
        self.room_subject_label.hide()
        self.muc_info_refresh_timer.stop()

        logger.debug("Header cleared")

    def update_theme(self, theme_name: str):
        """
        Update theme colors for search result delegates.

        Args:
            theme_name: New theme name
        """
        self.theme_name = theme_name

        # Update both search delegates
        if self.search_dropdown_delegate:
            self.search_dropdown_delegate.set_theme(theme_name)
        if self.search_modal_delegate:
            self.search_modal_delegate.set_theme(theme_name)

        # Force repaint of search widgets if visible
        if self.search_dropdown.isVisible():
            self.search_dropdown.viewport().update()

        logger.debug(f"Header theme updated to: {theme_name}")

    def update_typing_indicator(self, state: str):
        """
        Update typing indicator in chat header.

        Args:
            state: Chat state ('active', 'composing', 'paused', 'inactive', 'gone')
        """
        # Only show typing for composing state
        if state == 'composing':
            if not self.is_contact_typing:
                self.is_contact_typing = True
                # Add typing indicator (pencil icon) and set italic
                self.contact_label.setText(f"{self.base_contact_name} ✎")
                font = self.contact_label.font()
                font.setItalic(True)
                self.contact_label.setFont(font)
                logger.debug(f"Typing indicator shown for {self.current_jid}")
        else:
            if self.is_contact_typing:
                self.is_contact_typing = False
                # Remove typing indicator and clear italic
                self.contact_label.setText(self.base_contact_name)
                font = self.contact_label.font()
                font.setItalic(False)
                self.contact_label.setFont(font)
                logger.debug(f"Typing indicator cleared for {self.current_jid}")

    def update_blocked_status(self, is_blocked: bool):
        """
        Show/hide blocked indicator.

        Args:
            is_blocked: True if contact is blocked, False otherwise
        """
        if is_blocked:
            self.blocked_indicator.show()
            logger.debug(f"Header: {self.current_jid} is blocked")
        else:
            self.blocked_indicator.hide()
            logger.debug(f"Header: {self.current_jid} is unblocked")

    def set_encryption_enabled(self, enabled: bool):
        """
        Update encryption button state.

        Args:
            enabled: True if encryption is enabled
        """
        if enabled:
            self.header_encryption_button.setText("🔒")
            self.header_encryption_button.setToolTip("OMEMO encryption enabled")
        else:
            self.header_encryption_button.setText("🔓")
            self.header_encryption_button.setToolTip("OMEMO encryption disabled")

    def _update_avatar(self):
        """Load and display avatar for current contact/room."""
        if not self.current_account_id or not self.current_jid:
            # No conversation loaded - clear avatar
            self.avatar_label.clear()
            return

        try:
            # Get avatar pixmap (from DB or fallback to initials)
            avatar_pixmap = get_avatar_pixmap(
                account_id=self.current_account_id,
                jid=self.current_jid,
                size=40
            )

            # Display avatar
            self.avatar_label.setPixmap(avatar_pixmap)
            logger.debug(f"Avatar updated for {self.current_jid}")

        except Exception as e:
            logger.error(f"Failed to load avatar for {self.current_jid}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Clear avatar on error
            self.avatar_label.clear()

    def _update_presence_indicator(self):
        """Update presence indicator for current contact (1-1 chats only)."""
        if not self.current_account_id or not self.current_jid or self.current_is_muc:
            return

        # Get presence from account manager
        account = self.account_manager.get_account(self.current_account_id)
        if not account:
            self.presence_indicator.setText("⚫")
            return

        presence = account.get_contact_presence(self.current_jid)

        # Map presence to colored indicator
        presence_map = {
            'available': '🟢',
            'away': '🟡',
            'xa': '🟠',
            'dnd': '🔴',
            'unavailable': '⚫'
        }

        indicator = presence_map.get(presence, '⚫')
        self.presence_indicator.setText(indicator)
        logger.debug(f"Updated presence indicator for {self.current_jid}: {presence} -> {indicator}")

    def _update_muc_info(self):
        """Update MUC participant count and subject (for MUC rooms only). Returns True if roster loaded."""
        if not self.current_account_id or not self.current_jid or not self.current_is_muc:
            return False

        # Get real-time participant count from XMPP client (same method as MUC details dialog)
        account = self.account_manager.get_account(self.current_account_id)
        if account and account.client:
            try:
                # Check if we've fully joined the room first (IMPORTANT: get_roster throws if not joined)
                room_joined = self.current_jid in account.client.joined_rooms

                if not room_joined:
                    # Room is bookmarked but not joined - show status and join button
                    self.participant_count_label.setText("👥 Not joined")
                    self.participant_count_label.show()
                    self.join_room_button.show()
                    return False

                # Query slixmpp's in-memory MUC roster (XEP-0045) - same as muc_details_dialog.py
                xep_0045 = account.client.plugin['xep_0045']
                roster = xep_0045.get_roster(self.current_jid)

                if roster:
                    # Deduplicate by real JID if available (non-anonymous rooms)
                    # Some users may have multiple nicknames (nickname changes, stale presence)
                    from slixmpp.jid import JID
                    room_jid_obj = JID(self.current_jid)
                    unique_jids = set()
                    anonymous_count = 0

                    for nick in roster:
                        real_jid_str = xep_0045.get_jid_property(room_jid_obj, nick, 'jid')
                        if real_jid_str:
                            bare_jid = str(real_jid_str).split('/')[0]
                            unique_jids.add(bare_jid)
                        else:
                            # Anonymous user (semi-anonymous room)
                            anonymous_count += 1

                    total_count = len(unique_jids) + anonymous_count
                    self.participant_count_label.setText(f"👥 {total_count}")
                    self.participant_count_label.show()
                    self.join_room_button.hide()  # Hide join button when successfully joined
                    logger.debug(f"MUC participant count updated: {total_count} ({len(unique_jids)} identified + {anonymous_count} anonymous)")
                    return True
                else:
                    # Empty roster (shouldn't happen after join)
                    self.participant_count_label.setText("👥 0")
                    self.participant_count_label.show()
                    self.join_room_button.hide()  # Hide join button
                    return False

            except Exception as e:
                logger.error(f"Failed to get MUC participant count for {self.current_jid}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.participant_count_label.setText("👥 ?")
                return False

        # No account or client
        self.participant_count_label.setText("👥 ?")
        return False

    def _refresh_muc_info_once(self):
        """Timer callback to refresh MUC info (participant count) until loaded."""
        if not self.current_is_muc:
            self.muc_info_refresh_timer.stop()
            return

        # Try to update
        loaded = self._update_muc_info()

        if loaded:
            # Successfully loaded - stop timer
            self.muc_info_refresh_timer.stop()
            logger.debug(f"MUC roster loaded for {self.current_jid}, stopping refresh timer")
        else:
            # Not loaded yet - increment attempts
            self.muc_refresh_attempts += 1

            # Stop after 15 attempts (30 seconds)
            if self.muc_refresh_attempts >= 15:
                self.muc_info_refresh_timer.stop()
                logger.warning(f"MUC roster not loaded after 30 seconds for {self.current_jid}, stopping refresh timer")

    def _on_join_room_clicked(self):
        """Handle Join Room button click for bookmarked-but-not-joined MUC rooms."""
        if not self.current_is_muc or not self.current_jid or not self.current_account_id:
            return

        account = self.account_manager.get_account(self.current_account_id)
        if not account:
            return

        # Get bookmark info to get nickname
        bookmark = account.muc.get_bookmark(self.current_jid)
        if not bookmark:
            logger.warning(f"No bookmark found for {self.current_jid}")
            return

        nick = bookmark.nick or account.jid.split('@')[0]
        password = bookmark.password

        logger.info(f"Joining MUC room {self.current_jid} as {nick} (triggered from chat header)")

        # Disable button during join
        self.join_room_button.setEnabled(False)
        self.join_room_button.setText("Joining...")

        async def do_join():
            try:
                # Use _perform_room_join directly (no DB transaction needed for rejoining)
                # Room is already bookmarked, we just need to send join presence
                await account.muc._perform_room_join(self.current_jid, nick, password)
                logger.info(f"Successfully initiated join for {self.current_jid}")

                # Update UI will happen automatically via roster_updated signal
                # and _update_muc_info will be called by the refresh timer
                # Error handling is done via muc_join_error signal (see _on_muc_join_error)

            except Exception as e:
                # This catches exceptions from the join call itself (not server errors)
                logger.error(f"Failed to join room {self.current_jid}: {e}")
                QMessageBox.critical(self, "Join Failed", f"Failed to join room:\n{e}")

                # Re-enable button on error
                self.join_room_button.setEnabled(True)
                self.join_room_button.setText("Join Room")

        # Use QTimer to schedule the async task (avoids nested task issues)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: asyncio.create_task(do_join()))

    def _on_muc_join_error(self, room_jid: str, friendly_msg: str, server_details: str):
        """
        Handle MUC join error signal.

        Called when server rejects our join attempt (e.g., members-only, banned, etc.).

        Args:
            room_jid: Room JID that failed to join
            friendly_msg: User-friendly error message
            server_details: Server error details (condition code + text)
        """
        # Only handle errors for the currently displayed room
        if room_jid != self.current_jid:
            return

        logger.warning(f"MUC join error for {room_jid}: {friendly_msg}")

        # Stop the MUC roster refresh timer (join failed, no roster to load)
        if self.muc_info_refresh_timer.isActive():
            self.muc_info_refresh_timer.stop()
            logger.debug(f"Stopped MUC roster timer for {room_jid} (join failed)")

        # Re-enable join button
        self.join_room_button.setEnabled(True)
        self.join_room_button.setText("Join Room")

        # Format error message: friendly message on first line, server details in parentheses on second
        error_message = f"{friendly_msg}\n\n({server_details})"

        # Show non-blocking error dialog (use .show() instead of .exec() to avoid blocking async event loop)
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setWindowTitle("Cannot Join Room")
        msg_box.setText(error_message)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.show()  # Non-blocking

    def _on_spell_check_button_clicked(self):
        """Show spell check menu when button is clicked."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        self.spell_check_manager.create_spell_check_menu(menu)

        # Show menu below the button
        button_pos = self.spell_check_button.mapToGlobal(self.spell_check_button.rect().bottomLeft())
        menu.exec_(button_pos)

        # Update button text after menu closes (language might have changed)
        self.update_spell_check_button()

    def update_spell_check_button(self):
        """Update spell check button to show current language flag or 🤬 when disabled."""
        if self.spell_check_manager:
            # Check if spell check is enabled for current conversation
            if not self.current_conversation_id:
                self.spell_check_button.setText("🤬")
                return

            # Get enabled state from conversation_setting table (same method as spell_check_manager)
            enabled_str = self.db.get_conversation_setting(
                self.current_conversation_id,
                'spell_check_enabled',
                default='1'
            )
            enabled = enabled_str == '1'

            if enabled:
                # Show flag emoji when enabled
                flag_emoji = self.spell_check_manager.get_flag_emoji()
                self.spell_check_button.setText(flag_emoji)
            else:
                # Show 🤬 when disabled
                self.spell_check_button.setText("🤬")

    def _on_audio_call_clicked(self):
        """Handle audio call button click - emit signal for parent to handle."""
        if not self.current_jid or not self.current_account_id:
            logger.warning("Cannot start call: no conversation selected")
            return

        # Check if this is a MUC (group calls not supported yet)
        if self.current_is_muc:
            QMessageBox.warning(
                self,
                "Group Calls Not Supported",
                "Audio calls are currently only supported for 1-on-1 conversations.\n"
                "Group calls will be available in a future update."
            )
            logger.info(f"Blocked call attempt to MUC room: {self.current_jid}")
            return

        # Get account
        account = self.account_manager.get_account(self.current_account_id)
        if not account:
            logger.error(f"Cannot start call: account {self.current_account_id} not found")
            QMessageBox.critical(
                self,
                "Call Failed",
                "Could not start call: account not available."
            )
            return

        # Check if call functionality is available
        if not account.call_bridge or not account.jingle_adapter:
            logger.warning(f"Call functionality not available for account {self.current_account_id}")
            QMessageBox.warning(
                self,
                "Calling Not Available",
                "Audio calling is not available for this account.\n"
                "This may be due to missing dependencies or initialization errors."
            )
            return

        # Emit signal to parent
        logger.info(f"Emitting call_requested signal for {self.current_jid}")
        self.call_requested.emit("audio")

    def _on_header_encryption_clicked(self):
        """Handle header encryption button click - emit signal and update DB."""
        # Get current state from button
        encryption_enabled = self.header_encryption_button.isChecked()

        # Update button appearance
        self.set_encryption_enabled(encryption_enabled)

        # Save to database
        if self.current_conversation_id:
            encryption_value = 1 if encryption_enabled else 0
            self.db.execute("""
                UPDATE conversation SET encryption = ? WHERE id = ?
            """, (encryption_value, self.current_conversation_id))
            self.db.commit()

            logger.info(f"OMEMO encryption {'enabled' if encryption_enabled else 'disabled'} for conversation {self.current_conversation_id}")

        # Emit signal to parent
        self.encryption_toggled.emit(encryption_enabled)

    def _on_info_button_clicked(self):
        """Handle info/settings button click - emit signal for parent to handle."""
        if not self.current_account_id or not self.current_jid:
            return

        logger.debug(f"Info button clicked for {self.current_jid} (is_muc={self.current_is_muc})")

        # Emit signal to parent
        self.info_clicked.emit()

    def _on_search_text_changed(self, text):
        """Handle search input text changes - search when minimum characters met."""
        if len(text) < SEARCH_MIN_CHARS:
            self.search_dropdown.hide()
            self.search_results = []
            self.search_total_count = 0
            return

        # Search messages in current conversation
        if not self.current_conversation_id:
            return

        # SQL search (case-insensitive LIKE)
        query = f"%{text}%"

        # First, get total count of results
        count_row = self.db.fetchone("""
            SELECT COUNT(*) as total
            FROM message m
            JOIN content_item ci ON m.id = ci.foreign_id
            WHERE ci.conversation_id = ? AND ci.content_type = 0 AND m.body LIKE ?
        """, (self.current_conversation_id, query))

        self.search_total_count = count_row['total'] if count_row else 0

        # Get first batch of results for dropdown using shared helper
        rows = _execute_search_query(
            self.db,
            self.current_conversation_id,
            query,
            limit=SEARCH_DROPDOWN_LIMIT
        )

        # Store results and search query for modal
        self.search_results = rows
        self.current_search_query = query

        # Populate dropdown
        self.search_dropdown.clear()
        if rows:
            from datetime import datetime
            from PySide6.QtCore import QLocale
            locale = QLocale()

            for row in rows:
                body = row['body']
                timestamp = row['time']

                # Format timestamp
                dt = datetime.fromtimestamp(timestamp)
                time_str = locale.toString(dt, "ddd, d MMM yyyy, HH:mm")

                # Truncate body if too long
                display_body = body if len(body) < SEARCH_DROPDOWN_TRUNCATE else body[:SEARCH_DROPDOWN_TRUNCATE] + "..."

                # Create list item - we'll use plain text since HTML doesn't work in QListWidget
                # The timestamp will appear smaller via CSS in the stylesheet
                item = QListWidgetItem(f"{display_body}\n{time_str}")
                item.setData(Qt.UserRole, row['message_id'])
                item.setData(Qt.UserRole + 1, row['content_item_id'])
                self.search_dropdown.addItem(item)

            # Add "Show all results" button if there are more results than dropdown limit
            if self.search_total_count > SEARCH_DROPDOWN_LIMIT:
                show_all_item = QListWidgetItem(f"▼ Show all results ({self.search_total_count} total)")
                show_all_item.setData(Qt.UserRole, -1)  # Special marker for "show all" button
                show_all_item.setData(Qt.UserRole + 1, -1)
                show_all_item.setTextAlignment(Qt.AlignCenter)
                # Style it differently to look like a button
                font = show_all_item.font()
                font.setBold(True)
                show_all_item.setFont(font)
                self.search_dropdown.addItem(show_all_item)

            # Position dropdown below search input
            global_pos = self.search_input.mapToGlobal(self.search_input.rect().bottomLeft())
            self.search_dropdown.move(global_pos)
            self.search_dropdown.show()
            self.search_dropdown.setCurrentRow(0)

            # Keep focus on search input so user can continue typing
            self.search_input.setFocus()
        else:
            self.search_dropdown.hide()

    def _on_search_enter_pressed(self):
        """Handle Enter key in search input - load selected result or cancel highlight if empty."""
        # If search is empty, cancel any active highlight and return to live zone
        if not self.search_input.text():
            if self.message_widget and hasattr(self.message_widget, 'clear_highlight_and_return_to_live'):
                if self.message_widget.message_delegate.highlighted_index is not None:
                    logger.info("Enter on empty search - cancelling highlight and returning to live zone")
                    self.message_widget.clear_highlight_and_return_to_live()
            return

        # Otherwise, select current result if dropdown is visible
        if not self.search_dropdown.isVisible():
            return

        current_item = self.search_dropdown.currentItem()
        if current_item:
            self._select_search_result(current_item)

    def _preview_search_result(self, item):
        """Preview search result without closing search (for Up/Down arrow navigation)."""
        message_id = item.data(Qt.UserRole)
        content_item_id = item.data(Qt.UserRole + 1)

        logger.debug(f"Auto-navigating to result: message_id={message_id}, content_item_id={content_item_id}")

        # Emit signal to jump to this message (keeps search open)
        self.search_message_clicked.emit(message_id, content_item_id)

    def _select_search_result(self, item):
        """Select search result - closes search and jumps to message (or opens modal)."""
        message_id = item.data(Qt.UserRole)
        content_item_id = item.data(Qt.UserRole + 1)

        # Check if this is the "Show all results" button (marked with -1)
        if message_id == -1 and content_item_id == -1:
            logger.info("Opening search results modal")
            self._open_search_modal()
            return

        logger.info(f"Search result clicked: message_id={message_id}, content_item_id={content_item_id}")

        # Hide dropdown and clear search
        self.search_dropdown.hide()
        self.search_input.clear()
        self.search_results = []

        # Emit signal to parent to load around this message
        self.search_message_clicked.emit(message_id, content_item_id)

    def install_search_shortcut(self, parent_widget):
        """Install Ctrl+F shortcut to focus search input."""
        shortcut = QShortcut(QKeySequence("Ctrl+F"), parent_widget)
        shortcut.activated.connect(self._focus_search)

    def _focus_search(self):
        """Focus the search input (called by Ctrl+F)."""
        self.search_input.setFocus()
        self.search_input.selectAll()

    def eventFilter(self, obj, event):
        """Event filter for search input - handle arrow keys and ESC."""
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        if obj == self.search_input and event.type() == QEvent.KeyPress:
            key_event = event
            key = key_event.key()

            # ESC: clear input and hide dropdown (cancel search)
            if key == Qt.Key_Escape:
                self.search_dropdown.hide()
                self.search_input.clear()  # Clear the input
                self.search_results = []
                return True

            if self.search_dropdown.isVisible():
                # Arrow Down: move to next result and auto-jump to it
                if key == Qt.Key_Down:
                    current_row = self.search_dropdown.currentRow()
                    if current_row < self.search_dropdown.count() - 1:
                        self.search_dropdown.setCurrentRow(current_row + 1)
                        # Auto-jump to the newly selected result
                        new_item = self.search_dropdown.currentItem()
                        if new_item:
                            self._preview_search_result(new_item)
                    return True

                # Arrow Up: move to previous result and auto-jump to it
                elif key == Qt.Key_Up:
                    current_row = self.search_dropdown.currentRow()
                    if current_row > 0:
                        self.search_dropdown.setCurrentRow(current_row - 1)
                        # Auto-jump to the newly selected result
                        new_item = self.search_dropdown.currentItem()
                        if new_item:
                            self._preview_search_result(new_item)
                    return True

        # Enter key is handled by returnPressed signal, not here
        return super().eventFilter(obj, event)

    def _open_search_modal(self):
        """Open the search results modal dialog showing all results."""
        if not self.current_conversation_id or not hasattr(self, 'current_search_query'):
            return

        # Create and show modal
        modal = SearchResultsModal(
            db=self.db,
            conversation_id=self.current_conversation_id,
            search_query=self.current_search_query,
            total_count=self.search_total_count,
            parent=self,
            theme_name=self.theme_name
        )

        # Connect modal result clicks to same signal as dropdown
        modal.result_clicked.connect(self._on_modal_result_clicked)

        # Hide dropdown when modal opens
        self.search_dropdown.hide()

        # Show modal
        modal.exec()

    def _on_modal_result_clicked(self, message_id, content_item_id):
        """Handle result click from modal - same as dropdown clicks."""
        logger.info(f"Modal search result clicked: message_id={message_id}, content_item_id={content_item_id}")

        # Clear search input
        self.search_input.clear()
        self.search_results = []

        # Emit signal to load around this message
        self.search_message_clicked.emit(message_id, content_item_id)
