#!/usr/bin/env python3
"""
Offline tests for received file downloads (attachment download leak fix).

Covers: trusted-sender rule, https-only and redirect check, size limit while
streaming, aesgcm stream decryption (12- and 16-byte IV), proxy connector
choice (no fallback to a direct connection).

No network: HTTP responses are fakes.

Run with: <venv>/bin/python -m unittest tests/test_attachment_download.py
"""

import os
import sys
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
from yarl import URL
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from drunk_xmpp import http_download as hd
from drunk_xmpp.http_download import DownloadError
from siproxylin.utils import download_policy as dp

REPO_TMP = Path(__file__).parent.parent / 'tmp'


def temp_dir():
    """Temp dir in the repo tmp/ when it exists."""
    return tempfile.TemporaryDirectory(dir=str(REPO_TMP) if REPO_TMP.is_dir() else None)


async def chunks_of(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i:i + size]


class FakeContent:
    def __init__(self, data: bytes):
        self.data = data

    def iter_chunked(self, size):
        return chunks_of(self.data, size)


class FakeResponse:
    def __init__(self, url, status=200, data=b'', headers=None, content_length='auto'):
        self.url = URL(url)
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(data)
        self.content_length = len(data) if content_length == 'auto' else content_length
        self.released = False

    def release(self):
        self.released = True


class FakeSession:
    """session.get() returns the response registered for the URL."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, allow_redirects=True):
        assert allow_redirects is False, "redirects must be followed by hand"
        self.calls.append(str(url))
        return self.responses[str(url)]


def run(coro):
    return asyncio.run(coro)


# =============================================================================
# Trusted sender rule
# =============================================================================

def roster(blocked=0, to=0, frm=0, ask=0):
    return {'blocked': blocked, 'we_see_their_presence': to,
            'they_see_our_presence': frm, 'we_requested_subscription': ask}


class TestTrustedSender(unittest.TestCase):

    def test_no_roster_row(self):
        self.assertFalse(dp.roster_row_is_trusted(None))

    def test_subscription_either_direction(self):
        self.assertTrue(dp.roster_row_is_trusted(roster(to=1)))
        self.assertTrue(dp.roster_row_is_trusted(roster(frm=1)))
        self.assertTrue(dp.roster_row_is_trusted(roster(to=1, frm=1)))

    def test_own_pending_request(self):
        self.assertTrue(dp.roster_row_is_trusted(roster(ask=1)))

    def test_no_subscription(self):
        self.assertFalse(dp.roster_row_is_trusted(roster()))

    def test_blocked(self):
        self.assertFalse(dp.roster_row_is_trusted(roster(blocked=1, to=1, frm=1)))

    def test_is_trusted_sender_with_db(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE roster (
            account_id INTEGER, jid_id INTEGER, blocked INTEGER DEFAULT 0,
            we_see_their_presence INTEGER DEFAULT 0, they_see_our_presence INTEGER DEFAULT 0,
            we_requested_subscription INTEGER DEFAULT 0, they_requested_subscription INTEGER DEFAULT 0)""")
        conn.execute("INSERT INTO roster (account_id, jid_id, they_see_our_presence) VALUES (1, 10, 1)")
        conn.execute("INSERT INTO roster (account_id, jid_id, they_requested_subscription) VALUES (1, 11, 1)")

        class Db:
            def fetchone(self, q, p=()):
                return conn.execute(q, p).fetchone()

        self.assertTrue(dp.is_trusted_sender(Db(), 1, 10))
        # Only their request to us: not trusted
        self.assertFalse(dp.is_trusted_sender(Db(), 1, 11))
        # Other account, no row
        self.assertFalse(dp.is_trusted_sender(Db(), 2, 10))

    def test_may_auto_download(self):
        self.assertTrue(dp.may_auto_download(1, is_muc=False, trusted=False))
        self.assertTrue(dp.may_auto_download(0, is_muc=False, trusted=True))
        self.assertFalse(dp.may_auto_download(0, is_muc=False, trusted=False))
        # Group chats: never, also own files
        self.assertFalse(dp.may_auto_download(0, is_muc=True, trusted=True))
        self.assertFalse(dp.may_auto_download(1, is_muc=True, trusted=True))

    def test_safe_names(self):
        self.assertEqual(dp.safe_dir_name('..'), '_')
        self.assertNotIn('/', dp.safe_dir_name('a/../b'))
        self.assertEqual(dp.safe_extension('photo.JPG'), '.jpg')
        self.assertEqual(dp.safe_extension('x.tar/../../y'), '.bin')
        self.assertEqual(dp.safe_extension('noext'), '.bin')


