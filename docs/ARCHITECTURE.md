# Architecture

Siproxylin tries to follow a modular architecture that separates concerns between networking, business logic, and user interface.

## Design Principles

- **Privacy First**: Per-account proxy support, enforced call relaying, OMEMO encryption
- **Modularity**: Component-based "barrel" pattern for feature isolation
- **Single Responsibility**: Each component handles one concern
- **Event-Driven**: Async XMPP events drive Qt GUI updates via signals
- **Library-Based**: Leverage mature XMPP libraries (slixmpp, python-omemo)

## High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                  GUI Layer (Qt6)                    │
│  - Main Window, Dialogs, Widgets                    │
│  - Contact List, Chat View, Call Window             │
└─────────────────┬───────────────────────────────────┘
                  │ Qt Signals/Slots
                  ▼
┌─────────────────────────────────────────────────────┐
│              Core Layer (Business Logic)            │
│  - Account Management (Brewery)                     │
│  - Feature Barrels (Messages, Calls, OMEMO, etc.)   │
└─────────────────┬───────────────────────────────────┘
                  │ Callbacks
                  ▼
┌─────────────────────────────────────────────────────┐
│            XMPP Layer (DrunkXMPP)                   │
│  - Connection Management (slixmpp)                  │
│  - Protocol Implementation (XEPs)                   │
│  - OMEMO Encryption (python-omemo)                  │
└─────────────────┬───────────────────────────────────┘
                  │ Network I/O
                  ▼
