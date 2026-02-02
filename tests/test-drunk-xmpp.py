#!/usr/bin/env python3
"""
Minimal test script for drunk-xmpp library.
Tests: connection and OMEMO initialization and neary everything else
Uses relatively ugly interface but sufficce for basic feature tests
See help function below to get an idea what it can do
"""

import asyncio
import logging
import sys
import yaml
from pathlib import Path

# Add parent directory to path to import drunk_xmpp module
sys.path.insert(0, '..')

# Import from refactored drunk_xmpp package
from drunk_xmpp import (
    DrunkXMPP,
    create_registration_session,
    query_registration_form,
    submit_registration,
    close_registration_session,
    change_password,
    delete_account
)


def load_config(config_path: str) -> dict:
    """Load YAML config file."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)

    # Store config directory for relative paths
    config['_config_dir'] = config_file.parent.absolute()
    return config


def setup_logging(config: dict) -> None:
    """Setup logging from config."""
    logging_config = config.get('logging', {})
    level_str = logging_config.get('level', 'INFO')
    level = getattr(logging, level_str.upper(), logging.INFO)

    config_dir = config.get('_config_dir', Path.cwd())

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    # Console handler
    console_config = logging_config.get('console', {})
    if console_config.get('enabled', True):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # File handler
    file_config = logging_config.get('file', {})
    if file_config.get('enabled', False):
        log_file = Path(file_config.get('path', 'test-drunk-xmpp-logs/drunk-xmpp.log'))
        if not log_file.is_absolute():
            log_file = config_dir / log_file

        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # XML/Protocol logging
    xml_config = logging_config.get('xml', {})
    if xml_config.get('enabled', False):
        xml_log_file = Path(xml_config.get('path', 'test-drunk-xmpp-logs/xmpp-protocol.log'))
        if not xml_log_file.is_absolute():
            xml_log_file = config_dir / xml_log_file

        xml_log_file.parent.mkdir(parents=True, exist_ok=True)

        xml_logger = logging.getLogger('slixmpp.xmlstream.xmlstream')
        xml_logger.setLevel(logging.DEBUG)

        xml_handler = logging.FileHandler(xml_log_file)
        xml_handler.setLevel(logging.DEBUG)
        xml_formatter = logging.Formatter('%(asctime)s - %(message)s')
        xml_handler.setFormatter(xml_formatter)
        xml_logger.addHandler(xml_handler)


def print_help_grouped():
    """Print available commands grouped by category."""
    print()
    print("=" * 60)
    print("AVAILABLE COMMANDS (grouped by category)")
    print("=" * 60)
    print()

    print("CONNECTION:")
    print("  /connect                     - Reconnect to server")
    print("  /connected?                  - Check connection state (debug)")
    print("  /disconnect                  - Disconnect (user-initiated, no auto-reconnect)")
    print("  /keepalive?                  - Test auto-reconnect (disconnect but keep auto-reconnect)")
    print("  /quit                        - Exit")
    print()

    print("MESSAGING (1-to-1):")
    print("  /send <jid> <message>        - Send plaintext message")
    print("  /sendenc <jid> <message>     - Send OMEMO-encrypted message")
    print("  /reply <jid> <1|2> <msg>     - Reply to last (1) or 2nd last (2) message")
    print("  /replyenc <jid> <1|2> <msg>  - Reply with OMEMO encryption")
    print("  /edit <jid> <new_text>       - Edit last sent message (preserves encryption)")
    print("  /editid <jid> <1|2|3> <text> - Edit sent message by index (1=last, 2=2nd, 3=3rd)")
    print("  /react <jid> <1|2> <emoji>   - React to message with emoji")
    print("  /unreact <jid> <1|2>         - Remove reactions from message")
    print()

    print("MUC/ROOMS:")
    print("  /join <room> <nick> [pass]   - Join MUC room")
    print("  /leave <room>                - Leave MUC room")
    print("  /sendmuc <room> <message>    - Send plaintext to MUC room")
    print("  /sendmucenc <room> <message> - Send OMEMO-encrypted to MUC room")
    print("  /bookmarks                   - List server bookmarks")
    print("  /bookmark-add <jid> <name> <nick> [password] - Add/update bookmark")
    print("  /bookmark-rm <jid>           - Remove bookmark")
    print("  /room-features <room_jid>    - Query MUC room features (OMEMO compatibility)")
    print()

    print("FILE TRANSFER:")
    print("  /file <jid> <path>           - Send file (plaintext)")
    print("  /fileenc <jid> <path>        - Send file (OMEMO encrypted)")
    print()

    print("OMEMO/SECURITY:")
    print("  /discover <jid>              - Discover OMEMO devices for JID")
    print("  /getdev <jid>                - Get OMEMO devices for JID (using drunk-xmpp method)")
    print("  /getowndev                   - Get own OMEMO devices")
    print("  /block <jid>                 - Block contact (XEP-0191)")
    print("  /unblock <jid>               - Unblock contact (XEP-0191)")
    print("  /blocked                     - List blocked contacts (XEP-0191)")
    print()

    print("ROSTER/SUBSCRIPTION:")
    print("  /subscribe <jid>             - Request presence subscription (RFC 6121)")
    print("  /approve <jid>               - Approve subscription request (RFC 6121)")
    print("  /deny <jid>                  - Deny subscription request (RFC 6121)")
    print("  /unsubscribe <jid>           - Cancel our subscription (RFC 6121)")
    print("  /revoke <jid>                - Revoke their subscription (RFC 6121)")
    print()

    print("REGISTRATION (XEP-0077):")
    print("  /register-query <server>     - Create session and query registration form")
    print("  /register-submit <user> <pass> [email] [ocr=SOL] - Submit registration (session-based)")
    print("  /change-password <jid> <old> <new> - Change password for existing account")
    print("  /delete-account <jid> <password> - Delete account permanently (WARNING!)")
    print()

    print("SERVER/DISCOVERY:")
    print("  /server-version              - Query server software version (XEP-0092)")
    print("  /server-features             - Query server features/XEPs (XEP-0030)")
    print("  /mam-check <jid>             - Check if JID supports MAM")
    print("  /history <jid> [max]         - Retrieve MAM history (default: 50 messages)")
    print("  /avatar <jid>                - Fetch avatar for JID (XEP-0084/0153)")
    print()

    print("ADVANCED/DEBUG:")
    print("  /typing <jid>                - Send 'composing' chat state (typing)")
    print("  /active <jid>                - Send 'active' chat state (stopped typing)")
    print("  /receipt <jid> <msg_id>      - Send delivery receipt")
    print("  /marker <jid> <msg_id> <type> - Send chat marker (received/displayed/acknowledged)")
    print("  /carbons                     - Show carbon copy status (XEP-0280)")
    print()

    print("HELP:")
    print("  /help                        - Show this help (grouped by category)")
    print("  /helpa                       - Show all commands alphabetically")
    print()
    print("=" * 60)
    print()


def print_help_alphabetical():
    """Print all available commands in alphabetical order."""
    print()
    print("=" * 60)
    print("AVAILABLE COMMANDS (alphabetical)")
    print("=" * 60)
    print()

    commands = [
        "/active <jid>                - Send 'active' chat state (stopped typing)",
        "/approve <jid>               - Approve subscription request (RFC 6121)",
        "/avatar <jid>                - Fetch avatar for JID (XEP-0084/0153)",
        "/block <jid>                 - Block contact (XEP-0191)",
        "/blocked                     - List blocked contacts (XEP-0191)",
        "/bookmark-add <jid> <name> <nick> [password] - Add/update bookmark",
        "/bookmark-rm <jid>           - Remove bookmark",
        "/bookmarks                   - List server bookmarks",
        "/carbons                     - Show carbon copy status (XEP-0280)",
        "/connect                     - Reconnect to server",
        "/connected?                  - Check connection state (debug)",
        "/deny <jid>                  - Deny subscription request (RFC 6121)",
        "/disconnect                  - Disconnect (user-initiated, no auto-reconnect)",
        "/discover <jid>              - Discover OMEMO devices for JID",
        "/edit <jid> <new_text>       - Edit last sent message (preserves encryption)",
        "/editid <jid> <1|2|3> <text> - Edit sent message by index (1=last, 2=2nd, 3=3rd)",
        "/file <jid> <path>           - Send file (plaintext)",
        "/fileenc <jid> <path>        - Send file (OMEMO encrypted)",
        "/getdev <jid>                - Get OMEMO devices for JID (using drunk-xmpp method)",
        "/getowndev                   - Get own OMEMO devices",
        "/help                        - Show help grouped by category",
        "/helpa                       - Show all commands alphabetically",
        "/history <jid> [max]         - Retrieve MAM history (default: 50 messages)",
        "/join <room> <nick> [pass]   - Join MUC room",
        "/keepalive?                  - Test auto-reconnect (disconnect but keep auto-reconnect)",
        "/leave <room>                - Leave MUC room",
        "/mam-check <jid>             - Check if JID supports MAM",
        "/marker <jid> <msg_id> <type> - Send chat marker (received/displayed/acknowledged)",
        "/quit                        - Exit",
        "/react <jid> <1|2> <emoji>   - React to message with emoji",
        "/receipt <jid> <msg_id>      - Send delivery receipt",
        "/register-query <server>     - Create session and query form (XEP-0077)",
        "/register-submit <user> <pass> [email] [ocr=SOL] - Submit registration, session-based (XEP-0077)",
        "/change-password <jid> <old> <new> - Change password for existing account (XEP-0077)",
        "/delete-account <jid> <password> - Delete account permanently (XEP-0077)",
        "/reply <jid> <1|2> <msg>     - Reply to last (1) or 2nd last (2) message",
        "/replyenc <jid> <1|2> <msg>  - Reply with OMEMO encryption",
        "/revoke <jid>                - Revoke their subscription (RFC 6121)",
        "/room-features <room_jid>    - Query MUC room features (OMEMO compatibility)",
        "/send <jid> <message>        - Send plaintext message",
        "/sendenc <jid> <message>     - Send OMEMO-encrypted message",
        "/sendmuc <room> <message>    - Send plaintext to MUC room",
        "/sendmucenc <room> <message> - Send OMEMO-encrypted to MUC room",
        "/server-features             - Query server features/XEPs (XEP-0030)",
        "/server-version              - Query server software version (XEP-0092)",
        "/subscribe <jid>             - Request presence subscription (RFC 6121)",
        "/typing <jid>                - Send 'composing' chat state (typing)",
        "/unblock <jid>               - Unblock contact (XEP-0191)",
        "/unreact <jid> <1|2>         - Remove reactions from message",
        "/unsubscribe <jid>           - Cancel our subscription (RFC 6121)",
    ]

    for cmd in commands:
        print(f"  {cmd}")

    print()
    print("=" * 60)
    print()


async def main():
    """Main test function - connect and wait for OMEMO init."""

    # Load config
    config = load_config('test-drunk-xmpp.conf')
    setup_logging(config)

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Starting drunk-xmpp minimal test")
    logger.info("=" * 60)

    xmpp_config = config['xmpp']

    # Message callback (for MUC messages - both live and history)
    async def on_message(room, nick, body, metadata, msg):
        # Extract message ID from metadata (XEP-0359)
        # Lookup preference: stanza_id → origin_id → message_id
        msg_id = metadata.stanza_id or metadata.origin_id or metadata.message_id

        if msg_id and room:
            if room not in message_tracking:
                message_tracking[room] = []
            message_tracking[room].append({'id': msg_id, 'body': body})
            logger.debug(f"Tracked MUC message {msg_id} from {room}/{nick}: {body[:30] if body else '(attachment)'}...")
            # Keep only last 2
            if len(message_tracking[room]) > 2:
                message_tracking[room] = message_tracking[room][-2:]

        print()  # Newline before message
        print("=" * 60)

        # Show metadata flags
        msg_type = "[HISTORY]" if metadata.is_history else "[LIVE]"
        encryption = f"[ENCRYPTED {metadata.encryption_type.upper()}]" if metadata.is_encrypted else "[PLAINTEXT]"

        # Show occupant-id if present (for multi-device detection)
        occupant_info = f" occupant_id={metadata.occupant_id[:8]}..." if metadata.occupant_id else ""

        print(f"[MUC] {msg_type} {encryption} {room}{occupant_info}")
        print(f"From: {nick}")
        if body:
            print(f"{body}")
        if metadata.has_attachment:
            print(f"[Attachment: {metadata.attachment_url}]")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)  # Reprint prompt

    # Message tracking for incoming messages (last 2 messages per JID)
    message_tracking = {}

    # Sent message tracking for editing (last 3 per JID with encryption status)
    sent_message_tracking = {}

    # Registration session tracking (for XEP-0077)
    active_reg_session = None
    active_reg_server = None

    def track_sent_message(jid, msg_id, body, encrypted):
        """Track sent message for later editing."""
        if jid not in sent_message_tracking:
            sent_message_tracking[jid] = []
        sent_message_tracking[jid].append({
            'id': msg_id,
            'body': body,
            'encrypted': encrypted
        })
        # Keep only last 3
        if len(sent_message_tracking[jid]) > 3:
            sent_message_tracking[jid] = sent_message_tracking[jid][-3:]
        logger.debug(f"Tracked sent message {msg_id} to {jid} (encrypted: {encrypted})")

    # Private message callback (for 1-to-1 chat AND carbon copies)
    async def on_private_message(from_jid, body, metadata, msg):
        # Extract message ID from metadata (XEP-0359)
        # Lookup preference: origin_id → stanza_id → message_id
        msg_id = metadata.origin_id or metadata.stanza_id or metadata.message_id

        if msg_id and from_jid:
            if from_jid not in message_tracking:
                message_tracking[from_jid] = []
            message_tracking[from_jid].append({'id': msg_id, 'body': body})
            logger.debug(f"Tracked message {msg_id} from {from_jid}: {body[:30] if body else '(attachment)'}...")
            logger.debug(f"  Total tracked for {from_jid}: {len(message_tracking[from_jid])}")
            # Keep only last 2
            if len(message_tracking[from_jid]) > 2:
                message_tracking[from_jid] = message_tracking[from_jid][-2:]

        print()  # Newline before message
        print("=" * 60)

        # Show carbon copy info
        if metadata.is_carbon:
            carbon_label = f"[CARBON {metadata.carbon_type.upper()}]"
            print(f"{carbon_label} ", end="")

        # Show encryption
        if metadata.is_encrypted:
            print(f"[ENCRYPTED {metadata.encryption_type.upper()}] ", end="")
        else:
            print(f"[PLAINTEXT] ", end="")

        # Show direction based on carbon type
        if metadata.is_carbon and metadata.carbon_type == 'sent':
            print(f"Message to {from_jid}:")
        else:
            print(f"Message from {from_jid}:")

        # Show if this is a reply
        if metadata.is_reply:
            print(f"[REPLY to: {metadata.reply_to_id}]")

        if body:
            print(f"{body}")
        if metadata.has_attachment:
            print(f"[Attachment: {metadata.attachment_url}]")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)  # Reprint prompt

    # NOTE: Carbon copy handlers are NO LONGER NEEDED (as of 2025-12-16)
    # DrunkXMPP now calls on_private_message_callback for carbon copies
    # with metadata.is_carbon=True and metadata.carbon_type='sent'/'received'
    # The old slixmpp event handlers below are kept for reference but not used.

    # Receipt received callback (XEP-0184)
    def on_receipt_received(from_jid, message_id):
        """Handler for delivery receipts."""
        print()
        print("=" * 60)
        print(f"🎉 BEEEP DELIVERY RECEIPT RECEIVED! 🎉")
        print(f"From: {from_jid}")
        print(f"Message ID: {message_id}")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # Marker received callback (XEP-0333)
    def on_marker_received(from_jid, message_id, marker_type):
        """Handler for chat markers (read receipts)."""
        marker_emoji = {
            'received': '📬',
            'displayed': '👁️',
            'acknowledged': '✅'
        }.get(marker_type, '📍')

        print()
        print("=" * 60)
        print(f"{marker_emoji} BEEEP CHAT MARKER RECEIVED: {marker_type.upper()} {marker_emoji}")
        print(f"From: {from_jid}")
        print(f"Message ID: {message_id}")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # Server ACK callback (XEP-0198)
    def on_server_ack(ack_info):
        """Handler for server acknowledgements."""
        print()
        print("=" * 60)
        print(f"✓ SERVER ACK RECEIVED!")
        print(f"Message ID: {ack_info.msg_id}")
        print(f"Server confirmed message reached the server")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # Presence changed callback (RFC 6121)
    async def on_presence_changed(from_jid, show):
        """Handler for contact presence changes."""
        presence_emoji = {
            'available': '🟢',
            'away': '🟡',
            'xa': '🟠',
            'dnd': '🔴',
            'unavailable': '⚫'
        }.get(show, '⚪')

        print()
        print("=" * 60)
        print(f"{presence_emoji} PRESENCE CHANGED: {show.upper()} {presence_emoji}")
        print(f"Contact: {from_jid}")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # Bookmarks received callback (XEP-0402)
    async def on_bookmarks_received(bookmarks):
        """Handler for bookmarks received from server."""
        print()
        print("=" * 60)
        print(f"📚 BOOKMARKS RECEIVED FROM SERVER (XEP-0402)")
        if bookmarks:
            print(f"Found {len(bookmarks)} bookmarks:")
            for i, bm in enumerate(bookmarks, 1):
                autojoin = "✓" if bm['autojoin'] else "✗"
                print(f"  [{i}] {autojoin} {bm['jid']}")
                print(f"      Name: {bm['name']}")
                print(f"      Nick: {bm['nick']}")
                if bm.get('password'):
                    print(f"      Password: ***")
        else:
            print("No bookmarks on server")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # MUC invite callback (XEP-0045)
    async def on_muc_invite(room_jid, inviter_jid, reason, password):
        """Handler for MUC invitations."""
        print()
        print("=" * 60)
        print(f"💌 MUC INVITE RECEIVED!")
        print(f"Room: {room_jid}")
        print(f"From: {inviter_jid}")
        if reason:
            print(f"Reason: {reason}")
        if password:
            print(f"Password: *** (protected)")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    def on_reaction(metadata, message_id, emojis):
        """
        Handler for incoming reactions (XEP-0444).

        Args:
            metadata: MessageMetadata with sender info (from_jid, muc_nick, occupant_id)
            message_id: ID of message being reacted to
            emojis: List of emoji strings (empty if reactions removed)
        """
        # Determine display name
        if metadata.message_type == 'groupchat':
            display_from = f"{metadata.muc_nick} (MUC)"
            if metadata.occupant_id:
                display_from += f" [occupant-id: {metadata.occupant_id[:8]}...]"
        else:
            display_from = metadata.from_jid

        print()
        print("=" * 60)
        if emojis:
            print(f"👍 REACTION RECEIVED!")
            print(f"From: {display_from}")
            print(f"Message ID: {message_id}")
            print(f"Emojis: {' '.join(emojis)}")
        else:
            print(f"🚫 REACTIONS REMOVED!")
            print(f"From: {display_from}")
            print(f"Message ID: {message_id}")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # Load rooms from config
    rooms = xmpp_config.get('rooms', {}) or {}  # Ensure it's a dict, not None
    if rooms:
        logger.info(f"Configured rooms: {list(rooms.keys())}")
    else:
        logger.info("No rooms configured in config.yaml")

    # Create client with rooms from config
    logger.info("Creating DrunkXMPP client...")
    # Get proxy settings from config (optional)
    proxy_config = xmpp_config.get('proxy', {})
    proxy_type = proxy_config.get('type') if proxy_config else None
    proxy_host = proxy_config.get('host') if proxy_config else None
    proxy_port = proxy_config.get('port') if proxy_config else None
    proxy_username = proxy_config.get('username') if proxy_config else None
    proxy_password = proxy_config.get('password') if proxy_config else None

    if proxy_type and proxy_host and proxy_port:
        logger.info(f"Proxy configured: {proxy_type} {proxy_host}:{proxy_port}")

    client = DrunkXMPP(
        jid=xmpp_config['jid'],
        password=xmpp_config['password'],
        rooms=rooms,
        omemo_storage_path=xmpp_config.get('omemo', {}).get('storage_path'),
        on_message_callback=on_message,
        on_private_message_callback=on_private_message,
        on_receipt_received_callback=on_receipt_received,
        on_marker_received_callback=on_marker_received,
        on_server_ack_callback=on_server_ack,
        on_presence_changed_callback=on_presence_changed,
        on_bookmarks_received_callback=on_bookmarks_received,
        on_muc_invite_callback=on_muc_invite,
        on_reaction_callback=on_reaction,
        enable_omemo=xmpp_config.get('omemo', {}).get('enabled', True),
        allow_any_message_editing=xmpp_config.get('message_editing', {}).get('allow_any_message', False),
        reconnect_max_delay=xmpp_config.get('reconnect_max_delay', 300),
        keepalive_interval=xmpp_config.get('keepalive_interval', 60),
        muc_history_default=xmpp_config.get('muc_history_default', 5),  # Default 5 history messages
        proxy_type=proxy_type,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_username=proxy_username,
        proxy_password=proxy_password,
    )

    # NOTE: Carbon copy event handlers NO LONGER REGISTERED (as of 2025-12-16)
    # DrunkXMPP now handles carbons internally and calls on_private_message_callback
    # with metadata.is_carbon=True
    # client.add_event_handler("carbon_received", on_carbon_received)  # REMOVED
    # client.add_event_handler("carbon_sent", on_carbon_sent)  # REMOVED

    # Subscription event handlers (roster management)
    def on_presence_subscribe(presence):
        """Handler for incoming subscription requests."""
        from_jid = presence['from'].bare
        print()
        print("=" * 60)
        print(f"[SUBSCRIPTION REQUEST] from {from_jid}")
        print("  (Auto-accepting in test - GUI should show dialog)")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)
        # Auto-accept for testing
        client.send_presence_subscription(pto=from_jid, ptype='subscribed')
        # Also subscribe back (mutual subscription)
        client.send_presence_subscription(pto=from_jid, ptype='subscribe')

    def on_presence_subscribed(presence):
        """Handler for subscription approval."""
        from_jid = presence['from'].bare
        print()
        print("=" * 60)
        print(f"[SUBSCRIPTION APPROVED] {from_jid} accepted your request")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    def on_presence_unsubscribe(presence):
        """Handler for unsubscription requests."""
        from_jid = presence['from'].bare
        print()
        print("=" * 60)
        print(f"[UNSUBSCRIPTION REQUEST] from {from_jid}")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)
        # Auto-confirm for testing
        client.send_presence_subscription(pto=from_jid, ptype='unsubscribed')

    def on_presence_unsubscribed(presence):
        """Handler for unsubscription confirmation."""
        from_jid = presence['from'].bare
        print()
        print("=" * 60)
        print(f"[UNSUBSCRIBED] {from_jid} removed you from contacts")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    def on_changed_subscription(presence):
        """Handler for roster subscription changes."""
        from_jid = presence['from'].bare if presence['from'] else 'unknown'
        # Get subscription from roster, not from presence stanza
        roster = client.client_roster
        subscription = 'none'
        if from_jid in roster:
            subscription = roster[from_jid].get('subscription', 'none')
        print()
        print("=" * 60)
        print(f"[ROSTER UPDATED] {from_jid}: subscription={subscription}")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    client.add_event_handler("presence_subscribe", on_presence_subscribe)
    client.add_event_handler("presence_subscribed", on_presence_subscribed)
    client.add_event_handler("presence_unsubscribe", on_presence_unsubscribe)
    client.add_event_handler("presence_unsubscribed", on_presence_unsubscribed)
    client.add_event_handler("changed_subscription", on_changed_subscription)

    # Connect
    server = xmpp_config.get('server')
    port = xmpp_config.get('port', 5222)

    logger.info(f"Connecting to {server}:{port} as {xmpp_config['jid']}...")

    if server:
        client.connect((server, port))
    else:
        client.connect()

    # Wait for connection
    logger.info("Waiting for connection...")
    await asyncio.sleep(3)

    if not client.is_connected():
        logger.error("Failed to connect!")
        return

    logger.info(" Connected to XMPP server!")
    logger.info(f"  JID: {client.boundjid.bare}")
    logger.info(f"  OMEMO enabled: {client.omemo_enabled}")

    # Wait for OMEMO to initialize
    if client.omemo_enabled:
        logger.info("Waiting for OMEMO to initialize...")
        for i in range(30):
            if client.is_omemo_ready():
                logger.info(" OMEMO ready!")
                break
            logger.info(f"  Still waiting... ({i+1}/30)")
            await asyncio.sleep(1)
        else:
            logger.warning("OMEMO not ready after 30 seconds")

    # Status summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Connection test complete!")
    logger.info("=" * 60)
    logger.info(f"  Connected: {client.is_connected()}")
    logger.info(f"  OMEMO enabled: {client.omemo_enabled}")
    logger.info(f"  OMEMO ready: {client.is_omemo_ready()}")

    # Show help on startup
    print_help_grouped()

    # Interactive loop
    while True:
        try:
            # Read command from stdin
            command = await asyncio.get_event_loop().run_in_executor(
                None, input, "drunk-xmpp> "
            )

            command = command.strip()

            if not command:
                continue

            if command == "/help":
                print_help_grouped()

            elif command == "/helpa":
                print_help_alphabetical()

            elif command == "/disconnect":
                logger.info("Disconnecting (user-initiated, no auto-reconnect)...")
                client.disconnect(disable_auto_reconnect=True)
                logger.info("✓ Disconnected. Use /connect to reconnect.")

            elif command == "/connected?":
                logger.info("Connection state check:")
                logger.info(f"  _connection_state (internal): {client._connection_state}")
                logger.info(f"  is_connected() (public): {client.is_connected()}")
                logger.info(f"  omemo_ready: {client.omemo_ready}")
                logger.info(f"  joined_rooms: {list(client.joined_rooms) if client.joined_rooms else []}")

            elif command == "/connect":
                if client.is_connected():
                    logger.warning("Client reports already connected - attempting reconnect anyway...")

                logger.info("Reconnecting to server...")
                server = xmpp_config.get('server')
                port = xmpp_config.get('port', 5222)

                if server:
                    client.connect((server, port))
                else:
                    client.connect()

                # Wait for connection
                await asyncio.sleep(3)

                if client.is_connected():
                    logger.info(f"✓ Reconnected as {client.boundjid.bare}")
                else:
                    logger.error("Failed to reconnect!")

            elif command == "/keepalive?":
                logger.info("Testing auto-reconnect: disconnecting but keeping auto-reconnect enabled...")
                logger.info("XEP-0199 should trigger reconnection after ping timeout (~90s)")
                client.disconnect(disable_auto_reconnect=False)
                logger.info("✓ Disconnected. Watch for automatic reconnection...")

            elif command == "/quit":
                logger.info("Disconnecting...")
                client.disconnect(disable_auto_reconnect=True)
                break

            elif command.startswith("/send "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /send <jid> <message>")
                    continue

                _, jid, message = parts
                logger.info(f"Sending plaintext to {jid}...")
                try:
                    msg_id = await client.send_private_message(jid, message)
                    track_sent_message(jid, msg_id, message, encrypted=False)
                    logger.info("✓ Sent!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/sendenc "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /sendenc <jid> <message>")
                    continue

                _, jid, message = parts
                logger.info(f"Sending OMEMO-encrypted to {jid}...")
                try:
                    msg_id = await client.send_encrypted_private_message(jid, message)
                    track_sent_message(jid, msg_id, message, encrypted=True)
                    logger.info("✓ Sent!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/join "):
                parts = command.split(None, 3)
                if len(parts) < 3:
                    logger.error("Usage: /join <room_jid> <nick> [password]")
                    continue

                room_jid = parts[1]
                nick = parts[2]
                password = parts[3] if len(parts) > 3 else None

                logger.info(f"Joining MUC {room_jid} as {nick}...")
                try:
                    await client.join_room(room_jid, nick, password)
                    logger.info(f"✓ Joined {room_jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/leave "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /leave <room_jid>")
                    continue

                room_jid = parts[1]
                logger.info(f"Leaving MUC {room_jid}...")
                try:
                    client.leave_room(room_jid)
                    logger.info(f"✓ Left {room_jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/sendmuc "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /sendmuc <room_jid> <message>")
                    continue

                _, room_jid, message = parts
                logger.info(f"Sending plaintext to MUC {room_jid}...")
                try:
                    msg_id = await client.send_to_muc(room_jid, message)
                    track_sent_message(room_jid, msg_id, message, encrypted=False)
                    logger.info("✓ Sent!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/sendmucenc "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /sendmucenc <room_jid> <message>")
                    continue

                _, room_jid, message = parts
                logger.info(f"Sending OMEMO-encrypted to MUC {room_jid}...")
                try:
                    msg_id = await client.send_encrypted_to_muc(room_jid, message)
                    track_sent_message(room_jid, msg_id, message, encrypted=True)
                    logger.info("✓ Sent!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/file "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /file <jid> <path>")
                    continue

                _, jid, filepath = parts
                logger.info(f"Sending file {filepath} to {jid}...")
                try:
                    await client.send_attachment_to_user(jid, filepath)
                    logger.info("✓ File sent!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/fileenc "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /fileenc <jid> <path>")
                    continue

                _, jid, filepath = parts
                logger.info(f"Sending OMEMO-encrypted file {filepath} to {jid}...")
                try:
                    await client.send_encrypted_file(jid, filepath)
                    logger.info("✓ Encrypted file sent!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/reply ") or command.startswith("/replyenc "):
                is_encrypted = command.startswith("/replyenc")
                parts = command.split(None, 3)
                if len(parts) < 4:
                    logger.error(f"Usage: {'/replyenc' if is_encrypted else '/reply'} <jid> <1|2> <message>")
                    continue

                _, jid, msg_index, reply_body = parts
                try:
                    msg_index = int(msg_index)
                    if msg_index not in (1, 2):
                        logger.error("Message index must be 1 or 2")
                        continue

                    # Get tracked message
                    if jid not in message_tracking or len(message_tracking[jid]) < msg_index:
                        logger.error(f"No message #{msg_index} found for {jid}")
                        continue

                    tracked_msg = message_tracking[jid][-msg_index]
                    msg_id = tracked_msg['id']
                    fallback_body = tracked_msg['body']

                    logger.info(f"Sending {'encrypted ' if is_encrypted else ''}reply to {jid} (msg: {msg_id})")
                    try:
                        await client.send_reply(jid, msg_id, reply_body, fallback_body, encrypt=is_encrypted)
                        logger.info("✓ Reply sent!")
                    except Exception as e:
                        logger.error(f"Failed: {e}")

                except ValueError:
                    logger.error("Message index must be a number (1 or 2)")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/react "):
                parts = command.split(None, 3)
                if len(parts) < 4:
                    logger.error("Usage: /react <jid> <1|2> <emoji>")
                    continue

                _, jid, msg_index, emoji = parts
                try:
                    msg_index = int(msg_index)
                    if msg_index not in (1, 2):
                        logger.error("Message index must be 1 or 2")
                        continue

                    # Get tracked message
                    if jid not in message_tracking or len(message_tracking[jid]) < msg_index:
                        logger.error(f"No message #{msg_index} found for {jid}")
                        continue

                    tracked_msg = message_tracking[jid][-msg_index]
                    msg_id = tracked_msg['id']

                    logger.info(f"Sending reaction {emoji} to {jid} (msg: {msg_id})")
                    try:
                        client.send_reaction(jid, msg_id, emoji)
                        logger.info("✓ Reaction sent!")
                    except Exception as e:
                        logger.error(f"Failed: {e}")

                except ValueError:
                    logger.error("Message index must be a number (1 or 2)")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/unreact "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /unreact <jid> <1|2>")
                    continue

                _, jid, msg_index = parts
                try:
                    msg_index = int(msg_index)
                    if msg_index not in (1, 2):
                        logger.error("Message index must be 1 or 2")
                        continue

                    # Get tracked message
                    if jid not in message_tracking or len(message_tracking[jid]) < msg_index:
                        logger.error(f"No message #{msg_index} found for {jid}")
                        continue

                    tracked_msg = message_tracking[jid][-msg_index]
                    msg_id = tracked_msg['id']

                    logger.info(f"Removing reactions from {jid} (msg: {msg_id})")
                    try:
                        client.remove_reaction(jid, msg_id)
                        logger.info("✓ Reactions removed!")
                    except Exception as e:
                        logger.error(f"Failed: {e}")

                except ValueError:
                    logger.error("Message index must be a number (1 or 2)")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/edit "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /edit <jid> <new_text>")
                    continue

                _, jid, new_text = parts

                # Get last sent message to this JID
                if jid not in sent_message_tracking or len(sent_message_tracking[jid]) == 0:
                    logger.error(f"No sent messages to {jid} to edit")
                    continue

                last_sent = sent_message_tracking[jid][-1]
                msg_id = last_sent['id']
                encrypted = last_sent['encrypted']

                logger.info(f"Editing last message to {jid} (id: {msg_id}, encrypted: {encrypted})...")
                try:
                    await client.edit_message(jid, msg_id, new_text, encrypt=encrypted)
                    # Update tracked message body
                    last_sent['body'] = new_text
                    logger.info("✓ Message edited!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/editid "):
                parts = command.split(None, 3)
                if len(parts) < 4:
                    logger.error("Usage: /editid <jid> <1|2|3> <new_text>")
                    logger.error("  Edit message: 1=last, 2=2nd last, 3=3rd last")
                    continue

                _, jid, msg_index, new_text = parts
                try:
                    msg_index = int(msg_index)
                    if msg_index not in (1, 2, 3):
                        logger.error("Message index must be 1, 2, or 3")
                        continue

                    # Get specified sent message
                    if jid not in sent_message_tracking or len(sent_message_tracking[jid]) < msg_index:
                        logger.error(f"No message #{msg_index} found for {jid}")
                        continue

                    target_msg = sent_message_tracking[jid][-msg_index]
                    msg_id = target_msg['id']
                    encrypted = target_msg['encrypted']

                    logger.info(f"Editing message #{msg_index} to {jid} (id: {msg_id}, encrypted: {encrypted})...")
                    try:
                        await client.edit_message(jid, msg_id, new_text, encrypt=encrypted)
                        # Update tracked message body
                        target_msg['body'] = new_text
                        logger.info("✓ Message edited!")
                    except Exception as e:
                        logger.error(f"Failed: {e}")

                except ValueError:
                    logger.error("Message index must be a number (1, 2, or 3)")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/discover "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /discover <jid>")
                    continue

                _, jid = parts
                logger.info(f"Discovering OMEMO devices for {jid}...")
                try:
                    from slixmpp.jid import JID
                    xep_0384 = client.plugin['xep_0384']
                    recipient_jid = JID(jid)

                    # Get session manager
                    session_manager = await xep_0384.get_session_manager()

                    logger.info(f"  Step 1: Refreshing device lists for: {recipient_jid.bare}")
                    # Refresh device lists across all backends (both OMEMO 0.3 and 0.8)
                    await session_manager.refresh_device_lists(recipient_jid.bare)

                    logger.info(f"  Step 2: Getting cached device information...")
                    # Now get the cached device information
                    device_info = await session_manager.get_device_information(recipient_jid.bare)

                    logger.info(f"  Device information for {recipient_jid.bare}:")
                    if device_info:
                        for device in device_info:
                            logger.info(f"    - Device ID: {device.device_id}")
                            logger.info(f"      Label: {device.label if hasattr(device, 'label') else 'N/A'}")
                    else:
                        logger.info(f"    No devices found")

                    logger.info("✓ Discovery complete!")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/getdev "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /getdev <jid>")
                    continue

                _, jid = parts
                logger.info(f"Getting OMEMO devices for {jid} using drunk-xmpp method...")
                try:
                    devices = await client.get_omemo_devices(jid)

                    if devices:
                        logger.info(f"✓ Found {len(devices)} device(s) for {jid}:")
                        for device in devices:
                            logger.info(f"  Device ID: {device['device_id']}")
                            logger.info(f"    Identity Key: {device['identity_key'][:50]}...")
                            logger.info(f"    Trust Level: {device['trust_level']}")
                            logger.info(f"    Label: {device['label'] or 'N/A'}")
                            logger.info(f"    Active: {device['active']}")
                            logger.info("")
                    else:
                        logger.info(f"  No devices found for {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command == "/getowndev":
                logger.info(f"Getting own OMEMO devices...")
                try:
                    devices = await client.get_own_omemo_devices()

                    if devices:
                        logger.info(f"✓ Found {len(devices)} own device(s):")
                        for device in devices:
                            logger.info(f"  Device ID: {device['device_id']}")
                            logger.info(f"    Identity Key: {device['identity_key'][:50]}...")
                            logger.info(f"    Trust Level: {device['trust_level']}")
                            logger.info(f"    Label: {device['label'] or 'N/A'}")
                            logger.info(f"    Active: {device['active']}")
                            logger.info("")
                    else:
                        logger.info(f"  No own devices found")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/avatar "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /avatar <jid>")
                    continue

                _, jid = parts
                logger.info(f"Fetching avatar for {jid}...")
                try:
                    avatar_data = await client.get_avatar(jid)

                    if avatar_data:
                        logger.info(f"✓ Avatar fetched successfully!")
                        logger.info(f"  Source: {avatar_data['source'].upper()}")
                        logger.info(f"  MIME type: {avatar_data['mime_type']}")
                        logger.info(f"  Size: {len(avatar_data['data'])} bytes")
                        logger.info(f"  SHA-1 hash: {avatar_data['hash']}")
                        logger.info("")
                        logger.info(f"  (Avatar image data not displayed in CLI)")
                    else:
                        logger.info(f"  No avatar found for {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/room-features "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /room-features <room_jid>")
                    continue

                _, room_jid = parts
                logger.info(f"Querying room features for {room_jid}...")
                try:
                    features = await client.get_room_features(room_jid)

                    if 'error' in features:
                        logger.error(f"✗ Failed to query room: {features['error']}")
                    else:
                        logger.info(f"✓ Room features for {room_jid}:")
                        logger.info(f"  Non-anonymous: {features['muc_nonanonymous']} {'(REQUIRED for OMEMO)' if features['muc_nonanonymous'] else '(⚠ OMEMO requires this)'}")
                        logger.info(f"  Members-only: {features['muc_membersonly']} {'(recommended for OMEMO)' if features['muc_membersonly'] else '(⚠ OMEMO recommends this)'}")
                        logger.info(f"  Open: {features['muc_open']}")
                        logger.info(f"  Password protected: {features['muc_passwordprotected']}")
                        logger.info(f"  Hidden: {features['muc_hidden']}")
                        logger.info(f"  Public: {features['muc_public']}")
                        logger.info(f"  Persistent: {features['muc_persistent']}")
                        logger.info(f"  Moderated: {features['muc_moderated']}")
                        logger.info("")
                        if features['supports_omemo']:
                            logger.info(f"  ✓ Room SUPPORTS OMEMO encryption (XEP-0384 compliant)")
                        else:
                            logger.info(f"  ✗ Room does NOT support OMEMO encryption")
                            if not features['muc_nonanonymous']:
                                logger.info(f"    - Room must be non-anonymous (XEP-0384 requirement)")
                            if not features['muc_membersonly']:
                                logger.info(f"    - Room should be members-only (XEP-0384 recommendation)")
                        logger.info("")
                        logger.info(f"  All features: {', '.join(features['features'][:10])}{'...' if len(features['features']) > 10 else ''}")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command == "/server-version":
                logger.info("Querying server software version...")
                try:
                    version = await client.get_server_version()

                    if version.get('error'):
                        logger.error(f"✗ Failed to query server version: {version['error']}")
                    else:
                        logger.info(f"✓ Server version information:")
                        logger.info(f"  Name: {version['name'] or 'N/A'}")
                        logger.info(f"  Version: {version['version'] or 'N/A'}")
                        logger.info(f"  OS: {version['os'] or 'N/A'}")
                        logger.info("")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command == "/server-features":
                logger.info("Querying server features and XEP support...")
                try:
                    features = await client.get_server_features()

                    if features.get('error'):
                        logger.error(f"✗ Failed to query server features: {features['error']}")
                    else:
                        logger.info(f"✓ Server features discovered")
                        logger.info("")

                        # Show identities
                        if features['identities']:
                            logger.info("Server identities:")
                            for identity in features['identities']:
                                name = f" ({identity['name']})" if identity['name'] else ""
                                logger.info(f"  - {identity['category']}/{identity['type']}{name}")
                            logger.info("")

                        # Show recognized XEPs
                        if features['xeps']:
                            logger.info(f"Recognized XEPs ({len(features['xeps'])} total):")
                            for xep in features['xeps']:
                                logger.info(f"  XEP-{xep['number']}: {xep['name']}")
                            logger.info("")
                        else:
                            logger.warning("  No recognized XEPs found")
                            logger.info("")

                        # Show total feature count
                        logger.info(f"Total features: {len(features['features'])}")

                        # Show all raw features
                        if features['features']:
                            logger.info("All raw features:")
                            for feature in features['features']:
                                logger.info(f"  - {feature}")
                        logger.info("")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/mam-check "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /mam-check <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Checking MAM support for {jid}...")
                try:
                    supported = await client.check_mam_support(jid)
                    if supported:
                        logger.info(f"✓ {jid} supports MAM")
                    else:
                        logger.info(f"✗ {jid} does NOT support MAM")
                except Exception as e:
                    logger.error(f"Failed to check MAM support: {e}")

            elif command.startswith("/history "):
                parts = command.split(None, 2)
                if len(parts) < 2:
                    logger.error("Usage: /history <jid> [max_messages]")
                    continue

                jid = parts[1]
                max_messages = 50  # default
                if len(parts) > 2:
                    try:
                        max_messages = int(parts[2])
                    except ValueError:
                        logger.error("max_messages must be a number")
                        continue

                logger.info(f"Retrieving MAM history from {jid} (max: {max_messages})...")
                try:
                    # For 1-1 chats, pass with_jid to filter to this specific contact
                    # For MUC rooms, the jid parameter is sufficient (room archive)
                    history = await client.retrieve_history(jid, max_messages=max_messages, with_jid=jid)
                    logger.info(f"✓ Retrieved {len(history)} messages:")
                    logger.info("")
                    for i, msg in enumerate(history, 1):
                        timestamp = msg['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if msg['timestamp'] else 'Unknown'
                        encrypted_flag = "[ENCRYPTED]" if msg['is_encrypted'] else "[PLAINTEXT]"

                        # Format sender - use nick for MUC, JID for 1-to-1
                        sender = msg['nick'] if msg['nick'] else msg['jid']

                        logger.info(f"  [{i}] {timestamp} {encrypted_flag}")
                        logger.info(f"      From: {sender}")
                        logger.info(f"      {msg['body'][:100]}{'...' if len(msg['body']) > 100 else ''}")
                        logger.info("")

                    if len(history) == 0:
                        logger.info("  (No messages in archive)")
                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command == "/bookmarks":
                logger.info("Fetching bookmarks from server...")
                try:
                    bookmarks = await client.get_bookmarks()
                    if bookmarks:
                        logger.info(f"Found {len(bookmarks)} bookmarks:")
                        for i, bm in enumerate(bookmarks, 1):
                            autojoin = "✓" if bm['autojoin'] else "✗"
                            logger.info(f"  [{i}] {autojoin} {bm['jid']}")
                            logger.info(f"      Name: {bm['name']}")
                            logger.info(f"      Nick: {bm['nick']}")
                            if bm['password']:
                                logger.info(f"      Password: ***")
                    else:
                        logger.info("No bookmarks found")
                except Exception as e:
                    logger.error(f"Failed to fetch bookmarks: {e}")

            elif command.startswith("/bookmark-add "):
                parts = command.split(None, 4)
                if len(parts) < 4:
                    logger.error("Usage: /bookmark-add <jid> <name> <nick> [password]")
                    continue

                jid = parts[1]
                name = parts[2]
                nick = parts[3]
                password = parts[4] if len(parts) > 4 else None

                logger.info(f"Adding bookmark for {jid}...")
                try:
                    await client.add_bookmark(jid, name, nick, password=password, autojoin=True)
                    logger.info("✓ Bookmark added/updated!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/bookmark-rm "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /bookmark-rm <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Removing bookmark for {jid}...")
                try:
                    await client.remove_bookmark(jid)
                    logger.info("✓ Bookmark removed!")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/block "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /block <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Blocking contact {jid}...")
                try:
                    success = await client.block_contact(jid)
                    if success:
                        logger.info(f"✓ Successfully blocked {jid}")
                    else:
                        logger.error(f"✗ Failed to block {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/unblock "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /unblock <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Unblocking contact {jid}...")
                try:
                    success = await client.unblock_contact(jid)
                    if success:
                        logger.info(f"✓ Successfully unblocked {jid}")
                    else:
                        logger.error(f"✗ Failed to unblock {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command == "/blocked":
                logger.info("Retrieving blocked contacts list...")
                try:
                    blocked = await client.get_blocked_contacts()
                    if blocked:
                        logger.info(f"Blocked contacts ({len(blocked)}):")
                        for jid in blocked:
                            logger.info(f"  - {jid}")
                    else:
                        logger.info("No blocked contacts")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/subscribe "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /subscribe <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Requesting presence subscription from {jid}...")
                try:
                    await client.request_subscription(jid)
                    logger.info(f"✓ Subscription request sent to {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/approve "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /approve <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Approving subscription request from {jid}...")
                try:
                    await client.approve_subscription(jid)
                    logger.info(f"✓ Subscription approved for {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/deny "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /deny <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Denying subscription request from {jid}...")
                try:
                    await client.deny_subscription(jid)
                    logger.info(f"✓ Subscription denied for {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/unsubscribe "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /unsubscribe <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Cancelling subscription to {jid}...")
                try:
                    await client.cancel_subscription(jid)
                    logger.info(f"✓ Subscription cancelled for {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/revoke "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /revoke <jid>")
                    continue

                jid = parts[1]
                logger.info(f"Revoking subscription for {jid}...")
                try:
                    await client.revoke_subscription(jid)
                    logger.info(f"✓ Subscription revoked for {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/typing "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /typing <jid>")
                    continue

                jid = parts[1]
                try:
                    client.send_chat_state(jid, 'composing')
                    logger.info(f"✓ Sent 'composing' state to {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/active "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /active <jid>")
                    continue

                jid = parts[1]
                try:
                    client.send_chat_state(jid, 'active')
                    logger.info(f"✓ Sent 'active' state to {jid}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/receipt "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /receipt <jid> <msg_id>")
                    continue

                jid = parts[1]
                msg_id = parts[2]
                try:
                    client.send_receipt(jid, msg_id)
                    logger.info(f"✓ Sent receipt to {jid} for message {msg_id}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/marker "):
                parts = command.split(None, 3)
                if len(parts) < 4:
                    logger.error("Usage: /marker <jid> <msg_id> <type>")
                    logger.error("  Type: received, displayed, or acknowledged")
                    continue

                jid = parts[1]
                msg_id = parts[2]
                marker_type = parts[3]

                try:
                    client.send_marker(jid, msg_id, marker_type)
                    logger.info(f"✓ Sent '{marker_type}' marker to {jid} for message {msg_id}")
                except ValueError as e:
                    logger.error(f"Invalid marker type: {e}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command == "/carbons":
                logger.info("Carbon copy (XEP-0280) status:")
                logger.info("  Carbons allow messages to be synced across multiple devices.")
                logger.info("  When enabled, messages sent/received on other devices appear here.")
                logger.info("")
                try:
                    # Check if carbons are enabled in the plugin
                    if 'xep_0280' in client.plugin:
                        logger.info("  ✓ XEP-0280 (Carbons) plugin loaded")
                        logger.info("  ✓ Carbon handlers registered in DrunkXMPP")
                        logger.info("")
                        logger.info("  Carbons should be automatically enabled on connect.")
                        logger.info("  Try sending an OMEMO message from your phone to test:")
                        logger.info("    1. Send encrypted message from monocles to a contact")
                        logger.info("    2. Watch for [CARBON TX] in logs")
                        logger.info("    3. Message should show decrypted text (not fallback)")
                    else:
                        logger.warning("  ✗ XEP-0280 plugin not loaded")
                except Exception as e:
                    logger.error(f"  Failed to check carbons status: {e}")

            elif command.startswith("/register-query "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /register-query <server>")
                    continue

                server = parts[1]
                logger.info(f"Querying registration form from {server}...")
                logger.info("(This creates a session object to preserve form data)")
                logger.info("")

                try:
                    # Close any existing session first
                    if active_reg_session:
                        logger.info(f"Closing previous registration session for {active_reg_server}...")
                        await close_registration_session(active_reg_session)
                        active_reg_session = None
                        active_reg_server = None

                    # Get proxy settings if configured
                    proxy_settings = None
                    proxy_config = xmpp_config.get('proxy', {})
                    if proxy_config and proxy_config.get('type'):
                        proxy_settings = {
                            'proxy_type': proxy_config.get('type'),
                            'proxy_host': proxy_config.get('host'),
                            'proxy_port': proxy_config.get('port'),
                            'proxy_username': proxy_config.get('username'),
                            'proxy_password': proxy_config.get('password')
                        }

                    # Create registration session
                    logger.info("Creating registration session...")
                    session_result = await create_registration_session(server, proxy_settings)

                    if not session_result['success']:
                        logger.error(f"✗ Failed to connect to {server}:")
                        logger.error(f"  {session_result['error']}")
                        continue

                    active_reg_session = session_result['session_id']
                    active_reg_server = server
                    logger.info(f"✓ Connected to {server} (session: {active_reg_session[:8]}...)")

                    # Query form using session
                    logger.info("Querying registration form...")
                    result = await query_registration_form(active_reg_session)

                    logger.info("=" * 60)
                    if result['success']:
                        logger.info(f"✓ Registration form received from {server}")
                        logger.info("")
                        if result['instructions']:
                            logger.info(f"Instructions: {result['instructions']}")
                            logger.info("")
                        logger.info(f"Required fields ({len(result['fields'])}):")
                        for field_name, field_info in result['fields'].items():
                            required = "REQUIRED" if field_info['required'] else "optional"
                            field_type = field_info.get('type', 'text-single')
                            logger.info(f"  - {field_name}: {field_info['label']} ({required}, type: {field_type})")

                        # Show CAPTCHA info if present
                        if result.get('captcha_data'):
                            captcha = result['captcha_data']
                            logger.info("")
                            logger.info("⚠ CAPTCHA detected:")
                            if captcha.get('media'):
                                for media in captcha['media']:
                                    logger.info(f"  - Media type: {media['type']}")
                                    # Save CAPTCHA image
                                    if media['type'].startswith('image/'):
                                        import tempfile
                                        tmp_path = f"/tmp/xmpp_captcha_{active_reg_session[:8]}.png"
                                        with open(tmp_path, 'wb') as f:
                                            f.write(media['data'])
                                        logger.info(f"  - Saved to: {tmp_path}")
                            logger.info("  - You must include 'ocr' field with CAPTCHA solution in submit")

                        logger.info("")
                        logger.info("Session is active. To register, use:")
                        logger.info(f"  /register-submit <username> <password> [email]")
                        logger.info("")
                        logger.info("Session preserves form data including CAPTCHA challenge IDs")
                    else:
                        logger.error(f"✗ Failed to query registration form:")
                        logger.error(f"  {result['error']}")
                        # Close session on error
                        await close_registration_session(active_reg_session)
                        active_reg_session = None
                        active_reg_server = None
                    logger.info("=" * 60)

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()
                    # Cleanup on error
                    if active_reg_session:
                        try:
                            await close_registration_session(active_reg_session)
                        except:
                            pass
                        active_reg_session = None
                        active_reg_server = None

            elif command.startswith("/register-submit "):
                # Usage: /register-submit <username> <password> [email] [ocr=SOLUTION]
                parts = command.split(None, 4)
                if len(parts) < 3:
                    logger.error("Usage: /register-submit <username> <password> [email] [ocr=SOLUTION]")
                    logger.info("  Note: You must first run /register-query <server>")
                    continue

                if not active_reg_session:
                    logger.error("✗ No active registration session!")
                    logger.error("  You must first run: /register-query <server>")
                    continue

                username = parts[1]
                password = parts[2]
                extra_fields = parts[3] if len(parts) > 3 else None

                logger.info(f"Attempting to register {username}@{active_reg_server}...")
                logger.info(f"Using active session: {active_reg_session[:8]}...")
                logger.info("")

                try:
                    form_data = {
                        'username': username,
                        'password': password
                    }

                    # Parse extra fields (email or ocr=solution)
                    if extra_fields:
                        if '=' in extra_fields:
                            # Parse field=value format (e.g., ocr=SOLUTION)
                            field_name, field_value = extra_fields.split('=', 1)
                            form_data[field_name] = field_value
                        else:
                            # Assume it's email
                            form_data['email'] = extra_fields

                    logger.info("Submitting registration...")
                    result = await submit_registration(active_reg_session, form_data)

                    logger.info("=" * 60)
                    if result['success']:
                        logger.info(f"✓ Registration successful!")
                        logger.info(f"  JID: {result['jid']}")
                        logger.info("")
                        logger.info("You can now login with:")
                        logger.info(f"  Username: {username}@{active_reg_server}")
                        logger.info(f"  Password: {password}")
                        logger.info("")
                        logger.info("Add this to your config and restart, or use account dialog in GUI")

                        # Close session after success
                        logger.info("")
                        logger.info("Closing registration session...")
                        await close_registration_session(active_reg_session)
                        active_reg_session = None
                        active_reg_server = None
                        logger.info("✓ Session closed")
                    else:
                        logger.error(f"✗ Registration failed:")
                        logger.error(f"  {result['error']}")
                        logger.info("")
                        logger.info("Session is still active. You can try again or run /register-query to start over")
                    logger.info("=" * 60)

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()
                    # Keep session alive on error so user can retry

            elif command.startswith("/change-password "):
                parts = command.split(None, 3)
                if len(parts) < 4:
                    logger.error("Usage: /change-password <jid> <old_password> <new_password>")
                    continue

                jid = parts[1]
                old_password = parts[2]
                new_password = parts[3]

                logger.info(f"Attempting to change password for {jid}...")
                logger.info("(This creates a temporary connection separate from your current session)")
                logger.info("")

                try:
                    # Get proxy settings if configured
                    proxy_settings = None
                    proxy_config = xmpp_config.get('proxy', {})
                    if proxy_config and proxy_config.get('type'):
                        proxy_settings = {
                            'proxy_type': proxy_config.get('type'),
                            'proxy_host': proxy_config.get('host'),
                            'proxy_port': proxy_config.get('port'),
                            'proxy_username': proxy_config.get('username'),
                            'proxy_password': proxy_config.get('password')
                        }

                    result = await change_password(jid, old_password, new_password, proxy_settings)

                    logger.info("=" * 60)
                    if result['success']:
                        logger.info(f"✓ Password changed successfully for {jid}!")
                        logger.info("")
                        logger.info("Your account password has been updated.")
                        logger.info("Use the new password for future logins.")
                    else:
                        logger.error(f"✗ Password change failed:")
                        logger.error(f"  {result['error']}")
                    logger.info("=" * 60)

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/delete-account "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /delete-account <jid> <password>")
                    continue

                jid = parts[1]
                password = parts[2]

                # Confirmation prompt
                logger.warning("=" * 60)
                logger.warning("⚠⚠⚠  WARNING: PERMANENT ACCOUNT DELETION  ⚠⚠⚠")
                logger.warning("=" * 60)
                logger.warning(f"You are about to PERMANENTLY DELETE the account:")
                logger.warning(f"  {jid}")
                logger.warning("")
                logger.warning("This will:")
                logger.warning("  - Delete the account from the server")
                logger.warning("  - Remove all associated data")
                logger.warning("  - Cannot be undone!")
                logger.warning("")
                logger.warning("Type 'DELETE' to confirm, or anything else to cancel:")
                logger.warning("=" * 60)

                confirmation = await asyncio.get_event_loop().run_in_executor(
                    None, input, "Confirmation: "
                )

                if confirmation.strip() != "DELETE":
                    logger.info("Account deletion cancelled.")
                    continue

                logger.info("")
                logger.info(f"Deleting account {jid}...")
                logger.info("(This creates a temporary connection separate from your current session)")
                logger.info("")

                try:
                    # Get proxy settings if configured
                    proxy_settings = None
                    proxy_config = xmpp_config.get('proxy', {})
                    if proxy_config and proxy_config.get('type'):
                        proxy_settings = {
                            'proxy_type': proxy_config.get('type'),
                            'proxy_host': proxy_config.get('host'),
                            'proxy_port': proxy_config.get('port'),
                            'proxy_username': proxy_config.get('username'),
                            'proxy_password': proxy_config.get('password')
                        }

                    result = await delete_account(jid, password, proxy_settings)

                    logger.info("=" * 60)
                    if result['success']:
                        logger.info(f"✓ Account {jid} deleted successfully!")
                        logger.info("")
                        logger.info("The account has been permanently removed from the server.")
                        logger.info("All associated data has been deleted.")
                    else:
                        logger.error(f"✗ Account deletion failed:")
                        logger.error(f"  {result['error']}")
                    logger.info("=" * 60)

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            else:
                logger.error(f"Unknown command: {command}")

        except KeyboardInterrupt:
            logger.info("\nReceived Ctrl+C, disconnecting...")
            client.disconnect()
            break
        except Exception as e:
            logger.exception(f"Error: {e}")

    logger.info("Goodbye!")


if __name__ == '__main__':
    asyncio.run(main())