# =============================================================================
# https only and redirects
# =============================================================================

class TestHttpsOnly(unittest.TestCase):

    def test_check_https(self):
        hd.check_https('https://example.org/a.jpg')
        for bad in ('http://example.org/a.jpg', 'ftp://example.org/a', 'file:///etc/passwd', 'https:///x'):
            with self.assertRaises(DownloadError) as cm:
                hd.check_https(bad)
            self.assertEqual(cm.exception.reason, 'not https')

    def test_parse_plain(self):
        url, key, iv = hd.parse_file_url('https://example.org/a.jpg')
        self.assertEqual(url, 'https://example.org/a.jpg')
        self.assertIsNone(key)
        with self.assertRaises(DownloadError):
            hd.parse_file_url('http://example.org/a.jpg')

    def test_parse_aesgcm(self):
        iv, key = os.urandom(12), os.urandom(32)
        url, k, i = hd.parse_file_url(f'aesgcm://example.org/a.jpg#{(iv + key).hex()}')
        self.assertEqual(url, 'https://example.org/a.jpg')
        self.assertEqual((k, i), (key, iv))
        iv16 = os.urandom(16)
        _, k, i = hd.parse_file_url(f'aesgcm://example.org/a.jpg#{(iv16 + key).hex()}')
        self.assertEqual((k, i), (key, iv16))
        for frag in ('', 'zz' * 44, 'ab' * 50):
            with self.assertRaises(DownloadError):
                hd.parse_file_url(f'aesgcm://example.org/a.jpg#{frag}')

    def test_redirect_to_https_is_followed(self):
        session = FakeSession({
            'https://a.example/f': FakeResponse('https://a.example/f', 302, headers={'Location': '/g'}),
            'https://a.example/g': FakeResponse('https://a.example/g', 200, b'ok'),
        })
        resp = run(hd.open_https_response(session, 'https://a.example/f'))
        self.assertEqual(resp.status, 200)
        self.assertEqual(session.calls, ['https://a.example/f', 'https://a.example/g'])

    def test_redirect_to_http_is_refused(self):
        session = FakeSession({
            'https://a.example/f': FakeResponse('https://a.example/f', 301,
                                                headers={'Location': 'http://b.example/f'}),
        })
        with self.assertRaises(DownloadError) as cm:
            run(hd.open_https_response(session, 'https://a.example/f'))
        self.assertEqual(cm.exception.reason, 'not https')
        self.assertEqual(session.calls, ['https://a.example/f'])

    def test_too_many_redirects(self):
        responses = {}
        for n in range(6):
            responses[f'https://a.example/{n}'] = FakeResponse(
                f'https://a.example/{n}', 302, headers={'Location': f'/{n + 1}'})
        session = FakeSession(responses)
        with self.assertRaises(DownloadError) as cm:
            run(hd.open_https_response(session, 'https://a.example/0', max_redirects=3))
        self.assertEqual(cm.exception.reason, 'too many redirects')
        self.assertEqual(len(session.calls), 4)

    def test_http_error_status(self):
        session = FakeSession({'https://a.example/f': FakeResponse('https://a.example/f', 404)})
        with self.assertRaises(DownloadError) as cm:
            run(hd.open_https_response(session, 'https://a.example/f'))
        self.assertEqual(cm.exception.reason, 'http 404')


# =============================================================================
# Size limit
# =============================================================================

