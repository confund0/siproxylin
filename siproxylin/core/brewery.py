"""
Account Brewery for DRUNK-XMPP-GUI.

The brewery where XMPP accounts are brewed and managed.
Manages multiple XMPP connections using drunk-xmpp.py library.
Handles connection state, reconnection, and message routing.
"""

import sys
import logging
import base64
import asyncio
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, Signal

# Import from refactored drunk_xmpp package
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from drunk_xmpp import DrunkXMPP, MessageMetadata
try:
    from drunk_call_hook import CallBridge
    from drunk_call_hook.protocol.jingle import JingleAdapter
    CALLS_AVAILABLE = True
except ImportError as e:
    CALLS_AVAILABLE = False
    # Detailed logging happens in CallBarrel where the imports are actually used
    # This is just a module-level check for delegation methods
    logger.warning(f"Call dependencies unavailable: {e} (calls will be disabled)")

from ..db.database import get_db
from ..db.omemo_storage import OMEMOStorageDB
from ..utils import setup_account_logger, get_account_logger, generate_resource
from ..utils.paths import get_paths
from ..services.receipt_handler import ReceiptHandler
from ..services.message_retry import get_retry_handler
from .barrels.connection import ConnectionBarrel
from .barrels.presence import PresenceBarrel
from .barrels.omemo import OmemoBarrel
from .barrels.avatars import AvatarBarrel
from .barrels.files import FileBarrel
from .barrels.messages import MessageBarrel
from .barrels.calls import CallBarrel
from .barrels.muc import MucBarrel


logger = logging.getLogger('siproxylin.account_manager')


