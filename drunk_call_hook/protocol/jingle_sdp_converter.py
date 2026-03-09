"""
JingleSDPConverter - Pure SDP ↔ Jingle XML Conversion

Design Principles:
1. NO business logic (session state, XMPP stanzas, callbacks)
2. Pure functions: input → output
3. Stateless (except for logger)
4. Testable in isolation

This class is extracted from JingleAdapter to separate concerns:
- JingleAdapter: Business logic, session management, XMPP communication
- JingleSDPConverter: Pure conversion between SDP and Jingle XML

See docs/CALLS/JINGLE-REFACTOR-PLAN.md for architecture.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .features import RtcpMuxHandler, SSRCHandler


class JingleSDPConverter:
    """
    Pure converter between SDP and Jingle XML.

    NO side effects, NO state management, NO business logic.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize converter.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)

    def sdp_to_jingle(self, sdp: str, role: str,
                      offer_context: Optional[Dict] = None) -> ET.Element:
        """
        Convert SDP to Jingle XML.

        Args:
            sdp: SDP string (from CreateOffer or CreateAnswer RPC)
            role: "offer" or "answer"
            offer_context: For answers, context from extract_offer_context()
                          (used to echo features like BUNDLE, rtcp-mux, etc.)

        Returns:
            <jingle> element with <content> children

        Raises:
            ValueError: If SDP is invalid or role is invalid
        """
        # Validate inputs
        if not sdp or not sdp.strip():
            raise ValueError("SDP cannot be empty")

        if role not in ('offer', 'answer'):
            raise ValueError(f"role must be 'offer' or 'answer', got: {role}")

        self.logger.debug(f"Converting SDP to Jingle (role={role}, {len(sdp)} bytes)")

        # Parse SDP into lines (handle both \r\n and \n)
        if '\r\n' in sdp:
            sdp_lines = sdp.strip().split('\r\n')
        else:
            sdp_lines = sdp.strip().split('\n')

        # Global session attributes
        ice_ufrag = None
        ice_pwd = None
        dtls_fingerprint = None
        dtls_hash = None
        dtls_setup = None
        bundle_mids = []

        # Parse global attributes
        for line in sdp_lines:
            if line.startswith('a=ice-ufrag:'):
                ice_ufrag = line.split(':', 1)[1]
            elif line.startswith('a=ice-pwd:'):
                ice_pwd = line.split(':', 1)[1]
            elif line.startswith('a=fingerprint:'):
                # Format: "a=fingerprint:sha-256 AB:CD:EF:..."
                parts = line.split(':', 1)[1].split(' ', 1)
                dtls_hash = parts[0]
                dtls_fingerprint = parts[1]
            elif line.startswith('a=setup:'):
                dtls_setup = line.split(':', 1)[1]
            elif line.startswith('a=group:BUNDLE'):
                # Format: "a=group:BUNDLE 0 1"
                bundle_mids = line.split()[1:]  # Skip "a=group:BUNDLE"

        # Parse media sections
        current_media = None
        current_media_lines = []
        media_sections = []

        for line in sdp_lines:
            if line.startswith('m='):
                # Save previous media section
                if current_media:
                    media_sections.append((current_media, current_media_lines))

                # Start new media section
                # Format: m=audio 9 UDP/TLS/RTP/SAVPF 111 ...
                parts = line.split(' ')
                current_media = parts[0].split('=')[1]  # 'audio' or 'video'
                current_media_lines = [line]
            elif current_media:
                current_media_lines.append(line)

        # Save last media section
        if current_media:
            media_sections.append((current_media, current_media_lines))

        # Create root <jingle> element
        jingle = ET.Element('{urn:xmpp:jingle:1}jingle')

        # Build Jingle content elements for each media section
        for media_type, media_lines in media_sections:
            # Parse mid from media section (used for content name)
            content_name = media_type  # Default to media type
            for line in media_lines:
                if line.startswith('a=mid:'):
                    content_name = line.split(':', 1)[1]
                    break

            # Parse SDP direction from media lines
            # a=sendrecv → senders='both'
            # a=recvonly → senders='initiator' (only initiator/Dino sends, we receive)
            # a=sendonly → senders='responder' (we send, initiator receives)
            # a=inactive → senders='none'
            sdp_direction = 'sendrecv'  # Default
            for line in media_lines:
                if line.strip() in ('a=sendrecv', 'a=recvonly', 'a=sendonly', 'a=inactive'):
                    sdp_direction = line.strip().split('=')[1]
                    break

            # Convert SDP direction to Jingle senders attribute
            jingle_senders_map = {
                'sendrecv': 'both',
                'recvonly': 'initiator',  # Only initiator sends (we only receive)
                'sendonly': 'responder',  # Only responder sends (we only send)
                'inactive': 'none'
            }
            jingle_senders = jingle_senders_map.get(sdp_direction, 'both')

            content = ET.SubElement(jingle, '{urn:xmpp:jingle:1}content')
            content.set('creator', 'initiator')
            content.set('name', content_name)
            content.set('senders', jingle_senders)

            # Parse m= line for payload types
            # Format: m=audio 9 UDP/TLS/RTP/SAVPF 111 ...
            m_line = media_lines[0]
            m_parts = m_line.split(' ')
            payload_types = m_parts[3:]  # Everything after protocol

            # Add RTP description
            description = ET.SubElement(content, '{urn:xmpp:jingle:apps:rtp:1}description')
            description.set('media', media_type)

            # Check if SDP has rtcp-mux (parsed once, used later)
            sdp_has_rtcp_mux = any(line.strip() == 'a=rtcp-mux' for line in media_lines)

            # Parse fmtp parameters (codec-specific parameters from SDP)
            # Format: a=fmtp:111 minptime=10;useinbandfec=1
            fmtp_params = {}  # {payload_type_id: {param_name: param_value}}
            for line in media_lines:
                if line.startswith('a=fmtp:'):
                    fmtp_line = line.split(':', 1)[1]  # "111 minptime=10;useinbandfec=1"
                    parts = fmtp_line.split(' ', 1)
                    pt_id = parts[0]
                    if len(parts) > 1:
                        params_str = parts[1]  # "minptime=10;useinbandfec=1"
                        params_dict = {}
                        for param in params_str.split(';'):
                            param = param.strip()
                            if '=' in param:
                                key, value = param.split('=', 1)
                                params_dict[key.strip()] = value.strip()
                        fmtp_params[pt_id] = params_dict

            # Parse codecs from rtpmap lines
            for line in media_lines:
                if line.startswith('a=rtpmap:'):
                    # Format: a=rtpmap:111 opus/48000/2
                    rtpmap = line.split(':', 1)[1]
                    pt_id, codec_info = rtpmap.split(' ', 1)

                    if pt_id in payload_types:
                        codec_parts = codec_info.split('/')
                        codec_name = codec_parts[0]
                        clockrate = codec_parts[1] if len(codec_parts) > 1 else '48000'
                        channels = codec_parts[2] if len(codec_parts) > 2 else '1'

                        payload = ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}payload-type')
                        payload.set('id', pt_id)
                        payload.set('name', codec_name)
                        payload.set('clockrate', clockrate)

                        # Opus is always stereo (2 channels)
                        if codec_name.lower() == 'opus':
                            payload.set('channels', '2')
                        elif int(channels) > 1:
                            payload.set('channels', channels)

                        # Add codec parameters from SDP fmtp
                        if pt_id in fmtp_params:
                            for param_name, param_value in fmtp_params[pt_id].items():
                                param_elem = ET.SubElement(payload, '{urn:xmpp:jingle:apps:rtp:1}parameter')
                                param_elem.set('name', param_name)
                                param_elem.set('value', param_value)

            # Parse SSRC info from SDP using SSRCHandler
            # IMPORTANT: Add SSRC *before* rtcp-mux to match Conversations' element ordering
            ssrc_info = SSRCHandler.parse_ssrc_from_sdp(media_lines)

            # Add SSRC elements with filtering based on role and offer_context
            # For offers: include all SSRC params
            # For answers: only include params that were in the offer (echo pattern)
            if ssrc_info:
                should_add_ssrc = False
                allowed_params = []

                if role == 'offer':
                    # For offers, include all SSRC params
                    should_add_ssrc = True
                elif role == 'answer' and offer_context:
                    # For answers, only if offer had SSRC
                    should_add_ssrc = len(offer_context.get('ssrc_params', [])) > 0
                    allowed_params = offer_context.get('ssrc_params', [])

                if should_add_ssrc:
                    # Build Jingle <source> elements with SSRCHandler
                    count = SSRCHandler.build_jingle_ssrc_elements(
                        ssrc_info, description, role, allowed_params
                    )
                    self.logger.debug(f"Added {count} SSRC source(s) to Jingle (role={role})")

            # Add rtcp-mux AFTER source (matches Conversations' element ordering)
            # Use RtcpMuxHandler to determine if we should include rtcp-mux
            if role == 'offer':
                # Creating Jingle offer from SDP offer
                if RtcpMuxHandler.should_add_to_offer_jingle(sdp_has_rtcp_mux):
                    ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')
            elif role == 'answer':
                # Creating Jingle answer from SDP answer
                offer_had_rtcp_mux = offer_context.get('rtcp_mux', False) if offer_context else False
                if RtcpMuxHandler.should_add_to_answer_jingle(sdp_has_rtcp_mux, offer_had_rtcp_mux):
                    ET.SubElement(description, '{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')

            # Add ICE-UDP transport with credentials
            transport = ET.SubElement(content, '{urn:xmpp:jingle:transports:ice-udp:1}transport')

            if ice_ufrag:
                transport.set('ufrag', ice_ufrag)
            if ice_pwd:
                transport.set('pwd', ice_pwd)

            # Parse ICE candidates
            for line in media_lines:
                if line.startswith('a=candidate:'):
                    # Format: a=candidate:foundation component protocol priority ip port typ type [raddr] [rport]
                    # Example: a=candidate:1 1 UDP 2130706431 192.168.1.100 54321 typ host
                    cand_str = line.split(':', 1)[1]
                    cand_parts = cand_str.split(' ')

                    if len(cand_parts) >= 8:
                        cand_el = ET.SubElement(transport, '{urn:xmpp:jingle:transports:ice-udp:1}candidate')
                        cand_el.set('foundation', cand_parts[0])
                        cand_el.set('component', cand_parts[1])
                        cand_el.set('protocol', cand_parts[2].lower())
                        cand_el.set('priority', cand_parts[3])
                        cand_el.set('ip', cand_parts[4])
                        cand_el.set('port', cand_parts[5])
                        cand_el.set('type', cand_parts[7])  # typ is at index 6, type value at 7
                        cand_el.set('generation', '0')

                        # Optional: related address/port for reflexive/relay candidates
                        if len(cand_parts) >= 12 and cand_parts[8] == 'raddr':
                            cand_el.set('rel-addr', cand_parts[9])
                            cand_el.set('rel-port', cand_parts[11])

            # Add DTLS fingerprint
            if dtls_fingerprint and dtls_hash:
                fingerprint_el = ET.SubElement(transport, '{urn:xmpp:jingle:apps:dtls:0}fingerprint')
                fingerprint_el.set('hash', dtls_hash)
                if dtls_setup:
                    fingerprint_el.set('setup', dtls_setup)
                fingerprint_el.text = dtls_fingerprint

        # Add BUNDLE group if present in SDP
        if bundle_mids:
            group_el = ET.SubElement(jingle, '{urn:xmpp:jingle:apps:grouping:0}group')
            group_el.set('semantics', 'BUNDLE')
            for mid in bundle_mids:
                content_ref = ET.SubElement(group_el, '{urn:xmpp:jingle:apps:grouping:0}content')
                content_ref.set('name', mid)

        self.logger.debug(f"Converted SDP to Jingle XML ({len(media_sections)} media sections)")

        return jingle

    def jingle_to_sdp(self, jingle: ET.Element, role: str) -> str:
        """
        Convert Jingle XML to SDP.

        Args:
            jingle: <jingle> element from session-initiate or session-accept
            role: "offer" or "answer"

        Returns:
            SDP string

        Raises:
            ValueError: If Jingle is invalid or has no content
        """
        # Validate inputs
        if role not in ('offer', 'answer'):
            raise ValueError(f"role must be 'offer' or 'answer', got: {role}")

        # Find content elements
        contents = jingle.findall('{urn:xmpp:jingle:1}content')
        if not contents:
            raise ValueError("No content elements found in Jingle")

        self.logger.debug(f"Converting Jingle to SDP (role={role}, {len(contents)} contents)")

        # Build SDP
        sdp_lines = [
            "v=0",
            "o=- 0 0 IN IP4 0.0.0.0",
            "s=-",
            "t=0 0",
        ]

        # Check for BUNDLE group
        group = jingle.find('{urn:xmpp:jingle:apps:grouping:0}group[@semantics="BUNDLE"]')
        if group is not None:
            bundle_names = []
            for content_ref in group.findall('{urn:xmpp:jingle:apps:grouping:0}content'):
                bundle_names.append(content_ref.get('name'))
            if bundle_names:
                sdp_lines.append(f"a=group:BUNDLE {' '.join(bundle_names)}")

        # Add msid-semantic for WebRTC
        sdp_lines.append("a=msid-semantic: WMS *")

        # Process each content (media section)
        for content in contents:
            content_name = content.get('name', 'audio')

            description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
            transport = content.find('{urn:xmpp:jingle:transports:ice-udp:1}transport')

            if description is not None:
                media = description.get('media')

                # Parse payload types
                payload_types = description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type')
                pt_ids = []
                rtpmap_lines = []
                fmtp_lines = []

                for pt in payload_types:
                    pt_id = pt.get('id')
                    pt_name = pt.get('name')

                    # Media-aware defaults
                    if media == 'video':
                        pt_clockrate = pt.get('clockrate', '90000')
                        pt_channels = None  # Video doesn't use channels
                    else:  # audio
                        pt_clockrate = pt.get('clockrate', '48000')
                        pt_channels = pt.get('channels', '1')

                    pt_ids.append(pt_id)

                    # Build rtpmap
                    if pt_channels and int(pt_channels) > 1:
                        rtpmap_lines.append(f"a=rtpmap:{pt_id} {pt_name}/{pt_clockrate}/{pt_channels}")
                    else:
                        rtpmap_lines.append(f"a=rtpmap:{pt_id} {pt_name}/{pt_clockrate}")

                    # Build fmtp from parameters
                    params = pt.findall('{urn:xmpp:jingle:apps:rtp:1}parameter')
                    if params:
                        param_strs = []
                        for param in params:
                            param_name = param.get('name')
                            param_value = param.get('value')
                            param_strs.append(f"{param_name}={param_value}")
                        fmtp_lines.append(f"a=fmtp:{pt_id} {';'.join(param_strs)}")

                # Add m= line with payload types
                pt_list = ' '.join(pt_ids) if pt_ids else '111'
                sdp_lines.append(f"m={media} 9 UDP/TLS/RTP/SAVPF {pt_list}")
                sdp_lines.append("c=IN IP4 0.0.0.0")
                sdp_lines.append("a=rtcp:9 IN IP4 0.0.0.0")

                # Parse transport (ICE-UDP)
                if transport is not None:
                    # ICE credentials
                    ice_ufrag = transport.get('ufrag')
                    ice_pwd = transport.get('pwd')

                    if ice_ufrag:
                        sdp_lines.append(f"a=ice-ufrag:{ice_ufrag}")
                    if ice_pwd:
                        sdp_lines.append(f"a=ice-pwd:{ice_pwd}")

                    sdp_lines.append("a=ice-options:trickle")

                    # DTLS fingerprint
                    fingerprint_el = transport.find('{urn:xmpp:jingle:apps:dtls:0}fingerprint')
                    if fingerprint_el is not None:
                        dtls_hash = fingerprint_el.get('hash', 'sha-256')
                        dtls_setup = fingerprint_el.get('setup', 'actpass')
                        fingerprint = fingerprint_el.text

                        sdp_lines.append(f"a=fingerprint:{dtls_hash} {fingerprint}")
                        sdp_lines.append(f"a=setup:{dtls_setup}")

                    # Add mid (media ID)
                    sdp_lines.append(f"a=mid:{content_name}")

                    # Media direction
                    sdp_lines.append("a=sendrecv")

                    # Check for rtcp-mux in Jingle
                    jingle_has_rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux') is not None

                    # Use RtcpMuxHandler to determine if we should include rtcp-mux in SDP
                    if role == 'offer':
                        # Converting peer's Jingle offer to SDP for webrtcbin (we're answering)
                        if RtcpMuxHandler.should_add_to_answer_sdp(jingle_has_rtcp_mux):
                            sdp_lines.append("a=rtcp-mux")
                    elif role == 'answer':
                        # Converting peer's Jingle answer to SDP (setting remote description)
                        # Just echo what peer sent
                        if jingle_has_rtcp_mux:
                            sdp_lines.append("a=rtcp-mux")

                    # Add rtpmap lines
                    sdp_lines.extend(rtpmap_lines)

                    # Add fmtp lines
                    sdp_lines.extend(fmtp_lines)

                    # ICE candidates
                    candidates = transport.findall('{urn:xmpp:jingle:transports:ice-udp:1}candidate')
                    for cand in candidates:
                        foundation = cand.get('foundation', '1')
                        component = cand.get('component', '1')
                        protocol = cand.get('protocol', 'udp').upper()
                        priority = cand.get('priority', '1')
                        ip = cand.get('ip', '0.0.0.0')
                        port = cand.get('port', '9')
                        cand_type = cand.get('type', 'host')

                        cand_line = f"a=candidate:{foundation} {component} {protocol} {priority} {ip} {port} typ {cand_type}"

                        # Optional: related address/port
                        rel_addr = cand.get('rel-addr')
                        rel_port = cand.get('rel-port')
                        if rel_addr and rel_port:
                            cand_line += f" raddr {rel_addr} rport {rel_port}"

                        # Add generation if present
                        generation = cand.get('generation', '0')
                        cand_line += f" generation {generation}"

                        sdp_lines.append(cand_line)

        sdp = "\r\n".join(sdp_lines) + "\r\n"

        self.logger.debug(f"Converted Jingle to SDP ({role}, {len(contents)} contents)")
        return sdp

    def extract_offer_context(self, jingle: ET.Element) -> Dict:
        """
        Extract offer details for echoing in answer.

        When creating an answer, we need to echo certain features from the offer:
        - BUNDLE group (if offered)
        - rtcp-mux (if offered)
        - RTP extensions (must match offer)
        - Codec parameters (must match offer)
        - SSRC parameters (must match offer's parameter names)

        Args:
            jingle: <jingle> element from session-initiate (offer)

        Returns:
            Dictionary with offer details:
            {
                'bundle': bool,
                'rtcp_mux': bool,
                'codecs': [{'id': str, 'name': str, ...}],
                'rtp_hdrext': [{'id': str, 'uri': str}],
                'ssrc_params': [str],  # Parameter names like 'cname', 'msid'
            }
        """
        context = {
            'bundle': False,
            'rtcp_mux': False,
            'codecs': [],
            'rtp_hdrext': [],
            'ssrc_params': [],
        }

        # Extract BUNDLE group (RFC 9143)
        group = jingle.find('{urn:xmpp:jingle:apps:grouping:0}group[@semantics="BUNDLE"]')
        if group is not None:
            context['bundle'] = True
            self.logger.debug("Offer has BUNDLE group")

        # Extract features from each content
        for content in jingle.findall('{urn:xmpp:jingle:1}content'):
            description = content.find('{urn:xmpp:jingle:apps:rtp:1}description')
            if description is None:
                continue

            # Check for rtcp-mux
            rtcp_mux = description.find('{urn:xmpp:jingle:apps:rtp:1}rtcp-mux')
            if rtcp_mux is not None:
                context['rtcp_mux'] = True

            # Extract RTP header extensions (RFC 8285)
            for ext in description.findall('{urn:xmpp:jingle:apps:rtp:rtp-hdrext:0}rtp-hdrext'):
                context['rtp_hdrext'].append({
                    'id': ext.get('id'),
                    'uri': ext.get('uri')
                })

            # Extract codecs
            for pt in description.findall('{urn:xmpp:jingle:apps:rtp:1}payload-type'):
                codec = {
                    'id': pt.get('id'),
                    'name': pt.get('name'),
                    'clockrate': pt.get('clockrate'),
                    'channels': pt.get('channels')
                }
                context['codecs'].append(codec)

            # Extract SSRC parameter names (XEP-0294) using SSRCHandler
            ssrc_params = SSRCHandler.extract_ssrc_params(description)
            context['ssrc_params'].extend(ssrc_params)

        self.logger.debug(f"Extracted offer context: bundle={context['bundle']}, "
                         f"rtcp_mux={context['rtcp_mux']}, "
                         f"codecs={len(context['codecs'])}, "
                         f"rtp_hdrext={len(context['rtp_hdrext'])}")

        return context


# ============================================================================
# Helper Functions (Private)
# ============================================================================

def _parse_sdp_media_section(lines: List[str]) -> Dict:
    """
    Parse a single SDP media section (m= line + attributes).

    Args:
        lines: SDP lines for one media section (starting with m=)

    Returns:
        Dictionary with media section details
    """
    # TODO: Implement
    raise NotImplementedError()


def _build_jingle_content(media: Dict) -> ET.Element:
    """
    Build <content> element from parsed media section.

    Args:
        media: Parsed media section dict

    Returns:
        <content> element
    """
    # TODO: Implement
    raise NotImplementedError()


def _parse_ice_candidate_sdp(line: str) -> Dict:
    """
    Parse ICE candidate from SDP a=candidate line.

    Args:
        line: SDP line (e.g., "a=candidate:1 1 UDP 2130706431 192.168.1.1 54321 typ host")

    Returns:
        Dictionary with candidate fields
    """
    # TODO: Implement
    raise NotImplementedError()


def _build_ice_candidate_jingle(candidate: Dict) -> ET.Element:
    """
    Build <candidate> element from parsed candidate dict.

    Args:
        candidate: Parsed candidate dict

    Returns:
        <candidate> element
    """
    # TODO: Implement
    raise NotImplementedError()
