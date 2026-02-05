"""
ConnectionBarrel - Handles XMPP connection management.

Responsibilities:
- Connect/disconnect to XMPP server
- Connection status tracking
- Server discovery (XEP-0030, XEP-0092)
"""

import logging
import base64
import asyncio
from typing import Optional

from drunk_xmpp import DrunkXMPP
from ...db.database import get_db
from ...db.omemo_storage import OMEMOStorageDB


class ConnectionBarrel:
    """Manages XMPP connection for an account."""

    def __init__(self, account_id: int, account_data: dict, db, logger, signals: dict):
        """
        Initialize connection barrel.

        Args:
            account_id: Account ID
            account_data: Account settings from database
            db: Database singleton (direct access)
            logger: Account logger instance
            signals: Dict of Qt signal references for emitting events
        """
        self.account_id = account_id
        self.account_data = account_data
        self.db = db
        self.logger = logger
        self.signals = signals

        # Connection state
        self.client: Optional[DrunkXMPP] = None
        self.connected = False
        self._status = 'disconnected'

        # Server information (queried on connect)
        self.server_version: Optional[dict] = None  # XEP-0092
        self.server_features: Optional[dict] = None  # XEP-0030

    def _set_status(self, status: str):
        """
        Update internal status tracking.
        Note: Does NOT emit signal here - signal is emitted by brewery.py event handlers
        after connection is fully established (session_start/session_resumed).
        This ensures GUI updates happen after connection is truly ready.
        """
        self._status = status
        # Signal emitted by brewery.py: self.connection_state_changed.emit(self.account_id, status)

    def connect(self, callbacks: dict):
        """
        Connect to XMPP server.

        Args:
            callbacks: Dict of callback functions for DrunkXMPP events
                Required keys: on_message_callback, on_private_message_callback,
                on_message_error_callback, on_receipt_received_callback, etc.
        """
        if self.connected:
            if self.logger:
                self.logger.warning("Already connected")
            return

        self._set_status('connecting')

        if self.logger:
            self.logger.info(f"Connecting to XMPP server...")
            self.logger.info(f"  JID: {self.account_data['bare_jid']}")
            self.logger.info(f"  Resource: {self.account_data.get('resource', 'auto')}")
            self.logger.info(f"  OMEMO: {self.account_data.get('omemo_enabled', True)}")

        # Decode password
        password_encoded = self.account_data.get('password', '')
        try:
            password = base64.b64decode(password_encoded).decode()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to decode password: {e}")
            return

        # Build JID with resource
        jid = self.account_data['bare_jid']
        if self.account_data.get('resource'):
            jid = f"{jid}/{self.account_data['resource']}"

        # Load MUC rooms from bookmarks
        rooms = {}
        bookmarks = self.db.fetchall("""
            SELECT j.bare_jid, b.nick, b.password
            FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ?
        """, (self.account_id,))

        for bookmark in bookmarks:
            room_jid = bookmark['bare_jid']
            nick = bookmark['nick']
            # Decode password if present
            room_password = None
            if bookmark['password']:
                try:
                    room_password = base64.b64decode(bookmark['password']).decode()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to decode password for {room_jid}: {e}")

            rooms[room_jid] = {
                'nick': nick,
                'password': room_password
            }

        if self.logger and rooms:
            self.logger.info(f"Loaded {len(rooms)} MUC rooms from bookmarks")

        # Create OMEMO storage backend (DB-based for GUI)
        omemo_storage = None
        if self.account_data.get('omemo_enabled', 1):
            omemo_storage = OMEMOStorageDB(self.db, self.account_id)
            if self.logger:
                self.logger.info(f"OMEMO storage: DB backend (account_id={self.account_id})")

        # Get proxy settings from account data
        proxy_type = self.account_data.get('proxy_type')
        proxy_host = self.account_data.get('proxy_host')
        proxy_port = self.account_data.get('proxy_port')
        proxy_username = self.account_data.get('proxy_username')
        proxy_password = None

        # Decode proxy password if present
        if self.account_data.get('proxy_password'):
            try:
                proxy_password = base64.b64decode(self.account_data.get('proxy_password')).decode()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to decode proxy password: {e}")

        if proxy_type and proxy_host and proxy_port:
            if self.logger:
                self.logger.info(f"Proxy configured: {proxy_type} {proxy_host}:{proxy_port}")

        # Create DrunkXMPP client
        try:
            self.client = DrunkXMPP(
                jid=jid,
                password=password,
                rooms=rooms,
                omemo_storage=omemo_storage,
                on_message_callback=callbacks.get('on_message_callback'),
                on_private_message_callback=callbacks.get('on_private_message_callback'),
                on_message_error_callback=callbacks.get('on_message_error_callback'),
                on_receipt_received_callback=callbacks.get('on_receipt_received_callback'),
                on_marker_received_callback=callbacks.get('on_marker_received_callback'),
                on_server_ack_callback=callbacks.get('on_server_ack_callback'),
                on_chat_state_callback=callbacks.get('on_chat_state_callback'),
                on_presence_changed_callback=callbacks.get('on_presence_changed_callback'),
                on_bookmarks_received_callback=callbacks.get('on_bookmarks_received_callback'),
                on_muc_invite_callback=callbacks.get('on_muc_invite_callback'),
                on_muc_joined_callback=callbacks.get('on_muc_joined_callback'),
                on_muc_join_error_callback=callbacks.get('on_muc_join_error_callback'),
                on_room_config_changed_callback=callbacks.get('on_room_config_changed_callback'),
                on_message_correction_callback=callbacks.get('on_message_correction_callback'),
                on_avatar_update_callback=callbacks.get('on_avatar_update_callback'),
                on_nickname_update_callback=callbacks.get('on_nickname_update_callback'),
                own_nickname=self.account_data.get('nickname'),
                on_reaction_callback=callbacks.get('on_reaction_callback'),
                on_subscription_request_callback=callbacks.get('on_subscription_request_callback'),
                on_subscription_changed_callback=callbacks.get('on_subscription_changed_callback'),
                enable_omemo=bool(self.account_data.get('omemo_enabled', 1)),
                reconnect_max_delay=300,
                keepalive_interval=60,
                proxy_type=proxy_type,
                proxy_host=proxy_host,
                proxy_port=proxy_port,
                proxy_username=proxy_username,
                proxy_password=proxy_password,
            )

            # Don't add duplicate event handlers - drunk-xmpp already has them
            # We'll check connection status through is_connected() instead

            # Add roster update handler to sync roster to database
            self.client.add_event_handler("roster_update", callbacks.get('on_roster_update'))

            # Add session_start handler to auto-join rooms
            self.client.add_event_handler("session_start", callbacks.get('on_session_start'))

            # Add session_resumed handler for XEP-0198 stream resumption
            self.client.add_event_handler("session_resumed", callbacks.get('on_session_resumed'))

            # Add connection event handlers for status bar updates
            self.client.add_event_handler("disconnected", callbacks.get('on_disconnected'))
            self.client.add_event_handler("failed_auth", callbacks.get('on_failed_auth'))

            # Carbon handlers (XEP-0280) are now internal to DrunkXMPP
            # DrunkXMPP calls on_private_message_callback for carbons with metadata.is_carbon=True
            # No need to register external handlers anymore (as of 2025-12-16)

            if self.logger:
                self.logger.info("DrunkXMPP client created")

            # CallBridge will be initialized in on_session_start (after connection)

            # Connect to server
            server = self.account_data.get('server_override')
            port = self.account_data.get('port', 5222)

            if server:
                if self.logger:
                    self.logger.info(f"Connecting to {server}:{port} (manual override)...")
                self.client.connect((server, port))
            else:
                if self.logger:
                    self.logger.info("Connecting via SRV auto-discovery...")
                self.client.connect()

            # Don't set self.connected = True here
            # The on_session_start handler will set it when session actually starts

            if self.logger:
                self.logger.info("Connection initiated successfully (waiting for session_start...)")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Connection failed: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    def disconnect(self, call_bridge=None):
        """
        Disconnect from XMPP server.

        Args:
            call_bridge: Optional CallBridge instance to disconnect
        """
        if not self.client:
            if self.logger:
                self.logger.warning("Cannot disconnect: no client instance")
            return

        if self.logger:
            self.logger.info(f"Disconnecting from XMPP server... (connected={self.connected})")

        try:
            # User-initiated disconnect from GUI - disable auto-reconnect
            # (Network failures trigger reconnect internally without calling this method)
            self.client.disconnect(disable_auto_reconnect=True)
            # Don't set self.connected = False here immediately
            # The on_disconnected_event handler will set it when disconnect actually completes

            # Disconnect CallBridge if present
            if call_bridge:
                try:
                    asyncio.create_task(call_bridge.disconnect())
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error disconnecting CallBridge: {e}")

            if self.logger:
                self.logger.info("Disconnect initiated")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Disconnect error: {e}")
            # On error, ensure we update the flag
            self.connected = False

    def is_connected(self) -> bool:
        """
        Check if connected to XMPP server.

        Note: This is not reliable for UI status display due to TCP timeout delays.
        Returns the internal connected flag.
        """
        return self.connected

    async def query_server_info(self):
        """
        Query server version (XEP-0092) and features (XEP-0030) on connect/reconnect.

        Server may change config between connections (restarts, upgrades), so we
        re-query on every connect. Results stored in-memory only.

        Logs at INFO level for query fact, DEBUG level for full details.
        """
        if not self.client:
            return

        # Query server version (XEP-0092)
        try:
            if self.logger:
                self.logger.info("Querying server version (XEP-0092)...")

            self.server_version = await self.client.get_server_version()

            if self.server_version.get('error'):
                if self.logger:
                    self.logger.warning(f"Server version query failed: {self.server_version['error']}")
            else:
                if self.logger:
                    version_str = f"{self.server_version['name']} {self.server_version['version']}"
                    if self.server_version['os']:
                        version_str += f" ({self.server_version['os']})"
                    self.logger.info(f"Server version: {version_str}")
                    self.logger.debug(f"Server version details: {self.server_version}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to query server version: {e}")
            self.server_version = {'error': str(e)}

        # Query server features (XEP-0030)
        try:
            if self.logger:
                self.logger.info("Querying server features (XEP-0030)...")

            self.server_features = await self.client.get_server_features()

            if self.server_features.get('error'):
                if self.logger:
                    self.logger.warning(f"Server features query failed: {self.server_features['error']}")
            else:
                if self.logger:
                    xep_count = len(self.server_features['xeps'])
                    feature_count = len(self.server_features['features'])
                    self.logger.info(f"Server features: {xep_count} recognized XEPs, {feature_count} total features")
                    self.logger.debug(f"Server identities: {self.server_features['identities']}")
                    # Format XEP list for logging
                    xep_list = [f"XEP-{x['number']}: {x['name']}" for x in self.server_features['xeps']]
                    self.logger.debug(f"Recognized XEPs: {xep_list}")
                    self.logger.debug(f"All features: {self.server_features['features']}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to query server features: {e}")
            self.server_features = {'error': str(e)}

    @staticmethod
    async def test_connection(jid, password, server=None, port=None,
                            proxy_type=None, proxy_host=None, proxy_port=None,
                            proxy_username=None, proxy_password=None):
        """
        Test XMPP connection with given credentials (static utility method).

        Args:
            jid: Bare JID (user@server.com)
            password: Plain text password
            server: Optional server override
            port: Optional port override
            proxy_type: Optional proxy type ('SOCKS5', 'HTTP', or None)
            proxy_host: Optional proxy host
            proxy_port: Optional proxy port
            proxy_username: Optional proxy username
            proxy_password: Optional proxy password

        Returns:
            dict: {
                'success': bool,
                'error': str or None,
                'server_info': str (server address used)
            }
        """
        logger = logging.getLogger('siproxylin.connection_test')
        test_result = {"success": False, "error": None, "server_info": None}
        client = None

        try:
            # Create temporary client for testing
            client = DrunkXMPP(
                jid=jid,
                password=password,
                rooms={},
                enable_omemo=False,
                reconnect_max_delay=10,
                keepalive_interval=60,
                proxy_type=proxy_type,
                proxy_host=proxy_host,
                proxy_port=proxy_port,
                proxy_username=proxy_username,
                proxy_password=proxy_password
            )

            client.connection_timeout = 15

            # Event for synchronization
            connected_event = asyncio.Event()

            async def on_session_start(event):
                test_result["success"] = True
                connected_event.set()

            async def on_failed_auth(event):
                test_result["error"] = "Authentication failed. Check JID and password."
                connected_event.set()

            client.add_event_handler("session_start", on_session_start)
            client.add_event_handler("failed_auth", on_failed_auth)

            # Connect
            if server:
                logger.info(f"Test connection: connecting to {server}:{port}")
                client.connect((server, port))
                test_result["server_info"] = f"{server}:{port}"
            else:
                logger.info(f"Test connection: auto-discovering server for {jid}")
                client.connect()
                test_result["server_info"] = "Auto-discovered"

            # Wait for result with timeout
            try:
                await asyncio.wait_for(connected_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                test_result["error"] = "Connection timeout. Server not responding."

        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            test_result["error"] = str(e)

        finally:
            # Cleanup
            if client:
                try:
                    client.disconnect(wait=True)
                    await asyncio.sleep(1.0)  # Give time for cleanup

                    # Cancel any remaining connection attempts
                    if hasattr(client, 'cancel_connection_attempt'):
                        client.cancel_connection_attempt()
                except Exception as e:
                    logger.debug(f"Test cleanup error (ignored): {e}")

        return test_result
