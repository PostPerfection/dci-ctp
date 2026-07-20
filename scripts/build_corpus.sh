#!/usr/bin/env bash
set -euo pipefail

# Build the per-error-code negative-test corpus.
# 1. build one real base DCP with dcpwizard (real J2K + PCM MXFs)
# 2. clone+mutate it once per error code (corpus_gen.py)
# 3. record ClairMeta reference-package verdicts if present (scan_reference.py)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CORPUS="$REPO_DIR/corpus"
BASE="$CORPUS/valid/dcp_ov"

DCPWIZARD="${DCPWIZARD:-$(command -v dcpwizard 2>/dev/null || echo "$HOME/src/PostPerfection/dcpwizard/rust/target/release/dcpwizard")}"

if [[ ! -x "$DCPWIZARD" ]]; then
    echo "ERROR: dcpwizard not found at $DCPWIZARD" >&2
    exit 1
fi

# a valid ISDCF-named base so the clean baseline has zero warnings
SRC="${SOURCE_VIDEO:-/tmp/ctp-corpus-source.mp4}"
if [[ ! -f "$SRC" ]]; then
    echo "Creating source clip (2s testsrc2 + tone)..."
    ffmpeg -y -f lavfi -i testsrc2=size=2048x1080:rate=24:duration=2 \
           -f lavfi -i sine=frequency=1000:sample_rate=48000:duration=2 \
           -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$SRC" 2>/dev/null
fi

echo "Building base DCP with dcpwizard..."
rm -rf "$BASE"
mkdir -p "$(dirname "$BASE")"
"$DCPWIZARD" create \
    --title "CTPBase_TST_F_EN_US_51_2K_PPF_20260720_PPF_SMPTE_OV" \
    --content-type TST --video "$SRC" --output "$BASE" >/dev/null 2>&1
echo "  base: $BASE"

echo "Generating negative fixtures..."
python3 "$SCRIPT_DIR/corpus_gen.py"

echo "Recording reference-package verdicts (if ClairMeta_Data present)..."
python3 "$SCRIPT_DIR/scan_reference.py" || true

echo "Done."