class XMPPAccount(QObject):
    """
    Wrapper for a single XMPP account connection.
    """

    # Signals for connection state changes
    connection_state_changed = Signal(int, str)  # (account_id, state: 'connecting'|'connected'|'disconnected'|'error')
    connection_error = Signal(int, str)  # (account_id, error_message)
    roster_updated = Signal(int)  # (account_id)
    message_received = Signal(int, str, bool)  # (account_id, from_jid, is_marker) - new message or marker/receipt update
    chat_state_changed = Signal(int, str, str)  # (account_id, from_jid, state) - typing indicators
    presence_changed = Signal(int, str, str)  # (account_id, jid, presence) - contact presence changed
    muc_invite_received = Signal(int, str, str, str, str)  # (account_id, room_jid, inviter_jid, reason, password)
    muc_join_error = Signal(str, str, str)  # (room_jid, friendly_message, server_error_text)
    muc_join_success = Signal(int, str)  # (account_id, room_jid)
    muc_role_changed = Signal(int, str, str, str)  # (account_id, room_jid, old_role, new_role)
    avatar_updated = Signal(int, str)  # (account_id, jid) - avatar fetched/updated
    nickname_updated = Signal(int, str, str)  # (account_id, jid, nickname) - contact nickname updated (XEP-0172)
    subscription_request_received = Signal(int, str)  # (account_id, from_jid) - incoming subscription request
    subscription_changed = Signal(int, str, str)  # (account_id, from_jid, change_type) - subscription state changed

    # Message retry signals (Phase 4)
    retry_started = Signal(int)  # (account_id)
    retry_completed = Signal(int, dict)  # (account_id, stats)
    retry_failed = Signal(int, str)  # (account_id, error_msg)

    # Call signals (DrunkCALL integration)
    call_incoming = Signal(int, str, str, list)  # (account_id, session_id, from_jid, media_types)
    call_initiated = Signal(int, str, str, list)  # (account_id, session_id, peer_jid, media_types)
    call_accepted = Signal(int, str)  # (account_id, session_id)
    call_terminated = Signal(int, str, str, str)  # (account_id, session_id, reason, peer_jid)
    call_state_changed = Signal(int, str, str)  # (account_id, session_id, state)

    def __init__(self, account_id: int, account_data: dict):
        """
        Initialize XMPP account wrapper.

        Args:
            account_id: Account ID from database
            account_data: Account settings from database
        """
        super().__init__()
        self.account_id = account_id
        self.account_data = account_data
        self.db = get_db()

        # Receipt handler for database updates
        self.receipt_handler = ReceiptHandler(self.db)

        # Message retry handler (global singleton)
        self.retry_handler = get_retry_handler()

        # Setup logging for this account (app log only, XML is global)
        log_level = account_data.get('log_level', 'INFO')
        app_log_enabled = bool(account_data.get('log_app_enabled', 1))

        self.app_logger = setup_account_logger(
            account_id=account_id,
            log_level=log_level,
            app_log_enabled=app_log_enabled
        )

        # Nickname cache (XEP-0172: User Nickname) - in-memory only
        self.contact_nicknames: Dict[str, str] = {}

        # Create signal dictionary for barrels
        self._signals = {
            'connection_state_changed': self.connection_state_changed,
            'connection_error': self.connection_error,
            'roster_updated': self.roster_updated,
            'presence_changed': self.presence_changed,
            'subscription_request_received': self.subscription_request_received,
            'subscription_changed': self.subscription_changed,
            'avatar_updated': self.avatar_updated,
            'nickname_updated': self.nickname_updated,
            'message_received': self.message_received,
            'chat_state_changed': self.chat_state_changed,
            'muc_join_error': self.muc_join_error,
            'muc_role_changed': self.muc_role_changed,
            'muc_invite_received': self.muc_invite_received,
            'call_incoming': self.call_incoming,
            'call_initiated': self.call_initiated,
            'call_accepted': self.call_accepted,
            'call_terminated': self.call_terminated,
            'call_state_changed': self.call_state_changed,
        }

        # Initialize ConnectionBarrel (handles connection/disconnection)
        self.connection = ConnectionBarrel(
            account_id=self.account_id,
            account_data=account_data,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals
        )

        # Initialize barrels - all start with client=None, set later in on_session_start

        # Initialize PresenceBarrel (handles roster and presence)
        self.presence = PresenceBarrel(
            account_id=self.account_id,
            client=None,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals
        )

        # Initialize OmemoBarrel (handles OMEMO device management)
        self.omemo = OmemoBarrel(
            account_id=self.account_id,
            client=None,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals
        )

        # Initialize AvatarBarrel (handles avatar fetching and storage)
        self.avatars = AvatarBarrel(
            account_id=self.account_id,
            client=None,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals
        )

        # Initialize FileBarrel (handles file transfers and attachments)
        self.files = FileBarrel(
            account_id=self.account_id,
            client=None,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals
        )

        # Initialize MessageBarrel (handles message operations)
        self.messages = MessageBarrel(
            account_id=self.account_id,
            client=None,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals,
            receipt_handler=self.receipt_handler,
            files_barrel=self.files  # Pass FileBarrel reference for file handling
        )

        # Initialize CallBarrel (handles audio/video calls)
        self.calls = CallBarrel(
            account_id=self.account_id,
            client=None,
            app_logger=self.app_logger,
            signals=self._signals,
            account_data=account_data  # Pass for proxy settings
        )

        # Initialize MucBarrel (handles MUC operations)
        self.muc = MucBarrel(
            account_id=self.account_id,
            client=None,
            db=self.db,
            logger=self.app_logger,
            signals=self._signals,
            account_data=self.account_data
        )

        if self.app_logger:
            self.app_logger.info(f"XMPP account wrapper created for {account_data['bare_jid']}")

    # Property aliases for backwards compatibility (delegate to ConnectionBarrel)
    @property
    def client(self):
        """DrunkXMPP client instance."""
        return self.connection.client

    @client.setter
    def client(self, value):
        self.connection.client = value

    @property
    def connected(self):
        """Connection status flag."""
        return self.connection.connected

    @connected.setter
    def connected(self, value):
        self.connection.connected = value

    @property
    def server_version(self):
        """Server version info (XEP-0092)."""
        return self.connection.server_version

    @property
    def server_features(self):
        """Server features info (XEP-0030)."""
        return self.connection.server_features

    def connect(self):
        """Connect to XMPP server - delegates to ConnectionBarrel."""
        # Prepare callbacks for DrunkXMPP
        callbacks = {
            'on_message_callback': self.messages._on_message,
            'on_private_message_callback': self.messages._on_private_message,
            'on_message_error_callback': self.messages._on_message_error,
            'on_receipt_received_callback': self.messages._on_receipt_received,
            'on_marker_received_callback': self.messages._on_marker_received,
            'on_server_ack_callback': self.messages._on_server_ack,
            'on_chat_state_callback': self.messages._on_chat_state,
            'on_presence_changed_callback': self._on_presence_changed,
            'on_bookmarks_received_callback': self.muc.sync_bookmarks,
            'on_muc_invite_callback': self.muc.on_muc_invite,
            'on_muc_joined_callback': self._on_muc_joined,
            'on_muc_join_error_callback': self.muc.on_muc_join_error,
            'on_muc_role_changed_callback': self._on_muc_role_changed,
            'on_room_config_changed_callback': self.muc.on_room_config_changed,
            'on_message_correction_callback': self.messages._on_message_correction,
            'on_avatar_update_callback': self.avatars.on_avatar_update,
            'on_nickname_update_callback': self._on_nickname_update,
            'on_reaction_callback': self.messages._on_reaction,
            'on_subscription_request_callback': self._on_subscription_request,
            'on_subscription_changed_callback': self._on_subscription_changed,
            'on_roster_update': self._on_roster_update,
            'on_session_start': self._on_session_start_autojoin,
            'on_session_resumed': self._on_session_resumed,
            'on_disconnected': self._on_disconnected_event,
            'on_failed_auth': self._on_failed_auth_event,
            'on_connection_failed': self._on_connection_failed_event,
        }
        # Delegate to ConnectionBarrel
        self.connection.connect(callbacks)

    def disconnect(self):
        """Disconnect from XMPP server - delegates to ConnectionBarrel."""
        call_bridge = self.calls.call_bridge if hasattr(self, 'calls') else None
        self.connection.disconnect(call_bridge=call_bridge)

    def is_connected(self) -> bool:
        """Check if connected to XMPP server."""
        return self.connection.is_connected()

    def reload_and_reconnect(self):
        """
        Reload account settings from database and reconnect if currently connected.

        Handles the async disconnect → wait → connect flow properly using signals.
        Called when user saves account settings via account dialog.
        """
        from PySide6.QtCore import Qt

        # Reload settings from database first
        account_data = self.db.fetchone("SELECT * FROM account WHERE id = ?", (self.account_id,))
        if not account_data:
            if self.app_logger:
                self.app_logger.error("Failed to reload account settings - account not found")
            return

        # Update account_data dict in-place (maintains shared reference with barrels)
        self.account_data.clear()
        self.account_data.update(dict(account_data))

        # If connected, disconnect and wait for event before reconnecting
        if self.connected:
            if self.app_logger:
                self.app_logger.info("Reloading account settings - will disconnect and reconnect")

            # One-shot connection: when disconnected, then reconnect
            def on_disconnect_complete(account_id, state):
                if account_id == self.account_id and state == 'disconnected':
                    if self.app_logger:
                        self.app_logger.debug("Disconnect complete, reconnecting with new settings")
                    self.connect()

            self.connection_state_changed.connect(on_disconnect_complete, Qt.ConnectionType.SingleShotConnection)
            self.disconnect()
        else:
            if self.app_logger:
                self.app_logger.debug("Account settings reloaded (was not connected)")

    # =========================================================================
    # MUC Management (delegates to MucBarrel)
    # =========================================================================

    async def add_and_join_room(self, room_jid: str, nick: str, password: str = None):
        """Add and join room - delegates to MucBarrel."""
        return await self.muc.add_and_join_room(room_jid, nick, password)

    def get_contact_presence(self, jid: str) -> str:
        """Get presence for a contact - delegates to PresenceBarrel."""
        return self.presence.get_contact_presence(jid)

    # =========================================================================
    # Message Management (delegates to MessageBarrel)
    # =========================================================================

    async def send_message(self, to_jid: str, message: str, encrypted: bool = False):
        """Send message - delegates to MessageBarrel."""
        return await self.messages.send_message(to_jid, message, encrypted)

    def send_reaction(self, to_jid: str, message_id: str, emoji: str):
        """Send reaction - delegates to MessageBarrel."""
        self.messages.send_reaction(to_jid, message_id, emoji)

    def remove_reaction(self, to_jid: str, message_id: str):
        """Remove reaction - delegates to MessageBarrel."""
        self.messages.remove_reaction(to_jid, message_id)

    async def request_subscription(self, jid: str):
        """Request presence subscription - delegates to PresenceBarrel."""
        await self.presence.request_subscription(jid)

    async def approve_subscription(self, jid: str):
        """Approve subscription request - delegates to PresenceBarrel."""
        await self.presence.approve_subscription(jid)

    async def deny_subscription(self, jid: str):
        """Deny subscription request - delegates to PresenceBarrel."""
        await self.presence.deny_subscription(jid)

    async def cancel_subscription(self, jid: str):
        """Cancel subscription - delegates to PresenceBarrel."""
        await self.presence.cancel_subscription(jid)

    async def revoke_subscription(self, jid: str):
        """Revoke subscription - delegates to PresenceBarrel."""
        await self.presence.revoke_subscription(jid)

    # =========================================================================
    # OMEMO Device Management (delegates to OmemoBarrel)
    # =========================================================================

    async def sync_omemo_devices_to_db(self, jid: str):
        """Sync OMEMO devices - delegates to OmemoBarrel."""
        await self.omemo.sync_omemo_devices_to_db(jid)

    # =========================================================================
    # Message Retry (Phase 4)
    # =========================================================================

    async def _retry_pending_messages(self):
        """
        Retry pending messages for this account on reconnect.
        Called automatically on session_start (successful connection/reconnection).
        """
        if self.app_logger:
            self.app_logger.debug("Starting message retry...")

        try:
            # Emit signal: retry started
            self.retry_started.emit(self.account_id)

            # Call retry handler (pass THIS account's XMPP client to ensure proper routing)
            stats = await self.retry_handler.retry_pending_messages_for_account(
                account_id=self.account_id,
                xmpp_client=self.client,  # Use THIS account's client for proper routing
                db=self.db
            )

            # Emit signal: retry completed with stats
            self.retry_completed.emit(self.account_id, stats)

            if self.app_logger:
                self.app_logger.debug(f"Message retry completed: {stats}")

        except Exception as e:
            if self.app_logger:
                self.app_logger.error(f"Message retry failed: {e}")
                import traceback
                self.app_logger.error(traceback.format_exc())

            # Emit signal: retry failed
            self.retry_failed.emit(self.account_id, str(e))

    # =========================================================================
    # Status Event Handlers
    # =========================================================================

    async def _on_session_start_autojoin(self, event):
        """Handle successful XMPP session start - auto-join bookmarked rooms and retry pending messages."""
        if self.app_logger:
            self.app_logger.debug("Session started, checking for rooms to auto-join...")

        # Update connection status
        self.connected = True
        self.connection._set_status('connected')
        self.connection_state_changed.emit(self.account_id, 'connected')

        # Set client reference for barrels (now that we're connected)
        self.presence.client = self.client
        self.omemo.client = self.client
        self.avatars.client = self.client
        self.files.client = self.client
        self.messages.client = self.client
        self.calls.client = self.client
        self.muc.client = self.client

        # Initialize CallBridge for audio/video calls (async)
        await self.calls._setup_call_functionality()

        # Query server information (XEP-0092 and XEP-0030)
        await self.connection.query_server_info()

        # Phase 4: Auto-join rooms FIRST (so retry can send to MUCs)
        await self.muc.auto_join_bookmarked_rooms()

        # Phase 5: Retry pending messages AFTER rooms are joined
        await self._retry_pending_messages()

        # Phase 6: Catch up 1-1 chat messages from MAM (messages sent while offline)
        # This retrieves messages from contacts similar to how MUC rooms catch up
        await self.messages.catchup_private_chats()

    async def _on_session_resumed(self, event):
        """Handle XMPP session resumption (XEP-0198)."""
        self.connected = True
        self.connection._set_status('connected')
        self.connection_state_changed.emit(self.account_id, 'connected')
        if self.app_logger:
            self.app_logger.info("XMPP session resumed (XEP-0198) - reconnected")
        # Room state and OMEMO are preserved, no need to rejoin

        # IMPORTANT: Catch up on messages sent while connection was down
        # Session resume means stream was temporarily interrupted - we may have missed messages
        await self.messages.catchup_private_chats()

    async def _on_session_start(self, event):
        """Handle successful XMPP session start."""
        self.connected = True
        self.connection._set_status('connected')
        self.connection_state_changed.emit(self.account_id, 'connected')
        if self.app_logger:
            self.app_logger.info("XMPP session started - now connected")

        # Sync blocked contacts from server (XEP-0191)
        await self._sync_blocked_contacts()

    async def _on_disconnected_event(self, event):
        """Handle disconnection event."""
        self.connected = False
        self.connection._set_status('disconnected')
        self.connection_state_changed.emit(self.account_id, 'disconnected')
        if self.app_logger:
            self.app_logger.info("XMPP disconnected")

    async def _on_failed_auth_event(self, event):
        """Handle authentication failure event."""
        self.connected = False
        self.connection._set_status('error')
        self.connection_state_changed.emit(self.account_id, 'error')
        if self.app_logger:
            self.app_logger.error("XMPP authentication failed")

    async def _on_connection_failed_event(self, event):
        """Handle connection failure event."""
        error_msg = str(event) if event else "Connection failed"

        if self.app_logger:
            self.app_logger.error(f"XMPP connection failed: {error_msg}")

        # Only show GUI error for SSL/TLS errors (cert issues), not transient network errors
        if 'SSL' in error_msg or 'certificate' in error_msg.lower() or 'TLS' in error_msg:
            self.connected = False
            self.connection._set_status('error')
            self.connection_state_changed.emit(self.account_id, 'error')
            self.connection_error.emit(self.account_id, error_msg)

    async def _on_disconnected(self, event):
        """Handle disconnection."""
        self.connected = False
        self.connection._set_status('disconnected')
        self.connection_state_changed.emit(self.account_id, 'disconnected')
        if self.app_logger:
            self.app_logger.info("XMPP disconnected")

    async def _on_failed_auth(self, event):
        """Handle authentication failure."""
        self.connected = False
        self.connection._set_status('error')
        if self.app_logger:
            self.app_logger.error("XMPP authentication failed")

    async def _sync_blocked_contacts(self):
        """
        Sync blocked contacts from server (XEP-0191) to local database.
        Called on session start to ensure DB matches server state.
        """
        if not self.client:
            return

        try:
            # Get blocked contacts from server
            blocked_jids = await self.client.get_blocked_contacts()

            if self.app_logger:
                self.app_logger.debug(f"Syncing {len(blocked_jids)} blocked contacts from server")

            # Update database: mark all contacts as unblocked first
            self.db.execute(
                "UPDATE roster SET blocked = 0 WHERE account_id = ?",
                (self.account_id,)
            )

            # Then mark blocked contacts
            for jid in blocked_jids:
                # Get jid_id
                jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
                if jid_row:
                    jid_id = jid_row['id']
                    # Update roster entry
                    self.db.execute(
                        "UPDATE roster SET blocked = 1 WHERE account_id = ? AND jid_id = ?",
                        (self.account_id, jid_id)
                    )

            self.db.commit()

            if self.app_logger:
                self.app_logger.debug("Blocked contacts synced successfully")

        except Exception as e:
            if self.app_logger:
                self.app_logger.error(f"Failed to sync blocked contacts: {e}")
                import traceback
                self.app_logger.error(traceback.format_exc())

    # =========================================================================
    # Message Classification
    # =========================================================================

    def _is_message_from_us(self, metadata: MessageMetadata, counterpart_jid: str) -> bool:
        """Check if message is from us - delegates to MessageBarrel."""
        return self.messages._is_message_from_us(metadata, counterpart_jid)

    def _classify_message(self, metadata: MessageMetadata, counterpart_jid: str) -> dict:
        """Classify message - delegates to MessageBarrel."""
        return self.messages._classify_message(metadata, counterpart_jid)

    def _check_message_duplicate(self, message_id: Optional[str], origin_id: Optional[str],
                                  stanza_id: Optional[str]) -> bool:
        """Check message duplicate - delegates to MessageBarrel."""
        return self.messages._check_message_duplicate(message_id, origin_id, stanza_id)

    def _update_message_marked(self, message_id: Optional[str], origin_id: Optional[str],
                               stanza_id: Optional[str], marked: int):
        """Update message marked status - delegates to MessageBarrel."""
        self.messages._update_message_marked(message_id, origin_id, stanza_id, marked)

    # =========================================================================
    # Call Functionality (DrunkCALL Integration) - Delegates to CallBarrel
    # =========================================================================

    # Property aliases for backwards compatibility
    @property
    def call_bridge(self):
        """CallBridge instance - delegates to CallBarrel."""
        return self.calls.call_bridge

    @property
    def jingle_adapter(self):
        """JingleAdapter instance - delegates to CallBarrel."""
        return self.calls.jingle_adapter

    # Delegation methods (all delegate to CallBarrel)
    async def _setup_call_functionality(self):
        """Setup call functionality - delegates to CallBarrel."""
        await self.calls._setup_call_functionality()

    def _on_ice_candidate_from_go(self, session_id: str, candidate: dict):
        """Handle ICE candidate from Go - delegates to CallBarrel."""
        self.calls._on_ice_candidate_from_go(session_id, candidate)

    def _on_connection_state_from_go(self, session_id: str, state: str):
        """Handle connection state from Go - delegates to CallBarrel."""
        self.calls._on_connection_state_from_go(session_id, state)

    async def _on_xmpp_call_incoming(self, peer_jid: str, session_id: str, media: list):
        """Handle incoming call - delegates to CallBarrel."""
        await self.calls._on_xmpp_call_incoming(peer_jid, session_id, media)

    async def _on_xmpp_call_accepted(self, session_id: str, peer_jid: str):
        """Handle call accepted - delegates to CallBarrel."""
        await self.calls._on_xmpp_call_accepted(session_id, peer_jid)

    async def _on_xmpp_call_terminated(self, session_id: str, reason: str):
        """Handle call terminated - delegates to CallBarrel."""
        await self.calls._on_xmpp_call_terminated(session_id, reason)

    async def _on_jingle_incoming_call(self, session_id: str, peer_jid: str, sdp_offer: str, media: list):
        """Handle Jingle incoming call - delegates to CallBarrel."""
        await self.calls._on_jingle_incoming_call(session_id, peer_jid, sdp_offer, media)

    async def _on_jingle_call_answered(self, session_id: str, sdp_answer: str):
        """Handle Jingle call answered - delegates to CallBarrel."""
        await self.calls._on_jingle_call_answered(session_id, sdp_answer)

    async def _on_jingle_call_terminated(self, session_id: str, reason: str):
        """Handle Jingle call terminated - delegates to CallBarrel."""
        await self.calls._on_jingle_call_terminated(session_id, reason)

    async def _complete_call_acceptance(self, session_id: str):
        """Complete call acceptance - delegates to CallBarrel."""
        await self.calls._complete_call_acceptance(session_id)

    async def _on_call_offer_received(self, session_id: str, peer_jid: str, sdp_offer: str, media: list):
        """Handle call offer - delegates to CallBarrel."""
        await self.calls._on_call_offer_received(session_id, peer_jid, sdp_offer, media)

    async def _on_call_answer_received(self, session_id: str, sdp_answer: str):
        """Handle call answer - delegates to CallBarrel."""
        await self.calls._on_call_answer_received(session_id, sdp_answer)

    async def _on_ice_candidate_received(self, session_id: str, candidate: dict):
        """Handle ICE candidate - delegates to CallBarrel."""
        await self.calls._on_ice_candidate_received(session_id, candidate)

    async def _on_call_terminated(self, session_id: str, reason: str):
        """Handle call terminated - delegates to CallBarrel."""
        await self.calls._on_call_terminated(session_id, reason)

    def _on_call_state_changed_sync(self, session_id: str, state: str):
        """Handle call state change (sync) - delegates to CallBarrel."""
        self.calls._on_call_state_changed_sync(session_id, state)

    async def _on_call_state_changed(self, session_id: str, state: str):
        """Handle call state change - delegates to CallBarrel."""
        await self.calls._on_call_state_changed(session_id, state)

    async def start_call(self, peer_jid: str, media: list = None) -> str:
        """Start call - delegates to CallBarrel."""
        return await self.calls.start_call(peer_jid, media)

    async def _on_incoming_call_timeout(self, session_id: str, peer_jid: str):
        """Handle incoming call timeout - delegates to CallBarrel."""
        await self.calls._on_incoming_call_timeout(session_id, peer_jid)

    async def _on_outgoing_call_timeout(self, session_id: str, peer_jid: str):
        """Handle outgoing call timeout - delegates to CallBarrel."""
        await self.calls._on_outgoing_call_timeout(session_id, peer_jid)

    def _cancel_incoming_call_timer(self, session_id: str):
        """Cancel incoming call timer - delegates to CallBarrel."""
        self.calls._cancel_incoming_call_timer(session_id)

    def _cancel_outgoing_call_timer(self, session_id: str):
        """Cancel outgoing call timer - delegates to CallBarrel."""
        self.calls._cancel_outgoing_call_timer(session_id)

    async def accept_call(self, session_id: str):
        """Accept call - delegates to CallBarrel."""
        await self.calls.accept_call(session_id)

    async def hangup_call(self, session_id: str):
        """Hang up call - delegates to CallBarrel."""
        await self.calls.hangup_call(session_id)

    async def end_call(self, session_id: str, reason: str = 'success', send_terminate: bool = True):
        """End call - delegates to CallBarrel."""
        await self.calls.end_call(session_id, reason, send_terminate)

    async def get_call_stats(self, session_id: str) -> dict:
        """Get call stats - delegates to CallBarrel."""
        return await self.calls.get_call_stats(session_id)

    @staticmethod
    def get_available_input_devices() -> list:
        """Get available input devices - delegates to CallBarrel."""
        return CallBarrel.get_available_input_devices()

    @staticmethod
    def get_available_output_devices() -> list:
        """Get available output devices - delegates to CallBarrel."""
        return CallBarrel.get_available_output_devices()

    def set_audio_devices(self, input_device_id: str = None, output_device_id: str = None):
        """Set audio devices - delegates to CallBarrel."""
        self.calls.set_audio_devices(input_device_id, output_device_id)

    def get_current_audio_devices(self) -> dict:
        """Get current audio devices - delegates to CallBarrel."""
        return self.calls.get_current_audio_devices()

    # =========================================================================
    # Roster Handler
    # =========================================================================

    async def _on_roster_update(self, event):
        """Handle roster updates - delegates to PresenceBarrel."""
        await self.presence._on_roster_update(event, self._fetch_roster_avatars)

    # =========================================================================
    # Receipt/Marker/ACK Callbacks (wire to receipt_handler for DB updates)
    # =========================================================================

    def _on_receipt_received(self, from_jid: str, message_id: str):
        """Handle delivery receipt - delegates to MessageBarrel."""
        self.messages._on_receipt_received(from_jid, message_id)
    def _on_marker_received(self, from_jid: str, message_id: str, marker_type: str):
        """Handle chat marker - delegates to MessageBarrel."""
        self.messages._on_marker_received(from_jid, message_id, marker_type)

    async def _on_carbon_received(self, wrapper_msg):
        """
        OBSOLETE: Carbon copies are now handled by _on_private_message().

        As of 2025-12-16, DrunkXMPP calls on_private_message_callback for
        carbon copies with metadata.is_carbon=True, metadata.carbon_type='received'.
        This handler is kept for backwards compatibility but does nothing.

        Args:
            wrapper_msg: Carbon wrapper containing the forwarded message (unused)
        """
        pass

    async def _on_carbon_sent(self, wrapper_msg):
        """
        OBSOLETE: Carbon copies are now handled by _on_private_message().

        As of 2025-12-16, DrunkXMPP calls on_private_message_callback for
        carbon copies with metadata.is_carbon=True, metadata.carbon_type='sent'.
        This handler is kept for backwards compatibility but does nothing.

        Args:
            wrapper_msg: Carbon wrapper containing the forwarded message (unused)
        """
        pass

    def _on_reaction(self, metadata, message_id: str, emojis: list):
        """Handle reaction - delegates to MessageBarrel."""
        self.messages._on_reaction(metadata, message_id, emojis)
    def _on_chat_state(self, from_jid: str, state: str):
        """Handle chat state - delegates to MessageBarrel."""
        self.messages._on_chat_state(from_jid, state)

    async def _on_presence_changed(self, from_jid: str, show: str):
        """Handle presence change - delegates to PresenceBarrel."""
        await self.presence._on_presence_changed(from_jid, show)

    async def _on_muc_joined(self, room_jid: str, nick: str):
        """
        Handle MUC join success (self-presence received).

        Emits signal to update UI (hide join button, show as joined).
        Then delegates to MUC barrel for MAM retrieval and metadata fetch.

        Args:
            room_jid: Bare JID of the room
            nick: Our nickname in the room
        """
        logger.info(f"[Account {self.account_id}] Successfully joined MUC {room_jid} as {nick}")
        self.muc_join_success.emit(self.account_id, room_jid)

        # Delegate to barrel for MAM and metadata fetch
        await self.muc.on_muc_joined(room_jid, nick)

    async def _on_muc_role_changed(self, room_jid: str, old_role: str, new_role: str):
        """
        Handle MUC role change (e.g., visitor → participant when voice granted).

        Emits signal to update UI (hide visitor overlay, enable input).

        Args:
            room_jid: Bare JID of the room
            old_role: Previous role (visitor, participant, moderator, none)
            new_role: New role
        """
        logger.info(f"[Account {self.account_id}] Role changed in {room_jid}: {old_role} → {new_role}")
        self.muc_role_changed.emit(self.account_id, room_jid, old_role, new_role)

    # =========================================================================
    # Avatar Management (delegates to AvatarBarrel)
    # =========================================================================

    async def _on_avatar_update(self, jid: str, avatar_data: dict):
        """Handle avatar update - delegates to AvatarBarrel."""
        await self.avatars.on_avatar_update(jid, avatar_data)

    def _store_avatar(self, jid: str, avatar_data: dict):
        """Store avatar - delegates to AvatarBarrel."""
        self.avatars.store_avatar(jid, avatar_data)

    async def _fetch_roster_avatars(self):
        """Fetch roster avatars - delegates to AvatarBarrel."""
        await self.avatars.fetch_roster_avatars()

    # =========================================================================
    # Nickname Management (XEP-0172: User Nickname)
    # =========================================================================

    async def _on_nickname_update(self, jid: str, nickname: Optional[str]):
        """
        Handle contact nickname update from XEP-0172 PEP event.

        Updates in-memory cache and emits signal to refresh UI.

        Args:
            jid: Bare JID of the contact
            nickname: The nickname text, or None if cleared
        """
        if nickname:
            self.contact_nicknames[jid] = nickname
            self.app_logger.info(f"Nickname updated for {jid}: {nickname}")
        else:
            # Nickname was cleared
            if jid in self.contact_nicknames:
                del self.contact_nicknames[jid]
                self.app_logger.info(f"Nickname cleared for {jid}")

        # Emit signal to refresh UI (pass empty string instead of None for Qt signal compatibility)
        self.nickname_updated.emit(self.account_id, jid, nickname if nickname else '')

    def get_contact_display_name(self, jid: str, roster_name: Optional[str] = None) -> str:
        """
        Get display name for a contact using 3-source priority.

        Priority: roster.name > contact_nickname > jid

        Args:
            jid: Bare JID of the contact
            roster_name: Optional roster name if already available (avoids DB query)

        Returns:
            Display name to show in UI
        """
        # 1. Check roster name (highest priority)
        if roster_name:
            return roster_name

        # 2. Check nickname cache (middle priority)
        if jid in self.contact_nicknames:
            return self.contact_nicknames[jid]

        # 3. Fall back to JID (lowest priority)
        return jid

    async def publish_own_nickname(self, nickname: Optional[str] = None):
        """
        Publish own nickname via XEP-0172.

        Args:
            nickname: Nickname to publish. If None, reads from account database.
                     Empty string clears the nickname.
        """
        if self.connection and self.connection.client:
            await self.connection.client.publish_nickname(nickname)
        else:
            self.logger.warning("Cannot publish nickname: not connected")

    async def _on_subscription_request(self, from_jid: str):
        """Handle subscription request - delegates to PresenceBarrel."""
        await self.presence._on_subscription_request(from_jid)

    async def _on_subscription_changed(self, from_jid: str, change_type: str):
        """Handle subscription change - delegates to PresenceBarrel."""
        await self.presence._on_subscription_changed(from_jid, change_type)

    def _on_server_ack(self, ack_info):
        """Handle server ACK - delegates to MessageBarrel."""
        self.messages._on_server_ack(ack_info)


