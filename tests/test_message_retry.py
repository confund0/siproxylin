#!/usr/bin/env python3
"""
Offline tests: the message retry on reconnect and a send task that still runs.

A message typed just as the link dies: its send task can hang and survive
the reconnect. The retry must skip the row while the task runs. When the
task fails after that, it retries its row once. Also: a message is marked
server-acked only by the XEP-0198 ACK of its own stanza, and server ACK
entries of an old session are dropped on session end.

Run with: QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest tests/test_message_retry.py
"""

import os
import sys
import asyncio
import logging
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
# PySide6 first: patch.dict clears and refills sys.modules on exit (crash in Qt otherwise)
import PySide6.QtWidgets  # noqa: E402,F401
from drunk_xmpp.client import DrunkXMPP  # noqa: E402  (slixmpp loads its plugins later)
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    # Same block: message_manager must see the same retry handler module
    from siproxylin.gui.managers.message_manager import MessageManager
    from siproxylin.services.message_retry import get_retry_handler
logging.disable(logging.NOTSET)

ACCOUNT = 1
PEER = 'bob@example.net'
ROW_ID = 7


def pending_row():
    return {
        'id': ROW_ID, 'first_retry_attempt': None, 'counterpart_jid': PEER, 'body': 'A',
        'encryption': 0, 'type': 0, 'retry_count': 0, 'origin_id': 'temp-1', 'time': 0,
    }


class InFlightRetryTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.handler = get_retry_handler()

    def tearDown(self):
        logging.disable(logging.NOTSET)
        self.handler._in_flight.clear()
        self.handler._skipped.clear()

    def make_manager(self):
        mm = MessageManager.__new__(MessageManager)
        mm.chat_view = mock.Mock(current_account_id=ACCOUNT, current_jid=PEER)
        mm.db = mock.Mock()
        mm.db.fetchone.side_effect = [None, {'id': 1}]  # not a MUC, jid row
        mm.db.insert_message_atomic.return_value = (ROW_ID, 1)
        return mm

    async def retry(self):
        db = mock.Mock()
        db.get_pending_messages.return_value = [pending_row()]
        client = mock.Mock(rooms={})
        client.send_private_message = mock.AsyncMock(return_value='new-id')
        with mock.patch.object(self.handler, '_check_message_in_mam', mock.AsyncMock(return_value=False)):
            stats = await self.handler.retry_pending_messages_for_account(ACCOUNT, client, db)
        return stats, client

    def hanging_account(self, hang, connected):
        """Account whose send waits for the hang future. connected: the state when the send fails."""
        account = mock.Mock(account_id=ACCOUNT, **{'is_connected.side_effect': [True, connected]})
        account.client = mock.Mock(rooms={})
        account.client.send_private_message = mock.AsyncMock(return_value='new-id')

        async def send_hangs(*args):
            return await hang

        account.send_message = mock.AsyncMock(side_effect=send_hangs)
        return account

    def test_failed_send_retries_skipped_row(self):
        async def body():
            hang = asyncio.get_running_loop().create_future()
            account = self.hanging_account(hang, connected=True)
            mm = self.make_manager()
            mm.db.get_pending_messages.return_value = [pending_row()]
            mam = mock.AsyncMock(return_value=False)
            with mock.patch.object(self.handler, '_check_message_in_mam', mam):
                task = asyncio.ensure_future(mm._send_message_async(account, PEER, 'A', False))
                await asyncio.sleep(0)

                # New session: the retry skips the row
                stats, client = await self.retry()
                client.send_private_message.assert_not_called()

                # The old task fails while connected: it retries its row once on the new client
                hang.set_exception(ConnectionError('device list timeout'))
                await task
            mam.assert_awaited_once()
            account.client.send_private_message.assert_awaited_once_with(PEER, 'A')
            mm.db.increment_retry_count.assert_called_once_with(ROW_ID)
            self.assertFalse(self.handler.is_in_flight(ROW_ID))
            self.assertFalse(self.handler.take_skipped(ROW_ID))

        asyncio.run(body())

    def test_failed_send_not_skipped_or_offline_waits(self):
        async def body():
            # Not skipped: the next session's retry sends it
            hang = asyncio.get_running_loop().create_future()
            account = self.hanging_account(hang, connected=True)
            task = asyncio.ensure_future(self.make_manager()._send_message_async(account, PEER, 'A', False))
            await asyncio.sleep(0)
            hang.set_exception(ConnectionError('link dead'))
            await task
            account.client.send_private_message.assert_not_called()

            # Skipped, but the link is down again: no retry now, the mark is gone
            hang = asyncio.get_running_loop().create_future()
            account = self.hanging_account(hang, connected=False)
            task = asyncio.ensure_future(self.make_manager()._send_message_async(account, PEER, 'A', False))
            await asyncio.sleep(0)
            await self.retry()
            hang.set_exception(ConnectionError('link dead'))
            await task
            account.client.send_private_message.assert_not_called()
            self.assertFalse(self.handler.take_skipped(ROW_ID))

        asyncio.run(body())

    def test_successful_send_after_skip_no_retry(self):
        async def body():
            hang = asyncio.get_running_loop().create_future()
            account = self.hanging_account(hang, connected=True)
            mm = self.make_manager()
            mm.chat_view.track_sent_message = mock.Mock()
            task = asyncio.ensure_future(mm._send_message_async(account, PEER, 'A', False))
            await asyncio.sleep(0)
            await self.retry()
            hang.set_result('old-id')
            await task
            account.client.send_private_message.assert_not_called()
            self.assertFalse(self.handler.take_skipped(ROW_ID))

        asyncio.run(body())

    def test_retry_skips_in_flight_row_then_picks_it_up(self):
        async def body():
            hang = asyncio.get_running_loop().create_future()
            # Connected at send time, link down again when the task fails
            account = mock.Mock(account_id=ACCOUNT, **{'is_connected.side_effect': [True, False]})

            async def send_hangs(*args):
                return await hang

            account.send_message = mock.AsyncMock(side_effect=send_hangs)
            task = asyncio.ensure_future(self.make_manager()._send_message_async(account, PEER, 'A', False))
            await asyncio.sleep(0)
            self.assertTrue(self.handler.is_in_flight(ROW_ID))

            # New session while the old send task hangs: the retry skips the row
            stats, client = await self.retry()
            client.send_private_message.assert_not_called()
            self.assertEqual(stats['resent'], 0)

            # The old task fails: the row stays pending, the next retry sends it
            hang.set_exception(ConnectionError('link dead'))
            await task
            self.assertFalse(self.handler.is_in_flight(ROW_ID))
            stats, client = await self.retry()
            client.send_private_message.assert_awaited_once_with(PEER, 'A')
            self.assertEqual(stats['resent'], 1)
            self.assertFalse(self.handler.is_in_flight(ROW_ID))

        asyncio.run(body())


class ServerAckTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def make_client(self):
        client = DrunkXMPP(jid='user@example.org/test', password='secret', rooms={}, enable_omemo=False)
        acked = []
        client.on_server_ack_callback = lambda info: acked.append(info.msg_id)
        return client, acked

    def test_message_acked_only_by_its_own_ack(self):
        async def body():
            client, acked = self.make_client()
            sm = client.plugin['xep_0198']
            sm.enabled_out = True
            sent = []
            with mock.patch.object(client, 'send', side_effect=lambda data, *a, **k: sent.append(data)):
                msg_id = await client.send_private_message('bob@example.net', 'hi')
            # The ACK request is queued behind the message, not sent before it
            self.assertEqual(len(sent), 2)
            self.assertEqual(sent[0]['id'], msg_id)
            self.assertIn('urn:xmpp:sm:3', sent[1])
            self.assertTrue(sent[1].startswith('<r'))

            # The out filter runs: a presence first, then the message
            sm._handle_outgoing(client.make_presence())
            sm._handle_outgoing(sent[0])
            # h=1 covers only the presence
            sm._handle_ack({'h': 1})
            self.assertEqual(acked, [])
            # h=2 covers the message
            sm._handle_ack({'h': 2})
            self.assertEqual(acked, [msg_id])
            self.assertEqual(client.pending_server_acks, set())

        asyncio.run(body())

    def test_no_ack_request_without_stream_management(self):
        # A server without XEP-0198 closes the stream on <r/>
        async def body():
            client, acked = self.make_client()
            client.plugin['xep_0198'].enabled_out = False
            sent = []
            with mock.patch.object(client, 'send', side_effect=lambda data, *a, **k: sent.append(data)):
                msg_id = await client.send_private_message('bob@example.net', 'hi')
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]['id'], msg_id)
            self.assertEqual(client.pending_server_acks, set())

        asyncio.run(body())

    def test_session_end_drops_old_ack_entries(self):
        async def body():
            client, acked = self.make_client()
            client.pending_server_acks.add('old-msg')
            await client._on_session_end(None)
            self.assertEqual(client.pending_server_acks, set())
            # A stanza with the same id acked later does not reach the app
            msg = client.make_message(mto='bob@example.net', mbody='x')
            msg['id'] = 'old-msg'
            client._on_stanza_acked(msg)
            self.assertEqual(acked, [])

        asyncio.run(body())


if __name__ == '__main__':
    unittest.main()
