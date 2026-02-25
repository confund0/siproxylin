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
                error_iq = iq.reply()
                error_iq['type'] = 'error'
                error_iq.send()
                return

            # Send IQ result (ACK)
            iq.reply().send()

        except Exception as e:
            self.logger.error(f"Error handling Jingle IQ: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            error_iq = iq.reply()
            error_iq['type'] = 'error'
            error_iq.send()

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
            sdp_offer = self._jingle_to_sdp(jingle, 'offer')
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

        # Extract and store offer details for echoing in answer (WebRTC standard behavior)
        offer_details = self._extract_offer_details(jingle)
        self.sessions[sid]['offer_details'] = offer_details

        # Count candidates in the offer SDP
        candidate_count = sdp_offer.count('a=candidate:')
        self.logger.info(f"Offer contains {candidate_count} candidates in SDP")

        # TRICKLE ICE FIX: Detect trickle-only offers (0 candidates in SDP)
        # Conversations.im sends offers with 0 candidates, relying entirely on trickle ICE.
        # This causes a race condition: we call setRemoteDescription(offer) with 0 candidates,
        # Pion starts ICE checking with 0 remote candidates, then candidates arrive 400ms later.
        # Fix: Defer answer creation until we receive at least one candidate via transport-info.
        if candidate_count == 0:
            self.logger.info(f"[TRICKLE-ICE] Offer has 0 candidates - deferring answer until candidates arrive")
            self.sessions[sid]['waiting_for_candidates'] = True
            self.sessions[sid]['sdp_offer'] = sdp_offer  # Store for later

            # Safety timeout: If no candidates arrive within 5 seconds, proceed anyway
            # This prevents the call from hanging indefinitely if transport-info gets lost
            import asyncio
            async def candidates_timeout():
                await asyncio.sleep(5.0)
                if sid in self.sessions and self.sessions[sid].get('waiting_for_candidates', False):
                    self.logger.warning(f"[TRICKLE-ICE] Timeout waiting for candidates for {sid} - proceeding anyway")
                    self.sessions[sid]['waiting_for_candidates'] = False
                    if self.on_candidates_ready:
                        await self.on_candidates_ready(sid)

            # Schedule the timeout (fire and forget)
            asyncio.create_task(candidates_timeout())
        else:
            self.logger.info(f"[TRICKLE-ICE] Offer has {candidate_count} candidates - proceeding normally")
            self.sessions[sid]['waiting_for_candidates'] = False

        self.logger.info(f"Incoming call from {peer_jid}: {media_types}")

        # Notify AccountManager (this triggers answer creation in normal flow)
        # For trickle-only offers, we'll defer the actual answer creation
        if self.on_incoming_call:
            await self.on_incoming_call(sid, peer_jid, sdp_offer, media_types)

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
            sdp_answer = self._jingle_to_sdp(jingle, 'answer')
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

        # THEN flush pending local ICE candidates
        if sid in self.pending_ice_candidates:
            pending = self.pending_ice_candidates[sid]
            self.logger.info(f"Flushing {len(pending)} queued ICE candidates for {sid}")
            for cand in pending:
                await self.send_ice_candidate(sid, cand)
            del self.pending_ice_candidates[sid]

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

        # TRICKLE ICE FIX: Check if we were waiting for candidates before creating answer
        # This handles the race condition where Conversations sends trickle-only offers
        waiting_for_candidates = session.get('waiting_for_candidates', False)
        if waiting_for_candidates and len(candidates) > 0:
            self.logger.info(f"[TRICKLE-ICE] First candidates arrived for {sid}, now proceeding with deferred answer creation")
            session['waiting_for_candidates'] = False

            # Add candidates to Pion FIRST before creating answer
            if self.on_ice_candidate_received:
                for candidate in candidates:
                    await self.on_ice_candidate_received(sid, candidate)

            # Now trigger the deferred answer creation
            # We need to call the account manager's accept_call method which will create the answer
            # The sdp_offer was already stored in session-initiate handler
            # Signal to AccountManager that it can now proceed with answer creation
            if self.on_candidates_ready:
                await self.on_candidates_ready(sid)

            return  # Don't process candidates again below

        # Normal flow: add candidates to ongoing session
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
        """Bridge ICE candidates from CallManager to Jingle transport-info."""
        # Check if session-initiate has been sent yet
        if session_id in self.sessions:
            state = self.sessions[session_id].get('state', 'new')
            if state in ('proposing', 'proceeding', 'pending'):
                # Session-initiate hasn't been sent yet - queue the candidate
                if session_id not in self.pending_ice_candidates:
                    self.pending_ice_candidates[session_id] = []
                self.pending_ice_candidates[session_id].append(candidate)
                self.logger.debug(f"Queued ICE candidate for {session_id} (state={state}, queue_size={len(self.pending_ice_candidates[session_id])})")
                return

        # Session-initiate sent (outgoing call) or session-accept sent (incoming call) - send candidate immediately
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
        jingle = self._build_jingle_element(
            iq, 'session-initiate', sid, initiator=initiator
        )

        # Convert SDP to Jingle XML
        self._sdp_to_jingle(sdp, jingle, media, session_id=sid)

        # HYBRID TRICKLE ICE: Include initial candidates in session-initiate
        # (many implementations, including Conversations.im, expect this)
        if sid in self.pending_ice_candidates:
            pending = self.pending_ice_candidates[sid]
            if pending:
                self.logger.info(f"[HYBRID-ICE] Including {len(pending)} initial candidates in session-initiate")
                self._inject_candidates_into_jingle(jingle, pending)
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
        jingle = self._build_jingle_element(
            iq, 'session-accept', session_id, responder=responder
        )

        # Convert SDP to Jingle XML
        self.logger.info(f"[SDP-ANSWER] {session_id}:\n{sdp}")
        # Don't include SSRC in session-accept (not standard for answers, even though offer has it)
        self._sdp_to_jingle(sdp, jingle, session['media'], session_id=session_id, include_ssrc=False)

        # Candidates are already in the SDP (we wait for gathering to complete in Go)
        # _sdp_to_jingle() has already parsed and added them to the Jingle XML
        # Clear pending queue to avoid duplicates (previously caused both components to have same ports)
        if session_id in self.pending_ice_candidates:
            pending_count = len(self.pending_ice_candidates[session_id])
            del self.pending_ice_candidates[session_id]
            self.logger.info(f"[HYBRID-ICE] Cleared {pending_count} pending candidates (already in SDP)")

        self.logger.info(f"Sending session-accept for {session_id}")

        # Debug: Log the Jingle XML we're sending
        from xml.etree import ElementTree as ET_format
        jingle_xml = ET_format.tostring(jingle, encoding='unicode')
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
        # Use sdpMid directly as content name (e.g., '0')
        # This MUST match the content name from session-initiate/session-accept
        sdp_mid = candidate.get('sdpMid', '0')
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

    def _extract_offer_details(self, jingle: ET.Element) -> Dict[str, Any]:
        """
        Extract offer details for later echoing in answer.

        Extracts WebRTC features from incoming Jingle offer:
        - BUNDLE group
        - RTP header extensions
        - Codec parameters
        - RTCP-FB elements

        This follows WebRTC standard behavior: features in offer should be
        echoed back in answer if we support them.

        Args:
            jingle: Jingle element from session-initiate

        Returns:
            Dictionary with offer details
        """
        details = {
            'bundle_group': None,
            'rtp_extensions': [],       # [{id, uri}]
            'codec_params': {},          # {pt_id: {param_name: param_value}}
            'rtcp_fb': {},               # {pt_id: [type values]}
            'has_ssrc': False,           # Whether offer includes SSRC
            'ssrc_params': [],           # List of SSRC parameter names in offer (e.g., ['cname', 'msid'])
            'extmap_allow_mixed': False, # Whether offer has extmap-allow-mixed
            'encryption': {}             # {media: {crypto-suite, key-params, tag}} - SDES encryption per media
        }

        # Extract BUNDLE group (RFC 9143)
        group = jingle.find('{urn:xmpp:jingle:apps:grouping:0}group[@semantics="BUNDLE"]')
        if group is not None:
            content_names = []
            for content_ref in group.findall('{urn:xmpp:jingle:apps:grouping:0}content'):
                content_names.append(content_ref.get('name'))
            details['bundle_group'] = content_names
            self.logger.debug(f"Extracted BUNDLE group: {content_names}")

        # Extract RTP extensions, codec params, RTCP-FB, encryption from each content
        for content in jingle.findall('{urn:xmpp:jingle:1}content'):
            description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
            if description is None:
                continue

            media = description.get('media')

            # SDES encryption (legacy, but Dino includes it)
            encryption_el = description.find('{urn:xmpp:jingle:apps:rtp:1}encryption')
            if encryption_el is not None:
                crypto_el = encryption_el.find('{urn:xmpp:jingle:apps:rtp:1}crypto')
                if crypto_el is not None:
                    details['encryption'][media] = {
                        'crypto-suite': crypto_el.get('crypto-suite'),
                        'key-params': crypto_el.get('key-params'),
                        'tag': crypto_el.get('tag')
                    }
                    self.logger.debug(f"Extracted SDES encryption for {media}")

            # RTP header extensions (RFC 8285)
            for ext in description.findall('{urn:xmpp:jingle:apps:rtp:rtp-hdrext:0}rtp-hdrext'):
                details['rtp_extensions'].append({
                    'id': ext.get('id'),
                    'uri': ext.get('uri')
                })

            # Check for extmap-allow-mixed (RFC 8285)
            if description.find('{urn:xmpp:jingle:apps:rtp:rtp-hdrext:0}extmap-allow-mixed') is not None:
                details['extmap_allow_mixed'] = True

            # Check for SSRC (XEP-0294) and extract parameter names
            source = description.find('{urn:xmpp:jingle:apps:rtp:ssma:0}source')
            if source is not None:
                details['has_ssrc'] = True
                # Extract parameter names (e.g., ['cname', 'msid'])
                # Conversations sends: cname, msid (2 params)
                # Some clients might send: cname only (1 param)
                # We'll only echo parameters that were in the offer
                for param in source.findall('{urn:xmpp:jingle:apps:rtp:ssma:0}parameter'):
                    param_name = param.get('name')
                    if param_name and param_name not in details['ssrc_params']:
                        details['ssrc_params'].append(param_name)
                self.logger.debug(f"Offer SSRC has parameters: {details['ssrc_params']}")

            # Codec parameters
            for pt in description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type'):
                pt_id = pt.get('id')
                params = {}
                for param in pt.findall('{urn:xmpp:jingle:apps:rtp:1}parameter'):
                    params[param.get('name')] = param.get('value')
                if params:
                    details['codec_params'][pt_id] = params

                # RTCP-FB
                fb_types = []
                for fb in pt.findall('{urn:xmpp:jingle:apps:rtp:rtcp-fb:0}rtcp-fb'):
                    fb_types.append(fb.get('type'))
                if fb_types:
                    details['rtcp_fb'][pt_id] = fb_types

        # Log what we extracted
        if details['bundle_group']:
            self.logger.info(f"Offer has BUNDLE group with {len(details['bundle_group'])} contents")
        if details['rtp_extensions']:
            self.logger.info(f"Offer has {len(details['rtp_extensions'])} RTP extensions")
        if details['codec_params']:
            self.logger.info(f"Offer has codec params for {len(details['codec_params'])} payload types")
        if details['has_ssrc']:
            self.logger.info(f"Offer has SSRC with parameters: {details['ssrc_params']}")

        return details

    def _echo_offer_features(self, jingle: ET.Element, offer_details: Dict[str, Any], media_sections: List):
        """
        Echo offer features in answer (BUNDLE, RTP extensions, codec params).

        This implements WebRTC standard behavior: features present in the offer
        should be echoed back in the answer to indicate support.

        Args:
            jingle: Jingle element being built (answer)
            offer_details: Stored offer details from _extract_offer_details()
            media_sections: List of media sections parsed from SDP
        """
        # Find all content elements (we just built them in _sdp_to_jingle)
        contents = jingle.findall('{urn:xmpp:jingle:1}content')
        if not contents:
            return

        # Echo RTP header extensions (add to first audio/video description)
        # Per BUNDLE: extensions are shared across bundled media
        if offer_details['rtp_extensions']:
            for content in contents:
                description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
                if description is not None:
                    for ext in offer_details['rtp_extensions']:
                        ext_el = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:rtp-hdrext:0}rtp-hdrext')
                        ext_el.set('id', ext['id'])
                        ext_el.set('uri', ext['uri'])
                    self.logger.debug(f"Echoed {len(offer_details['rtp_extensions'])} RTP extensions in answer")

                    # Echo extmap-allow-mixed if it was in the offer (RFC 8285)
                    if offer_details.get('extmap_allow_mixed', False):
                        ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:rtp-hdrext:0}extmap-allow-mixed')
                        self.logger.debug("Echoed extmap-allow-mixed in answer")

                    break  # Only add to first media (per BUNDLE)

        # Echo codec parameters and RTCP-FB (for codecs we selected in our answer)
        # We only echo params for payload types that exist in our answer
        if offer_details['codec_params'] or offer_details['rtcp_fb']:
            for content in contents:
                description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
                if description is None:
                    continue

                for pt in description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type'):
                    pt_id = pt.get('id')

                    # Echo codec parameters
                    if pt_id in offer_details['codec_params']:
                        # Check if we already have parameters (from Pion's SDP fmtp parsing)
                        existing_params = pt.findall('{urn:xmpp:jingle:apps:rtp:1}parameter')
                        if not existing_params:
                            # No params yet - add from offer
                            for param_name, param_value in offer_details['codec_params'][pt_id].items():
                                param_el = ET.SubElement(pt, '{urn:xmpp:jingle:apps:rtp:1}parameter')
                                param_el.set('name', param_name)
                                param_el.set('value', param_value)
                            self.logger.debug(f"Echoed codec params for pt={pt_id} from offer")

                    # Echo RTCP-FB feedback types (transport-cc, etc.)
                    if pt_id in offer_details['rtcp_fb']:
                        for fb_type in offer_details['rtcp_fb'][pt_id]:
                            fb_el = ET.SubElement(pt, '{urn:xmpp:jingle:apps:rtp:rtcp-fb:0}rtcp-fb')
                            fb_el.set('type', fb_type)
                        self.logger.debug(f"Echoed {len(offer_details['rtcp_fb'][pt_id])} RTCP-FB types for pt={pt_id}")

        # Add BUNDLE group (CRITICAL for Monocles!)
        # This MUST be added at the jingle level, not inside content
        if offer_details['bundle_group']:
            group_el = ET.SubElement(jingle, '{urn:xmpp:jingle:apps:grouping:0}group')
            group_el.set('semantics', 'BUNDLE')
            for content in contents:
                content_name = content.get('name')
                if content_name:
                    content_ref = ET.SubElement(group_el, '{urn:xmpp:jingle:apps:grouping:0}content')
                    content_ref.set('name', content_name)
            self.logger.info(f"Added BUNDLE group with {len(contents)} contents to answer")

    def _sdp_to_jingle(self, sdp: str, jingle: ET.Element, media: List[str], session_id: Optional[str] = None, include_ssrc: bool = True):
        """
        Convert SDP to Jingle XML content elements.

        Parses SDP and extracts:
        - Media descriptions (audio/video)
        - Codecs (payload types)
        - ICE candidates
        - DTLS fingerprint
        - ICE credentials (ufrag/pwd)

        Args:
            sdp: SDP string to convert
            jingle: Jingle XML element to populate
            media: List of media types (e.g., ['audio'])
            session_id: Optional session ID (used to access offer_details for echoing features)
        """
        # Parse SDP into lines
        sdp_lines = sdp.strip().split('\r\n')

        # Session-level attributes (used as fallback for media-specific)
        global_ice_ufrag = None
        global_ice_pwd = None
        global_dtls_fingerprint = None
        global_dtls_hash = None
        global_dtls_setup = None

        # Parse session-level attributes (fallback values)
        for line in sdp_lines:
            if line.startswith('a=ice-ufrag:'):
                global_ice_ufrag = line.split(':', 1)[1]
            elif line.startswith('a=ice-pwd:'):
                global_ice_pwd = line.split(':', 1)[1]
            elif line.startswith('a=fingerprint:'):
                # Format: "a=fingerprint:sha-256 AB:CD:EF:..."
                parts = line.split(':', 1)[1].split(' ', 1)
                global_dtls_hash = parts[0]
                global_dtls_fingerprint = parts[1]
            elif line.startswith('a=setup:'):
                global_dtls_setup = line.split(':', 1)[1]

        # Parse media sections
        current_media = None
        current_media_lines = []
        media_sections = []

        for line in sdp_lines:
            if line.startswith('m='):
                # Save previous media section
                if current_media:
                    media_sections.append((current_media, current_media_lines))

                # Start new media section
                # Format: m=audio 9 UDP/TLS/RTP/SAVPF 111 ...
                parts = line.split(' ')
                current_media = parts[0].split('=')[1]  # 'audio' or 'video'
                current_media_lines = [line]
            elif current_media:
                current_media_lines.append(line)

        # Save last media section
        if current_media:
            media_sections.append((current_media, current_media_lines))

        # Build Jingle content elements for each media section
        for media_type, media_lines in media_sections:
            if media_type not in media:
                continue

            # Parse mid from media section (used for content name)
            content_name = media_type  # Default to media type
            for line in media_lines:
                if line.startswith('a=mid:'):
                    content_name = line.split(':', 1)[1]
                    break

            # Parse media-specific attributes (take precedence over session-level)
            media_ice_ufrag = None
            media_ice_pwd = None
            media_dtls_fingerprint = None
            media_dtls_hash = None
            media_dtls_setup = None

            for line in media_lines:
                if line.startswith('a=ice-ufrag:'):
                    media_ice_ufrag = line.split(':', 1)[1]
                elif line.startswith('a=ice-pwd:'):
                    media_ice_pwd = line.split(':', 1)[1]
                elif line.startswith('a=fingerprint:'):
                    # Format: "a=fingerprint:sha-256 AB:CD:EF:..."
                    parts = line.split(':', 1)[1].split(' ', 1)
                    media_dtls_hash = parts[0]
                    media_dtls_fingerprint = parts[1]
                elif line.startswith('a=setup:'):
                    media_dtls_setup = line.split(':', 1)[1]

            # Use media-level if present, fallback to session-level
            ice_ufrag = media_ice_ufrag if media_ice_ufrag else global_ice_ufrag
            ice_pwd = media_ice_pwd if media_ice_pwd else global_ice_pwd
            dtls_fingerprint = media_dtls_fingerprint if media_dtls_fingerprint else global_dtls_fingerprint
            dtls_hash = media_dtls_hash if media_dtls_hash else global_dtls_hash
            dtls_setup = media_dtls_setup if media_dtls_setup else global_dtls_setup

            content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
            content.set('creator', 'initiator')
            content.set('name', content_name)
            content.set('senders', 'both')  # Explicitly set for clarity

            # Parse m= line for payload types
            # Format: m=audio 9 UDP/TLS/RTP/SAVPF 111 ...
            m_line = media_lines[0]
            m_parts = m_line.split(' ')
            payload_types = m_parts[3:]  # Everything after protocol

            # Add RTP description
            description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
            description.set('media', media_type)

            # Check if SDP has rtcp-mux (from Pion's negotiation)
            has_rtcp_mux = any(line.strip() == 'a=rtcp-mux' for line in media_lines)

            # Parse fmtp parameters (codec-specific parameters from SDP)
            # Format: a=fmtp:111 minptime=10;useinbandfec=1
            fmtp_params = {}  # {payload_type_id: {param_name: param_value}}
            for line in media_lines:
                if line.startswith('a=fmtp:'):
                    fmtp_line = line.split(':', 1)[1]  # "111 minptime=10;useinbandfec=1"
                    parts = fmtp_line.split(' ', 1)
                    pt_id = parts[0]
                    if len(parts) > 1:
                        params_str = parts[1]  # "minptime=10;useinbandfec=1"
                        params_dict = {}
                        for param in params_str.split(';'):
                            param = param.strip()
                            if '=' in param:
                                key, value = param.split('=', 1)
                                params_dict[key.strip()] = value.strip()
                        fmtp_params[pt_id] = params_dict

            # Parse codecs from rtpmap lines
            for line in media_lines:
                if line.startswith('a=rtpmap:'):
                    # Format: a=rtpmap:111 opus/48000/2
                    rtpmap = line.split(':', 1)[1]
                    pt_id, codec_info = rtpmap.split(' ', 1)

                    if pt_id in payload_types:
                        codec_parts = codec_info.split('/')
                        codec_name = codec_parts[0]
                        clockrate = codec_parts[1] if len(codec_parts) > 1 else '48000'
                        channels = codec_parts[2] if len(codec_parts) > 2 else '1'

                        payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
                        payload.set('id', pt_id)
                        payload.set('name', codec_name)
                        payload.set('clockrate', clockrate)

                        # Opus is always stereo (2 channels) - CRITICAL for Conversations.im compatibility
                        if codec_name.lower() == 'opus':
                            payload.set('channels', '2')
                        elif int(channels) > 1:
                            payload.set('channels', channels)

                        # Add codec parameters from SDP fmtp (parsed from Pion's SDP)
                        # This ensures we only echo parameters that Pion negotiated
                        if pt_id in fmtp_params:
                            for param_name, param_value in fmtp_params[pt_id].items():
                                param_elem = ET.SubElement(payload, '{urn:xmpp:jingle:apps:rtp:1}parameter')
                                param_elem.set('name', param_name)
                                param_elem.set('value', param_value)

            # Parse SSRC info from Pion's SDP (only if offer had SSRC - echo pattern)
            # IMPORTANT: Add SSRC *before* rtcp-mux to match Conversations' element ordering
            # Check if we should add SSRC (only if offer had it AND include_ssrc is True)
            # Most clients don't echo SSRC in session-accept, so we skip it for answers
            should_add_ssrc = False
            allowed_ssrc_params = []  # Which SSRC parameter names to include
            if include_ssrc and session_id and session_id in self.sessions:
                offer_details = self.sessions[session_id].get('offer_details', {})
                should_add_ssrc = offer_details.get('has_ssrc', False)
                allowed_ssrc_params = offer_details.get('ssrc_params', [])

            if should_add_ssrc:
                ssrc_info = {}  # {ssrc: {attr_name: attr_value}}
                for line in media_lines:
                    if line.startswith('a=ssrc:'):
                        # Format: a=ssrc:2485877649 cname:pion-audio
                        # Split: "a=ssrc:2485877649 cname:pion-audio" → ["a=ssrc", "2485877649 cname:pion-audio"]
                        parts = line.split(':', 1)
                        if len(parts) >= 2:
                            # parts[1] = "2485877649 cname:pion-audio"
                            rest = parts[1].strip()
                            # Split by space: "2485877649 cname:pion-audio" → ["2485877649", "cname:pion-audio"]
                            ssrc_parts = rest.split(' ', 1)
                            if len(ssrc_parts) >= 2:
                                ssrc = ssrc_parts[0]  # "2485877649"
                                # ssrc_parts[1] = "cname:pion-audio"
                                if ':' in ssrc_parts[1]:
                                    attr_name, attr_value = ssrc_parts[1].split(':', 1)
                                    if ssrc not in ssrc_info:
                                        ssrc_info[ssrc] = {}
                                    ssrc_info[ssrc][attr_name] = attr_value

                # Add source elements (if we have SSRC info)
                # IMPORTANT: Only include SSRC parameters that were in the offer (echo pattern)
                # Conversations sends: cname + msid (2 params)
                # Pion generates: cname + msid + mslabel + label (4 params)
                # We filter to match what was in the offer
                for ssrc, attrs in ssrc_info.items():
                    source_el = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:ssma:0}source')
                    source_el.set('ssrc', ssrc)

                    # Filter attributes: only include what was in the offer
                    filtered_count = 0
                    for attr_name, attr_value in attrs.items():
                        if not allowed_ssrc_params or attr_name in allowed_ssrc_params:
                            param_el = ET.SubElement(source_el, '{urn:xmpp:jingle:apps:rtp:ssma:0}parameter')
                            param_el.set('name', attr_name)
                            param_el.set('value', attr_value)
                            filtered_count += 1

                    self.logger.debug(f"Added SSRC {ssrc} with {filtered_count}/{len(attrs)} attributes to Jingle (filtered to match offer)")
                    if filtered_count < len(attrs):
                        skipped = [name for name in attrs.keys() if name not in allowed_ssrc_params]
                        self.logger.debug(f"Skipped SSRC params not in offer: {skipped}")

            # Add SDES encryption BEFORE rtcp-mux (echo from offer if present)
            if session_id and session_id in self.sessions:
                offer_details = self.sessions[session_id].get('offer_details', {})
                encryption_data = offer_details.get('encryption', {})
                if media_type in encryption_data:
                    enc = encryption_data[media_type]
                    encryption_el = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}encryption')
                    crypto_el = ET.SubElement(encryption_el, '{urn:xmpp:jingle:apps:rtp:1}crypto')
                    crypto_el.set('crypto-suite', enc['crypto-suite'])
                    crypto_el.set('key-params', enc['key-params'])
                    crypto_el.set('tag', enc['tag'])
                    self.logger.debug(f"Echoed SDES encryption for {media_type}")

            # Add rtcp-mux AFTER encryption (matches Dino's element ordering)
            # Only if Pion negotiated it in the SDP answer (RTCPMuxPolicyNegotiate)
            if has_rtcp_mux:
                ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')
                self.logger.debug(f"Added rtcp-mux to Jingle (from Pion's SDP)")

            # Add ICE-UDP transport with credentials
            transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')

            if ice_ufrag:
                transport.set('ufrag', ice_ufrag)
            if ice_pwd:
                transport.set('pwd', ice_pwd)

            # Parse ICE candidates FIRST (standard element order: candidates before fingerprint)
            for line in media_lines:
                if line.startswith('a=candidate:'):
                    # Format: a=candidate:foundation component protocol priority ip port typ type [raddr] [rport]
                    # Example: a=candidate:1 1 UDP 2130706431 192.168.1.100 54321 typ host
                    cand_str = line.split(':', 1)[1]
                    cand_parts = cand_str.split(' ')

                    if len(cand_parts) >= 8:
                        cand_el = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
                        cand_el.set('foundation', cand_parts[0])
                        cand_el.set('component', cand_parts[1])
                        cand_el.set('protocol', cand_parts[2].lower())
                        cand_el.set('priority', cand_parts[3])
                        cand_el.set('ip', cand_parts[4])
                        cand_el.set('port', cand_parts[5])
                        cand_el.set('type', cand_parts[7])  # typ is at index 6, type value at 7
                        cand_el.set('generation', '0')

                        # Optional: related address/port for reflexive/relay candidates
                        if len(cand_parts) >= 12 and cand_parts[8] == 'raddr':
                            cand_el.set('rel-addr', cand_parts[9])
                            cand_el.set('rel-port', cand_parts[11])

            # Add DTLS fingerprint AFTER candidates (standard element order)
            if dtls_fingerprint and dtls_hash:
                fingerprint_el = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
                fingerprint_el.set('hash', dtls_hash)
                if dtls_setup:
                    fingerprint_el.set('setup', dtls_setup)
                fingerprint_el.text = dtls_fingerprint

            # Add Jingle transport options for Conversations compatibility
            # XEP-0176: ICE-UDP Transport Method (Trickle ICE)
            trickle_el = ET.SubElement(transport, '{http://gultsch.de/xmpp/drafts/jingle/transports/ice-udp/option}trickle')
            renomination_el = ET.SubElement(transport, '{http://gultsch.de/xmpp/drafts/jingle/transports/ice-udp/option}renomination')

        # Echo offer features in answer (WebRTC standard behavior)
        if session_id and session_id in self.sessions:
            offer_details = self.sessions[session_id].get('offer_details')
            if offer_details:
                self._echo_offer_features(jingle, offer_details, media_sections)

        self.logger.debug(f"Converted SDP to Jingle XML (parsed {len(media_sections)} media sections)")

    def _jingle_to_sdp(self, jingle: ET.Element, sdp_type: str) -> str:
        """
        Convert Jingle XML to SDP.

        Parses Jingle and extracts:
        - Media descriptions
        - Codecs
        - ICE candidates
        - DTLS fingerprint
        - ICE credentials
        """
        sdp_lines = [
            "v=0",
            "o=- 0 0 IN IP4 0.0.0.0",
            "s=-",
            "t=0 0",
        ]

        contents = jingle.findall('{urn:xmpp:jingle:1}content')

        for content in contents:
            # Get content name (used for a=mid)
            content_name = content.get('name', 'audio')

            description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
            transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')

            if description is not None:
                media = description.get('media')

                # Parse payload types
                payload_types = description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type')
                pt_ids = []
                rtpmap_lines = []

                for pt in payload_types:
                    pt_id = pt.get('id')
                    pt_name = pt.get('name')
                    pt_clockrate = pt.get('clockrate', '48000')
                    pt_channels = pt.get('channels', '1')

                    pt_ids.append(pt_id)

                    # Build rtpmap
                    if int(pt_channels) > 1:
                        rtpmap_lines.append(f"a=rtpmap:{pt_id} {pt_name}/{pt_clockrate}/{pt_channels}")
                    else:
                        rtpmap_lines.append(f"a=rtpmap:{pt_id} {pt_name}/{pt_clockrate}")

                # Add m= line with payload types
                pt_list = ' '.join(pt_ids) if pt_ids else '111'
                sdp_lines.append(f"m={media} 9 UDP/TLS/RTP/SAVPF {pt_list}")
                sdp_lines.append("c=IN IP4 0.0.0.0")
                sdp_lines.append("a=rtcp:9 IN IP4 0.0.0.0")

                # Add mid (media ID) - CRITICAL for aiortc to match offer/answer
                sdp_lines.append(f"a=mid:{content_name}")

                # Add rtpmap lines
                if not rtpmap_lines and media == 'audio':
                    # Fallback to Opus if no codecs specified
                    sdp_lines.append("a=rtpmap:111 opus/48000/2")
                else:
                    sdp_lines.extend(rtpmap_lines)

                # CRITICAL: Do NOT add rtcp-mux to SDP even if Jingle has it
                # Conversations.im/Monocles expect BOTH component 1 and component 2 candidates
                # even when rtcp-mux is present (for backward compatibility)
                # By omitting rtcp-mux from SDP, Pion will gather both components
                # Then Pion will include rtcp-mux in the SDP answer (negotiation)
                # We'll convert that back to Jingle with <rtcp-mux /> in session-accept

                # Parse transport (ICE-UDP)
                if transport is not None:
                    # ICE credentials
                    ice_ufrag = transport.get('ufrag')
                    ice_pwd = transport.get('pwd')

                    if ice_ufrag:
                        sdp_lines.append(f"a=ice-ufrag:{ice_ufrag}")
                    if ice_pwd:
                        sdp_lines.append(f"a=ice-pwd:{ice_pwd}")

                    sdp_lines.append("a=ice-options:trickle")

                    # DTLS fingerprint
                    fingerprint_el = transport.find('{urn:xmpp:jingle:apps:dtls:0}fingerprint')
                    if fingerprint_el is not None:
                        dtls_hash = fingerprint_el.get('hash', 'sha-256')
                        dtls_setup = fingerprint_el.get('setup', 'actpass')
                        fingerprint = fingerprint_el.text

                        sdp_lines.append(f"a=setup:{dtls_setup}")
                        sdp_lines.append(f"a=fingerprint:{dtls_hash} {fingerprint}")

                    # ICE candidates
                    candidates = transport.findall('{urn:xmpp:jingle:transports:ice-udp:1}candidate')
                    for cand in candidates:
                        foundation = cand.get('foundation', '1')
                        component = cand.get('component', '1')
                        protocol = cand.get('protocol', 'udp').upper()
                        priority = cand.get('priority', '1')
                        ip = cand.get('ip', '0.0.0.0')
                        port = cand.get('port', '9')
                        cand_type = cand.get('type', 'host')

                        cand_line = f"a=candidate:{foundation} {component} {protocol} {priority} {ip} {port} typ {cand_type}"

                        # Optional: related address/port
                        rel_addr = cand.get('rel-addr')
                        rel_port = cand.get('rel-port')
                        if rel_addr and rel_port:
                            cand_line += f" raddr {rel_addr} rport {rel_port}"

                        sdp_lines.append(cand_line)

                # Media direction
                if sdp_type == 'offer':
                    sdp_lines.append("a=sendrecv")
                else:
                    sdp_lines.append("a=sendrecv")

        sdp = "\r\n".join(sdp_lines) + "\r\n"

        self.logger.debug(f"Converted Jingle XML to SDP ({sdp_type}, {len(contents)} media sections)")
        return sdp

    # =========================================================================
    # CallBridge Event Callbacks (Go → Python → Jingle)
    # =========================================================================

    async def _on_bridge_ice_candidate(self, session_id: str, candidate: Dict[str, Any]):
        """
        Handle ICE candidate from CallBridge (Go service).

        Sends the candidate to peer via Jingle transport-info.

        Args:
            session_id: Jingle session ID
            candidate: ICE candidate dict with 'candidate', 'sdpMid', 'sdpMLineIndex'
        """
        self.logger.debug(f"Received ICE candidate from Go for {session_id}")

        # Check if session exists
        if session_id not in self.sessions:
            self.logger.warning(f"Session {session_id} not found, queueing ICE candidate")
            if session_id not in self.pending_ice_candidates:
                self.pending_ice_candidates[session_id] = []
            self.pending_ice_candidates[session_id].append(candidate)
            return

        session = self.sessions[session_id]
        peer_jid = session['peer_jid']
        state = session.get('state', 'new')

        # Check session state - queue candidates until session-accept sent/received
        # Per XEP-0176: can send transport-info after session stanza exchange
        if state in ('proposing', 'proceeding', 'pending', 'incoming', 'accepted'):
            # Session-initiate not sent yet OR waiting for session-accept
            # For incoming calls: 'incoming' = just received session-initiate, 'accepted' = we accepted but haven't sent session-accept yet
            # Queue candidate to send later (avoids "No module is handling this query" from peer)
            if session_id not in self.pending_ice_candidates:
                self.pending_ice_candidates[session_id] = []
            self.pending_ice_candidates[session_id].append(candidate)
            queue_size = len(self.pending_ice_candidates[session_id])
            self.logger.debug(f"Queued ICE candidate for {session_id} (state={state}, queue_size={queue_size})")
            return

        # State is 'active' or later - safe to send transport-info immediately (Trickle ICE)
        # Parse ICE candidate string
        candidate_str = candidate.get('candidate', '')
        sdp_mid = candidate.get('sdpMid', 'audio')  # Default to audio
        sdp_mline_index = candidate.get('sdpMLineIndex', 0)

        # Convert SDP candidate string to Jingle XML format
        # Example: "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"
        parts = candidate_str.split()
        if len(parts) < 8:
            self.logger.warning(f"Invalid ICE candidate format: {candidate_str}")
            return

        try:
            foundation = parts[0].split(':')[1]  # Remove "candidate:" prefix
            component = parts[1]
            protocol = parts[2]
            priority = parts[3]
            ip = parts[4]
            port = parts[5]
            # parts[6] is "typ"
            cand_type = parts[7]

            # Build Jingle transport-info IQ
            iq = self.xmpp.make_iq_set(ito=peer_jid)
            # Access underlying XML element (slixmpp Iq stanza)
            jingle = ET.SubElement(iq.xml, '{urn:xmpp:jingle:1}jingle')
            jingle.set('action', 'transport-info')
            jingle.set('sid', session_id)

            # Add content element
            content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
            content.set('creator', session.get('creator', 'initiator'))
            content.set('name', sdp_mid)

            # Add transport element with candidate
            transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
            candidate_elem = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
            candidate_elem.set('component', component)
            candidate_elem.set('foundation', foundation)
            candidate_elem.set('generation', '0')
            candidate_elem.set('id', f"cand-{foundation}")
            candidate_elem.set('ip', ip)
            candidate_elem.set('network', '0')
            candidate_elem.set('port', port)
            candidate_elem.set('priority', priority)
            candidate_elem.set('protocol', protocol)
            candidate_elem.set('type', cand_type)

            # Send transport-info
            iq.send()
            self.logger.debug(f"Sent ICE candidate transport-info to {peer_jid}")

        except Exception as e:
            self.logger.error(f"Error sending ICE candidate: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

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
