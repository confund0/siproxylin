#!/usr/bin/env python3
"""
Minimal test script for drunk-xmpp library.
Tests: connection and OMEMO initialization and neary everything else
Uses relatively ugly interface but sufficce for basic feature tests
See help function below to get an idea what it can do

Usage:
    test-drunk-xmpp.py [--config PATH] [--json] [--log-dir DIR]
                       [--verbose] [--events all|none]

    --config   Config file. Default: test-drunk-xmpp.conf in the current dir,
               else the one next to this script.
    --json     Agent mode: JSON lines on stdin and stdout (see below).
    --log-dir  Write drunk-xmpp.log and xmpp-protocol.log to DIR
               (turns both files on, the config paths are not used).
    --verbose  JSON mode: every reply has "logs" (INFO and higher).
    --events   JSON mode: "all" (default) writes each event as its own
               line; "none" writes no event lines (wait replies still
               carry the full event).

End of input (EOF) disconnects and exits with code 0, in both modes.

JSON mode:
    stdout has JSON lines only (one object per line). Logs, help and
    other text go to stderr and the log files.

    Request (one per line on stdin):
        {"id": 1, "cmd": "send", "args": ["bob@localhost", "hi there"]}
      The same as typing "/send bob@localhost hi there". Args are joined
      with spaces, so only the last arg may contain spaces.
      Commands that ask for "DELETE" take it from the request:
        {"id": 2, "cmd": "pep-delete", "args": ["node"], "confirm": "DELETE"}
      PEP checks: "/pep-get <node> <jid>" reads the node of another JID
      (what a contact can read); "/pep-config <node>" logs the config of
      an own node; "/pep-create <node>" creates an own node with the
      server default config.

    Reply (when the command is finished):
        {"id": 1, "done": true, "ok": true, "error": null,
         "t_start": "...", "t_end": "...", "msg_id": "...",
         "sent": [{"t", "kind", "type", "id", "origin_id", "to"}, ...],
         "logs": [{"t", "level", "logger", "msg"}, ...]}
      Only the command itself counts: its own code and the asyncio tasks
      it starts. Stanzas and logs from incoming traffic (slixmpp
      callbacks) are not part of the reply.
      ok is false if the command raised or logged an ERROR.
      "sent" lists the stanzas the command sent (message, presence, iq).
      "msg_id" is the id of the first message stanza in "sent" (null if
      none).
      "logs" is only in the reply when ok is false (WARNING and higher),
      or always with --verbose (INFO and higher).
      Times are local time "YYYY-MM-DD HH:MM:SS.mmm" (the clock of the
      logs), usable with tools/siplog.py --since/--until.

    Event (anything that comes in):
        {"event": "message", "t": "...", "from": ..., "body": ..., ...}
      Events: ready, connected, disconnected, connect_failed, message,
      receipt, marker, server_ack, presence, chat_state, reaction,
      message_error, subscription, roster, bookmarks, muc_invite,
      muc_joined, muc_join_error.
      "message" has the fields it knows: jid, from, to, room, nick, body,
      body_clean, encrypted, decrypt_failed, type, id, stanza_id,
      origin_id, archive_id, replace_id, reply_to_id, attachment_url,
      source (live, carbon, history, mam, correction).
      body_clean is the body without the reply quote (XEP-0461
      fallback); it is only there when a quote was removed.
      /history writes one "message" event per archived message (source
      mam); for a group chat it has "room" too. "/history <jid> 50
      --since-run" asks only for messages since this process started;
      "--start <ISO time>" sets the start (no zone = local time).
      /history skips messages already seen live or as carbon in this
      process (like the app); "--no-skip" turns this off.
      "presence" has from (full JID), jid (bare JID), show, status, type.
      It comes only when one of show, status, type changed for that
      full JID.
      "ready" comes after the login and the OMEMO start (or its timeout).
      Commands sent before "ready" wait in a queue.

    Built-in commands (also in human mode as /wait and /sleep):
        {"id": 3, "cmd": "wait", "event": "message",
         "match": {"body": "hi"}, "timeout": 10}
      Returns the first matching event in "event" of the reply (older
      events not yet taken by a wait count too), or ok=false on timeout.
      The reply has the full event, also when the event line was
      already written (use --events none to get each event only once).
      "match": {"body~": "hi"} means "body contains hi".
        {"id": 4, "cmd": "sleep", "args": [1.5]}
        {"id": 5, "cmd": "quit"}
      Human form: /wait message body~=hi timeout=10

    Example (two processes, bob waits for alice):
        (echo '{"id":1,"cmd":"wait","event":"message","match":{"body":"hi"},"timeout":30}';
         echo '{"id":2,"cmd":"quit"}') | test-drunk-xmpp.py --json --config bob.conf > bob.out &
        (echo '{"id":1,"cmd":"send","args":["bob@localhost","hi"]}';
         echo '{"id":2,"cmd":"quit"}') | test-drunk-xmpp.py --json --config alice.conf
"""

import argparse
import asyncio
import contextvars
import json
import logging
import sys
import threading
import xml.etree.ElementTree as ET
import yaml
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Start time of this process (UTC, whole seconds), for /history --since-run
RUN_START = datetime.now(timezone.utc).replace(microsecond=0)

# Add parent directory to path to import drunk_xmpp module
sys.path.insert(0, str(SCRIPT_DIR.parent))

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
from drunk_xmpp import xep_0428


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


def apply_log_dir(config: dict, log_dir: str) -> None:
    """Point both log files to log_dir and turn them on (--log-dir)."""
    log_dir = Path(log_dir).resolve()
    logging_config = config.setdefault('logging', {}) or {}
    config['logging'] = logging_config
    logging_config['file'] = {'enabled': True, 'path': str(log_dir / 'drunk-xmpp.log')}
    logging_config['xml'] = {'enabled': True, 'path': str(log_dir / 'xmpp-protocol.log')}


