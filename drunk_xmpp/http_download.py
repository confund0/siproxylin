"""
HTTP download helpers for received file attachments.

- Build the aiohttp connector from the account proxy fields (no local DNS).
- Allow only https URLs, also for redirect targets.
- Stream the body to a file with a size limit.
- Decrypt aesgcm:// files (XEP-0454) while streaming.

No Qt and no database code here, so the tests can import this module.
"""

from typing import Optional, Tuple

import aiohttp
from yarl import URL

# Timeouts in seconds. Downloads over Tor are slow, so the total is large.
HTTP_TIMEOUT_TOTAL = 1800
HTTP_TIMEOUT_CONNECT = 60
HTTP_TIMEOUT_READ = 60

# Most redirects we follow for one download
HTTP_MAX_REDIRECTS = 3

# Length of the AES-GCM auth tag at the end of an aesgcm file
AESGCM_TAG_SIZE = 16

CHUNK_SIZE = 64 * 1024

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class DownloadError(Exception):
    """Download failed. `reason` is a short text for the user."""

    def __init__(self, reason: str, detail: Optional[str] = None):
        super().__init__(detail or reason)
        self.reason = reason


def build_connector(proxy_type: Optional[str], proxy_host: Optional[str] = None,
                    proxy_port: Optional[int] = None, proxy_username: Optional[str] = None,
                    proxy_password: Optional[str] = None):
    """
    Build the aiohttp connector for the account.

    No proxy type -> plain aiohttp connector.
    Proxy type set -> aiohttp_socks ProxyConnector with remote DNS.
    A proxy that cannot be used raises DownloadError('proxy error').
    There is never a fallback to a direct connection.
    """
    if not proxy_type:
        return aiohttp.TCPConnector()

    try:
        from aiohttp_socks import ProxyConnector, ProxyType
    except ImportError:
        raise DownloadError('proxy error', 'aiohttp-socks is not installed')

    types = {'SOCKS5': ProxyType.SOCKS5, 'HTTP': ProxyType.HTTP}
    ptype = types.get(str(proxy_type).upper())
    if ptype is None:
        raise DownloadError('proxy error', f'unknown proxy type: {proxy_type}')

    try:
        port = int(proxy_port)
    except (TypeError, ValueError):
        raise DownloadError('proxy error', 'proxy port is missing')
    if not proxy_host or not (0 < port < 65536):
        raise DownloadError('proxy error', 'proxy host or port is missing')

    return ProxyConnector(
        proxy_type=ptype,
        host=proxy_host,
        port=port,
        username=proxy_username or None,
        password=proxy_password or None,
        rdns=True,
    )


def make_session(proxy_type: Optional[str] = None, proxy_host: Optional[str] = None,
                 proxy_port: Optional[int] = None, proxy_username: Optional[str] = None,
                 proxy_password: Optional[str] = None):
    """
    Make an aiohttp session for file downloads.

    Uses build_connector() (proxy with remote DNS, or plain without proxy),
    timeouts, no cookies and no proxy from the environment.
    Must be called inside the running event loop.
    """
    connector = build_connector(proxy_type, proxy_host, proxy_port, proxy_username, proxy_password)
    timeout = aiohttp.ClientTimeout(
        total=HTTP_TIMEOUT_TOTAL,
        connect=HTTP_TIMEOUT_CONNECT,
        sock_connect=HTTP_TIMEOUT_CONNECT,
        sock_read=HTTP_TIMEOUT_READ,
    )
    return aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
    )


def check_https(url) -> URL:
    """Return the URL as yarl.URL, or raise DownloadError('not https')."""
    u = url if isinstance(url, URL) else URL(str(url))
    if u.scheme != 'https' or not u.host:
        raise DownloadError('not https', f'refused URL scheme: {u.scheme}')
    return u


def parse_file_url(file_url: str) -> Tuple[str, Optional[bytes], Optional[bytes]]:
    """
    Split a file URL into (https_url, key, iv).

    aesgcm://host/path#<iv><key> maps to https://host/path.
    The fragment is 88 hex chars (12-byte IV) or 96 hex chars (16-byte IV),
    with a 32-byte key at the end.
    Plain https URLs give key and iv None. Other schemes raise DownloadError.
    """
    if file_url.startswith('aesgcm://'):
        rest = file_url[len('aesgcm://'):]
        if '#' not in rest:
            raise DownloadError('bad key', 'aesgcm URL has no key')
        http_part, fragment = rest.split('#', 1)
        if len(fragment) not in (88, 96):
            raise DownloadError('bad key', f'aesgcm fragment length {len(fragment)}')
        try:
            raw = bytes.fromhex(fragment)
        except ValueError:
            raise DownloadError('bad key', 'aesgcm fragment is not hex')
        iv, key = raw[:-32], raw[-32:]
        https_url = f'https://{http_part}'
        check_https(https_url)
        return https_url, key, iv

    check_https(file_url)
    return file_url, None, None


