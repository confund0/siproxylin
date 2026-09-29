#!/usr/bin/env python3
"""
Offline tests for HTTP uploads through the account proxy (XEP-0363 patch).

Covers: the patched ClientSession uses the proxy connector with remote DNS,
a plain connector for "no proxy", an error when the proxy is not known,
an error (no direct connection) on a bad proxy, the ContextVar reset after
an upload, and the slixmpp upload_file() PUT with the patched session.

No network: session.put is a fake.

Run with: <venv>/bin/python -m unittest tests/test_upload_proxy.py
"""

import io
import sys
import asyncio
import unittest
from pathlib import Path
from unittest import mock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
from aiohttp_socks import ProxyConnector

from drunk_xmpp.http_download import DownloadError
from drunk_xmpp.slixmpp_patches import xep_0363_upload_proxy as up
from drunk_xmpp.file_uploads import FileUploadMixin
from slixmpp.plugins.xep_0363 import http_upload

SOCKS = {'proxy_type': 'SOCKS5', 'proxy_host': '127.0.0.1', 'proxy_port': 9050,
         'proxy_username': 'u', 'proxy_password': 'pw'}
NO_PROXY = {'proxy_type': None}


def run(coro):
    return asyncio.run(coro)


class TestPatchedClientSession(unittest.TestCase):

    def setUp(self):
        up.apply_patch()

    def test_patch_applied(self):
        self.assertIs(http_upload.ClientSession, up.upload_client_session)

    def test_proxy_connector(self):
        async def body():
            with up.use_upload_proxy(SOCKS):
                async with http_upload.ClientSession(headers={'User-Agent': 'x'}) as s:
                    self.assertIsInstance(s.connector, ProxyConnector)
                    self.assertTrue(s.connector._rdns)
                    self.assertEqual(s.connector._proxy_host, '127.0.0.1')
                    self.assertEqual(s.connector._proxy_password, 'pw')
                    self.assertEqual(s.headers.get('User-Agent'), 'x')
                    self.assertIsNone(s.timeout.total)
                    self.assertIsNotNone(s.timeout.connect)
                    self.assertIsNotNone(s.timeout.sock_read)
                    self.assertFalse(s.trust_env)
        run(body())

    def test_no_proxy_plain_connector(self):
        async def body():
            with up.use_upload_proxy(NO_PROXY):
                async with http_upload.ClientSession(headers={}) as s:
                    self.assertIsInstance(s.connector, aiohttp.TCPConnector)
                    self.assertNotIsInstance(s.connector, ProxyConnector)
        run(body())

    def test_unset_raises(self):
        async def body():
            with self.assertRaises(up.UploadProxyError):
                http_upload.ClientSession(headers={})
        run(body())

    def test_bad_proxy_no_fallback(self):
        async def body():
            bad = dict(SOCKS, proxy_host=None)
            with up.use_upload_proxy(bad):
                with self.assertRaises(DownloadError) as cm:
                    http_upload.ClientSession(headers={})
            self.assertEqual(cm.exception.reason, 'proxy error')
            with up.use_upload_proxy(dict(SOCKS, proxy_type='SOCKS4')):
                with self.assertRaises(DownloadError):
                    http_upload.ClientSession(headers={})
        run(body())

    def test_patch_skipped_without_client_session(self):
        fake = mock.MagicMock(spec=[])
        with mock.patch.dict(sys.modules, {'slixmpp.plugins.xep_0363.http_upload': fake}):
            with mock.patch('slixmpp.plugins.xep_0363.http_upload', fake, create=True):
                with self.assertLogs(up.log, level='WARNING'):
                    up.apply_patch()
        self.assertFalse(hasattr(fake, 'ClientSession'))


class FakeResponse:
    status = 201

    async def text(self):
        return ''

    def close(self):
        pass


class TestSlixmppUploadFile(unittest.TestCase):
    """Run slixmpp's XEP_0363.upload_file() with a fake slot and a fake PUT."""

    def setUp(self):
        up.apply_patch()

    def make_plugin(self):
        plugin = object.__new__(http_upload.XEP_0363)
        object.__setattr__(plugin, 'config', {})
        plugin.upload_service = 'upload.example'
        plugin.max_file_size = float('+inf')
        plugin.default_content_type = 'application/octet-stream'
        plugin._upload_service_purposes = None
        slot = {'http_upload_slot': {
            'put': {'url': 'https://upload.example/put', 'headers': []},
            'get': {'url': 'https://upload.example/get'},
        }}
        plugin.request_slot = mock.AsyncMock(return_value=slot)
        return plugin

    def test_put_uses_proxy_session(self):
        seen = []

        async def fake_put(session, url, data=None, headers=None):
            seen.append((session.connector, url, data.read()))
            return FakeResponse()

        async def body():
            plugin = self.make_plugin()
            with mock.patch.object(aiohttp.ClientSession, 'put', fake_put):
                with up.use_upload_proxy(SOCKS):
                    url = await plugin.upload_file('a.bin', input_file=io.BytesIO(b'data'))
            self.assertEqual(url, 'https://upload.example/get')
            self.assertEqual(len(seen), 1)
            self.assertIsInstance(seen[0][0], ProxyConnector)
            self.assertEqual(seen[0][1:], ('https://upload.example/put', b'data'))
        run(body())

    def test_no_put_without_proxy_fields(self):
        async def body():
            plugin = self.make_plugin()
            put = mock.AsyncMock()
            with mock.patch.object(aiohttp.ClientSession, 'put', put):
                with self.assertRaises(up.UploadProxyError):
                    await plugin.upload_file('a.bin', input_file=io.BytesIO(b'data'))
            put.assert_not_called()
        run(body())


class FakeClient(FileUploadMixin):
    """FileUploadMixin with fake plugins. Records the ContextVar during the upload."""

    def __init__(self, fields):
        self.http_proxy_fields = fields
        self.logger = mock.MagicMock()
        self.seen = []
        self.fail = False
        self.plugins = {'xep_0363': mock.MagicMock(), 'xep_0454': mock.MagicMock()}
        self.plugins['xep_0363'].upload_file = self._fake_upload
        self.plugins['xep_0454'].upload_file = self._fake_upload
        self.send_encrypted_private_message = mock.AsyncMock(return_value='id1')

    def __getitem__(self, name):
        return self.plugins[name]

    async def _fake_upload(self, *args, **kwargs):
        self.seen.append(up.upload_proxy_fields.get())
        if self.fail:
            raise RuntimeError('upload failed')
        return 'https://upload.example/get'


class TestFileUploadMixin(unittest.TestCase):

    def setUp(self):
        self.file = Path(__file__)

    def assert_unset(self):
        with self.assertRaises(LookupError):
            up.upload_proxy_fields.get()

    def test_plain_upload_sets_and_resets(self):
        client = FakeClient(SOCKS)
        run(client.upload_file(str(self.file)))
        self.assertEqual(client.seen, [SOCKS])
        self.assert_unset()

    def test_encrypted_upload_sets_and_resets(self):
        client = FakeClient(NO_PROXY)
        run(client.send_encrypted_file('bob@example', str(self.file)))
        self.assertEqual(client.seen, [NO_PROXY])
        self.assert_unset()

    def test_reset_after_error(self):
        client = FakeClient(SOCKS)
        client.fail = True
        with self.assertRaises(RuntimeError):
            run(client.upload_file(str(self.file)))
        self.assertEqual(client.seen, [SOCKS])
        self.assert_unset()


if __name__ == '__main__':
    unittest.main()
