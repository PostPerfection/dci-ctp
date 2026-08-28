#!/usr/bin/env bash
set -euo pipefail

# Download the ClairMeta_Data ECL reference DCPs (~1.5GB).
# Pinned to one commit so the observed verdicts recorded in corpus/manifest.json
# stay reproducible. Bump COMMIT and re-run scripts/scan_reference.py together.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST="${CLAIRMETA_DATA:-$(dirname "$REPO_DIR")/dci-ctp-work/ClairMeta_Data}"

URL="https://github.com/ClairMeta/ClairMeta_Data.git"
COMMIT="a78c4cbf86bb31388180cdfa7652ed7368c614cc"

if [[ -d "$DEST/DCP/ECL-SET" ]]; then
    echo "ClairMeta_Data already present at $DEST"
    exit 0
fi

echo "Downloading ClairMeta_Data ECL set (~1.5GB) at $COMMIT..."
echo "Source: $URL"

mkdir -p "$DEST"
git init -q "$DEST"
git -C "$DEST" remote add origin "$URL" 2>/dev/null || git -C "$DEST" remote set-url origin "$URL"
git -C "$DEST" fetch --depth 1 origin "$COMMIT"
git -C "$DEST" checkout -q FETCH_HEAD

if [[ ! -d "$DEST/DCP/ECL-SET" ]]; then
    echo "ERROR: $DEST/DCP/ECL-SET missing after checkout"
    exit 1
fi

echo "Done. ClairMeta_Data at: $DEST"
echo "Set CLAIRMETA_DATA=$DEST when running the corpus or the differential."
