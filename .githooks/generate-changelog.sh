#!/bin/bash
# Generate CHANGELOG.md from git tags and commit messages
# Format: Keep-a-Changelog style with full commit details

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Find repo root (where .git directory is)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
    echo -e "${RED}Error: Not in a git repository${NC}" >&2
    exit 1
fi

CHANGELOG_FILE="$REPO_ROOT/CHANGELOG.md"
VERSION_FILE="$REPO_ROOT/version.sh"

# Check if version.sh exists
if [ ! -f "$VERSION_FILE" ]; then
    echo -e "${RED}Error: version.sh not found at $VERSION_FILE${NC}" >&2
    exit 1
fi

echo -e "${GREEN}Generating CHANGELOG.md from git history...${NC}"

# Get all tags sorted by date (newest first)
tags=($(git tag --sort=-creatordate))

if [ ${#tags[@]} -eq 0 ]; then
    echo -e "${YELLOW}Warning: No version tags found. CHANGELOG will be empty.${NC}"
fi

# Function to extract codename from version.sh at a specific commit
get_codename_at_tag() {
    local tag=$1
    git show "$tag:version.sh" 2>/dev/null | grep -oP 'SIPROXYLIN_CODENAME="\K[^"]+' || echo ""
}

# Function to get date of tag
get_tag_date() {
    local tag=$1
    git log -1 --format=%ai "$tag" | cut -d' ' -f1
}

# Read current version from version.sh
source "$VERSION_FILE"
CURRENT_VERSION="${SIPROXYLIN_VERSION#v}"
CURRENT_CODENAME="$SIPROXYLIN_CODENAME"

# Start writing the changelog
cat > "$CHANGELOG_FILE" << 'EOF'
# Changelog

All notable changes to Siproxylin are documented in this file.

---

EOF

# Check if version.sh > latest_tag and there are unreleased commits
if [ ${#tags[@]} -gt 0 ]; then
    LATEST_TAG="${tags[0]}"
    LATEST_VERSION="${LATEST_TAG#v}"
    UNRELEASED=$(git log "$LATEST_TAG..HEAD" --oneline 2>/dev/null)

    # Compare versions: if current > latest AND there are unreleased commits
    if [ -n "$UNRELEASED" ] && [ "$(printf '%s\n' "$CURRENT_VERSION" "$LATEST_VERSION" | sort -V | tail -1)" = "$CURRENT_VERSION" ] && [ "$CURRENT_VERSION" != "$LATEST_VERSION" ]; then
        echo -e "${GREEN}Including unreleased commits as v$CURRENT_VERSION...${NC}"

        # Build version header for new version
        CURRENT_DATE=$(date +%Y-%m-%d)
        if [ -n "$CURRENT_CODENAME" ]; then
            VERSION_HEADER="[$CURRENT_VERSION - $CURRENT_CODENAME]"
        else
            VERSION_HEADER="[$CURRENT_VERSION]"
        fi

        # Write new version section
        echo "## $VERSION_HEADER - $CURRENT_DATE" >> "$CHANGELOG_FILE"
        echo "" >> "$CHANGELOG_FILE"
        # git log "$LATEST_TAG..HEAD" | egrep '(^$|^  |^commit )' | sed "s/^commit .*/>>/g" >> "$CHANGELOG_FILE"
        git log "$LATEST_TAG..HEAD" | egrep '(^$|^  |^commit )' | awk '{ if($1 ~ /^commit/) { print "> (" substr($2, 1, 10) ")" } else { print } }' >> "$CHANGELOG_FILE"
        echo "" >> "$CHANGELOG_FILE"
    fi
fi

# Loop through tags to generate changelog (newest first)
for ((i=0; i<${#tags[@]}; i++)); do
    TAG="${tags[$i]}"
    VERSION="${TAG#v}" # Strip 'v' prefix
    CODENAME=$(get_codename_at_tag "$TAG")
    TAG_DATE=$(get_tag_date "$TAG")

    # Build version header
    if [ -n "$CODENAME" ]; then
        VERSION_HEADER="[$VERSION - $CODENAME]"
    else
        VERSION_HEADER="[$VERSION]"
    fi

    echo -e "${GREEN}Processing $TAG ($TAG_DATE)...${NC}"

    # Write version header
    echo "## $VERSION_HEADER - $TAG_DATE" >> "$CHANGELOG_FILE"
    echo "" >> "$CHANGELOG_FILE"

    # Get commits for this version
    if [ $i -eq $((${#tags[@]} - 1)) ]; then
        # Last (oldest) tag - get all commits up to this tag
        git log "$TAG" | egrep '(^$|^  |^commit )' | awk '{ if($1 ~ /^commit/) { print "> (" substr($2, 1, 10) ")" } else { print } }' >> "$CHANGELOG_FILE"
    else
        # Get commits between previous (older) tag and current tag
        PREV_TAG="${tags[$((i + 1))]}"
        git log "$PREV_TAG..$TAG" | egrep '(^$|^  |^commit )' | awk '{ if($1 ~ /^commit/) { print "> (" substr($2, 1, 10) ")" } else { print } }' >> "$CHANGELOG_FILE"
    fi

    echo "" >> "$CHANGELOG_FILE"
done

echo -e "${GREEN}✓ CHANGELOG.md generated successfully at $CHANGELOG_FILE${NC}"
exit 0