class AesGcmStreamDecryptor:
    """
    AES-256-GCM decryption for a streamed body.

    The last 16 bytes of the body are the auth tag, so we always keep the
    last 16 bytes back until finalize().
    """

    def __init__(self, key: bytes, iv: bytes):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        self._decryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).decryptor()
        self._tail = b''

    def update(self, chunk: bytes) -> bytes:
        data = self._tail + chunk
        if len(data) <= AESGCM_TAG_SIZE:
            self._tail = data
            return b''
        self._tail = data[-AESGCM_TAG_SIZE:]
        return self._decryptor.update(data[:-AESGCM_TAG_SIZE])

    def finalize(self) -> bytes:
        """Check the tag. Raises DownloadError('decryption failed') on a bad tag."""
        from cryptography.exceptions import InvalidTag
        if len(self._tail) != AESGCM_TAG_SIZE:
            raise DownloadError('decryption failed', 'file is shorter than the auth tag')
        try:
            return self._decryptor.finalize_with_tag(self._tail)
        except InvalidTag:
            raise DownloadError('decryption failed', 'bad auth tag')


async def open_https_response(session, url: str, max_redirects: int = HTTP_MAX_REDIRECTS):
    """
    GET the URL and follow redirects by hand, so every target is checked for https.

    Returns an open response with status 200. The caller must release it.
    """
    current = check_https(url)
    for _ in range(max_redirects + 1):
        response = await session.get(current, allow_redirects=False)
        if response.status in REDIRECT_STATUSES:
            location = response.headers.get('Location')
            response.release()
            if not location:
                raise DownloadError(f'http {response.status}', 'redirect without Location')
            current = check_https(response.url.join(URL(location)))
            continue
        if response.status != 200:
            response.release()
            raise DownloadError(f'http {response.status}')
        return response
    raise DownloadError('too many redirects')


async def stream_to_file(chunks, out, max_bytes: int,
                         key: Optional[bytes] = None, iv: Optional[bytes] = None) -> int:
    """
    Write an async iterator of byte chunks to the open binary file `out`.

    max_bytes limits the received bytes (for aesgcm this includes the tag).
    With key and iv the data is decrypted while streaming.
    Returns the written size. The caller opens, closes and deletes the file.
    """
    decryptor = AesGcmStreamDecryptor(key, iv) if key is not None else None
    received = 0
    written = 0
    async for chunk in chunks:
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            raise DownloadError('too large')
        data = decryptor.update(chunk) if decryptor else chunk
        if data:
            out.write(data)
            written += len(data)
    if decryptor:
        data = decryptor.finalize()
        if data:
            out.write(data)
            written += len(data)
    out.flush()
    return written


async def download_to_file(session, file_url: str, out, max_bytes: int,
                           max_redirects: int = HTTP_MAX_REDIRECTS) -> int:
    """
    Download a file URL (https:// or aesgcm://) into the open binary file `out`.

    Checks Content-Length first, then counts bytes while streaming.
    Returns the size of the written (decrypted) file.
    Raises DownloadError with a short reason on any failure.
    """
    https_url, key, iv = parse_file_url(file_url)
    limit = max_bytes + (AESGCM_TAG_SIZE if key is not None else 0)

    try:
        response = await open_https_response(session, https_url, max_redirects)
        try:
            length = response.content_length
            if length is not None and length > limit:
                raise DownloadError('too large')
            return await stream_to_file(
                response.content.iter_chunked(CHUNK_SIZE), out, limit, key, iv)
        finally:
            response.release()
    except DownloadError:
        raise
    except aiohttp.ClientProxyConnectionError as e:
        raise DownloadError('proxy error', str(e))
    except ImportError as e:
        raise DownloadError('proxy error', str(e))
    except Exception as e:
        # python-socks raises its own errors for proxy failures
        if type(e).__module__.startswith(('python_socks', 'aiohttp_socks')):
            raise DownloadError('proxy error', str(e))
        if isinstance(e, TimeoutError) or e.__class__.__name__ in ('TimeoutError', 'ServerTimeoutError'):
            raise DownloadError('timeout', str(e))
        if isinstance(e, aiohttp.ClientError):
            raise DownloadError('network error', str(e))
        if isinstance(e, OSError):
            raise DownloadError('file error', str(e))
        raise
