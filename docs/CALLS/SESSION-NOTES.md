# Session Notes - rtpbin Implementation

**Purpose:** Practical notes between work sessions to avoid repeating mistakes

**Last Updated:** 2026-02-27

---

## Current Work: Phase 2 - rtpbin Implementation

**Branch:** `calls-rtpbin`
**Status:** ✅ ICE/DTLS working with Dino (both directions)
**Focus:** Audio verification, video support
**Strategy:** Copy Dino's architecture exactly

---

## Dino Source Code Reference

**Local Copy:** `/home/m/claude/siproxylin/drunk_call_service/tmp/dino`

**Key Files to Reference:**
- `plugins/ice/src/transport_parameters.vala` - ICE agent initialization (lines 98-150)
- `plugins/ice/src/dtls_srtp.vala` - DTLS-SRTP handler
- `plugins/rtp/src/stream.vala` - rtpbin pipeline setup
- `crypto-vala/src/srtp.vala` - libsrtp2 wrapper

**Quick Navigation:**
```bash
cd /home/m/claude/siproxylin/drunk_call_service/tmp/dino
grep -n "add_stream" plugins/ice/src/transport_parameters.vala
```

---

## Known Issues & Fixes

### Issue 1: Remote Candidate Queueing ✅ **[FIXED 2026-02-27]**

**Symptom:** Candidates arriving before ICE stream creation were rejected

**Root Cause:**
- Python sends remote candidates via `AddICECandidate()` from Jingle transport-info
- In incoming calls, candidates arrive BEFORE `CreateAnswer()` creates the ICE stream
- C++ session exists but `stream_id_ == 0` → "Agent or stream not initialized"
- Candidates were silently dropped → ICE failure

**The Fix:**
Queue candidates when stream doesn't exist, drain after stream creation:

```cpp
// In AddICECandidate():
if (ice_agent_->stream_id() == 0) {
  remote_candidate_queue_.push_back(queued);
  return true;  // Queue for later
}

// In CreateAnswer() after AddStream():
DrainRemoteCandidateQueue();  // Add queued candidates now
```

**Files Changed:**
- `drunk_call_service/include/session.h:193-201` - Queue structures
- `drunk_call_service/src/session.cc:607-620` - Queue logic
- `drunk_call_service/src/session.cc:1217-1242` - Drain implementation
- `drunk_call_service/src/session.cc:363-365` - Call drain

**Result:**
- ✅ Incoming calls from Dino: 44+ seconds stable
- ✅ Outgoing calls to Dino: 91+ seconds stable
- ✅ Both components reach CONNECTED → READY
- ✅ DTLS handshake completes
- ✅ No crashes, clean shutdown

**Test Sessions:**
- Incoming: `f850362c-3c94-4009-b561-97b7ff497f3a`
- Outgoing: `71513754-6a1e-4942-953b-218f32419d40`

### Issue 2: Candidate Parsing (FIXED ✅)

**Was Broken:** Guessing component_id from mline_index

**Root Cause:**
```cpp
// WRONG:
int component_id = sdp_mline_index + 1;
```

**Fix (Copied from GStreamer):**
```cpp
// CORRECT:
NiceCandidate* candidate = nice_agent_parse_remote_candidate_sdp(...);
guint component_id = candidate->component_id;  // Extract from parsed candidate!
```

**Reference:** `gstreamer-1.22/gst-libs/gst/webrtc/nice/nice.c:_parse_ice_candidate`

---

## Dino Reference Patterns

### Pattern 1: ICE Agent Creation

**Dino File:** `plugins/ice/transport_parameters.vala:98`

**Key Points:**
```vala
// 1. Create agent with components
stream_id = agent.add_stream(2);  // 2 components: RTP + RTCP

// 2. Set TURN for ALL components
for (uint8 component_id = 1; component_id <= 2; component_id++) {
    agent.set_relay_info(stream_id, component_id,
        turn_ip, turn_port, username, password, Nice.RelayType.UDP);
}

// 3. Attach recv callbacks for BOTH components
for (uint8 component_id = 1; component_id <= 2; component_id++) {
    agent.attach_recv(stream_id, component_id, ctx, on_recv);
}
```

