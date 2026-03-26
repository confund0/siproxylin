"""
FileBarrel - Handles file transfers and attachments.

Responsibilities:
- File download and upload (XEP-0363 HTTP Upload)
- OMEMO encrypted file handling (XEP-0454)
- File attachment storage and database tracking
"""

import logging
from typing import Optional


class FileBarrel:
    """Manages file transfers for an account."""

    def __init__(self, account_id: int, client, db, logger, signals: dict):
        """
        Initialize file barrel.

        Args:
            account_id: Account ID
            client: DrunkXMPP client instance (must be set before use)
            db: Database singleton (direct access)
            logger: Account logger instance
            signals: Dict of Qt signal references for emitting events
        """
        self.account_id = account_id
        self.client = client  # Will be None initially, set by brewery after connection
        self.db = db
        self.logger = logger
        self.signals = signals

    async def handle_incoming_file(self, jid_id: int, from_jid: str, file_url: str,
                                    is_encrypted: bool, timestamp: int, conversation_id: int,
                                    direction: int = 0, is_from_other_device: bool = False,
                                    message_id: Optional[str] = None, origin_id: Optional[str] = None,
                                    stanza_id: Optional[str] = None):
        """
        Handle incoming file attachment.

        Downloads file, stores it locally, and creates file_transfer DB entry.

        Args:
            jid_id: JID database ID
            from_jid: Sender JID
            file_url: File URL (http:// or aesgcm://)
            is_encrypted: Whether file is OMEMO encrypted (aesgcm://)
            timestamp: Message timestamp
            conversation_id: Conversation ID for content_item linking
            direction: 0=received, 1=sent (default: 0)
        """
        import aiohttp
        import mimetypes
        import os
        from datetime import datetime
        from urllib.parse import urlparse
        from ...utils.paths import _mkdir_secure, _chmod_secure

        try:
            # Determine if URL is encrypted (aesgcm://)
            is_aesgcm = file_url.startswith('aesgcm://')

            # Extract filename from URL
            if is_aesgcm:
                # aesgcm://example.com/path/file.jpg#fragment
                # Convert to https:// for download, keep fragment for decryption
                url_parts = file_url[len('aesgcm://'):]
                if '#' in url_parts:
                    http_part, fragment = url_parts.split('#', 1)
                else:
                    http_part = url_parts
                    fragment = None

                download_url = f'https://{http_part}'
                parsed = urlparse(download_url)
            else:
                download_url = file_url
                parsed = urlparse(file_url)
                fragment = None

            # Extract filename from URL path
            path_parts = parsed.path.rstrip('/').split('/')
            url_filename = path_parts[-1] if path_parts else 'attachment'

            # Generate storage path: {data_dir}/attachments/{account_id}/{sender_jid}/YYYY-mm-dd_HHMMSS.ext
            from ...utils.paths import get_paths
            paths = get_paths()

            # Create sender-specific directory (automatically uses dev or XDG path)
            attachments_base = paths.data_dir / 'attachments'
            sender_dir = attachments_base / str(self.account_id) / from_jid
            _mkdir_secure(sender_dir, parents=True)

            # Generate timestamped filename
            dt = datetime.fromtimestamp(timestamp)
            timestamp_str = dt.strftime('%Y-%m-%d_%H%M%S')

            # Extract extension from original filename
            _, ext = os.path.splitext(url_filename)
            if not ext:
                ext = '.bin'

            local_filename = f'{timestamp_str}{ext}'
            local_path = sender_dir / local_filename

            if self.logger:
                self.logger.info(f"Downloading file from {download_url} to {local_path}")

            # Download file
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url) as response:
                    if response.status == 200:
                        file_data = await response.read()

                        # Decrypt if OMEMO encrypted
                        if is_aesgcm and fragment:
                            if self.logger:
                                self.logger.info("Decrypting OMEMO-encrypted file")
                            try:
                                # Parse fragment for decryption (XEP-0454 format)
                                # Fragment format: IV (24 hex chars) + key (64 hex chars) = 88 chars total
                                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

                                if len(fragment) != 88:
                                    raise ValueError(f"Invalid fragment length: {len(fragment)} (expected 88)")

                                # Extract IV (first 24 hex chars = 12 bytes) and key (next 64 hex chars = 32 bytes)
                                iv = bytes.fromhex(fragment[:24])    # 12 bytes
                                key = bytes.fromhex(fragment[24:])   # 32 bytes

                                # Last 16 bytes of file_data are the GCM auth tag
                                tag = file_data[-16:]
                                ciphertext = file_data[:-16]

                                # Decrypt using AES-256-GCM
                                cipher = Cipher(
                                    algorithms.AES(key),
                                    modes.GCM(iv, tag)
                                )
                                decryptor = cipher.decryptor()
                                file_data = decryptor.update(ciphertext) + decryptor.finalize()

                                if self.logger:
                                    self.logger.info("File decrypted successfully")

                            except Exception as e:
                                if self.logger:
                                    self.logger.error(f"Failed to decrypt file: {e}")
                                raise

                        # Write to file with secure permissions
                        local_path.write_bytes(file_data)
                        _chmod_secure(local_path, 0o600)

                        file_size = len(file_data)

                        if self.logger:
                            self.logger.info(f"File downloaded: {local_path} ({file_size} bytes)")

                        # Guess MIME type
                        mime_type, _ = mimetypes.guess_type(local_filename)

                        # Insert file_transfer + content_item atomically with deduplication
                        file_transfer_id, content_item_id = self.db.insert_file_transfer_atomic(
                            account_id=self.account_id,
                            counterpart_id=jid_id,
                            conversation_id=conversation_id,
                            direction=direction,  # 0=received, 1=sent
                            time=timestamp,
                            local_time=timestamp,
                            file_name=url_filename,
                            path=str(local_path),
                            mime_type=mime_type,
                            size=file_size,
                            state=2,  # state=2 (complete)
                            encryption=1 if is_encrypted else 0,
                            provider=0,  # provider=0 (HTTP Upload)
                            is_carbon=1 if is_from_other_device else 0,
                            url=file_url,
                            message_id=message_id,
                            origin_id=origin_id,
                            stanza_id=stanza_id
                        )

                        if file_transfer_id is None:
                            # Duplicate file, skip
                            if self.logger:
                                self.logger.info(f"Skipped duplicate file: {url_filename}")
                            return

                        if self.logger:
                            self.logger.info(f"File transfer record created (ID: {file_transfer_id})")

                        # Emit signal to notify GUI (updates unread count, notifications, bold text)
                        self.signals['message_received'].emit(self.account_id, from_jid, False)

                    else:
                        if self.logger:
                            self.logger.error(f"Failed to download file: HTTP {response.status}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to handle incoming file: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    async def handle_carbon_file(self, jid_id: int, counterpart_jid: str, file_url: str,
                                   is_encrypted: bool, timestamp: int, conversation_id: int,
                                   direction: int):
        """
        Handle file attachment from a carbon copy (sent from another device).

        Downloads file, stores it locally, and creates file_transfer DB entry.
        Similar to handle_incoming_file but supports both directions for carbons.

        Deduplication: For sent carbons (direction=1), checks if a file_transfer already
        exists from GUI send (which stores immediately without URL). If found, updates
        it with URL instead of creating duplicate.

        Args:
            jid_id: JID database ID
            counterpart_jid: Contact JID (recipient if sent, sender if received)
            file_url: File URL (http:// or aesgcm://)
            is_encrypted: Whether file is OMEMO encrypted (aesgcm://)
            timestamp: Message timestamp
            conversation_id: Conversation ID for content_item linking
            direction: 0=received (carbon_received), 1=sent (carbon_sent)
        """
        import aiohttp
        import mimetypes
        import os
        from datetime import datetime
        from urllib.parse import urlparse
        from ...utils.paths import _mkdir_secure, _chmod_secure

        try:
            # DEDUPLICATION: For sent files (direction=1), check if GUI already created record
            # GUI creates file_transfer immediately without URL, carbon provides the URL
            if direction == 1:
                # Look for recent file_transfer with same counterpart, direction, no URL
                # Within last 60 seconds to handle timing variations
                time_threshold = timestamp - 60
                existing = self.db.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND counterpart_id = ? AND direction = 1
                      AND url IS NULL AND time >= ?
                    ORDER BY time DESC
                    LIMIT 1
                """, (self.account_id, jid_id, time_threshold))

                if existing:
                    # Update existing record with URL instead of creating new one
                    if self.logger:
                        self.logger.info(f"[CARBON TX] Updating existing file_transfer {existing['id']} with URL")

                    self.db.execute("""
                        UPDATE file_transfer
                        SET url = ?, is_carbon = 1
                        WHERE id = ?
                    """, (file_url, existing['id']))
                    self.db.commit()

                    # Emit signal to refresh GUI
                    self.signals['message_received'].emit(self.account_id, counterpart_jid, False)
                    return

            # Determine if URL is encrypted (aesgcm://)
            is_aesgcm = file_url.startswith('aesgcm://')

            # Extract filename from URL
            if is_aesgcm:
                # aesgcm://example.com/path/file.jpg#fragment
                # Convert to https:// for download, keep fragment for decryption
                url_parts = file_url[len('aesgcm://'):]
                if '#' in url_parts:
                    http_part, fragment = url_parts.split('#', 1)
                else:
                    http_part = url_parts
                    fragment = None

                download_url = f'https://{http_part}'
                parsed = urlparse(download_url)
            else:
                download_url = file_url
                parsed = urlparse(file_url)
                fragment = None

            # Extract filename from URL path
            path_parts = parsed.path.rstrip('/').split('/')
            url_filename = path_parts[-1] if path_parts else 'attachment'

            # Generate storage path: {data_dir}/attachments/{account_id}/{counterpart_jid}/YYYY-mm-dd_HHMMSS.ext
            from ...utils.paths import get_paths
            paths = get_paths()

            # Create counterpart-specific directory (automatically uses dev or XDG path)
            attachments_base = paths.data_dir / 'attachments'
            counterpart_dir = attachments_base / str(self.account_id) / counterpart_jid
            _mkdir_secure(counterpart_dir, parents=True)

            # Generate timestamped filename
            dt = datetime.fromtimestamp(timestamp)
            timestamp_str = dt.strftime('%Y-%m-%d_%H%M%S')

            # Extract extension from original filename
            _, ext = os.path.splitext(url_filename)
            if not ext:
                ext = '.bin'

            local_filename = f'{timestamp_str}{ext}'
            local_path = counterpart_dir / local_filename

            if self.logger:
                direction_str = "sent" if direction == 1 else "received"
                self.logger.info(f"Downloading carbon {direction_str} file from {download_url} to {local_path}")

            # Download file
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url) as response:
                    if response.status == 200:
                        file_data = await response.read()

                        # Decrypt if OMEMO encrypted
                        if is_aesgcm and fragment:
                            if self.logger:
                                self.logger.info("Decrypting OMEMO-encrypted file")
                            try:
                                # Parse fragment for decryption (XEP-0454 format)
                                # Fragment format: IV (24 hex chars) + key (64 hex chars) = 88 chars total
                                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

                                if len(fragment) != 88:
                                    raise ValueError(f"Invalid fragment length: {len(fragment)} (expected 88)")

                                # Extract IV (first 24 hex chars = 12 bytes) and key (next 64 hex chars = 32 bytes)
                                iv = bytes.fromhex(fragment[:24])    # 12 bytes
                                key = bytes.fromhex(fragment[24:])   # 32 bytes

                                # Last 16 bytes of file_data are the GCM auth tag
                                tag = file_data[-16:]
                                ciphertext = file_data[:-16]

                                # Decrypt using AES-256-GCM
                                cipher = Cipher(
                                    algorithms.AES(key),
                                    modes.GCM(iv, tag)
                                )
                                decryptor = cipher.decryptor()
                                file_data = decryptor.update(ciphertext) + decryptor.finalize()

                                if self.logger:
                                    self.logger.info("File decrypted successfully")

                            except Exception as e:
                                if self.logger:
                                    self.logger.error(f"Failed to decrypt file: {e}")
                                raise

                        # Write to file with secure permissions
                        local_path.write_bytes(file_data)
                        _chmod_secure(local_path, 0o600)

                        file_size = len(file_data)

                        if self.logger:
                            self.logger.info(f"File downloaded: {local_path} ({file_size} bytes)")

                        # Guess MIME type
                        mime_type, _ = mimetypes.guess_type(local_filename)

                        # Insert file_transfer + content_item atomically with deduplication
                        file_transfer_id, content_item_id = self.db.insert_file_transfer_atomic(
                            account_id=self.account_id,
                            counterpart_id=jid_id,
                            conversation_id=conversation_id,
                            direction=direction,  # 0=received, 1=sent
                            time=timestamp,
                            local_time=timestamp,
                            file_name=url_filename,
                            path=str(local_path),
                            mime_type=mime_type,
                            size=file_size,
                            state=2,  # state=2 (complete)
                            encryption=1 if is_encrypted else 0,
                            provider=0,  # provider=0 (HTTP Upload)
                            is_carbon=1,  # is_carbon=1 (carbon copy from another device)
                            url=file_url,
                            message_id=message_id,
                            origin_id=origin_id,
                            stanza_id=stanza_id
                        )

                        if file_transfer_id is None:
                            # Duplicate file, skip
                            if self.logger:
                                self.logger.info(f"Skipped duplicate carbon file: {url_filename}")
                            return

                        if self.logger:
                            self.logger.info(f"Carbon file transfer record created (ID: {file_transfer_id})")

                        # Emit signal to notify GUI (updates unread count, notifications, bold text)
                        self.signals['message_received'].emit(self.account_id, counterpart_jid, False)

                    else:
                        if self.logger:
                            self.logger.error(f"Failed to download carbon file: HTTP {response.status}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to handle carbon file: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
