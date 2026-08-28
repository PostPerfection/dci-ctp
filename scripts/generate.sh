#!/usr/bin/env bash
set -euo pipefail

# Generate test DCPs using dcpwizard
# Requires: dcpwizard, ffmpeg

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST="$REPO_DIR/tests/generated"
DCPWIZARD="${DCPWIZARD:-$(command -v dcpwizard 2>/dev/null || true)}"
SOURCE_VIDEO="${SOURCE_VIDEO:-}"

if [[ ! -x "$DCPWIZARD" ]]; then
    echo "ERROR: dcpwizard not found (set DCPWIZARD to the binary or put dcpwizard on PATH)"
    exit 1
fi

if [[ ! -f "$SOURCE_VIDEO" ]]; then
    echo "ERROR: source video not found: ${SOURCE_VIDEO:-(unset)}"
    echo "Set SOURCE_VIDEO to a video with a picture and a sound track"
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
echo "Generating: short_2k_24fps..."
$DCPWIZARD create --title "CTP-Test-2K-24" --video "$SHORT_VIDEO" \
    --output "$DEST/short_2k_24fps" -v 2>&1 | tail -5

echo ""
echo "Done. Generated DCPs at: $DEST"
ls "$DEST" 2>/dev/null
