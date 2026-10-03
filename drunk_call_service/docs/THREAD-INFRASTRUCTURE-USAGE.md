# Thread Infrastructure Usage Reference

**Quick Reference**: How to use ThreadSafeQueue and SessionManager in the gRPC service

**Test Examples**: See `tests/standalone/test_step8_thread_safe_queue.cpp` and `test_step9_session_manager.cpp`

---

## ThreadSafeQueue<T>

**Purpose**: Cross-thread event streaming from GLib thread → gRPC StreamEvents thread

**Header**: `#include "thread_safe_queue.h"`

### Basic Usage

```cpp
// Create queue for events
ThreadSafeQueue<CallEvent> event_queue;

// Producer thread (GLib callbacks push events)
void on_ice_candidate(const char* candidate) {
    CallEvent event;
    event.set_type(CallEvent::ICE_CANDIDATE);
    event.set_candidate(candidate);
    event_queue.push(event);  // Thread-safe, wakes consumer
}

// Consumer thread (gRPC StreamEvents RPC)
grpc::Status StreamEvents(..., grpc::ServerWriter<CallEvent>* writer) {
    while (session->active) {
        CallEvent event;

        // Block with 1s timeout (allows cancellation checks)
        if (event_queue.pop(event, std::chrono::milliseconds(1000))) {
            writer->Write(event);  // Send to Python
        }

        // Check if client disconnected
        if (context->IsCancelled()) break;
    }
    return grpc::Status::OK;
}

// Cleanup (when session ends)
event_queue.shutdown();  // Wakes all blocked threads
```

### Key Methods

| Method | Description |
|--------|-------------|
| `push(item)` | Push item to queue, notify waiting thread |
| `pop(item, timeout)` | Pop item with timeout, returns `false` on timeout/shutdown |
| `shutdown()` | Mark queue as shutdown, wake all waiting threads |
| `is_shutdown()` | Check if queue is shutdown |

### Important Notes

- **Push after shutdown**: No-op, events discarded silently
- **Pop timeout**: Use 1000ms default for cancellation checks in streaming loops
- **Thread-safe**: All methods can be called from any thread

---

## SessionManager

**Purpose**: Thread-safe session map for gRPC handlers + GLib callbacks

**Headers**:
```cpp
#include "session_manager.h"
```

### Basic Usage

```cpp
// Global session manager (one per service)
SessionManager session_manager;

// Creating a session (CreateSession RPC)
grpc::Status CreateSession(const CreateSessionRequest* request, ...) {
    // Create session structure
    auto session = std::make_shared<CallSession>();
    session->session_id = request->session_id();
    session->peer_jid = request->peer_jid();
    session->active = true;

    // Create WebRTC session
    session->webrtc = std::make_unique<WebRTCSession>();
    session->webrtc->initialize(config);

    // Create event queue
    session->event_queue = std::make_shared<ThreadSafeQueue<CallEvent>>();

    // Set callbacks (will fire in GLib thread, push to queue)
    session->webrtc->set_ice_candidate_callback([session](const ICECandidate& cand) {
        CallEvent event;
        event.set_type(CallEvent::ICE_CANDIDATE);
        event.set_candidate(cand.candidate);
        session->event_queue->push(event);
    });

    // Add to manager
    session_manager.add_session(session->session_id, session);

    return grpc::Status::OK;
}

// Accessing a session (any RPC)
grpc::Status CreateOffer(const CreateOfferRequest* request, ...) {
    auto session = session_manager.get_session(request->session_id());
    if (!session) {
        return grpc::Status(grpc::NOT_FOUND, "Session not found");
    }

    // Use session (safe - shared_ptr keeps it alive)
    session->webrtc->create_offer(callback);

    return grpc::Status::OK;
}

// Ending a session (EndSession RPC)
grpc::Status EndSession(const EndSessionRequest* request, ...) {
    auto session = session_manager.get_session(request->session_id());
    if (session) {
        // Cleanup
        session->active = false;
        session->event_queue->shutdown();
        session->webrtc->stop();

        // Remove from map (other threads may still hold shared_ptr)
        session_manager.remove_session(request->session_id());
    }

    return grpc::Status::OK;
}

// Cleanup all sessions (service shutdown)
void cleanup_all_sessions() {
    auto session_ids = session_manager.get_all_session_ids();
    for (const auto& id : session_ids) {
        auto session = session_manager.get_session(id);
        if (session) {
            session->active = false;
            session->event_queue->shutdown();
            session->webrtc->stop();
        }
        session_manager.remove_session(id);
    }
}
```

### CallSession Structure

