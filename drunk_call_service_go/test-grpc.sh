#!/bin/bash
PATH=/usr/local/go/bin:$HOME/go/bin:$PATH

# Install grpcurl if not present
if ! command -v grpcurl &> /dev/null; then
    echo "Installing grpcurl..."
    go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest
fi

echo "Testing DrunkCallService gRPC..."
echo ""

# List available services
echo "=== Available Services ==="
grpcurl -plaintext localhost:50051 list
echo ""

# List methods in CallService
echo "=== CallService Methods ==="
grpcurl -plaintext localhost:50051 list call.CallService
echo ""

# Test CreateSession
echo "=== Test CreateSession ==="
grpcurl -plaintext -d '{"session_id": "test-123", "peer_jid": "user@example.com"}' \
    localhost:50051 call.CallService/CreateSession
echo ""

# Test CreateOffer
echo "=== Test CreateOffer ==="
grpcurl -plaintext -d '{"session_id": "test-123"}' \
    localhost:50051 call.CallService/CreateOffer
echo ""

# Test EndSession
echo "=== Test EndSession ==="
grpcurl -plaintext -d '{"session_id": "test-123"}' \
    localhost:50051 call.CallService/EndSession
echo ""

echo "✅ Tests complete!"
