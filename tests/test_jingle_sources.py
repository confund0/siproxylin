#!/usr/bin/env python3
"""
Offline tests: a=ssrc lines from webrtcbin reach the Jingle as <source>.

Our <source> elements carry cname only, the same cname for audio and
video. They never carry msid: Conversations stops sending video when it
gets our msid (tested 2026-10-05). This is checked for an offer and for
an answer to a Conversations offer (which has cname and msid).

Run: venv/bin/python -m unittest tests/test_jingle_sources.py
"""

import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# drunk_call_hook/__init__.py needs grpc (not in the repo venv): use empty
# package modules, so only the converter and its features load
_pkg = types.ModuleType('drunk_call_hook')
_pkg.__path__ = [str(ROOT / 'drunk_call_hook')]
_protocol = types.ModuleType('drunk_call_hook.protocol')
_protocol.__path__ = [str(ROOT / 'drunk_call_hook' / 'protocol')]
with mock.patch.dict(sys.modules, {'drunk_call_hook': _pkg,
                                   'drunk_call_hook.protocol': _protocol}):
    from drunk_call_hook.protocol.jingle_sdp_converter import JingleSDPConverter

NS_JINGLE = '{urn:xmpp:jingle:1}'
NS_RTP = '{urn:xmpp:jingle:apps:rtp:1}'
NS_SSMA = '{urn:xmpp:jingle:apps:rtp:ssma:0}'

STREAM = '0b6c2f0e-3c0a-4b8e-9f3e-5d7a1c2b4e6f'
CNAME = 'user1804289383@host-7a1c2b4e'

FINGERPRINT = ('9F:AB:5C:DE:24:02:56:FE:55:E1:AE:C6:8F:21:E3:9F:'
               '1E:18:43:44:27:BC:D7:5F:DF:09:36:6F:2E:1E:D9:7A')

# Offer as webrtcbin writes it (audio first, as our offerer does)
OFFER_SDP = f"""v=0
o=- 0 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE audio0 video1
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=setup:actpass
a=ice-ufrag:abcd
a=ice-pwd:1234567890abcdefghijkl
a=rtcp-mux
a=sendrecv
a=rtpmap:111 OPUS/48000/2
a=ssrc:1111111111 msid:{STREAM} webrtctransceiver0
a=ssrc:1111111111 cname:{CNAME}
a=mid:audio0
a=fingerprint:sha-256 {FINGERPRINT}
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 0.0.0.0
a=setup:actpass
a=ice-ufrag:abcd
a=ice-pwd:1234567890abcdefghijkl
a=rtcp-mux
a=sendrecv
a=rtpmap:96 VP8/90000
a=rtcp-fb:96 nack pli
a=ssrc:2222222222 msid:{STREAM} webrtctransceiver1
a=ssrc:2222222222 cname:{CNAME}
a=mid:video1
a=fingerprint:sha-256 {FINGERPRINT}
"""

# Answer as webrtcbin writes it to a Conversations offer (video first)
ANSWER_SDP = f"""v=0
o=- 0 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE 0 1
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 0.0.0.0
a=ice-ufrag:XjvL
a=ice-pwd:3sIKoUkM0M+1CEXNxYm67i
a=mid:0
a=rtcp-mux
a=setup:active
a=rtpmap:96 VP8/90000
a=ssrc:2222222222 msid:{STREAM} webrtctransceiver2
a=ssrc:2222222222 cname:{CNAME}
a=sendrecv
a=fingerprint:sha-256 {FINGERPRINT}
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:XjvL
a=ice-pwd:3sIKoUkM0M+1CEXNxYm67i
a=mid:1
a=rtcp-mux
a=setup:active
a=rtpmap:111 OPUS/48000/2
a=fmtp:111 minptime=10;useinbandfec=1
a=ssrc:1111111111 msid:{STREAM} webrtctransceiver3
a=ssrc:1111111111 cname:{CNAME}
a=sendrecv
a=fingerprint:sha-256 {FINGERPRINT}
"""

# Short form of a real Conversations session-initiate (sources with cname and msid)
CONVERSATIONS_OFFER = """<jingle xmlns="urn:xmpp:jingle:1" action="session-initiate" sid="s1">
<content creator="initiator" name="0">
<description xmlns="urn:xmpp:jingle:apps:rtp:1" media="video">
<payload-type name="VP8" id="96" clockrate="90000"/>
<source xmlns="urn:xmpp:jingle:apps:rtp:ssma:0" ssrc="993407488">
<parameter name="cname" value="+t7O+tTdTBhUoj3D"/>
<parameter name="msid" value="- video-track-460466b8"/>
</source>
<rtcp-mux/>
</description>
<transport xmlns="urn:xmpp:jingle:transports:ice-udp:1" ufrag="JR2g" pwd="3o4y1DbEkqY+YzOQGYGPwvnd"/>
</content>
<content creator="initiator" name="1">
<description xmlns="urn:xmpp:jingle:apps:rtp:1" media="audio">
<payload-type channels="2" name="opus" id="111" clockrate="48000"/>
<source xmlns="urn:xmpp:jingle:apps:rtp:ssma:0" ssrc="669028746">
<parameter name="cname" value="+t7O+tTdTBhUoj3D"/>
<parameter name="msid" value="- audio-track-235f4e2e"/>
</source>
<rtcp-mux/>
</description>
<transport xmlns="urn:xmpp:jingle:transports:ice-udp:1" ufrag="JR2g" pwd="3o4y1DbEkqY+YzOQGYGPwvnd"/>
</content>
</jingle>"""


def _sources_by_media(jingle):
    """Return {media: [(ssrc, {name: value})]} for all contents."""
    result = {}
    for content in jingle.findall(f'{NS_JINGLE}content'):
        description = content.find(f'{NS_RTP}description')
        media = description.get('media')
        sources = []
        for source in description.findall(f'{NS_SSMA}source'):
            params = {p.get('name'): p.get('value')
                      for p in source.findall(f'{NS_SSMA}parameter')}
            sources.append((source.get('ssrc'), params))
        result[media] = sources
    return result


class TestJingleSources(unittest.TestCase):

    def setUp(self):
        self.converter = JingleSDPConverter()

    def _check(self, jingle):
        sources = _sources_by_media(jingle)
        self.assertEqual(set(sources), {'audio', 'video'})
        # cname only, same cname for audio and video, no msid
        self.assertEqual(sources['audio'], [('1111111111', {'cname': CNAME})])
        self.assertEqual(sources['video'], [('2222222222', {'cname': CNAME})])

    def test_offer_has_sources_with_cname_only(self):
        jingle = self.converter.sdp_to_jingle(OFFER_SDP, role='offer')
        self._check(jingle)

    def test_answer_to_conversations_has_sources_with_cname_only(self):
        offer_context = self.converter.extract_offer_context(ET.fromstring(CONVERSATIONS_OFFER))
        self.assertEqual(set(offer_context['ssrc_params']), {'cname', 'msid'})
        jingle = self.converter.sdp_to_jingle(ANSWER_SDP, role='answer',
                                              offer_context=offer_context)
        self._check(jingle)


if __name__ == '__main__':
    unittest.main()
