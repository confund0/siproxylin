"""
SSRCHandler - SSRC (Synchronization Source) parsing and filtering.

Handles XEP-0294 (Jingle RTP Source Description) SSRC parameter negotiation.

Key Concepts:
- SSRC identifies an RTP stream source (unique per sender)
- SDP format: a=ssrc:2485877649 cname:pion-audio
- Jingle format: <source ssrc="2485877649"><parameter name="cname" value="pion-audio"/></source>
- Namespace: urn:xmpp:jingle:apps:rtp:ssma:0 (SSMA = Source-Specific Media Attributes)

Filtering Rule (WebRTC Echo Pattern):
- Offers: Include the SSRC parameters the caller allows (all if no list)
- Answers: ONLY echo parameter names that were in the offer
- Example: Conversations sends {cname, msid}, Pion generates {cname, msid, mslabel, label}
          → Filter answer to {cname, msid} to match offer
- The converter (jingle_sdp_converter.py) also removes msid from our offers and
  answers: Conversations stops sending video when it gets our msid

References:
- XEP-0294: Jingle RTP Source Description
- RFC 5576: Source-Specific Media Attributes in SDP
"""

from typing import Dict, List, Optional
import xml.etree.ElementTree as ET


class SSRCHandler:
    """
    Static handler for SSRC (Synchronization Source) parsing and filtering.

    Pure conversion logic, no state. Similar to RtcpMuxHandler.
    """

    @staticmethod
    def parse_ssrc_from_sdp(sdp_lines: List[str]) -> Dict[str, Dict[str, str]]:
        """
        Parse SSRC information from SDP a=ssrc: lines.

        SDP format:
            a=ssrc:2485877649 cname:pion-audio
            a=ssrc:2485877649 msid:stream-id track-id

        Args:
            sdp_lines: List of SDP lines (media section)

        Returns:
            Dict mapping SSRC → {param_name: param_value}
            Example: {'2485877649': {'cname': 'pion-audio', 'msid': 'stream-id track-id'}}
        """
        ssrc_info = {}  # {ssrc: {attr_name: attr_value}}

        for line in sdp_lines:
            if line.startswith('a=ssrc:'):
                # Format: a=ssrc:2485877649 cname:pion-audio
                parts = line.split(':', 1)
                if len(parts) >= 2:
                    rest = parts[1].strip()
                    # Split by space: "2485877649 cname:pion-audio" → ["2485877649", "cname:pion-audio"]
                    ssrc_parts = rest.split(' ', 1)
                    if len(ssrc_parts) >= 2:
                        ssrc = ssrc_parts[0]
                        # ssrc_parts[1] = "cname:pion-audio"
                        if ':' in ssrc_parts[1]:
                            attr_name, attr_value = ssrc_parts[1].split(':', 1)
                            if ssrc not in ssrc_info:
                                ssrc_info[ssrc] = {}
                            ssrc_info[ssrc][attr_name] = attr_value

        return ssrc_info

    @staticmethod
    def filter_ssrc_params(ssrc_attrs: Dict[str, str], allowed_params: List[str]) -> Dict[str, str]:
        """
        Filter SSRC parameters to only include allowed parameter names.

        Used for answers to echo only the parameters that were in the offer.

        Args:
            ssrc_attrs: Dict of {param_name: param_value} for one SSRC
            allowed_params: List of allowed parameter names (e.g., ['cname', 'msid'])

        Returns:
            Filtered dict with only allowed parameters
        """
        if not allowed_params:
            # No filtering - return all
            return ssrc_attrs

        return {name: value for name, value in ssrc_attrs.items() if name in allowed_params}

    @staticmethod
    def build_jingle_ssrc_elements(
        ssrc_info: Dict[str, Dict[str, str]],
        parent_element: ET.Element,
        role: str,
        allowed_params: Optional[List[str]] = None
    ) -> int:
        """
        Build Jingle <source> elements with <parameter> children.

        Jingle format (XEP-0294):
            <description xmlns="urn:xmpp:jingle:apps:rtp:1">
                <source xmlns="urn:xmpp:jingle:apps:rtp:ssma:0" ssrc="2485877649">
                    <parameter name="cname" value="pion-audio"/>
                    <parameter name="msid" value="stream-id track-id"/>
                </source>
            </description>

        Args:
            ssrc_info: Dict from parse_ssrc_from_sdp() {ssrc: {param_name: param_value}}
            parent_element: Parent XML element (usually <description>)
            role: 'offer' or 'answer' - determines filtering behavior
            allowed_params: Parameter names to keep. For answers: names from the offer.
                For offers: None keeps all parameters.

        Returns:
            Number of <source> elements created
        """
        count = 0

        for ssrc, attrs in ssrc_info.items():
            # Create <source> element
            source_el = ET.SubElement(parent_element, '{urn:xmpp:jingle:apps:rtp:ssma:0}source')
            source_el.set('ssrc', ssrc)

            # Filter attributes based on role
            if role == 'offer' and allowed_params is None:
                # Offers without a list: include all parameters
                filtered_attrs = attrs
            elif role == 'offer':
                # Offers with a list: only these parameters
                filtered_attrs = SSRCHandler.filter_ssrc_params(attrs, allowed_params)
            else:  # role == 'answer'
                # Answers: only include parameters that were in the offer
                filtered_attrs = SSRCHandler.filter_ssrc_params(attrs, allowed_params or [])

            # Add <parameter> elements
            for attr_name, attr_value in filtered_attrs.items():
                param_el = ET.SubElement(source_el, '{urn:xmpp:jingle:apps:rtp:ssma:0}parameter')
                param_el.set('name', attr_name)
                param_el.set('value', attr_value)

            count += 1

        return count

    @staticmethod
    def extract_ssrc_params(jingle_description: ET.Element) -> List[str]:
        """
        Extract SSRC parameter names from Jingle offer (for echoing in answer).

        Used in extract_offer_context() to record which parameters the peer sent,
        so we can echo only those parameters in our answer.

        Args:
            jingle_description: <description> element from Jingle offer

        Returns:
            List of parameter names (e.g., ['cname', 'msid'])
        """
        param_names = []

        # Find all <source> elements
        sources = jingle_description.findall('{urn:xmpp:jingle:apps:rtp:ssma:0}source')
        for source in sources:
            # Find all <parameter> elements within each source
            params = source.findall('{urn:xmpp:jingle:apps:rtp:ssma:0}parameter')
            for param in params:
                param_name = param.get('name')
                if param_name and param_name not in param_names:
                    param_names.append(param_name)

        return param_names
