# Architecture Decision Records (ADR)
# Primarily intended for AI assistant

> **Purpose**: The "10 Commandments" - Critical architectural patterns and rules
> **This is NOT**: A todo list, historical log, or feature tracker
> **For History**: Ask user where historical notes are stored

---

## Session Workflow

**Work style:** 1 task → user test → remind commit → update docs → move on. Require regression testing when appropriate.

---

## Critical Rules - MUST FOLLOW

### 1. DrunkXMPP is STABLE
- Changes require explicit user agreement
- Test in `tests/test-drunk-xmpp.py` FIRST
- User tests and approves before GUI integration

### 2. User Owns Git & Process Control
- User handles: git commits, start/stop apps, CLI commands
- **Don't do it for them** - remind, don't execute

### 3. Commit Quality
- Commit at logical checkpoints
- Separate commits for: features, bugfixes, refactoring, documentation
- **SPEAK UP when a commit should be made** (don't wait for user)

### 4. Quality > Speed
- "Time is not pushing, the quality is"
- Take time to get it right

### 5. Use Library Methods
- NO manual XML parsing like `msg.find('{urn:xmpp:sid:0}origin-id')`
- Use slixmpp's built-in methods
- In case slixmpp bug found create runtime patch in drunk_xmpp/slixmpp_patches/
- In case XEP is not implemented add to drunk_xmpp/xep_0XYZ/
- Jingle XEP-016* is located within drunk_call_hook/

### 6. No print()
- Always use logger instances
- See: `siproxylin/utils/logger.py`

### 7. Codec Parameter Negotiation (Calls)
- **DO**: Parse `a=fmtp:` from GStreamer webrtcbin's SDP answer → convert to Jingle `<parameter>` elements
- **DON'T**: Hardcode codec parameters
- **Why**: GStreamer webrtcbin handles codec negotiation - Python just translates SDP ↔ Jingle
- See: `drunk_call_hook/protocol/jingle.py`

### 8. MUC Message Direction (XEP-0421)
- **Primary**: Compare `metadata.occupant_id` with `self.client.own_occupant_ids[room_jid]`
- **Fallback**: Compare `metadata.muc_nick.lower()` with nickname
- Use occupant-id first, fall back to nickname for servers without XEP-0421

### 9. Message States - Let Handlers Update
- NEVER manually update `marked` field
- Handlers auto-update based on XEP-0198/0184/0333
- See Message States table below

### 10. Database - Single Connection
- **MUST USE**: `get_db()` singleton
- NEVER create new SQLite connections
- See: `app/db/database.py`

### 11. Call State Coordination
- **Python owns**: Signaling state machine (proposed, ringing, connecting, active, ended)
- **C++ reports**: Media/ICE states via gRPC (ice-connection-state, gathering-state)
- **Critical**: NEVER let layers get out of sync - always bidirectional cleanup
- **Termination**: All paths (user hangup, network failure, remote hangup) must synchronize both layers
- See: `docs/CALLS.md` for detailed state machine

---

## Quick Reference

| Task | Pattern | Notes |
|------|---------|-------|
| Database access | `get_db()` | Never create new connections! |
| DrunkXMPP storage | Pass `Storage` object | `omemo_storage=storage` |
| Message states | Let handlers update `marked` | Don't manually set it |
| Logging | `setup_account_logger()` or `setup_main_logger()` | Never `print()` |
| Callbacks | Must be `async` | No blocking in callbacks |
| Error handling | Specific exceptions, logger + traceback | Never bare `except:` |
| DB migrations | Create `.sql` in `siproxylin/db/migrations/` | Auto-runs on startup |
| Calls (media) | C++ service via gRPC | Python = signaling, C++ = media |

**Development Paths** (from project root):
- **Database**: `sip_dev_paths/data/siproxylin.db`
- **XMPP XML logs**: `sip_dev_paths/logs/xmpp-protocol.log`
- **Application logs**: `sip_dev_paths/logs/account-*-app.log` and `sip_dev_paths/logs/main.log`

---

## Documentation Philosophy

- **Less is more** - One concise up-to-date doc > scattered histories
- **No code samples** - Reference files only (code changes, docs don't)
- **No line numbers** - They go stale, use file paths instead
- **Current state only** - Document what IS, not what WAS (keep git for history)
- **File references** - "See `app/db/database.py` insert_message_atomic()" not code blocks

**Dev Docs Structure:**
- `/home/m/claude/siproxylin/docs/` - Current project documentation
- History tracked in git commits

---

## Message States (Dino-compatible)

**Field**: `message.marked` (INTEGER)

| Value | Meaning | Icon |
|-------|---------|------|
| 0 | PENDING | ⌛ |
| 1 | SENT (server ACK) | ✓ |
| 2 | RECEIVED (delivery receipt) | ✓✓ |
| 7 | READ/DISPLAYED | ✔✔ |
| 8 | ERROR (discarded) | ⚠ |

**Note**: Values 3-6 reserved for future use (Dino compatibility). Receipt handlers auto-update (XEP-0198/0184/0333). Insert with `marked=0`, let handlers update.

---

## Calls Architecture

### Encryption

**DTLS-SRTP** (Always On) - Standard WebRTC encryption for all calls, handled automatically by GStreamer webrtcbin.

### Components

```
main_window.py
    ├─ CallService (C++ process, app lifetime)
    └─ AccountManager (per-account)
           ├─ CallBridge (gRPC client)
           └─ JingleAdapter (Jingle ↔ SDP)
                   ↓
          DrunkXMPP calls/mixin.py (XEP-0353)
```

**Key Directories:**
- `drunk_xmpp/calls/` - XEP-0353 Jingle Message Initiation (STABLE)
- `drunk_call_hook/` - Python → C++ bridge (gRPC)
- `drunk_call_service/` - C++ service (GStreamer webrtcbin)

**Per-Account Isolation**: Separate CallBridge + JingleAdapter per account, no shared state.

**Security Requirements (Call Service)**:
- **Proxy enforcement**: MUST support SOCKS5/HTTP proxy for all network traffic (STUN/TURN)
- **Leak prevention**: MUST fail calls if proxy configured but unavailable (no fallback to direct)
- **Privacy modes**: relay-only ICE (no host/srflx candidates exposed) ✅ implemented
- See: `docs/CALLS.md` for architecture details

---

## DrunkXMPP Architecture

**Key Points**:
- Standalone library wrapping slixmpp
- Storage-agnostic: `Storage` interface OR file path
- Event-driven via async callbacks
- All callbacks must be `async` (no blocking!)

**Module Organization** (see `drunk_xmpp/`):
- `client.py` - Core connection, MUC
- `file_uploads.py` - XEP-0363 HTTP upload
- `mam.py` - XEP-0313 message archive
- `message_extensions.py` - XEP-0308/0444/0461 (edit, reactions, replies)
- `omemo_support.py`, `omemo_devices.py` - OMEMO encryption

**When Modifying**: ALWAYS test with `tests/test-drunk-xmpp.py` first, get user approval.

---

## Code Quality Checklist

Before implementing:
- [ ] Using library API? (not manual XML parsing)
- [ ] Using logger? (not print())
- [ ] Using get_db()? (not creating new connections)
- [ ] Callbacks are async? (no blocking)
- [ ] DrunkXMPP tested first? (if modifying it)
- [ ] Letting handlers manage message states? (not manually updating marked)

---

**Last Updated**: 2026-03-02
**Remember**: This is THE reference. When in doubt, check the commandments.