class TestSizeLimit(unittest.TestCase):

    def test_limit_while_streaming(self):
        with temp_dir() as d:
            with open(Path(d) / 'f.part', 'wb') as out:
                with self.assertRaises(DownloadError) as cm:
                    run(hd.stream_to_file(chunks_of(b'x' * 1000, 100), out, max_bytes=999))
            self.assertEqual(cm.exception.reason, 'too large')

    def test_exact_limit_ok(self):
        with temp_dir() as d:
            part = Path(d) / 'f.part'
            with open(part, 'wb') as out:
                size = run(hd.stream_to_file(chunks_of(b'x' * 1000, 64), out, max_bytes=1000))
            self.assertEqual(size, 1000)
            self.assertEqual(part.read_bytes(), b'x' * 1000)

    def test_content_length_checked_first(self):
        resp = FakeResponse('https://a.example/f', 200, b'x' * 10, content_length=10 ** 9)
        session = FakeSession({'https://a.example/f': resp})
        with temp_dir() as d:
            part = Path(d) / 'f.part'
            with open(part, 'wb') as out:
                with self.assertRaises(DownloadError) as cm:
                    run(hd.download_to_file(session, 'https://a.example/f', out, max_bytes=100))
            self.assertEqual(cm.exception.reason, 'too large')
            self.assertEqual(part.read_bytes(), b'')
            self.assertTrue(resp.released)

    def test_lying_content_length(self):
        # Server says 10 bytes but sends more: the stream count stops it
        resp = FakeResponse('https://a.example/f', 200, b'x' * 500, content_length=10)
        session = FakeSession({'https://a.example/f': resp})
        with temp_dir() as d:
            with open(Path(d) / 'f.part', 'wb') as out:
                with self.assertRaises(DownloadError) as cm:
                    run(hd.download_to_file(session, 'https://a.example/f', out, max_bytes=100))
            self.assertEqual(cm.exception.reason, 'too large')

    def test_limits(self):
        self.assertEqual(dp.AUTO_DOWNLOAD_MAX_BYTES, 25 * 1024 * 1024)
        self.assertEqual(dp.MANUAL_DOWNLOAD_MAX_BYTES, 256 * 1024 * 1024)


# =============================================================================
# aesgcm stream decryption
# =============================================================================

class TestAesGcm(unittest.TestCase):

    def _roundtrip(self, iv_len, chunk_size):
        key, iv = os.urandom(32), os.urandom(iv_len)
        plain = os.urandom(5000)
        body = AESGCM(key).encrypt(iv, plain, None)  # ciphertext + 16-byte tag
        url = f'aesgcm://a.example/f.jpg#{(iv + key).hex()}'
        resp = FakeResponse('https://a.example/f.jpg', 200, body)
        session = FakeSession({'https://a.example/f.jpg': resp})

        # Use a small chunk size to test tag handling across chunk borders
        with mock.patch.object(hd, 'CHUNK_SIZE', chunk_size), temp_dir() as d:
            part = Path(d) / 'f.part'
            with open(part, 'wb') as out:
                size = run(hd.download_to_file(session, url, out, max_bytes=len(plain)))
            self.assertEqual(size, len(plain))
            self.assertEqual(part.read_bytes(), plain)

    def test_iv_12(self):
        for chunk in (1, 7, 16, 17, 4096):
            self._roundtrip(12, chunk)

    def test_iv_16(self):
        for chunk in (1, 15, 16, 33, 4096):
            self._roundtrip(16, chunk)

    def test_bad_tag(self):
        key, iv = os.urandom(32), os.urandom(12)
        body = bytearray(AESGCM(key).encrypt(iv, b'hello world', None))
        body[-1] ^= 1
        dec = hd.AesGcmStreamDecryptor(key, iv)
        dec.update(bytes(body))
        with self.assertRaises(DownloadError) as cm:
            dec.finalize()
        self.assertEqual(cm.exception.reason, 'decryption failed')

    def test_too_short(self):
        dec = hd.AesGcmStreamDecryptor(os.urandom(32), os.urandom(12))
        dec.update(b'short')
        with self.assertRaises(DownloadError):
            dec.finalize()


# =============================================================================
# Proxy connector choice
# =============================================================================