```cpp
struct CallSession {
    std::string session_id;                          // Jingle session ID
    std::string peer_jid;                            // Remote peer JID
    std::unique_ptr<WebRTCSession> webrtc;          // WebRTC session (library)
    std::shared_ptr<ThreadSafeQueue<CallEvent>> event_queue;  // Event streaming
    std::atomic<bool> active;                        // Session active flag
    std::chrono::steady_clock::time_point created_at;
};
```

### Key Methods

| Method | Description |
|--------|-------------|
| `get_session(id)` | Get session by ID, returns `shared_ptr` (nullptr if not found) |
| `add_session(id, session)` | Add session to map |
| `remove_session(id)` | Remove session from map |
| `get_all_session_ids()` | Get all session IDs (for cleanup) |

### Important Notes

- **Thread-safe**: All methods use mutex protection
- **Shared pointer safety**: Removing from map doesn't destroy session if other threads hold `shared_ptr`
- **Critical pattern**: StreamEvents RPC can hold session while EndSession removes it from map

---

## Threading Model

```
┌─────────────────────────┐
│  Main Thread            │
│  - Starts GLib thread   │
│  - Starts gRPC server   │
└─────────────────────────┘
         ↓ spawns
┌─────────────────────────┐         ┌─────────────────────────┐
│  GLib Main Loop Thread  │         │  gRPC Thread Pool       │
│  (dedicated)            │         │  (multiple threads)     │
├─────────────────────────┤         ├─────────────────────────┤
│ - g_main_loop_run()     │         │ - CreateSession         │
│ - GStreamer callbacks   │         │ - CreateOffer           │
│ - ICE candidates        │         │ - StreamEvents          │
│ - State changes         │         │ - EndSession            │
│                         │         │ - All RPCs              │
└────────┬────────────────┘         └────────┬────────────────┘
         │                                   │
         │ pushes events                     │ pops events
         └──────────► ThreadSafeQueue ◄──────┘
                           │
                           │ stores sessions
                           ▼
                     SessionManager
                  (thread-safe map)
```

### Key Rules

1. **GLib thread**: Processes GStreamer signals, pushes events to queue
2. **gRPC threads**: Handle RPCs, pop events from queue
3. **SessionManager**: Shared by all threads, mutex-protected
4. **Shared pointers**: Keep sessions alive across thread boundaries

---

## Common Patterns

### Pattern 1: Async SDP Operation (CreateOffer)

```cpp
grpc::Status CreateOffer(...) {
    auto session = session_manager.get_session(session_id);

    // Synchronization primitives
    std::mutex sdp_mutex;
    std::condition_variable sdp_cv;
    std::string sdp_result;
    bool sdp_done = false;

    // Set callback (fires in GLib thread)
    session->webrtc->set_sdp_callback([&](bool success, const SDPMessage& sdp) {
        std::lock_guard<std::mutex> lock(sdp_mutex);
        sdp_result = sdp.sdp;
        sdp_done = true;
        sdp_cv.notify_one();
    });

    // Trigger operation
    session->webrtc->create_offer();

    // Wait for callback (max 10s)
    std::unique_lock<std::mutex> lock(sdp_mutex);
    if (!sdp_cv.wait_for(lock, std::chrono::seconds(10), [&] { return sdp_done; })) {
        return grpc::Status(grpc::DEADLINE_EXCEEDED, "Timeout");
    }

    response->set_sdp(sdp_result);
    return grpc::Status::OK;
}
```

### Pattern 2: Event Streaming

```cpp
grpc::Status StreamEvents(..., grpc::ServerWriter<CallEvent>* writer) {
    auto session = session_manager.get_session(session_id);

    while (session->active && !context->IsCancelled()) {
        CallEvent event;

        if (session->event_queue->pop(event, std::chrono::milliseconds(1000))) {
            writer->Write(event);
        }
    }

    return grpc::Status::OK;
}
```

### Pattern 3: Session Cleanup

```cpp
// EndSession RPC removes from map
session_manager.remove_session(session_id);

// But StreamEvents may still hold shared_ptr - that's OK!
// Session destroyed only when last shared_ptr destroyed
```

---

## Testing

Run comprehensive thread-safety tests:

```bash
cd drunk_call_service/tests/standalone
make test_step8_thread_safe_queue test_step9_session_manager
./test_step8_thread_safe_queue  # 6 tests: push/pop, timeout, shutdown, concurrency
./test_step9_session_manager    # 7 tests: CRUD, ref-counting, stress (1000 ops, 10 threads)
```

**Critical test**: `test_step9_session_manager` Test 6 validates the "remove while in use" pattern - 5 threads hold 50 shared_ptrs while map is cleared.

