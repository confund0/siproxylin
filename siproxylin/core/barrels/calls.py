"""
CallBarrel - Handles audio/video call operations.

Responsibilities:
- Call setup and teardown (XEP-0353 Jingle Message Initiation)
- Jingle signaling (XEP-0166/0167) via JingleAdapter
- CallBridge management (Go service integration via gRPC)
- Call timers and state management
- Audio device management
- Call history logging (Phase 4)
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any
from PySide6.QtCore import QTimer
from slixmpp import JID

from ...utils.paths import get_paths
from ...core.constants import CallState, CallDirection, CallType
from ...db.database import get_db

# Try to import call dependencies (CallBridge for gRPC → Go service, JingleAdapter for XEP-0166/0167)
try:
    from drunk_call_hook import CallBridge
    from drunk_call_hook.protocol.jingle import JingleAdapter
    from drunk_call_hook.protocol.features.trickle_ice import IncomingCallState
    CALLS_AVAILABLE = True
except ImportError as e:
    CALLS_AVAILABLE = False
    # Log import failure with full context for debugging
    logger = logging.getLogger('siproxylin.calls')
    logger.error("=" * 80)
    logger.error("CALL FUNCTIONALITY DISABLED - Import failure")
    logger.error("=" * 80)
    logger.error(f"Failed to import call dependencies: {e}")
    logger.error("Expected modules:")
    logger.error("  - CallBridge (from drunk_call_hook): gRPC client for Go call service")
    logger.error("  - JingleAdapter (from drunk_call_hook.protocol.jingle): Jingle signaling (XEP-0166/0167)")
    logger.error("Impact: Audio/video calling will be completely disabled")
    logger.error("Possible causes:")
    logger.error("  1. drunk_call_hook module not installed or not in Python path")
    logger.error("  2. Go call service dependencies missing (grpc, protobuf)")
    logger.error("  3. Module import error (check traceback below)")
    import traceback
    logger.debug("Import traceback:")
    logger.debug(traceback.format_exc())
    logger.error("=" * 80)


class CallBarrel:
    """Manages audio/video calls for an account."""

    @staticmethod
    def _extract_turn_server(ice_servers: list) -> tuple[str, str, str]:
        """
        Extract first TURN server from XEP-0215 ice_servers list.

        Args:
            ice_servers: List of ICE server dicts from format_ice_servers()
                        Format: [{"urls": ["turn:..."], "username": "...", "credential": "..."}]

        Returns:
            Tuple of (turn_server_url, turn_username, turn_password)
            TURN server URL will have scheme prefix stripped (host:port only)
            Username and password will be URL-encoded for use in TURN URLs
            Empty strings if no TURN server found.
        """
        from urllib.parse import quote

        for server in ice_servers:
            urls = server.get('urls', [])
            for url in urls:
                if url.startswith('turn:'):
                    # Strip "turn:" prefix - C++ will add turn:// with credentials
                    # URL-encode credentials because they may contain special chars like ':'
                    return (
                        url[5:],  # Remove "turn:" prefix
                        quote(server.get('username', ''), safe=''),
                        quote(server.get('credential', ''), safe='')
                    )
                elif url.startswith('turns:'):
                    # Strip "turns:" prefix
                    return (
                        url[6:],  # Remove "turns:" prefix
                        quote(server.get('username', ''), safe=''),
                        quote(server.get('credential', ''), safe='')
                    )
        return ('', '', '')

    def __init__(self, account_id: int, client, app_logger, signals: dict, account_data: dict = None):
        """
        Initialize call barrel.

        Args:
            account_id: Account ID
            client: DrunkXMPP client instance (must be set before use)
            app_logger: Account logger instance
            signals: Dict of Qt signal references for emitting events
            account_data: Account settings dict (for proxy configuration)
        """
        self.account_id = account_id
        self.client = client  # Will be None initially, set by brewery after connection
        self.logger = app_logger
        self.signals = signals

        # Extract proxy settings from account_data
        self.proxy_type = None
        self.proxy_host = None
        self.proxy_port = None
        self.proxy_username = None
        self.proxy_password = None

        if account_data:
            import base64
            self.proxy_type = account_data.get('proxy_type')
            self.proxy_host = account_data.get('proxy_host')
            self.proxy_port = account_data.get('proxy_port')
            self.proxy_username = account_data.get('proxy_username')
            # Decode proxy password (stored as base64 in database)
            encoded_password = account_data.get('proxy_password')
            if encoded_password:
                try:
                    self.proxy_password = base64.b64decode(encoded_password).decode()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to decode proxy password: {e}")

        # Call state (moved from brewery.__init__)
        self.call_bridge = None  # CallBridge instance (Go service)
        self.jingle_adapter = None  # JingleAdapter for Jingle signaling
        self.pending_call_offers: Dict[str, str] = {}  # session_id → sdp_offer (temp storage)
        self.pending_call_media: Dict[str, list] = {}  # session_id → media types (for outgoing calls)
        self.accepted_calls: set = set()  # Track calls user accepted (sent proceed, waiting for session-initiate)
        self.incoming_call_timers: Dict[str, Any] = {}  # session_id → QTimer (60s timeout for unanswered calls)
        self.outgoing_call_timers: Dict[str, Any] = {}  # session_id → QTimer (60s timeout for unanswered calls)

        # Call logging (Phase 4)
        self.call_db_ids: Dict[str, int] = {}  # session_id → call.id (for updating call state)
        self.call_start_times: Dict[str, int] = {}  # session_id → start timestamp
        self.call_peer_jids: Dict[str, str] = {}  # session_id → peer_jid (for logging)

    # =========================================================================
    # Call Logging (Phase 4)
    # =========================================================================

    def _log_call_to_db(self, session_id: str, peer_jid: str, direction: int, state: int):
        """
        Log call to database (create new call record + content_item).

        Args:
            session_id: Session ID
            peer_jid: Peer JID (can be bare or full JID)
            direction: CallDirection.INCOMING or CallDirection.OUTGOING
            state: CallState value
        """
        try:
            db = get_db()

            # Always use bare JID for conversations
            # Incoming calls may have full JID (user@domain/resource)
            # Outgoing calls use bare JID (user@domain)
            # Both must map to the same conversation
            bare_jid = JID(peer_jid).bare

            # Get or create JID
            counterpart_jid_id = db.get_or_create_jid(bare_jid)

            # Get or create conversation (1-to-1 chat)
            conversation_id = db.get_or_create_conversation(self.account_id, counterpart_jid_id, conv_type=0)

            # Prepare timestamps
            now = int(time.time())
            self.call_start_times[session_id] = now
            self.call_peer_jids[session_id] = peer_jid

            # Insert call record with safe default state
            # Always insert as ENDED - actual runtime state tracked in memory
            # Only final states get persisted via updates
            from siproxylin.core.constants import CallState
            call_id, content_item_id = db.insert_call(
                account_id=self.account_id,
                counterpart_id=counterpart_jid_id,
                conversation_id=conversation_id,
                direction=direction,
                time=now,
                local_time=now,
                end_time=None,  # Will be set when call actually ends
                encryption=1,  # DTLS-SRTP (always encrypted)
                state=CallState.ENDED.value,  # Safe default - no intermediate states persisted
                call_type=CallType.AUDIO.value,  # Phase 1-4: audio only
                counterpart_resource=None,
                our_resource=None
            )

            # Store call_id for later updates
            self.call_db_ids[session_id] = call_id

            if self.logger:
                self.logger.debug(f"Logged call to DB: call_id={call_id}, session={session_id}, state={state}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to log call to database: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    def _update_call_state_in_db(self, session_id: str, state: int, end_time: Optional[int] = None):
        """
        Update call state in database.

        Only persist final states that can survive a restart.
        Intermediate states (RINGING, ESTABLISHING, IN_PROGRESS) are tracked in memory only.

        Args:
            session_id: Session ID
            state: New CallState value
            end_time: End timestamp (optional)
        """
        try:
            # Only persist final states - intermediate states can't survive restart
            # Skip: RINGING (0), ESTABLISHING (1), IN_PROGRESS (2)
            from siproxylin.core.constants import CallState
            if state in (CallState.RINGING.value, CallState.ESTABLISHING.value, CallState.IN_PROGRESS.value):
                if self.logger:
                    self.logger.debug(f"Skipping DB update for intermediate state {state} (session {session_id})")
                return

            call_id = self.call_db_ids.get(session_id)
            if not call_id:
                if self.logger:
                    self.logger.warning(f"Cannot update call state: no call_id for session {session_id}")
                return

            db = get_db()
            db.update_call_state(call_id, state, end_time)

            if self.logger:
                self.logger.debug(f"Updated call state in DB: call_id={call_id}, state={state}, end_time={end_time}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to update call state in database: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    # =========================================================================
    # Call Functionality (DrunkCALL Integration)
    # =========================================================================

    def _load_audio_settings(self) -> tuple[str, str, str, str, dict]:
        """
        Load audio device and processing settings from calls.json.

        Returns:
            Tuple of (microphone_device_id, microphone_display_name, speakers_device_id, speakers_display_name, audio_processing_settings).
            Empty strings = system default.
            audio_processing_settings is a dict with: echo_cancel, echo_suppression_level,
            noise_suppression, noise_suppression_level, gain_control
        """
        # Default audio processing settings (all enabled with moderate levels)
        audio_processing = {
            'echo_cancel': True,
            'echo_suppression_level': 1,  # 0=low, 1=moderate, 2=high
            'noise_suppression': True,
            'noise_suppression_level': 1,  # 0=low, 1=moderate, 2=high, 3=very-high
            'gain_control': True
        }

        try:
            settings_path = get_paths().config_dir / 'calls.json'
            if settings_path.exists():
                with open(settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)

                    # Handle both dict (new format) and string (old format) for backward compatibility
                    mic_setting = settings.get('microphone_device', '')
                    speakers_setting = settings.get('speakers_device', '')

                    if isinstance(mic_setting, dict):
                        mic_id = mic_setting.get('device_id', '')
                        mic_display = mic_setting.get('display_name', '')
                    else:
                        mic_id = mic_setting  # Old format (string)
                        mic_display = ''

                    if isinstance(speakers_setting, dict):
                        speakers_id = speakers_setting.get('device_id', '')
                        speakers_display = speakers_setting.get('display_name', '')
                    else:
                        speakers_id = speakers_setting  # Old format (string)
                        speakers_display = ''

                    # Load audio processing settings if present
                    if 'audio_processing' in settings:
                        audio_processing.update(settings['audio_processing'])

                    if self.logger:
                        self.logger.debug(f"Loaded audio settings: mic={mic_id or 'default'}, speakers={speakers_id or 'default'}, processing={audio_processing}")
                    return mic_id, mic_display, speakers_id, speakers_display, audio_processing
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to load audio settings: {e}")

        return '', '', '', '', audio_processing  # Default to system defaults

    async def _setup_call_functionality(self):
        """
        Get singleton CallBridge (Go service) for audio/video calls.

        Note: CallBridge is shared by ALL accounts (only one Go service runs).
        """
        if not self.client:
            if self.logger:
                self.logger.error("Cannot setup calls: no XMPP client")
            return

        if not CALLS_AVAILABLE:
            if self.logger:
                self.logger.error("=" * 80)
                self.logger.error("Cannot initialize call functionality - dependencies unavailable")
                self.logger.error("=" * 80)
                self.logger.error("CALLS_AVAILABLE = False (import failed during module load)")
                self.logger.error("This means drunk_call_hook failed to import (see startup logs for details)")
                self.logger.error("Expected: CallBridge + JingleAdapter + Go call service")
                self.logger.error("Impact: Audio/video calling disabled for this account")
                self.logger.error("=" * 80)
            return

        try:
            # Create CallBridge (gRPC client for this account)
            # Go service is started by MainWindow
            if self.logger:
                self.logger.debug("Creating CallBridge for this account...")

            self.call_bridge = CallBridge(
                logger=self.logger,
                on_ice_candidate=None,  # Will be set by JingleAdapter
                on_connection_state=None  # Will be set by JingleAdapter
            )

            # Connect to Go service
            success = await self.call_bridge.connect()
            if not success:
                raise RuntimeError("Failed to connect CallBridge to Go service")

            # Create JingleAdapter for Jingle signaling (XEP-0166/0167)
            self.jingle_adapter = JingleAdapter(
                xmpp_client=self.client,
                call_bridge=self.call_bridge,
                on_incoming_call=self._on_jingle_incoming_call,
                on_call_answered=self._on_jingle_call_answered,
                on_call_terminated=self._on_jingle_call_terminated,
                on_ice_candidate_received=self._on_ice_candidate_received,
                on_call_state_changed=self._on_call_state_changed,
                on_candidates_ready=self._on_candidates_ready,
                logger=self.logger
            )

            # Set up callbacks from DrunkXMPP.CallsMixin (XEP-0353) → AccountManager
            self.client.on_call_incoming = self._on_xmpp_call_incoming
            self.client.on_call_accepted = self._on_xmpp_call_accepted
            self.client.on_call_terminated = self._on_xmpp_call_terminated

            if self.logger:
                self.logger.debug("CallBridge + JingleAdapter ready (audio/video calling enabled)")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to get CallBridge: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            self.call_bridge = None

    def _on_ice_candidate_from_go(self, session_id: str, candidate: dict):
        """
        Handle ICE candidate from Go service.

        Args:
            session_id: Session ID
            candidate: ICE candidate dict
        """
        # TODO: Send via Jingle transport-info
        if self.logger:
            self.logger.debug(f"ICE candidate from Go for session {session_id}")

    def _on_connection_state_from_go(self, session_id: str, state: str):
        """
        Handle connection state change from Go service.

        Args:
            session_id: Session ID
            state: Connection state (e.g., 'connected', 'failed', 'closed')
        """
        if self.logger:
            self.logger.debug(f"Call connection state: {state} (session {session_id})")

        # TODO: Emit Qt signal to update GUI

    # ============================================================================
    # XEP-0353: Jingle Message Initiation Callbacks (from DrunkXMPP.CallsMixin)
    # ============================================================================

    async def _on_xmpp_call_incoming(self, peer_jid: str, session_id: str, media: list):
        """
        Handle incoming call proposal (XEP-0353 <propose>).

        This is called BEFORE the actual Jingle session-initiate.
        Flow: propose → User accepts → proceed → session-initiate
        """
        if self.logger:
            self.logger.info(f"Incoming call from {peer_jid}: {media} (session {session_id})")

        # Import here to avoid circular dependency
        from ..brewery import get_account_brewery

        # ========================================================================
        # ALPHA LIMITATION: One call at a time (global across all accounts)
        # ========================================================================
        # Future expansion (Phase 7+): Remove this check to enable:
        # - Multiple concurrent calls (per-account or globally)
        # - Call routing/bridging (e.g., Account A ↔ You ↔ Account B)
        # - Group calls with multiple participants
        #
        # Architecture already supports this:
        # - Each CallBarrel has session tracking dicts (call_id_by_session, etc.)
        # - Database schema supports call_participant table for multi-party calls
        # - Just remove this auto-reject logic when ready for multi-call support
        # ========================================================================
        # Exclude current session_id to avoid rejecting itself
        if get_account_brewery().has_active_call(exclude_session_id=session_id):
            if self.logger:
                self.logger.warning(f"Auto-rejecting incoming call (busy): {session_id} from {peer_jid}")

            # Send XEP-0353 reject with busy signal
            try:
                if self.client:
                    self.client.send_call_reject(session_id)
                    if self.logger:
                        self.logger.debug(f"Sent XEP-0353 reject (busy) for {session_id}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error sending busy reject: {e}")

            # Don't show dialog, don't start timer, just return
            return

        # Log incoming call to database (Phase 4)
        self._log_call_to_db(session_id, peer_jid, CallDirection.INCOMING.value, CallState.RINGING.value)

        # Start 60-second timeout timer for unanswered calls
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(lambda: asyncio.ensure_future(
            self._on_incoming_call_timeout(session_id, peer_jid)
        ))
        timeout_timer.start(60000)  # 60 seconds - graceful timeout
        self.incoming_call_timers[session_id] = timeout_timer

        if self.logger:
            self.logger.debug(f"Started 60s timeout timer for incoming call: {session_id}")

        # Emit signal to GUI to show incoming call dialog
        # Use call_soon_threadsafe to avoid asyncio task conflicts
        if self.logger:
            self.logger.debug(f"Emitting call_incoming signal for session {session_id}")
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(
            self.signals['call_incoming'].emit, self.account_id, session_id, peer_jid, media
        )
        if self.logger:
            self.logger.debug(f"Signal call_incoming emitted for session {session_id}")

    async def _on_xmpp_call_accepted(self, session_id: str, peer_jid: str):
        """
        Handle call accepted (XEP-0353 <proceed>).

        Callee sent proceed, now we create CallBridge session and send session-initiate with SDP.
        """
        if self.logger:
            self.logger.debug(f"Call accepted by {peer_jid}, creating session and sending session-initiate (session {session_id})")

        # Cancel outgoing call timeout (peer answered)
        self._cancel_outgoing_call_timer(session_id)

        if not self.jingle_adapter or not self.call_bridge:
            if self.logger:
                self.logger.error("JingleAdapter or CallBridge not initialized")
            return

        try:
            # Get media types from pending call (stored in start_call)
            media_types = self.pending_call_media.get(session_id, ['audio'])
            enable_video = 'video' in media_types

            if self.logger:
                self.logger.debug(f"Outgoing call media types: {media_types}, video enabled: {enable_video}")

            # Query XEP-0215 for TURN servers
            turn_server, turn_username, turn_password = '', '', ''
            try:
                if self.logger:
                    self.logger.debug("Querying server for TURN servers (XEP-0215)")
                services = await self.client.get_external_services()
                if services:
                    ice_servers = self.client.format_ice_servers(services)
                    turn_server, turn_username, turn_password = self._extract_turn_server(ice_servers)
                    if turn_server and self.logger:
                        self.logger.debug(f"Using TURN server from XEP-0215: {turn_server}")
                elif self.logger:
                    self.logger.debug("Server does not support XEP-0215, will use Jami TURN")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to query XEP-0215: {e}, will use Jami TURN")

            # Load audio device and processing settings
            mic_device, mic_display, speakers_device, speakers_display, audio_proc = self._load_audio_settings()

            # Create CallBridge session (WebRTC peer connection)
            result = await self.call_bridge.create_session(
                peer_jid, session_id, mic_device, speakers_device,
                microphone_display_name=mic_display,
                speakers_display_name=speakers_display,
                proxy_host=self.proxy_host or "",
                proxy_port=self.proxy_port or 0,
                proxy_username=self.proxy_username or "",
                proxy_password=self.proxy_password or "",
                proxy_type=self.proxy_type or "",
                turn_server=turn_server,
                turn_username=turn_username,
                turn_password=turn_password,
                echo_cancel=audio_proc['echo_cancel'],
                echo_suppression_level=audio_proc['echo_suppression_level'],
                noise_suppression=audio_proc['noise_suppression'],
                noise_suppression_level=audio_proc['noise_suppression_level'],
                gain_control=audio_proc['gain_control'],
                enable_video=enable_video
            )

            # Handle return value - can be bool (legacy) or tuple (success, video_port)
            if isinstance(result, tuple):
                success, video_port = result
            else:
                success = result
                video_port = None

            if not success:
                raise RuntimeError("Failed to create CallBridge session")

            # Generate SDP offer from CallBridge
            sdp_offer = await self.call_bridge.create_offer(session_id)

            # Create outgoing session in JingleAdapter (encapsulated API)
            self.jingle_adapter.create_outgoing_session(session_id, peer_jid, sdp_offer, media_types)

            # Store video port for RTP streaming
            if video_port:
                self.jingle_adapter.sessions[session_id]['video_port'] = video_port
                if self.logger:
                    self.logger.info(f"Video enabled for outgoing call {session_id}, RTP port: {video_port}")

            # Send ONLY session-initiate (skip propose - already sent by mixin)
            await self.jingle_adapter._send_session_initiate(session_id)

            if self.logger:
                self.logger.debug(f"Sent Jingle session-initiate to {peer_jid}")

            # Clean up pending media types
            self.pending_call_media.pop(session_id, None)

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to send session-initiate: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            # Cleanup on failure (best effort)
            try:
                await self.call_bridge.end_session(session_id)
            except Exception as cleanup_error:
                if self.logger:
                    self.logger.debug(f"Cleanup failed for session {session_id}: {cleanup_error}")
                pass  # Ignore cleanup errors when already handling a failure

    async def _on_xmpp_call_terminated(self, session_id: str, reason: str):
        """
        Handle call terminated via XEP-0353 (reject/retract).

        This is for early termination before Jingle session starts.
        Peer already sent reject/retract, so we don't send terminate.
        """
        if self.logger:
            self.logger.debug(f"XEP-0353 early termination: {reason} (session {session_id})")

        # Cancel timers (works for both incoming and outgoing)
        self._cancel_incoming_call_timer(session_id)
        self._cancel_outgoing_call_timer(session_id)

        # Use unified cleanup (send_terminate=False - peer already sent reject/retract)
        # CallBridge/Jingle sessions may or may not exist yet, end_call() handles gracefully
        await self.end_call(session_id, reason=reason, send_terminate=False)

    # ============================================================================
    # Jingle IQ Callbacks (from JingleAdapter - SDP negotiation)
    # ============================================================================

    async def _on_jingle_incoming_call(self, session_id: str, peer_jid: str, sdp_offer: str, media: list):
        """
        Handle incoming Jingle session-initiate (from JingleAdapter).

        This has the actual SDP offer, unlike XEP-0353 propose which just announces the call.
        """
        if self.logger:
            self.logger.debug(f"Jingle session-initiate from {peer_jid}: {media} (sid={session_id})")

        # Store SDP offer
        self.pending_call_offers[session_id] = sdp_offer

        # Check if we need to wait for candidates (trickle-only offer)
        waiting_for_candidates = self.jingle_adapter.sessions.get(session_id, {}).get('waiting_for_candidates', False)

        # If user already accepted (sent proceed), create session and complete acceptance
        if session_id in self.accepted_calls:
                # User already accepted, now we have SDP - create C++ session immediately
                if self.logger:
                    self.logger.info(f"User already accepted, creating C++ session for {session_id}")

                session_created = await self._create_incoming_session(session_id)
                if not session_created:
                    if self.logger:
                        self.logger.error(f"Failed to create incoming session for {session_id}")
                    await self.end_call(session_id, reason='failed-application')
                    return

                if waiting_for_candidates:
                    # TRICKLE-ICE: Defer answer creation until candidates arrive
                    if self.logger:
                        self.logger.info(f"[TRICKLE-ICE] Session created but offer has 0 candidates - deferring answer for {session_id}")
                    # The _on_candidates_ready callback will create the answer when candidates arrive
                    return

                # Normal flow: we have offer + candidates, create answer now
                if self.logger:
                    self.logger.debug(f"User already accepted, creating answer for {session_id}")

                try:
                    # Create SDP answer via CallBridge (also sets remote SDP)
                    sdp_answer = await self.call_bridge.create_answer(session_id, sdp_offer)

                    # State transition: Remote SDP set + Answer created
                    self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.REMOTE_SET)
                    self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.ANSWER_READY)

                    # Send Jingle session-accept via JingleAdapter
                    await self.jingle_adapter.send_answer(session_id, sdp_answer)

                    # Process buffered transport-info candidates (received while session was being created)
                    # IMPORTANT: Retrieve buffer AFTER send_answer() to catch any candidates that arrived during answer creation
                    buffered_candidates = self.jingle_adapter.trickle_ice.get_buffered_candidates(session_id)
                    if buffered_candidates:
                        if self.logger:
                            self.logger.info(f"[BUFFER] Processing {len(buffered_candidates)} buffered candidates for {session_id}")
                        for candidate in buffered_candidates:
                            try:
                                await self.call_bridge.add_ice_candidate(session_id, candidate)
                            except Exception as e:
                                if self.logger:
                                    self.logger.warning(f"Failed to add buffered candidate: {e}")

                    # State transition: Active (normal trickle ICE mode)
                    self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.ACTIVE)

                    if self.logger:
                        self.logger.debug(f"Sent Jingle session-accept for {session_id}")

                    # Remove from accepted_calls set
                    self.accepted_calls.discard(session_id)

                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Failed to complete call acceptance: {e}")
                        import traceback
                        self.logger.error(traceback.format_exc())
                    await self.end_call(session_id, reason='failed-application')
                    self.accepted_calls.discard(session_id)

    async def _on_jingle_call_answered(self, session_id: str, sdp_answer: str):
        """
        Handle incoming Jingle session-accept (peer answered our call).

        Set remote SDP answer in CallBridge.
        """
        if self.logger:
            self.logger.debug(f"Call answered, received SDP answer (sid={session_id})")

        # Set remote description in Go service (CRITICAL for ICE candidates to work)
        try:
            await self.call_bridge.set_remote_description(session_id, sdp_answer, 'answer')
            if self.logger:
                self.logger.debug(f"Remote SDP answer set for {session_id}")

            # Emit signal to transition from outgoing dialog to call window
            # Use call_soon_threadsafe to avoid asyncio task conflicts
            if self.logger:
                self.logger.debug(f"Emitting call_accepted signal for session {session_id}")
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                self.signals['call_accepted'].emit, self.account_id, session_id
            )
            if self.logger:
                self.logger.debug(f"Signal call_accepted emitted for session {session_id}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to set remote description: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    async def _on_jingle_call_terminated(self, session_id: str, reason: str):
        """
        Handle incoming Jingle session-terminate from peer.

        Peer already sent terminate, so we don't send it back.
        """
        if self.logger:
            self.logger.info(f"Peer terminated call via Jingle: {reason} (sid={session_id})")

        # Use unified cleanup (send_terminate=False - peer already sent it)
        await self.end_call(session_id, reason=reason, send_terminate=False)

    async def _create_incoming_session(self, session_id: str):
        """
        Create C++ CallBridge session for incoming call.

        This is called as soon as we have the SDP offer, before creating the answer.
        This ensures the session exists before any ICE candidates arrive.

        Args:
            session_id: Jingle session ID

        Returns:
            bool: True if session created successfully
        """
        if self.logger:
            self.logger.debug(f"Creating C++ session for incoming call {session_id}")

        # Get stored SDP offer
        if session_id not in self.pending_call_offers:
            if self.logger:
                self.logger.error(f"No stored SDP offer for session {session_id}")
            return False

        try:
            # Get media types from session
            media_types = self.jingle_adapter.sessions[session_id].get('media', ['audio'])
            enable_video = 'video' in media_types

            if self.logger:
                self.logger.debug(f"Session {session_id} media types: {media_types}, video enabled: {enable_video}")

            # Query XEP-0215 for TURN servers
            turn_server, turn_username, turn_password = '', '', ''
            try:
                if self.logger:
                    self.logger.debug("Querying server for TURN servers (XEP-0215)")
                services = await self.client.get_external_services()
                if services:
                    ice_servers = self.client.format_ice_servers(services)
                    turn_server, turn_username, turn_password = self._extract_turn_server(ice_servers)
                    if turn_server and self.logger:
                        self.logger.debug(f"Using TURN server from XEP-0215: {turn_server}")
                elif self.logger:
                    self.logger.debug("Server does not support XEP-0215, will use Jami TURN")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to query XEP-0215: {e}, will use Jami TURN")

            # Load audio device and processing settings
            mic_device, mic_display, speakers_device, speakers_display, audio_proc = self._load_audio_settings()

            # State transition: Resources (TURN credentials + devices) ready
            self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.RESOURCES_READY)

            # Create CallBridge session (incoming call)
            # GStreamer webrtcbin will queue any ICE candidates that arrive before set-remote-description
            result = await self.call_bridge.create_session(
                self.jingle_adapter.sessions[session_id]['peer_jid'],
                session_id,
                mic_device,
                speakers_device,
                microphone_display_name=mic_display,
                speakers_display_name=speakers_display,
                proxy_host=self.proxy_host or "",
                proxy_port=self.proxy_port or 0,
                proxy_username=self.proxy_username or "",
                proxy_password=self.proxy_password or "",
                proxy_type=self.proxy_type or "",
                turn_server=turn_server,
                turn_username=turn_username,
                turn_password=turn_password,
                echo_cancel=audio_proc['echo_cancel'],
                echo_suppression_level=audio_proc['echo_suppression_level'],
                noise_suppression=audio_proc['noise_suppression'],
                noise_suppression_level=audio_proc['noise_suppression_level'],
                gain_control=audio_proc['gain_control'],
                enable_video=enable_video
            )

            # Handle return value - can be bool (legacy) or tuple (success, video_port)
            if isinstance(result, tuple):
                success, video_port = result
            else:
                success = result
                video_port = None

            if not success:
                raise RuntimeError("Failed to create CallBridge session")

            # Store video port for RTP streaming
            if video_port:
                self.jingle_adapter.sessions[session_id]['video_port'] = video_port
                if self.logger:
                    self.logger.info(f"Video enabled for session {session_id}, RTP port: {video_port}")

            if self.logger:
                self.logger.info(f"C++ session created for incoming call {session_id}")

            # State transition: C++ session created
            self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.SESSION_CREATED)

            return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to create incoming session: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            return False

    async def _on_candidates_ready(self, session_id: str):
        """
        Handle candidates arriving for trickle-only offers.

        This callback is triggered when we receive the first ICE candidate via transport-info
        for an incoming call that had 0 candidates in the offer SDP (trickle-only mode).

        The answer creation was deferred waiting for candidates to avoid the race condition
        where Pion starts ICE checking with 0 remote candidates.

        NOTE: The C++ session should already exist at this point (created in accept_call()).
        """
        if self.logger:
            self.logger.debug(f"[TRICKLE-ICE] Candidates ready, proceeding with deferred answer creation for {session_id}")

        # Check if user already accepted this call
        if session_id not in self.accepted_calls:
            if self.logger:
                self.logger.warning(f"Candidates ready but call {session_id} not yet accepted by user")
            return

        # Get stored SDP offer
        if session_id not in self.pending_call_offers:
            if self.logger:
                self.logger.error(f"No stored SDP offer for session {session_id}")
            return

        sdp_offer = self.pending_call_offers[session_id]

        try:
            # Session should already exist (created in accept_call), but create it if missing
            # This handles the edge case where candidates arrive before accept_call completes
            # In normal flow, this will be a no-op since session already exists
            if self.logger:
                self.logger.debug(f"Ensuring C++ session exists for {session_id}")
            # Note: create_session is idempotent in CallBridge - safe to call multiple times

            # Create SDP answer via CallBridge (also sets remote SDP)
            # GStreamer has queued any ICE candidates that arrived before set-remote-description
            sdp_answer = await self.call_bridge.create_answer(session_id, sdp_offer)

            # State transition: Remote SDP set + Answer created
            self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.REMOTE_SET)
            self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.ANSWER_READY)

            # Send Jingle session-accept via JingleAdapter
            await self.jingle_adapter.send_answer(session_id, sdp_answer)

            # Process buffered transport-info candidates (received while session was being created)
            # IMPORTANT: Retrieve buffer AFTER send_answer() to catch any candidates that arrived during answer creation
            buffered_candidates = self.jingle_adapter.trickle_ice.get_buffered_candidates(session_id)
            if buffered_candidates:
                if self.logger:
                    self.logger.info(f"[BUFFER] Processing {len(buffered_candidates)} buffered candidates for {session_id}")
                for candidate in buffered_candidates:
                    try:
                        await self.call_bridge.add_ice_candidate(session_id, candidate)
                    except Exception as e:
                        if self.logger:
                            self.logger.warning(f"Failed to add buffered candidate: {e}")

            # State transition: Active (normal trickle ICE mode)
            self.jingle_adapter.trickle_ice.set_incoming_state(session_id, IncomingCallState.ACTIVE)

            if self.logger:
                self.logger.debug(f"Sent Jingle session-accept for {session_id} (deferred)")

            # Remove from accepted_calls set
            self.accepted_calls.discard(session_id)

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to complete deferred call acceptance: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            await self.call_bridge.end_session(session_id)
            self.accepted_calls.discard(session_id)

    # ============================================================================
    # Legacy Jingle Callbacks (kept for reference)
    # ============================================================================

    async def _complete_call_acceptance(self, session_id: str):
        """
        Complete call acceptance by processing SDP and sending session-accept.

        This is called when:
        1. User accepts XEP-0353 call and session-initiate arrives with SDP
        2. User accepts legacy direct Jingle call (future)

        Args:
            session_id: Session ID
        """
        sdp_offer = self.pending_call_offers.get(session_id)
        if not sdp_offer:
            if self.logger:
                self.logger.error(f"Cannot complete call acceptance: No SDP offer for {session_id}")
            return

        if self.logger:
            self.logger.debug(f"Completing call acceptance with SDP negotiation (session {session_id})")

        # Get session info from Jingle adapter (use public API)
        session_info = self.jingle_adapter.get_session_info(session_id)
        if not session_info:
            if self.logger:
                self.logger.error(f"Cannot complete call acceptance: Unknown session {session_id}")
            return

        peer_jid = session_info['peer_jid']

        # Query XEP-0215 for TURN servers
        turn_server, turn_username, turn_password = '', '', ''
        try:
            if self.logger:
                self.logger.debug("Querying server for TURN servers (XEP-0215)")
            services = await self.client.get_external_services()
            if services:
                ice_servers = self.client.format_ice_servers(services)
                turn_server, turn_username, turn_password = self._extract_turn_server(ice_servers)
                if turn_server and self.logger:
                    self.logger.info(f"Using TURN server from XEP-0215: {turn_server}")
            elif self.logger:
                self.logger.debug("Server does not support XEP-0215, will use Jami TURN")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to query XEP-0215: {e}, will use Jami TURN")

        # Load audio device and processing settings
        mic_device, mic_display, speakers_device, speakers_display, audio_proc = self._load_audio_settings()

        # Create WebRTC session
        await self.call_bridge.create_session(
            peer_jid, session_id, mic_device, speakers_device,
            microphone_display_name=mic_display,
            speakers_display_name=speakers_display,
            proxy_host=self.proxy_host or "",
            proxy_port=self.proxy_port or 0,
            proxy_username=self.proxy_username or "",
            proxy_password=self.proxy_password or "",
            proxy_type=self.proxy_type or "",
            turn_server=turn_server,
            turn_username=turn_username,
            turn_password=turn_password,
            echo_cancel=audio_proc['echo_cancel'],
            echo_suppression_level=audio_proc['echo_suppression_level'],
            noise_suppression=audio_proc['noise_suppression'],
            noise_suppression_level=audio_proc['noise_suppression_level'],
            gain_control=audio_proc['gain_control']
        )

        # Set remote description (caller's offer)
        await self.call_bridge.set_remote_description(
            session_id, sdp_offer, 'offer'
        )

        # Generate SDP answer
        sdp_answer = await self.call_bridge.create_answer(session_id)

        # Send answer via Jingle (XMPP signaling)
        await self.jingle_adapter.send_answer(session_id, sdp_answer)

        # Clean up stored offer
        del self.pending_call_offers[session_id]

        if self.logger:
            self.logger.debug(f"Call session-accept sent (session {session_id})")

    async def _on_call_offer_received(self, session_id: str, peer_jid: str,
                                       sdp_offer: str, media: list):
        """
        Handle incoming call offer (Jingle session-initiate).

        This is called AFTER XEP-0353 propose/proceed exchange.
        If we already have this session from XEP-0353, DON'T emit duplicate incoming call signal.
        """
        # Store SDP offer for when user accepts
        self.pending_call_offers[session_id] = sdp_offer

        # Check if this session already exists from XEP-0353 propose
        if session_id in self.client.call_sessions:
            # This is the expected session-initiate after we sent proceed
            # User already accepted via the incoming call dialog, so automatically process SDP
            if self.logger:
                self.logger.debug(f"Received session-initiate for known session {session_id} (from XEP-0353 flow)")

            # Automatically complete the call acceptance with SDP negotiation
            await self._complete_call_acceptance(session_id)
            return

        # This is a direct Jingle call (no XEP-0353 propose) - legacy mode
        if self.logger:
            self.logger.debug(f"Incoming call from {peer_jid}: {media} (direct Jingle, no XEP-0353)")

        # Emit signal to GUI for legacy direct Jingle calls
        self.signals['call_incoming'].emit(self.account_id, session_id, peer_jid, media)

    async def _on_call_answer_received(self, session_id: str, sdp_answer: str):
        """Handle call answer (outgoing call accepted)."""
        if self.logger:
            self.logger.debug(f"Call accepted (session {session_id})")

        # Set remote description (peer's answer)
        if self.call_bridge:
            await self.call_bridge.set_remote_description(
                session_id, sdp_answer, 'answer'
            )

        # Emit signal to GUI
        self.signals['call_accepted'].emit(self.account_id, session_id)

    async def _on_ice_candidate_received(self, session_id: str, candidate: dict):
        """Handle ICE candidate received from peer."""
        if self.logger:
            self.logger.debug(f"Adding remote ICE candidate to session {session_id}: {candidate.get('candidate', '')[:60]}...")

        # Add candidate to WebRTC peer connection
        if self.call_bridge:
            await self.call_bridge.add_ice_candidate(session_id, candidate)
            if self.logger:
                self.logger.debug(f"Successfully added remote ICE candidate to CallBridge for {session_id}")

    async def _on_call_terminated(self, session_id: str, reason: str):
        """Handle call termination."""
        if self.logger:
            self.logger.info(f"Call terminated: {reason} (session {session_id})")

        # Emit signal to GUI
        self.signals['call_terminated'].emit(self.account_id, session_id, reason)

    def _on_call_state_changed_sync(self, session_id: str, state: str):
        """
        Sync wrapper for call state change callback (called from GStreamer thread).
        Schedules the async work in the client's event loop.
        """
        try:
            # Get the XMPP client's event loop
            if self.client and hasattr(self.client, 'loop') and self.client.loop:
                loop = self.client.loop
            else:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.get_event_loop()

            # Schedule the coroutine
            asyncio.run_coroutine_threadsafe(
                self._on_call_state_changed(session_id, state),
                loop
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error scheduling state change: {e}")

    async def _on_call_state_changed(self, session_id: str, state: str):
        """Handle WebRTC connection state changes."""
        if self.logger:
            self.logger.debug(f"Call state: {state} (session {session_id})")

        # Update database based on connection state (Phase 4)
        if state == 'connected':
            # Call successfully connected
            self._update_call_state_in_db(session_id, CallState.IN_PROGRESS.value)
        elif state == 'failed':
            # Connection failed - update to FAILED before terminating
            self._update_call_state_in_db(session_id, CallState.FAILED.value, end_time=int(time.time()))

        # Handle connection failure - terminate immediately (no waiting for resurrection)
        if state == 'failed':
            if self.logger:
                self.logger.warning(f"Call connection FAILED, terminating: {session_id}")
            await self.end_call(session_id, reason='connectivity-error', send_terminate=True)
            return  # Don't emit signal, end_call already handles it

        # We DON'T send XEP-0353 <accept/> message here
        # The <accept/> message doesn't exist in XEP-0353 spec - it's a legacy artifact
        # in slixmpp's plugin that has no actual use. The spec only defines:
        # - <propose/> (initiator starts call)
        # - <proceed/> (responder accepts)
        # - <reject/> (responder rejects)
        # - <retract/> (initiator cancels)
        # - <finish/> (call ends)
        # Connection state is communicated via Jingle session-accept and ICE/DTLS negotiation.

        # Emit signal to GUI
        self.signals['call_state_changed'].emit(self.account_id, session_id, state)

    async def start_call(self, peer_jid: str, media: list = None) -> str:
        """
        Initiate outgoing call.

        Args:
            peer_jid: Peer JID to call
            media: List of media types ['audio'] or ['audio', 'video']

        Returns:
            Session ID

        Raises:
            ValueError: If call functionality not initialized
        """
        if not self.call_bridge:
            raise ValueError("CallBridge not initialized - call functionality unavailable")

        # Import here to avoid circular dependency
        from ..brewery import get_account_brewery

        # ========================================================================
        # ALPHA LIMITATION: One call at a time (global across all accounts)
        # See incoming call handler for full expansion roadmap (Phase 7+)
        # ========================================================================
        if get_account_brewery().has_active_call():
            error_msg = "Cannot start call: Another call is already in progress (alpha version: one call at a time)"
            if self.logger:
                self.logger.warning(error_msg)
            raise ValueError(error_msg)

        if media is None:
            media = ['audio']

        if self.logger:
            self.logger.info(f"Starting call to {peer_jid}: {media}")

        # Generate unique session ID
        import uuid
        session_id = str(uuid.uuid4())

        # Send XEP-0353 <propose> via DrunkXMPP mixin
        # This triggers: propose → peer sends proceed → _on_xmpp_call_accepted() → create session + send session-initiate
        # The mixin generates its own session_id for the XEP-0353 flow
        try:
            actual_sid = self.client.send_call_propose(peer_jid, media)

            # Store media types for later use in _on_xmpp_call_accepted
            self.pending_call_media[actual_sid] = media

            if self.logger:
                self.logger.debug(f"Call propose sent (session {actual_sid})")

            # Log outgoing call to database (Phase 4)
            self._log_call_to_db(actual_sid, peer_jid, CallDirection.OUTGOING.value, CallState.RINGING.value)

            # Start 60-second timeout timer for unanswered outgoing calls
            timeout_timer = QTimer()
            timeout_timer.setSingleShot(True)
            timeout_timer.timeout.connect(lambda: asyncio.ensure_future(
                self._on_outgoing_call_timeout(actual_sid, peer_jid)
            ))
            timeout_timer.start(60000)  # 60 seconds - graceful timeout
            self.outgoing_call_timers[actual_sid] = timeout_timer

            if self.logger:
                self.logger.debug(f"Started 60s timeout timer for outgoing call: {actual_sid}")

            # Emit signal to show outgoing call dialog
            # Use call_soon_threadsafe to avoid asyncio task conflicts
            if self.logger:
                self.logger.debug(f"Emitting call_initiated signal for session {actual_sid}")
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                self.signals['call_initiated'].emit, self.account_id, actual_sid, peer_jid, media
            )
            if self.logger:
                self.logger.debug(f"Signal call_initiated emitted for session {actual_sid}")

            # CallBridge session and SDP offer will be created in _on_xmpp_call_accepted()
            # when we receive the <proceed> response from the peer
            return actual_sid

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to send call propose: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            raise  # Re-raise so GUI can handle error

    async def _on_incoming_call_timeout(self, session_id: str, peer_jid: str):
        """
        Handle incoming call timeout (60 seconds elapsed without answer).

        Send reject to stop caller (otherwise they keep ringing forever and block our line).
        This is a graceful timeout - user had 60 seconds to respond.
        """
        if self.logger:
            self.logger.warning(f"Incoming call timeout (60s): {session_id} from {peer_jid}")

        # Send XEP-0353 reject to stop caller (free our line for other calls)
        try:
            if self.client:
                self.client.send_call_reject(session_id)
                if self.logger:
                    self.logger.debug(f"Sent XEP-0353 reject for timed-out call (60s): {session_id}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error sending reject for timeout: {e}")

        # Clean up via unified end_call (don't send terminate - we sent reject above)
        await self.end_call(session_id, reason='timeout', send_terminate=False)

    async def _on_outgoing_call_timeout(self, session_id: str, peer_jid: str):
        """
        Handle outgoing call timeout (60 seconds elapsed without answer).

        Send retract to cancel our call proposal (stop ringing on peer's side).
        This is a graceful timeout - peer had 60 seconds to respond.
        """
        if self.logger:
            self.logger.warning(f"Outgoing call timeout (60s): {session_id} to {peer_jid}")

        # Send XEP-0353 retract to cancel our proposal (stop ringing on peer's side)
        try:
            if self.client:
                self.client.send_call_retract(session_id)
                if self.logger:
                    self.logger.debug(f"Sent XEP-0353 retract for timed-out call (60s): {session_id}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error sending retract for timeout: {e}")

        # Clean up via unified end_call (don't send terminate - we sent retract above)
        await self.end_call(session_id, reason='timeout', send_terminate=False)

    def _cancel_incoming_call_timer(self, session_id: str):
        """Cancel incoming call timeout timer if it exists."""
        if session_id in self.incoming_call_timers:
            timer = self.incoming_call_timers.pop(session_id)
            if timer.isActive():
                timer.stop()
            if self.logger:
                self.logger.debug(f"Canceled incoming call timer: {session_id}")

    def _cancel_outgoing_call_timer(self, session_id: str):
        """Cancel outgoing call timeout timer if it exists."""
        if session_id in self.outgoing_call_timers:
            timer = self.outgoing_call_timers.pop(session_id)
            if timer.isActive():
                timer.stop()
            if self.logger:
                self.logger.debug(f"Canceled outgoing call timer: {session_id}")


    async def accept_call(self, session_id: str):
        """
        Accept incoming call.

        Args:
            session_id: Session ID of incoming call

        Raises:
            ValueError: If call functionality not initialized or session unknown
        """
        if not self.call_bridge:
            raise ValueError("CallBridge not initialized - call functionality unavailable")

        # Cancel timeout timer (user answered)
        self._cancel_incoming_call_timer(session_id)

        if self.logger:
            self.logger.info(f"Accepting call (session {session_id})")

        # FIRST: Send XEP-0353 <proceed> message to notify caller we're ready
        # This tells the peer to send session-initiate with SDP offer
        # Uses DrunkXMPP.CallsMixin to avoid asyncio reentrancy issues
        try:
            self.client.send_call_proceed(session_id)
            # Track that we accepted this call
            self.accepted_calls.add(session_id)
            if self.logger:
                self.logger.debug(f"Sent XEP-0353 proceed for {session_id}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to send proceed: {e}")

        # Check if we already have the SDP offer (session-initiate arrived before user clicked accept)
        sdp_offer = self.pending_call_offers.get(session_id)
        if not sdp_offer:
            # For XEP-0353, SDP comes in session-initiate AFTER proceed
            # Will be completed when session-initiate arrives in _on_jingle_incoming_call
            if self.logger:
                self.logger.debug(f"Waiting for session-initiate for {session_id}")
            return

        # We have the SDP offer - create C++ session immediately
        # This ensures the session exists BEFORE any ICE candidates arrive
        if self.logger:
            self.logger.info(f"SDP offer already available, creating C++ session for {session_id}")

        session_created = await self._create_incoming_session(session_id)
        if not session_created:
            if self.logger:
                self.logger.error(f"Failed to create incoming session for {session_id}")
            await self.end_call(session_id, reason='failed-application')
            return

        # Check if we need to wait for candidates (trickle-only offer)
        waiting_for_candidates = self.jingle_adapter.sessions.get(session_id, {}).get('waiting_for_candidates', False)
        if waiting_for_candidates:
            if self.logger:
                self.logger.info(f"[TRICKLE-ICE] Waiting for first candidates before creating answer for {session_id}")
            # _on_candidates_ready will create the answer when candidates arrive
            return

        # Normal flow: we have offer + candidates, create answer now
        try:
            # Create SDP answer via CallBridge (also sets remote SDP)
            sdp_answer = await self.call_bridge.create_answer(session_id, sdp_offer)

            # Send Jingle session-accept via JingleAdapter
            await self.jingle_adapter.send_answer(session_id, sdp_answer)

            if self.logger:
                self.logger.info(f"Sent Jingle session-accept for {session_id}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to accept call: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            await self.call_bridge.end_session(session_id)

    async def hangup_call(self, session_id: str):
        """
        Hang up active call (user-initiated).

        This is a convenience wrapper around end_call() for user hangup action.
        Also used when user clicks "Reject" on incoming call dialog.

        Args:
            session_id: Session ID of call to hang up

        Raises:
            ValueError: If call functionality not initialized
        """
        if not self.call_bridge:
            raise ValueError("CallBridge not initialized - call functionality unavailable")

        # Cancel timeout timers (works for both incoming and outgoing)
        self._cancel_incoming_call_timer(session_id)
        self._cancel_outgoing_call_timer(session_id)

        # Check if this is an early rejection (XEP-0353 propose, no Jingle session yet)
        is_early_rejection = (
            self.jingle_adapter and
            session_id not in self.jingle_adapter.sessions and
            hasattr(self.client, 'call_sessions') and
            session_id in self.client.call_sessions
        )

        if is_early_rejection:
            # Check call direction to send correct XEP-0353 message
            call_direction = self.client.call_sessions[session_id].get('direction', 'incoming')

            if call_direction == 'outgoing':
                # Send XEP-0353 retract (cancel our own proposal)
                if self.logger:
                    self.logger.debug(f"Retracting outgoing call before session-initiate (session {session_id})")

                try:
                    self.client.send_call_retract(session_id)
                    if self.logger:
                        self.logger.debug(f"Sent XEP-0353 retract for {session_id}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error sending XEP-0353 retract: {e}")
            else:
                # Send XEP-0353 reject (reject incoming proposal)
                if self.logger:
                    self.logger.debug(f"Rejecting incoming call before session-initiate (session {session_id})")

                try:
                    self.client.send_call_reject(session_id)
                    if self.logger:
                        self.logger.debug(f"Sent XEP-0353 reject for {session_id}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error sending XEP-0353 reject: {e}")

            # Clean up without sending Jingle terminate (session doesn't exist)
            await self.end_call(session_id, reason='decline', send_terminate=False)
        else:
            # Normal hangup (Jingle session exists)
            if self.logger:
                self.logger.debug(f"User hanging up call (session {session_id})")

            # Use unified end_call with send_terminate=True (we're initiating termination)
            await self.end_call(session_id, reason='success', send_terminate=True)

    async def set_mute(self, session_id: str, muted: bool):
        """
        Set microphone mute state for an active call.

        Args:
            session_id: Session ID of active call
            muted: True to mute microphone, False to unmute

        Raises:
            ValueError: If call functionality not initialized
        """
        if not self.call_bridge:
            raise ValueError("CallBridge not initialized - call functionality unavailable")

        if self.logger:
            self.logger.info(f"Setting mute state for call {session_id}: muted={muted}")

        try:
            await self.call_bridge.set_mute(session_id, muted)
            if self.logger:
                self.logger.debug(f"Mute state set successfully for {session_id}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to set mute state for {session_id}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            raise

    async def end_call(self, session_id: str, reason: str = 'success', send_terminate: bool = True):
        """
        Unified call termination and cleanup.

        This is called by ALL termination paths:
        - User hangup (via hangup_call)
        - Peer terminate (Jingle session-terminate received)
        - XEP-0353 reject/retract
        - Timeout handlers
        - Error handlers

        Args:
            session_id: Session ID to terminate
            reason: Termination reason ('success', 'decline', 'busy', 'timeout', 'connectivity-error', etc.)
            send_terminate: Whether to send Jingle session-terminate (False if peer already sent it)
        """
        # Idempotency check - prevent double cleanup
        if not hasattr(self, '_ended_calls'):
            self._ended_calls = set()

        if session_id in self._ended_calls:
            if self.logger:
                self.logger.debug(f"Call {session_id} already ended, skipping cleanup")
            return

        self._ended_calls.add(session_id)

        if self.logger:
            self.logger.debug(f"Ending call: session={session_id}, reason={reason}, send_terminate={send_terminate}")

        # Update call state in database before cleanup (Phase 4)
        # Map termination reason to CallState
        end_time = int(time.time())
        if reason in ('decline', 'busy'):
            final_state = CallState.DECLINED.value
        elif reason == 'timeout':
            final_state = CallState.MISSED.value
        elif reason == 'connectivity-error':
            final_state = CallState.FAILED.value
        elif reason == 'answered_elsewhere':
            # Multi-device: call answered on another device (phone/tablet)
            final_state = CallState.ANSWERED_ELSEWHERE.value
        elif reason == 'rejected_elsewhere':
            # Multi-device: call rejected on another device (phone/tablet)
            final_state = CallState.REJECTED_ELSEWHERE.value
        elif reason == 'finished':
            # Multi-device: call ended on another device (generic)
            final_state = CallState.OTHER_DEVICE.value
        else:  # 'success' or other normal terminations
            final_state = CallState.ENDED.value

        self._update_call_state_in_db(session_id, final_state, end_time)

        # Get peer_jid BEFORE cleanup (needed for GUI signal)
        # Try multiple sources: call logging tracker, Jingle session, XEP-0353 call_sessions
        peer_jid = 'unknown'
        try:
            # First: Check call logging tracker (always set for incoming/outgoing calls)
            if session_id in self.call_peer_jids:
                peer_jid = self.call_peer_jids[session_id]
            # Second: Try Jingle session (for active calls)
            elif self.jingle_adapter and session_id in self.jingle_adapter.sessions:
                peer_jid = self.jingle_adapter.sessions[session_id]['peer_jid']
            # Third: Fall back to XEP-0353 call_sessions (for early-stage calls)
            elif hasattr(self.client, 'call_sessions') and session_id in self.client.call_sessions:
                peer_jid = self.client.call_sessions[session_id].get('peer_jid', 'unknown')
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Could not get peer_jid for {session_id}: {e}")

        # Layer 1: CallBridge cleanup (Go service)
        if self.call_bridge:
            try:
                await self.call_bridge.end_session(session_id)
                if self.logger:
                    self.logger.debug(f"CallBridge session ended: {session_id}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error ending CallBridge session: {e}")

        # Layer 2: JingleAdapter cleanup + send terminate if needed
        if self.jingle_adapter:
            try:
                if send_terminate:
                    await self.jingle_adapter.terminate(session_id, reason=reason)
                    if self.logger:
                        self.logger.debug(f"Sent Jingle session-terminate: {session_id}")

                # Clean up Jingle session state (even if terminate already sent by peer)
                await self.jingle_adapter.cleanup_session(session_id, send_terminate=False)
                if self.logger:
                    self.logger.debug(f"JingleAdapter session cleaned up: {session_id}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error in JingleAdapter cleanup: {e}")

        # Layer 3: DrunkXMPP cleanup (XEP-0353 call_sessions)
        if hasattr(self.client, 'call_sessions') and session_id in self.client.call_sessions:
            try:
                del self.client.call_sessions[session_id]
                if self.logger:
                    self.logger.debug(f"Removed from DrunkXMPP call_sessions: {session_id}")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error cleaning DrunkXMPP call_sessions: {e}")

        # Layer 4: AccountManager state cleanup
        self.pending_call_offers.pop(session_id, None)
        self.pending_call_media.pop(session_id, None)
        self.accepted_calls.discard(session_id)

        # Phase 4: Call logging state cleanup
        self.call_db_ids.pop(session_id, None)
        self.call_start_times.pop(session_id, None)
        self.call_peer_jids.pop(session_id, None)

        if self.logger:
            self.logger.debug(f"AccountManager state cleaned up: {session_id}")

        # Layer 5: Emit Qt signal to GUI (call window will close)
        # Use call_soon_threadsafe to avoid asyncio task conflicts
        if self.logger:
            self.logger.debug(f"Emitting call_terminated signal for session {session_id}")
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(
            self.signals['call_terminated'].emit, self.account_id, session_id, reason, peer_jid
        )
        if self.logger:
            self.logger.debug(f"Signal call_terminated emitted for session {session_id}")

        if self.logger:
            self.logger.debug(f"Call ended successfully: {session_id} (reason: {reason})")

    async def get_call_stats(self, session_id: str) -> dict:
        """
        Get call statistics.

        Args:
            session_id: Session ID

        Returns:
            Statistics dictionary

        Raises:
            ValueError: If call functionality not initialized
        """
        if not self.call_bridge:
            raise ValueError("CallBridge not initialized")

        return await self.call_bridge.get_stats(session_id)

    # =========================================================================
    # Audio Device Management (GUI Hooks for Audio Settings Dialog)
    # =========================================================================

    @staticmethod
    def get_available_input_devices() -> list:
        """
        Get list of available audio input devices (microphones).

        Returns:
            List of AudioDevice objects

        GUI Hook: Call this from audio settings dialog to populate microphone dropdown.
        """
        from ...utils.audio_devices import get_audio_device_manager
        device_manager = get_audio_device_manager()
        return device_manager.get_input_devices()

    @staticmethod
    def get_available_output_devices() -> list:
        """
        Get list of available audio output devices (speakers).

        Returns:
            List of AudioDevice objects

        GUI Hook: Call this from audio settings dialog to populate speaker dropdown.
        """
        from ...utils.audio_devices import get_audio_device_manager
        device_manager = get_audio_device_manager()
        return device_manager.get_output_devices()

    def set_audio_devices(self, input_device_id: str = None, output_device_id: str = None):
        """
        Set preferred audio devices for this account.

        Args:
            input_device_id: Microphone device ID (from AudioDevice.id)
            output_device_id: Speaker device ID (from AudioDevice.id)

        GUI Hook: Call this when user selects devices in audio settings dialog.

        Note: This only affects NEW calls. Active calls will continue using
              their current devices.
        """
        if self.call_bridge:
            if input_device_id is not None:
                self.call_bridge.input_device_id = input_device_id
                if self.logger:
                    self.logger.debug(f"Set input device: {input_device_id}")

            if output_device_id is not None:
                self.call_bridge.output_device_id = output_device_id
                if self.logger:
                    self.logger.debug(f"Set output device: {output_device_id}")

            # TODO: Store preferences in database for persistence across sessions

    def get_current_audio_devices(self) -> dict:
        """
        Get currently configured audio devices.

        Returns:
            Dict with 'input' and 'output' device IDs (or None if auto-detect)

        GUI Hook: Call this to show current selection in audio settings dialog.
        """
        if self.call_bridge:
            return {
                'input': self.call_bridge.input_device_id,
                'output': self.call_bridge.output_device_id
            }
        return {'input': None, 'output': None}
