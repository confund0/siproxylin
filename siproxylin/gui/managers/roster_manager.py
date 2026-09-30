"""
RosterManager - Manages roster updates, presence, and contact display.

Extracted from MainWindow to improve maintainability.
"""

import logging
from PySide6.QtCore import Slot


logger = logging.getLogger('siproxylin.roster_manager')


class RosterManager:
    """
    Manages roster updates, presence changes, and contact display updates.

    Responsibilities:
    - Handle roster updates and refresh UI
    - Update presence indicators
    - Handle chat state changes (typing indicators)
    - Update contact display names (roster name, nickname, JID priority)
    - Handle avatar updates
    - Process incoming messages for UI updates
    - Connect roster signals from accounts
    """

    def __init__(self, main_window):
        """
        Initialize RosterManager.

        Args:
            main_window: MainWindow instance (for accessing widgets and services)
        """
        self.main_window = main_window
        self.account_manager = main_window.account_manager
        self.contact_list = main_window.contact_list
        self.chat_view = main_window.chat_view
        self.notification_manager = main_window.notification_manager

        logger.debug("RosterManager initialized")

    def connect_account_signals(self, account):
        """
        Connect roster-related signals from an account.

        Args:
            account: XMPPAccount instance
        """
        account.roster_updated.connect(self.on_roster_updated)
        account.message_received.connect(self.on_message_received)
        account.chat_state_changed.connect(self.on_chat_state_changed)
        account.presence_changed.connect(self.on_presence_changed)
        account.nickname_updated.connect(self.on_nickname_updated)
        account.avatar_updated.connect(self.on_avatar_updated)
        logger.debug(f"Connected roster signals for account {account.account_id}")

    def on_roster_updated(self, account_id: int):
        """
        Handle roster update signal from account.

        Args:
            account_id: Account ID whose roster was updated
        """
        logger.debug(f"Roster updated for account {account_id}, refreshing contact list")
        self.contact_list.refresh()

        # If the currently open chat is from this account, update its header display name
        if self.chat_view.current_account_id == account_id and self.chat_view.current_jid:
            jid = self.chat_view.current_jid
            logger.debug(f"Updating chat header display name for open chat: {jid}")
            # Use unified display name refresh (applies 3-source priority)
            self.refresh_contact_display_name(account_id, jid)

            # Update encryption button visibility for MUC rooms
            # (disco_cache has fresh data from config changes via status codes)
            if self.chat_view.current_is_muc:
                self.chat_view._update_encryption_button_visibility()

    @Slot(int, str, bool)
    def on_message_received(self, account_id: int, from_jid: str, is_marker: bool = False):
        """
        Handle incoming message signal from account.

        Args:
            account_id: Account ID
            from_jid: Sender JID
            is_marker: True if marker/receipt update, False if actual new message
        """
        event_type = "marker/receipt" if is_marker else "message"
        logger.debug(f"{event_type.capitalize()} received from {from_jid} on account {account_id}")

        # Check if this message is for the currently open chat
        is_current_chat = (self.chat_view.current_account_id == account_id and
                          self.chat_view.current_jid == from_jid)

        # The app window is active and not minimized (the user can see the chat)
        window_active = self.main_window.isActiveWindow() and not self.main_window.isMinimized()

        if is_current_chat:
            # Refresh the chat view
            if is_marker:
                # Marker/receipt and file state updates: delayed refresh, many updates give one refresh
                self.chat_view.refresh_later()
                logger.debug(f"Chat view refresh scheduled for {event_type}")
            else:
                # Displayed markers only when the user sees the newest messages (see mark_read_if_seen)
                self.chat_view.refresh()
                logger.debug(f"Chat view refreshed for {event_type}")

        # Check if this is a new conversation (not currently in chat list)
        # Only refresh roster for new conversations to avoid expensive reloads
        if not is_marker:
            item = self.contact_list._find_contact_item(account_id, from_jid)
            if item is None:
                # New conversation - contact not in chat list yet
                logger.debug(f"New conversation from {from_jid}, refreshing chat list")
                self.contact_list.load_roster()

        # Update unread indicators in contact list (always, even if chat is open)
        self.contact_list.update_unread_indicators(account_id, from_jid)

        # Update status bar stats for actual new messages (not marker/receipt updates)
        if not is_marker:
            self.main_window._update_status_bar_stats()

        # Send OS notification for actual new messages (not for markers).
        # An open chat skips it only while the app window is active and not minimized.
        if not is_marker and not (is_current_chat and window_active):
            self.notification_manager.send_message_notification(account_id, from_jid)

    def on_chat_state_changed(self, account_id: int, from_jid: str, state: str):
        """
        Handle chat state change (typing indicators).

        Args:
            account_id: Account ID
            from_jid: Contact JID
            state: Chat state ('active', 'composing', 'paused', 'inactive', 'gone')
        """
        logger.debug(f"Chat state from {from_jid}: {state}")

        # Update typing indicator in contact list
        self.contact_list.update_typing_indicator(account_id, from_jid, state)

        # Also update typing indicator in chat header if this is the current chat
        if (self.chat_view.current_account_id == account_id and
            self.chat_view.current_jid == from_jid):
            self.chat_view.update_typing_indicator(state)

    def on_presence_changed(self, account_id: int, jid: str, presence: str):
        """
        Handle presence change (contact status updates).

        Args:
            account_id: Account ID
            jid: Contact JID
            presence: Presence show value ('available', 'away', 'xa', 'dnd', 'unavailable')
        """
        logger.debug(f"Presence change for {jid}: {presence}")

        # Update presence indicator in contact list (event-driven, not polling!)
        self.contact_list.update_presence_single(account_id, jid, presence)

    def refresh_contact_display_name(self, account_id: int, jid: str):
        """
        Unified method to refresh display name for a contact.

        Applies 3-source priority: roster.name → nickname → JID
        Updates contact list and chat header if applicable.

        Args:
            account_id: Account ID
            jid: Contact bare JID
        """
        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            return

        # Get roster name (highest priority)
        roster_name = None
        if account.client:
            roster = account.client.client_roster
            if jid in roster:
                try:
                    roster_name = roster[jid]['name'] or None
                except (KeyError, TypeError):
                    pass

        # Get display name using 3-source priority
        display_name = account.get_contact_display_name(jid, roster_name=roster_name)

        # Refresh contact list (for now, reload entire roster - can optimize later)
        self.contact_list.load_roster()

        # If this chat is open, update header
        if (self.chat_view.current_account_id == account_id and
            self.chat_view.current_jid == jid):
            logger.debug(f"Updating chat header display name for open chat: {jid}")
            base_name = f"💬 {display_name}"
            self.chat_view.header.update_display_name(base_name)
            self.chat_view.header.roster_name = roster_name

    @Slot(int, str)
    def on_nickname_updated(self, account_id: int, jid: str, nickname: str):
        """
        Handle nickname update signal from account (XEP-0172).

        Args:
            account_id: Account ID
            jid: JID whose nickname was updated
            nickname: New nickname (empty string if cleared)
        """
        logger.debug(f"Nickname updated for {jid} on account {account_id}: {nickname if nickname else '(cleared)'}")
        # Use unified display name refresh
        self.refresh_contact_display_name(account_id, jid)

    def on_avatar_updated(self, account_id: int, jid: str):
        """
        Handle avatar update signal from account.

        Args:
            account_id: Account ID
            jid: JID whose avatar was updated
        """
        logger.debug(f"Avatar updated for {jid} on account {account_id}")

        # If this is the currently open chat, refresh the avatar
        if (self.chat_view.current_account_id == account_id and
            self.chat_view.current_jid == jid):
            logger.debug(f"Refreshing avatar for current chat: {jid}")
            # Invalidate cache for this JID
            from ...utils.avatar import get_avatar_cache
            get_avatar_cache().invalidate(jid)
            # Refresh avatar display in header
            self.chat_view.header._update_avatar()
