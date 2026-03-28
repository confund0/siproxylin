# OMEMO Encryption

End-to-end encryption for private messaging in Siproxylin.

## Overview

**What is OMEMO?**
- End-to-end encryption protocol for XMPP (XEP-0384)
- Based on Signal's Double Ratchet algorithm
- Encrypts messages so only sender and recipient can read them
- Server cannot decrypt messages
- Each device has its own encryption keys

**Security Benefits:**
- Forward secrecy: Past messages stay safe if keys are compromised
- Future secrecy: New keys generated for each message
- Multi-device support: Each device independently encrypted
- Metadata protection: Only message content encrypted, not metadata (sender/recipient/timestamp)

**Implementation:**
- Backend: python-omemo library via slixmpp
- Storage: SQLite database (`omemo_storage` and `omemo_device` tables)
- File uploads: Encrypted with AES-GCM, shared via aesgcm:// URLs (XEP-0454)

---

## Trust Levels

OMEMO uses a four-level trust system for device verification:

### 0 - Untrusted (Undecided)
- **Meaning**: New device, not yet verified by user
- **Display**: ⚠️ Untrusted (dark yellow)
- **When used**: Default state for new devices when Blind Trust is disabled
- **Encryption**: May be blocked depending on account policy
- **Action needed**: Manually verify fingerprint to upgrade to Verified

### 1 - Blind Trust
- **Meaning**: Automatically trusted on first use (BTBV policy)
- **Display**: 👁️ Blind Trust (blue)
- **When used**: Default state when Blind Trust Before Verification is enabled
- **Encryption**: Allowed without manual verification
- **Action needed**: Optionally verify fingerprint to upgrade to Verified

### 2 - Verified (Trusted)
- **Meaning**: Manually verified by user
- **Display**: ✅ Verified (dark green)
- **When used**: After user confirms fingerprint matches via out-of-band verification
- **Encryption**: Highest trust level
- **Recommended for**: Sensitive contacts, important conversations

