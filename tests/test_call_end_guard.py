#!/usr/bin/env python3
"""
Offline tests for the end_call guard in CallBarrel.

Two accounts in the app can see the same call id: an account with the
same JID as the phone gets the phone's XEP-0353 <finish> as a carbon.
The call service and the GUI know calls by session id only, so
end_call of an account without that call must not end the session in
the call service and must not send call_terminated to the GUI. An
account with the call (ringing or early stage) still ends it.
end_session is called only for a call service session this account
created: the account with the phone's JID can ring for the same id
that the other account is calling. A ringing incoming call stays known
when the DB entry fails.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_call_end_guard.py
"""

import os
import sys
import asyncio
import logging
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle',
    'drunk_call_hook.protocol.features', 'drunk_call_hook.protocol.features.trickle_ice')}
# PySide6 first: patch.dict refills sys.modules on exit, which breaks PySide6 lazy loading
from PySide6.QtCore import QCoreApplication  # noqa: E402
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.core.barrels.calls import CallBarrel
logging.disable(logging.NOTSET)

SID = 'A14qA4pgzl7k_I56b2PklQ'

APP = QCoreApplication.instance() or QCoreApplication([])


class FakeClient:
    def __init__(self):
        self.call_sessions = {}


def make_barrel():
    signal = mock.MagicMock()
    barrel = CallBarrel(2, FakeClient(), None,
                        {'call_terminated': signal, 'call_incoming': mock.MagicMock()})
    barrel.call_bridge = mock.MagicMock()
    barrel.call_bridge.end_session = mock.AsyncMock()
    barrel.jingle_adapter = mock.MagicMock()
    barrel.jingle_adapter.sessions = {}
    barrel.jingle_adapter.terminate = mock.AsyncMock()
    barrel.jingle_adapter.cleanup_session = mock.AsyncMock()
    return barrel, signal


async def end_and_settle(barrel):
    await barrel.end_call(SID, reason='finished', send_terminate=False)
    await asyncio.sleep(0)  # run call_soon_threadsafe callbacks


class TestCallEndGuard(unittest.TestCase):

    def test_unknown_call_is_skipped(self):
        """<finish> carbon for a call of another account: nothing ends."""
        barrel, signal = make_barrel()
        asyncio.run(end_and_settle(barrel))
        barrel.call_bridge.end_session.assert_not_awaited()
        barrel.jingle_adapter.cleanup_session.assert_not_awaited()
        signal.emit.assert_not_called()

    def test_ringing_call_ends(self):
        """Incoming call still ringing (logged, no call service session yet).

        Same id as the outgoing call of another account: its call service
        session must stay.
        """
        barrel, signal = make_barrel()
        barrel.call_peer_jids[SID] = 'peer@example.org/phone'
        asyncio.run(end_and_settle(barrel))
        barrel.call_bridge.end_session.assert_not_awaited()
        signal.emit.assert_called_once_with(2, SID, 'finished', 'peer@example.org/phone')

    def test_own_bridge_session_ends(self):
        """A call service session this account created is ended."""
        barrel, signal = make_barrel()
        barrel.call_peer_jids[SID] = 'peer@example.org/phone'
        barrel._bridge_sessions.add(SID)
        asyncio.run(end_and_settle(barrel))
        barrel.call_bridge.end_session.assert_awaited_once_with(SID)
        signal.emit.assert_called_once()
        self.assertNotIn(SID, barrel._bridge_sessions)

    def test_ringing_call_without_db_entry_ends(self):
        """Propose, the DB entry fails, then the caller retracts."""
        barrel, signal = make_barrel()
        brewery = mock.MagicMock()
        brewery.get_account_brewery.return_value.has_active_call.return_value = False

        async def run():
            with mock.patch.dict(sys.modules, {'siproxylin.core.brewery': brewery}), \
                    mock.patch('siproxylin.core.barrels.calls.get_db', side_effect=RuntimeError('db')):
                await barrel._on_xmpp_call_incoming('peer@example.org/phone', SID, ['audio'])
            self.assertNotIn(SID, barrel.call_peer_jids)
            await barrel._on_xmpp_call_terminated(SID, 'retract')
            await asyncio.sleep(0)

        asyncio.run(run())
        signal.emit.assert_called_once_with(2, SID, 'retract', 'unknown')
        self.assertNotIn(SID, barrel._incoming_calls)

    def test_early_call_in_client_only_ends(self):
        """Call known only to the XEP-0353 state of the client."""
        barrel, signal = make_barrel()
        barrel.client.call_sessions[SID] = {'peer_jid': 'peer@example.org/phone'}
        asyncio.run(end_and_settle(barrel))
        signal.emit.assert_called_once()

    def test_second_end_is_skipped(self):
        """end_call runs the cleanup once per call."""
        barrel, signal = make_barrel()
        barrel.call_peer_jids[SID] = 'peer@example.org/phone'
        barrel._bridge_sessions.add(SID)
        asyncio.run(end_and_settle(barrel))
        asyncio.run(end_and_settle(barrel))
        barrel.call_bridge.end_session.assert_awaited_once_with(SID)


if __name__ == '__main__':
    unittest.main()
