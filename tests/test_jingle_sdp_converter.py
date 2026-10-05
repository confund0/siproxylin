#!/usr/bin/env python3
"""
Unit tests for JingleSDPConverter - Pure SDP ↔ Jingle XML conversion.

Test Strategy (TDD):
1. Write failing tests for basic conversion
2. Extract conversion logic from JingleAdapter
3. Make tests pass
4. Refactor for clarity
5. Add more complex test cases

Run with: pytest tests/test_jingle_sdp_converter.py -v
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import xml.etree.ElementTree as ET
from drunk_call_hook.protocol.jingle_sdp_converter import JingleSDPConverter


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def converter():
    """Create a JingleSDPConverter instance."""
    return JingleSDPConverter()


@pytest.fixture
def simple_sdp_offer():
    """
    Simple SDP offer with:
    - 1 audio m-line (Opus codec)
    - ICE credentials
    - 1 ICE candidate
    - DTLS fingerprint
    - rtcp-mux
    """
    return """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE 0
a=msid-semantic: WMS *
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:abcd
a=ice-pwd:1234567890abcdefghij
a=ice-options:trickle
a=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99
a=setup:actpass
a=mid:0
a=sendrecv
a=rtcp-mux
a=rtpmap:111 opus/48000/2
a=fmtp:111 minptime=10;useinbandfec=1
a=candidate:1 1 UDP 2130706431 192.168.1.100 54321 typ host generation 0
"""


@pytest.fixture
def simple_sdp_answer():
    """Simple SDP answer matching the offer above."""
    return """v=0
