#!/usr/bin/env bash
set -euo pipefail

# Create synthetic test DCPs for the CTP test suite
# These are minimal XML structures designed to test specific validation rules

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
TESTS="$REPO_DIR/tests/synthetic"

echo "Creating synthetic test DCPs..."

# Helper to create a minimal valid SMPTE DCP
create_valid_smpte() {
    local dir="$1"
    local title="${2:-Test}"
    local resolution="${3:-2048 1080}"
    local edit_rate="${4:-24 1}"
    local content_kind="${5:-test}"
    
    mkdir -p "$dir"
    
    local cpl_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    local pkl_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    local am_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    local pic_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    local snd_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    local reel_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    
    # VOLINDEX
    cat > "$dir/VOLINDEX.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<VolumeIndex xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Index>1</Index>
</VolumeIndex>
EOF

    # CPL
    cat > "$dir/CPL.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/429-7/2006/CPL">
  <Id>${cpl_uuid}</Id>
  <ContentTitleText>${title}</ContentTitleText>
  <IssueDate>2025-01-01T00:00:00+00:00</IssueDate>
  <ContentKind>${content_kind}</ContentKind>
  <ReelList>
    <Reel>
      <Id>${reel_uuid}</Id>
      <AssetList>
        <MainPicture>
          <Id>${pic_uuid}</Id>
          <EditRate>${edit_rate}</EditRate>
          <IntrinsicDuration>48</IntrinsicDuration>
          <Duration>48</Duration>
          <FrameRate>${edit_rate}</FrameRate>
          <ScreenAspectRatio>${resolution}</ScreenAspectRatio>
        </MainPicture>
        <MainSound>
          <Id>${snd_uuid}</Id>
          <EditRate>${edit_rate}</EditRate>
          <IntrinsicDuration>48</IntrinsicDuration>
          <Duration>48</Duration>
        </MainSound>
      </AssetList>
    </Reel>
  </ReelList>
</CompositionPlaylist>
EOF

    # Create MXF stubs (minimal but non-empty)
    dd if=/dev/zero of="$dir/picture.mxf" bs=1024 count=4 2>/dev/null
    dd if=/dev/zero of="$dir/sound.mxf" bs=1024 count=2 2>/dev/null
    
    # Compute CPL hash
    local cpl_hash
    cpl_hash=$(openssl dgst -sha1 -binary "$dir/CPL.xml" | base64)
    local pic_hash
    pic_hash=$(openssl dgst -sha1 -binary "$dir/picture.mxf" | base64)
    local snd_hash
    snd_hash=$(openssl dgst -sha1 -binary "$dir/sound.mxf" | base64)
    
    # PKL
    cat > "$dir/PKL.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<PackingList xmlns="http://www.smpte-ra.org/schemas/429-8/2007/PKL">
  <Id>${pkl_uuid}</Id>
  <IssueDate>2025-01-01T00:00:00+00:00</IssueDate>
  <Issuer>dci-ctp test suite</Issuer>
  <Creator>dci-ctp</Creator>
  <AssetList>
    <Asset>
      <Id>${cpl_uuid}</Id>
      <Hash>${cpl_hash}</Hash>
      <Size>$(stat -c%s "$dir/CPL.xml")</Size>
      <Type>text/xml</Type>
    </Asset>
    <Asset>
      <Id>${pic_uuid}</Id>
      <Hash>${pic_hash}</Hash>
      <Size>$(stat -c%s "$dir/picture.mxf")</Size>
      <Type>application/mxf</Type>
    </Asset>
    <Asset>
      <Id>${snd_uuid}</Id>
      <Hash>${snd_hash}</Hash>
      <Size>$(stat -c%s "$dir/sound.mxf")</Size>
      <Type>application/mxf</Type>
    </Asset>
  </AssetList>
</PackingList>
EOF

    # PKL hash for ASSETMAP
    local pkl_size
    pkl_size=$(stat -c%s "$dir/PKL.xml")

    # ASSETMAP
    cat > "$dir/ASSETMAP.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Id>${am_uuid}</Id>
  <Creator>dci-ctp test suite</Creator>
  <IssueDate>2025-01-01T00:00:00+00:00</IssueDate>
  <AssetList>
    <Asset>
      <Id>${pkl_uuid}</Id>
      <PackingList>true</PackingList>
      <ChunkList>
        <Chunk><Path>PKL.xml</Path></Chunk>
      </ChunkList>
    </Asset>
    <Asset>
      <Id>${cpl_uuid}</Id>
      <ChunkList>
        <Chunk><Path>CPL.xml</Path></Chunk>
      </ChunkList>
    </Asset>
    <Asset>
      <Id>${pic_uuid}</Id>
      <ChunkList>
        <Chunk><Path>picture.mxf</Path></Chunk>
      </ChunkList>
    </Asset>
    <Asset>
      <Id>${snd_uuid}</Id>
      <ChunkList>
        <Chunk><Path>sound.mxf</Path></Chunk>
      </ChunkList>
    </Asset>
  </AssetList>
</AssetMap>
EOF
}

