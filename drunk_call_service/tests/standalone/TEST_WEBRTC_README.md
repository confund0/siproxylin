# WebRTC Standalone Test

## Purpose

These tests verify that the WebRTC C++ code works correctly **without any gRPC/Python/Jingle complexity**.

They use:
- ✅ Real microphones (default system mic via autoaudiosrc)
- ✅ Direct SDP/ICE exchange over stdin/stdout
- ✅ Localhost connection only (no STUN/TURN)
- ✅ Simple text protocol (pipeable through netcat)
- ✅ Stats verification (bandwidth > 0 kbps = success)

**What we're testing**: Does the WebRTC C++ code create the audio sending pipeline correctly?

**Critical check**: Does the answer SDP contain `a=sendrecv` or `a=recvonly`?

## Build

```bash
cd drunk_call_service/tests/standalone
make test_webrtc_caller test_webrtc_answerer
```

## Usage

### Method 1: Named Pipes (Same Machine)

```bash
# Create named pipes
mkfifo /tmp/pipe1 /tmp/pipe2

# Terminal 1: Start answerer
./test_webrtc_answerer < /tmp/pipe1 > /tmp/pipe2

# Terminal 2: Start caller
./test_webrtc_caller < /tmp/pipe2 > /tmp/pipe1
```

### Method 2: File Exchange (Manual, for debugging)

```bash
# Step 1: Run caller, capture offer
./test_webrtc_caller > caller_out.txt 2>&1 &
CALLER_PID=$!

# Wait for offer to be created, then kill caller temporarily
sleep 2
kill -TERM $CALLER_PID

# Step 2: Run answerer with offer, capture answer
./test_webrtc_answerer < caller_out.txt > answerer_out.txt 2>&1 &
ANSWERER_PID=$!

# Step 3: Feed answer back to caller
cat answerer_out.txt | ./test_webrtc_caller 2>&1
```

### Method 3: Netcat (Different Machines or Clarity)

```bash
# Machine 1 (answerer):
nc -l 5555 | ./test_webrtc_answerer | nc -l 5556

# Machine 2 (caller):
nc machine1 5555 | ./test_webrtc_caller | nc machine1 5556
```

## Expected Output

### Caller (test_webrtc_caller)

```
=== WebRTC Caller Test Starting ===
Creating WebRTC session (outgoing mode)...
Initializing session...
Starting session (pipeline → PLAYING)...
Creating SDP offer...
✅ Offer created (XXX bytes)
✅ Offer contains a=sendrecv (audio sending enabled)

OFFER
v=0
o=- ...
[SDP content]
END_OFFER

ICE 0 candidate:...
ICE 0 candidate:...
ICE_DONE

Received ANSWER marker
Answer SDP received (XXX bytes)
Remote description set
Waiting for remote ICE candidates...
Adding remote ICE candidate: mline=0 candidate=...
✅ ICE CONNECTED! Starting stats monitor...

Stats[0]: bytes_sent=XXXXX, bandwidth=XX.XX kbps, ice_state=connected
Stats[1]: bytes_sent=XXXXX, bandwidth=XX.XX kbps, ice_state=connected
...

=== TEST SUMMARY ===
Offer created: YES
Answer received: YES
ICE connected: YES
Final bytes_sent: XXXXX
Final bandwidth: XX.XX kbps
SUCCESS: YES ✅
```

### Answerer (test_webrtc_answerer)

```
=== WebRTC Answerer Test Starting ===
Creating WebRTC session (incoming mode)...
Waiting for OFFER from stdin...
Received OFFER marker
Offer SDP received (XXX bytes)
Creating SDP answer...
✅ Answer created (XXX bytes)
✅ Answer contains a=sendrecv (audio sending enabled)

ANSWER
v=0
o=- ...
[SDP content]
END_ANSWER

ICE 0 candidate:...
ICE 0 candidate:...
ICE_DONE

✅ ICE CONNECTED! Starting stats monitor...

Stats[0]: bytes_sent=XXXXX, bandwidth=XX.XX kbps, ice_state=connected
Stats[1]: bytes_sent=XXXXX, bandwidth=XX.XX kbps, ice_state=connected
...

=== TEST SUMMARY ===
Offer received: YES
Answer created: YES
ICE connected: YES
Final bytes_sent: XXXXX
Final bandwidth: XX.XX kbps
SUCCESS: YES ✅
```

## Success Criteria

### ✅ SUCCESS (bandwidth > 0 kbps on BOTH sides)

**Interpretation**: WebRTC C++ code works correctly. If calls still fail in the app, look at the gRPC, Python or Jingle layers.

### ❌ FAILURE (bandwidth = 0 kbps)

**Possible issues**:
1. **Answer has `a=recvonly` instead of `a=sendrecv`**
   - → Audio source pipeline not created
   - → Bug in `create_audio_source_pipeline()` logic
   - → Check `is_outgoing_` flag handling in webrtc_session.cpp

2. **ICE doesn't connect**
   - → Check firewall/network
   - → Try on same machine with loopback

3. **Pipeline errors**
   - → Check GStreamer logs in test_caller.log and test_answerer.log
   - → Look for pipeline state change failures

## Troubleshooting

### Check logs
```bash
tail -f test_caller.log
tail -f test_answerer.log
```

### Enable GStreamer debug
```bash
GST_DEBUG=3 ./test_webrtc_caller ...
GST_DEBUG=webrtcbin:7 ./test_webrtc_answerer ...
```

### Check if microphone is available
```bash
pactl list sources | grep -A 10 "Name:"
```

### Test with audiotestsrc (if mic issues)
Edit the test files and change:
```cpp
config.microphone_device = "";  // autoaudiosrc (default mic)
```
to:
```cpp
// In webrtc_session.cpp create_audio_source_pipeline():
// Replace: audio_src_ = gst_element_factory_make("autoaudiosrc", "audio_src");
// With:    audio_src_ = gst_element_factory_make("audiotestsrc", "audio_src");
```

## Protocol Format

Simple line-delimited text:

```
OFFER
v=0
...
END_OFFER

ANSWER
v=0
...
END_ANSWER

ICE <mline_index> <candidate_string>
ICE <mline_index> <candidate_string>
...
ICE_DONE

STATS: bytes_sent=XXXX bandwidth=XX.XX kbps ice=connected
```

## Notes

- Test duration: 30 seconds (15 stats samples at 2-second intervals)
- Logs written to: `test_caller.log` and `test_answerer.log`
- Both tests use default system microphone (empty device string)
- No echo cancellation/noise suppression (disabled for testing)
- ICE uses host candidates only (no STUN/TURN servers)

## What This Tests

✅ WebRTCSession C++ implementation
✅ SDP offer/answer creation
✅ Audio pipeline creation (autoaudiosrc → opusenc → webrtcbin)
✅ ICE connectivity (localhost)
✅ Real microphone integration
✅ Stats collection

❌ NOT tested (known to work):
- gRPC integration (bypassed)
- Python bridge (bypassed)
- Jingle translation (bypassed)
- STUN/TURN servers
- Multiple simultaneous sessions

---

**Last Updated**: 2026-03-05
