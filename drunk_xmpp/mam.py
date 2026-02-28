"""
Message Archive Management (MAM) module for DrunkXMPP.

XEP-0313: Message Archive Management

Provides methods for retrieving message history from the server archive.
"""

from typing import List, Dict, Optional
from datetime import datetime
from slixmpp.jid import JID
from slixmpp.exceptions import IqError, IqTimeout


class MAMMixin:
    """
    Mixin providing Message Archive Management functionality.

    Requirements (provided by DrunkXMPP):
    - self.plugin: Dict of loaded slixmpp plugins
    - self.rooms: Dict of joined rooms
    - self.boundjid: Current bound JID
    - self.omemo_enabled: Boolean indicating if OMEMO is enabled
    - self.logger: Logger instance
    """

    async def retrieve_history(
        self,
        jid: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        max_messages: Optional[int] = None,
        with_jid: Optional[str] = None,
        start_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Retrieve message history from server using MAM (XEP-0313).

        For MUC rooms: Queries the room's archive (room must support MAM).
        For 1-to-1 chats: Queries your account's archive.

        Args:
            jid: Room JID (for MUC history) or user JID (for 1-to-1 chat history)
            start: Optional start datetime for history range
            end: Optional end datetime for history range
            max_messages: Maximum number of messages to retrieve (None = unlimited)
            with_jid: Optional filter - only messages with this JID (for 1-to-1 archive queries)
            start_id: Optional MAM archive ID to start after (for efficient catchup)

        Returns:
            List of dicts with keys:
                - 'jid': Sender JID (bare)
                - 'nick': Nickname (for MUC messages) or None
                - 'body': Message body (decrypted if OMEMO)
                - 'timestamp': Message timestamp (datetime)
                - 'is_encrypted': Whether message was OMEMO encrypted
                - 'archive_id': MAM archive result ID (becomes server_id in storage)
                - 'message': Original message stanza

        Raises:
            RuntimeError: If MAM is not supported by the server/room
        """
        from datetime import datetime as dt_class

        max_str = str(max_messages) if max_messages else "unlimited"
        self.logger.info(f"Retrieving MAM history from {jid} (max: {max_str})")

        # Determine if this is a MUC room or 1-to-1 chat
        is_muc = jid in self.rooms
        query_jid = JID(jid) if is_muc else None  # For MUC, query the room; for 1-to-1, query our own server

        xep_0313 = self.plugin['xep_0313']
        history = []

        try:
            # Build RSM parameters
            rsm_params = {'max': 300}  # Page size: 300 messages per request (good for desktop)
            if start_id:
                rsm_params['after'] = start_id  # Start after this archive ID (efficient catchup)

            # Use iterate() for automatic pagination
            # rsm={'max': N} sets page size (server returns up to N messages per page)
            # total=M sets total message limit across all pages (None = unlimited)
            async for result_msg in xep_0313.iterate(
                jid=query_jid,
                start=start,
                end=end,
                with_jid=JID(with_jid) if with_jid else None,
                rsm=rsm_params,
                total=max_messages  # Total limit (None = unlimited, no artificial cap)
            ):
                # Extract the forwarded message from MAM result
                # Structure: result_msg['mam_result']['forwarded']['stanza']
                mam_result = result_msg['mam_result']
                forwarded = mam_result['forwarded']

                if forwarded is None:
                    self.logger.warning("MAM result had no forwarded stanza")
                    continue

                archived_msg = forwarded['stanza']
                if archived_msg is None:
                    self.logger.warning("Forwarded stanza was empty")
                    continue

                # Get timestamp from delay element
                delay = forwarded['delay']
                timestamp = delay['stamp'] if delay else None

                # Extract sender info
                from_jid = archived_msg['from']

                # For MUC messages, extract nick from resource
                nick = from_jid.resource if is_muc else None
                sender_bare = from_jid.bare

                # Get message body
                body = archived_msg['body']

                # Skip message corrections in MAM archive (XEP-0308)
                # Corrections are processed live via _on_message_correction handler.
                # If we process them again from MAM, we'd create duplicate "[Failed to decrypt...]"
                # messages for corrections we couldn't decrypt.
                if archived_msg['replace']['id']:
                    self.logger.debug(f"Skipping MAM correction (replace id: {archived_msg['replace']['id']}) from {from_jid}")
                    continue

                # Check if message is OMEMO encrypted and decrypt if needed
                is_encrypted = False
                if self.omemo_enabled and body:
                    xep_0384 = self.plugin['xep_0384']
                    if xep_0384.is_encrypted(archived_msg):
                        is_encrypted = True
                        try:
                            # Decrypt the message
                            decrypted_msg, device_info = await xep_0384.decrypt_message(archived_msg)
                            body = decrypted_msg['body']
                            self.logger.debug(f"Decrypted MAM message from {from_jid} (device {device_info.device_id})")
                        except Exception as e:
                            self.logger.error(f"Failed to decrypt MAM OMEMO message from {from_jid}: {e}")
                            body = "[Failed to decrypt OMEMO message]"

                            # Filter out MUC reflections from MAM archive:
                            # When we send an encrypted MUC message, it gets archived and reflected back.
                            # We can't decrypt our own messages (not encrypted for ourselves).
                            #
                            # MAM reflections have one of these characteristics:
                            # 1. No sender nickname (empty resource) - server strips it in archive
                            # 2. Sender nickname matches ours - server includes it
                            #
                            # Since we already have the sent version (direction=1), skip these to
                            # avoid duplicates showing "[Failed to decrypt...]"
                            if is_muc:
                                # Extract occupant-id for reliable detection
                                msg_occupant_id = archived_msg['occupant-id']['id'] or None  # Empty string -> None

                                # Check if reflection using occupant-id (most reliable) or nick fallback
                                is_reflection = False
                                if msg_occupant_id and jid in self.own_occupant_ids:
                                    is_reflection = (msg_occupant_id == self.own_occupant_ids[jid])
                                else:
                                    # Fallback: Use self.rooms to get our nickname (more reliable than our_nicks during MAM sync)
                                    our_nick = self.rooms[jid].get('nick') if jid in self.rooms else None
                                    is_reflection = (not nick or (our_nick and nick == our_nick))

                                if is_reflection:
                                    self.logger.debug(f"Skipping MAM reflection (own encrypted message) in {jid}")
                                    continue
                            else:
                                # Filter out 1-1 carbons with failed decryption (same principle as MUC reflections)
                                # When we send an encrypted 1-1 message, server sends it back as carbon_sent.
                                # We can't decrypt our own carbons (not encrypted for the sending device).
                                # Since we already have the sent version (direction=1), skip these to
                                # avoid duplicates showing "[Failed to decrypt...]"
                                if sender_bare == self.boundjid.bare:
                                    self.logger.debug(f"Skipping MAM carbon (own encrypted message) from {from_jid}")
                                    continue

                # Skip empty messages
                if not body:
                    continue

                # Get MAM archive result ID (this becomes server_id when stored)
                archive_id = mam_result.get('id', archived_msg.get('id'))

                # Extract occupant-id if available (XEP-0421)
                occupant_id = archived_msg['occupant-id']['id'] or None  # Empty string -> None

                history.append({
                    'jid': sender_bare,
                    'nick': nick,
                    'body': body,
                    'timestamp': timestamp,
                    'is_encrypted': is_encrypted,
                    'archive_id': archive_id,
                    'occupant_id': occupant_id,
                    'message': archived_msg
                })

                self.logger.debug(f"Retrieved MAM message from {from_jid}: {body[:50] if body else '(no body)'}... (encrypted: {is_encrypted})")

            self.logger.info(f"Retrieved {len(history)} messages from MAM archive")
            return history

        except IqError as e:
            error_condition = e.iq['error']['condition']
            error_text = e.iq['error']['text'] if e.iq['error']['text'] else 'Unknown error'
            self.logger.error(f"MAM query failed: {error_condition} - {error_text}")

            if error_condition == 'feature-not-implemented':
                raise RuntimeError(f"MAM not supported by {jid}")
            raise RuntimeError(f"MAM query failed: {error_condition} - {error_text}")
        except IqTimeout:
            self.logger.error("MAM query timeout")
            raise RuntimeError("MAM query timeout")
        except Exception as e:
            self.logger.exception(f"Failed to retrieve MAM history: {e}")
            raise

    async def check_mam_support(self, jid: str) -> bool:
        """
        Check if a JID supports Message Archive Management (MAM).

        For MUC rooms: Checks if the room supports MAM.
        For 1-to-1 chats: Checks if the user's server supports MAM.

        Args:
            jid: Room JID (for MUC) or user JID (for 1-to-1 chat)

        Returns:
            True if MAM is supported, False otherwise

        Raises:
            IqError: If service discovery fails
            IqTimeout: If service discovery times out
        """
        self.logger.debug(f"Checking MAM support for {jid}")

        # Determine if this is a MUC room or 1-to-1 chat
        is_muc = jid in self.rooms

        # For MUC: query the room directly
        # For 1-to-1: query the user's server (our own account server for user archives)
        if is_muc:
            query_jid = jid
        else:
            # For 1-to-1 chats, MAM archive is on our own server
            query_jid = self.boundjid.bare

        try:
            # Use service discovery to get features
            disco_info = await self.plugin['xep_0030'].get_info(jid=query_jid, timeout=10)

            # Check if MAM namespace is in features
            mam_namespace = 'urn:xmpp:mam:2'
            mam_supported = mam_namespace in disco_info['disco_info']['features']

            if mam_supported:
                self.logger.info(f"MAM supported by {query_jid}")
            else:
                self.logger.info(f"MAM NOT supported by {query_jid}")

            return mam_supported

        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Service discovery failed for {query_jid}: {error_condition}")
            raise
        except IqTimeout:
            self.logger.warning(f"Service discovery timeout for {query_jid}")
            raise
        except Exception as e:
            self.logger.exception(f"Failed to check MAM support for {query_jid}: {e}")
            raise