**Our Implementation:** `src/ice_agent.cc:AddStream()`
- ✅ Creates 2 components
- ✅ Sets TURN for both
- ✅ Attaches recv callbacks
- ⚠️ May be missing timing or initialization details

### Pattern 2: DTLS-SRTP Integration

**Dino File:** `plugins/ice/dtls_srtp.vala`

**Key Points:**
```vala
// 1. Create DTLS handler BEFORE ICE starts
var dtls = new DtlsSrtp(mode);  // client/server based on SDP setup

// 2. Wire to ICE callbacks
datagram_connection.received.connect((data) => {
    if (is_dtls_packet(data)) {
        dtls.handle_incoming_data(data);
    } else if (is_srtp_packet(data)) {
        var decrypted = srtp.decrypt(data);
        push_to_rtpbin(decrypted);
    }
});

// 3. Trigger handshake when Component 1 reaches READY
ice_agent.component_state_changed.connect((component_id, state) => {
    if (component_id == 1 && state == READY) {
        dtls.start_handshake();
    }
});
```

**Our Implementation:** `src/dtls_srtp_handler.cc`
- ✅ DTLS handshake implemented
- ✅ SRTP encrypt/decrypt implemented
- ⚠️ Check: When is handshake triggered? (Component state callback)

### Pattern 3: SDP Parsing

**GStreamer Reference:** `gst-plugins-bad/gst/webrtc/webrtcsdp.c`

**Use GStreamer APIs (not manual parsing):**
```cpp
// GOOD (what we do):
GstSDPMessage* sdp;
gst_sdp_message_parse_buffer(sdp_string.data(), sdp_string.size(), &sdp);
const gchar* ice_ufrag = gst_sdp_message_get_attribute_val(sdp, "ice-ufrag");

// BAD (don't do this):
std::regex pattern("a=ice-ufrag:(.*)");
std::smatch match;
// ... manual regex parsing
```

**Our Implementation:** `src/sdp_parser.cc`
- ✅ Uses GstSDPMessage API
- ✅ Extracts ICE credentials
- ✅ Extracts DTLS fingerprint
- ✅ Parses setup attribute

---

## Common Pitfalls

### Pitfall 1: Component ID Confusion

**Wrong Assumption:** mline_index == component_id

**Reality:**
- Component 1: RTP (always)
- Component 2: RTCP (if separate, not rtcp-mux)
- mline_index: SDP m= line index (0 for audio, 1 for video)

**Example:**
- Video call SDP has mline_index=1 (video)
- But candidates can be component=1 (RTP) OR component=2 (RTCP)
- Must extract from candidate string itself!

### Pitfall 2: Pipeline State Timing

**Wrong Order:**
```cpp
CreateAnswer() {
    SetRemoteDescription(offer);  // ❌ Pipeline not ready
    SetPipelineState(PLAYING);
    CreateAnswer();  // ❌ May generate empty SDP
}
```

**Correct Order:**
```cpp
CreateAnswer() {
    SetPipelineState(PLAYING);  // ✅ First!
    WaitForStateChange();
    SetRemoteDescription(offer);
    CreateAnswer();  // ✅ Works
}
```

### Pitfall 3: Candidate String Format

**GStreamer expects:** `"candidate:..."`
**Python sends:** `"candidate:..."`
**libnice expects:** `"a=candidate:..."`

**Solution:**
```cpp
std::string candidate_with_prefix = "a=" + candidate;
NiceCandidate* parsed = nice_agent_parse_remote_candidate_sdp(
    agent, stream_id, candidate_with_prefix.c_str());
```

**Our Implementation:** Already fixed in `ice_agent.cc:266-269`

---

## Testing Checklist

### Before Testing
- [ ] Built latest code: `make clean && make -j$(nproc)`
- [ ] Binary updated: Check timestamp on `bin/drunk-call-service-linux`
- [ ] Python restarted: Kill old service, let Python restart it
- [ ] Logs cleared: Archive old logs to see fresh output

