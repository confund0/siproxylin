#!/usr/bin/env python3
"""
Test script for Phase 2 rtpbin migration
Tests basic functionality without actual media flow
"""

import grpc
import sys
import os

# Add the proto directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'drunk_call_service/proto'))

import call_pb2
import call_pb2_grpc

def test_session_creation():
    """Test 1: Can we create a session with rtpbin + IceAgent?"""
    print("\n=== Test 1: Session Creation ===")

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    try:
        # Create session
        request = call_pb2.CreateSessionRequest(
            session_id="test-phase2-001",
            peer_jid="peer@example.com",
            relay_only=False,  # Allow P2P for testing
            echo_cancel=False,
            noise_suppression=False,
            gain_control=False
        )

        response = stub.CreateSession(request)
        print(f"✅ Session created: {response.session_id}")
        print(f"   Status: {response.status}")
        return response.session_id

    except grpc.RpcError as e:
        print(f"❌ Failed to create session: {e.code()} - {e.details()}")
        return None

def test_offer_generation(session_id):
    """Test 2: Can we generate a stub SDP offer?"""
    print("\n=== Test 2: Offer Generation ===")

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    try:
        request = call_pb2.CreateOfferRequest(session_id=session_id)
        response = stub.CreateOffer(request)

        print(f"✅ Offer created ({len(response.sdp)} bytes)")
        print(f"   SDP Preview:")
        for line in response.sdp.split('\r\n')[:10]:  # Show first 10 lines
            print(f"     {line}")

        # Check for ICE credentials in SDP
        if 'a=ice-ufrag:' in response.sdp and 'a=ice-pwd:' in response.sdp:
            print("✅ ICE credentials present in SDP")
        else:
            print("⚠️  ICE credentials missing from SDP")

        return True

    except grpc.RpcError as e:
        print(f"❌ Failed to create offer: {e.code()} - {e.details()}")
        return False

def test_ice_candidates(session_id):
    """Test 3: Can we receive ICE candidates?"""
    print("\n=== Test 3: ICE Candidate Gathering ===")

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    try:
        request = call_pb2.GetEventsRequest(
            session_id=session_id,
            timeout_ms=5000  # Wait up to 5 seconds for candidates
        )

        candidate_count = 0
        gathering_complete = False

        print("Waiting for ICE candidates...")
        for event in stub.GetEvents(request):
            if event.event_type == "ICE_CANDIDATE":
                candidate_count += 1
                print(f"✅ Candidate {candidate_count}: {event.candidate[:50]}...")

            elif event.event_type == "ICE_GATHERING_STATE_CHANGE":
                print(f"   Gathering state: {event.data}")
                if event.data == "complete":
                    gathering_complete = True
                    break

            # Stop after 10 candidates to avoid flooding
            if candidate_count >= 10:
                print("   (stopping after 10 candidates)")
                break

        if candidate_count > 0:
            print(f"✅ Received {candidate_count} ICE candidates")
        else:
            print("⚠️  No ICE candidates received (check STUN/TURN config)")

        return candidate_count > 0

    except grpc.RpcError as e:
        print(f"❌ Failed to get events: {e.code()} - {e.details()}")
        return False

def test_stats(session_id):
    """Test 4: Can we query stats from IceAgent?"""
    print("\n=== Test 4: Stats Query ===")

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    try:
        request = call_pb2.GetStatsRequest(session_id=session_id)
        response = stub.GetStats(request)

        print(f"✅ Stats retrieved:")
        print(f"   Connection state: {response.connection_state}")
        print(f"   ICE state: {response.ice_connection_state}")
        print(f"   Gathering state: {response.ice_gathering_state}")

        return True

    except grpc.RpcError as e:
        print(f"❌ Failed to get stats: {e.code()} - {e.details()}")
        return False

def test_cleanup(session_id):
    """Test 5: Can we close the session cleanly?"""
    print("\n=== Test 5: Session Cleanup ===")

    channel = grpc.insecure_channel('localhost:50051')
    stub = call_pb2_grpc.CallServiceStub(channel)

    try:
        request = call_pb2.CloseSessionRequest(session_id=session_id)
        response = stub.CloseSession(request)

        print(f"✅ Session closed successfully")
        return True

    except grpc.RpcError as e:
        print(f"❌ Failed to close session: {e.code()} - {e.details()}")
        return False

def main():
    print("=" * 60)
    print("Phase 2 Migration Test Suite")
    print("Testing rtpbin + IceAgent integration")
    print("=" * 60)

    # Run tests
    session_id = test_session_creation()
    if not session_id:
        print("\n❌ Cannot continue without session")
        return 1

    test_offer_generation(session_id)
    test_ice_candidates(session_id)
    test_stats(session_id)
    test_cleanup(session_id)

    print("\n" + "=" * 60)
    print("✅ Phase 2 Migration Tests Complete!")
    print("=" * 60)
    print("\nNOTE: These are basic connectivity tests.")
    print("Media flow requires Phase 3 (DTLS-SRTP) to be implemented.")

    return 0

if __name__ == '__main__':
    sys.exit(main())