### 3 - Compromised (Distrusted)
- **Meaning**: Manually marked as untrustworthy
- **Display**: ❌ Compromised (red)
- **When used**: Device lost, stolen, or suspected compromised
- **Encryption**: Device EXCLUDED from encryption (messages won't be readable on this device)
- **Use case**: Force re-verification or permanently block compromised device

---

## Blind Trust Mode (BTBV)

### What is Blind Trust Before Verification?

BTBV is a trust policy that automatically trusts new devices on first use, allowing immediate encrypted communication without manual fingerprint verification.

**How It Works:**
- When contact adds a new device, it's automatically trusted (level 1)
- Encryption starts immediately without user intervention
- User can manually verify later to upgrade to level 2

**Trade-offs:**

**Enabled (default, more convenient):**
- ✅ Seamless encryption, no user action needed
- ✅ Better user experience
- ⚠️ Vulnerable to MITM attacks during initial key exchange
- ⚠️ Relies on server security

**Disabled (more secure):**
- ✅ All devices must be manually verified
- ✅ Protected against server-side key injection
- ⚠️ Encryption blocked until verification
- ⚠️ More tedious for users

**Configuration:**
- Account Settings → Security tab → "Blind Trust Before Verification (BTBV)" checkbox
- Default: Enabled

**Recommendation:**
- Enable for most contacts (convenience)
- Disable for highly sensitive contacts requiring paranoid security

---

## Contact Details Dialog - OMEMO Tabs

Access via:
- Right-click contact in roster → "View Details"
- Chat window header → Gear icon → "View Details"
- Contacts Manager → Select contact → "Edit Contact"

### Tab: OMEMO - Peer

Shows your contact's OMEMO devices.

**Device Table Columns:**
1. **Device ID** - Unique 32-bit identifier for the device
2. **Fingerprint** - Base64-encoded identity key, formatted in groups of 8 characters
3. **Trust Level** - Visual indicator with emoji and color (see Trust Levels above)
4. **First Seen** - When device was first discovered
5. **Last Seen** - Last activity timestamp
6. **Actions** - Buttons to change trust level

**Action Buttons:**

**For Verified devices (✅):**
- **Distrust** - Downgrade to Untrusted (level 0). Use when you want to re-verify.

**For Compromised devices (❌):**
- **Trust** - Upgrade to Verified (level 2). Use after device is recovered/secured.

**For Untrusted/Blind Trust devices (⚠️ or 👁️):**
- **Verify** - Upgrade to Verified (level 2). Use after confirming fingerprint matches.
- **Mark Compromised** (red) - Downgrade to Compromised (level 3). Use if device is suspected stolen/hacked.

**Workflow:**
1. Click action button
2. Confirmation dialog appears explaining the change
3. Click "Yes" to confirm
4. Device trust level updated immediately
5. Table refreshes to show new state

### Tab: OMEMO - Own

Shows your own OMEMO devices (all logged-in instances).

**Same columns and actions as "OMEMO - Peer" tab.**

**Use Cases:**
- Verify your other devices (phone, laptop, etc.)
- Mark lost/stolen devices as Compromised
- Check when devices were last active
- Audit your device list for security

---

## Device Verification Guide

### Proper Verification Process

**Never verify devices blindly!** Always compare fingerprints via secure out-of-band channel.

**Step-by-Step:**

1. **Share Fingerprints Securely**
   - In-person: Show fingerprints on screen
   - Phone call: Read fingerprints aloud
   - Video call: Show fingerprints on camera
   - DO NOT: Share via XMPP message (defeats the purpose!)

2. **Open Contact Details**
   - Right-click contact → "View Details"
   - Navigate to "OMEMO - Peer" tab

3. **Compare Fingerprints**
   - Contact shares their device fingerprint
   - Locate matching Device ID in table
   - Compare fingerprint character-by-character
   - Fingerprint format: `05c48712 2ba463fd 11223344 55667788`

4. **Take Action**
   - **If match**: Click "Verify" button → Device becomes ✅ Verified
   - **If mismatch**: Click "Mark Compromised" → Device becomes ❌ Compromised (DO NOT use it!)

5. **Verify All Devices**
   - Repeat for each of contact's devices
   - Verify your own devices via "OMEMO - Own" tab

### Fingerprint Format

**Per XEP-0384 Specification:**
- Lowercase hexadecimal encoding
- Grouped in sets of 8 characters
- 4 groups per line (for 32-byte keys = 64 hex chars = 2 lines)
- Monospace font for readability
- Case-insensitive (but displayed as lowercase)

**Format is compatible with:**
- Conversations (Android)
- Dino (Desktop)
- Gajim (Desktop)
- All XEP-0384 compliant OMEMO clients

**Example:**
```
05c48712 2ba463fd 11223344 55667788
99aabbcc ddeeff00 12345678 9abcdef0
```

---

## Account Settings

### OMEMO Configuration

**Location:** Account Settings → Security tab (or "Add Account" dialog)

**Settings:**

1. **Enable OMEMO encryption** (Checkbox)
   - Default: Checked
   - Enables OMEMO for this account
   - Disabling removes encryption capability

2. **OMEMO Mode** (Dropdown)
   - **default**: OMEMO enabled, encryption toggle available in chat
   - **optional**: Same as default (legacy naming)
   - **required**: Force OMEMO for all messages (not fully implemented)
   - **off**: OMEMO disabled, all messages plaintext
   - Default: 'default'

3. **Blind Trust Before Verification (BTBV)** (Checkbox)
   - Default: Checked
   - See "Blind Trust Mode" section above
   - Controls automatic device trust

### Encryption Toggle (In Chat)

**Location:** Chat window → Message input area → Encryption button

**Modes:**
- **Encrypted** (padlock icon): Messages encrypted with OMEMO
- **Plaintext** (open padlock): Messages sent unencrypted

**Behavior:**
- Toggle persists per-conversation
- Default: Encrypted (if OMEMO enabled)
- Falls back to plaintext if contact has no trusted devices

---

## Known Limitations

### Current Implementation

The OMEMO implementation is functional but has some limitations:

**Device Management:**
- Cannot remove old/lost devices from list
- No bulk trust operations (must verify each device individually)

**Verification:**
- No QR code scanning for fingerprint verification
- Manual character-by-character comparison required
- No automated fingerprint exchange

**Settings:**
- BTBV checkbox exists and saves to database, but DrunkXMPP `_btbv_enabled()` is hardcoded to `return True` (drunk_xmpp/client.py:247, omemo_support.py:98)
- "required" OMEMO mode not fully enforced

### Known Issues

**Phantom Subscriptions:**
- PubSub subscriptions can corrupt device lists
- Symptom: Duplicate devices, missing devices
- See: `old_docs/HISTORY/OMEMO-PHANTOM.md`

**JID Normalization:**
- JIDs must be lowercase for database lookups
- Mixed-case JIDs may cause device sync failures
- Implementation handles this automatically

**Privacy Lists:**
- Blocking contacts via privacy lists can prevent OMEMO initialization
- Use built-in blocking instead

---

## Technical Details

### Architecture

**Backend:**
- XMPP library: slixmpp (XEP-0384)
- Crypto library: python-omemo (Signal protocol)
- Storage backend: `siproxylin/db/omemo_storage.py`

**Database Tables:**
- `omemo_storage`: Cryptographic material (keys, sessions, trust state)
- `omemo_device`: GUI display metadata (device IDs, fingerprints, timestamps)

**Implementation Files:**
- `siproxylin/gui/contact_details_dialog.py` - OMEMO tabs UI
- `siproxylin/core/barrels/omemo.py` - OMEMO business logic
- `drunk_xmpp/omemo_support.py` - OMEMO protocol implementation
- `drunk_xmpp/omemo_devices.py` - Device query methods

### File Encryption

- Files encrypted with AES-GCM before upload
- Encryption key embedded in URL: `aesgcm://host/path#key`
- Recipient decrypts locally after download
- Server cannot decrypt files
- Implemented via XEP-0454 (OMEMO Media Sharing)

---

## FAQ

**Q: Why are some devices automatically trusted?**
A: Blind Trust Before Verification (BTBV) is enabled by default. Disable it in account settings for paranoid security.

**Q: What happens if I mark a device as Compromised?**
A: Messages will NOT be encrypted for that device. The device will be unable to read your messages.

**Q: Can I undo trust changes?**
A: Yes. Use action buttons to change trust level at any time.

**Q: How do I verify a contact's device?**
A: Compare fingerprints via secure channel (in-person, phone, video). See "Device Verification Guide" above.

**Q: Why doesn't the contact see my messages?**
A: Their device may be Untrusted/Compromised, or they disabled OMEMO. Check their device trust level in "OMEMO - Peer" tab.

**Q: Can the server read my messages?**
A: No. OMEMO provides end-to-end encryption. Only you and your contact can decrypt messages.

**Q: What if I lose my device?**
A: Log into another device, open "OMEMO - Own" tab, mark the lost device as Compromised. This excludes it from future encryption.

---

**Last Updated:** 2026-02-23
**Based on:** Current implementation + research from `old_docs/PHASE-CHATS/`
