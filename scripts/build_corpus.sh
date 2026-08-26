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
# stereoscopic 3D (429-10). --video-bit-rate caps each eye, and both eyes share
# one edit unit, so 100 keeps the measured peak near 200 Mb/s, under the DCI 250
# limit with headroom (125 lands exactly on 250.01 and fails).
build "$VALID/dcp_3d" \
    --title "CTP3D_TST_F-3D_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" --right-eye "$RIGHT" \
    --video-bit-rate 100
# the same 3D package at the full 2K bandwidth, which measures over the DCI
# peak limit: the j2k_bitrate_exceeded fixture source
build "$CORPUS/.bitrate_src" \
    --title "CTPRate_TST_F-3D_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" --right-eye "$RIGHT" \
    --video-bit-rate 250
# Atmos AuxData (429-18)
build "$VALID/dcp_atmos" \
    --title "CTPAtmos_TST_F_EN_US_51-ATMOS_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" --atmos "$ATMOS"
# mono: no MCA soundfield, used as the unlabeled-sound fixture
build "$CORPUS/.mono_src" \
    --title "CTPMono_TST_F_EN_US_10_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAVMONO"

# encrypted (unsigned) DCP: dcpwizard emits encrypted packages with a KeyId but
# no CPL/PKL signature, so this is the real dcp_not_signed fixture source.
CERTS="$SRCDIR/certs"
ENCKEYS="$SRCDIR/enc_keys.json"
# regenerated every run: a kept chain survives a postkit certificate fix, and the
# corpus then reports defects that are already fixed
rm -rf "$CERTS"
"$DCPWIZARD" certificate chain --organization CTP --output "$CERTS" >/dev/null 2>&1
# the only package here whose CPL and PKL carry a real ds:Signature. without it
# unencrypted_dcp_not_signed has no baseline that does not already fire it, so
# no fixture for that code can be anything but vacuous.
build "$VALID/dcp_signed" \
    --title "CTPSigned_TST_F_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" \
    --signer-cert "$CERTS/signer.pem" --signer-key "$CERTS/signer.key" \
    --signer-chain "$CERTS/intermediate.pem" --signer-chain "$CERTS/root.pem"

rm -f "$ENCKEYS"
build "$CORPUS/.enc_src" \
    --title "CTPEnc_TST_F_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" \
    --encrypt --key-out "$ENCKEYS"

# encrypted and signed for real: the dcp_not_signed fixture's baseline. A real
# dcpwizard build, not a synthetic signature, so its signature verifies.
rm -f "$SRCDIR/enc_signed_keys.json"
build "$VALID/dcp_encrypted_signed" \
    --title "CTPEncSig_TST_F_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
    --content-type TST --video "$LEFT" --audio "$WAV51" \
    --encrypt --key-out "$SRCDIR/enc_signed_keys.json" \
    --signer-cert "$CERTS/signer.pem" --signer-key "$CERTS/signer.key" \
    --signer-chain "$CERTS/intermediate.pem" --signer-chain "$CERTS/root.pem"

