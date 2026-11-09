#!/usr/bin/env bash
# Release tag + rollback script for Regent.
#
# Usage:
#   scripts/release_tag.sh create <version>    # Create a release tag
#   scripts/release_tag.sh rollback            # Rollback to previous tag
#   scripts/release_tag.sh list                # List recent tags
#
# Examples:
#   scripts/release_tag.sh create v1.2.3
#   scripts/release_tag.sh rollback

set -euo pipefail

ACTION="${1:-help}"
VERSION="${2:-}"

create_tag() {
    if [ -z "$VERSION" ]; then
        echo "ERROR: version required (e.g., v1.2.3)"
        exit 1
    fi

    # Ensure we're on main/master
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    if [ "$BRANCH" != "main" ] && [ "$BRANCH" != "master" ]; then
        echo "ERROR: must be on main/master branch (currently on $BRANCH)"
        exit 1
    fi

    # Ensure working tree is clean
    if [ -n "$(git status --porcelain)" ]; then
        echo "ERROR: working tree is not clean"
        exit 1
    fi

    # Check if tag already exists
    if git rev-parse "$VERSION" >/dev/null 2>&1; then
        echo "ERROR: tag $VERSION already exists"
        exit 1
    fi

    # Create annotated tag
    git tag -a "$VERSION" -m "Release $VERSION"
    echo "Created tag: $VERSION"
    echo "Push with: git push origin $VERSION"
}

rollback() {
    # Get current and previous tags
    CURRENT=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    if [ -z "$CURRENT" ]; then
        echo "ERROR: no tags found"
        exit 1
    fi

    PREVIOUS=$(git describe --tags --abbrev=0 "$CURRENT^" 2>/dev/null || echo "")
    if [ -z "$PREVIOUS" ]; then
        echo "ERROR: no previous tag to rollback to"
        exit 1
    fi

    echo "Current: $CURRENT"
    echo "Rolling back to: $PREVIOUS"
    echo ""
    echo "To complete rollback:"
    echo "  git checkout $PREVIOUS"
    echo "  # Deploy the checked-out version"
    echo "  # Or: git reset --hard $PREVIOUS (destructive!)"
}

list_tags() {
    echo "Recent release tags:"
    git tag -l --sort=-creatordate | head -10
}

case "$ACTION" in
    create)
        create_tag
        ;;
    rollback)
        rollback
        ;;
    list)
        list_tags
        ;;
    help|*)
        echo "Usage: $0 {create <version>|rollback|list}"
        echo ""
        echo "Commands:"
        echo "  create <version>  Create an annotated release tag"
        echo "  rollback          Show rollback instructions"
        echo "  list              List recent release tags"
        ;;
esac