o=- 654321 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE 0
a=msid-semantic: WMS *
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=rtcp:9 IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=ice-options:trickle
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=sendrecv
a=rtcp-mux
a=rtpmap:111 opus/48000/2
a=fmtp:111 minptime=10;useinbandfec=1
a=candidate:1 1 UDP 2130706431 192.168.1.200 43210 typ host generation 0
"""


# ============================================================================
# Test: Basic SDP → Jingle Conversion
# ============================================================================

def test_sdp_to_jingle_offer_basic(converter, simple_sdp_offer):
    """Test basic SDP offer → Jingle XML conversion."""
    # Convert
    jingle = converter.sdp_to_jingle(simple_sdp_offer, role='offer')

    # Verify it's an Element
    assert isinstance(jingle, ET.Element)

    # Verify <jingle> element
    assert jingle.tag == '{urn:xmpp:jingle:1}jingle'

    # Verify has <content> children
    contents = jingle.findall('{urn:xmpp:jingle:1}content')
    assert len(contents) == 1, "Should have 1 audio content"

    content = contents[0]
    assert content.get('name') == '0', "Content name should be mid=0"

    # Verify <description> (RTP)
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
    assert description is not None, "Should have RTP description"
    assert description.get('media') == 'audio'

    # Verify codec (Opus)
    payloads = description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type')
    assert len(payloads) == 1, "Should have 1 payload type (Opus)"

    opus = payloads[0]
    assert opus.get('id') == '111'
    assert opus.get('name') == 'opus'
    assert opus.get('clockrate') == '48000'
    assert opus.get('channels') == '2'

    # Verify <transport> (ICE-UDP)
    transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')
    assert transport is not None, "Should have ICE-UDP transport"
    assert transport.get('ufrag') == 'abcd'
    assert transport.get('pwd') == '1234567890abcdefghij'

    # Verify <fingerprint> (DTLS)
    fingerprint = transport.find('{urn:xmpp:jingle:apps:dtls:0}fingerprint')
    assert fingerprint is not None, "Should have DTLS fingerprint"
    assert fingerprint.get('hash') == 'sha-256'
    assert fingerprint.get('setup') == 'actpass'
    # Note: Fingerprint text is uppercase with colons
    assert 'AA:BB:CC' in fingerprint.text.upper()

    # Verify ICE candidate
    candidates = transport.findall('{urn:xmpp:jingle:transports:ice-udp:1}candidate')
    assert len(candidates) == 1, "Should have 1 ICE candidate"

    candidate = candidates[0]
    assert candidate.get('component') == '1'
    assert candidate.get('foundation') == '1'
    assert candidate.get('generation') == '0'
    assert candidate.get('ip') == '192.168.1.100'
    assert candidate.get('port') == '54321'
    assert candidate.get('priority') == '2130706431'
    assert candidate.get('protocol') == 'udp'
    assert candidate.get('type') == 'host'


def test_sdp_to_jingle_has_rtcp_mux(converter, simple_sdp_offer):
    """Test that rtcp-mux in SDP is converted to Jingle."""
    jingle = converter.sdp_to_jingle(simple_sdp_offer, role='offer')

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')

    # Verify <rtcp-mux/> element
    rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')
    assert rtcp_mux is not None, "Should have rtcp-mux element"


def test_sdp_to_jingle_has_bundle(converter, simple_sdp_offer):
    """Test that BUNDLE group in SDP is converted to Jingle."""
    jingle = converter.sdp_to_jingle(simple_sdp_offer, role='offer')

    # Verify <group> element with BUNDLE
    group = jingle.find('{urn:xmpp:jingle:apps:grouping:0}group')
    assert group is not None, "Should have BUNDLE group"
    assert group.get('semantics') == 'BUNDLE'

    # Verify <content> reference
    content_refs = group.findall('{urn:xmpp:jingle:apps:grouping:0}content')
    assert len(content_refs) == 1
    assert content_refs[0].get('name') == '0'


# ============================================================================
# Test: Basic Jingle → SDP Conversion
# ============================================================================

def test_jingle_to_sdp_offer_basic(converter):
    """Test basic Jingle offer → SDP conversion."""
    # Create minimal Jingle offer XML
    jingle = ET.Element('{urn:xmpp:jingle:1}jingle')
    jingle.set('action', 'session-initiate')
    jingle.set('sid', 'test-session-123')

    # Create content (audio)
    content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
    content.set('creator', 'initiator')
    content.set('name', '0')
    content.set('senders', 'both')

    # RTP description
    description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
    description.set('media', 'audio')

    # Opus payload
    payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
    payload.set('id', '111')
    payload.set('name', 'opus')
    payload.set('clockrate', '48000')
    payload.set('channels', '2')

    # rtcp-mux
    ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    # ICE-UDP transport
    transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
    transport.set('ufrag', 'test-ufrag')
    transport.set('pwd', 'test-password-1234567890')

    # DTLS fingerprint
    fingerprint = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
    fingerprint.set('hash', 'sha-256')
    fingerprint.set('setup', 'actpass')
    fingerprint.text = 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99'

    # ICE candidate
    candidate = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
    candidate.set('component', '1')
    candidate.set('foundation', '1')
    candidate.set('generation', '0')
    candidate.set('id', 'candidate-1')
    candidate.set('ip', '192.168.1.100')
    candidate.set('network', '0')
    candidate.set('port', '54321')
    candidate.set('priority', '2130706431')
    candidate.set('protocol', 'udp')
    candidate.set('type', 'host')

    # Convert to SDP
    sdp = converter.jingle_to_sdp(jingle, role='offer')

    # Verify SDP structure
    assert isinstance(sdp, str)
    assert len(sdp) > 0

    # Verify basic SDP fields
    assert 'v=0' in sdp, "Should have version"
    assert 'o=' in sdp, "Should have origin"
    assert 's=' in sdp, "Should have session name"
    assert 't=0 0' in sdp, "Should have timing"

    # Verify media line
    assert 'm=audio' in sdp, "Should have audio media line"
    assert 'UDP/TLS/RTP/SAVPF' in sdp, "Should have correct protocol"
    assert '111' in sdp, "Should include Opus payload type"

    # Verify codec
    assert 'a=rtpmap:111 opus/48000/2' in sdp, "Should have Opus rtpmap"

    # Verify ICE
    assert 'a=ice-ufrag:test-ufrag' in sdp
    assert 'a=ice-pwd:test-password-1234567890' in sdp
    assert 'a=candidate:' in sdp
    assert '192.168.1.100' in sdp
    assert '54321' in sdp

    # Verify DTLS fingerprint
    assert 'a=fingerprint:sha-256' in sdp
    assert 'AA:BB:CC' in sdp.upper()

    # Verify rtcp-mux
    assert 'a=rtcp-mux' in sdp

    # Verify setup
    assert 'a=setup:actpass' in sdp


# ============================================================================
# Test: Round-Trip Conversion
# ============================================================================

def test_sdp_to_jingle_to_sdp_round_trip(converter, simple_sdp_offer):
    """Test SDP → Jingle → SDP round-trip preserves essential info."""
    # Convert SDP → Jingle
    jingle = converter.sdp_to_jingle(simple_sdp_offer, role='offer')

    # Convert Jingle → SDP
    sdp_result = converter.jingle_to_sdp(jingle, role='offer')

    # Verify essential fields preserved
    assert 'a=ice-ufrag:abcd' in sdp_result, "ICE ufrag should be preserved"
    assert 'a=ice-pwd:1234567890abcdefghij' in sdp_result, "ICE pwd should be preserved"
    assert 'a=rtpmap:111 opus/48000/2' in sdp_result, "Opus codec should be preserved"
    assert 'a=rtcp-mux' in sdp_result, "rtcp-mux should be preserved"
    assert '192.168.1.100' in sdp_result, "Candidate IP should be preserved"
    assert '54321' in sdp_result, "Candidate port should be preserved"


# ============================================================================
# Test: Offer Context Extraction
# ============================================================================

def test_extract_offer_context(converter, simple_sdp_offer):
    """Test extracting offer context for echoing in answer."""
    jingle = converter.sdp_to_jingle(simple_sdp_offer, role='offer')
    context = converter.extract_offer_context(jingle)

    # Verify context structure
    assert isinstance(context, dict)

    # Verify BUNDLE
    assert 'bundle' in context
    assert context['bundle'] is True

    # Verify rtcp-mux
    assert 'rtcp_mux' in context
    assert context['rtcp_mux'] is True

    # Verify codecs
    assert 'codecs' in context
    assert len(context['codecs']) == 1
    assert context['codecs'][0]['id'] == '111'
    assert context['codecs'][0]['name'] == 'opus'


# ============================================================================
# Test: Error Handling
# ============================================================================

def test_sdp_to_jingle_empty_sdp(converter):
    """Test handling of empty SDP."""
    with pytest.raises(ValueError, match="SDP.*empty"):
        converter.sdp_to_jingle("", role='offer')


def test_sdp_to_jingle_invalid_role(converter, simple_sdp_offer):
    """Test handling of invalid role."""
    with pytest.raises(ValueError, match="role.*must be.*offer.*answer"):
        converter.sdp_to_jingle(simple_sdp_offer, role='invalid')


def test_jingle_to_sdp_no_content(converter):
    """Test handling of Jingle with no content."""
    jingle = ET.Element('{urn:xmpp:jingle:1}jingle')
    jingle.set('action', 'session-initiate')

    with pytest.raises(ValueError, match="No content"):
        converter.jingle_to_sdp(jingle, role='offer')


# ============================================================================
# Test: rtcp-mux Negotiation (RtcpMuxHandler)
# ============================================================================

def test_rtcp_mux_answer_both_have_it(converter):
    """
    Test rtcp-mux negotiation: Offer has it, SDP answer has it.

    Result: Jingle answer SHOULD have rtcp-mux (both sides agree).
    """
    sdp_answer = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=rtcp-mux
a=rtpmap:111 opus/48000/2
"""

    # Offer context: offer HAD rtcp-mux
    offer_context = {
        'bundle': False,
        'rtcp_mux': True,  # ← Offer had rtcp-mux
        'codecs': [],
        'rtp_hdrext': [],
        'ssrc_params': []
    }

    jingle = converter.sdp_to_jingle(sdp_answer, role='answer', offer_context=offer_context)

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
    rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    assert rtcp_mux is not None, "Should have rtcp-mux (both offer and answer support it)"


