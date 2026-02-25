#!/bin/bash
PATH=/usr/local/go/bin:$HOME/go/bin:$PATH

set -e

echo "Building DrunkCallService..."

# Create bin directory
mkdir -p bin

# Generate protobuf code
echo "Generating protobuf code..."
protoc --go_out=. --go_opt=paths=source_relative \
       --go-grpc_out=. --go-grpc_opt=paths=source_relative \
       proto/call.proto

# go clean -cache -modcache -i -r

# Download dependencies and update go.sum
echo "Downloading dependencies..."
go mod tidy
go mod download

# Build for current platform
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
echo "Building for $PLATFORM..."
#go build -mod=mod -o bin/drunk-call-service-$PLATFORM .
go build -o bin/drunk-call-service-$PLATFORM .

echo "Build complete: bin/drunk-call-service-$PLATFORM"
