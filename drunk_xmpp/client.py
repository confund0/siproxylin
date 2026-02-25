#!/usr/bin/env python3
"""
DRUNK-XMPP - Privacy-focused XMPP client library
Reusable XMPP client with OMEMO encryption support.

Features:
- Persistent MUC connections with auto-rejoin
- XEP-0030 (Service Discovery) server feature discovery
- XEP-0045 (Multi-User Chat) support
- XEP-0054 (vcard-temp) vCard functionality
- XEP-0084 (User Avatar) modern PEP-based avatars
- XEP-0085 (Chat State Notifications) typing indicators
- XEP-0092 (Software Version) server version queries
- XEP-0153 (vCard-Based Avatars) legacy avatar support
- XEP-0184 (Message Delivery Receipts) delivery confirmations
- XEP-0199 (XMPP Ping) keepalive
- XEP-0215 (External Service Discovery) STUN/TURN server credentials
- XEP-0280 (Message Carbons) for multi-device synchronization
- XEP-0308 (Last Message Correction) for editing messages
- XEP-0313 (Message Archive Management) for message history retrieval
- XEP-0333 (Chat Markers) read receipts
- XEP-0384 (OMEMO) end-to-end encryption
- XEP-0402 (PEP Native Bookmarks) server-side room list sync
- Graceful handling of kicks, bans, nick conflicts
- Automatic OMEMO version negotiation (0.3.0 and 0.8.0+)
- Blind Trust Before Verification (BTBV) for automatic trust
- Detailed logging for debugging
"""

import logging
import asyncio
import json
import sys
import traceback
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, FrozenSet, Set, Any
from dataclasses import dataclass, field

# Apply slixmpp patches BEFORE importing slixmpp
from drunk_xmpp.slixmpp_patches import (
    apply_xep0199_patch,
    apply_xep0280_reactions_patch,
    apply_xep0353_finish_patch,
    apply_xep0045_membership_patch,
    apply_cert_stdin_prevention_patch
)
apply_xep0199_patch()
apply_xep0280_reactions_patch()
apply_xep0353_finish_patch()
apply_xep0045_membership_patch()
apply_cert_stdin_prevention_patch()

# import aiohttp  # Not currently used
from slixmpp import ClientXMPP
from slixmpp.jid import JID
from slixmpp.stanza import Message
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.plugins import register_plugin

# Proxy support using python-socks library (supports HTTP CONNECT and SOCKS5)
try:
    from python_socks.async_.asyncio import Proxy
    PROXY_AVAILABLE = True
except ImportError:
    PROXY_AVAILABLE = False

# OMEMO imports
from omemo.storage import Storage, Maybe, Just, Nothing
from omemo.types import JSONType, DeviceInformation
from slixmpp_omemo import XEP_0384, TrustLevel

# Local imports
from .discovery import DiscoveryMixin
from .messaging import MessagingMixin
from .bookmarks import BookmarksMixin
from .calls import CallsMixin
from .omemo_devices import OMEMODevicesMixin
from .mam import MAMMixin
from .file_uploads import FileUploadMixin
from .message_extensions import MessageExtensionsMixin
from .avatar import AvatarMixin
from .external_services import ExternalServicesMixin


@dataclass
class MessageMetadata:
    """
    Metadata extracted from XMPP message stanza.

    DrunkXMPP provides facts about the message, clients interpret them.
    This is a pure data object - no business logic, just attributes extracted
    from the stanza and XMPP protocol state.

    Philosophy:
    - DrunkXMPP DETECTS and PROVIDES metadata
    - Client (GUI/test tool) INTERPRETS and DECIDES what to do
    """

    # Message identity (XEP-0359: Unique and Stable Stanza IDs)
    message_id: Optional[str] = None     # Basic message ID attribute (msg.get('id'))
    origin_id: Optional[str] = None      # XEP-0359 origin-id (client-assigned)
    stanza_id: Optional[str] = None      # XEP-0359 stanza-id (server-assigned)

    # Source information
    from_jid: Optional[str] = None       # Full JID (user@domain/resource)
    to_jid: Optional[str] = None         # Full JID

    # Message type flags
    message_type: Optional[str] = None   # 'chat', 'groupchat', 'error', 'normal'
    is_carbon: bool = False              # XEP-0280: Message Carbon
    carbon_type: Optional[str] = None    # 'sent' or 'received'
    is_history: bool = False             # Has delay element (XEP-0203)
    delay_timestamp: Optional[datetime] = None  # Timestamp from delay element

    # MUC-specific (XEP-0045)
    occupant_id: Optional[str] = None    # XEP-0421: Anonymous occupant ID
    muc_nick: Optional[str] = None       # Nickname/resource in MUC

    # Encryption (XEP-0384: OMEMO)
    is_encrypted: bool = False
    encryption_type: Optional[str] = None  # 'omemo'
    decrypt_success: bool = False
    decrypt_failed: bool = False
    sender_device_id: Optional[int] = None  # OMEMO device ID of sender

    # Content flags
    has_body: bool = False
    has_attachment: bool = False
    attachment_url: Optional[str] = None
    attachment_encrypted: bool = False   # aesgcm:// URL

    # XEP-0444: Message Reactions
    is_reaction: bool = False
    reaction_to_id: Optional[str] = None
    reaction_emojis: List[str] = field(default_factory=list)

    # XEP-0461: Message Replies
    is_reply: bool = False
    reply_to_id: Optional[str] = None
    reply_to_jid: Optional[str] = None

    # XEP-0308: Last Message Correction
    is_correction: bool = False
    replaces_id: Optional[str] = None

    # XEP-0184: Message Delivery Receipts
    is_receipt: bool = False
    receipt_for_id: Optional[str] = None

    # XEP-0333: Chat Markers
    is_marker: bool = False
    marker_type: Optional[str] = None    # 'received', 'displayed', 'acknowledged'
    marker_for_id: Optional[str] = None

    # XEP-0085: Chat State Notifications
    is_chat_state: bool = False
    chat_state: Optional[str] = None     # 'active', 'composing', 'paused', 'inactive', 'gone'

    # Error handling
    is_error: bool = False
    error_type: Optional[str] = None
    error_condition: Optional[str] = None


class OMEMOStorage(Storage):
    """
    OMEMO storage implementation using a JSON file backend.
    Based on the slixmpp-omemo example implementation.
    """

    def __init__(self, json_file_path: Path) -> None:
        super().__init__()
        self.__json_file_path = json_file_path
        self.__data: Dict[str, JSONType] = {}

        # Ensure parent directory exists
        self.__json_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing data
        try:
            with open(self.__json_file_path, encoding="utf8") as f:
                self.__data = json.load(f)
        except Exception:
            pass

    async def _load(self, key: str) -> Maybe[JSONType]:
        if key in self.__data:
            return Just(self.__data[key])
        return Nothing()

    async def _store(self, key: str, value: JSONType) -> None:
        self.__data[key] = value
        with open(self.__json_file_path, "w", encoding="utf8") as f:
            json.dump(self.__data, f, indent=2)

    async def _delete(self, key: str) -> None:
        self.__data.pop(key, None)
        with open(self.__json_file_path, "w", encoding="utf8") as f:
            json.dump(self.__data, f, indent=2)


class PluginCouldNotLoad(Exception):
    """Exception raised when OMEMO plugin fails to load."""
    pass


