"""
Chat view widget for Siproxylin.

Displays message history for a conversation with timestamps and encryption indicators.
"""

import logging
import asyncio
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListView, QLabel,
    QFrame, QPushButton, QTextEdit, QToolButton, QScrollBar, QMenu, QFileDialog, QSizePolicy, QMessageBox,
    QStackedWidget
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction, QKeyEvent, QTextCursor

from ...db.database import get_db
from ..widgets.message_delegate import MessageBubbleDelegate
from ..widgets.spell_highlighter import EnchantHighlighter
from ...styles.theme_manager import get_theme_manager
from ...core import get_account_manager
from ...utils.avatar import get_avatar_pixmap, get_avatar_cache
from .taps.input import MessageInputField
from .taps.scroll_manager import ScrollManager
from .taps.spell_check import SpellCheckManager
from .taps.header import ChatHeaderWidget
from .taps.context_menus import ContextMenuManager
from .taps.messages import MessageDisplayWidget
from .taps.message_actions import MessageActionsManager
from .taps.welcome_view import WelcomeView


logger = logging.getLogger('siproxylin.chat_view')


class ChatViewWidget(QWidget):
    """Chat view widget for displaying message history."""

    # Signal emitted when user wants to send a message
    send_message = Signal(int, str, str, bool)  # (account_id, jid, message, encrypted)
    # Signal emitted when user wants to send a file
    send_file = Signal(int, str, str, bool)  # (account_id, jid, file_path, encrypted)
    # Signal emitted when user wants to edit a message
    edit_message = Signal(int, str, str, str, bool)  # (account_id, jid, message_id, new_body, encrypted)
    # Signal emitted when user wants to send a reply
    send_reply = Signal(int, str, str, str, str, bool)  # (account_id, jid, reply_to_id, reply_body, fallback_body, encrypted)
    # Signals for welcome view actions
    add_account_requested = Signal()
    create_account_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.db = get_db()
        self.account_manager = get_account_manager()
        self.current_account_id = None
        self.current_jid = None
        self.current_conversation_id = None  # Track conversation ID for settings
        self.current_is_muc = False  # Track if current conversation is MUC
        self.selected_file_path = None  # Track selected file for attachment

        # Track input buffer per conversation (privacy: isolate drafts between chats)
        # Memory-only (privacy-first approach): drafts lost on app restart, prioritizes privacy over usability
        # Format: {(account_id, jid): str}
        self.input_buffers = {}

        # Track file attachment per conversation (also memory-only for privacy)
        # Format: {(account_id, jid): file_path_str}
        self.file_buffers = {}

        # Setup UI - QStackedWidget for view modes
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create stacked widget for view modes
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        # === Page 0: Welcome View (no conversation selected) ===
        self.welcome_page = WelcomeView()
        self.welcome_page.add_account_requested.connect(self._handle_add_account_request)
        self.welcome_page.create_account_requested.connect(self._handle_create_account_request)
        self.stack.addWidget(self.welcome_page)  # Page 0

        # === Page 1: Chat View (active conversation) ===
        chat_page = QWidget()
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # Chat header (contact info + controls)
        # spell_check_manager will be set later after input field is created
        from ...styles.theme_manager import get_theme_manager
        theme_manager = get_theme_manager()
        self.header = ChatHeaderWidget(self.db, self.account_manager, None, chat_page, theme_name=theme_manager.current_theme)

        # Connect header signals to handlers
        self.header.call_requested.connect(self._handle_call_request)
        self.header.encryption_toggled.connect(self._handle_encryption_toggle)
        self.header.info_clicked.connect(self._handle_info_click)
        self.header.search_message_clicked.connect(self._handle_search_result)

        chat_layout.addWidget(self.header)

        # Install Ctrl+F shortcut for search
        self.header.install_search_shortcut(chat_page)

        # Message display widget (manager for message area, model, delegate)
        self.message_widget = MessageDisplayWidget(self.db, self.account_manager, chat_page)

        # Pass MainWindow reference for polling control (Phase 2: smart polling)
        self.message_widget.main_window = self.parent()

        # Pass message_widget reference to header for search highlight management
        self.header.message_widget = self.message_widget

        # Message area container with scroll-to-bottom button
        self.message_container = QWidget()
        message_container_layout = QVBoxLayout(self.message_container)
        message_container_layout.setContentsMargins(0, 0, 0, 0)
        message_container_layout.setSpacing(0)
        message_container_layout.addWidget(self.message_widget.message_area)

        # Scroll manager (handles scroll-to-bottom button and auto-scroll)
        self.scroll_manager = ScrollManager(self.message_widget.message_area, self.message_container, self.message_widget)

        # Set scroll manager on message widget
        self.message_widget.set_scroll_manager(self.scroll_manager)

        # Connect context menu for message area
        self.message_widget.message_area.setContextMenuPolicy(Qt.CustomContextMenu)
        self.message_widget.message_area.customContextMenuRequested.connect(self._show_context_menu)

        # Keep references for backward compatibility
        self.message_area = self.message_widget.message_area
        self.message_model = self.message_widget.message_model
        self.message_delegate = self.message_widget.message_delegate

        chat_layout.addWidget(self.message_container)

        # Message input area - compact frame that adapts to content
        self.input_frame = QFrame()  # Store as instance variable so we can resize it
        self.input_frame.setObjectName("inputFrame")
        self.input_frame.setFrameShape(QFrame.StyledPanel)
        self.input_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)  # Fixed height, expands width
        input_layout = QHBoxLayout(self.input_frame)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(8)

        # File attachment button (paperclip icon)
        self.attach_button = QToolButton()
        self.attach_button.setObjectName("attachButton")
        self.attach_button.setText("📎")
        self.attach_button.setToolTip("Attach file")
        self.attach_button.setMinimumWidth(40)  # Nearly square
        self.attach_button.clicked.connect(self._on_attach_file_clicked)
        input_layout.addWidget(self.attach_button)

        # Input field (multi-line with MessageInputField - same as QTextEdit but handles arrow-up)
        self.input_field = MessageInputField()
        self.input_field.setObjectName("inputField")
        self.input_field.setPlaceholderText("Type a message... (Shift+Enter for new line)")

        # Create message actions manager (NOTE: header created above, but we'll pass it here)
        # This will be fully initialized after header is set up
        self.message_actions = None  # Initialized after header is ready

        # Connect chat state signals (XEP-0085 typing notifications)
        self.input_field.composing_state.connect(self._send_composing_state)
        self.input_field.paused_state.connect(self._send_paused_state)
        self.input_field.active_state.connect(self._send_active_state)

        # Connect voice request signal
        self.input_field.voice_request_clicked.connect(self._handle_voice_request)

        # Connect image paste signal
        self.input_field.image_pasted.connect(self._on_image_pasted)

        # Set document and content margins (margin_top=7, margin_bottom=7)
        self.input_field.document().setDocumentMargin(2)
        self.input_field.setContentsMargins(4, 6, 4, 6)  # left, top, right, bottom

        # Add spell checking to input field (attaches to document, doesn't change widget behavior)
        spell_highlighter = EnchantHighlighter(self.input_field.document(), language='en_US')
        if not spell_highlighter.is_available():
            logger.info("Spell checker not available (install: apt install aspell aspell-en)")

        # Create spell check manager
        self.spell_check_manager = SpellCheckManager(self.db, self.input_field, spell_highlighter)
        # Cross-reference: header needs spell_check_manager, spell_check_manager needs header
        self.header.spell_check_manager = self.spell_check_manager
        self.spell_check_manager.header_widget = self.header
        # Keep reference to highlighter for backward compatibility with existing code
        self.spell_highlighter = spell_highlighter

        # Create context menu manager
        self.context_menu_manager = ContextMenuManager(self, self.db, self.account_manager, self.spell_check_manager)

        # Start with single-line height (FIXED, not min/max)
        self.input_field.setFixedHeight(36)

        self.input_field.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.input_field.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input_field.setLineWrapMode(QTextEdit.WidgetWidth)
        self.input_field.setAcceptRichText(False)  # Plain text only
        self.input_field.setContextMenuPolicy(Qt.CustomContextMenu)
        self.input_field.customContextMenuRequested.connect(self._show_input_context_menu)

        # Connect to auto-resize on DOCUMENT content change (not textChanged!)
        # This is the key from the StackOverflow answer!
        self.input_field.document().contentsChanged.connect(self._adjust_input_height)

        self.input_field.installEventFilter(self)
        input_layout.addWidget(self.input_field)

        # OMEMO button removed - now in header only
        self.encryption_enabled = True

        # Emoji picker button
        self.emoji_button = QToolButton()
        self.emoji_button.setObjectName("emojiButton")
        self.emoji_button.setText("🍺")
        self.emoji_button.setToolTip("Insert emoji")
        self.emoji_button.setFixedHeight(32)
        self.emoji_button.setMinimumWidth(40)
        self.emoji_button.clicked.connect(self._on_emoji_button_clicked)
        input_layout.addWidget(self.emoji_button)

        # Send button
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendButton")
        self.send_button.setMinimumWidth(80)
        self.send_button.clicked.connect(self._on_send_clicked)
        input_layout.addWidget(self.send_button)

        chat_layout.addWidget(self.input_frame, 0)  # 0 stretch = don't expand vertically

        # Add chat page to stack
        self.stack.addWidget(chat_page)  # Page 1

        # Start with empty state (Page 0)
        self.stack.setCurrentIndex(0)

        # Initialize message actions manager (now that header and input are ready)
        self.message_actions = MessageActionsManager(self, self.db, self.account_manager, self.input_field, self.header)

        # Connect arrow-up signal to load last message for editing
        self.input_field.arrow_up_in_empty_field.connect(self._load_last_message_for_editing)

        # Initially disable input until a contact is selected
        self._set_input_enabled(False)

        logger.debug("Chat view widget created with QStackedWidget")

    def load_conversation(self, account_id: int, jid: str):
        """
        Load conversation with a contact.

        Args:
            account_id: Account ID
            jid: Contact JID
        """
        # Save current input buffer before switching (privacy: isolate per-conversation)
        if self.current_account_id is not None and self.current_jid is not None:
            old_key = (self.current_account_id, self.current_jid)
            current_text = self.input_field.toPlainText()
            self.input_buffers[old_key] = current_text
            logger.debug(f"Saved input buffer for {old_key}: {len(current_text)} chars")

        # Clear any file selection when switching conversations
        self._clear_file_selection()

        # Reset chat state when switching conversations (XEP-0085)
        self.input_field.reset_chat_state()

        # Cancel any pending message edit or reply when switching conversations
        if self.message_actions.editing_message_id:
            self.message_actions.cancel_editing()
        if self.message_actions.replying_to_message_id:
            self.message_actions.cancel_reply()

        self.current_account_id = account_id
        self.current_jid = jid

        # Switch to chat view (Page 1)
        self.stack.setCurrentIndex(1)

        # Get jid_id and conversation_id
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
        if jid_row:
            jid_id = jid_row['id']

            # Check if this is a MUC room by checking the conversation type
            # (type=1 for MUC, type=0 for 1-on-1)
            # First try to get existing conversation to determine type
            conv_row = self.db.fetchone("""
                SELECT id, type FROM conversation
                WHERE account_id = ? AND jid_id = ?
            """, (account_id, jid_id))

            if conv_row:
                # Existing conversation - use its type
                self.current_conversation_id = conv_row['id']
                self.current_is_muc = conv_row['type'] == 1
            else:
                # New conversation - check if it's bookmarked to determine type
                muc_check = self.db.fetchone("""
                    SELECT 1 FROM bookmark b
                    WHERE b.account_id = ? AND b.jid_id = ?
                """, (account_id, jid_id))

                self.current_is_muc = muc_check is not None
                conv_type = 1 if self.current_is_muc else 0
                self.current_conversation_id = self.db.get_or_create_conversation(account_id, jid_id, conv_type)
        else:
            self.current_conversation_id = None

        logger.debug(f"Loading conversation: account={account_id}, jid={jid}, is_muc={self.current_is_muc}, conv_id={self.current_conversation_id}")

        # Load encryption state and typing indicator preference from conversation (if exists)
        encryption_enabled = True  # default
        send_typing = True  # default
        if self.current_conversation_id:
            conv = self.db.fetchone("""
                SELECT encryption, send_typing FROM conversation WHERE id = ?
            """, (self.current_conversation_id,))
            if conv:
                # Set encryption state (0 = plain, 1 = OMEMO)
                encryption_enabled = bool(conv['encryption'])
                self.encryption_enabled = encryption_enabled

                # Get typing indicator preference (default True if NULL)
                send_typing = conv['send_typing'] if conv['send_typing'] is not None else 1

        # Update encryption button visibility based on conversation type and MUC features (XEP-0384, standard behavior)
        self._update_encryption_button_visibility()

        # Load spell check settings for this conversation
        self.spell_check_manager.set_conversation(self.current_conversation_id)

        # Prepare header data based on conversation type
        if self.current_is_muc:
            # MUC room - get bookmark name
            muc_room = self.db.fetchone("""
                SELECT b.name FROM bookmark b
                JOIN jid j ON b.jid_id = j.id
                WHERE b.account_id = ? AND j.bare_jid = ?
            """, (account_id, jid))
            display_name = muc_room['name'] if (muc_room and muc_room['name']) else jid
            base_contact_name = f"👥 {display_name}"
            roster_name = None  # MUCs don't have roster names
            is_blocked = False
            # MUCs cannot be blocked - check if user can send messages (based on role)
            self._update_muc_input_state(account_id, jid)
        else:
            # 1-on-1 contact
            contact = self.db.fetchone("""
                SELECT r.name, r.blocked, j.bare_jid
                FROM roster r
                JOIN jid j ON r.jid_id = j.id
                WHERE r.account_id = ? AND j.bare_jid = ?
            """, (account_id, jid))

            if contact:
                # Use 3-source priority: roster.name > contact_nickname > jid
                account = self.account_manager.get_account(account_id)
                if account:
                    display_name = account.get_contact_display_name(jid, roster_name=contact['name'])
                else:
                    display_name = contact['name'] or contact['bare_jid']
                base_contact_name = f"💬 {display_name}"
                # Store roster name for later use (e.g., nickname updates)
                roster_name = contact['name']
                # Check blocked status and update UI
                is_blocked = bool(contact['blocked'])
                self._set_input_enabled(not is_blocked)
            else:
                base_contact_name = f"💬 {jid}"
                roster_name = None
                # Not in roster, assume not blocked
                is_blocked = False
                self._set_input_enabled(True)
            # Always hide visitor overlay for 1-on-1 chats
            self.input_field.hide_visitor_overlay()

        # Load header with all conversation info
        # Note: send_typing is still loaded from DB above but not passed to header (no button anymore)
        self.header.load_contact(
            account_id=account_id,
            jid=jid,
            is_muc=self.current_is_muc,
            conversation_id=self.current_conversation_id,
            base_name=base_contact_name,
            encryption_enabled=encryption_enabled,
            roster_name=roster_name
        )

        # Update blocked indicator if needed (for 1-1 chats only)
        if not self.current_is_muc:
            self.header.update_blocked_status(is_blocked)

        # Load messages from database using message widget
        self.message_widget.load_messages(account_id, jid, self.current_is_muc, self.current_conversation_id)

        # Update input placeholder with shield indicator (after OMEMO capability is determined)
        self._update_input_placeholder()

        # Send displayed markers for received messages (chat opened)
        self.message_widget._send_displayed_markers()

        # Input enable/disable state already set by update_blocked_status() (lines 507, 511)
        # Don't unconditionally enable here - it would override blocked state

        # Restore input buffer for this conversation (privacy: per-conversation isolation)
        new_key = (account_id, jid)
        saved_text = self.input_buffers.get(new_key, "")
        self.input_field.setPlainText(saved_text)
        if saved_text:
            logger.debug(f"Restored input buffer for {new_key}: {len(saved_text)} chars")

        # Focus input field for immediate typing (safe even if disabled)
        self.input_field.setFocus()

    def refresh(self, send_markers: bool = False):
        """
        Refresh the message display.

        Args:
            send_markers: If True, send displayed markers for received messages.
                         Should only be True when opening chat or receiving new message,
                         NOT during polling refresh for receipt updates.
        """
        # logger.debug(f"refresh() called: account={self.current_account_id}, jid={self.current_jid}, send_markers={send_markers}")

        # Don't refresh if not on chat page (e.g., on welcome page)
        if self.stack.currentIndex() != 1:
            logger.debug("Skipping refresh - not on chat page")
            return

        # Delegate to message widget
        self.message_widget.refresh(send_markers)

    def update_theme(self, theme_name: str):
        """
        Update bubble colors when theme changes.

        Args:
            theme_name: New theme name
        """
        # Update message widget (message bubbles)
        self.message_widget.update_theme(theme_name)

        # Update header (search result delegates)
        self.header.update_theme(theme_name)

    def clear(self):
        """Clear the chat view."""
        # Switch to empty state (Page 0)
        self.stack.setCurrentIndex(0)

        self.current_account_id = None
        self.current_jid = None
        self.header.clear()
        self.message_widget.clear()
        self._set_input_enabled(False)

    def _set_input_enabled(self, enabled: bool):
        """Enable or disable message input."""
        self.input_field.setEnabled(enabled)
        self.emoji_button.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        # OMEMO button is now in header, not in input area

    def _update_input_placeholder(self):
        """
        Update input field placeholder text with shield indicator.

        Shows:
        - 🛡️ Type a message... - OMEMO available AND enabled
        - 🛡️❌ Type a message... - OMEMO not supported OR disabled
        """
        # Get OMEMO capability from message widget (set during load_messages)
        omemo_capable = getattr(self.message_widget, 'current_omemo_capable', False)

        # Get encryption toggle state
        encryption_enabled = getattr(self, 'encryption_enabled', False)

        # Determine shield indicator
        if omemo_capable and encryption_enabled:
            shield = "🛡️🔒 "
        else:
            shield = "🛡️❌ "

        # Update placeholder (preserve "Type a message..." text)
        self.input_field.setPlaceholderText(f"{shield}Type a message... (Shift+Enter for new line)")
        logger.debug(f"Updated input placeholder: omemo_capable={omemo_capable}, encryption_enabled={encryption_enabled}, shield={shield.strip()}")

    def _update_muc_input_state(self, account_id: int, room_jid: str):
        """
        Update MUC input state based on user's role (visitor check).

        Args:
            account_id: Account ID
            room_jid: Room JID
        """
        account = self.account_manager.get_account(account_id)
        if not account or not account.is_connected():
            self._set_input_enabled(True)
            self.input_field.hide_visitor_overlay()
            return

        role = account.muc.get_own_role(room_jid)
        logger.debug(f"MUC role for {room_jid}: {role}")

        # Visitor in moderated room cannot send messages
        if role == 'visitor':
            self._set_input_enabled(True)  # Keep field enabled so overlay is clickable
            self.input_field.show_visitor_overlay()
            # Reset throttling timer when becoming visitor (e.g., voice revoked)
            # This allows immediate new request after demotion
            account.muc.reset_voice_request_timer(room_jid)
            logger.info(f"User is visitor in {room_jid} - showing voice request overlay")
        else:
            # Participant, moderator, or role not yet known - enable normal input
            self._set_input_enabled(True)
            self.input_field.hide_visitor_overlay()
            # Reset throttling timer when promoted (voice granted)
            # This allows new request if demoted again later
            account.muc.reset_voice_request_timer(room_jid)
            logger.debug(f"User has voice in {room_jid} - hiding overlay and resetting timer")

    def _update_encryption_button_visibility(self):
        """
        Update encryption button visibility based on conversation type and MUC features.

        Per XEP-0384:
        - 1-to-1 Chat: Always show
        - MUC: Show only if room CURRENTLY supports OMEMO (requires muc_nonanonymous AND muc_membersonly)
        - Uses live disco_cache data, not stale database values
        """
        if not self.current_conversation_id:
            self.header.header_encryption_button.setVisible(True)  # Default: show
            return

        try:
            # Get conversation type
            conv = self.db.fetchone("""
                SELECT type
                FROM conversation
                WHERE id = ?
            """, (self.current_conversation_id,))
        except Exception as e:
            # Graceful degradation if query fails
            logger.debug(f"Failed to query conversation: {e}")
            self.header.header_encryption_button.setVisible(True)  # Default: show
            return

        if not conv:
            self.header.header_encryption_button.setVisible(True)  # Default: show
            return

        conv_type = conv['type']

        # Read MUC feature flags from disco_cache (in-memory) instead of database
        # for always-fresh data
        muc_nonanonymous = None
        muc_membersonly = None

        # Get account and client to access disco_cache
        account = self.account_manager.get_account(self.current_account_id)
        if account and account.client and hasattr(account.client, 'disco_cache'):
            disco_info = account.client.disco_cache.get(self.current_jid, {})
            muc_nonanonymous = disco_info.get('muc_nonanonymous')
            muc_membersonly = disco_info.get('muc_membersonly')

        # Determine visibility
        show_button = True

        if conv_type == 0:  # CHAT (1-to-1)
            show_button = True
        elif conv_type == 1:  # GROUPCHAT (MUC)
            # XEP-0384: OMEMO requires non-anonymous (MUST) and members-only (SHOULD)
            # Always check disco_cache for current room capabilities (ignore stale DB encryption field)
            if muc_nonanonymous is not None and muc_membersonly is not None:
                # disco_cache available - check if room CURRENTLY supports OMEMO
                show_button = bool(muc_nonanonymous and muc_membersonly)
            else:
                # disco_cache not available (offline or not joined) - safe default: show button
                show_button = True

        self.header.header_encryption_button.setVisible(show_button)

        if not show_button:
            logger.debug(
                f"Hiding encryption button for MUC {self.current_jid}: "
                f"nonanonymous={muc_nonanonymous}, membersonly={muc_membersonly}"
            )

    # Header signal handlers

    def _handle_call_request(self, call_type: str):
        """Handle call request from header (audio or video)."""
        if not self.current_jid or not self.current_account_id:
            logger.warning("Cannot start call: no conversation selected")
            return

        # Get account
        account = self.account_manager.get_account(self.current_account_id)
        if not account:
            logger.error(f"Cannot start call: account {self.current_account_id} not found")
            return

        # Start call
        logger.info(f"Starting {call_type} call to {self.current_jid}")

        async def start_call_with_error_handling():
            try:
                await account.start_call(self.current_jid, [call_type])
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Call failed: {error_msg}")

                # Show user-friendly error message
                def show_error_dialog():
                    if "service-unavailable" in error_msg or "No module is handling" in error_msg:
                        QMessageBox.warning(
                            self,
                            "Call Not Supported",
                            f"The recipient's client doesn't support {call_type} calls.\n\n"
                            f"Contact: {self.current_jid}\n\n"
                            f"They may need to:\n"
                            f"• Use a desktop XMPP client with Jingle support\n"
                            f"• Enable call functionality in their client settings"
                        )
                    else:
                        QMessageBox.critical(
                            self,
                            "Call Failed",
                            f"Could not start call to {self.current_jid}\n\n"
                            f"Error: {error_msg}"
                        )

                # Defer to Qt event loop
                QTimer.singleShot(0, show_error_dialog)

        # Defer async task creation to Qt event loop
        def schedule_call():
            asyncio.create_task(start_call_with_error_handling())
        QTimer.singleShot(0, schedule_call)

    def _handle_encryption_toggle(self, enabled: bool):
        """Handle encryption toggle from header."""
        # Update local state
        self.encryption_enabled = enabled
        logger.debug(f"Encryption toggled: {enabled}")

        # Update input placeholder to reflect new encryption state
        self._update_input_placeholder()

    def _handle_info_click(self):
        """Handle info button click from header - opens contact details or MUC settings."""
        if not self.current_account_id or not self.current_jid:
            return

        logger.debug(f"Info button clicked for {self.current_jid} (is_muc={self.current_is_muc})")

        if self.current_is_muc:
            # Open MUC settings/details dialog
            # Use .show() instead of .exec() to avoid blocking asyncio event loop
            logger.debug("Opening MUC details dialog for room")
            from ..muc_details_dialog import MUCDetailsDialog

            dialog = MUCDetailsDialog(
                account_id=self.current_account_id,
                room_jid=self.current_jid,
                parent=self
            )

            # Auto-delete when closed (Qt handles cleanup)
            dialog.setAttribute(Qt.WA_DeleteOnClose)

            # Show non-blocking (allows asyncio to continue processing XMPP)
            dialog.show()
        else:
            # Open contact details dialog (same as roster right-click "View Details")
            # Use .show() instead of .exec() to avoid blocking asyncio event loop
            logger.debug("Opening contact details dialog")
            from ..contact_details_dialog import ContactDetailsDialog

            dialog = ContactDetailsDialog(
                account_id=self.current_account_id,
                jid=self.current_jid,
                parent=self
            )

            # Connect signals to main window handlers
            main_window = self.window()
            if main_window and hasattr(main_window, '_on_contact_saved'):
                dialog.contact_saved.connect(main_window._on_contact_saved)
            if main_window and hasattr(main_window, '_on_block_status_changed'):
                dialog.block_status_changed.connect(main_window._on_block_status_changed)

            # Auto-delete when closed (Qt handles cleanup)
            dialog.setAttribute(Qt.WA_DeleteOnClose)

            # Show non-blocking (allows asyncio to continue processing XMPP)
            dialog.show()

    def _set_input_enabled_from_blocked(self, is_blocked: bool):
        """Update input field enabled state based on blocked status (inverse)."""
        self._set_input_enabled(not is_blocked)

    # Public methods (called from main_window.py)

    def update_typing_indicator(self, state: str):
        """
        Update typing indicator in chat header (forwarded to header widget).

        Args:
            state: Chat state ('active', 'composing', 'paused', 'inactive', 'gone')
        """
        self.header.update_typing_indicator(state)

    def update_blocked_status(self, is_blocked: bool):
        """
        Update blocked status in chat header and input field (forwarded to header widget).

        Args:
            is_blocked: True if contact is blocked, False otherwise
        """
        # Update header
        self.header.update_blocked_status(is_blocked)
        # Update input field state
        self._set_input_enabled(not is_blocked)

    def _handle_search_result(self, message_id, content_item_id):
        """Handle search result click - load around message."""
        logger.info(f"Search result handler: message_id={message_id}, content_item_id={content_item_id}")
        self.message_widget.load_around_message(content_item_id, context=50)

    def _handle_add_account_request(self):
        """Handle Add Account button click from welcome view."""
        logger.info("Add Account button clicked from welcome view")
        self.add_account_requested.emit()

    def _handle_create_account_request(self):
        """Handle Create Account button click from welcome view."""
        logger.info("Create Account button clicked from welcome view")
        self.create_account_requested.emit()

    # Other handlers

    def _show_file_attachment_indicator(self, file_path):
        """
        Show file attachment indicator in input field (red paperclip + filename).

        Args:
            file_path: Path to attached file
        """
        import os
        from PySide6.QtGui import QColor, QTextCharFormat

        filename = os.path.basename(file_path)

        # Set text with red color using QTextCursor formatting
        self.input_field.clear()
        cursor = self.input_field.textCursor()

        # Create attachment format (red, no bold)
        attachment_format = QTextCharFormat()
        attachment_format.setForeground(QColor("#ff4444"))  # Red

        # Insert formatted text
        cursor.insertText(f"📎 {filename}", attachment_format)

        # Move cursor to end (don't select/highlight)
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_field.setTextCursor(cursor)

        # Disable input - XMPP doesn't support text with attachments
        self.input_field.setReadOnly(True)

    def _on_image_pasted(self, qimage, mime_data):
        """
        Handle image pasted from clipboard.

        Creates a temporary file from the pasted image, strips EXIF data
        (Qt's QImage.save() doesn't preserve EXIF), and shows attachment UI.
        Preserves original format (PNG, JPEG, WebP, etc.) based on clipboard MIME type.

        Args:
            qimage: QImage from clipboard
            mime_data: QMimeData containing format information
        """
        if not self.current_account_id or not self.current_jid:
            logger.warning("Cannot paste image: no active conversation")
            return

        # Clear any previous file selection first
        if self.selected_file_path:
            self._clear_file_selection()

        import os
        from datetime import datetime
        from ...utils.paths import get_paths, _mkdir_secure

        # Detect image format from MIME data
        # Common formats: image/png, image/jpeg, image/webp, image/bmp, image/gif
        format_name = "PNG"  # Default fallback
        file_ext = ".png"

        for fmt in mime_data.formats():
            fmt_lower = fmt.lower()
            if fmt_lower == "image/png" or fmt_lower == "png":
                format_name = "PNG"
                file_ext = ".png"
                break
            elif fmt_lower == "image/jpeg" or fmt_lower == "jpeg" or fmt_lower == "image/jpg":
                format_name = "JPEG"
                file_ext = ".jpg"
                break
            elif fmt_lower == "image/webp" or fmt_lower == "webp":
                format_name = "WEBP"
                file_ext = ".webp"
                break
            elif fmt_lower == "image/bmp" or fmt_lower == "bmp":
                format_name = "BMP"
                file_ext = ".bmp"
                break
            elif fmt_lower == "image/gif" or fmt_lower == "gif":
                # GIF is animated, convert to PNG for static image
                format_name = "PNG"
                file_ext = ".png"
                break

        logger.debug(f"Detected clipboard image format: {format_name} (extension: {file_ext})")

        # Save directly to permanent attachments folder (same pattern as received files)
        paths = get_paths()
        attachments_base = paths.data_dir / 'attachments'
        jid_dir = attachments_base / str(self.current_account_id) / self.current_jid
        _mkdir_secure(jid_dir, parents=True)

        # Generate timestamped filename
        timestamp_str = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filename = f'{timestamp_str}{file_ext}'
        permanent_path = str(jid_dir / filename)

        # Save image to permanent location (Qt's save() automatically strips EXIF data for all formats)
        # Quality parameter for lossy formats (JPEG): 95 = high quality with minimal compression
        save_quality = 95 if format_name == "JPEG" else -1  # -1 = use Qt default for lossless formats

        if not qimage.save(permanent_path, format_name, save_quality):
            logger.error(f"Failed to save pasted image to {permanent_path}")
            try:
                os.unlink(permanent_path)
            except:
                pass
            return

        logger.info(f"Pasted image saved to permanent location: {permanent_path} (format={format_name}, EXIF data stripped)")

        # Set as selected file and show indicator (reuse existing UI logic)
        self.selected_file_path = permanent_path
        self._show_file_attachment_indicator(permanent_path)

        logger.debug(f"Image paste ready to send: {permanent_path}")

    def _on_attach_file_clicked(self):
        """Handle file attachment button click."""
        if not self.current_account_id or not self.current_jid:
            logger.warning("Cannot attach file: no active conversation")
            return

        # Open file dialog
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select File to Send",
            "",  # Start in user's home directory
            "All Files (*)"
        )

        if file_path:
            self.selected_file_path = file_path
            self._show_file_attachment_indicator(file_path)
            logger.debug(f"File selected: {file_path}")

    def _clear_file_selection(self):
        """Clear selected file and reset UI."""
        from PySide6.QtGui import QTextCharFormat

        self.selected_file_path = None
        self.input_field.clear()

        # Reset text format to default (remove red color)
        cursor = self.input_field.textCursor()
        default_format = QTextCharFormat()
        cursor.setCharFormat(default_format)
        self.input_field.setTextCursor(cursor)

        self.input_field.setReadOnly(False)  # Re-enable input
        self._update_input_placeholder()

    def _on_send_clicked(self):
        """Handle Send button click (or Enter key) - sends new message, reply, or saves edit."""
        # Delegate to message actions manager
        self.message_actions.on_send_clicked(self.current_account_id, self.current_jid)

    def _on_emoji_button_clicked(self):
        """Handle emoji button click - show emoji picker to insert into input field."""
        from ..dialogs.emoji_picker_dialog import show_emoji_picker_dialog

        if not self.current_account_id or not self.current_jid:
            return

        # Show emoji picker (pure UI - returns emoji or None)
        emoji = show_emoji_picker_dialog(self)

        if emoji:
            # Insert emoji at cursor position in input field
            cursor = self.input_field.textCursor()
            cursor.insertText(emoji)

            # Set focus back to input field
            self.input_field.setFocus()

    def _adjust_input_height(self):
        """Auto-resize input field based on content (StackOverflow approach)."""
        doc = self.input_field.document()
        margins = self.input_field.contentsMargins()

        # Get document height
        doc_height = doc.size().height()

        # Calculate total: document height + top margin + bottom margin
        new_height = int(doc_height + margins.top() + margins.bottom())

        # Clamp to reasonable bounds: min 36px, max 150px
        new_height = max(36, min(new_height, 150))

        # Only update if changed
        if self.input_field.height() != new_height:
            self.input_field.setFixedHeight(new_height)

            # Also adjust the frame height to wrap tightly around input
            # Frame height = input height + frame layout margins (8px top + 8px bottom)
            frame_height = new_height + 16
            self.input_frame.setFixedHeight(frame_height)

    def track_sent_message(self, message_id: str, body: str, encrypted: bool):
        """
        Track a sent message for potential editing.
        Call this after successfully sending a message.

        Args:
            message_id: XMPP message ID (stanza-id, origin-id, or server-id)
            body: Message text
            encrypted: Whether message was OMEMO encrypted
        """
        # Delegate to message actions manager
        self.message_actions.track_sent_message(self.current_account_id, self.current_jid, message_id, body, encrypted)

    def _load_last_message_for_editing(self):
        """Load last sent message into input field for editing (arrow-up handler)."""
        # Delegate to message actions manager
        self.message_actions.load_last_message_for_editing(self.current_account_id, self.current_jid)

    def _cancel_editing(self):
        """Cancel editing mode and restore normal send mode."""
        # Delegate to message actions manager
        self.message_actions.cancel_editing()

    def _start_reply(self, message_id: str, quoted_body: str):
        """
        Start composing a reply to a message.

        Args:
            message_id: XMPP message ID being replied to
            quoted_body: Text of the original message
        """
        # Delegate to message actions manager
        self.message_actions.start_reply(message_id, quoted_body)

    def _cancel_reply(self):
        """Cancel reply mode and restore normal send mode."""
        # Delegate to message actions manager
        self.message_actions.cancel_reply()

    def _edit_message_from_context(self, message_id: str, body: str, encrypted: bool):
        """
        Load a message for editing from right-click context menu.

        Args:
            message_id: XMPP message ID
            body: Message text
            encrypted: Whether message was encrypted
        """
        # Delegate to message actions manager
        self.message_actions.edit_message_from_context(message_id, body, encrypted)

    def _format_selection(self, prefix: str, suffix: str):
        """
        Wrap selected text with formatting markers (e.g., **bold**, __italic__).

        Args:
            prefix: Text to insert before selection
            suffix: Text to insert after selection
        """
        # Delegate to message actions manager
        self.message_actions.format_selection(prefix, suffix)

    def _handle_voice_request(self):
        """Handle voice request button click (user is visitor in moderated room)."""
        if not self.current_account_id or not self.current_jid or not self.current_is_muc:
            logger.warning("Voice request clicked but not in MUC context")
            return

        account = self.account_manager.get_account(self.current_account_id)
        if not account or not account.is_connected():
            logger.warning("Voice request clicked but account not connected")
            return

        # Request voice (returns dict with success, message, cooldown_remaining)
        result = account.muc.request_voice(self.current_jid)
        logger.info(f"Voice request result for {self.current_jid}: {result}")

        # Update overlay text based on result
        if result['success']:
            # Request sent successfully
            self.input_field.update_visitor_overlay_text(
                "Voice request sent to moderators. Waiting for approval..."
            )
        else:
            # Request failed or throttled
            if result['cooldown_remaining'] > 0:
                # Throttled - show cooldown
                minutes = result['cooldown_remaining'] // 60
                self.input_field.update_visitor_overlay_text(
                    f"Voice request already sent. You can request again in {minutes} minute(s)."
                )
            else:
                # Other error
                self.input_field.update_visitor_overlay_text(
                    f"Request failed: {result['message']}"
                )

    def _send_composing_state(self):
        """Send 'composing' chat state to peer (XEP-0085)."""
        if not self.current_account_id or not self.current_jid:
            return

        account = self.account_manager.get_account(self.current_account_id)
        if not account or not account.is_connected():
            return

        # Check if typing indicators are enabled for this conversation
        if self.current_conversation_id:
            conv = self.db.fetchone("""
                SELECT send_typing FROM conversation WHERE id = ?
            """, (self.current_conversation_id,))

            if conv and conv['send_typing'] == 0:
                logger.debug(f"Typing indicators disabled for {self.current_jid}, skipping 'composing' state")
                return

        try:
            account.client.send_chat_state(self.current_jid, 'composing')
            logger.debug(f"Sent 'composing' state to {self.current_jid}")
        except Exception as e:
            logger.error(f"Failed to send composing state: {e}")

    def _send_paused_state(self):
        """Send 'paused' chat state to peer (XEP-0085)."""
        if not self.current_account_id or not self.current_jid:
            return

        account = self.account_manager.get_account(self.current_account_id)
        if not account or not account.is_connected():
            return

        # Check if typing indicators are enabled for this conversation
        if self.current_conversation_id:
            conv = self.db.fetchone("""
                SELECT send_typing FROM conversation WHERE id = ?
            """, (self.current_conversation_id,))

            if conv and conv['send_typing'] == 0:
                logger.debug(f"Typing indicators disabled for {self.current_jid}, skipping 'paused' state")
                return

        try:
            account.client.send_chat_state(self.current_jid, 'paused')
            logger.debug(f"Sent 'paused' state to {self.current_jid}")
        except Exception as e:
            logger.error(f"Failed to send paused state: {e}")

    def _send_active_state(self):
        """Send 'active' chat state to peer (XEP-0085)."""
        if not self.current_account_id or not self.current_jid:
            return

        account = self.account_manager.get_account(self.current_account_id)
        if not account or not account.is_connected():
            return

        # Check if typing indicators are enabled for this conversation
        if self.current_conversation_id:
            conv = self.db.fetchone("""
                SELECT send_typing FROM conversation WHERE id = ?
            """, (self.current_conversation_id,))

            if conv and conv['send_typing'] == 0:
                logger.debug(f"Typing indicators disabled for {self.current_jid}, skipping 'active' state")
                return

        try:
            account.client.send_chat_state(self.current_jid, 'active')
            logger.debug(f"Sent 'active' state to {self.current_jid}")
        except Exception as e:
            logger.error(f"Failed to send active state: {e}")

    def eventFilter(self, obj, event):
        """Handle keyboard shortcuts in input field."""
        if obj == self.input_field and event.type() == event.Type.KeyPress:
            # Escape: Cancel file attachment, editing, or reply mode
            if event.key() == Qt.Key_Escape:
                if self.selected_file_path:
                    self._clear_file_selection()
                    return True  # Consume the event
                elif self.message_actions.editing_message_id:
                    self._cancel_editing()
                    return True  # Consume the event
                elif self.message_actions.replying_to_message_id:
                    self._cancel_reply()
                    return True  # Consume the event

            # Backspace: Cancel file attachment if input is read-only (attachment mode)
            if event.key() == Qt.Key_Backspace:
                if self.selected_file_path and self.input_field.isReadOnly():
                    self._clear_file_selection()
                    return True  # Consume the event

            # Ctrl+B: Bold (surround with *)
            if event.key() == Qt.Key_B and event.modifiers() & Qt.ControlModifier:
                self._format_selection('*', '*')
                return True  # Consume the event

            # Ctrl+I: Italic (surround with _)
            if event.key() == Qt.Key_I and event.modifiers() & Qt.ControlModifier:
                self._format_selection('_', '_')
                return True  # Consume the event

            # Ctrl+Shift+X: Strikethrough (surround with ~)
            # IMPORTANT: Check exact modifiers to avoid conflicting with Ctrl+X (cut)
            if event.key() == Qt.Key_X and event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                self._format_selection('~', '~')
                return True  # Consume the event

            # Enter key handling
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                # Check if Shift is held
                if event.modifiers() & Qt.ShiftModifier:
                    # Shift+Enter: insert newline (default behavior)
                    return False
                else:
                    # Enter alone: send message (or save edit)
                    self._on_send_clicked()
                    return True  # Consume the event

        return super().eventFilter(obj, event)

    def _is_near_bottom(self, threshold=100):
        """Check if scroll position is near bottom (delegates to ScrollManager)."""
        return self.scroll_manager._is_near_bottom(threshold)

    def resizeEvent(self, event):
        """Handle resize to reposition scroll button."""
        super().resizeEvent(event)
        if hasattr(self, 'scroll_manager'):
            self.scroll_manager.on_resize()

    def _show_context_menu(self, position):
        """Show context menu for message items (delegates to ContextMenuManager)."""
        self.context_menu_manager.show_message_context_menu(
            position,
            self.message_area,
            self.current_account_id,
            self.current_jid,
            self.message_actions.last_sent_messages
        )

    def _show_input_context_menu(self, position):
        """Show context menu for input field (delegates to ContextMenuManager)."""
        self.context_menu_manager.show_input_context_menu(
            position,
            self.input_field,
            self.spell_highlighter
        )

