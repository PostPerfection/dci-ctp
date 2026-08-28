#!/usr/bin/env bash
set -euo pipefail

# Prove dcpwizard's encryption + KDM chain is cryptographically correct without
# projector hardware, by an independent decrypt roundtrip:
#   encrypt a DCP -> build a KDM for a recipient we control ->
#   independently RSA-unwrap the content key from the KDM with the recipient's
#   private key (openssl, not dcpwizard's rsa crate) ->
#   decrypt the encrypted picture essence with that key (asdcplib-rs) and
#   confirm it succeeds, while a wrong key fails.
#
# What this proves: KDM key delivery (ST 430-1 RSA-OAEP wrap) and essence
# decryption (SMPTE 429-6 AES-128-CBC + check value + HMAC) are correct.
# Out of scope: playback on real media-block/projector hardware, forensic marking.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

DCPWIZARD="${DCPWIZARD:-$(command -v dcpwizard 2>/dev/null || true)}"
HELPER_DIR="$REPO_DIR/tools/decrypt-check"
HELPER="$HELPER_DIR/target/release/decrypt-check"

if [[ ! -x "$DCPWIZARD" ]]; then
    echo "ERROR: dcpwizard not found (set DCPWIZARD to the binary or put dcpwizard on PATH)" >&2
    exit 1
fi

for tool in openssl ffmpeg python3 cargo; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool not found" >&2; exit 1; }
done

echo "Building decrypt-check helper..."
cargo build --release --manifest-path "$HELPER_DIR/Cargo.toml" >/dev/null 2>&1 || {
    echo "ERROR: failed to build decrypt-check helper" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "Work dir: $WORK"

# 1. signer + recipient certs. Only the RSA-2048 key shape matters here; the
# recipient private key is ours, which is what makes independent recovery possible.
echo "[1/6] Generating signer + recipient RSA-2048 certs..."
openssl req -x509 -newkey rsa:2048 -keyout "$WORK/signer.key" -out "$WORK/signer.pem" \
    -days 3650 -nodes -subj "/O=dci-ctp/OU=verify/CN=CTP Signer" 2>/dev/null
openssl req -x509 -newkey rsa:2048 -keyout "$WORK/recipient.key" -out "$WORK/recipient.pem" \
    -days 3650 -nodes -subj "/O=dci-ctp/OU=verify/CN=CTP Recipient" 2>/dev/null

# 2. small source clip, then an encrypted DCP with a sidecar keys.json
echo "[2/6] Encoding source clip + encrypted DCP..."
SRC="$WORK/src.mp4"
ffmpeg -y -f lavfi -i testsrc2=size=2048x1080:rate=24:duration=1 \
       -f lavfi -i sine=frequency=1000:sample_rate=48000:duration=1 \
       -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$SRC" 2>/dev/null
"$DCPWIZARD" create \
    --title "CTPEnc_TST_F_EN_US_51_2K_PPF_20260720_PPF_SMPTE_OV" \
    --content-type TST --video "$SRC" \
    --encrypt --key-out "$WORK/keys.json" \
    --output "$WORK/dcp" >/dev/null

CPL_ID="$(python3 -c "import json;print(json.load(open('$WORK/keys.json'))['cpl_id'])")"
echo "  CPL id: $CPL_ID"

# 3. KDM binding the DCP's content keys to the recipient. dcpwizard rejects a
# signer whose notBefore is not earlier than the day the window opens, and
# openssl before 3.5 cannot backdate a cert, so the window opens tomorrow.
KDM_FROM="$(date -u -d tomorrow +%Y-%m-%dT%H:%M:%S+00:00)"
KDM_TO="$(date -u -d '+30 days' +%Y-%m-%dT%H:%M:%S+00:00)"
echo "[3/6] Generating KDM for the recipient..."
"$DCPWIZARD" kdm \
    --cpl-id "$CPL_ID" --content-title "CTP Encryption Verify" \
    --cert "$WORK/recipient.pem" \
    --signer-cert "$WORK/signer.pem" --signer-key "$WORK/signer.key" \
    --valid-from "$KDM_FROM" --valid-to "$KDM_TO" \
    --keys "$WORK/keys.json" --output "$WORK/kdm.xml" >/dev/null

# 4. independently recover the content keys from the KDM (openssl RSA-OAEP)
echo "[4/6] Recovering content keys from KDM with recipient private key..."
REC="$(python3 "$SCRIPT_DIR/recover_kdm_key.py" "$WORK/kdm.xml" "$WORK/recipient.key" "$WORK/keys.json")"
PIC_KEY="$(echo "$REC" | cut -d' ' -f1)"
PIC_KID="$(echo "$REC" | cut -d' ' -f2)"
echo "  Recovered picture key id: $PIC_KID"

# 5. decrypt the encrypted picture essence with the recovered key
echo "[5/6] Decrypting picture essence with the KDM-delivered key..."
PIC_MXF="$(ls "$WORK/dcp"/picture_*.mxf)"
"$HELPER" "$PIC_MXF" "$PIC_KEY" "$PIC_KID"

# 6. negative control: a KDM built for a DIFFERENT recipient must NOT yield a
# key our recipient can recover (RSA unwrap fails), proving the binding is real.
echo "[6/6] Negative control: KDM for a different recipient must not unwrap..."
openssl req -x509 -newkey rsa:2048 -keyout "$WORK/other.key" -out "$WORK/other.pem" \
    -days 3650 -nodes -subj "/O=dci-ctp/OU=verify/CN=Other Recipient" 2>/dev/null
"$DCPWIZARD" kdm \
    --cpl-id "$CPL_ID" --content-title "CTP Encryption Verify" \
    --cert "$WORK/other.pem" \
    --signer-cert "$WORK/signer.pem" --signer-key "$WORK/signer.key" \
    --valid-from "$KDM_FROM" --valid-to "$KDM_TO" \
    --keys "$WORK/keys.json" --output "$WORK/kdm_other.xml" >/dev/null
if python3 "$SCRIPT_DIR/recover_kdm_key.py" "$WORK/kdm_other.xml" "$WORK/recipient.key" "$WORK/keys.json" >/dev/null 2>&1; then
    echo "ERROR: recovered a key from a KDM addressed to a different recipient" >&2
    exit 1
fi
echo "  correctly failed to unwrap a foreign-recipient KDM"

echo ""
echo "PASS: dcpwizard encryption + KDM chain verified end to end."
echo "  KDM delivers the correct content key (RSA-OAEP unwrap, cross-checked vs keys.json)"
echo "  that key decrypts the encrypted essence; wrong/foreign keys fail."
