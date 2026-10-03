"""
FileBarrel - Handles file transfers and attachments.

Responsibilities:
- Received file attachments: store a pending row, download in the background
- Downloads go through the current account proxy (own HTTP session, no XMPP client needed)
- OMEMO encrypted file handling (XEP-0454, aesgcm://)
- File attachment storage and database tracking
"""

import asyncio
import json
import logging
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

from ...utils.download_policy import (
    AUTO_DOWNLOAD_MAX_BYTES,
    MANUAL_DOWNLOAD_MAX_BYTES,
    FT_STATE_PENDING,
    FT_STATE_TRANSFERRING,
    FT_STATE_COMPLETE,
    FT_STATE_FAILED,
    DOWNLOAD_ERROR_KEY,
    PART_SUFFIX,
    is_trusted_sender,
    proxy_fields_from_account,
    safe_dir_name,
    safe_extension,
)

# Most downloads that run at the same time (per account)
MAX_PARALLEL_DOWNLOADS = 3


class FileBarrel:
    """Manages file transfers for an account."""

    def __init__(self, account_id: int, client, db, logger, signals: dict,
                 account_data: Optional[dict] = None):
        """
        Initialize file barrel.

        Args:
            account_id: Account ID
            client: DrunkXMPP client instance (must be set before use)
            db: Database singleton (direct access)
            logger: Account logger instance
            signals: Dict of Qt signal references for emitting events
            account_data: Shared account settings dict (proxy settings are read
                          from it on each download). None: downloads fail.
        """
        self.account_id = account_id
        self.client = client  # Will be None initially, set by brewery after connection
        self.db = db
        self.logger = logger
        self.signals = signals
        self.account_data = account_data

        # Background downloads: file_transfer id -> task (queued or running)
        self._downloads = {}
        self._semaphore = None

        # HTTP session for downloads, and the proxy fields it was made with
        self._http_session = None
        self._http_session_key = None
        self._close_tasks = set()

        # Downloads from an earlier run cannot go on: mark them as failed
        self.recover_interrupted_downloads()

    # =========================================================================
    # Paths and helpers
    # =========================================================================

    def _account_dir(self) -> Path:
        """Download dir of this account: {data_dir}/attachments/{account_id}"""
        from ...utils.paths import get_paths
        return get_paths().data_dir / 'attachments' / str(self.account_id)

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
        return self._semaphore

    def _refresh_chat(self, jid: Optional[str]):
        """Refresh the chat view. is_marker=True: no notification, no new-message handling."""
        if jid:
            self.signals['message_received'].emit(self.account_id, jid, True)

    def _account_enabled(self) -> bool:
        """True if the account row exists and is enabled (read from the database)."""
        row = self.db.fetchone("SELECT enabled FROM account WHERE id = ?", (self.account_id,))
        return bool(row and row['enabled'])

    def is_trusted_sender(self, jid_id: int) -> bool:
        """True if files from this contact may download automatically (see download_policy)."""
        try:
            return is_trusted_sender(self.db, self.account_id, jid_id)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to check roster trust: {e}")
            return False

    @staticmethod
    def _file_name_from_url(file_url: str) -> str:
        """Last path part of the URL (without the aesgcm fragment)."""
        url = file_url
        if url.startswith('aesgcm://'):
            url = 'https://' + url[len('aesgcm://'):]
        try:
            path = urlparse(url).path
        except ValueError:
            path = ''
        parts = path.rstrip('/').split('/')
        name = unquote(parts[-1]) if parts else ''
        name = name.replace('/', '_').replace('\\', '_').replace('\x00', '_').strip()
        return name or 'attachment'

    def _set_state(self, file_transfer_id: int, state: int, error: Optional[str] = None):
        """Set the row state. error is stored as info['download_error'], None removes it."""
        row = self.db.fetchone(
            "SELECT info FROM file_transfer WHERE id = ?", (file_transfer_id,)
        )
        if not row:
            return
        info = {}
        if row['info']:
            try:
                info = json.loads(row['info'])
                if not isinstance(info, dict):
                    info = {}
            except (ValueError, TypeError):
                info = {}
        if error:
            info[DOWNLOAD_ERROR_KEY] = error
        else:
            info.pop(DOWNLOAD_ERROR_KEY, None)
        self.db.execute(
            "UPDATE file_transfer SET state = ?, info = ? WHERE id = ?",
            (state, json.dumps(info) if info else None, file_transfer_id)
        )
        self.db.commit()

    def _target_dir(self, bare_jid: str) -> Path:
        from ...utils.paths import _mkdir_secure
        target_dir = self._account_dir() / safe_dir_name(bare_jid)
        _mkdir_secure(target_dir, parents=True)
        return target_dir

    @staticmethod
    def _base_name(row) -> str:
        """File name without extension: {stamp}_{file_transfer id}"""
        try:
            stamp = datetime.fromtimestamp(int(row['time'])).strftime('%Y-%m-%d_%H%M%S')
        except (TypeError, ValueError, OSError, OverflowError):
            stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        return f'{stamp}_{row["id"]}'

    @staticmethod
    def _reserve_final_path(target_dir: Path, base: str, ext: str) -> Path:
        """
        Create a new empty file with O_EXCL and return its path.
        The download then replaces only this file, never a file we did not create.
        """
        for n in range(100):
            name = f'{base}{ext}' if n == 0 else f'{base}_{n}{ext}'
            path = target_dir / name
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            os.close(fd)
            return path
        raise FileExistsError(f'no free file name for {base}{ext}')

    # =========================================================================
    # HTTP session
    # =========================================================================

    def _get_http_session(self):
        """
        Return the download session for the CURRENT account proxy settings.

        The session is made on first use and made again when the proxy settings change.
        A bad proxy setting raises DownloadError('proxy error'); never a direct connection.
        """
        from drunk_xmpp.http_download import make_session

        fields = proxy_fields_from_account(self.account_data)
        key = tuple(sorted(fields.items()))
        session = self._http_session
        if session is not None and not session.closed and self._http_session_key == key:
            return session

        self._close_http_session()
        self._http_session = make_session(**fields)
        self._http_session_key = key
        return self._http_session

    def _close_http_session(self):
        """Close the download session. The close task is kept until it is done."""
        session = self._http_session
        self._http_session = None
        self._http_session_key = None
        if session is None or session.closed:
            return
        try:
            task = asyncio.ensure_future(session.close())
        except RuntimeError as e:
            if self.logger:
                self.logger.debug(f"HTTP session not closed (no event loop): {e}")
            return
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    # =========================================================================
    # Incoming files
    # =========================================================================

    async def handle_incoming_file(self, jid_id: int, from_jid: str, file_url: str,
                                    is_encrypted: bool, timestamp: int, conversation_id: int,
                                    direction: int = 0, is_from_other_device: bool = False,
                                    message_id: Optional[str] = None, origin_id: Optional[str] = None,
                                    stanza_id: Optional[str] = None,
                                    counterpart_resource: Optional[str] = None,
                                    auto_download: bool = False,
                                    refresh: bool = True) -> Optional[int]:
        """
        Handle incoming file attachment.

        Stores a pending file_transfer row first (state 0, no path).
        With auto_download=True, starts the download in the background
        with the automatic size limit. The caller decides auto_download.

        Args:
            jid_id: JID database ID
            from_jid: Conversation JID (contact, or room for MUC)
            file_url: File URL (https:// or aesgcm://)
            is_encrypted: Whether file is OMEMO encrypted (aesgcm://)
            timestamp: Message timestamp
            conversation_id: Conversation ID for content_item linking
            direction: 0=received, 1=sent (default: 0)
            counterpart_resource: Sender resource (MUC nickname), optional
            auto_download: Start the download now
            refresh: Refresh the chat view after the insert (False when the
                     caller refreshes once per batch, e.g. MAM pages)

        Returns:
            file_transfer id, or None for a duplicate or an error
        """
        try:
            file_name = self._file_name_from_url(file_url)
            mime_type, _ = mimetypes.guess_type(file_name)

            # Insert file_transfer + content_item atomically with deduplication
            file_transfer_id, _ = self.db.insert_file_transfer_atomic(
                account_id=self.account_id,
                counterpart_id=jid_id,
                conversation_id=conversation_id,
                direction=direction,  # 0=received, 1=sent
                time=timestamp,
                local_time=timestamp,
                file_name=file_name,
                path=None,  # set when the download is complete
                mime_type=mime_type,
                size=None,
                state=FT_STATE_PENDING,
                encryption=1 if is_encrypted else 0,
                provider=0,  # provider=0 (HTTP Upload)
                is_carbon=1 if is_from_other_device else 0,
                url=file_url,
                message_id=message_id,
                origin_id=origin_id,
                stanza_id=stanza_id,
                counterpart_resource=counterpart_resource
            )

            if file_transfer_id is None:
                if self.logger:
                    self.logger.info(f"Skipped duplicate file: {file_name}")
                return None

            if self.logger:
                self.logger.info(f"Pending file transfer stored (ID: {file_transfer_id}, auto_download={auto_download})")

            # Refresh only. The caller emits the new-message signal for incoming messages.
            if refresh:
                self._refresh_chat(from_jid)

            if auto_download:
                self._schedule_download(file_transfer_id, AUTO_DOWNLOAD_MAX_BYTES)

            return file_transfer_id

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to handle incoming file: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            return None

    def request_download(self, file_transfer_id: int) -> None:
        """
        Start the download of a file the user clicked.

        Works for rows in state 0 (pending) or 3 (failed); other states are ignored.
        Does nothing for a disabled account.
        Uses the manual size limit and no trust check. Runs in the background;
        the chat view is refreshed when the state changes.
        """
        if not self._account_enabled():
            if self.logger:
                self.logger.info(f"Download of file {file_transfer_id} not started: account is disabled")
            return
        row = self.db.fetchone(
            "SELECT state FROM file_transfer WHERE id = ? AND account_id = ?",
            (file_transfer_id, self.account_id)
        )
        if not row or row['state'] not in (FT_STATE_PENDING, FT_STATE_FAILED):
            return
        self._schedule_download(file_transfer_id, MANUAL_DOWNLOAD_MAX_BYTES)

    def _schedule_download(self, file_transfer_id: int, max_bytes: int):
        if file_transfer_id in self._downloads:
            return
        task = asyncio.ensure_future(self._run_download(file_transfer_id, max_bytes))
        self._downloads[file_transfer_id] = task
        task.add_done_callback(
            lambda t, fid=file_transfer_id: self._forget_download(fid, t))

    def _forget_download(self, file_transfer_id: int, task):
        # Remove only our own task (a new task for the same id may exist)
        if self._downloads.get(file_transfer_id) is task:
            del self._downloads[file_transfer_id]

    def cancel_all(self):
        """
        Stop all queued and running downloads and close the HTTP session.

        Called on disconnect, account disable and account deletion.
        Running rows (state 1) get state 3 with reason 'interrupted'; queued rows keep their state.
        """
        downloads = list(self._downloads.items())
        self._downloads.clear()
        for file_transfer_id, task in downloads:
            task.cancel()
            try:
                row = self.db.fetchone(
                    "SELECT state FROM file_transfer WHERE id = ?", (file_transfer_id,)
                )
                if row and row['state'] == FT_STATE_TRANSFERRING:
                    self._set_state(file_transfer_id, FT_STATE_FAILED, 'interrupted')
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to reset file {file_transfer_id}: {e}")
        if downloads and self.logger:
            self.logger.info(f"Cancelled {len(downloads)} download(s)")
        self._close_http_session()

    async def _run_download(self, file_transfer_id: int, max_bytes: int):
        try:
            async with self._get_semaphore():
                # The account may be disabled or deleted while the task waited
                if not self._account_enabled():
                    if self.logger:
                        self.logger.info(f"Download of file {file_transfer_id} skipped: account is disabled")
                    return
                await self._download(file_transfer_id, max_bytes)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.logger:
                self.logger.error(f"Download task failed (ID: {file_transfer_id}): {e}")

    def _row_exists(self, file_transfer_id: int) -> bool:
        return self.db.fetchone(
            "SELECT id FROM file_transfer WHERE id = ? AND account_id = ?",
            (file_transfer_id, self.account_id)
        ) is not None

    async def _download(self, file_transfer_id: int, max_bytes: int):
        """Download one file_transfer row. Sets state 1, then 2 or 3."""
        from drunk_xmpp.http_download import DownloadError, download_to_file
        from ...utils.paths import _chmod_secure

        row = self.db.fetchone("""
            SELECT ft.id, ft.state, ft.url, ft.time, ft.file_name, ft.mime_type, j.bare_jid
            FROM file_transfer ft
            JOIN jid j ON j.id = ft.counterpart_id
            WHERE ft.id = ? AND ft.account_id = ?
        """, (file_transfer_id, self.account_id))
        if not row or row['state'] not in (FT_STATE_PENDING, FT_STATE_FAILED):
            return

        jid = row['bare_jid']
        part_path = None  # set only when this task created the .part file
        final_path = None  # set only when this task reserved the final file
        try:
            if not row['url']:
                raise DownloadError('no url')

            session = self._get_http_session()

            target_dir = self._target_dir(jid)
            base = self._base_name(row)
            ext = safe_extension(row['file_name'])

            self._set_state(file_transfer_id, FT_STATE_TRANSFERRING)
            self._refresh_chat(jid)

            candidate = target_dir / f'{base}{ext}{PART_SUFFIX}'
            fd = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            part_path = candidate

            if self.logger:
                self.logger.info(f"Downloading file {file_transfer_id} to {part_path}")

            with os.fdopen(fd, 'wb') as out:
                size = await download_to_file(session, row['url'], out, max_bytes)

            # The row may be gone (chat or account deleted) while we downloaded
            if not self._row_exists(file_transfer_id):
                if self.logger:
                    self.logger.info(f"File {file_transfer_id} was deleted during the download")
                return

            final_path = self._reserve_final_path(target_dir, base, ext)
            os.replace(part_path, final_path)
            part_path = None
            _chmod_secure(final_path, 0o600)

            mime_type = row['mime_type'] or mimetypes.guess_type(final_path.name)[0]
            self.db.execute(
                "UPDATE file_transfer SET path = ?, size = ?, mime_type = ? WHERE id = ?",
                (str(final_path), size, mime_type, file_transfer_id)
            )
            self._set_state(file_transfer_id, FT_STATE_COMPLETE)
            final_path = None  # the file belongs to the row now

            if self.logger:
                self.logger.info(f"File {file_transfer_id} downloaded ({size} bytes)")

        except DownloadError as e:
            if self.logger:
                self.logger.warning(f"Download of file {file_transfer_id} failed: {e.reason} ({e})")
            self._set_state(file_transfer_id, FT_STATE_FAILED, e.reason)
        except asyncio.CancelledError:
            self._set_state(file_transfer_id, FT_STATE_FAILED, 'interrupted')
            raise
        except OSError as e:
            if self.logger:
                self.logger.error(f"Download of file {file_transfer_id} failed: {e}")
            self._set_state(file_transfer_id, FT_STATE_FAILED, 'file error')
        except Exception as e:
            if self.logger:
                self.logger.error(f"Download of file {file_transfer_id} failed: {e}")
            self._set_state(file_transfer_id, FT_STATE_FAILED, 'error')
        finally:
            # Delete only files this task created and did not hand over to the row
            for path in (part_path, final_path):
                if path is None:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    if self.logger:
                        self.logger.warning(f"Failed to delete {path}: {e}")
            self._refresh_chat(jid)

    def recover_interrupted_downloads(self):
        """
        Clean up downloads that did not finish (app closed or crashed).

        Download rows in state 1 that do not run now get state 3 with reason 'interrupted'.
        .part files in the download dir are deleted when no download runs.
        """
        try:
            # Downloads have no path until they are complete. Uploads (state 1 too) have a path.
            rows = self.db.fetchall("""
                SELECT id FROM file_transfer
                WHERE account_id = ? AND state = ? AND (path IS NULL OR path = '')
            """, (self.account_id, FT_STATE_TRANSFERRING))
            for row in rows:
                if row['id'] not in self._downloads:
                    self._set_state(row['id'], FT_STATE_FAILED, 'interrupted')
            if rows and self.logger:
                self.logger.info(f"Marked {len(rows)} interrupted download(s) as failed")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to reset interrupted downloads: {e}")

        # Only clean up when nothing runs, so we never delete a live .part file
        if self._downloads:
            return
        try:
            account_dir = self._account_dir()
            if account_dir.is_dir():
                for part in account_dir.rglob('*' + PART_SUFFIX):
                    if part.is_file() and not part.is_symlink():
                        part.unlink()
                        if self.logger:
                            self.logger.debug(f"Deleted leftover {part}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to delete leftover .part files: {e}")
