"""
XEP-0353: Jingle Message Initiation (Call Notifications)

This mixin handles the pre-call signaling messages (propose/proceed/accept/reject/retract).
Actual Jingle session negotiation is handled by jingle.py module.
"""

import logging
import uuid
from typing import Optional, Dict, Any, Callable
from slixmpp.jid import JID
from slixmpp.stanza import Iq, Message
from slixmpp.xmlstream import ET


class CallsMixin:
    """
    Mixin providing XEP-0353 Jingle Message Initiation support.

    Requirements (provided by DrunkXMPP):
    - self.plugin: Dict of loaded slixmpp plugins
    - self.make_message(): Method to create message stanzas
    - self.logger: Logger instance
    - self.add_event_handler(): Method to register event handlers
    - self.boundjid: Our JID
    """

    def _setup_call_handlers(self):
        """
        Register XEP-0353 event handlers.
        Called from DrunkXMPP._on_session_start.
        """
        # Register XEP-0353 Jingle Message Initiation handlers
        self.add_event_handler('jingle_message_propose', self._on_jingle_message_propose)
        self.add_event_handler('jingle_message_proceed', self._on_jingle_message_proceed)
        self.add_event_handler('jingle_message_accept', self._on_jingle_message_accept)
        self.add_event_handler('jingle_message_reject', self._on_jingle_message_reject)
        self.add_event_handler('jingle_message_retract', self._on_jingle_message_retract)
        self.add_event_handler('jingle_message_finish', self._on_jingle_message_finish)

        self.logger.debug("XEP-0353 call handlers registered")

    # ============================================================================
    # XEP-0353: Incoming Message Handlers
    # ============================================================================

    async def _on_jingle_message_propose(self, msg: Message):
        """
        Handle incoming <propose> message (XEP-0353).

        This is sent when someone wants to call us.
        Flow: Caller sends <propose> → We show incoming call dialog
        """
        # Skip historical/resent messages (MAM replay, server resends)
        # XEP-0353 §7.3: "client developers MAY choose to not show an 'incoming call' UI"
        # during MAM catchup to avoid ghost notifications for already-resolved calls
        if msg['delay']['stamp']:
            delay_from = msg['delay'].get('from', 'unknown')
            delay_stamp = msg['delay']['stamp']
            self.logger.info(
                f"Discarding historical call propose from {msg['from']} "
                f"(delayed by {delay_from} at {delay_stamp}, likely MAM replay or server resend)"
            )
            return

        sid = msg['jingle_propose']['id']
        peer_jid = str(msg['from'])

        # Receipt (XEP-0184) is optional and causes Python 3.11 asyncio issues
        # when sent from inside event handler. Phone works fine without it.
        # The important message is <proceed> which we send from GUI action (no asyncio issue)
        if msg['request_receipt']:
            self.logger.debug(f"Receipt requested for {msg['id']} but skipped (sent from event handler)")

        # Extract media types from description elements
        # get_descriptions() returns list of tuples: [(namespace, media), ...]
        # Example: [('urn:xmpp:jingle:apps:rtp:1', 'audio'), ('urn:xmpp:jingle:apps:rtp:1', 'video')]
        propose = msg['jingle_propose']
        descriptions = propose.get_descriptions()
        media_types = [media for namespace, media in descriptions]

        if not media_types:
            media_types = ['audio']  # Default to audio if no description

        self.logger.info(f"Received call propose from {peer_jid}: {media_types}, sid={sid}")

        # Store session
        self.call_sessions[sid] = {
            'peer_jid': peer_jid,
            'media': media_types,
            'state': 'proposed',
            'direction': 'incoming'
        }

        # Notify AccountManager via callback
        if self.on_call_incoming:
            try:
                await self.on_call_incoming(peer_jid, sid, media_types)
            except Exception as e:
                self.logger.error(f"Error in on_call_incoming callback: {e}", exc_info=True)

    async def _on_jingle_message_proceed(self, msg: Message):
        """
        Handle incoming <proceed> message (XEP-0353).

        This is sent when the callee accepts our call proposal.
        Flow: We sent <propose> → Callee sends <proceed> → We send session-initiate
        """
        # Skip historical/resent messages (MAM replay, server resends)
        if msg['delay']['stamp']:
            delay_from = msg['delay'].get('from', 'unknown')
            delay_stamp = msg['delay']['stamp']
            self.logger.info(
                f"Discarding historical call proceed from {msg['from']} "
                f"(delayed by {delay_from} at {delay_stamp}, likely MAM replay or server resend)"
            )
            return

        try:
            sid = msg['jingle_proceed']['id']
            peer_jid = str(msg['from'])

            self.logger.info(f"Received proceed from {peer_jid} for session {sid}")

            if sid not in self.call_sessions:
                self.logger.warning(f"Received proceed for unknown session {sid}")
                return

            session = self.call_sessions[sid]
            session['state'] = 'proceeding'

            # Notify AccountManager to send session-initiate
            # (This will be handled by jingle.py for actual SDP negotiation)
            if self.on_call_accepted:
                try:
                    await self.on_call_accepted(sid, peer_jid)
                except Exception as e:
                    self.logger.error(f"Error in on_call_accepted callback: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Error in _on_jingle_message_proceed handler: {e}", exc_info=True)

    async def _on_jingle_message_accept(self, msg: Message):
        """
        Handle incoming <accept> message (XEP-0353).

        This can mean two things:
        1. Peer accepted our outgoing call (DTLS handshake complete)
        2. Another device of OURS accepted incoming call (dismiss incoming call UI)
        """
        # Skip historical/resent messages (MAM replay, server resends)
        if msg['delay']['stamp']:
            delay_from = msg['delay'].get('from', 'unknown')
            delay_stamp = msg['delay']['stamp']
            self.logger.info(
                f"Discarding historical call accept from {msg['from']} "
                f"(delayed by {delay_from} at {delay_stamp}, likely MAM replay or server resend)"
            )
            return

        sid = msg['jingle_accept']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Received accept from {peer_jid} for session {sid}")

        # Check if this is our own device accepting (multi-device scenario)
        # When another device accepts, it sends <accept> to bare JID (all our devices)
        from_bare = JID(peer_jid).bare
        our_bare = self.boundjid.bare if self.boundjid else None

        if from_bare == our_bare and sid in self.call_sessions:
            # Another device of ours accepted the call - dismiss incoming call UI
            self.logger.info(f"Call {sid} answered on another device ({peer_jid}), dismissing incoming call")

            # Clean up session
            del self.call_sessions[sid]

            # Notify AccountManager to dismiss incoming call dialog/notification
            if self.on_call_terminated:
                try:
                    await self.on_call_terminated(sid, 'answered_elsewhere')
                except Exception as e:
                    self.logger.error(f"Error in on_call_terminated callback: {e}", exc_info=True)
        elif sid in self.call_sessions:
            # Normal accept from peer (outgoing call accepted)
            self.call_sessions[sid]['state'] = 'accepted'

    async def _on_jingle_message_reject(self, msg: Message):
        """
        Handle incoming <reject> message (XEP-0353).

        This can mean two things:
        1. Peer rejected our outgoing call
        2. Another device of OURS rejected incoming call (dismiss incoming call UI)
        """
        # Skip historical/resent messages (MAM replay, server resends)
        if msg['delay']['stamp']:
            delay_from = msg['delay'].get('from', 'unknown')
            delay_stamp = msg['delay']['stamp']
            self.logger.info(
                f"Discarding historical call reject from {msg['from']} "
                f"(delayed by {delay_from} at {delay_stamp}, likely MAM replay or server resend)"
            )
            return

        sid = msg['jingle_reject']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Call rejected by {peer_jid} (sid={sid})")

        # Check if this is our own device rejecting (multi-device scenario)
        from_bare = JID(peer_jid).bare
        our_bare = self.boundjid.bare if self.boundjid else None

        if sid in self.call_sessions:
            del self.call_sessions[sid]

        # Notify AccountManager
        if self.on_call_terminated:
            try:
                # If another device of ours rejected, use 'rejected_elsewhere'
                reason = 'rejected_elsewhere' if from_bare == our_bare else 'rejected'
                await self.on_call_terminated(sid, reason)
            except Exception as e:
                self.logger.error(f"Error in on_call_terminated callback: {e}", exc_info=True)

    async def _on_jingle_message_retract(self, msg: Message):
        """
        Handle incoming <retract> message (XEP-0353).

        This is sent when the caller cancels their call proposal.
        """
        # Skip historical/resent messages (MAM replay, server resends)
        if msg['delay']['stamp']:
            delay_from = msg['delay'].get('from', 'unknown')
            delay_stamp = msg['delay']['stamp']
            self.logger.info(
                f"Discarding historical call retract from {msg['from']} "
                f"(delayed by {delay_from} at {delay_stamp}, likely MAM replay or server resend)"
            )
            return

        sid = msg['jingle_retract']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Call retracted by {peer_jid} (sid={sid})")

        if sid in self.call_sessions:
            del self.call_sessions[sid]

        # Notify AccountManager
        if self.on_call_terminated:
            try:
                await self.on_call_terminated(sid, 'retracted')
            except Exception as e:
                self.logger.error(f"Error in on_call_terminated callback: {e}", exc_info=True)

    async def _on_jingle_message_finish(self, msg: Message):
        """
        Handle incoming <finish> message (XEP-0353).

        This is sent when the call ends (either party hangs up, or call answered elsewhere).
        Important for multi-device scenarios: when you answer on phone, phone sends <finish>
        to notify desktop the call is over.
        """
        # Skip historical/resent messages (MAM replay, server resends)
        if msg['delay']['stamp']:
            delay_from = msg['delay'].get('from', 'unknown')
            delay_stamp = msg['delay']['stamp']
            self.logger.info(
                f"Discarding historical call finish from {msg['from']} "
                f"(delayed by {delay_from} at {delay_stamp}, likely MAM replay or server resend)"
            )
            return

        sid = msg['jingle_finish']['id']
        peer_jid = str(msg['from'])

        self.logger.info(f"Call finished by {peer_jid} (sid={sid})")

        # Clean up session if it exists
        if sid in self.call_sessions:
            del self.call_sessions[sid]

        # Notify AccountManager (call ended elsewhere or peer hung up)
        if self.on_call_terminated:
            try:
                await self.on_call_terminated(sid, 'finished')
            except Exception as e:
                self.logger.error(f"Error in on_call_terminated callback: {e}", exc_info=True)

    # ============================================================================
    # XEP-0353: Sending Jingle Messages
    # ============================================================================

    def send_call_propose(self, peer_jid: str, media_types: list) -> str:
        """
        Send <propose> message to initiate a call (XEP-0353).

        Args:
            peer_jid: JID to call
            media_types: List of media types (e.g., ['audio'] or ['audio', 'video'])

        Returns:
            session_id: Generated session ID for this call
        """
        session_id = str(uuid.uuid4())
        peer_jid_bare = JID(peer_jid).bare

        self.logger.info(f"Sending call propose to {peer_jid_bare}: {media_types}, sid={session_id}")

        # Use slixmpp XEP-0353 plugin to send propose
        # descriptions format: List of tuples (namespace, media_type)
        # Example: [('urn:xmpp:jingle:apps:rtp:1', 'audio')]
        descriptions = [('urn:xmpp:jingle:apps:rtp:1', media) for media in media_types]
        self.plugin['xep_0353'].propose(
            mto=peer_jid_bare,
            sid=session_id,
            descriptions=descriptions
        )

        # Store session
        self.call_sessions[session_id] = {
            'peer_jid': peer_jid,
            'media': media_types,
            'state': 'proposing',
            'direction': 'outgoing'
        }

        return session_id

    def send_call_proceed(self, session_id: str):
        """
        Send <proceed> message to accept an incoming call proposal (XEP-0353).

        Args:
            session_id: Session ID from the <propose> message
        """
        if session_id not in self.call_sessions:
            raise ValueError(f"Unknown session: {session_id}")

        session = self.call_sessions[session_id]
        peer_jid = session['peer_jid']  # Full JID with resource (e.g., user@server/resource)

        self.logger.info(f"Sending proceed to {peer_jid} for session {session_id}")

        # CRITICAL: Send to FULL JID (with resource), not bare JID
        # The caller's specific resource sent the propose, so proceed must go back to that resource
        msg = self.make_message(mto=peer_jid, mtype='chat')
        msg['jingle_proceed']['id'] = session_id
        # Request receipt (same as Conversations does in propose)
        msg['request_receipt'] = True
        # Add store hint (XEP-0334) for proper archiving/carbons
        from xml.etree import ElementTree as ET
        store_hint = ET.Element('{urn:xmpp:hints}store')
        msg.xml.append(store_hint)
        msg.send()

        session['state'] = 'proceeding'
        self.logger.info(f"Sent XEP-0353 proceed for {session_id}")

    def send_call_accept(self, session_id: str):
        """
        Send <accept> message when call is fully connected (XEP-0353).

        This should be sent when DTLS handshake completes, not when session-accept is received.

        Args:
            session_id: Session ID
        """
        if session_id not in self.call_sessions:
            self.logger.warning(f"send_call_accept: Unknown session {session_id}")
            return

        session = self.call_sessions[session_id]
        peer_jid = session['peer_jid']
        peer_jid_bare = JID(peer_jid).bare

        self.logger.info(f"Sending accept to {peer_jid_bare} for session {session_id}")

        # Use slixmpp XEP-0353 plugin
        self.plugin['xep_0353'].accept(mto=peer_jid_bare, sid=session_id)

        session['state'] = 'accepted'

    def send_call_reject(self, session_id: str):
        """
        Send <reject> message to reject an incoming call proposal (XEP-0353).

        Args:
            session_id: Session ID from the <propose> message
        """
        if session_id not in self.call_sessions:
            self.logger.warning(f"send_call_reject: Unknown session {session_id}")
            return

        session = self.call_sessions[session_id]
        peer_jid = session['peer_jid']  # Full JID with resource

        self.logger.info(f"Sending reject to {peer_jid} for session {session_id}")

        # Send to full JID with type="chat" so server will carbon it to our other devices
        # This allows phone/tablet to dismiss "calling..." state when desktop rejects
        msg = self.make_message(mto=peer_jid, mtype='chat')
        msg['jingle_reject']['id'] = session_id
        msg.send()

        del self.call_sessions[session_id]

    def send_call_retract(self, session_id: str):
        """
        Send <retract> message to cancel an outgoing call proposal (XEP-0353).

        Args:
            session_id: Session ID
        """
        if session_id not in self.call_sessions:
            self.logger.warning(f"send_call_retract: Unknown session {session_id}")
            return

        session = self.call_sessions[session_id]
        peer_jid = session['peer_jid']
        peer_jid_bare = JID(peer_jid).bare

        self.logger.info(f"Sending retract to {peer_jid_bare} for session {session_id}")

        # Use slixmpp XEP-0353 plugin
        self.plugin['xep_0353'].retract(mto=peer_jid_bare, sid=session_id)

        del self.call_sessions[session_id]
