"""
JingleAdapter - XMPP/Jingle Signaling Implementation

Implements SignalingAdapter for XMPP using Jingle protocol (XEP-0166, XEP-0167).
Bridges DrunkXMPP ↔ DrunkCALL (GStreamer-based).

XEPs implemented:
- XEP-0166: Jingle (base signaling framework)
- XEP-0167: Jingle RTP Sessions (audio/video media)
- XEP-0353: Jingle Message Initiation (call notifications)
"""

import logging
import uuid
import asyncio
from typing import Optional, Dict, Any, List, Callable
from slixmpp.stanza import Iq
from slixmpp.xmlstream import ET

from drunk_call_hook.protocol.jingle_sdp_converter import JingleSDPConverter
from drunk_call_hook.protocol.features.trickle_ice import TrickleICEHandler, IncomingCallState


class JingleAdapter:
    """
    Jingle signaling adapter for XMPP.

    Connects DrunkXMPP (XMPP messaging) with CallBridge (Go/Pion WebRTC media).

    Responsibilities:
    - Build/parse Jingle IQ stanzas (XEP-0166)
    - Convert SDP ↔ Jingle XML
    - Send/receive call signaling via XMPP
    - Bridge CallBridge events to XMPP
    - Handle Jingle IQ actions (session-initiate/accept/terminate/transport-info)
    """

    def __init__(self, xmpp_client, call_bridge,
                 on_incoming_call: Optional[Callable] = None,
                 on_call_answered: Optional[Callable] = None,
                 on_call_terminated: Optional[Callable] = None,
                 on_ice_candidate_received: Optional[Callable] = None,
                 on_call_state_changed: Optional[Callable] = None,
                 on_candidates_ready: Optional[Callable] = None,
                 logger: Optional[logging.Logger] = None):
        """
        Initialize Jingle adapter.

        Args:
            xmpp_client: DrunkXMPP instance (for XMPP connection)
            call_bridge: CallBridge instance (Go service bridge)
            on_incoming_call: Callback for incoming calls (session_id, peer_jid, sdp_offer, media)
            on_call_answered: Callback when call is answered (session_id, sdp_answer)
            on_call_terminated: Callback when call ends (session_id, reason)
            on_ice_candidate_received: Callback for ICE candidates (session_id, candidate) - optional
            on_call_state_changed: Callback for connection state changes (session_id, state) - optional
            on_candidates_ready: Callback when candidates arrive for trickle-only offers (session_id) - optional
            logger: Logger instance (optional)
        """
        self.xmpp = xmpp_client
        self.bridge = call_bridge
        self.on_incoming_call = on_incoming_call
        self.on_call_answered = on_call_answered
        self.on_call_terminated = on_call_terminated
        self.on_ice_candidate_received = on_ice_candidate_received  # Optional - for trickle ICE
        self.on_call_state_changed = on_call_state_changed  # Optional - for connection state updates
        self.on_candidates_ready = on_candidates_ready  # Optional - for deferred answer creation
        self.logger = logger or logging.getLogger(__name__)

        # Initialize SDP ↔ Jingle converter (pure conversion, no business logic)
        self.converter = JingleSDPConverter(logger=self.logger)

        # Initialize Trickle ICE handler (manages deferred answer creation)
        self.trickle_ice = TrickleICEHandler(timeout_seconds=5.0, logger=self.logger)

        # Track session_id → peer_jid mapping
        self.sessions: Dict[str, Dict[str, Any]] = {}

        # Queue ICE candidates until session-initiate is sent
        self.pending_ice_candidates: Dict[str, List[Dict[str, Any]]] = {}

        # ICE statistics tracking (for debugging)
        self._ice_stats: Dict[str, Dict[str, Any]] = {}

        # Register Jingle IQ handlers with XMPP
        self._register_handlers()

        # Wire CallBridge callbacks to receive events from Go service
        # Each CallBridge instance is per-account, so callbacks naturally isolated
        self._wire_bridge_callbacks()

        # XEP-0353 (Jingle Message Initiation) handled by DrunkXMPP.CallsMixin
        # This avoids duplicate handler registration

        self.logger.info("JingleAdapter initialized (CallBridge mode)")

    # ============================================================================
    # Public API - Used by AccountManager
    # ============================================================================

    def create_outgoing_session(self, session_id: str, peer_jid: str, sdp_offer: str, media: List[str]):
        """
        Create outgoing session metadata.

        Called by AccountManager after creating CallBridge session and generating SDP offer.

        Args:
            session_id: Unique session identifier
            peer_jid: Full JID of peer (with resource)
            sdp_offer: SDP offer from CallBridge
            media: List of media types (e.g., ['audio'])
        """
        self.sessions[session_id] = {
            'peer_jid': peer_jid,
            'media': media,
            'sdp_offer': sdp_offer,
            'state': 'proposing'  # Will be updated to 'pending' after session-initiate sent
        }
        self.logger.debug(f"Created outgoing session {session_id} for {peer_jid}")

    def get_session_info(self, session_id: str) -> Optional[dict]:
        """
        Get session information.

        Returns session metadata including peer_jid, media, and state.
        Used by AccountManager and GUI to query session details.

        Args:
            session_id: Session ID to query

        Returns:
            dict with keys: peer_jid, media, state, sdp_offer (if available)
            None if session not found
        """
        return self.sessions.get(session_id)

    # ============================================================================
    # Internal Setup Methods
    # ============================================================================

    def _register_handlers(self):
        """Register XMPP handlers for Jingle stanzas."""
        from slixmpp.xmlstream.handler import CoroutineCallback
        from slixmpp.xmlstream.matcher import MatchXPath

        # Register handler for Jingle IQ stanzas (with proper namespace)
        # Use CoroutineCallback for async handler support
        self.xmpp.register_handler(
            CoroutineCallback(
                'Jingle IQ',
                MatchXPath("{jabber:client}iq[@type='set']/{urn:xmpp:jingle:1}jingle"),
                self._handle_jingle_iq
            )
        )
        self.logger.debug("Registered Jingle IQ handlers")

    def _wire_bridge_callbacks(self):
        """Wire CallBridge callbacks to receive events from Go service."""
        # Set callbacks on bridge to forward events to Jingle handlers
        self.bridge.on_ice_candidate = self._on_bridge_ice_candidate
        self.bridge.on_connection_state = self._on_bridge_connection_state
        self.logger.debug("Wired CallBridge callbacks")

    def _register_jingle_message_handlers(self):
        """Register XEP-0353 (Jingle Message Initiation) event handlers."""
        self.xmpp.add_event_handler('jingle_message_propose', self._on_jingle_message_propose)
        self.xmpp.add_event_handler('jingle_message_proceed', self._on_jingle_message_proceed)
        self.xmpp.add_event_handler('jingle_message_accept', self._on_jingle_message_accept)
        self.xmpp.add_event_handler('jingle_message_reject', self._on_jingle_message_reject)
        self.xmpp.add_event_handler('jingle_message_retract', self._on_jingle_message_retract)
        self.logger.debug("Registered XEP-0353 Jingle Message handlers")

    async def _handle_jingle_iq(self, iq: Iq):
        """Handle incoming Jingle IQ stanza."""
        # Access underlying XML element via .xml property
        jingle = iq.xml.find('{urn:xmpp:jingle:1}jingle')
        if jingle is None:
            self.logger.warning("Received IQ with no jingle element")
            return

        action = jingle.get('action')
        sid = jingle.get('sid')

        self.logger.info(f"Jingle IQ: action={action}, sid={sid}, from={iq['from']}")

        # Send IQ result (ACK) IMMEDIATELY before processing (XEP-0166 compliance)
        # The spec requires: "The responder acknowledges receipt" before processing.
        # Previously, we sent ACK after handler completion, which violated the spec
        # and caused session-accept to be sent before session-initiate was ACKed.
        # This caused Dino to stay in "Calling" state instead of "Connected".
        try:
            iq.reply().send()
        except Exception as e:
            self.logger.error(f"Failed to send IQ ACK for {action}: {e}")
            return

        # Now process the action (can take time for session-initiate/accept)
        try:
            if action == 'session-initiate':
                await self._handle_session_initiate(iq, jingle, sid)
            elif action == 'session-accept':
                await self._handle_session_accept(iq, jingle, sid)
            elif action == 'session-terminate':
                await self._handle_session_terminate(iq, jingle, sid)
            elif action == 'transport-info':
                await self._handle_transport_info(iq, jingle, sid)
            else:
                self.logger.warning(f"Unknown Jingle action: {action}")
                # Note: We already sent result ACK above, so unknown actions are just logged
                return

        except Exception as e:
            self.logger.error(f"Error handling Jingle IQ: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # Note: We already sent IQ result, can't send error now
            # The peer will see our ACK but we failed to process internally

    async def _handle_session_initiate(self, iq: Iq, jingle, sid: str):
        """Handle incoming call (session-initiate)."""
        peer_jid = str(iq['from'])

        # Parse content elements to determine media types
        contents = jingle.findall('{urn:xmpp:jingle:1}content')
        media_types = []

        for content in contents:
            description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
            if description is not None:
                media = description.get('media')  # 'audio' or 'video'
                media_types.append(media)

        # Convert Jingle XML to SDP offer
        try:
            sdp_offer = self.converter.jingle_to_sdp(jingle, role='offer')
            self.logger.info(f"[SDP-OFFER] {sid}:\n{sdp_offer}")
        except Exception as e:
            self.logger.error(f"Failed to convert Jingle to SDP: {e}")
            return

        # Extract remote ICE credentials from offer
        remote_ufrag = None
        remote_pwd = None
        for content in contents:
            transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')
            if transport is not None:
                remote_ufrag = transport.get('ufrag')
                remote_pwd = transport.get('pwd')
                if remote_ufrag and remote_pwd:
                    self.logger.debug(f"Extracted remote ICE credentials: ufrag={remote_ufrag}")
                break

        # Store session info
        self.sessions[sid] = {
            'peer_jid': peer_jid,
            'media': media_types,
            'state': 'incoming',
            'remote_ice_ufrag': remote_ufrag,
            'remote_ice_pwd': remote_pwd
        }

        # Extract and store offer context for echoing in answer (WebRTC standard behavior)
        offer_context = self.converter.extract_offer_context(jingle)
        self.sessions[sid]['offer_context'] = offer_context

        self.logger.info(f"Incoming call from {peer_jid}: {media_types}")

        # Set initial state for incoming call (enables buffering of transport-info)
        self.trickle_ice.set_incoming_state(sid, IncomingCallState.HAVE_OFFER)

        # Always call on_incoming_call to store SDP (required for accept_call to work)
        # This matches the working Conversations.im flow where SDP is stored immediately
        if self.on_incoming_call:
            await self.on_incoming_call(sid, peer_jid, sdp_offer, media_types)

        # Check if this is a trickle-only offer (0 candidates)
        if self.trickle_ice.should_defer_answer(sdp_offer):
            # Defer answer creation until candidates arrive via transport-info
            async def on_timeout(session_id: str):
                # Timeout expired - proceed with answer creation anyway
                if self.on_candidates_ready:
                    await self.on_candidates_ready(session_id)

            self.trickle_ice.defer_answer(sid, sdp_offer, peer_jid, media_types, on_timeout)

    async def _handle_session_accept(self, iq: Iq, jingle, sid: str):
        """Handle call acceptance (session-accept)."""
        if sid not in self.sessions:
            self.logger.warning(f"Received session-accept for unknown session: {sid}")
            return

        # Log the raw Jingle XML for debugging
        from xml.etree import ElementTree as ET_format
        jingle_xml = ET_format.tostring(jingle, encoding='unicode')
        self.logger.debug(f"Received session-accept Jingle XML:\n{jingle_xml}")

        # Convert Jingle XML to SDP answer
        try:
            sdp_answer = self.converter.jingle_to_sdp(jingle, role='answer')
            self.logger.debug(f"Converted to SDP answer:\n{sdp_answer}")
        except Exception as e:
            self.logger.error(f"Failed to convert Jingle to SDP: {e}")
            return

        # Extract and store remote ICE credentials (needed for transport-info validation)
        session = self.sessions[sid]
        contents = jingle.findall('{urn:xmpp:jingle:1}content')
        for content in contents:
            transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')
            if transport is not None:
                remote_ufrag = transport.get('ufrag')
                remote_pwd = transport.get('pwd')
                if remote_ufrag and remote_pwd:
                    session['remote_ice_ufrag'] = remote_ufrag
                    session['remote_ice_pwd'] = remote_pwd
                    self.logger.debug(f"Stored remote ICE credentials for {sid}: ufrag={remote_ufrag}")
                break

        session['state'] = 'accepted'

        self.logger.info(f"Call accepted: {sid}")

        # We'll send XEP-0353 <accept/> when the connection actually completes
        # (DTLS handshake done), not here. Sending it too early makes Conversations.im
        # think the call is connected when it's still establishing.

        # Set remote description FIRST (critical for ICE candidates to work)
        # This must happen before flushing local candidates to ensure any incoming
        # remote candidates during the flush will be accepted by the Go service
        if self.on_call_answered:
            await self.on_call_answered(sid, sdp_answer)

        # THEN flush pending local ICE candidates (trickle ICE post-accept)
        await self._flush_pending_candidates(sid)

    async def _handle_session_terminate(self, iq: Iq, jingle, sid: str):
        """Handle call termination (session-terminate)."""
        reason_el = jingle.find('{urn:xmpp:jingle:1}reason')
        reason = 'unknown'

        if reason_el is not None:
            for child in reason_el:
                reason = child.tag.split('}')[-1]  # Strip namespace
                break

        self.logger.info(f"Call terminated: {sid}, reason={reason}")

        # Send XEP-0353 <finish/> message before cleanup
        if sid in self.sessions:
            try:
                peer_jid = self.sessions[sid]['peer_jid']
                # Create message with finish element
                msg = self.xmpp.make_message(mto=peer_jid, mtype='chat')
                finish_el = ET.Element('{urn:xmpp:jingle-message:0}finish')
                finish_el.set('id', sid)
                # Add reason (format: <finish><reason><success/></reason></finish>)
                reason_el = ET.SubElement(finish_el, '{urn:xmpp:jingle:1}reason')
                # Use 'success' as default reason if not specified
                reason_name = reason if reason else 'success'
                ET.SubElement(reason_el, f'{{urn:xmpp:jingle:1}}{reason_name}')
                msg.append(finish_el)
                msg.send()
                self.logger.info(f"Sent XEP-0353 finish message for {sid} (reason: {reason_name})")
            except Exception as e:
                self.logger.warning(f"Failed to send finish message: {e}")

        # Clean up session
        if sid in self.sessions:
            del self.sessions[sid]

        # Clean up trickle ICE state and buffered candidates
        self.trickle_ice.cleanup_incoming_call(sid)

        # Notify AccountManager
        if self.on_call_terminated:
            await self.on_call_terminated(sid, reason)

    async def _handle_transport_info(self, iq: Iq, jingle, sid: str):
        """Handle ICE candidate exchange (transport-info)."""
        if sid not in self.sessions:
            self.logger.warning(f"Received transport-info for unknown session: {sid}")
            return

        session = self.sessions[sid]

        # Parse ICE candidates from transport element
        contents = jingle.findall('{urn:xmpp:jingle:1}content')
        candidates = []

        for content in contents:
            # Get content name (this is the mid value - e.g., "0", "1", "audio", etc.)
            content_name = content.get('name', 'audio')

            transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')
            if transport is not None:
                for candidate_el in transport.findall('{urn:xmpp:jingle:transports:ice-udp:1}candidate'):
                    # Accept ALL components from peer (including component 2)
                    # Hypothesis: Conversations' nomination logic requires seeing all candidates it sent
                    # Pion will create cross-component pairs that fail, but same-component pairs should succeed
                    component = candidate_el.get('component', '1')

                    cand_ip = candidate_el.get('ip')
                    cand_port = candidate_el.get('port')
                    cand_type = candidate_el.get('type')

                    candidate = {
                        'candidate': f"candidate:{candidate_el.get('foundation')} {component} {candidate_el.get('protocol')} {candidate_el.get('priority')} {cand_ip} {cand_port} typ {cand_type}",
                        'sdpMid': content_name,  # Use content name as mid (matches SDP a=mid)
                        'sdpMLineIndex': 0
                    }
                    candidates.append(candidate)
                    self.logger.info(f"Received ICE candidate for {sid}: {cand_ip}:{cand_port} ({cand_type}) component={component}")

        self.logger.debug(f"Received {len(candidates)} ICE candidates total for {sid}")

        # Track candidate statistics
        for candidate in candidates:
            self._track_ice_candidate(sid, candidate, 'received')

        # Check if we should buffer these candidates (incoming call not ready yet)
        if self.trickle_ice.should_buffer_candidates(sid):
            # Buffer candidates - they'll be processed after CreateAnswer completes
            self.trickle_ice.buffer_candidates(sid, candidates)

            # Check if we were waiting for candidates (trickle-only offer)
            if self.trickle_ice.candidates_arrived(sid):
                # First candidates arrived - trigger answer creation
                # (candidates already buffered above, will be processed later)
                offer_data = self.trickle_ice.get_deferred_offer(sid)
                if offer_data and self.on_incoming_call:
                    await self.on_incoming_call(
                        sid,
                        offer_data['peer_jid'],
                        offer_data['sdp'],
                        offer_data['media_types']
                    )

            return  # Candidates buffered, don't process yet

        # Normal flow: session is ready, add candidates immediately
        if self.on_ice_candidate_received:
            for candidate in candidates:
                await self.on_ice_candidate_received(sid, candidate)

    def _on_ice_candidate_from_webrtc_sync(self, session_id: str, candidate: Dict[str, Any]):
        """
        Sync wrapper for ICE candidate callback (called from GStreamer thread).
        Schedules the async work in the XMPP event loop.
        """
        # The XMPP client has its event loop - we need to get it from the client
        try:
            # The xmpp client (slixmpp) has its own loop
            if hasattr(self.xmpp, 'loop') and self.xmpp.loop:
                loop = self.xmpp.loop
            else:
                # Fallback: try to get the running loop
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop in this thread - use get_event_loop()
                    loop = asyncio.get_event_loop()

            # Schedule the coroutine in the XMPP loop
            asyncio.run_coroutine_threadsafe(
                self._on_ice_candidate_from_webrtc(session_id, candidate),
                loop
            )
        except Exception as e:
            self.logger.error(f"Error scheduling ICE candidate: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def _on_ice_candidate_from_webrtc(self, session_id: str, candidate: Dict[str, Any]):
        """
        Bridge ICE candidates from CallManager to Jingle transport-info.

        Uses centralized queuing decision (_should_queue_candidate).
        Queued candidates are bulk-included in session-initiate/session-accept.
        """
        if self._should_queue_candidate(session_id):
            # Queue candidate - will be included in session stanza
            if session_id not in self.pending_ice_candidates:
                self.pending_ice_candidates[session_id] = []
            self.pending_ice_candidates[session_id].append(candidate)
            state = self.sessions.get(session_id, {}).get('state', 'unknown')
            self.logger.debug(f"Queued ICE candidate for {session_id} (state={state}, queue_size={len(self.pending_ice_candidates[session_id])})")
            return

        # Session established - send via transport-info (trickle ICE)
        await self.send_ice_candidate(session_id, candidate)

    # XEP-0353 (Jingle Message Initiation) methods

    async def send_proceed(self, session_id: str):
        """
        Send XEP-0353 <proceed> message to notify peer we're ready to receive their call.

        This is used when WE (responder) accept an INCOMING call proposal:
        1. Peer sends <propose>
        2. WE send <proceed> (THIS METHOD)
        3. Peer sends <session-initiate>
        4. We send <session-accept>
        5. We send <accept>

        This is sync, not async, because xep_0353.proceed() is sync.
        """
        if session_id not in self.sessions:
            raise ValueError(f"Unknown session: {session_id}")

        session = self.sessions[session_id]
        peer_jid_full = session['peer_jid']

        # XEP-0353 messages MUST be sent to bare JID (not full JID)
        # This ensures the message gets carbon-copied to all resources
        from slixmpp import JID
        peer_jid_bare = JID(peer_jid_full).bare

        self.logger.info(f"send_proceed: session_id={session_id}, peer_jid_full={peer_jid_full}, peer_jid_bare={peer_jid_bare}")

        try:
            # Manually construct proceed message
            msg = self.xmpp.make_message(mto=peer_jid_bare, mtype='chat')
            proceed_el = msg.xml.find('{urn:xmpp:jingle-message:0}proceed')
            if proceed_el is None:
                import xml.etree.ElementTree as ET
                proceed_el = ET.SubElement(msg.xml, '{urn:xmpp:jingle-message:0}proceed')
            proceed_el.set('id', session_id)

            # Send directly - slixmpp will queue it
            msg.send()

            self.logger.info(f"Sent XEP-0353 proceed message for {session_id} to {peer_jid_bare}")
            session['state'] = 'proceeding'
        except Exception as e:
            self.logger.error(f"Failed to send proceed message: {e}", exc_info=True)
            raise

    def send_propose_accept(self, session_id: str):
        """
        Send XEP-0353 <accept> message to notify peer we're accepting their proposal.
        This is separate from Jingle session-accept - just the quick accept notification.

        This is sync, not async, because xep_0353.accept() is sync.
        """
        if session_id not in self.sessions:
            raise ValueError(f"Unknown session: {session_id}")

        session = self.sessions[session_id]
        peer_jid = session['peer_jid']

        try:
            self.xmpp.plugin['xep_0353'].accept(mto=peer_jid, sid=session_id)
            self.logger.info(f"Sent XEP-0353 accept message for {session_id}")
            session['state'] = 'accepted'
        except Exception as e:
            self.logger.error(f"Failed to send accept message: {e}")
            raise

    # XEP-0353 (Jingle Message Initiation) handlers

    async def _on_jingle_message_propose(self, msg):
        """Handle incoming <propose> message (XEP-0353)."""
        # Parse the jingle_propose stanza
        sid = msg['jingle_propose']['id']
        peer_jid = str(msg['from'])

        # TODO: Send message receipt (XEP-0184) if requested
        # Jingle messages don't have <body>, so auto-ack doesn't work
        # TEMPORARILY DISABLED due to Python 3.11 asyncio reentrancy restrictions
        # The receipt is optional - XEP-0353 doesn't require it
        if msg['request_receipt']:
            self.logger.warning(f"Receipt requested for propose {msg['id']} but sending disabled (asyncio reentrancy issue)")
            # reply = msg.reply()
            # reply['receipt'] = msg['id']
            # reply.send()

        # Extract media types from description elements
        propose = msg['jingle_propose']
        descriptions = propose.get_descriptions()
        media_types = [desc['media'] for desc in descriptions if 'media' in desc]

        if not media_types:
            media_types = ['audio']  # Default to audio if no description

        self.logger.info(f"Received call propose from {peer_jid}: {media_types}, sid={sid}")

        # Store session (will be completed when session-initiate arrives)
        self.sessions[sid] = {
            'peer_jid': peer_jid,
            'media': media_types,
            'state': 'proposed'
        }

        # This handler is DISABLED - XEP-0353 now handled by DrunkXMPP.CallsMixin
        # Keeping code for reference only

    async def _on_jingle_message_proceed(self, msg):
        """
        Handle <proceed> message - peer accepted our call.

        In XEP-0353 flow for outgoing calls:
        1. We (initiator) send <propose>
        2. Peer (responder) sends <proceed> (accepting)
        3. WE (initiator) send session-initiate to PEER
        4. Peer sends session-accept

        The initiator always sends session-initiate in Jingle.
        """
        sid = msg['jingle_proceed']['id']
        peer_jid_full = str(msg['from'])  # Full JID with resource

        self.logger.info(f"Received call proceed from {peer_jid_full} (sid={sid}) - sending session-initiate")

        if sid not in self.sessions:
            self.logger.warning(f"Received proceed for unknown session: {sid}")
            return

        # CRITICAL: Update peer_jid to the full JID with resource
        # Jingle session-initiate MUST be sent to the specific resource that sent proceed
        self.sessions[sid]['peer_jid'] = peer_jid_full
        self.sessions[sid]['state'] = 'proceeding'

        # Send session-initiate now that peer has accepted
        await self._send_session_initiate(sid)

    async def _on_jingle_message_accept(self, msg):
        """Handle <accept> message - peer accepted our call proposal (incoming call flow)."""
        sid = msg['jingle_accept']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Received call accept from {peer_jid} (sid={sid})")

        if sid not in self.sessions:
            self.logger.warning(f"Received accept for unknown session: {sid}")
            return

        # Accept from callee - they're ready for session-initiate
        # For incoming calls, we'd wait for their session-initiate instead
        self.logger.info(f"Peer accepted, waiting for their session-initiate (sid={sid})")

    async def _on_jingle_message_reject(self, msg):
        """Handle <reject> message - peer rejected our call proposal."""
        sid = msg['jingle_reject']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Call rejected by {peer_jid} (sid={sid})")

        if sid in self.sessions:
            del self.sessions[sid]

        # Notify AccountManager
        if self.on_call_terminated:
            await self.on_call_terminated(sid, 'declined')

    async def _on_jingle_message_retract(self, msg):
        """Handle <retract> message - peer canceled their call proposal."""
        sid = msg['jingle_retract']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Call retracted by {peer_jid} (sid={sid})")

        if sid in self.sessions:
            del self.sessions[sid]

        # Notify AccountManager
        if self.on_call_terminated:
            await self.on_call_terminated(sid, 'retracted')

    # SignalingAdapter interface implementation

    async def send_offer(self, peer_id: str, sdp: str, media: List[str], session_id: str = None) -> str:
        """
        Send call offer via XEP-0353 Jingle Message Initiation.

        Modern flow (required by Conversations):
        1. Send <propose> message with media description
        2. Wait for <accept> response
        3. Send <session-initiate> IQ with full Jingle

        Args:
            peer_id: Peer JID
            sdp: SDP offer from WebRTC
            media: List of media types ['audio'] or ['audio', 'video']
            session_id: Optional session ID (if not provided, generates new UUID)
        """
        sid = session_id or str(uuid.uuid4())

        # Store session info early (needed for accept handler)
        self.sessions[sid] = {
            'peer_jid': peer_id,
            'media': media,
            'sdp_offer': sdp,  # Store for later session-initiate
            'state': 'proposing'
        }

        self.logger.info(f"Sending call propose to {peer_id}: {media}, sid={sid}")

        # Step 1: Send <propose> message (XEP-0353)
        try:
            # slixmpp XEP-0353 expects descriptions as list of (namespace, media) tuples
            # Format: [(xmlns, media_type), ...]
            descriptions = [('urn:xmpp:jingle:apps:rtp:1', m) for m in media]
            self.xmpp.plugin['xep_0353'].propose(
                mto=peer_id,
                sid=sid,
                descriptions=descriptions
            )
            self.logger.info(f"Sent propose message to {peer_id} (sid={sid})")
        except Exception as e:
            self.logger.error(f"Failed to send propose: {e}")
            del self.sessions[sid]
            raise

        # We'll send session-initiate when we receive <accept>
        # See _handle_jingle_message_accept() below

        return sid

    async def _send_session_initiate(self, sid: str):
        """
        Send actual Jingle session-initiate after receiving <accept>.
        Called by XEP-0353 accept handler.
        """
        if sid not in self.sessions:
            self.logger.warning(f"Cannot send session-initiate for unknown session: {sid}")
            return

        session = self.sessions[sid]
        peer_id = session['peer_jid']
        sdp = session['sdp_offer']
        media = session['media']
        initiator = str(self.xmpp.boundjid)

        # Extract and store ICE credentials from SDP (needed for transport-info)
        ice_ufrag = None
        ice_pwd = None
        for line in sdp.split('\r\n'):
            if line.startswith('a=ice-ufrag:'):
                ice_ufrag = line.split(':', 1)[1]
            elif line.startswith('a=ice-pwd:'):
                ice_pwd = line.split(':', 1)[1]

        if ice_ufrag and ice_pwd:
            session['ice_ufrag'] = ice_ufrag
            session['ice_pwd'] = ice_pwd
            self.logger.debug(f"Stored ICE credentials for {sid}: ufrag={ice_ufrag}")

        # Validate SDP before sending
        self._validate_sdp(sdp, 'offer', sid)

        # Build Jingle session-initiate stanza
        iq = self.xmpp.make_iq_set(ito=peer_id)
        jingle_wrapper = self._build_jingle_element(
            iq, 'session-initiate', sid, initiator=initiator
        )

        # Convert SDP to Jingle XML (offer)
        jingle_content = self.converter.sdp_to_jingle(sdp, role='offer', offer_context=None)

        # Copy content and group elements from converter output to wrapper
        for content in jingle_content.findall('{urn:xmpp:jingle:1}content'):
            jingle_wrapper.append(content)
        for group in jingle_content.findall('{urn:xmpp:jingle:apps:grouping:0}group'):
            jingle_wrapper.append(group)

        # HYBRID TRICKLE ICE: Bulk-include queued candidates in session-initiate
        # Following Dino's pattern (xmpp-vala/xep/0176_jingle_ice_udp/transport_parameters.vala):
        # - Queue candidates UNTIL session-initiate is sent
        # - Include ALL queued candidates in session-initiate (not one-by-one)
        # - After session-initiate sent, new candidates go via transport-info
        # Many implementations (including Conversations.im) expect initial candidates
        # in the session stanza, not sent separately first
        if sid in self.pending_ice_candidates:
            pending = self.pending_ice_candidates[sid]
            if pending:
                self.logger.info(f"[HYBRID-ICE] Including {len(pending)} initial candidates in session-initiate")
                self._inject_candidates_into_jingle(jingle_wrapper, pending)
                # Clear the queue - candidates are now in the stanza
                del self.pending_ice_candidates[sid]
            else:
                self.logger.debug(f"[HYBRID-ICE] No pending candidates to include")

        self.logger.info(f"Sending session-initiate to {peer_id} (sid={sid})")

        # Log the full stanza for debugging
        from xml.etree import ElementTree as ET_format
        stanza_xml = ET_format.tostring(iq.xml, encoding='unicode')
        self.logger.debug(f"Sending session-initiate stanza:\n{stanza_xml}")

        # Send stanza
        try:
            await iq.send()
            session['state'] = 'pending'
            self.logger.info(f"Sent session-initiate to {peer_id}")

        except Exception as e:
            self.logger.error(f"Failed to send session-initiate: {e}")
            session['state'] = 'failed'
            # Notify AccountManager of failure
            if self.on_call_terminated:
                await self.on_call_terminated(sid, 'failed')
            raise

    async def send_answer(self, session_id: str, sdp: str):
        """
        Send call answer via XEP-0353 + Jingle session-accept.

        Flow:
        1. Send <accept> message (XEP-0353) to notify peer we're accepting
        2. Send session-accept IQ with full Jingle/SDP
        """
        if session_id not in self.sessions:
            raise ValueError(f"Unknown session: {session_id}")

        session = self.sessions[session_id]
        peer_jid = session['peer_jid']
        responder = str(self.xmpp.boundjid)

        # Extract and store local ICE credentials from SDP (needed for transport-info)
        ice_ufrag = None
        ice_pwd = None
        for line in sdp.split('\r\n'):
            if line.startswith('a=ice-ufrag:'):
                ice_ufrag = line.split(':', 1)[1]
            elif line.startswith('a=ice-pwd:'):
                ice_pwd = line.split(':', 1)[1]

        if ice_ufrag and ice_pwd:
            session['ice_ufrag'] = ice_ufrag
            session['ice_pwd'] = ice_pwd
            self.logger.debug(f"Stored ICE credentials for {session_id}: ufrag={ice_ufrag}")

        # Validate SDP before sending
        self._validate_sdp(sdp, 'answer', session_id)

        # We DON'T send XEP-0353 <accept/> here anymore
        # Sending it too early (before DTLS handshake) causes Conversations.im to show
        # "Connected" when the call is still establishing, leading to confusion.
        # The accept is now sent by AccountManager when connection state = "connected"
        # (after DTLS handshake completes). See account_manager.py:_on_call_state_changed()

        # Build Jingle session-accept stanza
        iq = self.xmpp.make_iq_set(ito=peer_jid)
        jingle_wrapper = self._build_jingle_element(
            iq, 'session-accept', session_id, responder=responder
        )

        # Convert SDP to Jingle XML (answer with offer_context for echoing)
        self.logger.info(f"[SDP-ANSWER] {session_id}:\n{sdp}")
        offer_context = self.sessions[session_id].get('offer_context')
        jingle_content = self.converter.sdp_to_jingle(sdp, role='answer', offer_context=offer_context)

        # Copy content and group elements from converter output to wrapper
        for content in jingle_content.findall('{urn:xmpp:jingle:1}content'):
            jingle_wrapper.append(content)
        for group in jingle_content.findall('{urn:xmpp:jingle:apps:grouping:0}group'):
            jingle_wrapper.append(group)

        # C++ uses trickle ICE - candidates arrive via callback after SDP creation
        # Inject any queued candidates into session-accept (hybrid trickle ICE)
        # This matches the pattern used for session-initiate (outgoing calls)
        if session_id in self.pending_ice_candidates:
            pending = self.pending_ice_candidates[session_id]
            if pending:
                self.logger.info(f"[HYBRID-ICE] Including {len(pending)} initial candidates in session-accept")
                self._inject_candidates_into_jingle(jingle_wrapper, pending)
            # Clear the queue - candidates are now in the stanza
            del self.pending_ice_candidates[session_id]

        self.logger.info(f"Sending session-accept for {session_id}")

        # Debug: Log the Jingle XML we're sending
        from xml.etree import ElementTree as ET_format
        jingle_xml = ET_format.tostring(jingle_wrapper, encoding='unicode')
        self.logger.info(f"[JINGLE-ANSWER] {session_id}:\n{jingle_xml}")

        # Send stanza
        try:
            await iq.send()
            session['state'] = 'active'
            self.logger.info(f"Sent session-accept for {session_id}")

        except Exception as e:
            self.logger.error(f"Failed to send session-accept: {e}")
            raise

    async def send_ice_candidate(self, session_id: str, candidate: Dict[str, Any]):
        """Send ICE candidate via Jingle transport-info."""
        self.logger.info(f"[CAND-DEBUG] send_ice_candidate called: session={session_id}")
        self.logger.info(f"[CAND-DEBUG] candidate dict: {candidate}")

        if session_id not in self.sessions:
            self.logger.warning(f"Attempted to send ICE for unknown session: {session_id}")
            self.logger.info(f"[CAND-DEBUG] Available sessions: {list(self.sessions.keys())}")
            return

        session = self.sessions[session_id]
        peer_jid = session['peer_jid']
        self.logger.info(f"[CAND-DEBUG] Session found, peer_jid: {peer_jid}")

        # Parse candidate string from aiortc
        # Format: "candidate:foundation component protocol priority ip port typ type [raddr X rport Y]"
        # Example: "candidate:1 1 UDP 2130706431 192.168.1.100 54321 typ host"
        cand_str = candidate.get('candidate', '')
        self.logger.info(f"[CAND-DEBUG] Candidate string: {cand_str}")

        if not cand_str.startswith('candidate:'):
            self.logger.warning(f"Invalid candidate format: {cand_str}")
            self.logger.info(f"[CAND-DEBUG] REJECTED: Doesn't start with 'candidate:'")
            return

        # Remove "candidate:" prefix and parse
        parts = cand_str.split('candidate:', 1)[1].split(' ')
        self.logger.info(f"[CAND-DEBUG] Parsed parts: {parts}")

        if len(parts) < 8:
            self.logger.warning(f"Incomplete candidate: {cand_str}")
            self.logger.info(f"[CAND-DEBUG] REJECTED: Only {len(parts)} parts (need 8+)")
            return

        foundation = parts[0]
        component = parts[1]
        protocol = parts[2].lower()
        priority = parts[3]
        ip = parts[4]
        port = parts[5]
        # parts[6] is "typ"
        cand_type = parts[7]

        self.logger.info(f"[CAND-DEBUG] Parsed candidate: protocol={protocol}, ip={ip}, port={port}, type={cand_type}")

        # Filter out TCP candidates - Conversations.im doesn't support them
        # This prevents "service-unavailable" errors in transport-info
        if protocol == 'tcp':
            self.logger.info(f"[CAND-DEBUG] REJECTED: TCP candidate (not supported by Conversations.im)")
            self.logger.debug(f"Skipping TCP ICE candidate (not supported by Conversations.im): {cand_str}")
            return

        self.logger.info(f"[CAND-DEBUG] Candidate passed all checks, building transport-info stanza...")

        # Optional: related address/port for srflx/relay candidates
        rel_addr = None
        rel_port = None
        if len(parts) >= 12 and parts[8] == 'raddr':
            rel_addr = parts[9]
            rel_port = parts[11]  # parts[10] is "rport"

        # Build Jingle transport-info stanza
        iq = self.xmpp.make_iq_set(ito=peer_jid)
        jingle = self._build_jingle_element(iq, 'transport-info', session_id)

        # Add content with transport candidates
        content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
        content.set('creator', 'initiator')
        # Use sdpMid directly as content name (e.g., '0', 'audio')
        # This MUST match the content name from session-initiate/session-accept
        # C++ may send empty sdpMid, so default to 'audio' for audio-only calls
        sdp_mid = candidate.get('sdpMid', 'audio')
        if not sdp_mid:  # Handle empty string from C++
            sdp_mid = 'audio'
        content.set('name', sdp_mid)

        transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')

        # Credentials in transport-info - behavior varies across implementations
        # Some clients include ufrag/pwd in transport-info, we currently don't
        # Credentials are already exchanged in session-initiate/session-accept
        # Tested with/without - no difference observed for Conversations compatibility
        # ice_ufrag = session.get('ice_ufrag')
        # ice_pwd = session.get('ice_pwd')
        # if ice_ufrag and ice_pwd:
        #     transport.set('ufrag', ice_ufrag)
        #     transport.set('pwd', ice_pwd)

        # Add parsed candidate
        # With RTCPMuxPolicyNegotiate, Pion will send both component 1 and 2 natively
        cand_el = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
        cand_el.set('foundation', foundation)
        cand_el.set('component', component)
        cand_el.set('protocol', protocol)
        cand_el.set('priority', priority)
        cand_el.set('ip', ip)
        cand_el.set('port', port)
        cand_el.set('type', cand_type)
        cand_el.set('generation', '0')

        # Add related address/port if present
        if rel_addr and rel_port:
            cand_el.set('rel-addr', rel_addr)
            cand_el.set('rel-port', rel_port)

        self.logger.info(f"[CAND-DEBUG] About to send transport-info stanza...")
        self.logger.debug(f"Sending ICE candidate for {session_id}: {ip}:{port} ({cand_type})")

        # Track candidate statistics
        self._track_ice_candidate(session_id, candidate, 'sent')

        # Send stanza
        try:
            self.logger.info(f"[CAND-DEBUG] Calling iq.send()...")
            await iq.send()
            self.logger.info(f"[CAND-DEBUG] ✅ transport-info sent successfully!")
        except Exception as e:
            self.logger.error(f"Failed to send transport-info: {e}")
            self.logger.info(f"[CAND-DEBUG] ❌ Exception during send: {e}")
            import traceback
            self.logger.info(f"[CAND-DEBUG] Traceback: {traceback.format_exc()}")

    async def terminate(self, session_id: str, reason: str = 'success'):
        """Terminate call via Jingle session-terminate."""
        if session_id not in self.sessions:
            self.logger.warning(f"Attempted to terminate unknown session: {session_id}")
            return

        session = self.sessions[session_id]
        peer_jid = session['peer_jid']

        # Build Jingle session-terminate stanza
        iq = self.xmpp.make_iq_set(ito=peer_jid)
        jingle = self._build_jingle_element(iq, 'session-terminate', session_id)

        # Add reason element
        reason_el = ET.SubElement(jingle, '{urn:xmpp:jingle:1}reason')
        ET.SubElement(reason_el, f'{{urn:xmpp:jingle:1}}{reason}')

        self.logger.info(f"Terminating call: {session_id}, reason={reason}")

        # Send stanza
        try:
            await iq.send()
            self.logger.info(f"Sent session-terminate for {session_id}")
        except Exception as e:
            self.logger.error(f"Failed to send session-terminate: {e}")
        finally:
            # Clean up session
            if session_id in self.sessions:
                del self.sessions[session_id]

    async def cleanup_session(self, session_id: str, send_terminate: bool = False):
        """
        Clean up Jingle session resources.

        This is called by AccountManager.end_call() to ensure complete cleanup.

        Args:
            session_id: Session to clean up
            send_terminate: Whether to send session-terminate IQ (default: False, already handled by terminate())
        """
        if send_terminate and session_id in self.sessions:
            # Send terminate if requested (fallback, usually already sent)
            await self.terminate(session_id, reason='success')

        # Clean up session metadata (terminate() already does this, but be defensive)
        if session_id in self.sessions:
            del self.sessions[session_id]
            self.logger.debug(f"Cleaned up Jingle session: {session_id}")

        # Clean up pending ICE candidates (MEMORY LEAK FIX)
        if session_id in self.pending_ice_candidates:
            candidate_count = len(self.pending_ice_candidates[session_id])
            del self.pending_ice_candidates[session_id]
            self.logger.debug(f"Cleaned up {candidate_count} pending ICE candidates for {session_id}")

        # Clean up trickle ICE state and buffered candidates
        self.trickle_ice.cleanup_incoming_call(session_id)

    # Helper methods for Jingle XML building

    def _inject_candidates_into_jingle(self, jingle: ET.Element, candidates: List[Dict[str, Any]]):
        """
        Inject ICE candidates into Jingle XML transport elements.

        Used for hybrid Trickle ICE - include initial candidates in session-initiate/session-accept.
        """
        # Find all transport elements in the jingle stanza
        for content in jingle.findall('{urn:xmpp:jingle:1}content'):
            transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')
            if transport is None:
                continue

            # Inject each candidate
            for candidate in candidates:
                # Parse candidate string (same logic as send_ice_candidate)
                cand_str = candidate.get('candidate', '')
                if not cand_str.startswith('candidate:'):
                    self.logger.warning(f"[HYBRID-ICE] Skipping invalid candidate: {cand_str}")
                    continue

                parts = cand_str.split('candidate:', 1)[1].split(' ')
                if len(parts) < 8:
                    self.logger.warning(f"[HYBRID-ICE] Skipping incomplete candidate: {cand_str}")
                    continue

                foundation = parts[0]
                component = parts[1]
                protocol = parts[2].lower()
                priority = parts[3]
                ip = parts[4]
                port = parts[5]
                cand_type = parts[7]  # parts[6] is "typ"

                # Filter TCP candidates (Conversations.im doesn't support them)
                if protocol == 'tcp':
                    self.logger.debug(f"[HYBRID-ICE] Filtering TCP candidate: {ip}:{port}")
                    continue

                # Optional: related address/port for srflx/relay
                rel_addr = None
                rel_port = None
                if len(parts) >= 12 and parts[8] == 'raddr':
                    rel_addr = parts[9]
                    rel_port = parts[11]

                # Create candidate element
                cand_el = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
                cand_el.set('foundation', foundation)
                cand_el.set('component', component)
                cand_el.set('protocol', protocol)
                cand_el.set('priority', priority)
                cand_el.set('ip', ip)
                cand_el.set('port', port)
                cand_el.set('type', cand_type)
                cand_el.set('generation', '0')

                if rel_addr and rel_port:
                    cand_el.set('rel-addr', rel_addr)
                    cand_el.set('rel-port', rel_port)

                self.logger.debug(f"[HYBRID-ICE] Injected candidate: {ip}:{port} ({cand_type})")

    def _build_jingle_element(self, iq: Iq, action: str, sid: str,
                              initiator: Optional[str] = None,
                              responder: Optional[str] = None) -> ET.Element:
        """Build a <jingle> element with common attributes."""
        jingle = ET.SubElement(iq.xml, '{urn:xmpp:jingle:1}jingle')
        jingle.set('action', action)
        jingle.set('sid', sid)

        if initiator:
            jingle.set('initiator', initiator)
        if responder:
            jingle.set('responder', responder)

        return jingle

    # =========================================================================
    # CallBridge Event Callbacks (Go → Python → Jingle)
    # =========================================================================

    async def _on_bridge_ice_candidate(self, session_id: str, candidate: Dict[str, Any]):
        """
        Handle ICE candidate from CallBridge (C++ service).

        Routing logic:
        - OUTGOING calls: Queue until session-initiate sent, then send via transport-info
        - INCOMING calls: Send immediately via transport-info (trickle ICE)

        Args:
            session_id: Jingle session ID
            candidate: ICE candidate dict with 'candidate', 'sdpMid', 'sdpMLineIndex'
        """
        self.logger.debug(f"Received ICE candidate from C++ for {session_id}")

        # Check if we should queue (only for outgoing calls before session-initiate)
        if self._should_queue_candidate(session_id):
            # OUTGOING CALL PATH: Queue until session-initiate is sent
            if session_id not in self.pending_ice_candidates:
                self.pending_ice_candidates[session_id] = []
            self.pending_ice_candidates[session_id].append(candidate)
            state = self.sessions.get(session_id, {}).get('state', 'unknown')
            self.logger.debug(f"[OUTGOING] Queued ICE candidate for {session_id} (state={state}, queue_size={len(self.pending_ice_candidates[session_id])})")
            return

        # TRICKLE ICE PATH: Send immediately via transport-info
        # Delegate to existing send_ice_candidate method (handles all XML correctly)
        self.logger.debug(f"[TRICKLE-ICE] Sending candidate via transport-info for {session_id}")
        try:
            await self.send_ice_candidate(session_id, candidate)
        except Exception as e:
            self.logger.error(f"Error sending ICE candidate: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

        # ===================================================================
        # COMMENTED OUT: Alternative inline implementation (saved for standard ICE)
        # May be needed if standard ICE requires different XML structure
        # ===================================================================
        # # Get peer JID and session data
        # if session_id not in self.sessions:
        #     self.logger.warning(f"Cannot send ICE candidate - session {session_id} not found")
        #     return
        #
        # session = self.sessions[session_id]
        # peer_jid = session['peer_jid']
        #
        # # Parse ICE candidate string
        # candidate_str = candidate.get('candidate', '')
        # sdp_mid = candidate.get('sdpMid', 'audio')  # Default to audio
        # sdp_mline_index = candidate.get('sdpMLineIndex', 0)
        #
        # # Convert SDP candidate string to Jingle XML format
        # # Example: "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"
        # parts = candidate_str.split()
        # if len(parts) < 8:
        #     self.logger.warning(f"Invalid ICE candidate format: {candidate_str}")
        #     return
        #
        # try:
        #     foundation = parts[0].split(':')[1]  # Remove "candidate:" prefix
        #     component = parts[1]
        #     protocol = parts[2]
        #     priority = parts[3]
        #     ip = parts[4]
        #     port = parts[5]
        #     # parts[6] is "typ"
        #     cand_type = parts[7]
        #
        #     # Build Jingle transport-info IQ
        #     iq = self.xmpp.make_iq_set(ito=peer_jid)
        #     # Access underlying XML element (slixmpp Iq stanza)
        #     jingle = ET.SubElement(iq.xml, '{urn:xmpp:jingle:1}jingle')
        #     jingle.set('action', 'transport-info')
        #     jingle.set('sid', session_id)
        #
        #     # Add content element
        #     content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
        #     content.set('creator', session.get('creator', 'initiator'))
        #     content.set('name', sdp_mid)
        #
        #     # Add transport element with candidate
        #     transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
        #     candidate_elem = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
        #     candidate_elem.set('component', component)
        #     candidate_elem.set('foundation', foundation)
        #     candidate_elem.set('generation', '0')
        #     candidate_elem.set('id', f"cand-{foundation}")
        #     candidate_elem.set('ip', ip)
        #     candidate_elem.set('network', '0')
        #     candidate_elem.set('port', port)
        #     candidate_elem.set('priority', priority)
        #     candidate_elem.set('protocol', protocol)
        #     candidate_elem.set('type', cand_type)
        #
        #     # Send transport-info
        #     await iq.send()
        #     self.logger.debug(f"[TRICKLE-ICE] Sent candidate via transport-info to {peer_jid}")
        #
        # except Exception as e:
        #     self.logger.error(f"Error sending ICE candidate: {e}")
        #     import traceback
        #     self.logger.error(traceback.format_exc())
        # ===================================================================

    async def _on_bridge_connection_state(self, session_id: str, state: str):
        """
        Handle connection state change from CallBridge (Go service).

        Forwards state to AccountManager for GUI updates.

        Args:
            session_id: Jingle session ID
            state: Connection state ('new', 'checking', 'connected', 'completed', 'failed', 'disconnected', 'closed')
        """
        self.logger.info(f"Connection state for {session_id}: {state}")

        # Forward to on_call_state_changed callback if set (AccountManager uses this for GUI updates)
        if self.on_call_state_changed:
            await self.on_call_state_changed(session_id, state)
        else:
            self.logger.debug(f"No on_call_state_changed callback set for {session_id}")

    def _validate_sdp(self, sdp: str, sdp_type: str, session_id: str):
        """
        Validate and log SDP statistics for debugging.

        Args:
            sdp: SDP string
            sdp_type: Type of SDP ('offer', 'answer')
            session_id: Jingle session ID
        """
        lines = sdp.split('\n')
        media_count = sdp.count("m=")
        ice_count = sdp.count("a=candidate:")

        # Extract ICE credentials
        ice_ufrag = None
        ice_pwd = None
        for line in lines:
            line = line.strip()
            if line.startswith('a=ice-ufrag:'):
                ice_ufrag = line.split(':', 1)[1].strip()
            elif line.startswith('a=ice-pwd:'):
                ice_pwd = line.split(':', 1)[1].strip()

        self.logger.info(
            f"[SDP-VALID] {session_id} ({sdp_type}): "
            f"{media_count} media, {ice_count} candidates, "
            f"ufrag={ice_ufrag}, pwd={'SET' if ice_pwd else 'MISSING'}"
        )

        # Warn if missing critical fields
        if not ice_ufrag or not ice_pwd:
            self.logger.warning(
                f"[SDP-VALID] {session_id}: ICE credentials missing! "
                f"ufrag={ice_ufrag}, pwd={ice_pwd}"
            )

    def _should_queue_candidate(self, session_id: str) -> bool:
        """
        Determine if ICE candidate should be queued vs sent immediately.

        ==================================================================================
        TRICKLE ICE STRATEGY (C++ service always uses trickle ICE):
        ==================================================================================

        OUTGOING CALLS (we initiate):
        - Queue candidates while preparing session-initiate (states: proposing, proceeding)
        - Include queued candidates in session-initiate stanza (hybrid approach)
        - After session-initiate sent (state: pending/active), send via transport-info

        INCOMING CALLS (peer initiates):
        - NO QUEUING - send all candidates via transport-info (pure trickle ICE)
        - Rationale: Session already established in Python when C++ generates candidates
        - States: incoming, accepted, active → always send via transport-info

        ==================================================================================

        State definitions:
        - proposing: Outgoing call, propose sent, waiting for proceed
        - proceeding: Outgoing call, proceed received, preparing session-initiate
        - pending: Outgoing call, session-initiate sent, waiting for session-accept
        - incoming: Incoming call, session-initiate received, preparing session-accept
        - accepted: Incoming call, session-accept about to be sent
        - active: Session established (both directions)

        Args:
            session_id: Jingle session ID

        Returns:
            True if candidate should be queued, False if it should be sent via transport-info
        """
        if session_id not in self.sessions:
            # Session doesn't exist yet - queue it (rare edge case)
            return True

        state = self.sessions[session_id].get('state', 'new')

        # ONLY queue for outgoing calls before session-initiate is sent
        return state in ('proposing', 'proceeding', 'pending')

    async def _flush_pending_candidates(self, session_id: str) -> None:
        """
        Flush queued ICE candidates after session-accept exchange.

        Called after:
        - Receiving session-accept (outgoing call accepted by peer)
        - Sending session-accept (incoming call accepted by us)

        Note: For session-initiate, candidates are bulk-included in the stanza
        (see _send_session_initiate lines 757-763). This method handles the
        post-accept trickle ICE flow.

        Args:
            session_id: Jingle session ID
        """
        if session_id in self.pending_ice_candidates:
            pending = self.pending_ice_candidates[session_id]
            self.logger.info(f"Flushing {len(pending)} queued ICE candidates for {session_id}")
            for cand in pending:
                await self.send_ice_candidate(session_id, cand)
            del self.pending_ice_candidates[session_id]

    def _track_ice_candidate(self, session_id: str, candidate: Dict[str, Any], direction: str):
        """
        Track ICE candidate statistics for debugging.

        Args:
            session_id: Jingle session ID
            candidate: Candidate dict with 'candidate' field
            direction: 'sent' or 'received'
        """
        if session_id not in self._ice_stats:
            self._ice_stats[session_id] = {
                'sent': 0,
                'received': 0,
                'sent_by_type': {},
                'received_by_type': {}
            }

        self._ice_stats[session_id][direction] += 1

        # Parse candidate type from SDP candidate string
        cand_str = candidate.get('candidate', '')
        if cand_str.startswith('candidate:'):
            parts = cand_str.split()
            if len(parts) >= 8:
                cand_type = parts[7]  # typ host/srflx/relay
                type_key = f'{direction}_by_type'
                self._ice_stats[session_id][type_key][cand_type] = \
                    self._ice_stats[session_id][type_key].get(cand_type, 0) + 1

        # Log summary
        stats = self._ice_stats[session_id]
        if direction == 'received':
            self.logger.info(
                f"[ICE-STATS] {session_id}: Received {stats['received']} total "
                f"(host:{stats['received_by_type'].get('host', 0)}, "
                f"srflx:{stats['received_by_type'].get('srflx', 0)}, "
                f"relay:{stats['received_by_type'].get('relay', 0)})"
            )
        else:
            self.logger.info(
                f"[ICE-STATS] {session_id}: Sent {stats['sent']} total "
                f"(host:{stats['sent_by_type'].get('host', 0)}, "
                f"srflx:{stats['sent_by_type'].get('srflx', 0)}, "
                f"relay:{stats['sent_by_type'].get('relay', 0)})"
            )
