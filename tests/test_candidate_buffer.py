#!/usr/bin/env python3
"""
Offline tests for the buffer of remote ICE candidates on incoming calls.

On an incoming call, transport-info candidates go to a buffer until the
answer is sent. The buffer is read after the session-accept. A candidate
can arrive while we wait for send_answer or add_ice_candidate. It must
still reach the call service (add_ice_candidate), exactly once, and only
after create_answer set the remote SDP.

Covers: user accepted before the session-initiate (JMI), deferred answer
for a trickle-only offer, and session-initiate before the user accepted.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by
stubs; the trickle ICE module is the real one.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_candidate_buffer.py
"""

import os
import sys
import asyncio
import logging
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Real trickle ICE module, loaded by path (its package imports grpc)
_spec = importlib.util.spec_from_file_location(
    'siproxylin_test_trickle_ice', ROOT / 'drunk_call_hook/protocol/features/trickle_ice.py')
trickle_ice = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trickle_ice)

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle',
    'drunk_call_hook.protocol.features')}
_stubs['drunk_call_hook.protocol.features.trickle_ice'] = trickle_ice
# PySide6 first: patch.dict refills sys.modules on exit, which breaks PySide6 lazy loading
from PySide6.QtWidgets import QApplication  # noqa: E402
logging.disable(logging.CRITICAL)
# Another test file can load the calls module first, without the stubs
# (then calls are disabled there). Load a new copy here with the stubs.
_old_calls = sys.modules.get('siproxylin.core.barrels.calls')
with mock.patch.dict(sys.modules, _stubs):
    sys.modules.pop('siproxylin.core.barrels.calls', None)
    from siproxylin.core.barrels.calls import CallBarrel
if _old_calls is not None:
    # The import above set the package attribute to the new copy: set it back
    sys.modules['siproxylin.core.barrels'].calls = _old_calls
logging.disable(logging.NOTSET)

State = trickle_ice.IncomingCallState

SID = '2lJd45cwAZ9rs5et6fHzwQ'
OFFER = 'v=0\r\na=candidate:1 1 udp 1 10.0.0.1 1000 typ host\r\n'

APP = QApplication.instance() or QApplication([])  # widget tests in the same run need QApplication


def cand(port):
    return {'candidate': f'candidate:1 1 udp 1 89.238.78.51 {port} typ relay',
            'sdpMid': '0', 'sdpMLineIndex': 0}


class FakeClient:
    def __init__(self):
        self.call_sessions = {}
        self.send_call_proceed = mock.MagicMock()


def make_barrel():
    barrel = CallBarrel(1, FakeClient(), None,
                        {'call_terminated': mock.MagicMock(), 'call_incoming': mock.MagicMock()})
    events = []

    async def create_answer(session_id, sdp):
        events.append('answer')
        return 'answer-sdp'

    barrel.call_bridge = mock.MagicMock()
    barrel.call_bridge.create_answer = mock.AsyncMock(side_effect=create_answer)
    barrel.call_bridge.end_session = mock.AsyncMock()
    barrel.jingle_adapter = mock.MagicMock()
    barrel.jingle_adapter.sessions = {SID: {'peer_jid': 'bob@localhost/phone', 'media': ['audio']}}
    barrel.jingle_adapter.trickle_ice = trickle_ice.TrickleICEHandler(
        logger=logging.getLogger('test'))
    barrel.jingle_adapter.send_answer = mock.AsyncMock()

    async def create_session(session_id):
        tice = barrel.jingle_adapter.trickle_ice
        tice.set_incoming_state(session_id, State.RESOURCES_READY)
        tice.set_incoming_state(session_id, State.SESSION_CREATED)
        return True

    barrel._create_incoming_session = create_session
    return barrel, events


async def transport_info(barrel, candidate):
    """Same branch as JingleAdapter for a transport-info candidate."""
    tice = barrel.jingle_adapter.trickle_ice
    if tice.should_buffer_candidates(SID):
        tice.buffer_candidates(SID, [candidate])
        return
    await barrel._on_ice_candidate_received(SID, candidate)