# subcommand-only fixtures: KDMs with shifted validity windows (kdm subcommand)
# and audio exhibiting clipping/silence (auto-qc). All three KDMs are real
# dcpwizard output with intact signatures. Their signing chain runs 2020-2040
# (written by corpus_gen.py, same generator as the certificate fixtures) because
# dcpwizard refuses a window outside the signer chain's life or starting the
# chain's own first day, which rules out a currently-valid window under the
# freshly generated $CERTS chain.
SUBCMD="$CORPUS/subcmd"
rm -rf "$SUBCMD"; mkdir -p "$SUBCMD"
KDM_CERTS="$SRCDIR/kdm_certs"
rm -rf "$KDM_CERTS"
python3 "$SCRIPT_DIR/corpus_gen.py" --write-kdm-chain "$KDM_CERTS"
ENC_CPLID=$(grep -oE 'urn:uuid:[0-9a-fA-F-]+' "$CORPUS/.enc_src"/CPL_*.xml | head -1)
gen_kdm() {
    "$DCPWIZARD" kdm --cpl-id "$ENC_CPLID" --content-title "CTPEnc" \
        --cert "$KDM_CERTS/signer.pem" --signer-cert "$KDM_CERTS/signer.pem" \
        --signer-key "$KDM_CERTS/signer.key" \
        --signer-chain "$KDM_CERTS/intermediate.pem" --signer-chain "$KDM_CERTS/root.pem" \
        --keys "$ENCKEYS" --valid-from "$1" --valid-to "$2" -o "$3" >/dev/null
}
gen_kdm "2020-06-01T00:00:00+00:00" "2021-06-01T00:00:00+00:00" "$SUBCMD/kdm_expired.xml"
gen_kdm "2035-01-01T00:00:00+00:00" "2039-01-01T00:00:00+00:00" "$SUBCMD/kdm_future.xml"
gen_kdm "2024-01-01T00:00:00+00:00" "2039-01-01T00:00:00+00:00" "$SUBCMD/kdm_valid.xml"
# full-scale (clipping), near-silent, and a clean -12 dBFS reference tone
ffmpeg -y -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=1" \
       -af "volume=40dB,alimiter=limit=1.0" -c:a pcm_s24le "$SUBCMD/clip.wav" 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=1" \
       -af "volume=-90dB" -c:a pcm_s24le "$SUBCMD/silent.wav" 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=1" \
       -af "volume=-12dB" -c:a pcm_s24le "$SUBCMD/normal.wav" 2>/dev/null
echo "  built encrypted source, KDMs and audio fixtures"

# non-DCI J2K essence + IMF IMP, for the picture/j2k codes. grok's grk_compress
# and (for the wrap bypass) the vendored asdcplib asdcp-wrap are both needed.
export PATH="$PATH:$HOME/bin/grok/bin"
GRK="$(command -v grk_compress || true)"

# build asdcp-wrap once, cached in the source dir (dcpwizard/postkit enforce DCI
# on their wrap paths and subset the fonts they embed, so the raw C++ wrapper is
# the only way to get non-DCI essence into an AS-DCP MXF or a font of a chosen
# size into an ST 429-5 timed-text MXF). Resolved beside the dcpwizard binary, so
# a CI checkout finds it where a working copy does.
WIZARD_REPO="$(cd "$(dirname "$DCPWIZARD")/../../.." 2>/dev/null && pwd || echo "")"
ASDCPLIB_SRC="${ASDCPLIB_SRC:-$WIZARD_REPO/extern/asdcplib}"
ASDCP_BUILD="$SRCDIR/asdcplib-build"
WRAP="$ASDCP_BUILD/src/asdcp-wrap"
if [[ ! -x "$WRAP" && -d "$ASDCPLIB_SRC" ]]; then
    echo "Building asdcp-wrap (cached in $ASDCP_BUILD)..."
    mkdir -p "$ASDCP_BUILD"
    (cd "$ASDCP_BUILD" && cmake "$ASDCPLIB_SRC" >/dev/null 2>&1 \
        && make asdcp-wrap -j"$(nproc)" >/dev/null 2>&1) || echo "  asdcp-wrap build failed"
fi
export LD_LIBRARY_PATH="$ASDCP_BUILD/src:$LD_LIBRARY_PATH"

