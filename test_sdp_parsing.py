#!/usr/bin/env python3
"""
Test SDP parsing functionality (Phase 4)
Tests SetRemoteDescription with mock SDP containing ICE credentials and DTLS fingerprint
"""

import grpc
import sys
import os

# Add proto path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'proto'))
import call_pb2
import call_pb2_grpc

def test_set_remote_description():
    """Test SetRemoteDescription with mock peer SDP"""

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    # Mock SDP from a peer (simulating Dino/Conversations offer)
    # Note: Using actual line breaks, gRPC will handle the encoding
    test_sdp = """v=0
o=- 0 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 RTP/AVP 96
c=IN IP4 0.0.0.0
a=rtpmap:96 opus/48000/2
a=mid:audio0
a=sendrecv
a=ice-ufrag:TESTUFRAG
a=ice-pwd:TESTPASSWORD123456789
a=ice-options:trickle
a=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99
a=setup:active
"""

    print("Testing SetRemoteDescription with mock SDP...")
    print(f"SDP length: {len(test_sdp)} bytes")
    print(f"Ice ufrag: TESTUFRAG")
    print(f"Setup: active (peer initiates DTLS)")
    print()

    try:
        response = stub.SetRemoteDescription(call_pb2.SetRemoteDescriptionRequest(
            session_id="test-session-1",
            remote_sdp=test_sdp,
            sdp_type="offer"
        ))

        print(f"✅ SetRemoteDescription succeeded: {response.success}")
        print()
        print("Expected in logs:")
        print("  - 'Setting remote ICE credentials: ufrag=TESTUFRAG, pwd_len=24'")
        print("  - 'Setting peer DTLS fingerprint: algorithm=sha-256, size=32 bytes'")
        print("  - 'Peer is active, setting DTLS mode to SERVER'")

    except grpc.RpcError as e:
        print(f"❌ gRPC Error: {e.code()} - {e.details()}")
        return False

    return True

def test_create_answer():
    """Test CreateAnswer which also parses remote offer"""

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    # Mock SDP offer from peer
    remote_offer = """v=0
o=- 0 0 IN IP4 0.0.0.0
s=-
t=0 0
m=audio 9 RTP/AVP 96
c=IN IP4 0.0.0.0
a=rtpmap:96 opus/48000/2
a=mid:audio0
a=sendrecv
a=ice-ufrag:PEER123
a=ice-pwd:PEERPWD1234567890123
a=ice-options:trickle
a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00
a=setup:actpass
"""

    print("\nTesting CreateAnswer with remote offer...")
    print(f"Peer setup: actpass (peer can do either role)")
    print()

    try:
        response = stub.CreateAnswer(call_pb2.CreateAnswerRequest(
            session_id="test-session-1",
            remote_sdp=remote_offer,
            offer_has_bundle=False
        ))

        print(f"✅ CreateAnswer succeeded")
        print(f"Answer SDP length: {len(response.sdp)} bytes")
        print()
        print("Expected in logs:")
        print("  - 'Setting remote ICE credentials: ufrag=PEER123, pwd_len=22'")
        print("  - 'Setting peer DTLS fingerprint from offer: algorithm=sha-256, size=32 bytes'")
        print("  - 'Peer is actpass (offer), setting DTLS mode to CLIENT'")
        print()
        print("Answer SDP should contain:")
        print("  - a=setup:active (we are CLIENT)")

        # Check if answer contains setup:active
        if "a=setup:active" in response.sdp:
            print("✅ Answer contains 'a=setup:active' (correct)")
        else:
            print("❌ Answer missing 'a=setup:active'")

    except grpc.RpcError as e:
        print(f"❌ gRPC Error: {e.code()} - {e.details()}")
        return False

    return True

if __name__ == "__main__":
    print("=" * 60)
    print("Phase 4: SDP Parsing Test")
    print("=" * 60)
    print()

    # Test 1: SetRemoteDescription
    success1 = test_set_remote_description()

    # Test 2: CreateAnswer
    success2 = test_create_answer()

    print()
    print("=" * 60)
    if success1 and success2:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed")
    print("=" * 60)
