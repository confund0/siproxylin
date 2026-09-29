"""
File Upload module for DrunkXMPP.

XEP-0363: HTTP File Upload

Provides methods for uploading files and sending attachments (with optional OMEMO encryption).
"""

import asyncio
from typing import Optional, Set
from slixmpp.jid import JID

from .slixmpp_patches.xep_0363_upload_proxy import use_upload_proxy


class FileUploadMixin:
    """
    Mixin providing file upload and attachment functionality.

    Requirements (provided by DrunkXMPP):
    - self['xep_0363']: HTTP File Upload plugin
    - self['xep_0066']: Out of Band Data plugin
    - self['xep_0380']: Explicit Message Encryption plugin
    - self.plugin: Dict of loaded slixmpp plugins
    - self.rooms: Dict of joined rooms
    - self.omemo_enabled: Boolean indicating if OMEMO is enabled
    - self.omemo_ready: Boolean indicating if OMEMO is ready
    - self.make_message(): Method to create message stanzas
    - self.logger: Logger instance
    """

    async def upload_file(self, file_path: str, content_type: Optional[str] = None) -> str:
        """
        Upload a file using XEP-0363 (HTTP File Upload).

        Args:
            file_path: Path to the file to upload
            content_type: MIME type of the file (auto-detected if not provided)

        Returns:
            The HTTP URL of the uploaded file

        Raises:
            RuntimeError: If upload fails
            FileNotFoundError: If file doesn't exist
        """
        import mimetypes
        from pathlib import Path

        file = Path(file_path)
        if not file.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file.stat().st_size
        file_name = file.name

        # Auto-detect content type if not provided
        if not content_type:
            content_type, _ = mimetypes.guess_type(file_name)
            if not content_type:
                content_type = 'application/octet-stream'

        self.logger.info(f"Uploading file: {file_name} ({file_size} bytes, {content_type})")

        # Request upload slot from server
        try:
            # The PUT goes through the account proxy (see xep_0363_upload_proxy)
            with use_upload_proxy(self.http_proxy_fields):
                slot = await self['xep_0363'].upload_file(
                    file_path,
                    domain=None,  # Auto-detect from server
                    timeout=30
                )
        except Exception as e:
            self.logger.exception(f"Failed to upload file: {e}")
            raise RuntimeError(f"File upload failed: {e}")

        self.logger.info(f"File uploaded successfully: {slot}")
        return slot

    async def send_attachment_to_muc(self, room_jid: str, file_path: str,
                                     caption: Optional[str] = None,
                                     content_type: Optional[str] = None) -> str:
        """
        Upload and send a file attachment to a MUC room.

        Args:
            room_jid: Room JID
            file_path: Path to the file to send
            caption: Optional caption/message to accompany the file
            content_type: MIME type of the file (auto-detected if not provided)

        Returns:
            Message ID (origin_id for tracking/deduplication)

        Raises:
            RuntimeError: If not joined to room or upload fails
        """
        if room_jid not in self.joined_rooms:
            self.logger.warning(f"Not joined to {room_jid}, attempting to join first")
            if room_jid in self.rooms:
                await self._join_room(room_jid, self.rooms[room_jid])
                await asyncio.sleep(2)
            else:
                raise ValueError(f"Room {room_jid} not configured")

        if room_jid not in self.joined_rooms:
            raise RuntimeError(f"Not joined to {room_jid}")

        # Upload the file first
        url = await self.upload_file(file_path, content_type)

        # Build message with OOB data for inline display
        from pathlib import Path
        file_name = Path(file_path).name

        # Create message stanza
        msg = self.make_message(mto=room_jid, mtype='groupchat')

        # Set body: include URL in body for clients that don't support OOB
        # Real-world practice: always include URL in body for fallback
        if caption:
            msg['body'] = f"{caption}\n{url}"
        else:
            msg['body'] = url

        # Add OOB data for inline media display (this is what clients use for inline rendering)
        msg['oob']['url'] = url

        # Add origin-id for message tracking and deduplication (XEP-0359)
        msg['origin_id']['id'] = msg['id']

        # Send the message
        msg.send()
        self.logger.info(f"Attachment sent to {room_jid}: {file_name}")

        # Return message ID for tracking/deduplication
        return msg['id']

    async def send_encrypted_attachment_to_muc(self, room_jid: str, file_path: str,
                                               caption: Optional[str] = None,
                                               content_type: Optional[str] = None) -> str:
        """
        Upload and send an OMEMO-encrypted file to a MUC room using XEP-0454.
        File is encrypted locally, uploaded, and sent as aesgcm:// URL in OMEMO message.

        Args:
            room_jid: Room JID
            file_path: Path to the file to send
            caption: Optional caption/message to accompany the file
            content_type: MIME type (ignored, XEP-0454 uses application/octet-stream)

        Returns:
            Message ID (origin_id for tracking/deduplication)

        Raises:
            RuntimeError: If OMEMO not enabled/ready or upload fails
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

        if room_jid not in self.joined_rooms:
            self.logger.warning(f"Not joined to {room_jid}, attempting to join first")
            if room_jid in self.rooms:
                await self._join_room(room_jid, self.rooms[room_jid])
                await asyncio.sleep(2)
            else:
                raise ValueError(f"Room {room_jid} not configured")

        if room_jid not in self.joined_rooms:
            raise RuntimeError(f"Not joined to {room_jid}")

        from pathlib import Path
        file = Path(file_path)
        if not file.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = file.name
        self.logger.info(f"Encrypting and sending file to MUC {room_jid}: {file_name}")

        # Use XEP-0454 plugin's upload_file method
        # This properly handles encryption, random filename with preserved extension,
        # upload, and aesgcm:// URL generation
        xep_0454 = self['xep_0454']
        # XEP-0454 uploads with XEP-0363, so the PUT goes through the account proxy
        with use_upload_proxy(self.http_proxy_fields):
            aesgcm_url = await xep_0454.upload_file(
                filename=file,
                content_type='application/octet-stream'
            )

        self.logger.debug(f"File encrypted and uploaded: {aesgcm_url[:60]}...")

        # Build message body with aesgcm:// URL
        if caption:
            message = f"{caption}\n{aesgcm_url}"
        else:
            message = aesgcm_url

        # Get XEP-0045 and XEP-0384 plugins
        xep_0045 = self.plugin['xep_0045']
        xep_0384 = self.plugin['xep_0384']

        # Create message stanza
        stanza = self.make_message(mto=room_jid, mtype='groupchat')
        stanza['body'] = message

        # Get all participants in the room for encryption
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

            # Send all encrypted versions
            for namespace, encrypted_msg in messages.items():
                encrypted_msg['eme']['namespace'] = namespace
                encrypted_msg['eme']['name'] = self['xep_0380'].mechanisms.get(namespace, 'OMEMO')

                # Copy origin-id from original stanza to encrypted message (for deduplication)
                encrypted_msg['origin_id']['id'] = stanza['id']

                encrypted_msg.send()
                self.logger.info(f"OMEMO-encrypted file sent to MUC {room_jid} (namespace: {namespace}): {file_name}")

            # Return the original stanza ID for tracking/deduplication
            return stanza['id']

        except Exception as e:
            self.logger.exception(f"Failed to encrypt attachment message: {e}")
            raise

    async def send_attachment_to_user(self, jid: str, file_path: str,
                                      caption: Optional[str] = None,
                                      content_type: Optional[str] = None) -> str:
        """
        Upload and send a file attachment to a user via private message.

        Args:
            jid: User JID
            file_path: Path to the file to send
            caption: Optional caption/message to accompany the file
            content_type: MIME type of the file (auto-detected if not provided)

        Returns:
            Message ID (origin_id for tracking/deduplication)

        Raises:
            RuntimeError: If upload fails
        """
        # Upload the file first
        url = await self.upload_file(file_path, content_type)

        # Build message with OOB data for inline display
        from pathlib import Path
        file_name = Path(file_path).name

        # Create message stanza
        msg = self.make_message(mto=jid, mtype='chat')

        # Set body: include URL in body for clients that don't support OOB
        # Real-world practice: always include URL in body for fallback
        if caption:
            msg['body'] = f"{caption}\n{url}"
        else:
            msg['body'] = url

        # Add OOB data for inline media display (this is what clients use for inline rendering)
        msg['oob']['url'] = url

        # Send the message
        msg.send()
        self.logger.info(f"Attachment sent to {jid}: {file_name}")

        # Return the message ID for tracking/deduplication
        return msg['id']

    async def send_encrypted_file(self, jid: str, file_path: str,
                                   caption: Optional[str] = None) -> str:
        """
        Send an OMEMO-encrypted file using XEP-0454 (OMEMO Media Sharing).
        File is encrypted locally, uploaded, and sent as aesgcm:// URL in OMEMO message.

        Args:
            jid: User JID
            file_path: Path to the file to send
            caption: Optional caption/message to accompany the file

        Returns:
            Message ID (origin_id for tracking/deduplication)

        Raises:
            RuntimeError: If OMEMO not ready or upload fails
            FileNotFoundError: If file doesn't exist
        """
        from pathlib import Path
        import tempfile

        file = Path(file_path)
        if not file.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = file.name
        self.logger.info(f"Encrypting and sending file to {jid}: {file_name}")

        # Use XEP-0454 plugin's upload_file method
        # This properly handles encryption, random filename with preserved extension,
        # upload, and aesgcm:// URL generation
        xep_0454 = self['xep_0454']
        # XEP-0454 uploads with XEP-0363, so the PUT goes through the account proxy
        with use_upload_proxy(self.http_proxy_fields):
            aesgcm_url = await xep_0454.upload_file(
                filename=file,
                content_type='application/octet-stream'
            )

        self.logger.debug(f"File encrypted and uploaded: {aesgcm_url[:60]}...")

        # Build message body
        if caption:
            message = f"{caption}\n{aesgcm_url}"
        else:
            message = aesgcm_url

        # Send as OMEMO-encrypted message
        message_id = await self.send_encrypted_private_message(jid, message)
        self.logger.info(f"OMEMO-encrypted file sent to {jid}: {file_name}")

        # Return the message ID for tracking/deduplication
        return message_id

    async def send_encrypted_attachment_to_user(self, jid: str, file_path: str,
                                                caption: Optional[str] = None,
                                                content_type: Optional[str] = None) -> str:
        """
        Upload and send an OMEMO-encrypted file attachment reference to a user.
        The file itself is uploaded via HTTP, but the message with the URL is encrypted.

        Args:
            jid: User JID
            file_path: Path to the file to send
            caption: Optional caption/message to accompany the file
            content_type: MIME type of the file (auto-detected if not provided)

        Returns:
            Message ID (origin_id for tracking/deduplication)

        Raises:
            RuntimeError: If OMEMO not enabled/ready or upload fails
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

        # Upload the file first
        url = await self.upload_file(file_path, content_type)

        # Build message with OOB data for inline display
        from pathlib import Path
        file_name = Path(file_path).name

        self.logger.debug(f"Sending OMEMO-encrypted attachment to {jid}: {file_name}")

        xep_0384 = self.plugin['xep_0384']

        # Create message stanza
        recipient_jid = JID(jid)
        stanza = self.make_message(mto=recipient_jid.bare, mtype='chat')

        # Set body: include URL in body for clients that don't support OOB
        # Real-world practice: always include URL in body for fallback
        if caption:
            stanza['body'] = f"{caption}\n{url}"
        else:
            stanza['body'] = url

        # Add OOB data for inline media display (this is what clients use for inline rendering)
        stanza['oob']['url'] = url

        # Refresh device list for recipient
        await xep_0384.refresh_device_lists({recipient_jid})

        # Encrypt the message
        try:
            messages, encryption_errors = await xep_0384.encrypt_message(stanza, {recipient_jid})

            if encryption_errors:
                self.logger.warning(f"Encryption errors: {encryption_errors}")

            if not messages:
                raise RuntimeError("Encryption produced no messages")

            # Send all encrypted versions
            for namespace, encrypted_msg in messages.items():
                encrypted_msg['eme']['namespace'] = namespace
                encrypted_msg['eme']['name'] = self['xep_0380'].mechanisms.get(namespace, 'OMEMO')

                # OOB extension not used with OMEMO - clients expect aesgcm:// URLs (XEP-0454)
                # For now, encrypted attachments will show as clickable URLs only

                # Copy origin-id from original stanza to encrypted message (for deduplication)
                encrypted_msg['origin_id']['id'] = stanza['id']

                encrypted_msg.send()
                self.logger.info(f"OMEMO-encrypted attachment sent to {jid} (namespace: {namespace}): {file_name}")

            # Return the original stanza ID for tracking/deduplication
            return stanza['id']

        except Exception as e:
            self.logger.exception(f"Failed to encrypt attachment message: {e}")
            raise

