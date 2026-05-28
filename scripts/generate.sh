#!/usr/bin/env bash
set -euo pipefail

# Generate test DCPs using dcpwizard
# Requires: dcpwizard, ffmpeg, grk_compress

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST="$REPO_DIR/tests/generated"
DCPWIZARD="${DCPWIZARD:-$(command -v dcpwizard 2>/dev/null || echo "$HOME/src/dcpwizard/rust/target/release/dcpwizard")}"
SOURCE_VIDEO="${SOURCE_VIDEO:-$HOME/dom_distribution/sintel-2048-surround.mp4}"

if [[ ! -x "$DCPWIZARD" ]]; then
    echo "ERROR: dcpwizard not found at $DCPWIZARD"
    exit 1
fi

if [[ ! -f "$SOURCE_VIDEO" ]]; then
    echo "ERROR: Source video not found: $SOURCE_VIDEO"
    echo "Set SOURCE_VIDEO env var to a test video"
    exit 1
fi

mkdir -p "$DEST"

echo "Generating test DCPs from: $SOURCE_VIDEO"
echo "Using dcpwizard: $DCPWIZARD"
echo ""

# Generate a short clip (first 2 seconds = 48 frames at 24fps)
SHORT_VIDEO="/tmp/dci-ctp-short.mp4"
if [[ ! -f "$SHORT_VIDEO" ]]; then
    echo "Creating 2-second test clip..."
    ffmpeg -y -ss 0 -t 2 -i "$SOURCE_VIDEO" -c copy "$SHORT_VIDEO" 2>/dev/null
fi

# Test 1: Standard 2K DCP
echo "Generating: standard_2k_24fps..."
$DCPWIZARD create --title "CTP-Test-2K-24" --video "$SHORT_VIDEO" \
    --output "$DEST/standard_2k_24fps" -v 2>&1 | tail -5 || true

echo ""
echo "Done. Generated DCPs at: $DEST"
ls "$DEST" 2>/dev/null
