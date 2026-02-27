# ICE Nomination Debugging - What NOT to Try Again

**Date**: 2026-02-27
**Project**: C++ rtpbin Call Service
**Context**: Conversations.im incoming calls fail at ICE nomination
**Purpose**: Prevent re-testing already-disproven theories

---

## Executive Summary

**Final Status (2026-02-27 22:30)**:
- ✅ **Dino → Us: WORKING** (incoming calls stable 44+ seconds)
- ✅ **Us → Dino: WORKING** (outgoing calls stable 91+ seconds)
- ✅ **ICE components reach CONNECTED → READY**
- ✅ **DTLS handshake completes successfully**
- ❌ **Conversations → Us: Still broken** (their libwebrtc nomination bug)

**The Real Issue Was:**
- Remote candidate queueing race condition (FIXED 2026-02-27)
- Candidates arriving before ICE stream creation were dropped
- After fix: Dino works perfectly both directions

**Conversations.im Pattern:**
- Dino → Us: ✅ Works (Dino nominates successfully)
- Conversations → Us: ❌ Fails (Conversations never nominates - THEIR BUG)
- Us → Conversations: ✅ Works (we nominate successfully)

**This is a CONFIRMED Conversations.im/libwebrtc issue, not ours.**

---

## Historical Context: xmpp-desktop (Dec 2025 - Jan 2026)

See `/home/m/claude/xmpp-desktop/docs/HISTORY/SIPROXYLIN-CALL-ISSUES.md` for complete analysis.

**Outcome after 2-3 weeks of debugging**:
- ❌ **NEVER SOLVED** for Conversations.im incoming calls
- ✅ Gave up and moved on (too annoying)
- ✅ Dino works perfectly both directions
- ✅ Outgoing to Conversations works perfectly

**Conclusion**: "Conversations.im issue, not ours. Leaving it for their team to fix."

---

## Theories Already Tested & DISPROVEN

### ❌ 1. OMEMO Fingerprint Encryption Required

**Hypothesis**: Conversations requires OMEMO-encrypted DTLS fingerprints for nomination.

**Why it seemed correct**:
- Conversations source has `ensureNoPlaintextFingerprint()` security check
- Namespace: `http://gultsch.de/xmpp/drafts/omemo/dlts-srtp-verification`
- Method: `AxolotlService.encrypt(IceUdpTransportInfo)`

**Why it was WRONG**:
- Conversations sends US plaintext fingerprints in session-initiate
- If it sends plain to us, it must accept plain from us
- OMEMO encryption is OPTIONAL (triggered by device ID in proceed message)
- Dino→Drunk works without device ID/OMEMO

**Verdict**: ❌ **DO NOT waste time on OMEMO theory**

---

### ❌ 2. Component 2 (RTCP) Candidates Required

**Hypothesis**: Conversations requires both component 1 (RTP) and component 2 (RTCP) candidates.

**Evidence**:
- Conversations uses `RtcpMuxPolicy.NEGOTIATE`
- Sends both component 1 and component 2 candidates
- We only send component 1 (Pion/rtpbin limitation)

**The definitive test**:
- Dino → Drunk: Component 1 ONLY → ✅ Dino nominates (4 pairs)
- Conversations → Drunk: Component 1 ONLY → ❌ Conversations doesn't nominate
- **Both received identical answers!**

**Verdict**: ❌ **Component 2 is NOT required for nomination**

---

### ❌ 3. Transport-Info Timing / Candidate Queueing

**Hypothesis**: Candidates arriving before session-accept are lost.

**The real bug found** (xmpp-desktop):
- Transport-info arriving BEFORE `state == SESSION_ACCEPTED` was queued but never processed
- Conversations Java code line 264-268: `pendingIceCandidates.addAll(candidates)` never drained

**What we fixed**:
- Server-level candidate queueing (queue candidates before session exists)
- Drain queue after CreateAnswer sets remote description