┌─────────────────────────────────────────────────────┐
│              Media Layer (Go + GStreamer)           │
│  - Audio/Video Calls (Pion WebRTC)                  │
│  - Media Processing (GStreamer pipelines)           │
│  - gRPC bridge to Python                            │
└─────────────────────────────────────────────────────┘
```

## Directory Structure

```
|
├── siproxylin/
│   ├── core/               # Business logic layer
│   │   ├── brewery.py      # Account orchestration
│   │   └── barrels/        # Feature components
│   │       ├── connection.py
│   │       ├── messages.py
│   │       ├── calls.py
│   │       ├── omemo.py
│   │       ├── presence.py
│   │       ├── files.py
│   │       ├── avatars.py
│   │       └── muc.py
│   │
│   ├── gui/                # User interface layer
│   │   ├── main_window.py
│   │   ├── contact_list.py
│   │   ├── chat_view/
│   │   ├── dialogs/
│   │   ├── widgets/
│   │   └── models/
│   │
│   ├── db/                 # Data persistence
│   │   ├── database.py
│   │   ├── schema.sql
│   │   └── migrations/
│   │
│   └── utils/              # Shared utilities
│
├── drunk_xmpp/             # XMPP protocol library
│   ├── client.py           # Core client wrapper
│   ├── omemo_support.py    # OMEMO integration
│   ├── mam.py              # Message archive
│   ├── file_uploads.py     # XEP-0363 uploads
│   └── calls/              # XEP-0353 Jingle
│
├── drunk_call_hook/        # Python-Go bridge (gRPC)
├── drunk_call_service/     # Go media service (Pion)
└── main.py                 # Application entry point
```

## Core Components

### Account Brewery (Core Layer)

**File**: `app/core/brewery.py`

**Purpose**: Central account manager that orchestrates multiple XMPP accounts

**Key Classes**:
- `AccountBrewery`: Singleton managing all accounts
- `XMPPAccount`: Wrapper for single account with feature barrels

**Responsibilities**:
- Load/save accounts from database
- Create barrel instances per account
- Connect Qt signals from barrels to GUI
- Coordinate account lifecycle (connect/disconnect)

### Barrel Pattern (Feature Isolation)

Each "barrel" handles one feature domain:

| Barrel | Responsibility | Key XEPs |
|--------|---------------|----------|
| **ConnectionBarrel** | XMPP connection, reconnect, status | RFC 6120 |
| **MessageBarrel** | Send/receive messages, receipts, markers | XEP-0184, 0333 |
| **PresenceBarrel** | Roster, subscriptions, presence | RFC 6121 |
| **OmemoBarrel** | E2E encryption device management | XEP-0384 |
| **CallBarrel** | Audio/video calls via Go service | XEP-0353, 0166 |
| **FileBarrel** | File uploads and attachments | XEP-0363, 0454 |
| **AvatarBarrel** | Avatar fetching and caching | XEP-0084, 0153 |
| **MucBarrel** | Multi-user chat rooms | XEP-0045, 0402 |

**Benefits**:
- Each barrel is independently testable
- Clear separation of concerns
- Easy to add new features without touching existing code

### DrunkXMPP Library (XMPP Layer)

**Location**: `drunk_xmpp/`

**Purpose**: Standalone library wrapping slixmpp with event-driven callbacks

**Key Features**:
- Storage-agnostic: Accepts `Storage` interface or file path
- Async event callbacks for all XMPP events
- Comprehensive XEP coverage (20+ extensions)
- Fully decoupled from GUI layer

**Important**: All callbacks must be `async` functions

### Database Layer

**File**: `app/db/database.py`

**Pattern**: Singleton with single shared connection

**Critical Rule**: ALWAYS use `get_db()` - never create new SQLite connections

**Schema Management**:
- Base schema: `app/db/schema.sql`
- Migrations: `app/db/migrations/*.sql` (auto-run on startup)
- Version tracking in `db_version` table

### Call Architecture

Calls use a hybrid Python + Go architecture:

```
Python (Signaling)              Go (Media)
┌─────────────────┐            ┌──────────────────┐
│  CallBarrel     │            │  Pion WebRTC     │
│  (Jingle)       │ ◄─ gRPC ─► │  (RTP/ICE/DTLS)  │
│                 │            │                  │
│  JingleAdapter  │            │  GStreamer       │
│  (SDP ↔ Jingle) │            │  (audio I/O)     │
└─────────────────┘            └──────────────────┘
```

**Why Go?**:
- Pion WebRTC provides battle-tested RTP/ICE/DTLS stack
- GStreamer bindings more mature in Go
- Better performance for real-time media

**Why not all Go?**:
- Python ecosystem for XMPP is richer (slixmpp, omemo)
- Qt GUI bindings better in Python
- Separation of concerns: signaling vs media

## Data Flow Patterns

### Pattern 1: XMPP Event → GUI Update

```
XMPP Network
    ↓ (callback)
DrunkXMPP
    ↓ (async callback)
Barrel Handler (process + store in DB)
    ↓ (emit Qt signal)
MainWindow Signal Handler
    ↓ (update widgets)
GUI Refresh
```

### Pattern 2: User Action → XMPP Send

```
GUI Widget (button click)
    ↓ (emit signal)
MainWindow Handler
    ↓ (call async method)
Barrel Method (prepare data)
    ↓ (call library)
DrunkXMPP Send
    ↓ (network)
XMPP Server
```

## Message States

Messages follow Dino-compatible state model:

| State | Value | Meaning | Icon |
|-------|-------|---------|------|
| PENDING | 0 | Queued to send | ⌛ |
| SENT | 1 | Server acknowledged (XEP-0198) | ✓ |
| RECEIVED | 2 | Delivery receipt (XEP-0184) | ✓✓ |
| DISPLAYED | 7 | Read marker (XEP-0333) | ✔✔ |
| ERROR | 8 | Send failed | ⚠ |

**Rule**: Insert with `marked=0`, let receipt handlers update automatically

## Encryption

### DTLS-SRTP (Calls)
- Standard WebRTC encryption (always on)
- Protects audio streams in transit
- Pion handles automatically

### OMEMO (Messages)
- End-to-end encryption for messages and files
- Double Ratchet algorithm (Signal protocol)
- Device fingerprint verification
- Storage: `omemo_storage` table (key-value)

## Threading Model

- **Main Thread**: Qt event loop (GUI)
- **XMPP Thread**: asyncio event loop (networking)
- **Go Service**: Separate process (media)

**Bridge**: Qt signals are thread-safe and cross thread boundaries

## Configuration

### Path Modes

| Mode | Flag | Data Location |
|------|------|---------------|
| dev | (none) | `./app_dev_paths/` |
| xdg | `--xdg` | `~/.config/`, `~/.local/share/`, `~/.cache/` |
| dot | `--dot-data-dir` | `~/.siproxylin/` (AppImage default) |

### Per-Account Settings

- XMPP server and credentials
- HTTP/SOCKS5 proxy configuration
- Resource name (for multi-device)
- Auto-connect on startup

## Development Guidelines

### Adding a New Feature

1. Implement in DrunkXMPP if protocol-level
2. Create or extend a barrel in `app/core/barrels/`
3. Add Qt signal to `XMPPAccount` class
4. Connect signal in `MainWindow.setup_accounts()`
5. Update GUI widgets to handle signal
6. Add database migration if needed

### Testing

- **Unit Tests**: Test barrels and DrunkXMPP independently
- **Integration Tests**: Test full XMPP flow with test server
- **Manual Testing**: Use two accounts to test interoperability

### Code Quality Rules

1. **Use library APIs**: Avoid manual XML parsing (use slixmpp methods)
2. **Make use of drunk_xmpp/slixmpp_patches** in case some method is missing or contains a bug
3. **Use logger**: Never `print()` (see `app/utils/logger.py`)
4. **Use get_db()**: Never create new SQLite connections
5. **Async callbacks**: All DrunkXMPP callbacks must be `async`
6. **Let handlers update state**: Don't manually set message `marked` field

## External Dependencies

### Python Libraries
- **PySide6**: Qt6 GUI framework (LGPL-3.0)
- **slixmpp**: XMPP client library (MIT)
- **python-omemo**: OMEMO encryption (LGPL-3.0)
- **grpcio**: Python-Go bridge (Apache-2.0)

### System Libraries
- **GStreamer 1.0**: Audio processing (LGPL-2.1+)
- **Qt6**: GUI toolkit (LGPL-3.0)
- **hunspell**: Spell checking dictionaries (LGPL-2.1)

### Go Dependencies
- **Pion WebRTC**: Media stack (MIT)
- **gRPC-Go**: RPC framework (Apache-2.0)

All dependencies are AGPL-3.0 compatible.

## Performance Considerations

- **Database**: Single connection with `check_same_thread=False`
- **Avatar Cache**: LRU cache (128 entries, 24h expiry)
- **Message History**: Load last 300 messages, infinite scroll for older
- **Roster**: In-memory model, DB queries only on load
- **Typing Indicators**: 5-second debounce to reduce traffic

## Security Considerations

- **Proxy Isolation**: Per-account proxies prevent correlation
- **Call Relaying**: Enforced TURN prevents IP leaks
- **OMEMO**: E2E encryption for messages and files
- **File Permissions**: All data directories created with 0700 (user-only)
- **Database**: No sensitive data in plaintext (OMEMO keys encrypted by library)

## Future Architecture Evolution

### Under Consideration
- **State-Driven Views**: Move from polling to reactive state management
- **Multi-Window Support**: Separate conversation windows
- **Plugin System**: Third-party extensions
- **Backend Service**: Headless daemon with GUI client

### Technical Debt
See `docs/TECH-DEBT/` for tracked architectural improvements.

---

**Last Updated**: 2026-02-01
**Document Status**: Public Release Documentation