# Recompute the CPL hash into PKL.xml after editing a CPL, so an intentional CPL
# defect doesn't also trip pkl_hash_mismatch.
reseal_cpl_hash() {
    python3 - "$1" << 'PY'
import sys, re, base64, hashlib
d = sys.argv[1]
cpl = open(f"{d}/CPL.xml", "rb").read()
h = base64.b64encode(hashlib.sha1(cpl).digest()).decode()
cid = re.search(rb"<Id>(urn:uuid:[^<]+)</Id>", cpl).group(1).decode()
pkl = open(f"{d}/PKL.xml").read()
# rewrite the Hash only in the Asset block that references the CPL
pkl = re.sub(r"<Asset>.*?</Asset>",
             lambda m: re.sub(r"<Hash>[^<]*</Hash>", f"<Hash>{h}</Hash>", m.group(0)) if cid in m.group(0) else m.group(0),
             pkl, flags=re.S)
open(f"{d}/PKL.xml", "w").write(pkl)
PY
}

# ===== VALID TEST DCPs =====

# Valid SMPTE 2K (Flat 1998x1080)
create_valid_smpte "$TESTS/valid/minimal_smpte" "CTP Valid SMPTE" "1998 1080" "24 1" "test"
echo "  Created: valid/minimal_smpte"

# Valid Scope 2K (2048x858)
create_valid_smpte "$TESTS/valid/scope_2k" "CTP Scope 2K" "2048 858" "24 1" "test"
echo "  Created: valid/scope_2k"

# Valid Flat 2K (1998x1080)
create_valid_smpte "$TESTS/valid/flat_2k" "CTP Flat 2K" "1998 1080" "24 1" "feature"
echo "  Created: valid/flat_2k"

# Valid Interop DCP
mkdir -p "$TESTS/valid/minimal_interop"
cat > "$TESTS/valid/minimal_interop/VOLINDEX" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<VolumeIndex xmlns="http://www.digicine.com/PROTO-ASDCP-AM-20040311#">
  <Index>1</Index>
</VolumeIndex>
EOF

interop_cpl_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
interop_pkl_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
interop_pic_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
interop_reel_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
interop_am_uuid="urn:uuid:$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"

cat > "$TESTS/valid/minimal_interop/CPL.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<CompositionPlaylist xmlns="http://www.digicine.com/PROTO-ASDCP-CPL-20040511#">
  <Id>${interop_cpl_uuid}</Id>
  <ContentTitleText>CTP Interop Test</ContentTitleText>
  <IssueDate>2025-01-01T00:00:00+00:00</IssueDate>
  <ContentKind>test</ContentKind>
  <ReelList>
    <Reel>
      <Id>${interop_reel_uuid}</Id>
      <AssetList>
        <MainPicture>
          <Id>${interop_pic_uuid}</Id>
          <EditRate>24 1</EditRate>
          <IntrinsicDuration>48</IntrinsicDuration>
          <Duration>48</Duration>
          <FrameRate>24 1</FrameRate>
          <ScreenAspectRatio>2048 858</ScreenAspectRatio>
        </MainPicture>
      </AssetList>
    </Reel>
  </ReelList>
</CompositionPlaylist>
EOF

dd if=/dev/zero of="$TESTS/valid/minimal_interop/picture.mxf" bs=1024 count=4 2>/dev/null

interop_cpl_hash=$(openssl dgst -sha1 -binary "$TESTS/valid/minimal_interop/CPL.xml" | base64)
interop_pic_hash=$(openssl dgst -sha1 -binary "$TESTS/valid/minimal_interop/picture.mxf" | base64)

cat > "$TESTS/valid/minimal_interop/PKL.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<PackingList xmlns="http://www.digicine.com/PROTO-ASDCP-PKL-20040311#">
  <Id>${interop_pkl_uuid}</Id>
  <IssueDate>2025-01-01T00:00:00+00:00</IssueDate>
  <Issuer>dci-ctp</Issuer>
  <Creator>dci-ctp</Creator>
  <AssetList>
    <Asset>
      <Id>${interop_cpl_uuid}</Id>
      <Hash>${interop_cpl_hash}</Hash>
      <Size>$(stat -c%s "$TESTS/valid/minimal_interop/CPL.xml")</Size>
      <Type>text/xml</Type>
    </Asset>
    <Asset>
      <Id>${interop_pic_uuid}</Id>
      <Hash>${interop_pic_hash}</Hash>
      <Size>$(stat -c%s "$TESTS/valid/minimal_interop/picture.mxf")</Size>
      <Type>application/mxf</Type>
    </Asset>
  </AssetList>
