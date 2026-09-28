#!/usr/bin/env python3
"""
Offline tests for DrunkXMPP auto-reconnect after an unexpected disconnect.

No network: slixmpp's ClientXMPP.connect() is replaced by a fake, and the
'disconnected' event is fired by hand (like connection_lost() does).

Run with: <venv>/bin/python -m pytest tests/test_auto_reconnect.py -v
      or: <venv>/bin/python tests/test_auto_reconnect.py
"""

import sys
import asyncio
import unittest
from pathlib import Path
from unittest import mock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from slixmpp import ClientXMPP
from drunk_xmpp.client import DrunkXMPP

# The minimum backoff delay is 1s, so we wait a bit longer than that
WAIT = 1.3


def make_client(**kwargs) -> DrunkXMPP:
    """Create a DrunkXMPP client without OMEMO (must run inside an event loop)."""
    return DrunkXMPP(
        jid='user@example.org/test',
        password='secret',
        rooms={},
        enable_omemo=False,
        **kwargs,
    )


class FakeConnect:
    """Replaces ClientXMPP.connect(): records calls, starts a pending 'attempt'."""

    def __init__(self):
        self.calls = []

    def __call__(self, client, host=None, port=None):
        self.calls.append((host, port))
        attempt = asyncio.get_event_loop().create_future()
        client._current_connection_attempt = attempt
        return attempt


def run(coro_func):
    """Run an async test body with a fake connect(); return the fake."""
    fake = FakeConnect()

    async def body():
        with mock.patch.object(ClientXMPP, 'connect', autospec=True, side_effect=fake):
            await coro_func(fake)

    asyncio.run(body())
    return fake


def drop(client, reason="Connection reset by peer"):
    """Simulate a hard drop: slixmpp connection_lost() fires 'disconnected'."""
    client._current_connection_attempt = None
    client.transport = None
    client.event('disconnected', reason)


class AutoReconnectTests(unittest.TestCase):

    def test_hard_drop_reconnects_with_srv(self):
        async def body(fake):
            client = make_client()
            client.connect()
            self.assertEqual(fake.calls, [(None, None)])
            drop(client)
            await asyncio.sleep(WAIT)
            self.assertEqual(fake.calls, [(None, None), (None, None)])
            # Backoff counter survives the auto connect()
            self.assertEqual(client.reconnect_attempts, 1)
        run(body)

    def test_hard_drop_reconnects_with_manual_address(self):
        async def body(fake):
            client = make_client()
            client.connect(('xmpp.example.org', 5223))
            # slixmpp stores it in custom_address; the fake does not, so the
            # fallback (_manual_address) is also covered on old slixmpp versions
            drop(client)
            await asyncio.sleep(WAIT)
            self.assertEqual(fake.calls[-1], ('xmpp.example.org', 5223))
            self.assertEqual(len(fake.calls), 2)
        run(body)

    def test_user_disconnect_does_not_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client._current_connection_attempt = None
            client.disconnect(disable_auto_reconnect=True)
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
            self.assertIsNone(client._auto_reconnect_task)
        run(body)

    def test_user_disconnect_during_backoff_cancels(self):
        async def body(fake):
            client = make_client()
            client.connect()
            drop(client)
            await asyncio.sleep(0.1)
            self.assertIsNotNone(client._auto_reconnect_task)
            client.disconnect(disable_auto_reconnect=True)
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
        run(body)

    def test_user_flag_set_during_backoff_is_checked_again(self):
        async def body(fake):
            client = make_client()
            client.connect()
            drop(client)
            await asyncio.sleep(0.1)
            # Flag set without cancel: the check after the sleep must catch it
            client.user_disconnected = True
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
        run(body)

    def test_auth_failure_does_not_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client._current_connection_attempt = None
            client.event('failed_auth', {})
            await asyncio.sleep(0)
            drop(client, "auth failed")
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
            # Next manual connect() clears the flag
            client.connect()
            self.assertFalse(client._auth_failed)
        run(body)

    def test_failed_all_auth_does_not_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client.event('failed_all_auth')
            drop(client)
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
        run(body)

    def test_stream_conflict_does_not_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client.event('stream_error', {'condition': 'conflict'})
            drop(client, "conflict")
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
        run(body)

    def test_attempt_in_flight_does_not_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client.transport = None
            # connect() of the fake left a pending attempt
            client.event('disconnected', "stream closed while connecting")
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
        run(body)

    def test_transport_up_does_not_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client._current_connection_attempt = None
            client.transport = mock.MagicMock()
            client.event('disconnected', "old stream")
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 1)
            client.transport = None
        run(body)

    def test_keepalive_reconnect_connects_only_once(self):
        async def body(fake):
            client = make_client()
            client.connect()
            client._current_connection_attempt = None
            client.transport = None
            # XEP-0199 ping timeout path: slixmpp reconnect() -> connect()
            client.reconnect(0.0, "Ping timeout")
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 2)
        run(body)

    def test_backoff_is_capped_by_max_delay(self):
        async def body(fake):
            client = make_client(reconnect_max_delay=1)
            client.connect()
            client.reconnect_attempts = 10  # 2**10 s without the cap
            drop(client)
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(client.reconnect_attempts, 11)
        run(body)

    def test_manual_connect_cancels_pending_reconnect(self):
        async def body(fake):
            client = make_client()
            client.connect()
            drop(client)
            await asyncio.sleep(0.1)
            client.connect()
            await asyncio.sleep(WAIT)
            self.assertEqual(len(fake.calls), 2)
        run(body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
