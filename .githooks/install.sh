#!/bin/bash
# Install git hooks for version validation

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_HOOKS_DIR="$SCRIPT_DIR/../.git/hooks"

# Create symlinks
ln -sf ../../.githooks/pre-commit "$GIT_HOOKS_DIR/pre-commit"
ln -sf ../../.githooks/pre-push "$GIT_HOOKS_DIR/pre-push"

# Make hooks executable
chmod +x "$SCRIPT_DIR/pre-commit"
chmod +x "$SCRIPT_DIR/pre-push"

echo "✓ Git hooks installed:"
echo "  - pre-commit: validates version increases and codename changes"
echo "  - pre-push: validates tags match version.sh"