def track_adds(barrel, events, during_add=None):
    """Record add_ice_candidate calls; during_add runs on the first call (a wait point)."""
    state = {'first': True}

    async def add(session_id, candidate):
        events.append(('add', candidate['candidate']))
        if during_add and state['first']:
            state['first'] = False
            await during_add()

    barrel.call_bridge.add_ice_candidate = mock.AsyncMock(side_effect=add)


def added(events):
    return [e[1] for e in events if isinstance(e, tuple)]


class TestCandidateBuffer(unittest.TestCase):

    def check(self, events, ports, barrel):
        names = added(events)
        for port in ports:
            self.assertEqual(sum(1 for n in names if f' {port} ' in n), 1, f'port {port}: {names}')
        self.assertEqual(len(names), len(ports))
        # Nothing reaches the call service before the remote SDP is set
        self.assertEqual(events[0], 'answer')
        self.assertEqual(barrel.jingle_adapter.trickle_ice.get_incoming_state(SID), State.ACTIVE)
        self.assertEqual(barrel.jingle_adapter.trickle_ice.get_buffered_candidates(SID), [])

    async def setup_incoming(self, barrel):
        """Session-initiate arrived; two candidates were buffered before the answer."""
        tice = barrel.jingle_adapter.trickle_ice
        tice.set_incoming_state(SID, State.HAVE_OFFER)
        await transport_info(barrel, cand(44406))
        await transport_info(barrel, cand(52417))

    def test_candidate_during_send_answer(self):
        """User accepted first (JMI): a candidate arrives while the session-accept is sent."""
        barrel, events = make_barrel()
        track_adds(barrel, events)
        barrel.accepted_calls.add(SID)

        async def run():
            await self.setup_incoming(barrel)

            async def send_answer(session_id, sdp):
                await transport_info(barrel, cand(52821))
            barrel.jingle_adapter.send_answer.side_effect = send_answer
            await barrel._on_jingle_incoming_call(SID, 'bob@localhost/phone', OFFER, ['audio'])
            await transport_info(barrel, cand(60000))

        asyncio.run(run())
        self.check(events, [44406, 52417, 52821, 60000], barrel)

    def test_candidate_during_add(self):
        """The case of the log: a candidate arrives while a buffered one is added."""
        barrel, events = make_barrel()
        barrel.accepted_calls.add(SID)

        async def run():
            await self.setup_incoming(barrel)
            track_adds(barrel, events, during_add=lambda: transport_info(barrel, cand(56584)))
            await barrel._on_jingle_incoming_call(SID, 'bob@localhost/phone', OFFER, ['audio'])

        asyncio.run(run())
        self.check(events, [44406, 52417, 56584], barrel)

    def test_deferred_answer(self):
        """Trickle-only offer: answer from _on_candidates_ready, candidate during the drain."""
        barrel, events = make_barrel()
        barrel.accepted_calls.add(SID)
        barrel.pending_call_offers[SID] = OFFER

        async def run():
            await self.setup_incoming(barrel)
            await barrel._create_incoming_session(SID)
            track_adds(barrel, events, during_add=lambda: transport_info(barrel, cand(56584)))

            async def send_answer(session_id, sdp):
                await transport_info(barrel, cand(52821))
            barrel.jingle_adapter.send_answer.side_effect = send_answer
            await barrel._on_candidates_ready(SID)

        asyncio.run(run())
        self.check(events, [44406, 52417, 52821, 56584], barrel)

    def test_offer_before_accept(self):
        """Session-initiate before the user accepted: accept_call sends the answer."""
        barrel, events = make_barrel()
        barrel.pending_call_offers[SID] = OFFER

        async def run():
            await self.setup_incoming(barrel)
            track_adds(barrel, events, during_add=lambda: transport_info(barrel, cand(56584)))

            async def send_answer(session_id, sdp):
                await transport_info(barrel, cand(52821))
            barrel.jingle_adapter.send_answer.side_effect = send_answer
            await barrel.accept_call(SID)
            await transport_info(barrel, cand(60000))

        asyncio.run(run())
        self.check(events, [44406, 52417, 52821, 56584, 60000], barrel)


if __name__ == '__main__':
    unittest.main()