### During Test Call
- [ ] Wait 20+ seconds before hanging up (ICE needs time!)
- [ ] Check stderr for debug logs: `tail -f ~/.siproxylin/logs/drunk-call-service.err`
- [ ] Monitor ICE state changes in C++ logs
- [ ] Watch XMPP protocol log for candidate exchange

### After Test
- [ ] Check component states (should reach READY/COMPLETED)
- [ ] Verify DTLS handshake triggered
- [ ] Confirm media packets flowing (bytes_sent/received > 0)
- [ ] Review any ERROR/WARN log lines

### Success Criteria
- ✅ Component 1 reaches READY state
- ✅ Component 2 reaches READY state (for video/trickle-only)
- ✅ DTLS handshake completes
- ✅ Audio flows (GetStats shows bytes_sent > 0)

---

## Debugging Commands

### Check Service Status
```bash
ps aux | grep drunk-call-service
```

### Monitor Logs in Real-Time
```bash
# C++ service logs
tail -f ~/.siproxylin/logs/drunk-call-service.log | grep -E "ICE|Component|DTLS"

# Python logs
tail -f ~/.siproxylin/logs/account-1-app.log | grep -E "call|candidate"

# XMPP protocol
tail -f ~/.siproxylin/logs/xmpp-protocol.log | grep -E "transport-info|session-"
```

### Enable libnice Debug
```bash
# In bridge.py, add to env before starting service:
env['NICE_DEBUG'] = 'all'
```

### Rebuild C++ Service
```bash
cd drunk_call_service
make clean
make -j$(nproc)
# Binary updated: bin/drunk-call-service-linux
```

### Test with Dino
1. Start call from Siproxylin to Dino (outgoing)
2. Answer on Dino side
3. Wait 20+ seconds
4. Check logs for ICE state progression
5. Hang up, check if media flowed

---

## File Reference Quick List

### rtpbin Implementation Files
```
drunk_call_service/
├── include/
│   ├── session.h              # Main session (rtpbin-based)
│   ├── ice_agent.h            # libnice wrapper (2 components)
│   ├── dtls_srtp_handler.h    # DTLS + SRTP
│   ├── sdp_parser.h           # SDP utilities
│   └── srtp_session.h         # libsrtp2 wrapper
├── src/
│   ├── session.cc             # rtpbin implementation
│   ├── ice_agent.cc           # 528 lines
│   ├── dtls_srtp_handler.cc   # 592 lines
│   ├── sdp_parser.cc          # 394 lines
│   └── srtp_session.cc        # 226 lines
└── src/session.cc.webrtcbin_backup  # Original webrtcbin (for reference)
```

### Dino Reference Files
**Location:** `/home/m/claude/siproxylin/drunk_call_service/tmp/dino`

```
tmp/dino/
├── plugins/ice/src/
│   ├── transport_parameters.vala  # ICE agent creation (lines 98-150)
│   └── dtls_srtp.vala             # DTLS-SRTP handler
├── plugins/rtp/src/
│   └── stream.vala                # rtpbin pipeline
└── crypto-vala/src/
    └── srtp.vala                  # libsrtp2 wrapper
```

---

## Next Session TODO

1. **Verify Audio Flow** (PRIORITY)
   - Make call with Dino, verify bidirectional audio
   - Check GetStats for bytes_sent/bytes_received > 0
   - Monitor RTP packet flow

2. **Long Duration Testing**
   - 5+ minute calls
   - Memory usage monitoring
   - Service stability

3. **Add Video Support**
   - Camera capture pipeline
   - Multiple video codecs
   - Test with Dino video calls

4. **Heartbeat Monitor** (copy from Go service)
   - Prevent orphan processes
   - 10s timeout without heartbeat

5. **Performance Optimization**
   - Reduce logging verbosity for production
   - Profile critical paths

---

**Remember:** When in doubt, copy Dino exactly. Don't invent, just translate Vala → C++.
