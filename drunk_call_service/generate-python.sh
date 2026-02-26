#!/bin/bash
set -e

echo "Generating Python gRPC code..."

# Use venv Python if available
if [ -f "../venv/bin/python" ]; then
    PYTHON="../venv/bin/python"
    echo "Using venv Python: $PYTHON"
else
    PYTHON="python3"
    echo "Using system Python: $PYTHON"
fi

# Output directory for Python code
OUTPUT_DIR="../drunk_call_hook/proto"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Generate Python protobuf + gRPC code
# We need to use proper proto path structure
$PYTHON -m grpc_tools.protoc \
    -I./proto \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    call.proto

# Create __init__.py to make it a package
touch "$OUTPUT_DIR/__init__.py"

# Fix the import in call_pb2_grpc.py (protoc generates wrong import)
# Change "import call_pb2" to "from . import call_pb2"
sed -i 's/^import call_pb2/from . import call_pb2/' "$OUTPUT_DIR/call_pb2_grpc.py"

echo "✅ Generated Python code in $OUTPUT_DIR"
echo "   - call_pb2.py (protobuf messages)"
echo "   - call_pb2_grpc.py (gRPC client/server stubs)"
echo "   - Fixed relative import in call_pb2_grpc.py"
