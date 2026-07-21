#!/usr/bin/env bash
set -euo pipefail

# Build the per-error-code negative-test corpus.
# 1. build the real DCPs dcpwizard produces (labeled 5.1 base, 3D, Atmos, mono)
# 2. clone+mutate/patch them once per error code (corpus_gen.py)
# 3. record ClairMeta reference-package verdicts if present (scan_reference.py)
#
# dcpwizard now labels sound with real ST 429-12 MCA subdescriptors and writes
# stereoscopic (429-10) and Atmos AuxData (429-18) essence, so the base is 5.1
# (labeled) and the mono build is the unlabeled fixture. Building needs the grok
# codec libs on the library path.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CORPUS="$REPO_DIR/corpus"
VALID="$CORPUS/valid"

DCPWIZARD="${DCPWIZARD:-$(command -v dcpwizard 2>/dev/null || echo "$HOME/src/PostPerfection/dcpwizard/rust/target/release/dcpwizard")}"

if [[ ! -x "$DCPWIZARD" ]]; then
    echo "ERROR: dcpwizard not found at $DCPWIZARD" >&2
    exit 1
fi

export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:$HOME/bin/grok/lib64"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:-}:$HOME/bin/grok/lib64/pkgconfig"

SRCDIR="${CTP_SRC_DIR:-/tmp/ctp-corpus-src}"
mkdir -p "$SRCDIR"
LEFT="$SRCDIR/left.mp4"
RIGHT="$SRCDIR/right.mp4"
WAV51="$SRCDIR/audio51.wav"
WAVMONO="$SRCDIR/mono.wav"
ATMOS="$SRCDIR/atmos_frames"

if [[ ! -f "$LEFT" ]]; then
    echo "Creating source media..."
    ffmpeg -y -f lavfi -i testsrc2=size=2048x1080:rate=24:duration=2 \
           -c:v libx264 -pix_fmt yuv420p "$LEFT" 2>/dev/null
    ffmpeg -y -f lavfi -i "testsrc2=size=2048x1080:rate=24:duration=2,negate" \
           -c:v libx264 -pix_fmt yuv420p "$RIGHT" 2>/dev/null
    # real 5.1 so dcpwizard writes MCA soundfield labels
    ffmpeg -y -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=2" \
           -af "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0" -c:a pcm_s24le "$WAV51" 2>/dev/null
    ffmpeg -y -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=2" \
           -c:a pcm_s24le "$WAVMONO" 2>/dev/null
fi

# dcpwizard wraps one aux frame per input file and requires the count to
# match the picture duration (2s @ 24fps = 48 frames)
if [[ ! -d "$ATMOS" ]]; then
    mkdir -p "$ATMOS"
    for i in $(seq -w 0 47); do
        head -c 4096 /dev/urandom > "$ATMOS/frame_$i.bin"
    done
fi

build() {
    local out="$1"; shift
    rm -rf "$out"
    "$DCPWIZARD" create "$@" --output "$out" >/dev/null 2>&1
    echo "  built $out"
}

echo "Building real DCPs with dcpwizard..."
mkdir -p "$VALID"
# labeled 5.1 base (the clean baseline; MCA labels present)
build "$VALID/dcp_ov" \
    --title "CTPBase_TST_F_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51"
# stereoscopic 3D (429-10)
build "$VALID/dcp_3d" \
    --title "CTP3D_TST_F-3D_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" --right-eye "$RIGHT"
# Atmos AuxData (429-18)
build "$VALID/dcp_atmos" \
    --title "CTPAtmos_TST_F_EN_US_51-ATMOS_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" --atmos "$ATMOS"
# mono: no MCA soundfield, used as the unlabeled-sound fixture
build "$CORPUS/.mono_src" \
    --title "CTPMono_TST_F_EN_US_10_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAVMONO"

echo "Generating negative fixtures..."
python3 "$SCRIPT_DIR/corpus_gen.py"

echo "Recording reference-package verdicts (if ClairMeta_Data present)..."
python3 "$SCRIPT_DIR/scan_reference.py" || true

echo "Done."
