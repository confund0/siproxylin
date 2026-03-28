"""
XEP-0428: Fallback Indication

This module implements XEP-0428 which provides a way to mark portions of
message body text as "fallback" content for legacy clients.

Used primarily with XEP-0461 (Replies) to mark quoted text, allowing clients
to strip fallback and show only the actual message content.

Example:
    Message body: "> Quoted text\nActual reply"
    Fallback marker: {ns_uri: "urn:xmpp:reply:0", from_char: 0, to_char: 15}

    Legacy client sees: Full text with "> Quoted text"
    Modern client extracts: "Actual reply" (strips chars 0-15)
"""

from typing import List, Dict, Any, Optional
from slixmpp.stanza import Message


NS_URI = "urn:xmpp:fallback:0"


class FallbackMarker:
    """
    Represents a single fallback indication.

    Attributes:
        ns_uri: Namespace URI indicating the feature this fallback is for
                (e.g., "urn:xmpp:reply:0" for XEP-0461 replies)
        from_char: Start character position (inclusive, 0-based)
        to_char: End character position (exclusive, 0-based)
    """

    def __init__(self, ns_uri: str, from_char: int, to_char: int):
        self.ns_uri = ns_uri
        self.from_char = from_char
        self.to_char = to_char

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for metadata."""
        return {
            'ns_uri': self.ns_uri,
            'from_char': self.from_char,
            'to_char': self.to_char
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'FallbackMarker':
        """Create from dictionary."""
        return FallbackMarker(
            ns_uri=data['ns_uri'],
            from_char=data['from_char'],
            to_char=data['to_char']
        )


def add_fallback_to_stanza(msg: Message, fallback: FallbackMarker) -> None:
    """
    Add XEP-0428 fallback indication to a message stanza using slixmpp's API.

    Args:
        msg: Slixmpp Message stanza
        fallback: FallbackMarker with namespace and character range

    Example XML:
        <fallback xmlns='urn:xmpp:fallback:0' for='urn:xmpp:reply:0'>
            <body xmlns='urn:xmpp:fallback:0' start='0' end='15'/>
        </fallback>
    """
    # Use slixmpp's native XEP-0428 API instead of manual XML manipulation
    msg['fallback']['for'] = fallback.ns_uri
    msg['fallback']['body']['start'] = fallback.from_char
    msg['fallback']['body']['end'] = fallback.to_char


def extract_fallbacks_from_stanza(msg: Message) -> List[FallbackMarker]:
    """
    Extract XEP-0428 fallback markers from a message stanza using slixmpp's API.

    Args:
        msg: Slixmpp Message stanza

    Returns:
        List of FallbackMarker objects
    """
    fallbacks = []

    # Use slixmpp's native XEP-0428 API to iterate fallback elements
    for fallback_elem in msg['fallbacks']:
        try:
            ns_uri = fallback_elem['for']
            if not ns_uri:
                continue

            # Get character positions from body element
            from_char = fallback_elem['body']['start']
            to_char = fallback_elem['body']['end']

            if from_char >= 0 and to_char > from_char:
                fallbacks.append(FallbackMarker(ns_uri, from_char, to_char))
        except (KeyError, ValueError, TypeError, AttributeError):
            # Invalid or missing attributes, skip
            continue

    return fallbacks


def format_reply_with_fallback(reply_body: str, fallback_body: str) -> tuple[str, FallbackMarker]:
    """
    Format a reply message with fallback text and create the corresponding marker.

    This is the single source of truth for XEP-0461 reply formatting.
    Both GUI (optimistic storage) and DrunkXMPP (sending) should use this.

    Args:
        reply_body: The actual reply text
        fallback_body: The original message being replied to (may include existing "> " quotes)

    Returns:
        tuple: (full_body, fallback_marker)
            - full_body: Formatted message with "> " quoted text + reply
            - fallback_marker: FallbackMarker indicating the quoted portion

    Example:
        reply_body = "Sounds good"
        fallback_body = "Want to meet?"
        full_body, marker = format_reply_with_fallback(reply_body, fallback_body)
        # full_body = "> Want to meet?\nSounds good"
        # marker = FallbackMarker("urn:xmpp:reply:0", 0, 17)

        # Nested reply:
        fallback_body = "> Want to meet?\nSounds good"
        full_body, marker = format_reply_with_fallback(reply_body, fallback_body)
        # full_body = ">> Want to meet?\n> Sounds good\nSee you there"
        # marker = FallbackMarker("urn:xmpp:reply:0", 0, 34)
    """
    # Format each line: lines already starting with ">" get ">" (no space)
    # Other lines get "> " (with space)
    fallback_lines = fallback_body.split('\n')
    formatted_lines = []
    for line in fallback_lines:
        if line.startswith('>'):
            # Already quoted - add another level: ">> " not "> > "
            formatted_lines.append(f">{line}")
        else:
            # Not quoted - add first level with space
            formatted_lines.append(f"> {line}")

    fallback_text = '\n'.join(formatted_lines) + '\n'
    full_body = fallback_text + reply_body

    # Create fallback marker for the quoted portion
    marker = FallbackMarker(
        ns_uri="urn:xmpp:reply:0",
        from_char=0,
        to_char=len(fallback_text)
    )

    return (full_body, marker)


def strip_fallbacks(body: str, fallbacks: List[FallbackMarker], ns_uri: Optional[str] = None) -> str:
    """
    Remove fallback portions from message body.

    Args:
        body: Original message body text
        fallbacks: List of FallbackMarker objects
        ns_uri: Optional filter - only strip fallbacks for this namespace
                (e.g., "urn:xmpp:reply:0" to only strip reply quotes)

    Returns:
        Message body with fallback portions removed

    Example:
        body = "> Quoted text\nActual reply"
        fallback = FallbackMarker("urn:xmpp:reply:0", 0, 15)
        result = strip_fallbacks(body, [fallback])
        # result = "Actual reply"
    """
    if not fallbacks:
        return body

    # Filter by namespace if specified
    if ns_uri:
        fallbacks = [f for f in fallbacks if f.ns_uri == ns_uri]

    if not fallbacks:
        return body

    # Sort fallbacks by position (reverse order to process from end to start)
    # This prevents position shifting when removing text
    sorted_fallbacks = sorted(fallbacks, key=lambda f: f.from_char, reverse=True)

    result = body
    for fallback in sorted_fallbacks:
        # Ensure character positions are within bounds
        if fallback.from_char < 0 or fallback.to_char > len(result):
            continue

        # Remove the fallback portion
        result = result[:fallback.from_char] + result[fallback.to_char:]

    return result
