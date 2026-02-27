# Call Service Status

**Last Updated:** 2026-02-27 22:30
**Current Branch:** `calls-rtpbin`
**Status:** ✅ **WORKING** - Both incoming and outgoing calls with Dino

---

## Current State

### ✅ What Works (2026-02-27)

**Incoming Calls (Dino → Us):**
- Duration tested: 44+ seconds (stable)
- ICE: Both components CONNECTED → READY
- DTLS: Single handshake (CLIENT mode)
- Certificate verification: ✅
- Service stability: No crashes

**Outgoing Calls (Us → Dino):**
- Duration tested: 91+ seconds (stable)
- ICE: Both components CONNECTED → READY
- DTLS: Single handshake (SERVER mode)
- Certificate verification: ✅
- Service stability: No crashes

**Test Sessions:**
- Incoming: `f850362c-3c94-4009-b561-97b7ff497f3a` (44s)
- Outgoing: `71513754-6a1e-4942-953b-218f32419d40` (91s)

### ❌ Known Limitations

**Conversations.im Incoming Calls:**
- Status: Not working (their libwebrtc nomination bug)
- Workaround: Initiate calls FROM desktop TO mobile
- Historical context: See `NOMINATION-DEBUGGING-HISTORY.md`

---

## Recent Fixes (2026-02-27)

### 1. Remote Candidate Queueing ✅ **CRITICAL FIX**

**Problem:** Candidates arriving before ICE stream creation were rejected:
```
"Agent or stream not initialized"
```

**Root Cause:**
- Python calls `AddICECandidate()` from transport-info
- C++ session exists, but `stream_id_ == 0` (no stream yet)
- Stream created later in `CreateAnswer()`
- Candidates were lost → ICE failure

**Solution:** Queue candidates when stream doesn't exist, drain after `AddStream()`

**Files:**
- `drunk_call_service/include/session.h:193-201` - Queue structures
- `drunk_call_service/src/session.cc:607-620` - Queue in AddICECandidate()
- `drunk_call_service/src/session.cc:1217-1242` - DrainRemoteCandidateQueue()
- `drunk_call_service/src/session.cc:363-365` - Drain after AddStream()

### 2. Previous Fixes (Earlier in Session)

- ✅ CreateAnswer() non-blocking (removed `gst_element_get_state()` blocking)
- ✅ Candidate format (strip "a=" prefix for gRPC)
- ✅ Session timing (create on session-initiate, not on user accept)
- ✅ rtcp_mux disabled (2 components for Conversations.im compatibility)

---

## Next Steps

### Immediate Testing Needed

1. **Audio Flow Verification**
   - Can you hear audio from Dino?
   - Can Dino hear audio from you?
   - Check RTP packet flow in GetStats

2. **Long Duration Test**
   - 5+ minute call
   - Verify service stability
   - Check memory usage

3. **Multiple Sequential Calls**
   - Test cleanup between calls
   - Verify no resource leaks

### Future Work

1. **Video Support** (after audio verified)
2. **Heartbeat Monitor** (copy from Go service - prevent orphan processes)
3. **Performance Optimization** (reduce logging verbosity)

---

## Quick Reference

### Build & Test
```bash
cd /home/m/claude/siproxylin/drunk_call_service
make -j$(nproc)
```

### Monitor Logs
```bash
tail -f ~/.siproxylin/logs/drunk-call-service.log | grep -E "Component|DTLS|queue"
```

### Check Latest Session
```bash
SESSION_ID=$(grep "CreateSession:" ~/.siproxylin/logs/drunk-call-service.log | tail -1 | grep -oP 'session_id=\K[^,]+')
grep "$SESSION_ID" ~/.siproxylin/logs/drunk-call-service.log
```

---

## Service Health

**Process:**
- Binary: `/home/m/claude/siproxylin/drunk_call_service/bin/drunk-call-service-linux`
- Started by: Python bridge (`drunk_call_hook/bridge.py`)
- Port: 50051 (gRPC)

**Stability:**
- No crashes since remote candidate queueing fix
- Service survives call failures gracefully
- Clean shutdown on session end

---

## Architecture Notes

**Call Flow:**
1. Jingle session-initiate → CreateSession + CreateAnswer
2. Candidates queued if stream not ready
3. AddStream() creates ICE stream
4. DrainRemoteCandidateQueue() adds queued candidates
5. ICE: GATHERING → CONNECTING → CONNECTED → READY
6. DTLS handshake on CONNECTED (single trigger)
7. SRTP keys derived, ready for media

**Key Patterns:**
- Copy Dino's patterns exactly (no invention)
- Non-blocking GStreamer state changes
- Remote candidate queueing for race conditions
- Single DTLS handshake trigger
