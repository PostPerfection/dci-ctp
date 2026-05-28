#!/usr/bin/env bash
set -euo pipefail

# Download ISDCF SMPTE Bv2.1 test content
# Note: This content is encrypted; keys require Deluxe registration.
# However, dcpdoctor can still validate structure/packaging without keys.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST="$REPO_DIR/tests/isdcf"

URL="https://files.isdcf.com/SMPTE-DCP-Content/SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders.zip"
ZIP_NAME="SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders.zip"

mkdir -p "$DEST"

if [[ -d "$DEST/SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders" ]]; then
    echo "ISDCF test content already present at $DEST"
    exit 0
fi

echo "Downloading ISDCF SMPTE Bv2.1 test content (~2GB)..."
echo "Source: $URL"

cd "$DEST"
if command -v wget &>/dev/null; then
    wget -c "$URL" -O "$ZIP_NAME"
elif command -v curl &>/dev/null; then
    curl -L -C - -o "$ZIP_NAME" "$URL"
else
    echo "ERROR: wget or curl required"
    exit 1
fi

echo "Extracting..."
unzip -o "$ZIP_NAME"
rm -f "$ZIP_NAME"

echo "Done. ISDCF test content at: $DEST"
