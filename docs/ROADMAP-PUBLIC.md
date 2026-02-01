# Roadmap

This document outlines the vision and development direction for Siproxylin.

---

## Project Vision

**Privacy-focused XMPP desktop client** with per-account proxy support, enforced call relaying, and full OMEMO encryption.

**Core Values**:
- Privacy first (no telemetry, no tracking, local-first)
- Standards compliance (XMPP/XEP interoperability)
- User freedom (AGPL-3.0, commercial license available)
- Quality over speed

---

## Current Feature Set

### Messaging
- OMEMO end-to-end encryption
- Message editing, replies, reactions
- File transfers with encryption
- Message carbons (multi-device sync)
- Message archive retrieval (MAM)
- Typing indicators and read markers
- Spell checking

### Communication
- Audio calls with echo cancellation
- Video calls (in development)
- Multi-user chat (MUC) rooms
- Contact presence and subscriptions

### Privacy & Security
- Per-account proxy support (HTTP/SOCKS5)
- TURN relay enforcement (no IP leaks)
- OMEMO encryption for messages and files
- Proxy support for calls and registration

### User Experience
- Multiple theme support
- Chat search and infinite scroll
- Desktop notifications
- Call history and logs
- Multi-account management

---

## Development Direction

### Near Term

**Cross-Platform Support**
- Windows and macOS builds
- Platform-agnostic audio/video device handling
- Native packaging for each platform

**Video Calls**
- Video streaming integration
- Camera selection and preview
- Video toggle during calls

**Call Features**
- Screen sharing
- Call quality indicators
- Improved notification system

### Medium Term

**User Experience**
- Pluggable themes architecture
- Enhanced roster views
- Improved search capabilities

**XMPP Features**
- Additional XEP implementations
- Server feature discovery improvements
- Enhanced MUC functionality

### Long Term

**Advanced Features**
- Multi-party conference calls
- Message search across all conversations
- Advanced contact management
- Call recording (with consent)

**Platform Expansion**
- Additional packaging formats
- Distribution through app stores
- Automated update system

---

## Technical Architecture

### Current Stack
- **Python 3.11+** - Application framework and GUI (PySide6/Qt6)
- **Go** - Media processing service (Pion WebRTC, GStreamer)
- **SQLite** - Local data storage
- **slixmpp** - XMPP protocol implementation
- **python-omemo** - OMEMO encryption

### Architectural Principles
- Modular "barrel" pattern for feature isolation
- Event-driven async communication
- Single database connection pattern
- Library-based approach (no manual XML parsing)

---

## XEP Coverage

**Currently Implemented** (29+ XEPs):
- Core XMPP (RFC 6120, 6121, 6122)
- XEP-0030 (Service Discovery)
- XEP-0045 (Multi-User Chat)
- XEP-0077 (In-Band Registration)
- XEP-0084, 0153 (Avatars)
- XEP-0085 (Chat State Notifications)
- XEP-0166, 0167, 0176 (Jingle RTP)
- XEP-0184 (Message Delivery Receipts)
- XEP-0191 (Blocking Command)
- XEP-0198 (Stream Management)
- XEP-0280 (Message Carbons)
- XEP-0308 (Last Message Correction)
- XEP-0313 (Message Archive Management)
- XEP-0333 (Chat Markers)
- XEP-0353 (Jingle Message Initiation)
- XEP-0363 (HTTP File Upload)
- XEP-0384 (OMEMO Encryption)
- XEP-0402 (PEP Native Bookmarks)
- XEP-0444 (Message Reactions)
- XEP-0454 (OMEMO Media Sharing)
- XEP-0461 (Message Replies)
- And more...

**Under Consideration**:
- Additional presence and status XEPs
- Enhanced MUC features
- File transfer improvements

---

## Client Compatibility

**Working**:
- Dino (desktop)
- Conversations.im (mobile)

**Testing Planned**:
- Gajim (desktop)
- Monocles (mobile)
- Other standards-compliant clients

---

## Contributing

We welcome contributions that align with our privacy-first, standards-compliant approach.

**Priority Areas**:
- Cross-platform testing and packaging
- Video call implementation
- Additional XEP implementations
- Documentation improvements
- Bug reports and testing

See `docs/CONTRIBUTING.md` for detailed guidelines.

---

## Success Criteria

The project is considered production-ready when:
- Audio and video calls work reliably across platforms
- Cross-platform builds available (Linux, Windows, macOS)
- Compatible with major XMPP clients
- No critical security or privacy issues
- Comprehensive user documentation
- Active testing and feedback from real users

---

## Philosophy

**Development Approach**:
- Implement features completely, not partially
- Test with real XMPP servers and clients
- Document both successes and limitations
- Maintain backward compatibility
- Respect user privacy at all times

**What We Won't Do**:
- Add telemetry or tracking
- Break XMPP standards compliance
- Compromise user privacy
- Rush features at the expense of quality

---

For detailed technical architecture, see `docs/ARCHITECTURE.md`.

For contributor guidelines, see `docs/CONTRIBUTING.md`.

For build instructions, see `docs/BUILD.md`.