class TestProxyConnector(unittest.TestCase):

    def test_no_proxy_plain_connector(self):
        async def body():
            from aiohttp_socks import ProxyConnector
            c = hd.build_connector(None)
            self.assertIsInstance(c, aiohttp.TCPConnector)
            self.assertNotIsInstance(c, ProxyConnector)
            await c.close()
        run(body())

    def test_socks5_remote_dns(self):
        async def body():
            from aiohttp_socks import ProxyConnector, ProxyType
            c = hd.build_connector('SOCKS5', '127.0.0.1', 9050, 'u', 'p:@/x')
            self.assertIsInstance(c, ProxyConnector)
            self.assertEqual(c._proxy_type, ProxyType.SOCKS5)
            self.assertTrue(c._rdns)
            self.assertEqual(c._proxy_password, 'p:@/x')
            await c.close()
        run(body())

    def test_http_proxy(self):
        async def body():
            from aiohttp_socks import ProxyConnector, ProxyType
            c = hd.build_connector('http', 'proxy.local', '3128')
            self.assertIsInstance(c, ProxyConnector)
            self.assertEqual(c._proxy_type, ProxyType.HTTP)
            await c.close()
        run(body())

    def test_bad_proxy_no_fallback(self):
        cases = [
            ('SOCKS4', '127.0.0.1', 9050),
            ('SOCKS5', None, 9050),
            ('SOCKS5', '127.0.0.1', None),
            ('SOCKS5', '127.0.0.1', 'abc'),
            ('SOCKS5', '127.0.0.1', 70000),
        ]
        for args in cases:
            with self.assertRaises(DownloadError) as cm:
                hd.build_connector(*args)
            self.assertEqual(cm.exception.reason, 'proxy error')

    def test_missing_package_no_fallback(self):
        with mock.patch.dict(sys.modules, {'aiohttp_socks': None}):
            with self.assertRaises(DownloadError) as cm:
                hd.build_connector('SOCKS5', '127.0.0.1', 9050)
            self.assertEqual(cm.exception.reason, 'proxy error')

    def test_make_session(self):
        async def body():
            from aiohttp_socks import ProxyConnector
            s1 = hd.make_session('SOCKS5', '127.0.0.1', 9050)
            self.assertIsInstance(s1.connector, ProxyConnector)
            self.assertTrue(s1.connector._rdns)
            self.assertIsInstance(s1.cookie_jar, aiohttp.DummyCookieJar)
            await s1.close()
            s2 = hd.make_session()
            self.assertNotIsInstance(s2.connector, ProxyConnector)
            await s2.close()
            with self.assertRaises(DownloadError):
                hd.make_session('SOCKS4', '127.0.0.1', 9050)
        run(body())

    def test_proxy_fields_from_account(self):
        import base64
        self.assertEqual(dp.proxy_fields_from_account({'proxy_type': None}), {'proxy_type': None})
        fields = dp.proxy_fields_from_account({
            'proxy_type': 'SOCKS5', 'proxy_host': 'h', 'proxy_port': 9050,
            'proxy_username': 'u', 'proxy_password': base64.b64encode(b'pw').decode()})
        self.assertEqual(fields['proxy_password'], 'pw')
        self.assertEqual(fields['proxy_host'], 'h')
        with self.assertRaises(DownloadError):
            dp.proxy_fields_from_account({'proxy_type': 'SOCKS5', 'proxy_password': '%%%'})
        with self.assertRaises(DownloadError):
            dp.proxy_fields_from_account(None)



# =============================================================================
# FileBarrel (download flow, name clash, current proxy, cancel)
# =============================================================================

def load_file_barrel():
    """
    Import siproxylin.core.barrels.files. Without PySide6 or grpc the package
    siproxylin.core cannot load, so we load the module from its file.
    """
    try:
        from siproxylin.core.barrels import files
        return files
    except (ImportError, NameError):
        # NameError: siproxylin.core.brewery without grpc (call service modules)
        pass
    import types
    import importlib.util
    root = Path(__file__).parent.parent
    for name in ('siproxylin.core', 'siproxylin.core.barrels'):
        if name not in sys.modules or not hasattr(sys.modules[name], '__path__'):
            mod = types.ModuleType(name)
            mod.__path__ = [str(root / name.replace('.', '/'))]
            sys.modules[name] = mod
    spec = importlib.util.spec_from_file_location(
        'siproxylin.core.barrels.files', str(root / 'siproxylin/core/barrels/files.py'))
    files = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = files
    spec.loader.exec_module(files)
    return files


class FakeSignal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class GatedContent:
    """Sends the first chunk, then waits for the gate before the rest."""

    def __init__(self, data: bytes, gate: asyncio.Event):
        self.data = data
        self.gate = gate

    def iter_chunked(self, size):
        async def gen():
            yield self.data[:1]
            await self.gate.wait()
            yield self.data[1:]
        return gen()