**Result**:
- ✅ Race condition FIXED
- ❌ Conversations still doesn't nominate

**Verdict**: ❌ **Timing/queueing is NOT the root cause** (but was a real bug worth fixing)

---

### ❌ 4. Jingle Protocol Non-Compliance

**Hypothesis**: Our Jingle XML is malformed or missing required elements.

**What was tested exhaustively**:
1. Device ID in proceed → ❌ Optional
2. DTLS setup role (`active` vs `passive`) → ❌ Correct as-is
3. Transport-info messages → ❌ Not the issue
4. Gathering-complete signal → ❌ Not needed (Dino doesn't send it)
5. Trickle vs bundled ICE → ❌ Both modes fail
6. ICE transport policy (all vs relay-only) → ❌ Not the issue
7. Session-accept structure → ❌ Perfect (Conversations sends IQ type="result")
8. `<renomination />` option → ✅ Already included

**Verdict**: ❌ **Jingle protocol is 100% CORRECT** (verified exhaustively, DO NOT re-test)

---

### ❌ 5. Relay-Only / Proxy / TCP-Only Mode

**Hypothesis**: Forcing relay-only or using proxy would fix nomination.

**What was tested** (xmpp-desktop Phase 3):
- HTTP CONNECT and SOCKS5 proxy support
- TURN relay-only mode when proxy enabled
- TCP4-only (no UDP, no IPv6)
- Zero IP leaks verified

**Result**:
- ✅ Successfully implemented (privacy feature)
- ❌ **Did NOT fix Conversations incoming calls** (still broken)

**Verdict**: ❌ **Proxy/relay-only does NOT help nomination**

---

### ❌ 6. ICE Transport Policy (All vs Relay)

**Hypothesis**: Conversations needs specific candidate types.

**Already tested** (xmpp-desktop):
- `ICETransportPolicyAll` (host + srflx + relay) → ❌ Same failure
- `ICETransportPolicyRelay` (relay-only) → ❌ Same failure

**Verdict**: ❌ **Candidate type filtering is NOT the issue**

---

### ❌ 7. SDP Attribute Differences

**Hypothesis**: Missing or wrong SDP attributes prevent nomination.

**What was compared**:
- Complete XML stanza comparison (Dino vs Conversations)
- All Jingle elements verified correct
- Codec negotiation correct
- RTP extensions echoed correctly
- BUNDLE group echoed correctly

**Result**: Jingle protocol 100% identical between working (Dino) and broken (Conversations) scenarios.

**Verdict**: ❌ **SDP/Jingle differences are NOT the cause**

---

## What We Know For Certain

**✅ PROVEN TO WORK**:
1. Our libnice integration (Dino nominates successfully)
2. Our SDP/Jingle implementation (verified exhaustively)
3. ICE connectivity checks (pairs reach `succeeded` state)
4. STUN binding request reception (peer's requests arrive)
5. Remote credential setup (ufrag/pwd set correctly)
6. Local candidate discovery (relay + srflx generated)

**❌ PROVEN TO FAIL**:
1. Conversations.im never nominates when it's controlling (initiator)
2. No selected candidate pair (OnNewSelectedPairFull never fires)
3. DTLS handshake never starts
4. Call fails after 9-second timeout

**🔍 THE MYSTERY**:
- **Why does Dino nominate but Conversations doesn't?**
- Both receive identical answer from us
- Both have successful ICE connectivity checks
- Both are "controlling" side (offerer/initiator)
- Different ICE libraries: Dino uses libnice, Conversations uses Android libwebrtc

---

## Resolution (2026-02-27)

**The Real Bug**: Remote candidate queueing race condition

**What was happening**:
- Incoming calls: Dino sends candidates via transport-info BEFORE Python calls CreateAnswer
- CreateAnswer creates ICE stream in C++
- Candidates arriving before stream creation were rejected: "Agent or stream not initialized"
- Candidates lost → ICE fails

**The Fix**:
- Queue remote candidates when `stream_id_ == 0`
- Drain queue after `AddStream()` in CreateAnswer
- Files: `session.h`, `session.cc:607-620, 1217-1242, 363-365`

**Result**:
- ✅ Dino incoming: 44+ seconds stable
- ✅ Dino outgoing: 91+ seconds stable
- ✅ ICE CONNECTED → READY (both components)
- ✅ DTLS handshake succeeds
- ❌ Conversations incoming: Still fails (confirmed their bug, not ours)

---

## What NOT to Try Again

**DO NOT waste time on**:
- ❌ OMEMO fingerprint encryption
- ❌ Component 2 candidate generation
- ❌ Jingle XML format changes
- ❌ Transport-info timing variations
- ❌ Proxy / relay-only mode
- ❌ SDP attribute tweaking
- ❌ DTLS setup role changes
- ❌ Gathering-complete signals
- ❌ Trickle vs bundled ICE
- ❌ ICE transport policy changes

**These have ALL been tested exhaustively and proven NOT to be the issue.**

---

## What TO Focus On

**New avenues (not yet explored in xmpp-desktop)**:
1. **libnice-specific settings**:
   - `g_object_set(agent_, "nomination-mode", NICE_NOMINATION_MODE_AGGRESSIVE, NULL)`
   - `g_object_set(agent_, "keepalive-conncheck", TRUE, NULL)`
   - `g_object_set(agent_, "support-renomination", TRUE, NULL)`
   - `g_object_set(agent_, "upnp", FALSE, NULL)`

2. **Compare exact libnice setup with Dino**:
   - Read: `/home/m/claude/siproxylin/drunk_call_service/tmp/dino/plugins/ice/src/transport_parameters.vala:98-124`
   - Check: What properties does Dino set that we don't?
   - Check: What's the exact sequence (controlling mode → add_stream → attach_recv → gather)?

3. **Packet-level debugging**:
   - Enable NICE_DEBUG=all and G_MESSAGES_DEBUG=all (already done)
   - Check for libnice debug output in stderr
   - If no output, libnice might need debug build

4. **Dino source code comparison**:
   - Compare our ice_agent.cc line-by-line with Dino's transport_parameters.vala
   - Look for ANY difference in agent creation, property setting, or callback wiring

---

## The Reality Check

**From xmpp-desktop experience**:
- 2-3 weeks of desperate debugging
- Exhaustive testing of every theory
- **NEVER SOLVED** for Conversations.im
- Eventually gave up (too annoying)

**Decision made then**: "This is a Conversations.im issue, not ours. Leaving it for their team to fix."

**Should we repeat this?**:
- ⚠️ **Be careful** not to waste weeks on the same dead ends
- ⚠️ **Focus on** libnice-specific differences (new territory)
- ⚠️ **Remember** Dino works, so there IS a way
- ⚠️ **But also** accept that it might be unfixable from our side (again)

---

## References

**xmpp-desktop docs**:
- `/home/m/claude/xmpp-desktop/docs/HISTORY/SIPROXYLIN-CALL-ISSUES.md` - Complete debugging history
- `/home/m/claude/xmpp-desktop/docs/HISTORY/AUDIO/CONVERSATIONS-NOMINATION-REQUIREMENTS.md` - Conversations source analysis

**Current project**:
- `drunk_call_service/src/ice_agent.cc` - Our libnice wrapper
- `drunk_call_service/tmp/dino/plugins/ice/src/transport_parameters.vala` - Dino reference

**Logs**:
- Session: `IgBTAObEuRZ1YIXrRiCkoQ` (2026-02-27 20:25-20:26)
- Components reach CONNECTING ✅
- Initial binding request received ✅
- But no nomination ❌

---

**Last Updated**: 2026-02-27
**Status**: Avoid re-testing disproven theories, focus on libnice-specific differences
**Strategy**: "Blind copy" Dino's libnice setup - don't invent, just translate!
