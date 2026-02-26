# Conversations.im Incoming Call Connection Issue - Systematic Investigation

**Status:** ACTIVE INVESTIGATION
**Created:** 2026-02-26
**Issue:** Incoming calls from Conversations mobile app fail to connect (ICE connectivity-error after 1s)
**Working:** Outgoing calls TO Conversations work, incoming FROM Dino work

---

## Current Hypothesis

**Initial Theory (from SESSION-ROTATE.md):** GStreamer webrtcbin only generates Component 1 (RTP) candidates, not Component 2 (RTCP).

**Key Observation:** Dino uses GStreamer webrtcbin AND successfully receives calls from Conversations.im
→ This proves GStreamer CAN work with Conversations
→ We must be configuring webrtcbin differently than Dino

---

## CRITICAL DISCOVERY - Dino's Architecture (2026-02-26)

**Dino does NOT use webrtcbin!** They use a completely different architecture:

### Dino's Stack:
1. **GStreamer rtpbin** (NOT webrtcbin) - Lower-level RTP/RTCP handling
   - Separate RTP and RTCP pads: `send_rtp_sink`, `send_rtcp_src`, `recv_rtp_sink`, `recv_rtcp_sink`
   - Manual component handling via appsrc/appsink
   - No built-in ICE/DTLS - handled separately

2. **libnice (Nice.Agent)** - ICE/STUN/TURN directly
   - Manual ICE candidate gathering for **BOTH Component 1 AND Component 2**
   - Line 109-112 in `transport_parameters.vala`: Sets TURN for **ALL components**:
     ```vala
     for (uint8 component_id = 1; component_id <= components; component_id++) {
         agent.set_relay_info(stream_id, component_id, turn_ip, ...);
     }
     ```
   - Line 98: `stream_id = agent.add_stream(components)` - Creates multi-component stream

3. **Manual DTLS-SRTP** - Custom implementation
   - `dtls_srtp_handler` in `dtls_srtp.vala`
   - Manual certificate handling
   - Encrypts/decrypts data manually before/after sending via Nice.Agent

4. **Manual Jingle ↔ SDP Translation** - No SDP involved!
   - Direct Jingle XML parsing/generation
   - No webrtcbin or SDP layer at all
   - `candidate_to_jingle()` and `candidate_to_nice()` functions

### Our Stack (webrtcbin):
1. **GStreamer webrtcbin** - All-in-one WebRTC
   - Built-in ICE via libnice (but abstracted)
   - Built-in DTLS-SRTP
   - Built-in SDP generation
   - **Problem:** Internally defaults to RTCP-mux, only generates Component 1

### Why Dino Works with Conversations:
- Dino **manually allocates ICE for both components** (line 109-112)
- Dino uses libnice directly, not through webrtcbin abstraction
- Dino has full control over component generation
- Dino doesn't rely on SDP or bundle policies

### Why We Fail:
- webrtcbin abstracts away component control
- webrtcbin defaults to modern WebRTC (Component 1 only, RTCP-mux)
- We cannot force webrtcbin to generate Component 2 candidates
- This is a fundamental limitation of webrtcbin for legacy Jingle clients

---

## Investigation Plan

### Phase 1: Study Working Implementation (Dino)
**Goal:** Understand how Dino configures GStreamer to work with Conversations

- [ ] 1.1: Download and examine Dino source code
  - Repo: https://github.com/dino/dino
  - Focus: `plugins/ice/src/` and `plugins/rtp/src/`
  - Look for: webrtcbin configuration, bundle-policy, rtcp-mux handling

- [ ] 1.2: Find Dino's webrtcbin initialization
  - Search for: `g_object_new("webrtcbin"`, `gst_element_factory_make("webrtcbin"`
  - Properties set: bundle-policy, ice-transport-policy, stun-server, turn-server
  - Signal connections: on-negotiation-needed, on-ice-candidate, pad-added

- [ ] 1.3: Compare Dino's SDP handling with ours
  - How does Dino parse Jingle session-initiate from Conversations?
  - How does Dino construct SDP to pass to webrtcbin?
  - Does Dino add/remove any SDP attributes before passing to GStreamer?

- [ ] 1.4: Check Dino's transceiver creation
  - When does Dino add transceivers?
  - What direction (sendrecv/sendonly/recvonly)?
  - Any special properties set on transceivers?

### Phase 2: Compare SDP Negotiation
**Goal:** Find differences between working (Dino) and failing (our) calls