</PackingList>
EOF

cat > "$TESTS/valid/minimal_interop/ASSETMAP" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<AssetMap xmlns="http://www.digicine.com/PROTO-ASDCP-AM-20040311#">
  <Id>${interop_am_uuid}</Id>
  <Creator>dci-ctp</Creator>
  <IssueDate>2025-01-01T00:00:00+00:00</IssueDate>
  <AssetList>
    <Asset>
      <Id>${interop_pkl_uuid}</Id>
      <PackingList>true</PackingList>
      <ChunkList>
        <Chunk><Path>PKL.xml</Path></Chunk>
      </ChunkList>
    </Asset>
    <Asset>
      <Id>${interop_cpl_uuid}</Id>
      <ChunkList>
        <Chunk><Path>CPL.xml</Path></Chunk>
      </ChunkList>
    </Asset>
    <Asset>
      <Id>${interop_pic_uuid}</Id>
      <ChunkList>
        <Chunk><Path>picture.mxf</Path></Chunk>
      </ChunkList>
    </Asset>
  </AssetList>
</AssetMap>
EOF
echo "  Created: valid/minimal_interop"

# ===== INVALID TEST DCPs =====

# Missing ASSETMAP
mkdir -p "$TESTS/invalid/missing_assetmap"
cat > "$TESTS/invalid/missing_assetmap/VOLINDEX.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<VolumeIndex xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Index>1</Index>
</VolumeIndex>
EOF
dd if=/dev/zero of="$TESTS/invalid/missing_assetmap/picture.mxf" bs=1024 count=1 2>/dev/null
echo "  Created: invalid/missing_assetmap"

# Empty DCP (just ASSETMAP, no CPL/PKL)
mkdir -p "$TESTS/invalid/empty_dcp"
cat > "$TESTS/invalid/empty_dcp/ASSETMAP.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000001</Id>
  <AssetList/>
</AssetMap>
EOF
echo "  Created: invalid/empty_dcp"

# Bad XML in CPL
mkdir -p "$TESTS/invalid/bad_xml"
cat > "$TESTS/invalid/bad_xml/ASSETMAP.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000001</Id>
  <AssetList>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000010</Id>
      <PackingList>true</PackingList>
      <ChunkList><Chunk><Path>PKL.xml</Path></Chunk></ChunkList>
    </Asset>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000002</Id>
      <ChunkList><Chunk><Path>CPL.xml</Path></Chunk></ChunkList>
    </Asset>
  </AssetList>
</AssetMap>
EOF
cat > "$TESTS/invalid/bad_xml/PKL.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<PackingList xmlns="http://www.smpte-ra.org/schemas/429-8/2007/PKL">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000010</Id>
  <AssetList>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000002</Id>
      <Hash>AAAAAAAAAAAAAAAAAAAAAAAAAAAA</Hash>
      <Size>999</Size>
      <Type>text/xml</Type>
    </Asset>
  </AssetList>
</PackingList>
EOF
cat > "$TESTS/invalid/bad_xml/CPL.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/429-7/2006/CPL">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000002</Id>
  <ContentTitleText>Bad XML Test</ContentTitleText>
  <BROKEN XML HERE >>>
</CompositionPlaylist>
EOF
echo "  Created: invalid/bad_xml"

# Bad hash
mkdir -p "$TESTS/invalid/bad_hash"
cat > "$TESTS/invalid/bad_hash/ASSETMAP.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000001</Id>
  <AssetList>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000010</Id>
      <PackingList>true</PackingList>
      <ChunkList><Chunk><Path>PKL.xml</Path></Chunk></ChunkList>
    </Asset>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000002</Id>
      <ChunkList><Chunk><Path>CPL.xml</Path></Chunk></ChunkList>
    </Asset>
  </AssetList>
</AssetMap>
EOF
cat > "$TESTS/invalid/bad_hash/CPL.xml" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/429-7/2006/CPL">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000002</Id>
  <ContentTitleText>Bad Hash Test</ContentTitleText>
  <ContentKind>test</ContentKind>
  <ReelList>
    <Reel>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000003</Id>
      <AssetList>
        <MainPicture>
          <Id>urn:uuid:00000000-0000-0000-0000-000000000004</Id>
          <EditRate>24 1</EditRate>
          <IntrinsicDuration>48</IntrinsicDuration>
          <Duration>48</Duration>
        </MainPicture>
      </AssetList>
    </Reel>
  </ReelList>