class ClosableSession(FakeSession):
    def __init__(self, responses):
        super().__init__(responses)
        self.closed = False

    async def close(self):
        self.closed = True


class TestFileBarrel(unittest.TestCase):

    def setUp(self):
        from siproxylin.db.database import Database
        self.files = load_file_barrel()
        self.tmp = temp_dir()
        self.dir = Path(self.tmp.name)
        self.db = Database(db_path=self.dir / 't.db')
        schema = (Path(__file__).parent.parent / 'siproxylin/db/schema.sql').read_text()
        self.db.connection.executescript(schema)
        self.db.execute("INSERT INTO account (id, bare_jid, enabled) VALUES (1, 'me@x', 1)")
        self.db.execute("INSERT INTO jid (id, bare_jid) VALUES (5, 'bob@x')")
        self.db.commit()
        self.conv = self.db.get_or_create_conversation(1, 5, 0)
        self.account_data = {'proxy_type': None}
        self.signal = FakeSignal()
        self.att = self.dir / 'att'
        patcher = mock.patch.object(self.files.FileBarrel, '_account_dir', lambda _self: self.att)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sessions = []
        self.responses = {}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def fake_make_session(self, **fields):
        session = ClosableSession(self.responses)
        session.fields = fields
        self.sessions.append(session)
        return session

    def barrel(self):
        # client stays None: downloads must not need a connected client
        return self.files.FileBarrel(1, None, self.db, None,
                                     {'message_received': self.signal}, self.account_data)

    def row(self, ft_id):
        r = self.db.fetchone("SELECT state, path, info FROM file_transfer WHERE id = ?", (ft_id,))
        return dict(r) if r else None

    async def add(self, fb, url, ts=1700000000, sid=None, auto=True):
        return await fb.handle_incoming_file(5, 'bob@x', url, False, ts, self.conv,
                                             stanza_id=sid or url, auto_download=auto)

    async def settle(self, fb):
        for _ in range(50):
            if not fb._downloads:
                return
            await asyncio.sleep(0.01)

    def test_same_second_no_name_clash(self):
        self.responses = {
            'https://h/a.jpg': FakeResponse('https://h/a.jpg', 200, b'A'),
            'https://h/b.jpg': FakeResponse('https://h/b.jpg', 200, b'B'),
        }

        async def body():
            with mock.patch.object(hd, 'make_session', self.fake_make_session):
                fb = self.barrel()
                # A file that is not ours already has the name of the first row
                d = self.att / 'bob@x'
                d.mkdir(parents=True)
                stamp = datetime_stamp(1700000000)
                (d / f'{stamp}_1.jpg').write_bytes(b'OLD')
                a = await self.add(fb, 'https://h/a.jpg')
                b = await self.add(fb, 'https://h/b.jpg')
                await self.settle(fb)
                ra, rb = self.row(a), self.row(b)
                self.assertEqual((ra['state'], rb['state']), (2, 2))
                self.assertNotEqual(ra['path'], rb['path'])
                self.assertTrue(Path(ra['path']).name.startswith(f'{stamp}_{a}'))
                self.assertTrue(Path(rb['path']).name.startswith(f'{stamp}_{b}'))
                self.assertEqual(Path(ra['path']).read_bytes(), b'A')
                self.assertEqual(Path(rb['path']).read_bytes(), b'B')
                self.assertEqual((d / f'{stamp}_1.jpg').read_bytes(), b'OLD')
                self.assertEqual(list(d.glob('*.part')), [])
        run(body())

    def test_foreign_part_file_not_deleted(self):
        self.responses = {'https://h/a.jpg': FakeResponse('https://h/a.jpg', 200, b'A')}

        async def body():
            with mock.patch.object(hd, 'make_session', self.fake_make_session):
                fb = self.barrel()
                d = self.att / 'bob@x'
                d.mkdir(parents=True)
                part = d / f'{datetime_stamp(1700000000)}_1.jpg.part'
                part.write_bytes(b'not ours')
                a = await self.add(fb, 'https://h/a.jpg')
                await self.settle(fb)
                self.assertEqual(self.row(a)['state'], 3)
                self.assertEqual(part.read_bytes(), b'not ours')
        run(body())

    def test_uses_current_proxy_settings(self):
        self.responses = {
            'https://h/a.jpg': FakeResponse('https://h/a.jpg', 200, b'A'),
            'https://h/b.jpg': FakeResponse('https://h/b.jpg', 200, b'B'),
        }

        async def body():
            with mock.patch.object(hd, 'make_session', self.fake_make_session):
                fb = self.barrel()
                await self.add(fb, 'https://h/a.jpg')
                await self.settle(fb)
                # Settings change in place (like reload_and_reconnect)
                self.account_data.update({'proxy_type': 'SOCKS5', 'proxy_host': '127.0.0.1',
                                          'proxy_port': 9050})
                await self.add(fb, 'https://h/b.jpg')
                await self.settle(fb)
                self.assertEqual(len(self.sessions), 2)
                self.assertIsNone(self.sessions[0].fields['proxy_type'])
                self.assertEqual(self.sessions[1].fields['proxy_type'], 'SOCKS5')
                await asyncio.sleep(0)
                self.assertTrue(self.sessions[0].closed)
                fb.cancel_all()
                await asyncio.sleep(0)
                self.assertTrue(self.sessions[1].closed)
        run(body())

    def test_bad_proxy_fails_no_direct(self):
        self.account_data.update({'proxy_type': 'SOCKS4', 'proxy_host': 'h', 'proxy_port': 1})

        async def body():
            fb = self.barrel()
            a = await self.add(fb, 'https://h/a.jpg')
            await self.settle(fb)
            r = self.row(a)
            self.assertEqual(r['state'], 3)
            self.assertIn('"download_error": "proxy error"', r['info'])
        run(body())

    def test_cancel_all(self):
        async def body():
            g = asyncio.Event()
            resp = FakeResponse('https://h/a.jpg', 200, b'AB')
            resp.content = GatedContent(b'AB', g)
            self.responses = {'https://h/a.jpg': resp}
            with mock.patch.object(hd, 'make_session', self.fake_make_session):
                fb = self.barrel()
                a = await self.add(fb, 'https://h/a.jpg')
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if self.row(a)['state'] == 1:
                        break
                self.assertEqual(self.row(a)['state'], 1)
                fb.cancel_all()
                await asyncio.sleep(0.05)
                r = self.row(a)
                self.assertEqual(r['state'], 3)
                self.assertIn('interrupted', r['info'])
                self.assertEqual(list((self.att / 'bob@x').glob('*')), [])
                self.assertTrue(self.sessions[0].closed)
                self.assertEqual(fb._downloads, {})
        run(body())

    def test_disabled_account(self):
        self.responses = {'https://h/a.jpg': FakeResponse('https://h/a.jpg', 200, b'A')}

        async def body():
            with mock.patch.object(hd, 'make_session', self.fake_make_session):
                fb = self.barrel()
                a = await self.add(fb, 'https://h/a.jpg', auto=False)
                self.db.execute("UPDATE account SET enabled = 0 WHERE id = 1")
                self.db.commit()
                fb.request_download(a)
                self.assertEqual(fb._downloads, {})
                # A queued task checks the account before it starts
                fb._schedule_download(a, 100)
                await self.settle(fb)
                self.assertEqual(self.row(a)['state'], 0)
                self.assertEqual(self.sessions, [])
        run(body())

    def test_row_deleted_during_download(self):
        async def body():
            g = asyncio.Event()
            resp = FakeResponse('https://h/a.jpg', 200, b'AB')
            resp.content = GatedContent(b'AB', g)
            self.responses = {'https://h/a.jpg': resp}
            with mock.patch.object(hd, 'make_session', self.fake_make_session):
                fb = self.barrel()
                a = await self.add(fb, 'https://h/a.jpg')
                await asyncio.sleep(0.05)
                self.db.execute("DELETE FROM file_transfer WHERE id = ?", (a,))
                self.db.commit()
                g.set()
                await self.settle(fb)
                self.assertIsNone(self.row(a))
                self.assertEqual(list((self.att / 'bob@x').glob('*')), [])
        run(body())


def datetime_stamp(ts: int) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d_%H%M%S')

if __name__ == '__main__':
    unittest.main()