# non-DCI resolution essence: 1920x1080 (not a DCP-DCI size), plain codestream
# (grok without a cinema profile => Rsiz 0, single tile-part). Covers
# picture_invalid_resolution and j2k_invalid_profile.
NONDCI="$CORPUS/.nondci"
rm -rf "$NONDCI"; mkdir -p "$NONDCI"
if [[ -x "$WRAP" && -n "$GRK" ]]; then
    NF="$SRCDIR/nondci_frames"; NJ="$SRCDIR/nondci_j2c"
    if [[ ! -d "$NJ" ]]; then
        rm -rf "$NF" "$NJ"; mkdir -p "$NF" "$NJ"
        ffmpeg -y -f lavfi -i testsrc2=size=1920x1080:rate=24:duration=2 \
               -pix_fmt rgb24 "$NF/f_%04d.png" 2>/dev/null
        n=0; for f in "$NF"/*.png; do
            printf -v out "$NJ/frame_%04d.j2c" "$n"
            grk_compress -i "$f" -o "$out" >/dev/null 2>&1; n=$((n+1))
        done
    fi
    "$WRAP" "$NJ" "$NONDCI/nondci_res.mxf" >/dev/null 2>&1 \
        && echo "  wrapped non-DCI 1920x1080 essence" \
        || echo "  asdcp-wrap of non-DCI essence failed"
else
    echo "  skipping non-DCI essence (asdcp-wrap or grk_compress unavailable)"
fi

# IMF IMP for picture_invalid_frame_rate: 8-bit frames -> grok J2K -> imfwizard
# create. imfwizard enforces App-2E resolution at wrap time, so frame rate is the
# only pic-vs-CPL mismatch we can inject (by editing the CPL edit rate).
IW="${IMFWIZARD:-$HOME/src/PostPerfection/imfwizard/rust/target/release/imfwizard}"
rm -rf "$VALID/imf_ov"
if [[ -x "$IW" && -n "$GRK" ]]; then
    IF="$SRCDIR/imf_frames"; IJ="$SRCDIR/imf_j2c"
    if [[ ! -d "$IJ" ]]; then
        rm -rf "$IF" "$IJ"; mkdir -p "$IF"
        ffmpeg -y -f lavfi -i testsrc2=size=2048x1080:rate=24:duration=2 \
               -pix_fmt rgb24 "$IF/f_%04d.png" 2>/dev/null
        "$IW" encode -i "$IF" -o "$IJ" >/dev/null 2>&1
    fi
    "$IW" create --video "$IJ" --audio "$WAV51" \
        --title "CTPImf_TST_F_EN_US_51_2K_PPF_20260721_PPF_SMPTE_OV" \
        --output "$VALID/imf_ov" >/dev/null 2>&1 \
        && echo "  built valid/imf_ov (IMP)" || echo "  imfwizard IMP build failed"
else
    echo "  skipping IMF IMP (imfwizard or grk_compress unavailable)"
fi

# second mastering tool: DCP-o-matic, SMPTE and Interop. The config dir is
# corpus-local so a build does not depend on the developer's own DoM settings.
DOM="${DCPOMATIC_CREATE:-$(command -v dcpomatic2_create 2>/dev/null || true)}"
DOM_CLI="${DCPOMATIC_CLI:-$(command -v dcpomatic2_cli 2>/dev/null || true)}"
DOMCONFIG="$SRCDIR/domconfig"
build_dom() {
    local out="$1" standard="$2" name="$3" film="$SRCDIR/domfilm_$2"
    rm -rf "$out" "$film"
    "$DOM" --config "$DOMCONFIG" --standard "$standard" --no-encrypt \
        -c TST --twok -a 6 -n "$name" "$LEFT" "$WAV51" -o "$film" >/dev/null 2>&1
    "$DOM_CLI" --config "$DOMCONFIG" "$film" >/dev/null 2>&1
    local dcp
    dcp=$(find "$film" -maxdepth 1 -mindepth 1 -type d -name "${name}_*" | head -1)
    if [[ -n "$dcp" ]]; then
        mv "$dcp" "$out" && echo "  built $out (DCP-o-matic $standard)"
    else
        echo "  DCP-o-matic $standard build failed"
    fi
}
if [[ -x "$DOM" && -x "$DOM_CLI" ]]; then
    mkdir -p "$DOMCONFIG"
    build_dom "$VALID/dcp_dom_ov" SMPTE CTPDom
    build_dom "$VALID/dcp_dom_interop" interop CTPDomIop
else
    echo "  skipping DCP-o-matic baselines (dcpomatic2_create/dcpomatic2_cli unavailable)"
fi

echo "Generating negative fixtures..."
python3 "$SCRIPT_DIR/corpus_gen.py"

echo "Recording reference-package verdicts (if ClairMeta_Data present)..."
python3 "$SCRIPT_DIR/scan_reference.py" || true

echo "Done."