class AccountBrewery:
    """
    The brewery - brews and manages multiple XMPP account connections.
    """

    def __init__(self):
        """Initialize the brewery."""
        self.db = get_db()
        self.accounts: Dict[int, XMPPAccount] = {}  # account_id -> XMPPAccount
        self.paths = get_paths()

        logger.debug("Account brewery initialized - ready to brew accounts")

    def create_account(self, jid: str, password: str, **optional_settings) -> int:
        """
        Create new account in database with defaults.

        Args:
            jid: Full JID (user@server/resource) or bare JID (user@server)
            password: Password in plaintext (will be encoded as base64)
            **optional_settings: Optional settings to override defaults (see below)

        Returns:
            account_id (int): ID of newly created account

        Default settings (can be overridden via optional_settings):
            enabled: 1
            resource: auto-generated if not provided
            nickname: None
            muc_nickname: None
            server_override: None
            port: None
            proxy_type: None
            proxy_host: None
            proxy_port: None
            proxy_username: None
            proxy_password: None (will be base64 encoded if provided)
            ignore_tls_errors: 0
            require_strong_tls: 1
            client_cert_path: None
            omemo_enabled: 1
            omemo_mode: 'default'
            omemo_blind_trust: 1
            omemo_storage_path: auto-generated based on account_id
            webrtc_enabled: 0
            carbons_enabled: 1
            typing_notifications: 1
            read_receipts: 1
            log_level: 'INFO'
            log_retention_days: 30
            log_app_enabled: 1
            log_xml_enabled: 1
        """
        # Parse JID to extract bare JID and resource
        if '/' in jid:
            bare_jid, resource = jid.rsplit('/', 1)
        else:
            bare_jid = jid
            resource = optional_settings.get('resource', None)

        # Auto-generate resource if not provided
        if not resource:
            resource = generate_resource(bare_jid)
            logger.debug(f"Auto-generated resource for {bare_jid}: {resource}")

        # Encode password as base64
        password_encoded = base64.b64encode(password.encode()).decode()

        # Encode proxy password if provided
        proxy_password = optional_settings.get('proxy_password')
        if proxy_password:
            proxy_password = base64.b64encode(proxy_password.encode()).decode()

        # Set defaults (can be overridden by optional_settings)
        defaults = {
            'enabled': 1,
            'nickname': None,
            'muc_nickname': None,
            'server_override': None,
            'port': None,
            'proxy_type': None,
            'proxy_host': None,
            'proxy_port': None,
            'proxy_username': None,
            'ignore_tls_errors': 0,
            'require_strong_tls': 1,
            'client_cert_path': None,
            'omemo_enabled': 1,
            'omemo_mode': 'default',
            'omemo_blind_trust': 1,
            'webrtc_enabled': 0,
            'carbons_enabled': 1,
            'typing_notifications': 1,
            'read_receipts': 1,
            'log_level': 'INFO',
            'log_retention_days': 30,
            'log_app_enabled': 1,
            'log_xml_enabled': 1,
        }

        # Merge with optional settings
        settings = {**defaults, **optional_settings}

        # Create account in database
        cursor = self.db.execute("""
            INSERT INTO account (
                bare_jid, password, nickname, muc_nickname, resource, enabled,
                server_override, port,
                proxy_type, proxy_host, proxy_port, proxy_username, proxy_password,
                ignore_tls_errors, require_strong_tls, client_cert_path,
                omemo_enabled, omemo_mode, omemo_blind_trust,
                webrtc_enabled, carbons_enabled, typing_notifications, read_receipts,
                log_level, log_retention_days, log_app_enabled, log_xml_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            bare_jid,
            password_encoded,
            settings['nickname'],
            settings['muc_nickname'],
            resource,
            settings['enabled'],
            settings['server_override'],
            settings['port'],
            settings['proxy_type'],
            settings['proxy_host'],
            settings['proxy_port'],
            settings['proxy_username'],
            proxy_password,
            settings['ignore_tls_errors'],
            settings['require_strong_tls'],
            settings['client_cert_path'],
            settings['omemo_enabled'],
            settings['omemo_mode'],
            settings['omemo_blind_trust'],
            settings['webrtc_enabled'],
            settings['carbons_enabled'],
            settings['typing_notifications'],
            settings['read_receipts'],
            settings['log_level'],
            settings['log_retention_days'],
            settings['log_app_enabled'],
            settings['log_xml_enabled']
        ))

        account_id = cursor.lastrowid

        # Update OMEMO storage path now that we have account_id
        omemo_path = str(self.paths.omemo_storage_path(account_id))
        self.db.execute(
            "UPDATE account SET omemo_storage_path = ? WHERE id = ?",
            (omemo_path, account_id)
        )

        self.db.commit()
        logger.info(f"Account {account_id} created: {bare_jid}")

        return account_id

    def update_account(self, account_id: int, **settings) -> bool:
        """
        Update existing account settings.

        Args:
            account_id: Account ID to update
            **settings: Settings to update (field_name=value)

        Returns:
            bool: True if update successful

        Raises:
            ValueError: If account_id not found

        Example:
            brewery.update_account(1, log_level='DEBUG', carbons_enabled=1)
            brewery.update_account(2, password='newpass', nickname='Work Account')

        Note:
            - Password will be automatically base64 encoded
            - Proxy password will be automatically base64 encoded
            - Only provided fields will be updated
        """
        # Check if account exists
        account = self.db.fetchone("SELECT id FROM account WHERE id = ?", (account_id,))
        if not account:
            raise ValueError(f"Account {account_id} not found")

        if not settings:
            logger.warning(f"update_account called with no settings for account {account_id}")
            return True

        # Encode password if provided
        if 'password' in settings:
            settings['password'] = base64.b64encode(settings['password'].encode()).decode()

        # Encode proxy password if provided
        if 'proxy_password' in settings and settings['proxy_password']:
            settings['proxy_password'] = base64.b64encode(settings['proxy_password'].encode()).decode()

        # Build UPDATE query dynamically
        set_clauses = []
        values = []
        for key, value in settings.items():
            set_clauses.append(f"{key} = ?")
            values.append(value)

        values.append(account_id)  # For WHERE clause

        query = f"UPDATE account SET {', '.join(set_clauses)} WHERE id = ?"

        self.db.execute(query, tuple(values))
        self.db.commit()

        logger.info(f"Account {account_id} updated: {list(settings.keys())}")
        return True

    def has_active_call(self, exclude_session_id: str = None) -> bool:
        """
        Check if there's an active call across ALL accounts (alpha version: one call at a time globally).

        Args:
            exclude_session_id: Session ID to exclude from check (used to avoid counting current incoming call)

        Returns:
            True if any account has an active call session, False otherwise
        """
        for account in self.accounts.values():
            # Check XEP-0353 call_sessions (populated early when propose sent/received)
            if hasattr(account.client, 'call_sessions'):
                for session_id in account.client.call_sessions.keys():
                    if session_id != exclude_session_id:
                        return True

            # Fallback: Check Jingle sessions (populated later during negotiation)
            if account.jingle_adapter:
                for session_id in account.jingle_adapter.sessions.keys():
                    if session_id != exclude_session_id:
                        return True

        return False

    def load_accounts(self):
        """Brew all enabled accounts from database and connect them."""
        accounts = self.db.fetchall(
            "SELECT * FROM account WHERE enabled = 1 ORDER BY id"
        )

        logger.debug(f"Brewing {len(accounts)} enabled accounts...")

        for account_data in accounts:
            account_id = account_data['id']
            bare_jid = account_data['bare_jid']

            logger.debug(f"Brewing account {account_id}: {bare_jid}")

            # Create account wrapper
            account = XMPPAccount(account_id, dict(account_data))
            self.accounts[account_id] = account

            # Connect
            account.connect()

        logger.debug(f"Loaded {len(self.accounts)} accounts")

    def get_account(self, account_id: int) -> Optional[XMPPAccount]:
        """
        Get account by ID.

        Args:
            account_id: Account ID

        Returns:
            XMPPAccount or None
        """
        return self.accounts.get(account_id)

    def connect_account(self, account_id: int):
        """
        Connect a specific account.

        Args:
            account_id: Account ID
        """
        if account_id in self.accounts:
            self.accounts[account_id].connect()
        else:
            # Load account from database
            account_data = self.db.fetchone("SELECT * FROM account WHERE id = ?", (account_id,))
            if not account_data:
                logger.error(f"Account {account_id} not found")
                return

            account = XMPPAccount(account_id, dict(account_data))
            self.accounts[account_id] = account
            account.connect()

    def disconnect_account(self, account_id: int):
        """
        Disconnect a specific account.

        Args:
            account_id: Account ID
        """
        if account_id in self.accounts:
            self.accounts[account_id].disconnect()

    def disconnect_all(self):
        """Disconnect all accounts."""
        logger.debug("Disconnecting all accounts...")

        for account_id, account in self.accounts.items():
            logger.debug(f"Disconnecting account {account_id}...")
            account.disconnect()

        logger.debug("All accounts disconnected")

    def get_account_status(self, account_id: int) -> str:
        """
        Get connection status for an account.

        Args:
            account_id: Account ID

        Returns:
            Status string ('connected', 'disconnected', 'unknown')
        """
        account = self.accounts.get(account_id)
        if not account:
            return 'unknown'

        if account.is_connected():
            return 'connected'
        else:
            return 'disconnected'


# Global account brewery instance (singleton)
_account_brewery: Optional[AccountBrewery] = None


def get_account_brewery() -> AccountBrewery:
    """
    Get global account brewery instance.

    Returns:
        AccountBrewery instance (singleton)
    """
    global _account_brewery
    if _account_brewery is None:
        _account_brewery = AccountBrewery()
    return _account_brewery