def now_str(ts=None) -> str:
    """Local time 'YYYY-MM-DD HH:MM:SS.mmm', the same clock as the logs."""
    dt = datetime.fromtimestamp(ts) if ts is not None else datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


# The running request (JSON mode). Set in the task that runs the command;
# asyncio tasks started by the command inherit it, slixmpp callbacks do not.
REQUEST = contextvars.ContextVar('request', default=None)


class Driver:
    """Events, requests and JSON output (--json).

    Events are kept in both modes, so /wait works in human mode too.
    In JSON mode all stdout writes go through write(), one line each.
    """

    MAX_EVENTS = 1000

    def __init__(self, json_mode: bool, out, verbose: bool = False, event_lines: bool = True):
        self.json_mode = json_mode
        self.out = out  # the real stdout
        self.verbose = verbose
        self.event_lines = event_lines
        self.lock = threading.Lock()
        self.events = []  # events not yet taken by a wait
        self.changed = asyncio.Event()
        self.req = None  # the running request (JSON mode only)
        self.req_token = None

    def write(self, obj: dict) -> None:
        line = json.dumps(obj, default=str)
        with self.lock:
            self.out.write(line + '\n')
            self.out.flush()

    def emit(self, event: str, **fields) -> None:
        """Record an event; in JSON mode also write it to stdout."""
        rec = {'event': event, 't': now_str()}
        rec.update({k: v for k, v in fields.items() if v is not None})
        self.events.append(rec)
        if len(self.events) > self.MAX_EVENTS:
            del self.events[0]
        self.changed.set()
        self.changed = asyncio.Event()
        if self.json_mode and self.event_lines:
            self.write(rec)

    @staticmethod
    def matches(rec: dict, event, match: dict) -> bool:
        if event and rec.get('event') != event:
            return False
        for key, want in (match or {}).items():
            if key.endswith('~'):
                have = rec.get(key[:-1])
                if have is None or str(want) not in str(have):
                    return False
            else:
                have = rec.get(key)
                if have != want and not (isinstance(want, str) and str(have) == want):
                    return False
        return True

    async def wait_event(self, event, match: dict, timeout: float):
        """Take the first matching event (old or new), or None on timeout."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for i, rec in enumerate(self.events):
                if self.matches(rec, event, match):
                    del self.events[i]
                    return rec
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    def begin(self, request: dict) -> None:
        """Start a request; call it in the task that runs the command."""
        self.req = {'request': request, 't_start': now_str(), 'sent': [],
                    'logs': [], 'error': None, 'extra': {}}
        self.req_token = REQUEST.set(self.req)

    def finish(self) -> None:
        req, self.req = self.req, None
        REQUEST.reset(self.req_token)
        self.req_token = None
        sent = [s for s in (self.stanza_fields(t, data) for t, data in req['sent']) if s]
        msg_id = next((s['id'] for s in sent if s['kind'] == 'message'), None)
        reply = {'id': req['request'].get('id'), 'done': True,
                 'ok': req['error'] is None, 'error': req['error'],
                 't_start': req['t_start'], 't_end': now_str(),
                 'msg_id': msg_id, 'sent': sent}
        if self.verbose:
            reply['logs'] = req['logs']
        elif req['error'] is not None:
            reply['logs'] = [l for l in req['logs'] if l['levelno'] >= logging.WARNING]
        for line in reply.get('logs', []):
            line.pop('levelno', None)
        reply.update(req['extra'])
        self.write(reply)

    def on_send(self, data) -> None:
        """Record an outgoing stanza if the running request sent it."""
        req = REQUEST.get()
        if req is None or req is not self.req:
            return
        req['sent'].append((now_str(), data))

    @staticmethod
    def stanza_fields(t, data):
        """Fields of a sent stanza for "sent" (None for other data).

        Read at the end of the request, so ids set by send filters count.
        """
        el = getattr(data, 'xml', None)
        if el is None:
            if isinstance(data, bytes):
                data = data.decode('utf-8', 'replace')
            try:
                el = ET.fromstring(data)
            except (ET.ParseError, TypeError):
                return None  # stream header, footer, whitespace
        kind = el.tag.rsplit('}', 1)[-1]
        if kind not in ('message', 'presence', 'iq'):
            return None
        origin = el.find('{urn:xmpp:sid:0}origin-id')
        return {
            't': t, 'kind': kind, 'type': el.get('type'), 'id': el.get('id'),
            'origin_id': origin.get('id') if origin is not None else None,
            'to': el.get('to'),
        }


class RequestLogHandler(logging.Handler):
    """Copy INFO and higher log records of the running request into it.

    Only records logged in the request's context count (see REQUEST).
    """

    def __init__(self, driver: Driver):
        super().__init__(logging.INFO)
        self.driver = driver

    def emit(self, record):
        req = REQUEST.get()
        if req is None or req is not self.driver.req:
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if record.levelno >= logging.ERROR and req['error'] is None:
            req['error'] = msg
        if not msg.strip('= '):
            return  # empty lines and ==== lines
        req['logs'].append({'t': now_str(record.created), 'level': record.levelname,
                            'logger': record.name, 'msg': msg,
                            'levelno': record.levelno})


REPLY_NS = 'urn:xmpp:reply:0'


def body_clean(body, metadata):
    """Body without the XEP-0461 reply quote, or None if nothing was removed."""
    if not body or not metadata.fallbacks:
        return None
    try:
        markers = [xep_0428.FallbackMarker.from_dict(f) for f in metadata.fallbacks]
        clean = xep_0428.strip_fallbacks(body, markers, REPLY_NS)
    except Exception:
        return None
    return clean if clean != body else None


def meta_fields(metadata) -> dict:
    """Event fields from a MessageMetadata."""
    return {
        'from': metadata.from_jid,
        'to': metadata.to_jid,
        'type': metadata.message_type,
        'id': metadata.message_id,
        'stanza_id': metadata.stanza_id,
        'origin_id': metadata.origin_id,
        'encrypted': metadata.is_encrypted,
        'decrypt_failed': metadata.decrypt_failed,
        'replace_id': metadata.replaces_id,
        'reply_to_id': metadata.reply_to_id,
        'attachment_url': metadata.attachment_url,
        'delay': metadata.delay_timestamp.isoformat() if metadata.delay_timestamp else None,
    }


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
    print("  /room-config <room_jid>      - Query MUC room configuration (owner config form)")
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

    print("PEP/PUBSUB (XEP-0060/0163):")
    print("  /pep-nodes                   - List all PEP nodes on server")
    print("  /pep-get <node> [jid]        - Get items from a PEP node (own, or of another JID)")
    print("  /pep-config <node>           - Show the config of an own PEP node")
    print("  /pep-create <node>           - Create an own PEP node with the server default config")
    print("  /pep-delete <node>           - Delete a PEP node (WARNING: permanent!)")
    print("  /pep-subscriptions           - List all PEP subscriptions")
    print("  /pep-unsubscribe <jid> <node> - Unsubscribe from a PEP node")
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
    print("  /history <jid> [max] [--start <ISO time>|--since-run] [--no-skip] - Retrieve MAM history (default: 50 messages)")
    print("                                 --start: only messages from this time on (no zone = local time)")
    print("                                 --since-run: only messages since this process started")
    print("  /avatar <jid>                - Fetch avatar for JID (XEP-0084/0153)")
    print()

    print("ADVANCED/DEBUG:")
    print("  /typing <jid>                - Send 'composing' chat state (typing)")
    print("  /active <jid>                - Send 'active' chat state (stopped typing)")
    print("  /receipt <jid> <msg_id>      - Send delivery receipt")
    print("  /marker <jid> <msg_id> <type> - Send chat marker (received/displayed/acknowledged)")
    print("  /carbons                     - Show carbon copy status (XEP-0280)")
    print()

    print("SCRIPTING:")
    print("  /wait <event> [key=value] [key~=text] [timeout=N] - Wait for an event (default 10 s)")
    print("  /sleep <seconds>             - Wait some seconds")
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
        "/history <jid> [max] [--start <ISO time>|--since-run] [--no-skip] - Retrieve MAM history (default: 50 messages)",
        "/join <room> <nick> [pass]   - Join MUC room",
        "/keepalive?                  - Test auto-reconnect (disconnect but keep auto-reconnect)",
        "/leave <room>                - Leave MUC room",
        "/mam-check <jid>             - Check if JID supports MAM",
        "/marker <jid> <msg_id> <type> - Send chat marker (received/displayed/acknowledged)",
        "/pep-config <node>           - Show the config of an own PEP node",
        "/pep-create <node>           - Create an own PEP node with the server default config",
        "/pep-delete <node>           - Delete a PEP node (WARNING: permanent!)",
        "/pep-get <node> [jid]        - Get items from a PEP node (own, or of another JID)",
        "/pep-nodes                   - List all PEP nodes on server",
        "/pep-subscriptions           - List all PEP subscriptions",
        "/pep-unsubscribe <jid> <node> - Unsubscribe from a PEP node",
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
        "/room-config <room_jid>      - Query MUC room configuration (owner config form)",
        "/send <jid> <message>        - Send plaintext message",
        "/sendenc <jid> <message>     - Send OMEMO-encrypted message",
        "/sendmuc <room> <message>    - Send plaintext to MUC room",
        "/sendmucenc <room> <message> - Send OMEMO-encrypted to MUC room",
        "/server-features             - Query server features/XEPs (XEP-0030)",
        "/server-version              - Query server software version (XEP-0092)",
        "/sleep <seconds>             - Wait some seconds",
        "/subscribe <jid>             - Request presence subscription (RFC 6121)",
        "/typing <jid>                - Send 'composing' chat state (typing)",
        "/unblock <jid>               - Unblock contact (XEP-0191)",
        "/unreact <jid> <1|2>         - Remove reactions from message",
        "/unsubscribe <jid>           - Cancel our subscription (RFC 6121)",
        "/wait <event> [key=value] [key~=text] [timeout=N] - Wait for an event (default 10 s)",
    ]

    for cmd in commands:
        print(f"  {cmd}")

    print()
    print("=" * 60)
    print()


async def main(args):
    """Main test function - connect and wait for OMEMO init."""

    # JSON mode: stdout is for JSON lines only, all other text goes to stderr
    json_out = sys.stdout
    if args.json:
        sys.stdout = sys.stderr
    driver = Driver(args.json, json_out, verbose=args.verbose,
                    event_lines=(args.events == 'all'))

    # JSON mode: read stdin from the start, so early commands wait in the queue
    request_queue = asyncio.Queue()
    if args.json:
        loop = asyncio.get_running_loop()

        def read_stdin():
            while True:
                line = sys.stdin.readline()
                loop.call_soon_threadsafe(request_queue.put_nowait, line or None)
                if not line:
                    break

        threading.Thread(target=read_stdin, daemon=True).start()

    # Load config
    config_path = args.config
    if config_path is None:
        config_path = 'test-drunk-xmpp.conf'
        if not Path(config_path).exists():
            config_path = str(SCRIPT_DIR / 'test-drunk-xmpp.conf')
    config = load_config(config_path)
    if args.log_dir:
        apply_log_dir(config, args.log_dir)
    setup_logging(config)
    logging.getLogger().addHandler(RequestLogHandler(driver))

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

        driver.emit('message', **meta_fields(metadata), room=room, nick=nick, body=body,
                    body_clean=body_clean(body, metadata), occupant_id=metadata.occupant_id,
                    source='history' if metadata.is_history else 'live')
        if driver.json_mode:
            return

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
        # The raw "message" handler sees only the carbon wrapper ids.
        # The app stores carbons under the inner ids, so add them too.
        if metadata.is_carbon:
            for value in (metadata.stanza_id, metadata.origin_id, metadata.message_id):
                if value:
                    seen_ids.add(value)

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

        driver.emit('message', **meta_fields(metadata), jid=from_jid, body=body,
                    body_clean=body_clean(body, metadata), carbon_type=metadata.carbon_type,
                    source='carbon' if metadata.is_carbon else 'live')
        if driver.json_mode:
            return

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
        driver.emit('receipt', **{'from': from_jid, 'id': message_id})
        if driver.json_mode:
            return
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
        driver.emit('marker', **{'from': from_jid, 'id': message_id, 'marker': marker_type})
        if driver.json_mode:
            return
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
        driver.emit('server_ack', id=ack_info.msg_id)
        if driver.json_mode:
            return
        print()
        print("=" * 60)
        print(f"✓ SERVER ACK RECEIVED!")
        print(f"Message ID: {ack_info.msg_id}")
        print(f"Server confirmed message reached the server")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    # Presence changed callback (RFC 6121)
    async def on_presence_changed(from_jid, show):
        """Handler for contact presence changes (the event comes from on_presence)."""
        if driver.json_mode:
            return
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
        driver.emit('bookmarks', bookmarks=[
            {'jid': bm.get('jid'), 'name': bm.get('name'), 'nick': bm.get('nick'),
             'autojoin': bm.get('autojoin')} for bm in (bookmarks or [])])
        if driver.json_mode:
            return
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
        driver.emit('muc_invite', room=room_jid, reason=reason or None,
                    **{'from': inviter_jid})
        if driver.json_mode:
            return
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
        driver.emit('reaction', **{'from': metadata.from_jid}, type=metadata.message_type,
                    nick=metadata.muc_nick, occupant_id=metadata.occupant_id,
                    id=message_id, emojis=list(emojis or []))
        if driver.json_mode:
            return

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

    # Callbacks with no print in human mode: they only record events
    def on_chat_state(from_jid, state):
        driver.emit('chat_state', **{'from': str(from_jid), 'state': state})

    async def on_message_error(from_jid, to_jid, error_type, error_condition, error_text, origin_id):
        driver.emit('message_error', **{'from': str(from_jid), 'to': str(to_jid)},
                    error_type=error_type, condition=error_condition, text=error_text,
                    origin_id=origin_id)

    async def on_message_correction(jid, replace_id, body, is_encrypted, msg):
        driver.emit('message', jid=str(jid), **{'from': str(msg['from']), 'to': str(msg['to'])},
                    type=msg['type'], id=msg['id'] or None,
                    stanza_id=msg['stanza_id']['id'] or None,
                    origin_id=msg['origin_id']['id'] or None, replace_id=replace_id,
                    body=body, encrypted=is_encrypted, source='correction')

    async def on_muc_joined(room_jid, nick):
        driver.emit('muc_joined', room=str(room_jid), nick=nick)

    async def on_muc_join_error(room_jid, condition, text):
        driver.emit('muc_join_error', room=str(room_jid), condition=condition, text=text)

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
        on_chat_state_callback=on_chat_state,
        on_message_error_callback=on_message_error,
        on_message_correction_callback=on_message_correction,
        on_muc_joined_callback=on_muc_joined,
        on_muc_join_error_callback=on_muc_join_error,
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

    # Ids of messages seen live in this process. /history passes them as
    # is_stored (like the app), so OMEMO messages that were already decrypted
    # live are not decrypted again from MAM (that fails).
    seen_ids = set()

    def remember_ids(msg):
        for value in (msg['stanza_id']['id'], msg['origin_id']['id'], msg['id']):
            if value:
                seen_ids.add(value)

    def is_stored(archive_id, origin_id, message_id):
        return any(x in seen_ids for x in (archive_id, origin_id, message_id) if x)

    client.add_event_handler("message", remember_ids)

    # NOTE: Carbon copy event handlers NO LONGER REGISTERED (as of 2025-12-16)
    # DrunkXMPP now handles carbons internally and calls on_private_message_callback
    # with metadata.is_carbon=True
    # client.add_event_handler("carbon_received", on_carbon_received)  # REMOVED
    # client.add_event_handler("carbon_sent", on_carbon_sent)  # REMOVED

    # Subscription event handlers (roster management)
    def on_presence_subscribe(presence):
        """Handler for incoming subscription requests."""
        from_jid = presence['from'].bare
        driver.emit('subscription', kind='subscribe', **{'from': from_jid})
        if not driver.json_mode:
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
        driver.emit('subscription', kind='subscribed', **{'from': from_jid})
        if driver.json_mode:
            return
        print()
        print("=" * 60)
        print(f"[SUBSCRIPTION APPROVED] {from_jid} accepted your request")
        print("=" * 60)
        print("drunk-xmpp> ", end="", flush=True)

    def on_presence_unsubscribe(presence):
        """Handler for unsubscription requests."""
        from_jid = presence['from'].bare
        driver.emit('subscription', kind='unsubscribe', **{'from': from_jid})
        if not driver.json_mode:
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
        driver.emit('subscription', kind='unsubscribed', **{'from': from_jid})
        if driver.json_mode:
            return
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
        if roster.has_jid(from_jid):
            subscription = roster[from_jid]['subscription']
        driver.emit('roster', jid=from_jid, subscription=subscription)
        if driver.json_mode:
            return
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

    # Presence events: one per change of (show, status, type) for a full JID
    last_presence = {}

    def on_presence(presence):
        ptype = presence['type']  # the show value for away, xa, dnd, chat
        if ptype in ('subscribe', 'subscribed', 'unsubscribe', 'unsubscribed',
                     'probe', 'error'):
            return
        if ptype == 'unavailable':
            show = 'unavailable'
        else:
            show = presence['show'] or 'available'
            if show == 'chat':
                show = 'available'
        full_jid = str(presence['from'])
        state = (show, presence['status'] or None, ptype)
        if last_presence.get(full_jid) == state:
            return
        last_presence[full_jid] = state
        driver.emit('presence', **{'from': full_jid}, jid=presence['from'].bare,
                    show=show, status=state[1], type=ptype)

    client.add_event_handler("presence", on_presence)

    # Connection state events
    conn_state = {'session': False, 'failed': False}

    def on_session(_e, resumed=None):
        conn_state['session'] = True
        driver.emit('connected', jid=client.boundjid.full, resumed=resumed)

    def on_auth_failed(_e):
        conn_state['failed'] = True

    client.add_event_handler("session_start", on_session)
    client.add_event_handler("session_resumed", lambda e: on_session(e, resumed=True))
    client.add_event_handler("failed_auth", on_auth_failed)
    client.add_event_handler("failed_all_auth", on_auth_failed)
    def on_disconnected(_e):
        # The server sends all presences again after a reconnect
        last_presence.clear()
        driver.emit('disconnected')

    client.add_event_handler("disconnected", on_disconnected)

    async def wait_connected(timeout: float = 20.0) -> bool:
        """Wait for a session start after connect(), up to timeout seconds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if conn_state['session'] and client.is_connected():
                return True
            if conn_state['failed']:
                return False
            await asyncio.sleep(0.05)
        return False

    # Record outgoing stanzas of the running request ("sent" in the reply).
    # send() runs in the caller's context, so REQUEST tells who sent it.
    orig_send = client.send

    def send_hook(data, use_filters=True):
        orig_send(data, use_filters)
        driver.on_send(data)

    client.send = send_hook

    # Optional CA file for a server with its own cert (e.g. the local test Prosody)
    if xmpp_config.get('ca_certs'):
        ca_certs = Path(xmpp_config['ca_certs'])
        if not ca_certs.is_absolute():
            ca_certs = config['_config_dir'] / ca_certs
        client.ca_certs = ca_certs

    # Connect
    server = xmpp_config.get('server')
    port = xmpp_config.get('port', 5222)

    logger.info(f"Connecting to {server}:{port} as {xmpp_config['jid']}...")

    conn_state.update(session=False, failed=False)
    if server:
        client.connect((server, port))
    else:
        client.connect()

    # Wait for connection
    logger.info("Waiting for connection...")
    if not await wait_connected():
        logger.error("Failed to connect!")
        driver.emit('connect_failed')
        return 1

    logger.info(" Connected to XMPP server!")
    logger.info(f"  JID: {client.boundjid.bare}")
    logger.info(f"  OMEMO enabled: {client.omemo_enabled}")

    # Wait for OMEMO to initialize
    if client.omemo_enabled:
        logger.info("Waiting for OMEMO to initialize...")
        for i in range(300):
            if client.is_omemo_ready():
                logger.info(" OMEMO ready!")
                break
            if i % 10 == 9:
                logger.info(f"  Still waiting... ({(i + 1) // 10}/30)")
            await asyncio.sleep(0.1)
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

    driver.emit('ready', jid=client.boundjid.bare, connected=client.is_connected(),
                omemo_enabled=client.omemo_enabled, omemo_ready=client.is_omemo_ready())

    # Show help on startup
    if not driver.json_mode:
        print_help_grouped()

    async def next_command():
        """Next command line and its JSON request (None in human mode).

        Returns (None, None) at the end of input.
        """
        if not driver.json_mode:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, input, "drunk-xmpp> "
                )
            except EOFError:
                return None, None
            return line, None
        while True:
            line = await request_queue.get()
            if line is None:
                return None, None
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request is not a JSON object")
            except ValueError as e:
                driver.write({'id': None, 'done': True, 'ok': False,
                              'error': f"Bad request: {e}", 't_start': now_str(),
                              't_end': now_str(), 'msg_id': None, 'sent': []})
                continue
            cmd = str(request.get('cmd') or '').strip().lstrip('/')
            cmd_args = request.get('args') or []
            if not isinstance(cmd_args, list):
                cmd_args = [cmd_args]
            return ('/' + cmd + ' ' + ' '.join(str(a) for a in cmd_args)).strip(), request

    async def ask_confirmation():
        """Read the DELETE confirmation (JSON mode: the "confirm" field)."""
        if driver.json_mode:
            return str(driver.req['request'].get('confirm') or '')
        return await asyncio.get_event_loop().run_in_executor(
            None, input, "Confirmation: "
        )

    async def drain_send_queue():
        """Wait until queued stanzas went out, so the reply lists them."""
        queue = getattr(client, 'waiting_queue', None)
        if queue is None:
            return
        try:
            await asyncio.wait_for(queue.join(), 2.0)
        except asyncio.TimeoutError:
            pass

    disconnect_future = None

    # Interactive loop
    while True:
        request = None
        try:
            # Read command from stdin
            command, request = await next_command()
            if request is not None:
                driver.begin(request)

            if command is None:
                logger.info("End of input, disconnecting...")
                disconnect_future = client.disconnect(disable_auto_reconnect=True)
                break

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

                conn_state.update(session=False, failed=False)
                # Connect in an empty context: the connection tasks must not
                # keep this request, or incoming traffic counts for its reply
                if server:
                    contextvars.Context().run(client.connect, (server, port))
                else:
                    contextvars.Context().run(client.connect)

                # Wait for connection
                if await wait_connected():
                    logger.info(f"✓ Reconnected as {client.boundjid.bare}")
                else:
                    logger.error("Failed to reconnect!")

            elif command == "/keepalive?":
                logger.info("Testing auto-reconnect: disconnecting but keeping auto-reconnect enabled...")
                logger.info("Auto-reconnect should start after a short backoff delay")
                client.disconnect(disable_auto_reconnect=False)
                logger.info("✓ Disconnected. Watch for automatic reconnection...")

            elif command == "/quit":
                logger.info("Disconnecting...")
                disconnect_future = client.disconnect(disable_auto_reconnect=True)
                break

            elif command == "/sleep" or command.startswith("/sleep "):
                parts = command.split()
                try:
                    seconds = float(parts[1]) if len(parts) > 1 else 1.0
                except ValueError:
                    logger.error("Usage: /sleep <seconds>")
                    continue
                await asyncio.sleep(seconds)

            elif command == "/wait" or command.startswith("/wait "):
                # Text form: /wait <event> [key=value] [key~=text] [timeout=N]
                parts = command.split()[1:]
                event_name = parts[0] if parts else None
                match = {}
                timeout = 10.0
                try:
                    for part in parts[1:]:
                        if part.startswith("timeout="):
                            timeout = float(part.split("=", 1)[1])
                        elif "~=" in part:
                            key, value = part.split("~=", 1)
                            match[key + "~"] = value
                        elif "=" in part:
                            key, value = part.split("=", 1)
                            match[key] = value
                    # JSON form: "event", "match" and "timeout" fields
                    if request is not None:
                        event_name = request.get('event', event_name)
                        match = request.get('match', match) or {}
                        timeout = float(request.get('timeout', timeout))
                except (ValueError, TypeError):
                    logger.error("Usage: /wait <event> [key=value] [key~=text] [timeout=N]")
                    continue

                event = await driver.wait_event(event_name, match, timeout)
                if event is None:
                    logger.error(f"Timeout: no '{event_name}' event matching {match} in {timeout} s")
                    continue
                if request is not None:
                    driver.req['extra']['event'] = event
                else:
                    print(json.dumps(event, default=str, ensure_ascii=False))

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

            elif command.startswith("/room-config "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /room-config <room_jid>")
                    continue

                _, room_jid = parts
                logger.info(f"Querying room configuration for {room_jid}...")
                logger.info("(Note: Requires room owner permissions)")
                try:
                    config = await client.get_room_config(room_jid)

                    if config and config.get('error'):
                        logger.error(f"✗ Failed to query room config: {config['error']}")
                        logger.info("")
                        if config['error'] == 'Permission denied (owner-only)':
                            logger.info("  You must be a room owner to view configuration.")
                            logger.info("  Try /room-features instead for disco#info (available to all users)")
                    else:
                        logger.info(f"✓ Room configuration for {room_jid}:")
                        logger.info("")
                        logger.info("  Basic Info:")
                        logger.info(f"    Room name: {config['roomname'] or '(not set)'}")
                        logger.info(f"    Description: {config['roomdesc'] or '(not set)'}")
                        logger.info("")
                        logger.info("  Access Control:")
                        logger.info(f"    Persistent: {config['persistent']} (room persists when empty)")
                        logger.info(f"    Public: {config['public']} (searchable)")
                        logger.info(f"    Members-only: {config['membersonly']} (only members can join)")
                        logger.info(f"    Password protected: {config['password_protected']}")
                        logger.info(f"    Max users: {config['max_users'] or 'unlimited'}")
                        logger.info(f"    Who can see JIDs: {config['whois']} (anyone or moderators)")
                        logger.info("")
                        logger.info("  Moderation:")
                        logger.info(f"    Moderated: {config['moderated']} (only participants with voice can send)")
                        logger.info(f"    Allow subject change: {config['allow_subject_change']}")
                        logger.info("")
                        logger.info("  Features:")
                        logger.info(f"    Allow invites: {config['allow_invites']}")
                        logger.info(f"    Enable logging: {config['enable_logging']}")
                        logger.info("")
                        # OMEMO compatibility check
                        omemo_ok = config['membersonly'] and (config['whois'] == 'anyone')
                        if omemo_ok:
                            logger.info("  ✓ Room configuration SUPPORTS OMEMO encryption")
                        else:
                            logger.info("  ✗ Room configuration does NOT support OMEMO encryption")
                            if not config['membersonly']:
                                logger.info("    - Should be members-only (XEP-0384)")
                            if config['whois'] != 'anyone':
                                logger.info("    - Should allow anyone to see JIDs (whois=anyone)")
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
                # /history <jid> [max] [--start <ISO time> | --since-run] [--no-skip]
                parts = command.split()
                usage = "Usage: /history <jid> [max_messages] [--start <ISO time> | --since-run] [--no-skip]"
                if len(parts) < 2:
                    logger.error(usage)
                    continue

                jid = parts[1]
                max_messages = 50  # default
                start = None
                rest = parts[2:]
                # --no-skip: do not skip messages seen in this process
                skip = "--no-skip" not in rest
                rest = [x for x in rest if x != "--no-skip"]
                try:
                    if rest and not rest[0].startswith("--"):
                        max_messages = int(rest.pop(0))
                    if rest == ["--since-run"]:
                        start = RUN_START
                    elif len(rest) == 2 and rest[0] == "--start":
                        start = datetime.fromisoformat(rest[1])
                        if start.tzinfo is None:
                            start = start.astimezone()  # local time
                        start = start.astimezone(timezone.utc)
                    elif rest:
                        raise ValueError(f"bad arguments {' '.join(rest)!r}")
                except ValueError as e:
                    logger.error(f"{usage} ({e})")
                    continue

                since = f", start: {start.isoformat()}" if start else ""
                logger.info(f"Retrieving MAM history from {jid} (max: {max_messages}{since})...")
                try:
                    # For 1-1 chats, pass with_jid to filter to this specific contact
                    # For MUC rooms, the jid parameter is sufficient (room archive)
                    # retrieve_history yields pages; skip receipt/marker entries
                    history = []
                    async for page in client.retrieve_history(jid, start=start, max_messages=max_messages,
                                                              with_jid=jid,
                                                              is_stored=is_stored if skip else None):
                        history.extend(m for m in page if not m.get('marker_type'))
                    logger.info(f"✓ Retrieved {len(history)} messages:")
                    logger.info("")
                    # Group chat events get "room", as live group chat events
                    room = jid if jid in client.rooms else None
                    for i, msg in enumerate(history, 1):
                        stanza = msg.get('message')
                        driver.emit('message', jid=msg.get('jid'), room=room, nick=msg.get('nick'),
                                    **({'from': str(stanza['from']), 'to': str(stanza['to']),
                                        'type': stanza['type'], 'id': stanza['id'] or None,
                                        'origin_id': stanza['origin_id']['id'] or None}
                                       if stanza is not None else {}),
                                    body=msg.get('body'), encrypted=msg.get('is_encrypted'),
                                    archive_id=msg.get('archive_id'),
                                    delay=msg['timestamp'].isoformat() if msg.get('timestamp') else None,
                                    source='mam')
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

            elif command == "/pep-nodes":
                logger.info("Listing all PEP nodes on server...")
                try:
                    xep_0060 = client.plugin['xep_0060']
                    own_jid = client.boundjid.bare

                    logger.info(f"Querying disco#items for {own_jid}...")
                    result = await xep_0060.get_nodes(own_jid)

                    if result and 'disco_items' in result and 'items' in result['disco_items']:
                        items = result['disco_items']['items']
                        logger.info(f"✓ Found {len(items)} PEP nodes:")
                        logger.info("")

                        omemo_nodes = []
                        other_nodes = []

                        for item in items:
                            node_name = item[1]  # (jid, node, name) tuple
                            if node_name:
                                if 'omemo' in node_name.lower() or 'axolotl' in node_name.lower():
                                    omemo_nodes.append(node_name)
                                else:
                                    other_nodes.append(node_name)

                        if omemo_nodes:
                            logger.info("OMEMO-related nodes:")
                            for node in omemo_nodes:
                                logger.info(f"  - {node}")
                            logger.info("")

                        if other_nodes:
                            logger.info(f"Other nodes ({len(other_nodes)}):")
                            for node in other_nodes[:10]:
                                logger.info(f"  - {node}")
                            if len(other_nodes) > 10:
                                logger.info(f"  ... and {len(other_nodes) - 10} more")
                    else:
                        logger.info("No PEP nodes found")

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/pep-get "):
                parts = command.split(None, 2)
                if len(parts) < 2:
                    logger.error("Usage: /pep-get <node> [jid]")
                    continue

                node = parts[1]
                logger.info(f"Getting items from PEP node: {node}")
                try:
                    xep_0060 = client.plugin['xep_0060']
                    # Another JID: tests what a contact can read
                    pep_jid = parts[2] if len(parts) > 2 else client.boundjid.bare

                    result = await xep_0060.get_items(pep_jid, node)

                    if result and 'pubsub' in result and 'items' in result['pubsub']:
                        items = result['pubsub']['items']
                        item_list = list(items)

                        logger.info(f"✓ Found {len(item_list)} items in node {node}:")
                        logger.info("")

                        for idx, item in enumerate(item_list, 1):
                            item_id = item.get('id', 'no-id')
                            logger.info(f"  Item {idx}: {item_id}")

                            # Show XML content
                            from xml.etree import ElementTree as ET
                            xml_str = ET.tostring(item.xml, encoding='unicode')
                            logger.info(f"    XML: {xml_str}")
                            logger.info("")
                    else:
                        logger.info(f"No items found in node {node}")

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/pep-config "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /pep-config <node>")
                    continue

                node = parts[1]
                logger.info(f"Getting config of PEP node: {node}")
                try:
                    xep_0060 = client.plugin['xep_0060']
                    result = await xep_0060.get_node_config(client.boundjid.bare, node)
                    form = result['pubsub_owner']['configure']['form']
                    for var, field in form.get_fields().items():
                        if var != 'FORM_TYPE':
                            logger.info(f"  {var} = {field['value']}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/pep-create "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /pep-create <node>")
                    continue

                node = parts[1]
                logger.info(f"Creating PEP node with the server default config: {node}")
                try:
                    xep_0060 = client.plugin['xep_0060']
                    await xep_0060.create_node(client.boundjid.bare, node)
                    logger.info(f"✓ PEP node created: {node}")
                except Exception as e:
                    logger.error(f"Failed: {e}")

            elif command.startswith("/pep-delete "):
                parts = command.split(None, 1)
                if len(parts) < 2:
                    logger.error("Usage: /pep-delete <node>")
                    continue

                node = parts[1]

                # Confirmation prompt
                logger.warning("=" * 60)
                logger.warning("⚠ WARNING: PERMANENT NODE DELETION ⚠")
                logger.warning("=" * 60)
                logger.warning(f"You are about to DELETE the PEP node:")
                logger.warning(f"  {node}")
                logger.warning("")
                logger.warning("This will remove all data in this node from the server.")
                logger.warning("Type 'DELETE' to confirm, or anything else to cancel:")
                logger.warning("=" * 60)

                confirmation = await ask_confirmation()

                if confirmation.strip() != "DELETE":
                    # JSON mode: ERROR gives ok=false, so a missing "confirm" is not read as success
                    (logger.error if driver.json_mode else logger.info)("Node deletion cancelled.")
                    continue

                logger.info(f"Deleting PEP node: {node}")
                try:
                    xep_0060 = client.plugin['xep_0060']
                    own_jid = client.boundjid.bare

                    result = await xep_0060.delete_node(own_jid, node)

                    logger.info("=" * 60)
                    logger.info(f"✓ PEP node deleted successfully: {node}")
                    logger.info("=" * 60)

                except Exception as e:
                    logger.error(f"Failed to delete node: {e}")
                    import traceback
                    traceback.print_exc()

            elif command == "/pep-subscriptions":
                logger.info("Listing PEP subscriptions...")
                try:
                    xep_0060 = client.plugin['xep_0060']

                    # Query subscriptions from own JID (PEP)
                    own_jid = client.boundjid.bare
                    logger.info(f"Querying subscriptions for {own_jid}...")
                    result = await xep_0060.get_subscriptions(own_jid)

                    if result and 'pubsub' in result and 'subscriptions' in result['pubsub']:
                        subs = result['pubsub']['subscriptions']
                        logger.info(f"✓ Found {len(subs)} PEP subscriptions:")
                        for sub in subs:
                            node = sub.get('node', 'N/A')
                            jid = sub.get('jid', 'N/A')
                            subscription = sub.get('subscription', 'N/A')
                            logger.info(f"  - Node: {node}")
                            logger.info(f"    JID: {jid}, Status: {subscription}")
                    else:
                        logger.info("No PEP subscriptions found")

                    # Also check pubsub.conversations.im
                    pubsub_jid = "pubsub.conversations.im"
                    logger.info(f"\nQuerying subscriptions from {pubsub_jid}...")
                    try:
                        result = await xep_0060.get_subscriptions(pubsub_jid)
                        if result and 'pubsub' in result and 'subscriptions' in result['pubsub']:
                            subs = result['pubsub']['subscriptions']
                            logger.info(f"✓ Found {len(subs)} subscriptions on pubsub service:")
                            for sub in subs:
                                node = sub.get('node', 'N/A')
                                jid = sub.get('jid', 'N/A')
                                subscription = sub.get('subscription', 'N/A')
                                logger.info(f"  - Node: {node}")
                                logger.info(f"    JID: {jid}, Status: {subscription}")
                        else:
                            logger.info("No subscriptions on pubsub service")
                    except Exception as e:
                        logger.warning(f"Could not query pubsub service: {e}")

                except Exception as e:
                    logger.error(f"Failed: {e}")
                    import traceback
                    traceback.print_exc()

            elif command.startswith("/pep-unsubscribe "):
                parts = command.split(None, 2)
                if len(parts) < 3:
                    logger.error("Usage: /pep-unsubscribe <service_jid> <node>")
                    logger.error("Example: /pep-unsubscribe pubsub.conversations.im eu.siacs.conversations.axolotl.devicelist")
                    continue

                service_jid = parts[1]
                node = parts[2]

                logger.info(f"Unsubscribing from node '{node}' on {service_jid}...")
                logger.info("Trying multiple methods to force unsubscribe...")

                try:
                    xep_0060 = client.plugin['xep_0060']
                    own_jid = client.boundjid.bare

                    # Method 1: Standard unsubscribe
                    logger.info(f"Method 1: Standard unsubscribe")
                    try:
                        iq = client.make_iq_set(ito=service_jid)
                        iq['pubsub']['unsubscribe']['node'] = node
                        iq['pubsub']['unsubscribe']['jid'] = own_jid
                        result = await iq.send()
                        logger.info(f"✓ Method 1 succeeded")
                    except Exception as e:
                        logger.warning(f"Method 1 failed: {e}")

                        # Method 2: Try with explicit subscriber JID
                        logger.info(f"Method 2: Trying alternate format...")
                        try:
                            iq = client.make_iq_set(ito=service_jid)
                            unsub = iq['pubsub']['unsubscribe']
                            unsub['node'] = node
                            unsub['jid'] = own_jid
                            # Force ifrom to be own JID
                            iq['from'] = own_jid
                            result = await iq.send()
                            logger.info(f"✓ Method 2 succeeded")
                        except Exception as e2:
                            logger.warning(f"Method 2 failed: {e2}")

                            # Method 3: Try querying and deleting by subid
                            logger.info(f"Method 3: Finding subscription ID...")
                            try:
                                subs_result = await xep_0060.get_subscriptions(service_jid)
                                if subs_result and 'pubsub' in subs_result:
                                    subs = subs_result['pubsub']['subscriptions']
                                    for sub in subs:
                                        if sub.get('node') == node:
                                            subid = sub.get('subid')
                                            if subid:
                                                logger.info(f"Found subid: {subid}, trying to unsubscribe with it...")
                                                iq = client.make_iq_set(ito=service_jid)
                                                unsub = iq['pubsub']['unsubscribe']
                                                unsub['node'] = node
                                                unsub['jid'] = own_jid
                                                unsub['subid'] = subid
                                                result = await iq.send()
                                                logger.info(f"✓ Method 3 succeeded with subid")
                                                break
                                    else:
                                        raise Exception("No matching subscription found")
                            except Exception as e3:
                                logger.error(f"All methods failed. Last error: {e3}")
                                logger.error("These may be phantom subscriptions that require server admin intervention.")
                                raise

                    logger.info(f"✓ Successfully unsubscribed from {node} on {service_jid}")

                except Exception as e:
                    logger.error(f"Failed to unsubscribe: {e}")
                    import traceback
                    traceback.print_exc()

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

                confirmation = await ask_confirmation()

                if confirmation.strip() != "DELETE":
                    (logger.error if driver.json_mode else logger.info)("Account deletion cancelled.")
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
            client.disconnect(disable_auto_reconnect=True)
            break
        except Exception as e:
            logger.exception(f"Error: {e}")
        finally:
            if request is not None:
                await drain_send_queue()
                driver.finish()

    # Let the disconnect finish, so queued stanzas are not lost
    if disconnect_future is not None:
        try:
            await asyncio.wait_for(asyncio.shield(disconnect_future), 3.0)
        except Exception:
            pass

    logger.info("Goodbye!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Interactive test tool for drunk_xmpp.")
    parser.add_argument('--config', help="config file (default: test-drunk-xmpp.conf "
                        "in the current dir, else next to this script)")
    parser.add_argument('--json', action='store_true',
                        help="JSON lines on stdin and stdout (for agents and scripts)")
    parser.add_argument('--log-dir', help="write drunk-xmpp.log and xmpp-protocol.log to this dir")
    parser.add_argument('--verbose', action='store_true',
                        help="JSON mode: add INFO and higher logs to every reply")
    parser.add_argument('--events', choices=('all', 'none'), default='all',
                        help="JSON mode: write event lines (all) or not (none)")
    sys.exit(asyncio.run(main(parser.parse_args())))