- [ ] 2.1: Capture Conversations→Dino call SDP
  - Get Jingle session-initiate from Conversations
  - Get SDP that Dino passes to webrtcbin (if logged)
  - Get Dino's SDP answer
  - Get Jingle session-accept back to Conversations

- [ ] 2.2: Capture Conversations→Siproxylin call SDP
  - Session: `wNmOsnmKl3aOppi5Mf3HNw` (latest test with host candidates)
  - Already have: Jingle session-initiate, our SDP answer
  - Compare line-by-line with Dino's

- [ ] 2.3: Check for critical SDP differences
  - `a=rtcp:` line presence/absence
  - `a=rtcp-mux` presence/absence
  - `a=bundle-only` attribute
  - ICE credentials (ufrag/pwd)
  - Codec parameters (fmtp)
  - RTP extensions

### Phase 3: Verify DTLS/Crypto Stack
**Goal:** Rule out DTLS handshake issues

- [ ] 3.1: Check GStreamer build configuration
  ```bash
  gst-inspect-1.0 dtlssrtpenc
  gst-inspect-1.0 dtlssrtpdec
  ldd $(which gst-inspect-1.0) | grep -E 'ssl|tls'
  ```
  - Verify: openssl or mbedtls present
  - Verify: DTLS 1.2 support

- [ ] 3.2: Capture network traffic (pcap)
  ```bash
  tcpdump -i any -w conversations-call.pcap udp port 50452
  ```
  - Check: DTLS ClientHello from Conversations
  - Check: Our DTLS ServerHello response
  - Check: DTLS handshake completion
  - Check: SRTP packets flowing

- [ ] 3.3: Enable maximum GStreamer debug logging
  ```bash
  GST_DEBUG=*:3,webrtc*:5,dtls*:5,ice*:5,nice*:5 ./drunk-call-service-linux
  ```
  - Look for: DTLS errors
  - Look for: ICE state transitions
  - Look for: Component 1 vs Component 2 mentions

### Phase 4: Test Specific Fixes
**Goal:** Systematically test each potential issue

- [ ] 4.1: Test bundle-policy variations
  - Current: `MAX_BUNDLE` for trickle-only peers
  - Try: `MAX_COMPAT` (bundle but compatible with non-bundle)
  - Try: `NONE` (separate transport per media)
  - Compare ICE candidates generated for each

- [ ] 4.2: Test explicit rtcp-mux handling
  - Add `a=rtcp-mux` to our answer SDP (force it)
  - Remove `a=rtcp:` line if present
  - See if Conversations accepts it

- [ ] 4.3: Test transceiver direction changes
  - Current: sendrecv (default)
  - Try: Explicitly set sendrecv on transceiver before answer
  - Try: Match exact direction from Conversations offer

- [ ] 4.4: Test ICE transport policy
  - Current: ALL (for non-relay-only) or RELAY (for relay-only)
  - Try: Force ALL for Conversations
  - Verify host, srflx, and relay candidates generated

- [ ] 4.5: Test DTLS role forcing
  - Check: What DTLS role does Conversations expect?
  - Check: What role does GStreamer default to?
  - Try: Force client/server role via SDP attributes

### Phase 5: Component 1 vs Component 2 Deep Dive
**Goal:** Confirm if component 2 is actually the issue

- [ ] 5.1: Parse ICE candidate strings from Conversations
  - Extract component field (2nd field in candidate string)
  - Count: How many component 1 vs component 2?
  - Check: Does Conversations send candidates for audio AND video separately?

- [ ] 5.2: Parse our generated ICE candidates
  - Check component field in all candidates
  - Verify: Are we generating ANY component 2 candidates?
  - Test: Does changing bundle-policy affect this?

- [ ] 5.3: Check libnice (ICE library) behavior
  ```bash
  gst-inspect-1.0 webrtcbin | grep -i component
  gst-inspect-1.0 webrtcbin | grep -i rtcp
  ```
  - Look for: Properties to control component generation
  - Look for: Signals related to ICE components

- [ ] 5.4: Test with explicit component 2 request
  - Research: Can we force libnice to allocate component 2?
  - Research: GStreamer rtpbin vs webrtcbin component handling
  - Test: Manual SDP manipulation to request separate RTCP

### Phase 6: Conversations App Analysis (if needed)
**Goal:** Understand Conversations' exact expectations