def test_rtcp_mux_answer_offer_has_answer_doesnt(converter):
    """
    Test rtcp-mux negotiation: Offer has it, SDP answer does NOT.

    Result: Jingle answer should NOT have rtcp-mux (answer rejects it).
    """
    sdp_answer = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=rtpmap:111 opus/48000/2
"""
    # NO a=rtcp-mux in SDP answer

    # Offer context: offer HAD rtcp-mux
    offer_context = {
        'bundle': False,
        'rtcp_mux': True,  # ← Offer had rtcp-mux
        'codecs': [],
        'rtp_hdrext': [],
        'ssrc_params': []
    }

    jingle = converter.sdp_to_jingle(sdp_answer, role='answer', offer_context=offer_context)

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
    rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    assert rtcp_mux is None, "Should NOT have rtcp-mux (SDP answer rejected it)"


def test_rtcp_mux_answer_offer_doesnt_answer_has(converter):
    """
    Test rtcp-mux negotiation: Offer does NOT have it, SDP answer has it.

    Result: Jingle answer should NOT have rtcp-mux (offer didn't propose it).
    """
    sdp_answer = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=rtcp-mux
a=rtpmap:111 opus/48000/2
"""

    # Offer context: offer did NOT have rtcp-mux
    offer_context = {
        'bundle': False,
        'rtcp_mux': False,  # ← Offer did NOT have rtcp-mux
        'codecs': [],
        'rtp_hdrext': [],
        'ssrc_params': []
    }

    jingle = converter.sdp_to_jingle(sdp_answer, role='answer', offer_context=offer_context)

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
    rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    assert rtcp_mux is None, "Should NOT have rtcp-mux (offer didn't propose it, can't add in answer)"


def test_rtcp_mux_answer_neither_has_it(converter):
    """
    Test rtcp-mux negotiation: Offer does NOT have it, SDP answer does NOT have it.

    Result: Jingle answer should NOT have rtcp-mux (neither side supports it).
    """
    sdp_answer = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=rtpmap:111 opus/48000/2
"""
    # NO a=rtcp-mux in SDP answer

    # Offer context: offer did NOT have rtcp-mux
    offer_context = {
        'bundle': False,
        'rtcp_mux': False,  # ← Offer did NOT have rtcp-mux
        'codecs': [],
        'rtp_hdrext': [],
        'ssrc_params': []
    }

    jingle = converter.sdp_to_jingle(sdp_answer, role='answer', offer_context=offer_context)

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
    rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    assert rtcp_mux is None, "Should NOT have rtcp-mux (neither offer nor answer support it)"


def test_jingle_answer_to_sdp_with_rtcp_mux(converter):
    """
    Test Jingle answer → SDP conversion with rtcp-mux.

    When we receive peer's Jingle answer with rtcp-mux, SDP should have it.
    """
    jingle = ET.Element('{urn:xmpp:jingle:1}jingle')
    jingle.set('action', 'session-accept')
    jingle.set('sid', 'test-session-123')

    content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
    content.set('creator', 'initiator')
    content.set('name', '0')

    description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
    description.set('media', 'audio')

    payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
    payload.set('id', '111')
    payload.set('name', 'opus')
    payload.set('clockrate', '48000')
    payload.set('channels', '2')

    # Add rtcp-mux
    ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
    transport.set('ufrag', 'test-ufrag')
    transport.set('pwd', 'test-password')

    fingerprint = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
    fingerprint.set('hash', 'sha-256')
    fingerprint.set('setup', 'active')
    fingerprint.text = 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99'

    sdp = converter.jingle_to_sdp(jingle, role='answer')

    assert 'a=rtcp-mux' in sdp, "SDP should have rtcp-mux (Jingle answer has it)"


def test_jingle_answer_to_sdp_without_rtcp_mux(converter):
    """
    Test Jingle answer → SDP conversion without rtcp-mux.

    When we receive peer's Jingle answer without rtcp-mux, SDP should NOT have it.
    """
    jingle = ET.Element('{urn:xmpp:jingle:1}jingle')
    jingle.set('action', 'session-accept')
    jingle.set('sid', 'test-session-123')

    content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
    content.set('creator', 'initiator')
    content.set('name', '0')

    description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
    description.set('media', 'audio')

    payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
    payload.set('id', '111')
    payload.set('name', 'opus')
    payload.set('clockrate', '48000')
    payload.set('channels', '2')

    # NO rtcp-mux element

    transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
    transport.set('ufrag', 'test-ufrag')
    transport.set('pwd', 'test-password')

    fingerprint = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
    fingerprint.set('hash', 'sha-256')
    fingerprint.set('setup', 'active')
    fingerprint.text = 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99'

    sdp = converter.jingle_to_sdp(jingle, role='answer')

    assert 'a=rtcp-mux' not in sdp, "SDP should NOT have rtcp-mux (Jingle answer doesn't have it)"


def test_jingle_offer_to_sdp_with_rtcp_mux(converter):
    """
    Test Jingle offer → SDP conversion with rtcp-mux.

    When we receive peer's Jingle offer with rtcp-mux, we should pass it to webrtcbin.
    """
    jingle = ET.Element('{urn:xmpp:jingle:1}jingle')
    jingle.set('action', 'session-initiate')
    jingle.set('sid', 'test-session-123')

    content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
    content.set('creator', 'initiator')
    content.set('name', '0')

    description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
    description.set('media', 'audio')

    payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
    payload.set('id', '111')
    payload.set('name', 'opus')
    payload.set('clockrate', '48000')
    payload.set('channels', '2')

    # Add rtcp-mux
    ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

    transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
    transport.set('ufrag', 'test-ufrag')
    transport.set('pwd', 'test-password')

    fingerprint = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
    fingerprint.set('hash', 'sha-256')
    fingerprint.set('setup', 'actpass')
    fingerprint.text = 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99'

    sdp = converter.jingle_to_sdp(jingle, role='offer')

    assert 'a=rtcp-mux' in sdp, "SDP should have rtcp-mux (pass peer's offer to webrtcbin)"


def test_jingle_offer_to_sdp_without_rtcp_mux(converter):
    """
    Test Jingle offer → SDP conversion without rtcp-mux.

    When we receive peer's Jingle offer without rtcp-mux, SDP should NOT have it.
    """
    jingle = ET.Element('{urn:xmpp:jingle:1}jingle')
    jingle.set('action', 'session-initiate')
    jingle.set('sid', 'test-session-123')

    content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
    content.set('creator', 'initiator')
    content.set('name', '0')

    description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
    description.set('media', 'audio')

    payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
    payload.set('id', '111')
    payload.set('name', 'opus')
    payload.set('clockrate', '48000')
    payload.set('channels', '2')

    # NO rtcp-mux

    transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')
    transport.set('ufrag', 'test-ufrag')
    transport.set('pwd', 'test-password')

    fingerprint = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
    fingerprint.set('hash', 'sha-256')
    fingerprint.set('setup', 'actpass')
    fingerprint.text = 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99'

    sdp = converter.jingle_to_sdp(jingle, role='offer')

    assert 'a=rtcp-mux' not in sdp, "SDP should NOT have rtcp-mux (peer's offer doesn't have it)"


# ============================================================================
# Test: Advanced Features (Placeholder for future)
# ============================================================================

@pytest.mark.skip(reason="Not implemented yet - future test")
def test_sdp_to_jingle_multiple_codecs(converter):
    """Test conversion with multiple codec options."""
    pass


def test_sdp_to_jingle_with_ssrc_offer(converter):
    """Test that SSRC is included in offers with cname only."""
    sdp_with_ssrc = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:abcd
a=ice-pwd:1234567890abcdefghij
a=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99
a=setup:actpass
a=mid:0
a=rtpmap:111 opus/48000/2
a=ssrc:2485877649 cname:pion-audio
a=ssrc:2485877649 msid:stream1 track1
a=ssrc:2485877649 mslabel:stream1
a=ssrc:2485877649 label:track1
"""

    jingle = converter.sdp_to_jingle(sdp_with_ssrc, role='offer')

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')

    # Verify SSRC source element
    source = description.find('{urn:xmpp:jingle:apps:rtp:ssma:0}source')
    assert source is not None, "Should have SSRC source element in offer"
    assert source.get('ssrc') == '2485877649'

    # Offers carry cname only (no msid: Conversations stops sending video with it)
    params = source.findall('{urn:xmpp:jingle:apps:rtp:ssma:0}parameter')
    param_names = [p.get('name') for p in params]
    assert param_names == ['cname'], "Should have only cname in offer"


def test_sdp_to_jingle_with_ssrc_answer_filtered(converter):
    """Test that SSRC params are filtered in answers based on offer_context."""
    sdp_with_ssrc = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=rtpmap:111 opus/48000/2
a=ssrc:9876543210 cname:pion-audio-resp
a=ssrc:9876543210 msid:stream2 track2
a=ssrc:9876543210 mslabel:stream2
a=ssrc:9876543210 label:track2
"""

    # Offer context with only cname and msid (like Conversations.im)
    offer_context = {
        'bundle': False,
        'rtcp_mux': False,
        'codecs': [],
        'rtp_hdrext': [],
        'ssrc_params': ['cname', 'msid']  # Only these 2 params
    }

    jingle = converter.sdp_to_jingle(sdp_with_ssrc, role='answer', offer_context=offer_context)

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')

    # Verify SSRC source element
    source = description.find('{urn:xmpp:jingle:apps:rtp:ssma:0}source')
    assert source is not None, "Should have SSRC source element in answer"
    assert source.get('ssrc') == '9876543210'

    # Verify only allowed SSRC parameters are included (filtered, never msid)
    params = source.findall('{urn:xmpp:jingle:apps:rtp:ssma:0}parameter')
    assert len(params) == 1, "Should have only cname (filtered to match offer, no msid)"

    param_names = [p.get('name') for p in params]
    assert 'cname' in param_names, "Should include cname (in offer)"
    assert 'msid' not in param_names, "Should NOT include msid (never sent)"
    assert 'mslabel' not in param_names, "Should NOT include mslabel (not in offer)"
    assert 'label' not in param_names, "Should NOT include label (not in offer)"


def test_sdp_to_jingle_with_ssrc_answer_no_offer_ssrc(converter):
    """Test that SSRC is NOT included in answers if offer had no SSRC."""
    sdp_with_ssrc = """v=0
o=- 123456 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=ice-ufrag:wxyz
a=ice-pwd:0987654321zyxwvutsrqp
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF
a=setup:active
a=mid:0
a=rtpmap:111 opus/48000/2
a=ssrc:9876543210 cname:pion-audio-resp
"""

    # Offer context with NO ssrc_params (offer had no SSRC)
    offer_context = {
        'bundle': False,
        'rtcp_mux': False,
        'codecs': [],
        'rtp_hdrext': [],
        'ssrc_params': []  # Offer had NO SSRC
    }

    jingle = converter.sdp_to_jingle(sdp_with_ssrc, role='answer', offer_context=offer_context)

    content = jingle.find('{urn:xmpp:jingle:1}content')
    description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')

    # Verify NO SSRC source element (should be skipped)
    source = description.find('{urn:xmpp:jingle:apps:rtp:ssma:0}source')
    assert source is None, "Should NOT have SSRC in answer when offer had no SSRC"


@pytest.mark.skip(reason="Not implemented yet - future test")
def test_jingle_to_sdp_with_rtp_hdrext(converter):
    """Test RTP header extensions."""
    pass


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
