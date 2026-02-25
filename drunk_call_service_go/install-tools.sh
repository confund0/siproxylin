#!/bin/bash
PATH=/usr/local/go/bin:$PATH

echo "Installing Go protobuf tools..."

# Install protoc-gen-go (protobuf code generator)
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest

# Install protoc-gen-go-grpc (gRPC code generator)
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

echo "Done! Tools installed to ~/go/bin/"
echo "Make sure ~/go/bin is in your PATH"