- [ ] 6.1: Review Conversations Jingle implementation
  - Repo: https://github.com/iNPUTmice/Conversations
  - File: `src/.../xmpp/jingle/` (Java code)
  - Look for: ICE candidate validation logic
  - Look for: What causes `connectivity-error` termination?

- [ ] 6.2: Check Conversations changelog/issues
  - Search: GStreamer, webrtc, ICE, component
  - Look for: Known compatibility issues
  - Look for: Configuration requirements

### Phase 7: Compare with Working Outgoing Calls
**Goal:** Understand why outgoing TO Conversations works but incoming doesn't

- [ ] 7.1: Review successful outgoing call logs
  - Session: `e91358e0-4d8b-4fd4-8317-587201fb9a8c` (working outgoing)
  - Extract: SDP offer we sent
  - Extract: ICE candidates we generated
  - Compare: With failing incoming call

- [ ] 7.2: Identify key difference in flow
  - Outgoing: We call CreateOffer, Conversations gets our SDP first
  - Incoming: Conversations calls, we CreateAnswer to their SDP
  - Question: Does CreateOffer vs CreateAnswer affect component generation?

### Phase 8: GStreamer Forums & Community
**Goal:** Leverage community knowledge

- [ ] 8.1: Search GStreamer Discourse for similar issues
  - Keywords: "webrtcbin component 2", "webrtcbin Jingle", "webrtcbin RTCP"
  - Look for: Solutions or workarounds

- [ ] 8.2: Search GStreamer GitLab issues
  - Repo: https://gitlab.freedesktop.org/gstreamer/gstreamer
  - Keywords: rtcp-mux, component, Jingle, Conversations

- [ ] 8.3: Consider asking on GStreamer Discourse
  - Prepare: Minimal example (SDP, logs, config)
  - Ask: "How to generate ICE component 2 with webrtcbin for non-rtcp-mux peer?"

---

## Test Session Reference

### Failed Incoming Call (Latest)
- **Session ID:** `wNmOsnmKl3aOppi5Mf3HNw`
- **Time:** 2026-02-26 11:17:16
- **Config:** MAX_BUNDLE, ICE policy ALL (not relay-only)
- **Generated:** Host, srflx, relay candidates (all component 1)
- **Result:** Conversations terminated with connectivity-error after 1s

### Working Incoming Call (Dino)
- **Session ID:** `8df41ab0-edca-44f4-9213-da7c7c543d09`
- **Time:** 2026-02-25 22:31:13
- **Result:** Full duplex audio working

### Working Outgoing Call (to Conversations)
- **Session ID:** `e91358e0-4d8b-4fd4-8317-587201fb9a8c`
- **Time:** 2026-02-25 23:54:15
- **Result:** Full duplex audio working

---

## Current Code State

**Modified files (trickle-only detection):**
- `drunk_call_service/proto/call.proto` - Added `is_trickle_only_peer`
- `drunk_call_hook/protocol/jingle.py` - Detect trickle-only peers
- `siproxylin/core/barrels/calls.py` - Pass flag
- `drunk_call_hook/bridge.py` - Handle flag + dynamic relay-only
- `drunk_call_service/include/session.h` - Config field
- `drunk_call_service/src/call_server.cc` - Read flag
- `drunk_call_service/src/session.cc` - Dynamic bundle-policy

**Status:** Changes kept for future use, but didn't solve the issue

---

## Key Questions to Answer

1. **How does Dino configure webrtcbin differently?**
   - Bundle policy, transceiver setup, SDP manipulation?

2. **Is component 2 really required?**
   - Or is there another mismatch (DTLS, codecs, extensions)?

3. **Why do outgoing calls work but not incoming?**
   - What's different about CreateOffer vs CreateAnswer flow?

4. **Can GStreamer webrtcbin generate component 2 at all?**
   - Or is there a fundamental limitation we're hitting?

---

## Success Criteria

- [ ] Incoming calls from Conversations.im connect successfully
- [ ] Audio flows both directions (full duplex)
- [ ] ICE connection state reaches "connected" or "completed"
- [ ] No `connectivity-error` termination from Conversations

---

## Rollback Plan

If investigation shows this is impossible with GStreamer webrtcbin:
1. Document limitation in CALLS-ON-C++-DESIGN.md
2. Revert trickle-only detection code (or keep for future)
3. Focus on other features (video, device enumeration)
4. Guide users to use outgoing calls to Conversations (which work)

---

## Path Forward - Three Options

