"""
Message Extensions module for DrunkXMPP.

XEP-0308: Last Message Correction (message editing)
XEP-0428: Message Fallback Indication
XEP-0444: Message Reactions
XEP-0461: Message Replies

Provides methods for replying to messages, reacting to messages, and editing messages.
"""

from typing import Optional, Set
from slixmpp.jid import JID
from . import xep_0428


class MessageExtensionsMixin:
    """
    Mixin providing message extension features.

    Requirements (provided by DrunkXMPP):
    - self['xep_0308']: Last Message Correction plugin
    - self['xep_0380']: Explicit Message Encryption plugin
    - self['xep_0444']: Message Reactions plugin
    - self['xep_0461']: Message Replies plugin
    - self.plugin: Dict of loaded slixmpp plugins
    - self.rooms: Dict of joined rooms
    - self.omemo_enabled: Boolean indicating if OMEMO is enabled
    - self.omemo_ready: Boolean indicating if OMEMO is ready
    - self.make_message(): Method to create message stanzas
    - self._get_message_type(): Method to determine message type (groupchat/chat)
    - self.logger: Logger instance
    """

    async def send_reply(self, to_jid: str, reply_to_id: str, reply_body: str,
                        fallback_body: Optional[str] = None, encrypt: bool = False):
        """
        Send a reply to a message using XEP-0461.

        Args:
            to_jid: Recipient JID
            reply_to_id: ID of message being replied to
            reply_body: Your reply text
            fallback_body: Optional original message text for fallback (adds "> original\nreply" for dumb clients)
            encrypt: Whether to use OMEMO encryption

        Returns:
            str: Message ID (origin-id) for tracking

        Raises:
            RuntimeError: If OMEMO encryption requested but not ready
        """
        self.logger.debug(f"Sending reply to {to_jid} (reply_to={reply_to_id})")

        # Determine message type (MUC or private chat)
        msg_type = self._get_message_type(to_jid)

        # Create base message stanza
        msg = self.make_message(mto=to_jid, mtype=msg_type)

        # Add XEP-0461 reply element
        msg['reply']['to'] = to_jid
        msg['reply']['id'] = reply_to_id

        # Format body using our custom function (single source of truth)
        # This ensures consistency between sending and optimistic storage
        if fallback_body:
            # Use format_reply_with_fallback as the canonical formatter
            # This handles nested quotes correctly: ">> " not "> > "
            full_body, fallback_marker = xep_0428.format_reply_with_fallback(reply_body, fallback_body)
            msg['body'] = full_body

            # Add XEP-0428 fallback marker
            xep_0428.add_fallback_to_stanza(msg, fallback_marker)

            self.logger.debug(f"Added fallback marker: chars 0-{fallback_marker.to_char}")
        else:
            # No fallback - just the reply body
            msg['body'] = reply_body

        # Request delivery receipt (XEP-0184)
        msg['request_receipt'] = True

        # Enable message archiving (XEP-0334 hint: store)
        msg.enable('store')

        # Handle OMEMO encryption if requested
        if encrypt:
            if not self.omemo_enabled:
                raise RuntimeError("OMEMO is not enabled")
            if not self.omemo_ready:
                raise RuntimeError("OMEMO is not ready")

            try:
                xep_0384 = self.plugin['xep_0384']

                # Get recipients for encryption
                recipient_jids: Set[JID] = set()
                if msg_type == 'groupchat':
                    # MUC: encrypt for all participants
                    xep_0045 = self.plugin['xep_0045']
                    room_jid_obj = JID(to_jid)
                    participants = xep_0045.get_roster(to_jid)

                    for nick in participants:
                        real_jid_str = xep_0045.get_jid_property(room_jid_obj, nick, 'jid')
                        if real_jid_str:
                            recipient_jids.add(JID(real_jid_str))

                    if not recipient_jids:
                        raise RuntimeError(f"No recipients found in {to_jid}")

                    self.logger.debug(f"Encrypting reply for {len(recipient_jids)} MUC participants")
                else:
                    # Private chat: single recipient
                    recipient_jids.add(JID(to_jid))

                # Refresh device lists
                session_manager = await xep_0384.get_session_manager()
                for recipient_jid in recipient_jids:
                    await session_manager.refresh_device_lists(recipient_jid.bare)

                # Encrypt the message
                # slixmpp-omemo creates NEW message stanzas with NEW IDs (via stream.new_id())
                # The original message ID is NOT preserved to avoid duplicate ID usage
                messages, encryption_errors = await xep_0384.encrypt_message(msg, recipient_jids)

                if encryption_errors:
                    self.logger.warning(f"Encryption errors: {encryption_errors}")

                if not messages:
                    raise RuntimeError("Encryption produced no messages")

                # Send encrypted versions and capture last message ID
                last_msg_id = None
                for namespace, encrypted_msg in messages.items():
                    encrypted_msg['eme']['namespace'] = namespace
                    encrypted_msg['eme']['name'] = self['xep_0380'].mechanisms.get(namespace, 'OMEMO')

                    # Add origin-id for message tracking (XEP-0359)
                    # MUST be set on the encrypted message, not the original!
                    encrypted_msg['origin_id']['id'] = encrypted_msg['id']

                    # Re-add receipt request and markable to encrypted message (XEP-0184, XEP-0333)
                    encrypted_msg['request_receipt'] = True
                    encrypted_msg['markable'] = True

                    encrypted_msg.send()
                    last_msg_id = encrypted_msg['id']
                    self.logger.info(f"OMEMO-encrypted reply sent to {to_jid} (namespace: {namespace}, id: {last_msg_id})")

                message_id = last_msg_id
            except Exception as e:
                self.logger.exception(f"Failed to encrypt reply: {e}")
                raise
        else:
            # Send plaintext - add origin-id here
            msg['origin_id']['id'] = msg['id']
            message_id = msg['id']
            msg.send()
            self.logger.info(f"Reply sent to {to_jid}")

        # Track seq number and request server ACK (XEP-0198)
        if message_id and hasattr(self.plugin['xep_0198'], 'seq'):
            self.pending_server_acks[message_id] = self.plugin['xep_0198'].seq
            self.logger.debug(f"Tracking reply {message_id} with seq {self.plugin['xep_0198'].seq}")
            self.plugin['xep_0198'].request_ack()

        return message_id

    def send_reaction(self, to_jid: str, message_id: str, emoji: str):
        """
        Send a reaction to a message using XEP-0444.

        Args:
            to_jid: Recipient JID
            message_id: ID of message to react to
            emoji: Emoji reaction (e.g., '❤️', '👍', '😂')
        """
        self.logger.debug(f"Sending reaction {emoji} to {to_jid} (msg: {message_id})")

        # Determine message type (MUC or private chat)
        msg_type = self._get_message_type(to_jid)

        # Build message with correct type and add reactions using slixmpp's helper
        msg = self.make_message(mto=to_jid, mtype=msg_type)
        self['xep_0444'].set_reactions(msg, message_id, [emoji])
        msg.enable('store')
        msg.send()

        self.logger.info(f"Reaction {emoji} sent to {to_jid}")

    def remove_reaction(self, to_jid: str, message_id: str):
        """
        Remove all reactions from a message using XEP-0444.

        Args:
            to_jid: Recipient JID
            message_id: ID of message to remove reactions from
        """
        self.logger.debug(f"Removing reactions from {to_jid} (msg: {message_id})")

        # Determine message type (MUC or private chat)
        msg_type = self._get_message_type(to_jid)

        # Build message with correct type and remove reactions using slixmpp's helper
        msg = self.make_message(mto=to_jid, mtype=msg_type)
        self['xep_0444'].set_reactions(msg, message_id, [])
        msg.enable('store')
        msg.send()

        self.logger.info(f"Reactions removed from {to_jid}")

    async def edit_message(self, jid: str, message_id: str, new_body: str, encrypt: bool = False):
        """
        Edit a message by ID using XEP-0308 (Last Message Correction).

        Most XMPP clients only support editing the last message and will return
        errors if you try to edit older messages. The caller should track which
        message was last sent.

        Args:
            jid: Recipient JID (can be user JID or room JID)
            message_id: ID of the message to edit
            new_body: New message text to replace the old one
            encrypt: Whether to encrypt the correction with OMEMO (should match original)

        Raises:
            RuntimeError: If OMEMO encryption requested but not ready
        """
        # Determine message type (check if it's a MUC)
        msg_type = self._get_message_type(jid)

        self.logger.debug(f"Editing message {message_id} to {jid} (encrypted: {encrypt})")

        if not encrypt:
            # Send plaintext correction
            # Compatibility: build_correction() added in slixmpp 1.9.0
            if hasattr(self['xep_0308'], 'build_correction'):
                correction = self['xep_0308'].build_correction(
                    id_to_replace=message_id,
                    mto=jid,
                    mtype=msg_type,
                    mbody=new_body
                )
            else:
                # Fallback for slixmpp < 1.9.0 (manual stanza building)
                correction = self.make_message(mto=jid, mtype=msg_type)
                correction['body'] = new_body
                correction['replace']['id'] = message_id

            # Add origin-id for message tracking (XEP-0359)
            correction['origin_id']['id'] = correction['id']
            correction.send()
            self.logger.info(f"Message correction sent to {jid} (replaced: {message_id})")
        else:
            # Send OMEMO-encrypted correction
            if not self.omemo_enabled:
                raise RuntimeError("OMEMO is not enabled")
            if not self.omemo_ready:
                raise RuntimeError("OMEMO is not ready")

            xep_0384 = self.plugin['xep_0384']

            # Build stanza for encryption (without replace element yet)
            stanza = self.make_message(mto=jid, mtype=msg_type)
            stanza['body'] = new_body

            # Get recipients for encryption
            recipient_jids: Set[JID] = set()
            if msg_type == 'groupchat':
                # MUC: encrypt for all participants
                xep_0045 = self.plugin['xep_0045']
                room_jid_obj = JID(jid)
                participants = xep_0045.get_roster(jid)

                for nick in participants:
                    real_jid_str = xep_0045.get_jid_property(room_jid_obj, nick, 'jid')
                    if real_jid_str:
                        recipient_jids.add(JID(real_jid_str))

                if not recipient_jids:
                    raise RuntimeError(f"No recipients found in {jid}")

                self.logger.debug(f"Encrypting correction for {len(recipient_jids)} participants")
            else:
                # Private message
                recipient_jids.add(JID(jid))

            # Refresh device lists
            session_manager = await xep_0384.get_session_manager()
            for recipient_jid in recipient_jids:
                await session_manager.refresh_device_lists(recipient_jid.bare)

            # Encrypt the correction
            try:
                messages, encryption_errors = await xep_0384.encrypt_message(stanza, recipient_jids)

                if encryption_errors:
                    self.logger.warning(f"Encryption errors: {encryption_errors}")

                if not messages:
                    raise RuntimeError("Encryption produced no messages")

                # Send all encrypted versions with replace element added
                for namespace, encrypted_msg in messages.items():
                    encrypted_msg['eme']['namespace'] = namespace
                    encrypted_msg['eme']['name'] = self['xep_0380'].mechanisms.get(namespace, 'OMEMO')
                    # Add origin-id for message tracking (XEP-0359)
                    encrypted_msg['origin_id']['id'] = encrypted_msg['id']
                    # Add replace element to encrypted message (must be outside <encrypted>)
                    encrypted_msg['replace']['id'] = message_id
                    encrypted_msg.send()
                    self.logger.info(f"OMEMO-encrypted correction sent to {jid} (namespace: {namespace}, replaced: {message_id})")

            except Exception as e:
                self.logger.exception(f"Failed to encrypt correction: {e}")
                raise