class XEP_0384Impl(XEP_0384):
    """
    OMEMO plugin implementation for DrunkXMPP.
    Supports both legacy OMEMO (0.3.0) and modern OMEMO (0.8.0+).
    """

    default_config = {
        "fallback_message": "This message is OMEMO encrypted.",
        "json_file_path": None,
        "storage_object": None
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.__storage: Storage

    def plugin_init(self) -> None:
        # Accept either a Storage object (for DB backend) or json_file_path (for file backend)
        if self.storage_object is not None:
            self.__storage = self.storage_object
        elif self.json_file_path:
            self.__storage = OMEMOStorage(Path(self.json_file_path))
        else:
            raise PluginCouldNotLoad("Either storage_object or json_file_path must be specified.")

        super().plugin_init()

    @property
    def storage(self) -> Storage:
        return self.__storage

    @property
    def _btbv_enabled(self) -> bool:
        """Enable Blind Trust Before Verification for automatic trust."""
        return True

    async def _devices_blindly_trusted(
        self,
        blindly_trusted: FrozenSet[DeviceInformation],
        identifier: Optional[str]
    ) -> None:
        """Called when devices are automatically trusted via BTBV."""
        log = logging.getLogger(__name__)
        log.info(f"[{identifier}] Devices trusted blindly: {blindly_trusted}")

    async def _prompt_manual_trust(
        self,
        manually_trusted: FrozenSet[DeviceInformation],
        identifier: Optional[str]
    ) -> None:
        """
        Called when manual trust decision is needed.
        Since BTBV is enabled, this should rarely be called.
        For now, we'll automatically trust all devices.
        """
        log = logging.getLogger(__name__)
        session_manager = await self.get_session_manager()

        for device in manually_trusted:
            log.info(f"[{identifier}] Auto-trusting device: {device}")
            await session_manager.set_trust(
                device.bare_jid,
                device.identity_key,
                TrustLevel.TRUSTED.value
            )


# Register our OMEMO plugin implementation
register_plugin(XEP_0384Impl)


class DrunkXMPP(ClientXMPP, DiscoveryMixin, MessagingMixin, BookmarksMixin, OMEMODevicesMixin, MAMMixin, FileUploadMixin, MessageExtensionsMixin, AvatarMixin, ExternalServicesMixin, CallsMixin):
    """
    DRUNK-XMPP client with OMEMO encryption support.
    """

    def __init__(
        self,
        jid: str,
        password: str,
        rooms: Dict[str, Dict],
        omemo_storage_path: Optional[str] = None,
        omemo_storage: Optional[Storage] = None,
        reconnect_max_delay: int = 300,
        keepalive_interval: int = 60,
        sasl_mech: str = 'SCRAM-SHA-1',
        on_message_callback: Optional[Callable] = None,
        on_private_message_callback: Optional[Callable] = None,
        on_message_error_callback: Optional[Callable] = None,
        on_receipt_received_callback: Optional[Callable] = None,
        on_marker_received_callback: Optional[Callable] = None,
        on_server_ack_callback: Optional[Callable] = None,
        on_chat_state_callback: Optional[Callable] = None,
        on_bookmarks_received_callback: Optional[Callable] = None,
        on_muc_invite_callback: Optional[Callable] = None,
        on_muc_joined_callback: Optional[Callable] = None,
        on_muc_join_error_callback: Optional[Callable] = None,
        on_muc_role_changed_callback: Optional[Callable] = None,
        on_message_correction_callback: Optional[Callable] = None,
        on_room_config_changed_callback: Optional[Callable] = None,
        on_avatar_update_callback: Optional[Callable] = None,
        on_reaction_callback: Optional[Callable] = None,
        on_subscription_request_callback: Optional[Callable] = None,
        on_subscription_changed_callback: Optional[Callable] = None,
        on_presence_changed_callback: Optional[Callable] = None,
        on_nickname_update_callback: Optional[Callable] = None,
        own_nickname: Optional[str] = None,
        enable_omemo: bool = True,
        allow_any_message_editing: bool = False,
        muc_history_default: int = 0,
        proxy_type: Optional[str] = None,
        proxy_host: Optional[str] = None,
        proxy_port: Optional[int] = None,
        proxy_username: Optional[str] = None,
        proxy_password: Optional[str] = None,
        client_cert_path: Optional[str] = None,
    ):
        """
        Initialize DrunkXMPP client with OMEMO support.

        Args:
            jid: User JID (e.g., user@example.com)
            password: User password
            rooms: Dict of {room_jid: {'nick': 'Nickname', 'password': 'optional', 'maxhistory': int}}
            omemo_storage_path: Path to JSON file for OMEMO key storage (default: ~/.xmpp-omemo-{jid}.json)
                                DEPRECATED: Use omemo_storage parameter instead for DB backend
            omemo_storage: Storage object for OMEMO keys (alternative to omemo_storage_path)
                          Pass this for DB backend, leave None for file-based storage
            reconnect_max_delay: Max reconnect delay in seconds
            keepalive_interval: Ping interval in seconds
            sasl_mech: SASL authentication mechanism (default: SCRAM-SHA-1)
                      Options: 'SCRAM-SHA-1', 'SCRAM-SHA-1-PLUS', 'SCRAM-SHA-256', 'PLAIN'
            on_message_callback: Optional callback for received MUC messages (room, nick, body, is_encrypted, msg, is_history=False)
            on_private_message_callback: Optional callback for received private messages (from_jid, body, is_encrypted, msg)
            on_message_error_callback: Optional callback for message errors (from_jid, to_jid, error_type, error_condition, error_text, origin_id)
            on_receipt_received_callback: Optional callback for delivery receipts (from_jid, message_id)
            on_marker_received_callback: Optional callback for chat markers (from_jid, message_id, marker_type)
            on_server_ack_callback: Optional callback for server ACKs (stanza) - XEP-0198
            on_chat_state_callback: Optional callback for chat state notifications (from_jid, state) - XEP-0085
            on_bookmarks_received_callback: Optional callback for bookmarks sync (bookmarks_list) - XEP-0402
            on_muc_invite_callback: Optional callback for MUC invites (room_jid, inviter_jid, reason, password) - XEP-0045
            on_muc_joined_callback: Optional callback for MUC room joined (room_jid, nick) - Fires after self-presence received (status code 110)
            on_muc_role_changed_callback: Optional callback for MUC role changes (room_jid, old_role, new_role) - XEP-0045
            on_room_config_changed_callback: Optional callback for room config changes (room_jid, room_name) - XEP-0045 status code 104
            on_avatar_update_callback: Optional callback for avatar updates (jid, avatar_data) - XEP-0084/0153
            on_nickname_update_callback: Optional callback for nickname updates (jid, nickname) - XEP-0172
            own_nickname: Optional nickname to publish via XEP-0172 on connect
            on_reaction_callback: Optional callback for message reactions (from_jid, message_id, emojis) - XEP-0444
            enable_omemo: Enable OMEMO encryption (default: True)
            muc_history_default: Default number of history messages to request when joining MUCs (default: 0)
                                Per-room override via rooms[room_jid]['maxhistory']
            allow_any_message_editing: Allow editing any message by ID, not just last one (default: False)
            proxy_type: Proxy type ('HTTP' or 'SOCKS5' or None)
            proxy_host: Proxy server hostname/IP
            proxy_port: Proxy server port
            proxy_username: Optional proxy authentication username
            proxy_password: Optional proxy authentication password
            client_cert_path: Path to client certificate .pem file for TLS authentication (key must be unencrypted)
        """
        # Pass sasl_mech to slixmpp's ClientXMPP constructor
        # This properly configures feature_mechanisms plugin before connection
        # Default to SCRAM-SHA-1 for best compatibility (avoids channel binding issues)
        super().__init__(jid, password, sasl_mech=sasl_mech)

        # Set connection timeout to 60 seconds (default is 30)
        self.connection_timeout = 60

        # Store proxy configuration for later use in _attempt_connection
        self.proxy_type = None
        self.proxy_url = None

        if proxy_type and proxy_host and proxy_port:
            if not PROXY_AVAILABLE:
                logging.getLogger(__name__).error(
                    "Proxy requested but python-socks library not installed. "
                    "Install with: pip install python-socks[asyncio]"
                )
                raise ImportError("python-socks library required for proxy support")

            # Validate proxy type
            proxy_type_upper = proxy_type.upper()
            if proxy_type_upper not in ['HTTP', 'SOCKS5']:
                logging.getLogger(__name__).warning(f"Unknown proxy type: {proxy_type}")
            else:
                self.proxy_type = proxy_type_upper

                # Build proxy URL for python-socks
                if proxy_username and proxy_password:
                    self.proxy_url = f"{proxy_type_upper.lower()}://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
                else:
                    self.proxy_url = f"{proxy_type_upper.lower()}://{proxy_host}:{proxy_port}"

                # Log proxy configuration (without password)
                proxy_info = f"{proxy_host}:{proxy_port}"
                if proxy_username:
                    proxy_info = f"{proxy_username}@{proxy_info}"
                logging.getLogger(__name__).info(f"{proxy_type_upper} proxy configured: {proxy_info}")

        # Configure client certificate for TLS authentication (works with both STARTTLS and direct TLS)
        # NOTE: Only unencrypted keys are supported (cert should be validated by caller before passing)
        if client_cert_path:
            self.certfile = client_cert_path
            self.keyfile = client_cert_path  # Same file contains both cert and key
            logging.getLogger(__name__).info(f"Client certificate configured: {client_cert_path}")

        self.rooms = rooms if rooms is not None else {}
        self.on_message_callback = on_message_callback
        self.on_private_message_callback = on_private_message_callback
        self.on_message_error_callback = on_message_error_callback
        self.on_receipt_received_callback = on_receipt_received_callback
        self.on_marker_received_callback = on_marker_received_callback
        self.on_server_ack_callback = on_server_ack_callback
        self.on_reaction_callback = on_reaction_callback
        self.on_chat_state_callback = on_chat_state_callback
        self.on_bookmarks_received_callback = on_bookmarks_received_callback
        self.on_muc_invite_callback = on_muc_invite_callback
        self.on_muc_joined_callback = on_muc_joined_callback
        self.on_muc_join_error_callback = on_muc_join_error_callback
        self.on_muc_role_changed_callback = on_muc_role_changed_callback
        self.on_message_correction_callback = on_message_correction_callback
        self.on_room_config_changed_callback = on_room_config_changed_callback
        self.on_avatar_update_callback = on_avatar_update_callback
        self.on_subscription_request_callback = on_subscription_request_callback
        self.on_subscription_changed_callback = on_subscription_changed_callback
        self.on_presence_changed_callback = on_presence_changed_callback
        self.on_nickname_update_callback = on_nickname_update_callback

        # Nickname cache (XEP-0172: User Nickname)
        self.nickname_cache: Dict[str, str] = {}
        self.own_nickname = own_nickname

        # Call-related state (CallsMixin)
        self.on_call_incoming: Optional[Callable] = None
        self.on_call_accepted: Optional[Callable] = None
        self.on_call_terminated: Optional[Callable] = None
        self.call_sessions: Dict[str, Dict[str, Any]] = {}

        self.logger = logging.getLogger('drunk-xmpp.client')
        self.joined_rooms = set()
        self.reconnect_attempts = 0
        self.omemo_enabled = enable_omemo
        self.omemo_ready = False
        self.allow_any_message_editing = allow_any_message_editing

        # Set up asyncio exception handler for unhandled task exceptions
        # This catches TimeoutError from slixmpp's internal join_muc_wait calls
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(self._asyncio_exception_handler)
        self.muc_history_default = muc_history_default

        # Track pending server ACKs: {msg_id: seq_number}
        self.pending_server_acks = {}

        # Track our own occupant-id per room (XEP-0421)
        # Key: room_jid (bare), Value: occupant_id string
        self.own_occupant_ids = {}

        # Track our own affiliation and role per room (XEP-0045)
        # Key: room_jid (bare), Value: affiliation string (owner, admin, member, none, outcast)
        # Value: role string (moderator, participant, visitor, none)
        self.own_affiliations = {}
        self.own_roles = {}

        # Track room subjects (XEP-0045 §8.1)
        # Key: room_jid (bare), Value: subject string
        self.room_subjects: Dict[str, str] = {}

        # Track user-initiated disconnect (don't auto-reconnect if True)
        self.user_disconnected = False

        # Track actual connection state (updated by event handlers)
        self._connection_state = False

        # Set OMEMO storage (either object or path for backward compatibility)
        self.omemo_storage = omemo_storage
        if omemo_storage_path:
            self.omemo_storage_path = Path(omemo_storage_path)
        else:
            # Default to home directory (for file-based storage)
            safe_jid = jid.split('@')[0].replace('/', '_').replace('.', '_')
            self.omemo_storage_path = Path.home() / f".xmpp-omemo-{safe_jid}.json"

        # Enable plugins (order matters for dependencies!)

        # Monkey-patch XEP-0198 stanza interfaces BEFORE registering the plugin!
        # This prevents warnings when MatchIDSender checks these stanzas during plugin init.
        # MatchIDSender is used for IQ response matching and checks ALL incoming stanzas,
        # including XEP-0198 stream management stanzas which don't have 'from'/'id' attributes.
        from slixmpp.plugins.xep_0198 import stanza as sm_stanza
        sm_stanza.Ack.interfaces = sm_stanza.Ack.interfaces | {'from', 'id'}
        sm_stanza.RequestAck.interfaces = sm_stanza.RequestAck.interfaces | {'from', 'id'}
        sm_stanza.Enabled.interfaces = sm_stanza.Enabled.interfaces | {'from'}
        sm_stanza.Resumed.interfaces = sm_stanza.Resumed.interfaces | {'from'}
        sm_stanza.Failed.interfaces = sm_stanza.Failed.interfaces | {'from', 'id'}

        # Stream Management - ACKs tell us when stanzas reached the server
        self.register_plugin('xep_0198', {'window': 5})  # Default window

        # Register our own handler for ACK stanzas directly
        from slixmpp.xmlstream.handler import Callback
        from slixmpp.xmlstream.matcher import MatchXPath
        self.register_handler(
            Callback('Custom SM Ack Handler',
                MatchXPath('{urn:xmpp:sm:3}a'),
                self._on_sm_ack_received,
                instream=True))
        self.logger.debug("Registered custom XEP-0198 ACK handler")

        self.register_plugin('xep_0030')  # Service Discovery (required by many XEPs)
        self.register_plugin('xep_0077')  # In-Band Registration (required for room membership requests)
        self.register_plugin('xep_0092')  # Software Version
        self.register_plugin('xep_0115')  # Entity Capabilities (advertise features to other clients)
        self.register_plugin('xep_0128')  # Service Discovery Extensions (required by xep_0363)
        self.register_plugin('xep_0045')  # Multi-User Chat
        self.register_plugin('xep_0421')  # Anonymous unique occupant identifiers for MUCs
        self.register_plugin('xep_0059')  # Result Set Management (required by xep_0313)
        self.register_plugin('xep_0066')  # Out of Band Data (for inline media)
        self.register_plugin('xep_0191')  # Blocking Command
        self.register_plugin('xep_0199', {'keepalive': True, 'interval': keepalive_interval})  # XMPP Ping with auto-reconnect
        self.register_plugin('xep_0203')  # Delayed Delivery (for history)
        self.register_plugin('xep_0280')  # Message Carbons (multi-device sync)
        self.register_plugin('xep_0297')  # Stanza Forwarding (required by xep_0280)
        self.register_plugin('xep_0308')  # Message Correction (Last Message Correction)
        self.register_plugin('xep_0313')  # Message Archive Management (MAM)
        self.register_plugin('xep_0359')  # Unique and Stable Stanza IDs
        self.register_plugin('xep_0363')  # HTTP File Upload
        self.register_plugin('xep_0380')  # Explicit Message Encryption
        self.register_plugin('xep_0428')  # Fallback Indication (required by xep_0461)
        self.register_plugin('xep_0454')  # OMEMO Media Sharing
        self.register_plugin('xep_0461')  # Message Replies
        self.register_plugin('xep_0334')  # Message Processing Hints (required by xep_0444)
        self.register_plugin('xep_0444')  # Message Reactions
        self.register_plugin('xep_0060')  # Publish-Subscribe (required by xep_0402)
        self.register_plugin('xep_0085')  # Chat State Notifications (typing indicators)
        self.register_plugin('xep_0163')  # Personal Eventing Protocol (required by xep_0402)
        self.register_plugin('xep_0172')  # User Nickname (PEP-based nicknames)
        self.logger.info("XEP-0172 plugin registered")
        self.register_plugin('xep_0184')  # Message Delivery Receipts
        self.register_plugin('xep_0223')  # Persistent Storage of Private Data via PubSub (required by xep_0402)
        self.register_plugin('xep_0333')  # Chat Markers (read receipts)
        self.register_plugin('xep_0402')  # PEP Native Bookmarks
        self.register_plugin('xep_0054')  # vcard-temp (base vCard functionality)
        self.register_plugin('xep_0084')  # User Avatar (PEP-based avatars)
        self.register_plugin('xep_0153')  # vCard-Based Avatars (legacy)
        self.register_plugin('xep_0353')  # Jingle Message Initiation (modern call signaling)

        # Enable OMEMO if requested
        if self.omemo_enabled:
            try:
                # Use Storage object if provided, otherwise use file path
                if self.omemo_storage:
                    self.register_plugin(
                        'xep_0384',
                        {'storage_object': self.omemo_storage},
                        module=sys.modules.get(__name__, sys.modules.get('__main__'))
                    )
                    self.logger.info(f"OMEMO plugin registered (storage: DB backend)")
                else:
                    self.register_plugin(
                        'xep_0384',
                        {'json_file_path': str(self.omemo_storage_path)},
                        module=sys.modules.get(__name__, sys.modules.get('__main__'))
                    )
                    self.logger.info(f"OMEMO plugin registered (storage: {self.omemo_storage_path})")
            except Exception as e:
                self.logger.error(f"Failed to register OMEMO plugin: {e}")
                self.omemo_enabled = False

        # Configure reconnection
        self.reconnect_max_delay = reconnect_max_delay

        # Event handlers
        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("session_resumed", self._on_session_resumed)
        self.add_event_handler("session_end", self._on_session_end)
        self.add_event_handler("disconnected", self._on_disconnected)
        self.add_event_handler("failed_auth", self._on_failed_auth)
        self.add_event_handler("groupchat_message", self._on_groupchat_message)
        self.add_event_handler("groupchat_subject", self._on_groupchat_subject)
        self.add_event_handler("groupchat_config_status", self._on_groupchat_config_status)
        self.add_event_handler("message", self._on_private_message)
        self.add_event_handler("message_error", self._on_message_error)
        self.add_event_handler("reactions", self._on_reactions)
        self.add_event_handler("message_correction", self._on_message_correction)
        self.add_event_handler("carbon_received", self._on_carbon_received)
        self.add_event_handler("carbon_sent", self._on_carbon_sent)
        self.add_event_handler("receipt_received", self._on_receipt_received)
        # XEP-0333 fires specific events for each marker type, plus generic 'marker' for all
        self.add_event_handler("marker_received", self._on_marker_received)
        self.add_event_handler("marker_displayed", self._on_marker_received)
        self.add_event_handler("marker_acknowledged", self._on_marker_received)
        # XEP-0085: Chat State Notifications (typing indicators)
        self.add_event_handler("chatstate_active", self._on_chat_state)
        self.add_event_handler("chatstate_composing", self._on_chat_state)
        self.add_event_handler("chatstate_paused", self._on_chat_state)
        self.add_event_handler("chatstate_inactive", self._on_chat_state)
        self.add_event_handler("chatstate_gone", self._on_chat_state)
        # XEP-0198 handler registered earlier at line 221 (right after plugin registration)
        # MUC presence handlers - register for MUC-specific events
        self.add_event_handler("groupchat_presence", self._on_muc_presence)
        self.add_event_handler("muc::%s::self-presence" % '*', self._on_muc_presence)
        self.add_event_handler("muc::%s::got-online" % '*', self._on_muc_presence)
        self.add_event_handler("muc::%s::got-offline" % '*', self._on_muc_presence)
        # MUC join errors: Wildcard handler doesn't work reliably, so we register per-room handlers in join_room()
        self.add_event_handler("groupchat_invite", self._on_groupchat_invite)

        # Subscription events (roster management)
        self.add_event_handler("presence_subscribe", self._on_presence_subscribe)
        self.add_event_handler("changed_subscription", self._on_changed_subscription)

        # Presence change events (for contact status updates)
        self.add_event_handler("presence_available", self._on_presence_changed)
        self.add_event_handler("presence_unavailable", self._on_presence_changed)
        self.add_event_handler("changed_status", self._on_presence_changed)

        # XEP-0172: User Nickname (PEP events)
        self.add_event_handler("user_nick_publish", self._on_user_nick_publish)
        self.logger.info("Registered event handler for 'user_nick_publish'")

        if self.omemo_enabled:
            self.add_event_handler("omemo_initialized", self._on_omemo_initialized)

    async def _on_omemo_initialized(self, event):
        """Handler for OMEMO initialization completion."""
        self.omemo_ready = True
        self.logger.info("OMEMO encryption initialized and ready")

    async def _on_session_start(self, event):
        """Handler for successful connection."""
        print(f"[DEBUG] DrunkXMPP._on_session_start CALLED for {self.boundjid}")
        self.logger.info(f"Connected to XMPP server as {self.boundjid.bare}")
        self._connection_state = True  # Mark as connected
        self.reconnect_attempts = 0

        # Advertise Jingle capabilities via XEP-0115 Entity Capabilities
        # Required for Conversations app to show call button and accept calls
        self.plugin['xep_0030'].add_feature('urn:xmpp:jingle:1')  # Jingle base (XEP-0166)
        self.plugin['xep_0030'].add_feature('urn:xmpp:jingle:apps:rtp:1')  # RTP Sessions (XEP-0167)
        self.plugin['xep_0030'].add_feature('urn:xmpp:jingle:apps:rtp:audio')  # Audio support
        self.plugin['xep_0030'].add_feature('urn:xmpp:jingle:transports:ice-udp:1')  # ICE-UDP transport (XEP-0176)
        self.plugin['xep_0030'].add_feature('urn:xmpp:jingle:apps:dtls:0')  # DTLS-SRTP encryption (XEP-0320)
        self.plugin['xep_0030'].add_feature('urn:xmpp:jingle-message:0')  # Jingle Message Initiation (XEP-0353)

        # Set client identity (NOT bot - Conversations refuses to call bots!)
        # Remove default "bot" identity and add "pc" (personal computer) type
        self.plugin['xep_0030'].del_identity(category='client', itype='bot')
        self.plugin['xep_0030'].add_identity(category='client', itype='pc', name='DrunkXMPP')

        self.logger.info("Jingle features advertised in disco#info")

        # Setup Jingle call handlers
        self._setup_call_handlers()

        # Enable Message Carbons before sending presence
        try:
            await self.plugin['xep_0280'].enable()
            self.logger.info("Message Carbons enabled")
        except Exception as e:
            self.logger.warning(f"Failed to enable Message Carbons: {e}")

        # Update capabilities and broadcast new presence with updated caps hash
        # This ensures Jingle features are included in the caps hash
        await self.plugin['xep_0115'].update_caps()

        # update_caps() already sends presence with broadcast=True, but we send again
        # to ensure all resources get updated presence
        self.send_presence()
        await self.get_roster()

        # Publish nickname if configured (XEP-0172)
        if self.own_nickname:
            await self.publish_nickname()

        # Fetch bookmarks from server (XEP-0402)
        if self.on_bookmarks_received_callback:
            try:
                bookmarks = await self.get_bookmarks()
                await self.on_bookmarks_received_callback(bookmarks)
            except Exception as e:
                self.logger.warning(f"Failed to fetch bookmarks: {e}")

        await self._join_all_rooms()

    async def _on_session_resumed(self, event):
        """Handler for session resumption (XEP-0198)."""
        self.logger.info("XMPP session resumed (XEP-0198 stream resumption)")
        self._connection_state = True  # Mark as connected
        self.reconnect_attempts = 0
        # joined_rooms and omemo_ready are NOT cleared - session state is preserved

        # Re-announce presence and refresh roster after reconnection
        # These calls are necessary even though session state is preserved, because:
        # 1. OMEMO plugin needs presence/roster events to re-initialize after network reconnect
        # 2. Other clients need to know we're back online
        # 3. Server state may have changed during disconnection
        await self.plugin['xep_0115'].update_caps()
        self.send_presence()
        await self.get_roster()

        # Re-publish nickname after session resumption (XEP-0172)
        if self.own_nickname:
            await self.publish_nickname()

        self.logger.debug("Presence and roster refreshed after session resumption")

    async def _on_session_end(self, event):
        """Handler for session end - clear state when session truly ends."""
        self.logger.warning("XMPP session ended")
        self.joined_rooms.clear()
        self.omemo_ready = False

    async def _on_disconnected(self, event):
        """Handler for disconnection."""
        self.logger.info("Disconnected from XMPP server")
        self._connection_state = False  # Mark as disconnected
        # Do NOT clear joined_rooms/omemo_ready here - XEP-0198 may resume session
        # State is only cleared in session_end handler when session truly ends

    async def _on_failed_auth(self, event):
        """Handler for authentication failure."""
        self.logger.critical("XMPP authentication failed! Check JID/password.")
        # Don't retry on auth failure
        self.abort()

    def _asyncio_exception_handler(self, loop, context):
        """
        Handle uncaught exceptions in asyncio tasks.

        This catches TimeoutError from slixmpp's internal join_muc_wait() calls
        (via the deprecated joinGroupchat API) and prevents them from polluting logs.

        Args:
            loop: The event loop
            context: Exception context dict with 'message', 'exception', 'future', etc.
        """
        exception = context.get('exception')
        message = context.get('message', 'Unhandled exception in async task')

        # Handle MUC join timeouts gracefully - these are expected when servers are slow
        if isinstance(exception, asyncio.TimeoutError):
            # Check if this is from XEP-0045 join_muc_wait
            task = context.get('future')
            if task and hasattr(task, 'get_coro'):
                coro = task.get_coro()
                coro_name = getattr(coro, '__name__', str(coro))
                if 'join_muc' in coro_name or 'XEP_0045' in str(coro):
                    self.logger.debug(f"MUC join timed out (expected behavior, will retry): {message}")
                    return

        # Log all other exceptions normally
        self.logger.error(f"Async task exception: {message}")
        if exception:
            self.logger.error(f"Exception type: {type(exception).__name__}: {exception}")
            import traceback
            self.logger.error(''.join(traceback.format_exception(type(exception), exception, exception.__traceback__)))

    async def _attempt_connection(self, host: str, port: int, tls: bool,
                                  server_hostname: Optional[str]) -> bool:
        """
        Override slixmpp's connection method to support proxy tunneling.

        This method creates a pre-connected socket through the proxy (if configured)
        and passes it to asyncio's create_connection using the sock parameter.

        No monkey-patching! Clean OOP approach using python-socks library.
        """
        import socket as socket_module
        import ssl

        self.event_when_connected = "connected"
        self._connect_loop_wait += 1
        ssl_context: Optional[ssl.SSLContext] = None
        if tls:
            ssl_context = self.get_ssl_context()

        if self._current_connection_attempt is None:
            return False

        try:
            # If proxy is configured, create pre-connected socket through proxy
            if self.proxy_url:
                self.logger.debug(f"Connecting to {host}:{port} via {self.proxy_type} proxy...")

                # Create proxy object from URL
                proxy = Proxy.from_url(self.proxy_url)

                # Connect through proxy and get socket
                sock = await proxy.connect(dest_host=host, dest_port=port)

                self.logger.debug(f"Proxy tunnel established to {host}:{port}")

                # Use the pre-connected socket with asyncio
                await self.loop.create_connection(
                    lambda: self,
                    sock=sock,
                    ssl=ssl_context,
                    server_hostname=server_hostname if tls else None
                )
            else:
                # No proxy - use default slixmpp behavior
                await self.loop.create_connection(
                    lambda: self,
                    host, port,
                    ssl=ssl_context,
                    server_hostname=server_hostname
                )

            self._connect_loop_wait = 0
            return True

        except socket_module.gaierror:
            self.event('connection_failed',
                       'No DNS record available for %s' % self.default_domain)
            return False
        except OSError as e:
            self.logger.debug('Connection failed: %s', e)
            self.event("connection_failed", e)
            return False
        except Exception as e:
            self.logger.error(f'Proxy connection failed: {e}')
            self.event("connection_failed", e)
            return False

    async def _join_all_rooms(self):
        """Join all configured MUCs."""
        for room_jid, room_config in self.rooms.items():
            await self._join_room(room_jid, room_config)

    async def _join_room(self, room_jid: str, room_config: Dict):
        """
        Join a single MUC room.

        Args:
            room_jid: Room JID (e.g., alerts@conference.example.com)
            room_config: Dict with 'nick' and optional 'password'
        """
        # Skip if already joined (check slixmpp's state, not just our tracker)
        if room_jid in self.plugin['xep_0045'].rooms:
            self.logger.debug(f"Already joined to {room_jid}, skipping")
            return

        nick = room_config.get('nick', 'Bot')
        password = room_config.get('password')
        maxhistory = room_config.get('maxhistory', self.muc_history_default)

        # Handle different password config formats
        if password in [None, '', 'none', 'noauth']:
            password = None

        self.logger.info(f"Joining MUC: {room_jid} as {nick} (history: {maxhistory})")

        try:
            # Use non-blocking join_muc instead of join_muc_wait
            # Our _on_muc_presence handler will update joined_rooms when we receive status code 110
            self.plugin['xep_0045'].join_muc(
                room_jid,
                nick,
                password=password,
                maxhistory=str(maxhistory) if maxhistory > 0 else "0"
            )
            self.logger.debug(f"Join presence sent for {room_jid}, waiting for self-presence confirmation...")
            # joined_rooms will be updated by _on_muc_presence when status code 110 arrives
        except Exception as e:
            self.logger.exception(f"Failed to send join presence for {room_jid}: {e}")

    async def _on_muc_presence(self, presence):
        """Handler for MUC presence updates."""
        try:
            room = presence['from'].bare
            nick = presence['from'].resource
            ptype = presence['type']

            # Check if it's our own presence (status code 110)
            status_codes = presence.get('muc', {}).get('status_codes', [])
            if status_codes and 110 in status_codes:
                if ptype == 'unavailable':
                    self.logger.warning(f"We left/got kicked from {room}")
                    if room in self.joined_rooms:
                        self.joined_rooms.remove(room)
                    # Clear our occupant-id, affiliation, and role for this room
                    if room in self.own_occupant_ids:
                        del self.own_occupant_ids[room]
                    if room in self.own_affiliations:
                        del self.own_affiliations[room]
                    if room in self.own_roles:
                        del self.own_roles[room]
                    # Attempt rejoin after delay only if room is still in our rooms dict
                    # (room gets removed when user explicitly leaves, so we don't rejoin)
                    if room in self.rooms:
                        asyncio.create_task(self._rejoin_room_delayed(room, 5))
                    else:
                        self.logger.debug(f"Room {room} not in rooms dict, skipping rejoin (user left intentionally)")
                else:
                    # Self-presence received - we're joined!
                    # Capture our occupant-id (XEP-0421) for reliable message direction detection
                    occupant_id = presence.get('occupant-id', {}).get('id')
                    if occupant_id:  # Check after assignment (empty string is falsy)
                        self.own_occupant_ids[room] = occupant_id
                        self.logger.debug(f"Captured our occupant-id in {room}: {occupant_id}")

                    # Capture our affiliation and role (XEP-0045)
                    # Available in presence via slixmpp's XEP-0045 plugin
                    xep_0045 = self.plugin['xep_0045']
                    affiliation = xep_0045.get_jid_property(room, nick, 'affiliation') or 'none'
                    role = xep_0045.get_jid_property(room, nick, 'role') or 'participant'

                    # Check if role changed (for voice request approval detection)
                    old_role = self.own_roles.get(room)
                    role_changed = (old_role is not None) and (old_role != role)

                    self.own_affiliations[room] = affiliation
                    self.own_roles[room] = role
                    self.logger.debug(f"Captured our affiliation in {room}: {affiliation}, role: {role}")

                    # Fire role change callback if role changed
                    if role_changed:
                        self.logger.info(f"Role changed in {room}: {old_role} → {role}")
                        if self.on_muc_role_changed_callback:
                            try:
                                await self.on_muc_role_changed_callback(room, old_role, role)
                            except Exception as e:
                                self.logger.error(f"Error in on_muc_role_changed_callback for {room}: {e}")

                    if room not in self.joined_rooms:
                        self.joined_rooms.add(room)
                        self.logger.info(f"Self-presence confirmed in {room} as {nick} - joined successfully")

                        # Fire callback to notify that room is fully joined (presence received)
                        if self.on_muc_joined_callback:
                            try:
                                await self.on_muc_joined_callback(room, nick)
                            except Exception as e:
                                self.logger.error(f"Error in on_muc_joined_callback for {room}: {e}")
                    else:
                        self.logger.debug(f"Self-presence confirmed in {room} as {nick} (room already in joined_rooms)")
            else:
                self.logger.debug(f"Presence in {room}: {nick} - {ptype}")
        except Exception as e:
            self.logger.error(f"Error in _on_muc_presence: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def _on_muc_error(self, presence):
        """
        Handler for MUC presence errors.

        Called when joining a room fails (e.g., banned, members-only, password incorrect).
        """
        room = presence['from'].bare

        # Only process if this is a MUC room we're trying to join
        if room not in self.rooms:
            self.logger.debug(f"MUC error for {room} but room not in rooms dict, ignoring")
            return

        error = presence['error']
        if not error:
            self.logger.debug(f"MUC error presence for {room} but no error element, ignoring")
            return

        condition = error['condition']
        text = error.get('text', '')

        self.logger.error(f"MUC join error for {room}: {condition} - {text}")

        # Fire callback for UI notification
        if self.on_muc_join_error_callback:
            try:
                await self.on_muc_join_error_callback(room, condition, text)
            except Exception as e:
                self.logger.error(f"Error in on_muc_join_error_callback: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    async def _on_groupchat_invite(self, inv):
        """
        Handler for MUC invitations (XEP-0045).

        Invites come in two forms:
        - Direct invites: <x xmlns='http://jabber.org/protocol/muc#user'><invite from='...' /></x>
        - Mediated invites: <x xmlns='jabber:x:conference' jid='...' />
        """
        room_jid = None
        inviter_jid = None
        reason = None
        password = None

        # Direct invite (XEP-0045 §7.8.1)
        muc_user = inv['muc']['invite']
        if muc_user:
            room_jid = inv['from'].bare
            inviter_jid_raw = muc_user.get('from', 'unknown')
            # Convert JID object to string for Qt signal compatibility
            inviter_jid = str(inviter_jid_raw) if inviter_jid_raw else 'unknown'
            reason = muc_user.get('reason', '')
            password = inv['muc'].get('password')

        # Mediated invite (legacy jabber:x:conference)
        x_conference = inv.get('x', {})
        if 'jid' in x_conference:
            room_jid = x_conference['jid']
            inviter_jid = inv['from'].bare
            reason = x_conference.get('reason', '')
            password = x_conference.get('password')

        if room_jid and self.on_muc_invite_callback:
            self.logger.info(f"MUC invite received: {room_jid} from {inviter_jid}")
            try:
                await self.on_muc_invite_callback(room_jid, inviter_jid, reason or '', password)
            except Exception as e:
                self.logger.error(f"Error in MUC invite callback: {e}")

    async def _on_presence_subscribe(self, pres):
        """
        Handler for incoming presence subscription requests (RFC 6121 §3.1.2).

        Called when someone wants to subscribe to our presence.
        """
        from_jid = pres['from'].bare
        self.logger.info(f"Presence subscription request from {from_jid}")

        if self.on_subscription_request_callback:
            try:
                await self.on_subscription_request_callback(from_jid)
            except Exception as e:
                self.logger.error(f"Error in subscription request callback: {e}")

    async def _on_changed_subscription(self, pres):
        """
        Handler for subscription state changes (RFC 6121).

        Called when subscription state changes (e.g., approved, cancelled).
        Updates roster automatically via slixmpp.
        """
        from_jid = pres['from'].bare
        subscription = pres['type']  # 'subscribed', 'unsubscribed', 'unsubscribe'
        self.logger.info(f"Subscription changed for {from_jid}: {subscription}")

        # Roster is automatically updated by slixmpp's roster plugin
        # We just notify the application so it can refresh the UI
        if self.on_subscription_changed_callback:
            try:
                await self.on_subscription_changed_callback(from_jid, subscription)
            except Exception as e:
                self.logger.error(f"Error in subscription changed callback: {e}")

    async def _on_presence_changed(self, presence):
        """
        Handler for presence changes (RFC 6121).

        Called when a contact's presence changes (available, away, unavailable, etc.).
        Fires for: presence_available, presence_unavailable, changed_status events.

        Args:
            presence: Presence stanza from slixmpp
        """
        from_jid = presence['from'].bare  # Strip resource to get bare JID
        ptype = presence['type']  # 'available', 'unavailable', 'error', 'probe', 'subscribe', etc.

        # Determine show value (presence state)
        # For available presence: check 'show' element ('away', 'xa', 'dnd', 'chat', or None/'available')
        # For unavailable presence: always 'unavailable'
        if ptype == 'unavailable':
            show = 'unavailable'
        else:
            # Get show value, default to 'available' if not specified
            show = presence.get('show', 'available')
            # Empty show or 'chat' both mean available
            if show in ('', 'chat'):
                show = 'available'

        self.logger.debug(f"[PRESENCE] {from_jid} is now '{show}' (type: {ptype})")

        # Call user callback if provided
        if self.on_presence_changed_callback:
            try:
                await self.on_presence_changed_callback(from_jid, show)
            except Exception as e:
                self.logger.error(f"Error in presence changed callback: {e}")

    async def _on_user_nick_publish(self, msg):
        """
        Handler for XEP-0172 user nickname PEP events.

        Called when a contact publishes or updates their nickname via PEP.

        Args:
            msg: Message stanza containing the PEP event
        """
        self.logger.info(f"Nickname PEP event received from {msg['from']}")
        try:
            from_jid = msg['from'].bare  # Get bare JID of the contact

            # Extract nickname from PEP event
            # The event contains pubsub items with UserNick payload
            items = msg['pubsub_event']['items']

            # Iterate over items (usually just one for nickname updates)
            for item in items:
                # Access UserNick stanza via plugin_attrib 'nick'
                nick_stanza = item['nick']
                if nick_stanza is not None:
                    # Extract nickname using slixmpp interface
                    nickname = nick_stanza['nick']
                    if nickname:
                        nickname = nickname.strip()
                        if nickname:
                            # Update cache
                            self.nickname_cache[from_jid] = nickname
                            self.logger.info(f"Updated nickname for {from_jid}: {nickname}")

                            # Notify callback
                            if self.on_nickname_update_callback:
                                try:
                                    await self.on_nickname_update_callback(from_jid, nickname)
                                except Exception as e:
                                    self.logger.exception(f"Error in nickname update callback: {e}")

        except Exception as e:
            self.logger.error(f"Error handling nickname PEP event: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def publish_nickname(self, nickname: Optional[str] = None):
        """
        Publish user's own nickname via XEP-0172.

        Args:
            nickname: Nickname to publish. If None, uses self.own_nickname.
                     Empty string clears the nickname.
        """
        try:
            nick_to_publish = nickname if nickname is not None else self.own_nickname

            if nick_to_publish:
                # Publish nickname
                await self.plugin['xep_0172'].publish_nick(nick=nick_to_publish)
                self.logger.info(f"Published nickname: {nick_to_publish}")
                self.own_nickname = nick_to_publish
            else:
                # Clear nickname by calling stop()
                self.plugin['xep_0172'].stop()
                self.logger.info("Cleared published nickname")
                self.own_nickname = None

        except Exception as e:
            self.logger.warning(f"Failed to publish nickname: {e}")

    async def _rejoin_room_delayed(self, room_jid: str, delay: int):
        """Rejoin a room after delay."""
        self.logger.info(f"Will attempt to rejoin {room_jid} in {delay}s")
        await asyncio.sleep(delay)
        if room_jid in self.rooms:
            await self._join_room(room_jid, self.rooms[room_jid])

    async def _handle_room_config_changed(self, room_jid: str):
        """
        Handle room configuration change (status code 104).
        Query disco#info to get the new room name and notify the application.
        Also updates the disco_cache with fresh features.

        Args:
            room_jid: The bare JID of the room that changed
        """
        try:
            # Query disco#info to get updated room information
            # Use get_room_features() instead of raw disco query for consistent formatting
            room_features = await self.get_room_features(room_jid)

            # Update disco_cache with fresh data
            if not hasattr(self, 'disco_cache'):
                self.disco_cache = {}
            self.disco_cache[room_jid] = room_features

            room_name = room_features.get('name')
            if room_name:
                self.logger.info(f"Room {room_jid} config changed - name: {room_name}, "
                               f"nonanonymous: {room_features.get('muc_nonanonymous')}, "
                               f"membersonly: {room_features.get('muc_membersonly')}")

                # Notify application via callback
                if self.on_room_config_changed_callback:
                    try:
                        await self.on_room_config_changed_callback(room_jid, room_name)
                    except Exception as e:
                        self.logger.error(f"Error in room config changed callback: {e}")
            else:
                self.logger.warning(f"Room {room_jid} config changed but no name found in disco#info")

        except IqTimeout:
            self.logger.error(f"Timeout querying disco#info for {room_jid}")
        except IqError as e:
            self.logger.error(f"Error querying disco#info for {room_jid}: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error handling room config change for {room_jid}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def _on_groupchat_subject(self, msg):
        """
        Handler for room subject changes (XEP-0045 §8.1).

        Slixmpp fires this event separately from groupchat_message for messages
        that contain ONLY a subject element (no body/thread).
        """
        room = msg['from'].bare
        subject = msg['subject'] or ''  # Empty string if cleared

        if subject:
            self.logger.info(f"Room subject changed for {room}: {subject[:100]}")
        else:
            self.logger.info(f"Room subject cleared for {room}")

        # Store subject for this room
        self.room_subjects[room] = subject

    async def _on_groupchat_config_status(self, msg):
        """
        Handler for MUC status codes (XEP-0045 status notifications).

        Slixmpp triggers this event when a MUC message contains status codes
        in the <x xmlns='http://jabber.org/protocol/muc#user'> element.

        Status codes indicating room configuration/privacy changes:
          104 = Room configuration changed
          172 = Room does not broadcast presence
          173 = Room discussions are publicly logged
          174 = Room is non-anonymous (full JIDs visible)
        """
        room = msg['from'].bare

        # Check for config/privacy-related status codes
        config_changed_codes = {104, 172, 173, 174}
        if msg['muc']['status_codes'] and config_changed_codes & msg['muc']['status_codes']:
            matched_codes = config_changed_codes & msg['muc']['status_codes']
            self.logger.info(f"Room configuration/privacy changed for {room} (status codes: {matched_codes})")
            await self._handle_room_config_changed(room)

    async def _on_groupchat_message(self, msg):
        """
        Handler for groupchat messages.

        Extracts metadata from the stanza and passes it to the callback.
        The client (GUI/test tool) interprets the metadata and decides what to do.
        """
        room = msg['from'].bare
        nick = msg['from'].resource
        body = msg['body']

        # Build metadata object
        metadata = MessageMetadata(
            message_type='groupchat',
            from_jid=str(msg['from']),
            to_jid=str(msg['to']),
            has_body=bool(body),
            muc_nick=nick
        )

        # Extract message IDs (basic ID + XEP-0359 stable IDs)
        metadata.message_id = msg.get('id')  # Basic message ID attribute
        try:
            metadata.origin_id = msg['origin_id']['id'] if msg['origin_id']['id'] else None
        except (KeyError, TypeError):
            pass
        try:
            metadata.stanza_id = msg['stanza_id']['id'] if msg['stanza_id']['id'] else None
        except (KeyError, TypeError):
            pass

        # Check for history (XEP-0203: Delayed Delivery)
        if msg['delay']['stamp']:
            metadata.is_history = True
            metadata.delay_timestamp = msg['delay']['stamp']

        # Extract occupant-id (XEP-0421)
        try:
            occupant_id = msg['occupant-id']['id']
            metadata.occupant_id = occupant_id if occupant_id else None
        except (KeyError, TypeError):
            pass

        # Check for message correction (XEP-0308)
        try:
            if msg['replace']['id']:
                metadata.is_correction = True
                metadata.replaces_id = msg['replace']['id']
                # Don't process corrections here - handled by _on_message_correction
                self.logger.debug(f"MUC message is a correction, will be handled by correction handler")
                return
        except (KeyError, TypeError):
            pass

        # Check for reactions (XEP-0444) - skip if this is a reaction message
        try:
            if msg['reactions']['id']:
                # This is a reaction message, handled by _on_reactions
                self.logger.debug(f"MUC message is a reaction, will be handled by reaction handler")
                return
        except (KeyError, TypeError):
            pass

        # Check for reply (XEP-0461)
        try:
            if msg['reply']['id']:
                metadata.is_reply = True
                metadata.reply_to_id = msg['reply']['id']
                metadata.reply_to_jid = msg['reply']['to']
        except (KeyError, TypeError):
            pass

        # Check for file attachment (XEP-0066)
        try:
            file_url = msg['oob']['url']
            if file_url:
                metadata.has_attachment = True
                metadata.attachment_url = file_url
                # Check if encrypted (aesgcm://)
                if file_url.startswith('aesgcm://'):
                    metadata.attachment_encrypted = True
        except (KeyError, TypeError):
            pass

        # Check for OMEMO encryption (XEP-0384)
        sender_device_id = None
        if self.omemo_enabled:
            xep_0384 = self.plugin['xep_0384']
            if xep_0384.is_encrypted(msg):
                metadata.is_encrypted = True
                metadata.encryption_type = 'omemo'

                try:
                    # Attempt decryption
                    decrypted_msg, device_info = await xep_0384.decrypt_message(msg)
                    body = decrypted_msg['body']
                    metadata.decrypt_success = True
                    metadata.sender_device_id = device_info.device_id
                    self.logger.debug(f"Decrypted MUC message from {nick} (device {device_info.device_id})")
                except Exception as e:
                    # Decryption failed
                    metadata.decrypt_failed = True
                    body = "[Failed to decrypt OMEMO message]"
                    self.logger.error(f"Failed to decrypt OMEMO message from {nick} in {room}: {e}")

        # Update has_body after potential decryption
        metadata.has_body = bool(body)

        # Check for aesgcm:// URL in body (XEP-0454: OMEMO Media Sharing)
        # This handles encrypted files sent without OOB extension
        if not metadata.has_attachment and metadata.has_body and body:
            # Check if body contains aesgcm:// URL (could have caption on separate line)
            for line in body.split('\n'):
                line = line.strip()
                if line.startswith('aesgcm://'):
                    metadata.has_attachment = True
                    metadata.attachment_url = line
                    metadata.attachment_encrypted = True
                    self.logger.debug(f"Detected aesgcm:// URL in MUC body: {line[:60]}...")
                    break

        # Skip empty messages (no body and no attachment)
        if not metadata.has_body and not metadata.has_attachment:
            return

        # Log message
        if not metadata.is_history:
            enc_str = f"encrypted ({metadata.encryption_type})" if metadata.is_encrypted else "plaintext"
            self.logger.debug(f"MUC message in {room} from {nick}: {body[:50] if body else '(attachment)'}... [{enc_str}]")
        else:
            enc_str = f"encrypted ({metadata.encryption_type})" if metadata.is_encrypted else "plaintext"
            self.logger.debug(f"MUC history in {room} from {nick} @ {metadata.delay_timestamp}: {body[:50] if body else '(attachment)'}... [{enc_str}]")

        # Pass to callback - client decides what to do with the metadata
        if self.on_message_callback:
            try:
                await self.on_message_callback(room, nick, body, metadata, msg)
            except Exception as e:
                self.logger.exception(f"Error in message callback: {e}")

    async def _on_private_message(self, msg):
        """
        Handler for private 1-to-1 messages.

        Extracts metadata from the stanza and passes it to the callback.
        The client (GUI/test tool) interprets the metadata and decides what to do.
        """
        try:
            # Ignore if this is a groupchat message (handled separately)
            if msg['type'] == 'groupchat':
                self.logger.debug(f"Ignoring groupchat message from {msg['from'].bare}")
                return

            from_jid = msg['from'].bare

            # Ignore messages from ourselves (not carbons - those come via carbon handlers)
            if from_jid == self.boundjid.bare:
                self.logger.debug(f"Ignoring message from ourselves ({from_jid})")
                return

            # Build metadata object
            metadata = MessageMetadata(
                message_type='chat',
                from_jid=str(msg['from']),
                to_jid=str(msg['to']),
                has_body=bool(msg['body'])
            )

            # Extract message IDs (basic ID + XEP-0359 stable IDs)
            metadata.message_id = msg.get('id')  # Basic message ID attribute
            try:
                metadata.origin_id = msg['origin_id']['id'] if msg['origin_id']['id'] else None
            except (KeyError, TypeError):
                pass
            try:
                metadata.stanza_id = msg['stanza_id']['id'] if msg['stanza_id']['id'] else None
            except (KeyError, TypeError):
                pass

            # Check for history (XEP-0203: Delayed Delivery)
            if msg['delay']['stamp']:
                metadata.is_history = True
                metadata.delay_timestamp = msg['delay']['stamp']

            # Check for message correction (XEP-0308)
            try:
                if msg['replace']['id']:
                    metadata.is_correction = True
                    metadata.replaces_id = msg['replace']['id']
                    # Don't process corrections here - handled by _on_message_correction
                    self.logger.debug(f"Private message is a correction, will be handled by correction handler")
                    return
            except (KeyError, TypeError):
                pass

            # Check for reactions (XEP-0444) - skip if this is a reaction message
            try:
                if msg['reactions']['id']:
                    # This is a reaction message, handled by _on_reactions
                    self.logger.debug(f"Private message is a reaction, will be handled by reaction handler")
                    return
            except (KeyError, TypeError):
                pass

            # Check for MUC invite (XEP-0045) - skip if this is an invite message
            # Invites are handled by _on_groupchat_invite, not as regular messages
            # Use get_plugin with check=True to avoid creating the element
            muc = msg.get_plugin('muc', check=True)
            if muc is not None:
                invite = muc.get_plugin('invite', check=True)
                if invite is not None:
                    self.logger.debug(f"Private message is a MUC invite, will be handled by invite handler")
                    return

            # Check for reply (XEP-0461)
            try:
                if msg['reply']['id']:
                    metadata.is_reply = True
                    metadata.reply_to_id = msg['reply']['id']
                    metadata.reply_to_jid = msg['reply']['to']
            except (KeyError, TypeError):
                pass

            # Check for file attachment (XEP-0066)
            try:
                file_url = msg['oob']['url']
                if file_url:
                    metadata.has_attachment = True
                    metadata.attachment_url = file_url
                    if file_url.startswith('aesgcm://'):
                        metadata.attachment_encrypted = True
            except (KeyError, TypeError):
                pass

            # Check for OMEMO encryption (XEP-0384)
            body = msg['body']
            if self.omemo_enabled:
                xep_0384 = self.plugin['xep_0384']
                if xep_0384.is_encrypted(msg):
                    metadata.is_encrypted = True
                    metadata.encryption_type = 'omemo'

                    try:
                        # Attempt decryption
                        decrypted_msg, device_info = await xep_0384.decrypt_message(msg)
                        body = decrypted_msg['body']
                        metadata.decrypt_success = True
                        metadata.sender_device_id = device_info.device_id
                        self.logger.debug(f"Decrypted private message from {from_jid} (device {device_info.device_id})")
                    except Exception as e:
                        # Decryption failed
                        metadata.decrypt_failed = True
                        body = "[Failed to decrypt OMEMO message]"
                        self.logger.error(f"Failed to decrypt OMEMO message from {from_jid}: {e}")

            # Update has_body after potential decryption
            metadata.has_body = bool(body)

            # Check for aesgcm:// URL in body (XEP-0454: OMEMO Media Sharing)
            # This handles encrypted files sent without OOB extension
            if not metadata.has_attachment and metadata.has_body and body:
                # Check if body contains aesgcm:// URL (could have caption on separate line)
                for line in body.split('\n'):
                    line = line.strip()
                    if line.startswith('aesgcm://'):
                        metadata.has_attachment = True
                        metadata.attachment_url = line
                        metadata.attachment_encrypted = True
                        self.logger.debug(f"Detected aesgcm:// URL in private message body: {line[:60]}...")
                        break

            # Skip empty messages (no body and no attachment)
            if not metadata.has_body and not metadata.has_attachment:
                self.logger.debug(f"Skipping empty message from {from_jid} (has_body={metadata.has_body}, has_attachment={metadata.has_attachment}, is_encrypted={metadata.is_encrypted}, decrypt_failed={metadata.decrypt_failed})")
                return

            # Log message
            enc_str = f"encrypted ({metadata.encryption_type})" if metadata.is_encrypted else "plaintext"
            self.logger.debug(f"Private message from {from_jid}: {body[:50] if body else '(attachment)'}... [{enc_str}]")

            # Pass to callback - client decides what to do with the metadata
            if self.on_private_message_callback:
                try:
                    await self.on_private_message_callback(from_jid, body, metadata, msg)
                except Exception as e:
                    self.logger.exception(f"Error in private message callback: {e}")
        except Exception as e:
            self.logger.exception(f"Unexpected error in _on_private_message handler from {msg['from'].bare}: {e}")

    def _on_reactions(self, msg):
        """
        Handler for incoming reactions (XEP-0444).

        Calls on_reaction_callback with (metadata, message_id, emojis).
        For MUC: metadata contains occupant_id, nickname, and room JID
        For 1-1: metadata contains sender bare JID
        """
        # Ignore reactions in error stanzas (bounced reactions from failed sends)
        if msg['type'] == 'error':
            self.logger.debug(f"Ignoring reaction in error stanza (bounced): {msg['from']}")
            return

        if not msg['reactions']['id']:
            return

        target_id = msg['reactions']['id']
        reactions = msg['reactions']['values']  # List of emoji strings

        # Build metadata for unified handling
        metadata = MessageMetadata(
            message_type=msg['type'],  # 'chat' or 'groupchat'
            from_jid=str(msg['from'])  # Full JID
        )

        # For MUC: extract nickname and occupant-id (XEP-0421)
        if msg['type'] == 'groupchat':
            metadata.muc_nick = msg['from'].resource  # Nickname
            try:
                occupant_id = msg['occupant-id']['id']
                metadata.occupant_id = occupant_id if occupant_id else None
            except (KeyError, TypeError):
                pass

        if reactions:
            display_from = metadata.muc_nick if metadata.muc_nick else str(msg['from'].bare)
            self.logger.info(f"Reaction from {display_from} to msg {target_id}: {' '.join(reactions)}")
        else:
            display_from = metadata.muc_nick if metadata.muc_nick else str(msg['from'].bare)
            self.logger.info(f"Reactions removed from {display_from} on msg {target_id}")

        # Call callback if registered
        if self.on_reaction_callback:
            try:
                self.on_reaction_callback(metadata, target_id, list(reactions))
            except Exception as e:
                self.logger.error(f"Error in reaction callback: {e}")

    async def _on_message_error(self, msg):
        """
        Handler for message errors.

        Called when a message fails to be delivered (XEP-0045 §7.5, RFC 6121).
        Maps error conditions to user-friendly messages.

        Args:
            msg: Error message stanza
        """
        from_jid = str(msg['from'])
        to_jid = str(msg['to'])

        # Extract origin-id to match the failed message (XEP-0359)
        origin_id = None
        try:
            origin_id = msg['origin_id']['id'] if msg['origin_id']['id'] else None
        except (KeyError, TypeError, AttributeError):
            pass

        # Extract error information
        error_type = msg['error']['type'] if msg['error']['type'] else 'unknown'
        error_condition = msg['error']['condition'] if msg['error']['condition'] else 'unknown-error'
        error_text = msg['error']['text'] if msg['error']['text'] else ''

        # Map error conditions to user-friendly messages
        # Based on XEP-0045 §7.5 (MUC private messages) and RFC 6121
        error_messages = {
            'item-not-found': 'User has left the room or does not exist',
            'not-acceptable': 'You must join the room to send messages',
            'forbidden': 'Room does not allow private messages',
            'service-unavailable': 'Private messages are not supported',
            'recipient-unavailable': 'Recipient is offline or unavailable',
            'remote-server-not-found': 'Server not found',
            'remote-server-timeout': 'Server timeout',
            'bad-request': 'Invalid message format',
        }

        # Prefer server's error text if available, fallback to our mapping
        if error_text:
            friendly_error = error_text
        else:
            friendly_error = error_messages.get(error_condition, error_condition)

        self.logger.warning(
            f"Message error from {from_jid}: {error_type}/{error_condition} - {friendly_error}"
        )

        # Call callback if registered
        if self.on_message_error_callback:
            try:
                await self.on_message_error_callback(from_jid, to_jid, error_type, error_condition, friendly_error, origin_id)
            except Exception as e:
                self.logger.exception(f"Error in message error callback: {e}")

    async def _on_message_correction(self, msg: Message):
        """Handler for incoming message corrections (XEP-0308)."""
        from_jid = msg['from'].bare
        corrected_id = msg['replace']['id']
        new_body = msg['body']

        if not corrected_id:
            return

        # Check if this is our own correction that we can't decrypt
        # For MUC: check the real JID in the MUC user item
        # For 1-1: check if from_jid is ourselves
        is_own_correction = False

        if msg['type'] == 'groupchat':
            # MUC message - check real JID using xep_0045
            room_jid = msg['from'].bare
            nickname = msg['from'].resource

            if nickname:
                xep_0045 = self.plugin['xep_0045']
                real_jid_str = xep_0045.get_jid_property(room_jid, nickname, 'jid')
                if real_jid_str:
                    real_jid = str(real_jid_str).split('/')[0]  # Get bare JID
                    if real_jid == self.boundjid.bare:
                        is_own_correction = True
                        self.logger.debug(f"Skipping own MUC correction (already updated in DB)")
        else:
            # 1-1 message - check sender
            if from_jid == self.boundjid.bare:
                is_own_correction = True
                self.logger.debug(f"Skipping own 1-1 correction (already updated in DB)")

        # If it's our own correction, skip it (DB already updated when we sent it)
        if is_own_correction:
            return

        # Check if message is encrypted
        is_encrypted = False
        if self.omemo_enabled:
            xep_0384 = self.plugin['xep_0384']
            if xep_0384.is_encrypted(msg):
                is_encrypted = True
                try:
                    # Decrypt the correction
                    decrypted_msg, device_info = await xep_0384.decrypt_message(msg)
                    new_body = decrypted_msg['body']
                    self.logger.debug(f"Decrypted message correction from device {device_info.device_id}")
                except Exception as e:
                    self.logger.error(f"Failed to decrypt OMEMO message correction from {from_jid}: {e}")
                    new_body = "[Failed to decrypt OMEMO message]"

        self.logger.info(f"Message correction from {from_jid}: msg {corrected_id} -> \"{new_body[:50]}...\" (encrypted: {is_encrypted})")

        # Call user callback if provided
        if self.on_message_correction_callback:
            try:
                await self.on_message_correction_callback(from_jid, corrected_id, new_body, is_encrypted, msg)
            except Exception as e:
                self.logger.exception(f"Error in message correction callback: {e}")

    async def _on_carbon_received(self, wrapper_msg: Message):
        """
        Handler for received carbon copies (XEP-0280).
        These are messages RECEIVED by another of our resources.
        We process them to keep this device in sync.

        Extracts metadata and passes to the same callback as private messages,
        with is_carbon=True and carbon_type='received'.
        """
        # Extract the actual message from the carbon wrapper
        actual_msg = wrapper_msg['carbon_received']

        if actual_msg is None:
            self.logger.warning("Carbon received wrapper had no forwarded message")
            return

        from_jid = actual_msg['from'].bare

        # Build metadata object (similar to private message handler)
        metadata = MessageMetadata(
            message_type='chat',
            from_jid=str(actual_msg['from']),
            to_jid=str(actual_msg['to']),
            has_body=bool(actual_msg['body']),
            is_carbon=True,
            carbon_type='received'
        )

        # Extract message IDs (basic ID + XEP-0359 stable IDs)
        metadata.message_id = actual_msg.get('id')  # Basic message ID attribute
        try:
            metadata.origin_id = actual_msg['origin_id']['id'] if actual_msg['origin_id']['id'] else None
        except (KeyError, TypeError):
            pass
        try:
            metadata.stanza_id = actual_msg['stanza_id']['id'] if actual_msg['stanza_id']['id'] else None
        except (KeyError, TypeError):
            pass

        # Check for history (XEP-0203)
        if actual_msg['delay']['stamp']:
            metadata.is_history = True
            metadata.delay_timestamp = actual_msg['delay']['stamp']

        # Check for reactions (XEP-0444) - skip if this is a reaction message
        try:
            if actual_msg['reactions']['id']:
                # This is a reaction message, will be handled by _on_reactions
                # slixmpp doesn't natively fire reaction events for carbons, so we have
                # a patch in slixmpp_patches/xep_0280_carbon_reactions.py that fires the event.
                # If reactions start appearing TWICE, it means slixmpp fixed the bug - REMOVE THE PATCH
                self.logger.debug(f"Carbon received is a reaction, expecting slixmpp patch to fire 'reactions' event")
                return
        except (KeyError, TypeError):
            pass

        # Check for reply (XEP-0461)
        try:
            if actual_msg['reply']['id']:
                metadata.is_reply = True
                metadata.reply_to_id = actual_msg['reply']['id']
                metadata.reply_to_jid = actual_msg['reply']['to']
        except (KeyError, TypeError):
            pass

        # Check for file attachment (XEP-0066)
        try:
            file_url = actual_msg['oob']['url']
            if file_url:
                metadata.has_attachment = True
                metadata.attachment_url = file_url
                if file_url.startswith('aesgcm://'):
                    metadata.attachment_encrypted = True
        except (KeyError, TypeError):
            pass

        # Check for OMEMO encryption (XEP-0384)
        body = actual_msg['body']
        if self.omemo_enabled:
            xep_0384 = self.plugin['xep_0384']
            if xep_0384.is_encrypted(actual_msg):
                metadata.is_encrypted = True
                metadata.encryption_type = 'omemo'

                # Check if message is encrypted for us before attempting decryption
                # Parse OMEMO header to get recipient device IDs
                try:
                    from xml.etree import ElementTree as ET
                    encrypted_el = actual_msg.xml.find('.//{eu.siacs.conversations.axolotl}encrypted')
                    if encrypted_el is not None:
                        # Get all recipient device IDs from <key rid="..."> elements
                        recipient_device_ids = set()
                        for key_el in encrypted_el.findall('.//{eu.siacs.conversations.axolotl}key'):
                            rid = key_el.attrib.get('rid')
                            if rid:
                                recipient_device_ids.add(int(rid))

                        # Get our own device ID
                        session_manager = await xep_0384.get_session_manager()
                        our_device_info, _ = await session_manager.get_own_device_information()
                        our_device_id = our_device_info.device_id

                        # Skip if message is not encrypted for us
                        if our_device_id not in recipient_device_ids:
                            self.logger.debug(
                                f"Skipping carbon_received: OMEMO encrypted for devices {recipient_device_ids}, "
                                f"not for our device {our_device_id}"
                            )
                            return
                except Exception as e:
                    self.logger.warning(f"Failed to parse OMEMO header for device check: {e}")
                    # Continue with decryption attempt (might be slixmpp API change)

                try:
                    # Decrypt the message
                    decrypted_msg, device_info = await xep_0384.decrypt_message(actual_msg)
                    body = decrypted_msg['body']
                    actual_msg['body'] = body  # Update for downstream
                    metadata.decrypt_success = True
                    metadata.sender_device_id = device_info.device_id
                    self.logger.debug(f"Decrypted carbon_received from device {device_info.device_id}")
                except Exception as e:
                    metadata.decrypt_failed = True
                    body = "[Failed to decrypt OMEMO message]"
                    actual_msg['body'] = body
                    self.logger.error(f"Failed to decrypt OMEMO carbon_received from {from_jid}: {e}")
                    # Note: We continue storing - this indicates a real OMEMO issue that should be visible

        # Update has_body after potential decryption
        metadata.has_body = bool(body)

        # Check for message correction (XEP-0308) - handle inline for carbons
        try:
            if actual_msg['replace']['id']:
                corrected_id = actual_msg['replace']['id']
                self.logger.info(f"[CARBON RX] Message correction from {from_jid}: msg {corrected_id} -> \"{body[:50] if body else '(no body)'}...\"")

                # Call correction callback directly (slixmpp doesn't fire correction event for carbons)
                if self.on_message_correction_callback:
                    try:
                        await self.on_message_correction_callback(from_jid, corrected_id, body, metadata.is_encrypted, actual_msg)
                    except Exception as e:
                        self.logger.exception(f"Error in carbon_received correction callback: {e}")
                return
        except (KeyError, TypeError):
            pass

        # Check for aesgcm:// URL in body (XEP-0454: OMEMO Media Sharing)
        # This handles encrypted files sent without OOB extension
        if not metadata.has_attachment and metadata.has_body and body:
            # Check if body contains aesgcm:// URL (could have caption on separate line)
            for line in body.split('\n'):
                line = line.strip()
                if line.startswith('aesgcm://'):
                    metadata.has_attachment = True
                    metadata.attachment_url = line
                    metadata.attachment_encrypted = True
                    self.logger.debug(f"Detected aesgcm:// URL in carbon_received body: {line[:60]}...")
                    break

        # Log
        enc_str = f"encrypted ({metadata.encryption_type})" if metadata.is_encrypted else "plaintext"
        self.logger.info(f"[CARBON RX] from {from_jid}: {body[:50] if body else '(attachment)'}... [{enc_str}]")

        # Pass to private message callback with carbon metadata
        if self.on_private_message_callback:
            try:
                await self.on_private_message_callback(from_jid, body, metadata, actual_msg)
            except Exception as e:
                self.logger.exception(f"Error in carbon_received callback: {e}")

    async def _on_carbon_sent(self, wrapper_msg: Message):
        """
        Handler for sent carbon copies (XEP-0280).
        These are messages SENT by another of our resources (other devices).
        We process them to display messages sent from other clients.

        Extracts metadata and passes to the same callback as private messages,
        with is_carbon=True and carbon_type='sent'.
        """
        # Extract the actual message from the carbon wrapper
        actual_msg = wrapper_msg['carbon_sent']

        if actual_msg is None:
            self.logger.warning("Carbon sent wrapper had no forwarded message")
            return

        to_jid = actual_msg['to'].bare

        # Build metadata object (similar to private message handler)
        metadata = MessageMetadata(
            message_type='chat',
            from_jid=str(actual_msg['from']),
            to_jid=str(actual_msg['to']),
            has_body=bool(actual_msg['body']),
            is_carbon=True,
            carbon_type='sent'
        )

        # Extract message IDs (basic ID + XEP-0359 stable IDs)
        metadata.message_id = actual_msg.get('id')  # Basic message ID attribute
        try:
            metadata.origin_id = actual_msg['origin_id']['id'] if actual_msg['origin_id']['id'] else None
        except (KeyError, TypeError):
            pass
        try:
            metadata.stanza_id = actual_msg['stanza_id']['id'] if actual_msg['stanza_id']['id'] else None
        except (KeyError, TypeError):
            pass

        # Check for history (XEP-0203)
        if actual_msg['delay']['stamp']:
            metadata.is_history = True
            metadata.delay_timestamp = actual_msg['delay']['stamp']

        # Check for reactions (XEP-0444) - skip if this is a reaction message
        try:
            if actual_msg['reactions']['id']:
                # This is a reaction message, will be handled by _on_reactions
                # slixmpp doesn't natively fire reaction events for carbons, so we have
                # a patch in slixmpp_patches/xep_0280_carbon_reactions.py that fires the event.
                # If reactions start appearing TWICE, it means slixmpp fixed the bug - REMOVE THE PATCH
                self.logger.debug(f"Carbon sent is a reaction, expecting slixmpp patch to fire 'reactions' event")
                return
        except (KeyError, TypeError):
            pass

        # Check for reply (XEP-0461)
        try:
            if actual_msg['reply']['id']:
                metadata.is_reply = True
                metadata.reply_to_id = actual_msg['reply']['id']
                metadata.reply_to_jid = actual_msg['reply']['to']
        except (KeyError, TypeError):
            pass

        # Check for file attachment (XEP-0066)
        try:
            file_url = actual_msg['oob']['url']
            if file_url:
                metadata.has_attachment = True
                metadata.attachment_url = file_url
                if file_url.startswith('aesgcm://'):
                    metadata.attachment_encrypted = True
        except (KeyError, TypeError):
            pass

        # Check for OMEMO encryption (XEP-0384)
        body = actual_msg['body']
        if self.omemo_enabled:
            xep_0384 = self.plugin['xep_0384']
            if xep_0384.is_encrypted(actual_msg):
                metadata.is_encrypted = True
                metadata.encryption_type = 'omemo'

                # Check if message is encrypted for us before attempting decryption
                # Parse OMEMO header to get recipient device IDs
                try:
                    from xml.etree import ElementTree as ET
                    encrypted_el = actual_msg.xml.find('.//{eu.siacs.conversations.axolotl}encrypted')
                    if encrypted_el is not None:
                        # Get all recipient device IDs from <key rid="..."> elements
                        recipient_device_ids = set()
                        for key_el in encrypted_el.findall('.//{eu.siacs.conversations.axolotl}key'):
                            rid = key_el.attrib.get('rid')
                            if rid:
                                recipient_device_ids.add(int(rid))

                        # Get our own device ID
                        session_manager = await xep_0384.get_session_manager()
                        our_device_info, _ = await session_manager.get_own_device_information()
                        our_device_id = our_device_info.device_id

                        # Skip if message is not encrypted for us
                        if our_device_id not in recipient_device_ids:
                            self.logger.debug(
                                f"Skipping carbon_sent: OMEMO encrypted for devices {recipient_device_ids}, "
                                f"not for our device {our_device_id}"
                            )
                            return
                except Exception as e:
                    self.logger.warning(f"Failed to parse OMEMO header for device check: {e}")
                    # Continue with decryption attempt (might be slixmpp API change)

                try:
                    # Decrypt the message
                    decrypted_msg, device_info = await xep_0384.decrypt_message(actual_msg)
                    body = decrypted_msg['body']
                    actual_msg['body'] = body  # Update for downstream
                    metadata.decrypt_success = True
                    metadata.sender_device_id = device_info.device_id
                    self.logger.debug(f"Decrypted carbon_sent from device {device_info.device_id}")
                except Exception as e:
                    metadata.decrypt_failed = True
                    body = "[Failed to decrypt OMEMO message]"
                    actual_msg['body'] = body
                    self.logger.error(f"Failed to decrypt OMEMO carbon_sent to {to_jid}: {e}")
                    # Note: We continue storing - this indicates a real OMEMO issue that should be visible

        # Update has_body after potential decryption
        metadata.has_body = bool(body)

        # Check for message correction (XEP-0308) - handle inline for carbons
        try:
            if actual_msg['replace']['id']:
                corrected_id = actual_msg['replace']['id']
                self.logger.info(f"[CARBON TX] Message correction to {to_jid}: msg {corrected_id} -> \"{body[:50] if body else '(no body)'}...\"")

                # Call correction callback directly (slixmpp doesn't fire correction event for carbons)
                if self.on_message_correction_callback:
                    try:
                        await self.on_message_correction_callback(to_jid, corrected_id, body, metadata.is_encrypted, actual_msg)
                    except Exception as e:
                        self.logger.exception(f"Error in carbon_sent correction callback: {e}")
                return
        except (KeyError, TypeError):
            pass

        # Check for aesgcm:// URL in body (XEP-0454: OMEMO Media Sharing)
        # This handles encrypted files sent without OOB extension
        if not metadata.has_attachment and metadata.has_body and body:
            # Check if body contains aesgcm:// URL (could have caption on separate line)
            for line in body.split('\n'):
                line = line.strip()
                if line.startswith('aesgcm://'):
                    metadata.has_attachment = True
                    metadata.attachment_url = line
                    metadata.attachment_encrypted = True
                    self.logger.debug(f"Detected aesgcm:// URL in carbon_sent body: {line[:60]}...")
                    break

        # Log
        enc_str = f"encrypted ({metadata.encryption_type})" if metadata.is_encrypted else "plaintext"
        self.logger.info(f"[CARBON TX] to {to_jid}: {body[:50] if body else '(attachment)'}... [{enc_str}]")

        # Pass to private message callback with carbon metadata
        # Client will interpret carbon_type='sent' to store as direction=1 (sent from other device)
        if self.on_private_message_callback:
            try:
                await self.on_private_message_callback(to_jid, body, metadata, actual_msg)
            except Exception as e:
                self.logger.exception(f"Error in carbon_sent callback: {e}")

    def _on_receipt_received(self, msg):
        """
        Handler for delivery receipts (XEP-0184).
        Called when a delivery receipt is received for a message we sent.
        """
        from_jid = msg['from'].bare

        # Extract message ID being acknowledged
        # XEP-0184: <received id="message-id" xmlns="urn:xmpp:receipts"/>
        message_id = msg['receipt']

        if not message_id:
            self.logger.debug(f"Received empty receipt from {from_jid}")
            return

        self.logger.info(f"[RECEIPT] Delivery receipt from {from_jid} for message {message_id}")

        # Call user callback if provided
        if self.on_receipt_received_callback:
            try:
                self.on_receipt_received_callback(from_jid, message_id)
            except Exception as e:
                self.logger.exception(f"Error in receipt callback: {e}")

    def _on_marker_received(self, msg):
        """
        Handler for chat markers (XEP-0333).
        Called when a chat marker (displayed/acknowledged/received) is received.

        Slixmpp's XEP-0333 plugin registers stanza interfaces for each marker type.
        Access via msg['marker_type']['id'] per slixmpp/plugins/xep_0333/stanza.py
        """
        from_jid = msg['from'].bare

        # Check for different marker types using slixmpp stanza interfaces
        # Use get_plugin with check=True to avoid creating elements that don't exist
        marker_type = None
        message_id = None

        # Try each marker type
        received = msg.get_plugin('received', check=True)
        if received is not None and received['id']:
            marker_type = 'received'
            message_id = received['id']
        else:
            displayed = msg.get_plugin('displayed', check=True)
            if displayed is not None and displayed['id']:
                marker_type = 'displayed'
                message_id = displayed['id']
            else:
                acknowledged = msg.get_plugin('acknowledged', check=True)
                if acknowledged is not None and acknowledged['id']:
                    marker_type = 'acknowledged'
                    message_id = acknowledged['id']

        if not message_id or not marker_type:
            self.logger.debug(f"Received incomplete chat marker from {from_jid}")
            return

        self.logger.info(f"[MARKER] Chat marker '{marker_type}' from {from_jid} for message {message_id}")

        # Call user callback if provided
        if self.on_marker_received_callback:
            try:
                self.on_marker_received_callback(from_jid, message_id, marker_type)
            except Exception as e:
                self.logger.exception(f"Error in marker callback: {e}")

    def _on_chat_state(self, msg):
        """
        Handler for chat state notifications (XEP-0085).
        Called when a contact changes their typing/activity state.

        States: active, composing, paused, inactive, gone
        """
        from_jid = msg['from'].bare

        # Extract chat state from message
        # The event name is like "chatstate_composing", extract the state
        state = None
        if msg['chat_state']:
            state = msg['chat_state']

        if not state:
            return

        self.logger.debug(f"[CHAT_STATE] {from_jid} is now '{state}'")

        # Call user callback if provided
        if self.on_chat_state_callback:
            try:
                self.on_chat_state_callback(from_jid, state)
            except Exception as e:
                self.logger.exception(f"Error in chat state callback: {e}")

    def _on_sm_ack_received(self, ack_stanza):
        """
        Handler for XEP-0198 ACK stanzas (<a h="X" />).
        Checks if any of our pending messages are covered by this ACK.
        """
        try:
            # Get the h value (number of stanzas server has received)
            ack_h = int(ack_stanza['h'])
            self.logger.debug(f"[XEP-0198] Server ACK received: h={ack_h}")

            # Check which of our pending messages are now acked
            acked_messages = []
            for msg_id, msg_seq in list(self.pending_server_acks.items()):
                if msg_seq <= ack_h:
                    self.logger.info(f"[SERVER ACK] Message {msg_id} acknowledged by server (seq {msg_seq} <= h {ack_h})")
                    acked_messages.append(msg_id)

                    # Call user callback if provided
                    if self.on_server_ack_callback:
                        # Create a simple object with the message ID
                        class AckInfo:
                            def __init__(self, msg_id):
                                self.msg_id = msg_id
                        self.on_server_ack_callback(AckInfo(msg_id))

            # Remove acked messages from pending
            for msg_id in acked_messages:
                del self.pending_server_acks[msg_id]

        except Exception as e:
            self.logger.debug(f"Could not process server ACK: {e}")

    async def send_to_muc(self, room_jid: str, message: str, message_type: str = 'groupchat') -> str:
        """
        Send an unencrypted message to a MUC room.

        Args:
            room_jid: Room JID
            message: Message text
            message_type: 'groupchat' or 'chat' (for private messages)

        Returns:
            Message ID (for tracking/editing)
        """
        if room_jid not in self.joined_rooms:
            self.logger.warning(f"Not joined to {room_jid}, attempting to join first")
            if room_jid in self.rooms:
                await self._join_room(room_jid, self.rooms[room_jid])
                # Wait a bit for join to complete
                await asyncio.sleep(2)
            else:
                self.logger.error(f"Room {room_jid} not in configuration!")
                raise ValueError(f"Room {room_jid} not configured")

        if room_jid not in self.joined_rooms:
            self.logger.error(f"Failed to join {room_jid}, cannot send message")
            raise RuntimeError(f"Not joined to {room_jid}")

        self.logger.debug(f"Sending message to {room_jid}: {message[:100]}...")
        msg = self.make_message(mto=room_jid, mbody=message, mtype=message_type)

        # Add origin-id for message tracking and editing (XEP-0359)
        msg['origin_id']['id'] = msg['id']

        # Request delivery receipt and chat markers (XEP-0184, XEP-0333)
        msg['request_receipt'] = True
        msg['markable'] = True

        msg.send()

        # Track seq number and request server ACK (XEP-0198)
        msg_id = msg['id']
        if hasattr(self.plugin['xep_0198'], 'seq'):
            self.pending_server_acks[msg_id] = self.plugin['xep_0198'].seq
            self.logger.debug(f"Tracking MUC message {msg_id} with seq {self.plugin['xep_0198'].seq}")
            self.plugin['xep_0198'].request_ack()

        self.logger.info(f"Message sent to {room_jid} (id: {msg_id})")
        return msg_id

    async def send_encrypted_to_muc(self, room_jid: str, message: str) -> str:
        """
        Send an OMEMO-encrypted message to a MUC room.
        Automatically encrypts for all participants in the room.

        Args:
            room_jid: Room JID
            message: Message text to encrypt

        Returns:
            Message ID (for tracking/editing)

        Raises:
            RuntimeError: If OMEMO is not enabled or not ready
            Exception: Various OMEMO-related exceptions from encryption process
        """
        if not self.omemo_enabled:
            raise RuntimeError("OMEMO is not enabled")

        if not self.omemo_ready:
            self.logger.warning("OMEMO not ready yet, waiting...")
            # Wait up to 10 seconds for OMEMO to initialize
            for _ in range(20):
                if self.omemo_ready:
                    break
                await asyncio.sleep(0.5)
            if not self.omemo_ready:
                raise RuntimeError("OMEMO initialization timeout")

        if room_jid not in self.joined_rooms:
            self.logger.warning(f"Not joined to {room_jid}, attempting to join first")
            if room_jid in self.rooms:
                await self._join_room(room_jid, self.rooms[room_jid])
                await asyncio.sleep(2)
            else:
                raise ValueError(f"Room {room_jid} not configured")

        if room_jid not in self.joined_rooms:
            raise RuntimeError(f"Not joined to {room_jid}")

        self.logger.debug(f"Sending OMEMO-encrypted message to {room_jid}: {message[:100]}...")

        # Get XEP-0045 and XEP-0384 plugins
        xep_0045 = self.plugin['xep_0045']
        xep_0384 = self.plugin['xep_0384']

        # Create message stanza
        stanza = self.make_message(mto=room_jid, mtype='groupchat')
        stanza['body'] = message

        # Get all participants in the room
        room_jid_obj = JID(room_jid)
        participants = xep_0045.get_roster(room_jid)

        # Get real JIDs of all participants
        recipient_jids: Set[JID] = set()
        for nick in participants:
            real_jid_str = xep_0045.get_jid_property(room_jid_obj, nick, 'jid')
            if real_jid_str:
                recipient_jids.add(JID(real_jid_str))
            else:
                self.logger.warning(f"Could not get real JID for {nick} in {room_jid}")

        if not recipient_jids:
            self.logger.warning(f"No recipients found in {room_jid}, cannot encrypt")
            raise RuntimeError(f"No recipients found in {room_jid}")

        self.logger.debug(f"Encrypting for {len(recipient_jids)} participants in {room_jid}")

        # Refresh device lists for all participants - use session_manager directly
        session_manager = await xep_0384.get_session_manager()
        for jid in recipient_jids:
            await session_manager.refresh_device_lists(jid.bare)

        # Encrypt the message
        try:
            messages, encryption_errors = await xep_0384.encrypt_message(stanza, recipient_jids)

            if encryption_errors:
                self.logger.warning(f"Encryption errors: {encryption_errors}")

            if not messages:
                raise RuntimeError("Encryption produced no messages")

            # Send all encrypted versions and capture last message ID
            last_msg_id = None
            for namespace, encrypted_msg in messages.items():
                encrypted_msg['eme']['namespace'] = namespace
                encrypted_msg['eme']['name'] = self['xep_0380'].mechanisms.get(namespace, 'OMEMO')

                # Add origin-id for message tracking and editing (XEP-0359)
                encrypted_msg['origin_id']['id'] = encrypted_msg['id']

                # Request delivery receipt and chat markers (XEP-0184, XEP-0333)
                encrypted_msg['request_receipt'] = True
                encrypted_msg['markable'] = True

                encrypted_msg.send()
                last_msg_id = encrypted_msg['id']
                self.logger.info(f"OMEMO-encrypted message sent to {room_jid} (namespace: {namespace}, id: {last_msg_id})")

            # Track seq number and request server ACK (XEP-0198)
            if last_msg_id and hasattr(self.plugin['xep_0198'], 'seq'):
                self.pending_server_acks[last_msg_id] = self.plugin['xep_0198'].seq
                self.logger.debug(f"Tracking MUC message {last_msg_id} with seq {self.plugin['xep_0198'].seq}")
                self.plugin['xep_0198'].request_ack()

            return last_msg_id

        except Exception as e:
            self.logger.exception(f"Failed to encrypt message: {e}")
            raise

    async def send_private_message(self, jid: str, message: str) -> str:
        """
        Send an unencrypted private message to a user.

        Args:
            jid: User JID (can be full or bare)
            message: Message text

        Returns:
            Message ID (for tracking/editing)
        """
        self.logger.debug(f"Sending private message to {jid}: {message[:100]}...")
        msg = self.make_message(mto=jid, mbody=message, mtype='chat')

        # Add origin-id for message tracking and editing (XEP-0359)
        msg['origin_id']['id'] = msg['id']

        # Request delivery receipt (XEP-0184)
        msg['request_receipt'] = True

        # Request chat markers for read receipts (XEP-0333)
        msg['markable'] = True

        msg.send()

        # Track seq number and request server ACK (XEP-0198)
        msg_id = msg['id']
        if hasattr(self.plugin['xep_0198'], 'seq'):
            self.pending_server_acks[msg_id] = self.plugin['xep_0198'].seq
            self.logger.debug(f"Tracking message {msg_id} with seq {self.plugin['xep_0198'].seq}")
            # Request immediate ACK from server
            self.plugin['xep_0198'].request_ack()

        self.logger.info(f"Private message sent to {jid} (id: {msg_id})")
        return msg_id

    async def send_encrypted_private_message(self, jid: str, message: str) -> str:
        """
        Send an OMEMO-encrypted private message to a user.

        Args:
            jid: User JID (can be full or bare)
            message: Message text to encrypt

        Returns:
            Message ID (for tracking/editing)

        Raises:
            RuntimeError: If OMEMO is not enabled or not ready
            Exception: Various OMEMO-related exceptions from encryption process
        """
        if not self.omemo_enabled:
            raise RuntimeError("OMEMO is not enabled")

        if not self.omemo_ready:
            self.logger.warning("OMEMO not ready yet, waiting...")
            for _ in range(20):
                if self.omemo_ready:
                    break
                await asyncio.sleep(0.5)
            if not self.omemo_ready:
                raise RuntimeError("OMEMO initialization timeout")

        self.logger.debug(f"Sending OMEMO-encrypted private message to {jid}: {message[:100]}...")

        xep_0384 = self.plugin['xep_0384']

        # Create message stanza
        recipient_jid = JID(jid)
        stanza = self.make_message(mto=recipient_jid.bare, mtype='chat')
        stanza['body'] = message

        # Request delivery receipt (XEP-0184)
        stanza['request_receipt'] = True

        # Refresh device list for recipient - use session_manager directly
        session_manager = await xep_0384.get_session_manager()
        await session_manager.refresh_device_lists(recipient_jid.bare)

        # Encrypt the message
        try:
            messages, encryption_errors = await xep_0384.encrypt_message(stanza, {recipient_jid})

            if encryption_errors:
                self.logger.warning(f"Encryption errors: {encryption_errors}")

            if not messages:
                raise RuntimeError("Encryption produced no messages")

            # Send all encrypted versions and capture last message ID
            last_msg_id = None
            for namespace, encrypted_msg in messages.items():
                encrypted_msg['eme']['namespace'] = namespace
                encrypted_msg['eme']['name'] = self['xep_0380'].mechanisms.get(namespace, 'OMEMO')

                # Add origin-id for message tracking and editing (XEP-0359)
                encrypted_msg['origin_id']['id'] = encrypted_msg['id']

                # Request delivery receipt and chat markers on encrypted message (XEP-0184, XEP-0333)
                encrypted_msg['request_receipt'] = True
                encrypted_msg['markable'] = True

                encrypted_msg.send()
                last_msg_id = encrypted_msg['id']
                self.logger.info(f"OMEMO-encrypted private message sent to {jid} (namespace: {namespace}, id: {last_msg_id})")

            # Track seq number and request server ACK (XEP-0198)
            if last_msg_id and hasattr(self.plugin['xep_0198'], 'seq'):
                self.pending_server_acks[last_msg_id] = self.plugin['xep_0198'].seq
                self.logger.debug(f"Tracking message {last_msg_id} with seq {self.plugin['xep_0198'].seq}")
                # Request immediate ACK from server
                self.plugin['xep_0198'].request_ack()

            return last_msg_id

        except Exception as e:
            self.logger.exception(f"Failed to encrypt private message: {e}")
            raise

    def is_connected(self) -> bool:
        """Check if connected and authenticated to XMPP server."""
        return self._connection_state

    def is_joined(self, room_jid: str) -> bool:
        """Check if joined to a specific room."""
        return room_jid in self.joined_rooms

    def get_joined_rooms(self) -> List[str]:
        """Get list of currently joined rooms."""
        return list(self.joined_rooms)

    async def join_room(self, room_jid: str, nick: str, password: Optional[str] = None):
        """
        Join a MUC room dynamically.

        Args:
            room_jid: Room JID (e.g., room@conference.server.com)
            nick: Nickname to use in the room
            password: Optional room password
        """
        room_config = {'nick': nick, 'password': password}
        # Add to rooms dict BEFORE sending join (so error handler can find it)
        self.rooms[room_jid] = room_config

        # Register per-room error handler (disposable=True removes it after first use)
        # Note: slixmpp's wildcard handlers (muc::*::presence-error) don't work reliably
        event_name = f"muc::{room_jid}::presence-error"
        self.add_event_handler(event_name, self._on_muc_error, disposable=True)

        await self._join_room(room_jid, room_config)

    def leave_room(self, room_jid: str):
        """
        Leave a MUC room.

        Args:
            room_jid: Room JID to leave
        """
        if room_jid not in self.joined_rooms:
            self.logger.warning(f"Not in room {room_jid}")
            return

        self.logger.info(f"Leaving MUC: {room_jid}")
        self.plugin['xep_0045'].leave_muc(room_jid, "Leaving")

        if room_jid in self.joined_rooms:
            self.joined_rooms.remove(room_jid)

        # Remove from rooms dict so we don't auto-rejoin
        if room_jid in self.rooms:
            del self.rooms[room_jid]

    async def destroy_room(self, room_jid: str, reason: str = '', altroom: str = None):
        """
        Destroy a MUC room (owner only, XEP-0045 §10.9).

        Permanently deletes the room from the server. All occupants will be kicked,
        and the room will no longer be accessible. This action cannot be undone.

        Args:
            room_jid: Room JID to destroy
            reason: Optional reason shown to participants
            altroom: Optional alternate room JID to suggest to participants
        """
        self.logger.info(f"Destroying room: {room_jid}" + (f" (reason: {reason})" if reason else ""))

        # Destroy room on server (raises exception on error)
        await self.plugin['xep_0045'].destroy(room_jid, reason, altroom)

        # Remove from tracking
        if room_jid in self.joined_rooms:
            self.joined_rooms.remove(room_jid)
        if room_jid in self.rooms:
            del self.rooms[room_jid]

        self.logger.info(f"Room destroyed successfully: {room_jid}")

    def send_muc_invite(self, room_jid: str, invitee_jid: str, reason: str = ''):
        """
        Send a mediated MUC invitation (XEP-0045 §7.8.2).

        Note: Passwords are NOT sent in invites. If the room is password-protected,
        the inviter must communicate the password separately (e.g., via direct message).

        Args:
            room_jid: Room JID to invite user to
            invitee_jid: JID of user to invite
            reason: Optional reason for the invitation
        """
        self.logger.info(f"Sending MUC invite: {invitee_jid} to {room_jid}" + (f" (reason: {reason})" if reason else ""))
        self.plugin['xep_0045'].invite(room_jid, invitee_jid, reason)

    async def change_room_subject(self, room_jid: str, subject: str) -> bool:
        """
        Change the subject/topic of a MUC room (XEP-0045 §8.1).

        Sends a groupchat message with a <subject> element to change the
        room's current topic. Only works if the user has permission to
        change the subject (room owners/admins can always change it, others
        depend on room configuration).

        Args:
            room_jid: Room JID
            subject: New subject text (can be empty string to clear)

        Returns:
            True if message was sent successfully, False otherwise
        """
        if room_jid not in self.joined_rooms:
            self.logger.warning(f"Cannot change subject: not in room {room_jid}")
            return False

        self.logger.info(f"Changing subject for {room_jid}: {subject[:50]}...")

        try:
            # Send groupchat message with <subject> element (XEP-0045 §8.1)
            msg = self.make_message(mto=room_jid, mtype='groupchat')
            msg['subject'] = subject
            msg.send()

            self.logger.info(f"Subject change sent for {room_jid}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to change subject for {room_jid}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    async def request_room_membership(self, room_jid: str, nickname: str, reason: str = ""):
        """
        Request membership in a members-only room (XEP-0045 §7.10).

        Uses XEP-0077 in-band registration to request membership. The room
        server may auto-approve or queue the request for admin approval.

        Args:
            room_jid: Room JID to request membership from
            nickname: Desired nickname for the room
            reason: Optional reason/message for room admin

        Returns:
            Dict with 'success' (bool) and 'error' (str or None)
        """
        self.logger.info(f"Requesting membership for {room_jid} with nickname '{nickname}'")

        try:
            # Call the patched XEP-0045 method (added by slixmpp_patches/xep_0045_membership.py)
            result = await self.plugin['xep_0045'].request_room_membership(
                room_jid,
                nickname,
                reason
            )

            if result['success']:
                self.logger.info(f"Membership request sent successfully to {room_jid}")
            else:
                self.logger.warning(f"Membership request failed for {room_jid}: {result.get('error')}")

            return result

        except Exception as e:
            self.logger.error(f"Error requesting membership for {room_jid}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {
                'success': False,
                'error': f"Unexpected error: {str(e)}"
            }

    def connect(self, address=None, **kwargs):
        """
        Override connect to clear user_disconnected flag.

        When user explicitly calls connect(), clear the user_disconnected flag
        so automatic reconnection (via XEP-0199 keepalive) can work if connection drops.

        Args:
            address: Optional tuple (host, port) for manual server override
            **kwargs: Additional arguments (host, port) passed by slixmpp internals
        """
        self.user_disconnected = False
        self.reconnect_attempts = 0  # Reset reconnect counter on manual connect
        self.logger.info("Connecting to XMPP server...")

        # XEP-0199 keepalive auto-enables on session_start/session_resumed events

        if address:
            # Called as connect((host, port))
            return super().connect(host=address[0], port=address[1])
        elif kwargs:
            # Called with host/port kwargs (from slixmpp internals)
            return super().connect(**kwargs)
        else:
            # Called as connect() - SRV discovery
            return super().connect()

    def disconnect(self, wait=2.0, reason=None, ignore_send_queue=False, disable_auto_reconnect=False):
        """
        Disconnect from XMPP server.

        Args:
            wait: Seconds to wait for disconnect (default 2.0)
            reason: Optional disconnect reason string
            ignore_send_queue: Whether to ignore pending messages
            disable_auto_reconnect: If True, disable XEP-0199 keepalive to prevent automatic reconnection.
                                    Use this for user-initiated disconnects (GUI button, /quit command).
                                    If False (default), keeps auto-reconnect enabled - used by slixmpp's
                                    internal reconnect() flow and for testing reconnection behavior.
        """
        self._connection_state = False  # Mark as disconnected immediately

        if disable_auto_reconnect:
            self.user_disconnected = True
            self.logger.info("Disconnecting from XMPP server (user-initiated, will not auto-reconnect)...")

            # Disable XEP-0199 keepalive to prevent automatic reconnection
            # This ensures user stays offline until they manually reconnect
            if hasattr(self, 'plugin') and 'xep_0199' in self.plugin:
                self.plugin['xep_0199'].disable_keepalive()
                self.logger.debug("XEP-0199 keepalive disabled")
        else:
            self.logger.info("Disconnecting from XMPP server (auto-reconnect may occur)...")

        # Clear joined rooms on disconnect (fixes MUC color staying blue)
        self.joined_rooms.clear()
        self.logger.debug("Cleared joined_rooms on disconnect")

        return super().disconnect(wait=wait, reason=reason, ignore_send_queue=ignore_send_queue)

    def is_omemo_ready(self) -> bool:
        """Check if OMEMO encryption is ready to use."""
        return self.omemo_enabled and self.omemo_ready

    def _get_message_type(self, jid: str) -> str:
        """
        Determine message type based on JID.

        Args:
            jid: JID to check

        Returns:
            'groupchat' if JID is a MUC room, 'chat' otherwise
        """
        return 'groupchat' if jid in self.rooms else 'chat'

    async def block_contact(self, jid: str) -> bool:
        """
        Block a contact (XEP-0191: Blocking Command).
        Uses slixmpp's XEP-0191 plugin.

        Args:
            jid: Contact JID to block (bare JID)

        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Blocking contact: {jid}")

        try:
            # Use slixmpp's XEP-0191 plugin
            await self['xep_0191'].block(jid)
            self.logger.info(f"Successfully blocked {jid}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to block {jid}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    async def unblock_contact(self, jid: str) -> bool:
        """
        Unblock a contact (XEP-0191: Blocking Command).
        Uses slixmpp's XEP-0191 plugin.

        Args:
            jid: Contact JID to unblock (bare JID)

        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Unblocking contact: {jid}")

        try:
            # Use slixmpp's XEP-0191 plugin
            await self['xep_0191'].unblock(jid)
            self.logger.info(f"Successfully unblocked {jid}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to unblock {jid}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    async def get_blocked_contacts(self) -> list:
        """
        Get list of blocked contacts (XEP-0191: Blocking Command).
        Uses slixmpp's XEP-0191 plugin.

        Returns:
            List of blocked JIDs (as strings), empty list on error
        """
        self.logger.info("Retrieving blocked contacts list")

        try:
            # Use slixmpp's XEP-0191 plugin
            blocked_items = await self['xep_0191'].get_blocked()

            # Extract JIDs from the result
            # blocked_items is an Iq stanza with <blocklist> containing BlockItem objects
            jids = []
            if blocked_items and 'blocklist' in blocked_items:
                blocklist = blocked_items['blocklist']
                for item in blocklist:
                    # BlockItem has 'jid' attribute directly
                    if hasattr(item, 'jid') and item.jid:
                        jids.append(str(item.jid))
                    # Fallback: try accessing as dict-like object
                    elif hasattr(item, '__getitem__') and 'jid' in item:
                        jids.append(str(item['jid']))

            self.logger.info(f"Retrieved {len(jids)} blocked contacts")
            return jids
        except Exception as e:
            self.logger.error(f"Failed to get blocked contacts: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []

    async def remove_roster_item(self, jid: str):
        """
        Remove contact from roster (RFC 6121 §2.5).
        Uses slixmpp's built-in del_roster_item() method.

        Args:
            jid: Contact JID to remove
        """
        self.logger.info(f"Removing {jid} from roster")

        try:
            # Use slixmpp's built-in method instead of manual XML
            self.del_roster_item(jid)
            self.logger.info(f"Successfully removed {jid} from roster")
        except Exception as e:
            self.logger.error(f"Failed to remove {jid} from roster: {e}")
            raise

    async def request_subscription(self, jid: str):
        """
        Request presence subscription from a contact (RFC 6121 §3.1.1).

        Sends a 'subscribe' presence stanza to request permission to see
        the contact's presence status.

        Args:
            jid: Contact JID to request subscription from
        """
        self.logger.info(f"Requesting presence subscription from {jid}")

        try:
            # Use slixmpp's built-in method
            self.send_presence_subscription(pto=jid, ptype='subscribe')
            self.logger.info(f"Subscription request sent to {jid}")
        except Exception as e:
            self.logger.error(f"Failed to request subscription from {jid}: {e}")
            raise

    async def approve_subscription(self, jid: str):
        """
        Approve a presence subscription request (RFC 6121 §3.1.3).

        Sends a 'subscribed' presence stanza to approve the contact's
        request to see our presence status.

        Args:
            jid: Contact JID to approve subscription for
        """
        self.logger.info(f"Approving subscription request from {jid}")

        try:
            # Use slixmpp's built-in method
            self.send_presence_subscription(pto=jid, ptype='subscribed')
            self.logger.info(f"Subscription approved for {jid}")
        except Exception as e:
            self.logger.error(f"Failed to approve subscription for {jid}: {e}")
            raise

    async def deny_subscription(self, jid: str):
        """
        Deny a presence subscription request (RFC 6121 §3.1.4).

        Sends an 'unsubscribed' presence stanza to deny the contact's
        request to see our presence status.

        Args:
            jid: Contact JID to deny subscription for
        """
        self.logger.info(f"Denying subscription request from {jid}")

        try:
            # Use slixmpp's built-in method
            self.send_presence_subscription(pto=jid, ptype='unsubscribed')
            self.logger.info(f"Subscription denied for {jid}")
        except Exception as e:
            self.logger.error(f"Failed to deny subscription for {jid}: {e}")
            raise

    async def cancel_subscription(self, jid: str):
        """
        Cancel our presence subscription to a contact (RFC 6121 §3.2).

        Sends an 'unsubscribe' presence stanza to stop receiving the
        contact's presence status.

        Args:
            jid: Contact JID to cancel subscription for
        """
        self.logger.info(f"Cancelling subscription to {jid}")

        try:
            # Use slixmpp's built-in method
            self.send_presence_subscription(pto=jid, ptype='unsubscribe')
            self.logger.info(f"Subscription cancelled for {jid}")
        except Exception as e:
            self.logger.error(f"Failed to cancel subscription to {jid}: {e}")
            raise

    async def revoke_subscription(self, jid: str):
        """
        Revoke a contact's subscription to our presence (RFC 6121 §3.2).

        Sends an 'unsubscribed' presence stanza to revoke permission for
        the contact to see our presence status.

        Args:
            jid: Contact JID to revoke subscription for
        """
        self.logger.info(f"Revoking subscription for {jid}")

        try:
            # Use slixmpp's built-in method
            self.send_presence_subscription(pto=jid, ptype='unsubscribed')
            self.logger.info(f"Subscription revoked for {jid}")
        except Exception as e:
            self.logger.error(f"Failed to revoke subscription for {jid}: {e}")
            raise