### Option A: Switch to rtpbin + libnice (Like Dino) ⚠️ **HIGH EFFORT**

**Advantages:**
- ✅ Full control over ICE components (can generate Component 2)
- ✅ Proven to work with Conversations (Dino's approach)
- ✅ More flexibility for legacy Jingle clients

**Disadvantages:**
- ❌ **Major rewrite** - Complete rearchitecture of C++ service
- ❌ Lose webrtcbin's conveniences (built-in DTLS, SDP handling)
- ❌ Need to manually implement DTLS-SRTP layer
- ❌ Need to manually handle ICE state machine
- ❌ Estimated effort: 5-7 days of work
- ❌ Higher complexity and maintenance burden

**Implementation:**
- Replace `webrtcbin` with `rtpbin`
- Use libnice C API directly (not through GStreamer)
- Implement custom DTLS-SRTP handler (or use Dino's approach)
- Manual RTP/RTCP encryption/decryption
- Manual ICE candidate gathering for both components

### Option B: Keep webrtcbin, Document Limitation ✅ **RECOMMENDED**

**Advantages:**
- ✅ **No code changes needed**
- ✅ Audio calls work perfectly with Dino and other RTCP-mux clients
- ✅ Outgoing calls TO Conversations work fine
- ✅ Can focus on other features (video, device enumeration, stats)
- ✅ webrtcbin is modern, well-maintained, and handles 90% of use cases

**Disadvantages:**
- ❌ Incoming calls FROM Conversations don't work
- ❌ Affects Conversations mobile users calling us

**Workaround for Users:**
- Conversations users can call each other via outgoing calls
- Other XMPP clients (Dino, Gajim) work fine for incoming calls
- Document in UI: "Incoming calls from Conversations may not connect"

**Documentation:**
- Update `CALLS-ON-C++-DESIGN.md` with known limitation
- Add comment in code explaining webrtcbin component behavior
- Note in release notes

### Option C: Hybrid Approach - Component 2 Workaround 🔬 **EXPERIMENTAL**

**Idea:** Keep webrtcbin but manually inject Component 2 candidates

**Theory:**
- Let webrtcbin generate Component 1 candidates normally
- Duplicate Component 1 candidates as Component 2 (same IPs, different ports)
- Send fake Component 2 candidates to Conversations
- Hope Conversations doesn't validate that Component 2 actually works

**Advantages:**
- ✅ Minimal code changes (just candidate duplication logic)
- ✅ Might trick Conversations into accepting the call

**Disadvantages:**
- ❌ **Hacky and unreliable**
- ❌ Component 2 candidates are fake (no actual RTCP port allocated)
- ❌ Conversations might validate candidates and still reject
- ❌ RTCP still won't flow on Component 2 (only Component 1)
- ❌ May break with future Conversations updates

**Not Recommended:** Too hacky, likely to fail

### Option D: Patch GStreamer webrtcbin 🔧 **VIABLE BUT HEAVY**

**Discovery:** webrtcbin hardcodes 1 component per stream in `gstwebrtcice.c:_create_nice_stream_item()`:
```c
item.nice_stream_id = nice_agent_add_stream (ice->priv->nice_agent, 1);  // ← HARDCODED!
```

**Advantages:**
- ✅ Proper fix at the root cause
- ✅ Would benefit entire GStreamer community (legacy Jingle support)
- ✅ Could be upstreamed to GStreamer project
- ✅ Once patched, works like Dino (both components allocated)
- ✅ No need to rewrite our entire service

**Disadvantages:**
- ❌ Need to build custom GStreamer plugins (libgstwebrtc.so)
- ❌ Need to maintain patch across GStreamer updates
- ❌ Upstreaming may take time (if accepted at all)
- ❌ Users need custom GStreamer build (packaging complexity)
- ⚠️ Medium effort: 1-2 days to patch + test

**Implementation:**
1. Fork gst-plugins-bad repository
2. Modify `ext/webrtc/gstwebrtcice.c:_create_nice_stream_item()`
3. Change: `nice_agent_add_stream(..., 1)` → `nice_agent_add_stream(..., 2)`
4. Add property to webrtcbin: `n-components` (default: 1 for compat, 2 for Jingle)
5. Build and install patched libgstwebrtc.so
6. Test with Conversations incoming calls
7. (Optional) Submit patch upstream to GStreamer

**Files to Modify:**
- `gst-plugins-bad/ext/webrtc/gstwebrtcice.c` - Change component count
- `gst-plugins-bad/ext/webrtc/gstwebrtcbin.c` - Add `n-components` property
- `gst-plugins-bad/ext/webrtc/transportstream.c` - Handle RTCP component

**Packaging:**
- Ship patched `libgstwebrtc.so` with siproxylin
- Set `LD_LIBRARY_PATH` or install to system
- Or use AppImage with bundled patched GStreamer

### Option E: Subclass GstWebRTCICE (Override add_stream) ⭐ **BEST SOLUTION!**

**Discovery:** `add_stream` is a **VIRTUAL METHOD** in `GstWebRTCICEClass`! (line 78-79 in ice.h)

```c
struct _GstWebRTCICEClass {
  GstObjectClass parent_class;
  GstWebRTCICEStream * (*add_stream) (GstWebRTCICE * ice, guint session_id);  // ← VIRTUAL!
```

**And webrtcbin has an `ice-agent` property** (readable + writable) where we can inject our custom implementation!

**Advantages:**
- ✅ **Zero GStreamer patching needed!**
- ✅ Pure C++ code in our project
- ✅ Clean OOP design (inheritance)
- ✅ No packaging complexity
- ✅ No maintenance of GStreamer patches
- ✅ Works with any GStreamer version
- ⚠️ Low effort: Half day to implement + test

**Disadvantages:**
- ⚠️ Slightly more code than Option D (subclass boilerplate)
- ⚠️ Need to understand GObject type system
- (No significant disadvantages!)

**Implementation:**

1. **Create custom ICE subclass** in our C++ service:
   ```cpp
   // Custom ICE agent with 2-component support
   typedef struct _DualComponentICE DualComponentICE;
   typedef struct _DualComponentICEClass DualComponentICEClass;

   struct _DualComponentICE {
     GstWebRTCICE parent;
     gboolean use_dual_components;  // Property to enable
   };

   struct _DualComponentICEClass {
     GstWebRTCICEClass parent_class;
   };

   G_DEFINE_TYPE(DualComponentICE, dual_component_ice, GST_TYPE_WEBRTC_ICE)
   ```

2. **Override add_stream method**:
   ```cpp
   static GstWebRTCICEStream* dual_component_ice_add_stream(
       GstWebRTCICE* ice, guint session_id) {
     DualComponentICE* self = DUAL_COMPONENT_ICE(ice);

     // Call parent to create stream
     GstWebRTCICEStream* stream = GST_WEBRTC_ICE_CLASS(
         dual_component_ice_parent_class)->add_stream(ice, session_id);

     if (self->use_dual_components) {
       // Access nice_agent from parent and add component 2
       // (Need to access private struct, see below)
     }

     return stream;
   }
   ```

3. **Set custom ICE agent on webrtcbin**:
   ```cpp
   // In Session::Init()
   DualComponentICE* custom_ice = g_object_new(
       TYPE_DUAL_COMPONENT_ICE,
       "use-dual-components", is_trickle_only_peer,
       NULL);

   g_object_set(webrtcbin_, "ice-agent", custom_ice, NULL);
   ```

**Challenges:**

1. **Private struct access**: The actual `nice_agent` is in private struct
   - **Solution A**: Use GObject properties to access nice_agent
   - **Solution B**: Don't subclass GstWebRTCICE, subclass the actual implementation (e.g., GstWebRTCNiceICE if it exists)
   - **Solution C**: Re-implement add_stream entirely without calling parent

2. **Finding the implementation class**: Need to find what actually implements GstWebRTCICE
   - Check: Is there a `GstWebRTCNiceICE` class?
   - Or do we need to subclass at a different level?

**Next Steps:**
1. Find the actual ICE implementation class (likely `GstWebRTCNiceICE` or similar)
2. Check if we can access `nice_agent` handle
3. Implement subclass with component-2 support
4. Test with Conversations incoming call

**Files to Add:**
- `drunk_call_service/src/dual_component_ice.cc` - Custom ICE subclass
- `drunk_call_service/include/dual_component_ice.h` - Header
- Update `session.cc` to use custom ICE agent

---

## Updated Recommendations After Investigation

### The Winner: Option E! ⭐

After discovering that `add_stream` is a virtual method and `ice-agent` is injectable, we have a clear winner:

**Option E: Subclass GstWebRTCICE** (half day)
- ✅ No GStreamer patching
- ✅ Clean C++ code in our project
- ✅ Zero packaging complexity
- ✅ Works with any GStreamer version

**Alternatives if Option E fails:**

**Option D: Patch GStreamer** (1-2 days)
- Fallback if private struct access blocks Option E
- Requires maintaining GStreamer patch

**Option B: Document Limitation** (0 days)
- Fallback if both E and D prove too complex
- Simple workaround exists

### Recommended: Try Option E first!

**Choose Option D if:**
- You want incoming calls from Conversations to work
- You're okay with maintaining a GStreamer patch
- You have 1-2 days to implement and test
- You plan to use AppImage (easy to bundle patched lib)

**Choose Option B if:**
- Time is critical for other features (video, device enumeration)
- You want zero maintenance overhead
- Conversations incoming calls are not a priority
- You can accept the workaround (users call us instead)

### My Recommendation: **Option E now!**

**Reasoning:**
1. **Low effort**: Half day vs 1-2 days for Option D
2. **Zero complexity**: No patching, no packaging issues
3. **Clean solution**: Proper OOP inheritance
4. **Quick test**: Can prove feasibility in 1-2 hours

**Implementation Path:**
1. **Today**: Implement Option E subclass (half day)
2. **Test**: Verify with Conversations incoming call
3. **If blocked**: Fall back to Option B + revisit later
4. **If works**: Move on to video features

**Rationale for "now" vs "later":**
- Only half day investment
- High chance of success (virtual methods are designed for this!)
- Solves the problem properly without technical debt
- If it doesn't work, we've only lost half a day

---

## If We Choose Option A (rtpbin Migration)

**Estimated Timeline:** 5-7 days
**Risk:** High (major architectural change)

### Implementation Steps:

1. **Study Dino's code in detail** (1 day)
   - Understand rtpbin pad linking
   - Study libnice API usage
   - Study DTLS-SRTP implementation

2. **Replace webrtcbin with rtpbin** (2 days)
   - Rewrite `Session::Init()` to use rtpbin
   - Manual pad linking for RTP/RTCP
   - Update audio pipeline connections

3. **Integrate libnice directly** (2 days)
   - Use libnice C API (not GStreamer plugin)
   - Implement candidate gathering for both components
   - Handle ICE state callbacks

4. **Implement DTLS-SRTP** (2 days)
   - Use OpenSSL or similar for DTLS
   - Manual SRTP key derivation
   - Encrypt/decrypt RTP/RTCP packets manually

5. **Testing** (1 day)
   - Test with Dino (both directions)
   - Test with Conversations (both directions)
   - Verify Component 2 candidates generated

### Code Areas to Modify:
- `drunk_call_service/src/session.cc` - Full rewrite of pipeline setup
- `drunk_call_service/include/session.h` - Add libnice agent members
- New files: `dtls_srtp.cc`, `dtls_srtp.h` (DTLS handler)
- Update CMakeLists.txt - Add libnice and OpenSSL dependencies

---

---

## Investigation Summary (2026-02-26)

### What We Discovered:

1. **Dino's Architecture**: Uses rtpbin + libnice directly (NOT webrtcbin)
   - Manual control over ICE component allocation
   - Explicitly allocates both Component 1 (RTP) and Component 2 (RTCP)
   - Code reference: `transport_parameters.vala:109-112`

2. **webrtcbin Implementation**: Built on top of rtpbin BUT...
   - Hardcodes 1 component per stream in `gstwebrtcice.c:_create_nice_stream_item()`
   - Line: `nice_agent_add_stream(ice->priv->nice_agent, 1);`
   - No property to configure component count
   - Designed for modern WebRTC with RTCP-mux only

3. **Root Cause**: webrtcbin cannot generate Component 2 (RTCP) candidates
   - Not a configuration issue
   - Not a bundle-policy issue
   - **Hardcoded limitation in GStreamer source code**

### Four Options Identified:

- **Option A**: Rewrite with rtpbin + libnice (5-7 days) - Too much effort
- **Option B**: Document limitation (0 days) - Simple, works for most
- **Option C**: Fake Component 2 candidates - Too hacky
- **Option D**: Patch GStreamer webrtcbin (1-2 days) - **NEW DISCOVERY!**

### Recommended Path:

**Short term:** Option B (document limitation)
**Long term:** Consider Option D (patch GStreamer) in future milestone

---

**Last Updated:** 2026-02-26
**Status:** Investigation complete, awaiting user decision