</CompositionPlaylist>
EOF
cat > "$TESTS/invalid/bad_hash/PKL.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<PackingList xmlns="http://www.smpte-ra.org/schemas/429-8/2007/PKL">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000010</Id>
  <AssetList>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000002</Id>
      <Hash>WRONG_HASH_INTENTIONALLY_BAD</Hash>
      <Size>999</Size>
      <Type>text/xml</Type>
    </Asset>
  </AssetList>
</PackingList>
EOF
echo "  Created: invalid/bad_hash"

# Missing CPL (has PKL but no valid CPL)
mkdir -p "$TESTS/invalid/missing_cpl"
cat > "$TESTS/invalid/missing_cpl/ASSETMAP.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<AssetMap xmlns="http://www.smpte-ra.org/schemas/429-9/2007/AM">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000001</Id>
  <AssetList>
    <Asset>
      <Id>urn:uuid:00000000-0000-0000-0000-000000000010</Id>
      <PackingList>true</PackingList>
      <ChunkList><Chunk><Path>PKL.xml</Path></Chunk></ChunkList>
    </Asset>
  </AssetList>
</AssetMap>
EOF
cat > "$TESTS/invalid/missing_cpl/PKL.xml" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<PackingList xmlns="http://www.smpte-ra.org/schemas/429-8/2007/PKL">
  <Id>urn:uuid:00000000-0000-0000-0000-000000000010</Id>
  <AssetList/>
</PackingList>
EOF
echo "  Created: invalid/missing_cpl"

# Bad content kind
create_valid_smpte "$TESTS/invalid/bad_content_kind" "Bad Kind" "2048 1080" "24 1" "INVALID_KIND"
echo "  Created: invalid/bad_content_kind"

# Bad edit rate (13fps is not DCI-approved)
create_valid_smpte "$TESTS/invalid/bad_edit_rate" "Bad Rate" "2048 1080" "13 1" "test"
echo "  Created: invalid/bad_edit_rate"

# Broken cross-reference: MainPicture references an Id not in the ASSETMAP
create_valid_smpte "$TESTS/invalid/bad_cross_ref" "Bad CrossRef" "2048 1080" "24 1" "test"
python3 - "$TESTS/invalid/bad_cross_ref/CPL.xml" << 'PY'
import sys, re
p = sys.argv[1]; s = open(p).read()
s = re.sub(r"(<MainPicture>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
           r"\1urn:uuid:deadbeef-0000-0000-0000-000000000000", s, count=1)
open(p, "w").write(s)
PY
reseal_cpl_hash "$TESTS/invalid/bad_cross_ref"
echo "  Created: invalid/bad_cross_ref"

# Markers: a MainMarkers track present but missing required FFMC/LFMC, and a
# marker with no Offset (marker_missing + marker_invalid, strict mode)
create_valid_smpte "$TESTS/invalid/bad_markers" "Bad Markers" "2048 1080" "24 1" "test"
python3 - "$TESTS/invalid/bad_markers/CPL.xml" << 'PY'
import sys
p = sys.argv[1]; s = open(p).read()
mm = """        <MainMarkers>
          <Id>urn:uuid:00000000-0000-0000-0000-0000000000aa</Id>
          <EditRate>24 1</EditRate>
          <IntrinsicDuration>48</IntrinsicDuration>
          <MarkerList>
            <Marker><Label>FFOC</Label></Marker>
          </MarkerList>
        </MainMarkers>
"""
s = s.replace("        <MainSound>", mm + "        <MainSound>", 1)
open(p, "w").write(s)
PY
reseal_cpl_hash "$TESTS/invalid/bad_markers"
echo "  Created: invalid/bad_markers"

# Encrypted content with no KDM in the package (encryption_detected + kdm_required)
create_valid_smpte "$TESTS/invalid/encrypted_no_kdm" "Encrypted NoKDM" "2048 1080" "24 1" "test"
python3 - "$TESTS/invalid/encrypted_no_kdm/CPL.xml" << 'PY'
import sys
p = sys.argv[1]; s = open(p).read()
s = s.replace("</MainPicture>",
              "  <KeyId>urn:uuid:00000000-0000-0000-0000-0000000000bb</KeyId>\n        </MainPicture>", 1)
open(p, "w").write(s)
PY
reseal_cpl_hash "$TESTS/invalid/encrypted_no_kdm"
echo "  Created: invalid/encrypted_no_kdm"

echo ""
echo "Done. Synthetic test DCPs created at: $TESTS"
