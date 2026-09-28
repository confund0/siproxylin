"""
Rules for received file attachments: when to download automatically, and size limits.

No Qt code here, so the tests can import this module.
"""

import os
import re
from typing import Optional

# Size limits in bytes
AUTO_DOWNLOAD_MAX_BYTES = 25 * 1024 * 1024
MANUAL_DOWNLOAD_MAX_BYTES = 256 * 1024 * 1024

# file_transfer.state values
FT_STATE_PENDING = 0
FT_STATE_TRANSFERRING = 1
FT_STATE_COMPLETE = 2
FT_STATE_FAILED = 3

# Key in file_transfer.info (JSON) for the reason of a failed download
DOWNLOAD_ERROR_KEY = 'download_error'

PART_SUFFIX = '.part'


def roster_row_is_trusted(row) -> bool:
    """
    A roster row makes a trusted sender if it is not blocked and has a
    subscription in either direction, or our own pending subscription request.
    No row (None) is not trusted.
    """
    if row is None:
        return False
    if row['blocked']:
        return False
    return bool(row['we_see_their_presence']
                or row['they_see_our_presence']
                or row['we_requested_subscription'])


def is_trusted_sender(db, account_id: int, jid_id: int) -> bool:
    """Look up the roster row of jid_id and apply roster_row_is_trusted()."""
    row = db.fetchone("""
        SELECT blocked, we_see_their_presence, they_see_our_presence,
               we_requested_subscription
        FROM roster
        WHERE account_id = ? AND jid_id = ?
    """, (account_id, jid_id))
    return roster_row_is_trusted(row)


def may_auto_download(direction: int, is_muc: bool, trusted: bool) -> bool:
    """
    Automatic download rule.

    Group chats: never. 1:1 chats: our own messages (direction 1, also from
    our other devices), or a trusted sender.
    """
    if is_muc:
        return False
    return direction == 1 or trusted


def safe_dir_name(name: str) -> str:
    """Make a JID usable as one directory name (no path separators, not '.' or '..')."""
    name = (name or '').replace('/', '_').replace('\\', '_').replace('\x00', '_')
    if name in ('', '.', '..'):
        name = '_'
    return name


def safe_extension(file_name: Optional[str]) -> str:
    """Return a short, plain file extension like '.jpg', or '.bin'."""
    _, ext = os.path.splitext(file_name or '')
    if re.fullmatch(r'\.[A-Za-z0-9]{1,10}', ext or ''):
        return ext.lower()
    return '.bin'


def proxy_fields_from_account(account_data) -> dict:
    """
    Proxy fields for http_download.make_session() from the account settings dict.

    No proxy type -> {'proxy_type': None} (direct).
    A proxy password that cannot be decoded raises DownloadError('proxy error'),
    so a bad setting never gives a direct connection.
    """
    import base64
    from drunk_xmpp.http_download import DownloadError

    if account_data is None:
        raise DownloadError('proxy error', 'no account settings')
    proxy_type = account_data.get('proxy_type')
    if not proxy_type:
        return {'proxy_type': None}

    password = None
    encoded = account_data.get('proxy_password')
    if encoded:
        try:
            password = base64.b64decode(encoded, validate=True).decode()
        except Exception:
            raise DownloadError('proxy error', 'cannot decode proxy password')

    return {
        'proxy_type': proxy_type,
        'proxy_host': account_data.get('proxy_host'),
        'proxy_port': account_data.get('proxy_port'),
        'proxy_username': account_data.get('proxy_username'),
        'proxy_password': password,
    }
