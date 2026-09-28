#!/usr/bin/env python3
"""Generate the dcpdoctor negative-test corpus.

Clones a real base DCP (built by build_corpus.sh via dcpwizard) once per error
code and applies a minimal mutation that triggers exactly that code, then
reseals the PKL hashes so no unrelated code fires. Writes a machine-readable
manifest a differential-testing agent can consume.

Each fixture keeps the base's real J2K/PCM MXFs (hardlinked, so essence checks
run for real), and only the XML is edited.
"""

import base64
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CORPUS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "corpus"))
REPO = os.path.dirname(CORPUS)
BASE = os.path.join(CORPUS, "valid", "dcp_ov")
MONO = os.path.join(CORPUS, ".mono_src")  # unlabeled-sound source (built by build_corpus.sh)
THREE_D = os.path.join(CORPUS, "valid", "dcp_3d")
THREE_D_FOUR_K = os.path.join(CORPUS, "valid", "dcp_3d_4k")
ATMOS = os.path.join(CORPUS, "valid", "dcp_atmos")
BITRATE_SRC = os.path.join(CORPUS, ".bitrate_src")  # 3D at full 2K bandwidth, over the DCI peak
IMF_SRC = os.path.join(CORPUS, "valid", "imf_ov")  # IMF IMP, built by build_corpus.sh

# the App 2E descriptor rules only run in IMF mode
APP2E_FLAGS = ["--imf"]
APP2E_FIXTURES = [
    ("cinema_profile", "picture_not_imf_profile"),
    ("colour_missing", "picture_colour_missing"),
    ("label_mismatch", "picture_coding_label_mismatch"),
    ("layout_mismatch", "picture_pixel_layout_mismatch"),
]
NONDCI_MXF = os.path.join(CORPUS, ".nondci", "nondci_res.mxf")  # non-DCI J2K wrapped by asdcp-wrap
# where build_corpus.sh caches its asdcplib build. dcpwizard and postkit both
# subset the fonts they embed, so this is the only wrapper that puts a font of a
# chosen size inside an ST 429-5 timed-text MXF.
ASDCP_BUILD = os.path.join(os.environ.get("CTP_SRC_DIR", "/tmp/ctp-corpus-src"), "asdcplib-build")
ASDCP_WRAP = os.path.join(ASDCP_BUILD, "src", "asdcp-wrap")
# the second mastering tool in the corpus. Every fixture derived from BASE
# resolves its targets by content, so the same mutation applies to these too.
ALL_MARKERS_BASELINE = "valid/dcp_all_markers"
ALL_MARKERS = os.path.join(CORPUS, *ALL_MARKERS_BASELINE.split("/"))
DOM_BASE = os.path.join(CORPUS, "valid", "dcp_dom_ov")
DOM_INTEROP = os.path.join(CORPUS, "valid", "dcp_dom_interop")
SIGNED_BASE = os.path.join(CORPUS, "valid", "dcp_signed")
ENCRYPTED_SIGNED_BASELINE = "valid/dcp_encrypted_signed"
ENCRYPTED_SIGNED_BASE = os.path.join(CORPUS, *ENCRYPTED_SIGNED_BASELINE.split("/"))

def sha1_b64(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def find(dirpath, pat):
    hits = glob.glob(os.path.join(dirpath, pat))
    if not hits:
        raise FileNotFoundError(f"{pat} in {dirpath}")
    return hits[0]


def clone(dest, copy_mxf=False, src=BASE):
    """Copy a fixture from `src` (default BASE). MXFs are hardlinked unless
    copy_mxf (so a fixture that mutates essence gets its own writable copy)."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        dst = os.path.join(dest, name)
        if name.endswith(".mxf") and not copy_mxf:
            os.link(s, dst)
        else:
            shutil.copy2(s, dst)
    return dest


def track_file(d, element):
    """The essence file a CPL track element points at, resolved id -> path
    through the ASSETMAP. A track file's name is the mastering tool's business,
    so resolving by id is the only way this works across vendors."""
    asset_id = cpl_asset_id(d, element)
    relative = assetmap_id_to_path(d).get(f"urn:uuid:{asset_id}")
    if not relative:
        raise FileNotFoundError(f"{element} asset {asset_id} not in the ASSETMAP of {d}")
    return os.path.join(d, relative)


def sound_mxf(d):
    return track_file(d, "MainSound")


def picture_mxf(d):
    """The picture track file, stereoscopic or not."""
    for element in ("MainPicture", "MainStereoscopicPicture"):
        try:
            return track_file(d, element)
        except (AttributeError, FileNotFoundError):
            continue
    raise FileNotFoundError(f"no picture track file in {d}")


def patch_bytes(path, old_hex, new_hex, count=1):
    """Byte-patch a copied essence file: replace old_hex with new_hex (equal
    length). Fails loudly if the expected pattern is not present exactly once."""
    old, new = bytes.fromhex(old_hex), bytes.fromhex(new_hex)
    assert len(old) == len(new), "patch length mismatch"
    data = open(path, "rb").read()
    n = data.count(old)
    assert n == count, f"expected {count} occurrence(s) of {old_hex} in {path}, found {n}"
    open(path, "wb").write(data.replace(old, new, count))


def local_tag_value(path, tag_hex, length):
    """Current value of an MXF local tag, as an int. The tag must appear exactly
    once so a coincidental byte match cannot pass silently."""
    header = bytes.fromhex(tag_hex + f"{length:04x}")
    data = open(path, "rb").read()
    n = data.count(header)
    assert n == 1, f"expected 1 occurrence of local tag {tag_hex} in {path}, found {n}"
    start = data.index(header) + len(header)
    return int.from_bytes(data[start:start + length], "big")


def patch_local_tag(path, tag_hex, length, new_value):
    """Set an MXF local tag, reading the current value rather than assuming it,
    so a fixture holds against essence from any mastering tool."""
    old = local_tag_value(path, tag_hex, length)
    width = length * 2
    patch_bytes(
        path,
        tag_hex + f"{length:04x}" + f"{old:0{width}x}",
        tag_hex + f"{length:04x}" + f"{new_value:0{width}x}",
    )
    return old


def break_mxf_footer(path):
    """Corrupt the SMPTE 377-1 footer partition pack key in an MXF's tail so
    check_mxf_partitions reports a missing footer partition. The header partition
    is left intact, so read_mxf_info still succeeds (no mxf_unreadable)."""
    # 13-byte partition pack key prefix (mxf_advanced.rs PARTITION_PACK_KEY)
    key = bytes.fromhex("060e2b34020501010d01020101")
    data = bytearray(open(path, "rb").read())
    scan_start = max(0, len(data) - 65536)  # dcpdoctor only scans the last 64 KiB
    target = None
    i = scan_start
    while (j := data.find(key, i)) >= 0:
        if data[j + 13] == 0x04:  # 0x04 = footer partition
            target = j
        i = j + 1
    assert target is not None, f"no footer partition in tail of {path}"
    data[target + 13] = 0x00
    open(path, "wb").write(data)


def replace_picture_mxf(d, src_mxf):
    """Swap the picture MXF in a fixture for another (non-DCI) essence, keeping
    the original filename so the ASSETMAP/PKL still resolve. reseal fixes hashes."""
    shutil.copy(src_mxf, picture_mxf(d))


def patch_j2k_component_count(mxf_path):
    """Byte-patch the first frame's SIZ Csiz field 3 -> 4 so the codestream
    declares 4 components. asdcp-wrap refuses to wrap a 4-component essence, so
    the count is patched into a valid 3-component wrap instead."""
    data = bytearray(open(mxf_path, "rb").read())
    soc = data.find(bytes.fromhex("FF51"))  # SIZ marker
    assert soc >= 0, f"no SIZ marker in {mxf_path}"
    # SIZ layout: FF51 Lsiz(2) Rsiz(2) then 8x 4-byte size params, then Csiz(2)
    csiz = soc + 2 + 2 + 2 + 4 * 8
    assert data[csiz:csiz + 2] == b"\x00\x03", "first SIZ is not 3-component"
    data[csiz:csiz + 2] = b"\x00\x04"
    open(mxf_path, "wb").write(data)


CODESTREAM_START_MARKERS = b"\xff\x4f\xff\x51"  # SOC immediately followed by SIZ


def codestream_bounds(data):
    """(start, end) of every J2K codestream in a picture MXF, located by the
    SOC+SIZ marker pair. Each one runs up to the next frame's."""
    starts = []
    at = data.find(CODESTREAM_START_MARKERS)
    while at >= 0:
        starts.append(at)
        at = data.find(CODESTREAM_START_MARKERS, at + len(CODESTREAM_START_MARKERS))
    return list(zip(starts, starts[1:] + [len(data)]))


def first_codestream(mxf_path):
    """(bytes, start, end) of the first J2K codestream in a picture MXF."""
    data = bytearray(open(mxf_path, "rb").read())
    bounds = codestream_bounds(data)
    assert bounds, f"no J2K codestream in {mxf_path}"
    return (data, *bounds[0])


def main_header_marker(data, start, end, marker):
    """Offset of a marker segment in a codestream's main header, walking the
    segments from SOC and stopping at the first SOT/SOD."""
    pos = start + 2
    while pos + 4 <= end:
        m = bytes(data[pos:pos + 2])
        if m in (b"\xff\x90", b"\xff\x93"):
            return None
        if m == marker:
            return pos
        seg = int.from_bytes(data[pos + 2:pos + 4], "big")
        if seg < 2:
            return None
        pos += 2 + seg
    return None


def patch_j2k_guard_bits(mxf_path):
    """Zero the guard-bit field of the first frame's QCD marker. RDD 52 requires
    1 guard bit at 2K, so 0 is a violation check_guard_bits_mxf reports."""
    data, s, e = first_codestream(mxf_path)
    qcd = main_header_marker(data, s, e, b"\xff\x5c")
    assert qcd is not None, f"no QCD marker in the first frame of {mxf_path}"
    sqcd = qcd + 4  # FF5C, Lqcd(2), then Sqcd
    assert data[sqcd] >> 5 == 1, f"first frame does not declare 1 guard bit ({data[sqcd] >> 5})"
    data[sqcd] &= 0x1F
    open(mxf_path, "wb").write(data)


TLM_MARKER = b"\xff\x55"
COMMENT_MARKER = b"\xff\x64"


def patch_j2k_tlm_to_comment(mxf_path):
    """Rewrite every frame's TLM marker code as a COM comment marker. Only the
    two marker bytes change, so the segment keeps its length and so do the
    codestream and the MXF's KLV lengths, and every frame loses its TLM together:
    patching frame 0 alone would leave the frames disagreeing about tlm_present
    instead."""
    data = bytearray(open(mxf_path, "rb").read())
    bounds = codestream_bounds(data)
    assert bounds, f"no J2K codestream in {mxf_path}"
    for start, end in bounds:
        tlm = main_header_marker(data, start, end, TLM_MARKER)
        assert tlm is not None, f"a codestream in {mxf_path} carries no TLM marker"
        data[tlm:tlm + len(COMMENT_MARKER)] = COMMENT_MARKER
    open(mxf_path, "wb").write(data)


COD_MARKER = b"\xff\x52"
# the multiple-component-transform flag's position in the COD segment's
# parameters: Scod(1) and the first three SGcod bytes come before it
MULTIPLE_COMPONENT_TRANSFORM_OFFSET = 4


def patch_j2k_multiple_component_transform(mxf_path):
    """Turn off the first frame's multiple-component-transform flag. Both values
    are legal T.800, the byte count is untouched, and dcpdoctor reads the flag
    only to compare frames and to summarise them, so the frames disagreeing is
    all that is left to report."""
    data, start, end = first_codestream(mxf_path)
    cod = main_header_marker(data, start, end, COD_MARKER)
    assert cod is not None, f"no COD marker in the first frame of {mxf_path}"
    at = cod + 4 + MULTIPLE_COMPONENT_TRANSFORM_OFFSET  # FF52, Lcod(2), then the parameters
    assert data[at] == 1, f"first frame of {mxf_path} already has no component transform"
    data[at] = 0
    open(mxf_path, "wb").write(data)


def patch_j2k_legacy_ffff(mxf_path):
    """Write 0xFF 0xFF into the first frame's entropy data at a byte position
    that is 254 mod 256 from the codestream start, the SMPTE Cat. 862 legacy
    decoder condition. Positions inside the first tile-part need no realignment,
    so the patch site is picked just past its SOD."""
    data, s, e = first_codestream(mxf_path)
    # walk to the first SOT, then on to its SOD
    pos = s + 2
    while bytes(data[pos:pos + 2]) != b"\xff\x90":
        pos += 2 + int.from_bytes(data[pos + 2:pos + 4], "big")
        assert pos < e, f"no SOT in the first frame of {mxf_path}"
    psot = int.from_bytes(data[pos + 6:pos + 10], "big")
    limit = s + psot if psot else e
    while bytes(data[pos:pos + 2]) != b"\xff\x93":
        pos += 2 + int.from_bytes(data[pos + 2:pos + 4], "big")
        assert pos < e, f"no SOD in the first frame of {mxf_path}"
    p = pos + 2 - s + 64  # 64 bytes into the entropy data
    p += (254 - p) % 256
    assert s + p + 2 < limit, f"no room in the first tile-part of {mxf_path}"
    data[s + p] = 0xFF
    data[s + p + 1] = 0xFF
    open(mxf_path, "wb").write(data)


def find_by_root_element(d, root):
    """The XML in `d` whose root element has this local name. Identifying a
    document by its content rather than its filename is what lets a corpus hold
    packages from more than one mastering tool: dcpwizard writes CPL_*.xml and
    PKL_*.xml, DCP-o-matic writes cpl_*.xml and pkl_*.xml."""
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not os.path.isfile(path) or not name.lower().endswith(".xml"):
            continue
        with open(path, errors="replace") as f:
            head = f.read(4096)
        # the first element that is not a declaration, comment or doctype. An
        # ASSETMAP carries a <PackingList> flag per asset, so anything looser
        # than the root element matches the wrong document.
        opening = re.search(r"<\s*(?![?!])(?:[\w.-]+:)?([\w.-]+)", head)
        if opening and opening.group(1) == root:
            return path
    raise FileNotFoundError(f"no {root} document in {d}")


def cpl_path(d):
    return find_by_root_element(d, "CompositionPlaylist")


def pkl_path(d):
    return find_by_root_element(d, "PackingList")


def am_path(d):
    # ST 429-9 allows either name, and Interop packages use the bare one
    for name in ("ASSETMAP.xml", "ASSETMAP"):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return os.path.join(d, "ASSETMAP.xml")


def read(p):
    with open(p) as f:
        return f.read()


def write(p, s):
    with open(p, "w") as f:
        f.write(s)


def assetmap_id_to_path(d):
    """Map urn-stripped asset id -> filesystem path from the ASSETMAP."""
    s = read(am_path(d))
    out = {}
    for m in re.finditer(r"<Asset>([\s\S]*?)</Asset>", s):
        block = m.group(1)
        idm = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", block)
        pm = re.search(r"<Path>([^<]+)</Path>", block)
        if idm and pm:
            out[idm.group(1)] = pm.group(1).strip()
    return out


def reseal_cpl_hashes(d):
    """Rewrite each CPL asset <Hash> from the file the ASSETMAP resolves its id
    to. A track asset carries its Hash after its Id, so the id a Hash belongs to
    is the last one before it. Without this, mutating essence leaves the CPL
    claiming the old digest while the PKL claims the new one, which dcpdoctor
    reports as cpl_pkl_hash_mismatch on top of the defect the fixture is for."""
    id2path = assetmap_id_to_path(d)
    try:
        p = cpl_path(d)
    except FileNotFoundError:
        return
    s = read(p)
    ids = [(m.end(), m.group(1)) for m in re.finditer(
        r"<(?:[\w.-]+:)?Id>\s*(urn:uuid:[0-9a-fA-F-]{36})\s*</(?:[\w.-]+:)?Id>", s)]

    def fix_hash(m):
        owner = [i for end, i in ids if end <= m.start()]
        rel = id2path.get(owner[-1]) if owner else None
        if not rel:
            return m.group(0)
        fp = os.path.join(d, rel)
        if not os.path.exists(fp):
            return m.group(0)
        prefix = m.group(1)
        return f"<{prefix}Hash>{sha1_b64(fp)}</{prefix}Hash>"

    write(p, re.sub(r"<((?:[\w.-]+:)?)Hash>[^<]*</(?:[\w.-]+:)?Hash>", fix_hash, s))


def reseal(d, reseal_cpl=True):
    """Recompute every CPL and PKL asset Hash+Size, then every ASSETMAP chunk
    Length, from the actual files so the only remaining defect is the intended
    one. Assets whose file is absent are left as-is (their hash check is skipped
    by dcpdoctor anyway). reseal_cpl=False leaves the CPL's own asset hashes
    alone, so a fixture whose defect is one of them survives the reseal while the
    PKL still records the CPL the package ships."""
    if reseal_cpl:
        reseal_cpl_hashes(d)  # before the PKL, so the PKL hashes the new CPL
    id2path = assetmap_id_to_path(d)
    pkl = pkl_path(d)
    s = read(pkl)

    def fix_asset(m):
        block = m.group(0)
        idm = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", block)
        if not idm:
            return block
        rel = id2path.get(idm.group(1))
        if not rel:
            return block
        fp = os.path.join(d, rel)
        if not os.path.exists(fp):
            return block
        h = sha1_b64(fp)
        size = os.path.getsize(fp)
        block = re.sub(r"<Hash>[^<]*</Hash>", f"<Hash>{h}</Hash>", block)
        block = re.sub(r"<Size>[^<]*</Size>", f"<Size>{size}</Size>", block)
        return block

    s = re.sub(r"<Asset>[\s\S]*?</Asset>", fix_asset, s)
    write(pkl, s)

    # DCP-o-matic writes a Length per chunk where dcpwizard writes none, so a
    # mutation that changes an XML file's size leaves a second defect behind.
    # Runs after the PKL rewrite so the PKL's own chunk gets its new size.
    def fix_chunk(m):
        block = m.group(0)
        rel = re.search(r"<Path>([^<]+)</Path>", block)
        if not rel or "<Length>" not in block:
            return block
        fp = os.path.join(d, rel.group(1))
        if not os.path.exists(fp):
            return block
        return re.sub(r"<Length>[^<]*</Length>",
                      f"<Length>{os.path.getsize(fp)}</Length>", block)

    am = am_path(d)
    write(am, re.sub(r"<Chunk>[\s\S]*?</Chunk>", fix_chunk, read(am)))


def strip_signature(xml):
    """Remove a document's Signature and the Signer beside it, whatever prefix
    they carry. Mirrors dcpwizard's own strip, so the fixture leaves the package
    a real tool would."""
    for local in ("Signature", "Signer"):
        xml = re.sub(
            r"[ \t]*<(?:[\w.-]+:)?" + local + r"[\s>][\s\S]*?</(?:[\w.-]+:)?"
            + local + r">\n?",
            "", xml, count=1)
    return xml


def side_effects(d, src):
    """Codes the corpus machinery leaves on a package whatever defect the fixture
    is for, so no fixture has to declare them by hand. Skipping the reseal leaves
    a stale chunk Length or PKL record, and the Bv2.1 four-second subtitle lead-in
    is unreachable in a 48-frame package, so any timed text at all trips it.
    Editing a signed CPL or PKL (the reseal does, on every fixture cloned from a
    signed source) breaks its signature, which dcpdoctor verifies."""
    codes = set()
    if assetmap_length_disagrees(d):
        codes.add("assetmap_size_mismatch")
    codes |= pkl_records_disagree(d)
    if carries_timed_text(d):
        codes.add("subtitle_first_event_early")
    if declared_font_unresolvable(d):
        codes.add("subtitle_font_missing")
    if signed_document_modified(d, src):
        codes.add("signature_invalid")
    return codes


def declared_font_unresolvable(d):
    """True when a subtitle document declares a LoadFont that resolves to no
    font in the package: the injected timed-text documents name a font urn no
    fixture adds an asset for, and dcpdoctor reports the glyph pass skipping."""
    ids = set(assetmap_id_to_path(d)) if os.path.isfile(am_path(d)) else set()
    for name in os.listdir(d):
        if not name.lower().endswith(".xml"):
            continue
        try:
            content = read(os.path.join(d, name))
        except (OSError, UnicodeDecodeError):
            continue
        if "<SubtitleReel" not in content and "<DCSubtitle" not in content:
            continue
        for m in re.finditer(r"<LoadFont[^>]*URI=\"([^\"]+)\"", content):
            if not os.path.isfile(os.path.join(d, m.group(1))):
                return True
        for m in re.finditer(r"<LoadFont[^>]*>([^<]+)</LoadFont>", content):
            if m.group(1).strip() not in ids:
                return True
    return False


DSIG_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"


def signed_document_modified(d, src):
    """True when a CPL or PKL carrying a ds:Signature is not byte-identical to
    the source file it was cloned from. Enveloped signatures cover the whole
    document, so any edit after signing invalidates them."""
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".xml"):
            continue
        try:
            content = read(os.path.join(d, name))
        except (OSError, UnicodeDecodeError):
            continue
        if DSIG_NAMESPACE not in content:
            continue
        original = os.path.join(src, name)
        if not os.path.isfile(original) or read(original) != content:
            return True
    return False


def carries_timed_text(d):
    try:
        cpl = read(cpl_path(d))
    except FileNotFoundError:
        return False
    return re.search(r"<(?:[\w.-]+:)?(?:MainSubtitle|ClosedCaption)\b", cpl) is not None


def pkl_records_disagree(d):
    """The PKL codes a stale Hash or Size would fire, read off the built package."""
    if not os.path.isfile(am_path(d)):
        return set()
    try:
        pkl = read(pkl_path(d))
    except FileNotFoundError:
        return set()
    id2path = assetmap_id_to_path(d)
    codes = set()
    for m in re.finditer(r"<Asset>[\s\S]*?</Asset>", pkl):
        block = m.group(0)
        idm = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", block)
        rel = id2path.get(idm.group(1)) if idm else None
        if not rel:
            continue
        fp = os.path.join(d, rel)
        if not os.path.exists(fp):
            continue
        h = re.search(r"<Hash>([^<]*)</Hash>", block)
        s = re.search(r"<Size>\s*(\d+)\s*</Size>", block)
        if h and h.group(1).strip() != sha1_b64(fp):
            codes.add("pkl_hash_mismatch")
        if s and int(s.group(1)) != os.path.getsize(fp):
            codes.add("pkl_size_mismatch")
    return codes


def assetmap_length_disagrees(d):
    """True when a chunk declares a Length the file on disk does not have.
    A mutation that resizes an XML file leaves this behind wherever Length is
    declared at all, which is DCP-o-matic's packages and not dcpwizard's, so the
    same mutation emits an extra code on one vendor and not the other."""
    am = am_path(d)
    if not os.path.isfile(am):
        return False
    for m in re.finditer(r"<Chunk>[\s\S]*?</Chunk>", read(am)):
        block = m.group(0)
        length = re.search(r"<Length>\s*(\d+)\s*</Length>", block)
        rel = re.search(r"<Path>([^<]+)</Path>", block)
        if not length or not rel:
            continue
        fp = os.path.join(d, rel.group(1).strip())
        if os.path.exists(fp) and os.path.getsize(fp) != int(length.group(1)):
            return True
    return False


# ── mutation helpers ─────────────────────────────────────────────────────────

def cpl_asset_id(d, element):
    """Bare asset id of a CPL track element. Tolerates a namespace prefix and
    any element order inside the asset, which vendors do differ on."""
    body = re.search(
        rf"<(?:[\w.-]+:)?{element}\b[^>]*>([\s\S]*?)</(?:[\w.-]+:)?{element}>",
        read(cpl_path(d)),
    ).group(1)
    return re.search(r"<(?:[\w.-]+:)?Id>\s*urn:uuid:([0-9a-fA-F-]{36})", body).group(1).lower()


def pic_id(d):
    return f"urn:uuid:{cpl_asset_id(d, 'MainPicture')}"


def edit_cpl_element(d, element, rewrite):
    """Rewrite the body of a CPL element through `rewrite`, keeping whatever
    namespace prefix it carries."""
    p = cpl_path(d)
    pattern = rf"<((?:[\w.-]+:)?){element}>([\s\S]*?)</(?:[\w.-]+:)?{element}>"

    def replace(m):
        prefix, body = m.group(1), m.group(2)
        return f"<{prefix}{element}>{rewrite(body)}</{prefix}{element}>"

    s, count = re.subn(pattern, replace, read(p), count=1)
    assert count == 1, f"no <{element}> in the CPL of {d}"
    write(p, s)


def set_picture_hash(d, value):
    """Set the CPL's MainPicture <Hash>, or remove the element when value is
    None. Any fixture calling this needs reseal_cpl=False, or the reseal writes
    the file's real digest straight back."""
    def rewrite(body):
        if value is None:
            edited, count = re.subn(
                r"[ \t]*<(?:[\w.-]+:)?Hash>[^<]*</(?:[\w.-]+:)?Hash>\n?", "", body, count=1)
        else:
            edited, count = re.subn(
                r"<((?:[\w.-]+:)?)Hash>[^<]*</(?:[\w.-]+:)?Hash>",
                rf"<\g<1>Hash>{value}</\g<1>Hash>", body, count=1)
        assert count == 1, f"the CPL MainPicture in {d} carries no <Hash>"
        return edited

    edit_cpl_element(d, "MainPicture", rewrite)


# a well-formed base64 SHA-1 that is no file's digest
WRONG_HASH = "AAAAAAAAAAAAAAAAAAAAAAAAAAA="
SUB_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444444"
# the id the mxf_asset_id_mismatch fixture puts in place of the picture asset id
REWRITTEN_PICTURE_ID = "5b17e100-1111-2222-3333-444444444477"
CCAP_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444455"
FONT_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444466"
DCST_NS = "http://www.smpte-ra.org/schemas/428-7/2010/DCST"
# the caption track element carries the digicine CC-CPL namespace, as a real
# Bv2.1 package does. The 429-7 AssetList ends in xs:any namespace="##other"
# processContents="lax", so a foreign-namespace element is schema-clean there,
# and dcpdoctor picks the schema from the root element namespace, so the CPL
# still validates as SMPTE.
CC_NS = "http://www.digicine.com/PROTO-ASDCP-CC-CPL-20070926#"


def add_timed_text(d, xml, *, filename="sub.xml", **kwargs):
    """Write a timed-text document into the package and attach a track to it."""
    write(os.path.join(d, filename), xml)
    attach_timed_text(d, filename, **kwargs)


SOUND_TRACK_END = "        </MainSound>"


def attach_timed_text(d, filename, *, element="MainSubtitle", asset_id=SUB_ID,
                      ns_decl="", entry_point=0, reel_index=0):
    """Attach a timed-text track (referencing `filename`, a loose document or a
    wrapped MXF) to reel `reel_index` and register the file in the ASSETMAP, so
    dcpdoctor's subtitle/closed-caption checks run on it. Bv2.1 §8.3.2 wants a
    zero EntryPoint on a timed-text asset; entry_point=None leaves the element
    out."""
    p = cpl_path(d)
    entry = "" if entry_point is None else f"          <EntryPoint>{entry_point}</EntryPoint>\n"
    # a track file needs a CPL <Hash>, which a loose XML asset does not. reseal
    # replaces the placeholder with the file's real digest
    hashed = (f"          <Hash>{WRONG_HASH}</Hash>\n"
              if filename.lower().endswith(".mxf") else "")
    block = (f"        <{element}{ns_decl}>\n"
             f"          <Id>{asset_id}</Id>\n"
             "          <EditRate>24 1</EditRate>\n"
             "          <IntrinsicDuration>48</IntrinsicDuration>\n"
             f"{entry}"
             "          <Duration>48</Duration>\n"
             f"{hashed}"
             f"        </{element}>\n")
    parts = read(p).split(SOUND_TRACK_END)
    assert len(parts) > reel_index + 1, f"{d} has no reel {reel_index + 1} carrying sound"
    head = SOUND_TRACK_END.join(parts[:reel_index + 1]) + SOUND_TRACK_END
    write(p, head + "\n" + block + SOUND_TRACK_END.join(parts[reel_index + 1:]))
    add_assetmap_entry(d, asset_id, filename)


def add_assetmap_entry(d, asset_id, filename):
    am = read(am_path(d))
    asset = ("    <Asset>\n"
             f"      <Id>{asset_id}</Id>\n"
             f"      <ChunkList><Chunk><Path>{filename}</Path></Chunk></ChunkList>\n"
             "    </Asset>\n")
    write(am_path(d), am.replace("  </AssetList>", asset + "  </AssetList>", 1))


def add_subtitle(d, sub_xml, **kwargs):
    add_timed_text(d, sub_xml, **kwargs)


MARKERS_ID = "urn:uuid:00000000-0000-0000-0000-0000000000aa"


def add_markers(d, markers, *, edit_rate="24 1"):
    """Attach a MainMarkers track to the first reel. It goes ahead of MainPicture,
    which is the only place the 429-7 AssetList sequence accepts it and where
    DCP-o-matic writes it. Each marker is (label, offset), offset None to leave
    the Offset element out."""
    entries = "".join(
        f"            <Marker><Label>{label}</Label>"
        f"{'' if offset is None else f'<Offset>{offset}</Offset>'}</Marker>\n"
        for label, offset in markers)
    block = ("        <MainMarkers>\n"
             f"          <Id>{MARKERS_ID}</Id>\n"
             f"          <EditRate>{edit_rate}</EditRate>\n"
             "          <IntrinsicDuration>48</IntrinsicDuration>\n"
             "          <MarkerList>\n"
             f"{entries}"
             "          </MarkerList>\n"
             "        </MainMarkers>\n")
    p = cpl_path(d)
    write(p, read(p).replace("        <MainPicture>",
                             block + "        <MainPicture>", 1))


def add_closed_caption(d, ccap_xml, **kwargs):
    """Attach a ClosedCaption track so check_timed_text_content runs the
    closed-caption limits (stricter than the subtitle ones) on the document."""
    add_timed_text(d, ccap_xml, element="cc:ClosedCaption", asset_id=CCAP_ID,
                   filename="ccap.xml", ns_decl=f' xmlns:cc="{CC_NS}"', **kwargs)


SECOND_REEL_ID = "urn:uuid:00000000-0000-0000-0000-0000000000f2"


def duplicate_first_reel(d, rewrite=None):
    """Append a copy of the first reel under a new Reel Id, optionally rewritten.
    The copy keeps the first reel's EntryPoint of 0, which check_reel_continuity
    skips, and every value the coherence and metadata checks read is the first
    reel's, so a plain duplicate leaves a two-reel CPL with nothing to report."""
    p = cpl_path(d)
    s = read(p)
    reel = re.search(r"<Reel>[\s\S]*?</Reel>", s).group(0)
    second = re.sub(r"(<Reel>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
                    rf"\g<1>{SECOND_REEL_ID}", reel, count=1)
    if rewrite:
        second = rewrite(second)
    write(p, s.replace("</ReelList>", second + "\n  </ReelList>", 1))


# the IssueDate form Deluxe QC demands and dcpdoctor checks for: yyyy-mm-ddThh:mm:ss
DCST_ISSUE_DATE = "2026-01-01T00:00:00"


def dcst(*, ns=True, sub_id=True, document_id=SUB_ID, reel_number=True, language="en",
         load_font=True, time_in="00:00:01:00", time_out="00:00:02:00", broken=False,
         lines=("hi",), placements=None, time_code_rate=24, issue_date=DCST_ISSUE_DATE,
         start_time="00:00:00:000", extra_namespace=None, padding_bytes=0):
    """Build a SMPTE DCST timed-text document, omitting or overriding parts to
    trigger a code. Each entry in `lines` becomes one <Text> element, which is
    how dcpdoctor counts displayed lines. `placements` gives each of them a
    (Valign, Vposition) pair, the spelling DCDMSubtitle-2010.xsd uses, and
    padding_bytes pads the document out with a comment no rule reads.

    Element order follows the SubtitleReelType sequence in DCDMSubtitle-2010.xsd
    (Id, ContentTitleText, IssueDate, ReelNumber, Language, EditRate,
    TimeCodeRate, StartTime, LoadFont, SubtitleList), so a document with nothing
    overridden is schema-clean and draws no finding of its own."""
    xmlns = f' xmlns="{DCST_NS}"' if ns else ' xmlns="urn:example:not-dcst"'
    if extra_namespace:
        xmlns += f' xmlns:extra="{extra_namespace}"'
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>\n<SubtitleReel{xmlns}>']
    if padding_bytes:
        parts.append(f"  <!-- {'x' * padding_bytes} -->")
    if sub_id:
        parts.append(f"  <Id>{document_id}</Id>")
    parts.append("  <ContentTitleText>CTPBase</ContentTitleText>")
    if issue_date is not None:
        parts.append(f"  <IssueDate>{issue_date}</IssueDate>")
    if reel_number:
        parts.append("  <ReelNumber>1</ReelNumber>")
    if language:
        parts.append(f"  <Language>{language}</Language>")
    parts.append("  <EditRate>24 1</EditRate>")
    if time_code_rate is not None:
        parts.append(f"  <TimeCodeRate>{time_code_rate}</TimeCodeRate>")
    if start_time is not None:
        parts.append(f"  <StartTime>{start_time}</StartTime>")
    if load_font:
        parts.append(f'  <LoadFont ID="Arial">{FONT_ID}</LoadFont>')
    parts.append("  <SubtitleList>")
    if broken:
        parts.append(f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}"><Text>hi</Broken')
        return "\n".join(parts)
    places = placements or [None] * len(lines)
    text = "".join(f"<Text{dcst_placement(place)}>{line}</Text>"
                   for line, place in zip(lines, places))
    parts.append(f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}">{text}</Subtitle>')
    parts.append("  </SubtitleList>\n</SubtitleReel>")
    return "\n".join(parts)


def dcst_placement(place):
    if place is None:
        return ""
    valign, vposition = place
    return f' Valign="{valign}" Vposition="{vposition}"'


DCSUBTITLE_CUE = (("00:00:01:000", "00:00:02:000", ("hi",)),)


def dcsubtitle(*, cues=DCSUBTITLE_CUE, font_uri="font.ttf"):
    """Build an Interop DCSubtitle document from (TimeIn, TimeOut, lines) cues.
    Interop references its font by URI rather than by asset urn, which is how the
    glyph check resolves one.

    DCSubtitle.xsd carries no targetNamespace and spells the identifier as a
    SubtitleID element ahead of MovieTitle, so a conformant document declares no
    xmlns at all and its times are three-digit ticks."""
    body = "".join(
        f'    <Subtitle SpotNumber="{n}" TimeIn="{time_in}" TimeOut="{time_out}">'
        + "".join(f"<Text>{line}</Text>" for line in lines)
        + "</Subtitle>\n"
        for n, (time_in, time_out, lines) in enumerate(cues, start=1))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<DCSubtitle Version="1.0">\n'
            f'  <SubtitleID>{SUB_ID[len("urn:uuid:"):]}</SubtitleID>\n'
            "  <MovieTitle>CTPBase</MovieTitle>\n"
            "  <ReelNumber>1</ReelNumber>\n"
            "  <Language>en</Language>\n"
            f'  <LoadFont Id="Arial" URI="{font_uri}"/>\n'
            '  <Font Id="Arial">\n'
            f"{body}"
            "  </Font>\n</DCSubtitle>\n")


SFNT_TABLE_RECORD_BYTES = 16
SFNT_HEADER_BYTES = 12


def make_font(chars, pad_to=0):
    """Minimal sfnt carrying nothing but a format-12 cmap that maps `chars` to
    sequential glyph ids. Every other code point resolves to glyph 0, which is
    what dcpdoctor's glyph-coverage check reports as missing. pad_to adds a
    second table of zeroes so the file reaches that many bytes and still parses,
    which is what an oversized font has to do to reach the size rule."""
    n = len(chars)
    sub = struct.pack(">HHIII", 12, 0, 16 + 12 * n, 0, n)
    for i, c in enumerate(chars):
        sub += struct.pack(">III", ord(c), ord(c), i + 1)
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + sub
    if not pad_to:
        header = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
        cmap_at = SFNT_HEADER_BYTES + SFNT_TABLE_RECORD_BYTES
        return header + b"cmap" + struct.pack(">III", 0, cmap_at, len(cmap)) + cmap
    header = struct.pack(">IHHHH", 0x00010000, 2, 32, 1, 0)
    tables_at = SFNT_HEADER_BYTES + 2 * SFNT_TABLE_RECORD_BYTES
    padding = pad_to - tables_at - len(cmap)
    padding -= padding % 4  # tables start on a four-byte boundary
    assert padding > 0, f"pad_to {pad_to} leaves no room beside the cmap"
    # the table directory is ordered by tag, and 'PAD ' sorts before 'cmap'
    records = (b"PAD " + struct.pack(">III", 0, tables_at, padding)
               + b"cmap" + struct.pack(">III", 0, tables_at + padding, len(cmap)))
    return header + records + bytes(padding) + cmap


def wrap_timed_text(xml, font, dest, *, asset_id, font_id=FONT_ID):
    """ST 429-5 wrap of a DCST document and one font, through asdcp-wrap. It
    resolves a LoadFont urn by scanning the XML's own directory for a filename
    carrying the dashed uuid, so both files go into a work directory of their
    own. -a fixes the MXF AssetUUID, which the CPL has to reference, and leaves
    it distinct from the document Id the descriptor records as its ResourceID."""
    with tempfile.TemporaryDirectory() as work:
        document = os.path.join(work, "sub.xml")
        write(document, xml)
        with open(os.path.join(work, font_id[len("urn:uuid:"):] + ".ttf"), "wb") as f:
            f.write(font)
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = os.pathsep.join(
            [os.path.join(ASDCP_BUILD, "src"), env.get("LD_LIBRARY_PATH", "")])
        subprocess.run(
            [ASDCP_WRAP, "-L", "-a", asset_id[len("urn:uuid:"):], document, dest],
            check=True, capture_output=True, env=env)


# ── certificate chains ───────────────────────────────────────────────────────
# ST 430-2 profile chains built with `cryptography`: a self-signed root, an
# intermediate, and a signer leaf, all sha256WithRSA / 2048-bit / e=65537, all
# sharing one Organization, each carrying its public-key thumbprint as
# dnQualifier. cert_rules.rs decides a cert's role by whether it issues another
# cert in the chain, so the leaf is the only cert that carries a defect.

CERT_ORGANIZATION = ".dci-ctp.corpus"
CERT_ORGANIZATIONAL_UNIT = ".Signature.dci-ctp.corpus"
CERT_NOT_BEFORE = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
CERT_NOT_AFTER = datetime.datetime(2040, 1, 1, tzinfo=datetime.timezone.utc)
ROOT_COMMON_NAME = ".dci-ctp.root"
INTERMEDIATE_COMMON_NAME = ".dci-ctp.intermediate"
# a 430-2 CommonName carries its role token before the first '.', so the CA CNs
# start with '.' (empty role) and only the signer leaf spells a role out
LEAF_COMMON_NAME = "CS.Signature.dci-ctp.corpus"
CA_KEY_USAGE = {"key_cert_sign": True, "crl_sign": True}
LEAF_KEY_USAGE = {"digital_signature": True, "key_encipherment": True}
KEY_USAGE_BITS = ("digital_signature", "content_commitment", "key_encipherment",
                  "data_encipherment", "key_agreement", "key_cert_sign", "crl_sign",
                  "encipher_only", "decipher_only")
# well-formed base64 of a 20-byte value that is not any key's SHA-1
WRONG_THUMBPRINT = "MTIzNDU2Nzg5MDEyMzQ1Njc4OTA="


def public_key_thumbprint(public_key):
    """Base64(SHA-1(subjectPublicKey BIT STRING payload)), the 430-2 dnQualifier.
    For an RSA key the BIT STRING payload is the DER of RSAPublicKey."""
    der = public_key.public_bytes(serialization.Encoding.DER,
                                  serialization.PublicFormat.PKCS1)
    return base64.b64encode(hashlib.sha1(der).digest()).decode()


def certificate_name(common_name, organization, dn_qualifier):
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, CERT_ORGANIZATIONAL_UNIT),
        x509.NameAttribute(NameOID.DN_QUALIFIER, dn_qualifier),
    ])


def issue_certificate(subject, key, issuer, issuer_key, serial, basic_constraints_ca,
                      key_usage):
    bits = {name: key_usage.get(name, False) for name in KEY_USAGE_BITS}
    return (x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(serial)
            .not_valid_before(CERT_NOT_BEFORE)
            .not_valid_after(CERT_NOT_AFTER)
            .add_extension(x509.BasicConstraints(ca=basic_constraints_ca, path_length=None),
                           critical=True)
            .add_extension(x509.KeyUsage(**bits), critical=True)
            .sign(issuer_key, hashes.SHA256())
            .public_bytes(serialization.Encoding.DER))


CERTIFICATE_AUTHORITIES = []


def certificate_authorities():
    """(root der, intermediate der, intermediate name, intermediate key), built
    once per run and shared by the clean baseline and every cert fixture."""
    if not CERTIFICATE_AUTHORITIES:
        root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        intermediate_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        root_name = certificate_name(ROOT_COMMON_NAME, CERT_ORGANIZATION,
                                     public_key_thumbprint(root_key.public_key()))
        intermediate_name = certificate_name(
            INTERMEDIATE_COMMON_NAME, CERT_ORGANIZATION,
            public_key_thumbprint(intermediate_key.public_key()))
        root = issue_certificate(root_name, root_key, root_name, root_key, 1, True,
                                 CA_KEY_USAGE)
        intermediate = issue_certificate(intermediate_name, intermediate_key, root_name,
                                         root_key, 2, True, CA_KEY_USAGE)
        CERTIFICATE_AUTHORITIES.extend([root, intermediate, intermediate_name,
                                        intermediate_key])
    return CERTIFICATE_AUTHORITIES


def certificate_chain(*, leaf_key_size=2048, leaf_common_name=LEAF_COMMON_NAME,
                      leaf_organization=CERT_ORGANIZATION,
                      leaf_basic_constraints_ca=False, leaf_key_usage=LEAF_KEY_USAGE,
                      leaf_dn_qualifier=None):
    """DER chain (leaf, intermediate, root). Every argument left at its default
    yields a chain that satisfies every rule in cert_rules.rs."""
    root, intermediate, intermediate_name, intermediate_key = certificate_authorities()
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=leaf_key_size)
    leaf_name = certificate_name(
        leaf_common_name, leaf_organization,
        leaf_dn_qualifier or public_key_thumbprint(leaf_key.public_key()))
    leaf = issue_certificate(leaf_name, leaf_key, intermediate_name, intermediate_key, 3,
                             leaf_basic_constraints_ca, leaf_key_usage)
    return [leaf, intermediate, root]


# a KDM DeviceList naming no device carries this one SHA-1 digest of the empty
# string, the DCI marker for "play on any trusted device"
ASSUME_TRUST_THUMBPRINT = "2jmj7l5rSw0yVb/vlWAYkK/YBwk="
# any other well-formed 20-byte digest, so the list names a device as well
NAMED_DEVICE_THUMBPRINT = base64.b64encode(hashlib.sha1(b"ctp-device").digest()).decode()
# three base64 characters decode to two bytes, short of a SHA-1 digest
SHORT_DIGEST = "AAAA"


def write_kdm_digest_variants(subcmd_dir):
    """Derive the three digest-rule KDMs from the valid one. Each edit breaks the
    document signature, which is why these run under the kdm subcommand rather
    than as package fixtures: `dcpdoctor kdm` reports every rule it can and the
    assertion is per code."""
    valid = os.path.join(subcmd_dir, "kdm_valid.xml")
    source = read(valid)
    thumbprint = f"<CertificateThumbprint>{ASSUME_TRUST_THUMBPRINT}</CertificateThumbprint>"
    assert thumbprint in source, f"{valid} carries no assume-trust thumbprint"

    write(os.path.join(subcmd_dir, "kdm_thumbprint_short.xml"),
          source.replace(thumbprint,
                         f"<CertificateThumbprint>{SHORT_DIGEST}</CertificateThumbprint>", 1))

    write(os.path.join(subcmd_dir, "kdm_named_and_assume_trust.xml"),
          source.replace(
              thumbprint,
              f"{thumbprint}\n            "
              f"<CertificateThumbprint>{NAMED_DEVICE_THUMBPRINT}</CertificateThumbprint>", 1))

    # ST 430-1 puts ContentAuthenticator straight after ContentTitleText, so the
    # schema pass stays clean and only the digest-length rule fires
    authenticated, count = re.subn(
        r"(</ContentTitleText>\n)",
        rf"\1        <ContentAuthenticator>{SHORT_DIGEST}</ContentAuthenticator>\n",
        source, count=1)
    assert count == 1, f"{valid} carries no <ContentTitleText>"
    write(os.path.join(subcmd_dir, "kdm_content_authenticator_short.xml"), authenticated)


def write_kdm_signing_chain(directory):
    """A conformant chain as PEM files for dcpwizard's kdm subcommand. Its life
    starts in 2020, so dcpwizard accepts a KDM window that is valid today: the
    chain `dcpwizard certificate chain` writes starts the day it runs, and
    dcpwizard refuses a window starting on the chain's own first day."""
    root, intermediate, intermediate_name, intermediate_key = certificate_authorities()
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = certificate_name(LEAF_COMMON_NAME, CERT_ORGANIZATION,
                                 public_key_thumbprint(leaf_key.public_key()))
    leaf = issue_certificate(leaf_name, leaf_key, intermediate_name, intermediate_key, 3,
                             False, LEAF_KEY_USAGE)
    os.makedirs(directory, exist_ok=True)

    def pem(der):
        return x509.load_der_x509_certificate(der).public_bytes(
            serialization.Encoding.PEM)

    with open(os.path.join(directory, "signer.pem"), "wb") as f:
        f.write(pem(leaf))
    with open(os.path.join(directory, "intermediate.pem"), "wb") as f:
        f.write(pem(intermediate))
    with open(os.path.join(directory, "root.pem"), "wb") as f:
        f.write(pem(root))
    with open(os.path.join(directory, "signer.key"), "wb") as f:
        f.write(leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))


def signature_with_chain(ders):
    """An enveloped ds:Signature carrying `ders` in ds:KeyInfo. The SignedInfo is
    complete enough for the 429-16 schema, but the digest and SignatureValue are
    placeholders, so signature_invalid always rides along."""
    certs = "".join(
        f"<ds:X509Certificate>{base64.b64encode(der).decode()}</ds:X509Certificate>"
        for der in ders)
    return ('<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
            "<ds:SignedInfo>"
            '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
            '<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
            '<ds:Reference URI="">'
            "<ds:Transforms>"
            '<ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
            "</ds:Transforms>"
            '<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
            f'<ds:DigestValue>{base64.b64encode(bytes(32)).decode()}</ds:DigestValue>'
            "</ds:Reference></ds:SignedInfo>"
            f"<ds:SignatureValue>{base64.b64encode(bytes(256)).decode()}</ds:SignatureValue>"
            f"<ds:KeyInfo><ds:X509Data>{certs}</ds:X509Data></ds:KeyInfo>"
            "</ds:Signature>")


def sign_cpl_with_chain(d, ders):
    p = cpl_path(d)
    write(p, read(p).replace("</CompositionPlaylist>",
                             signature_with_chain(ders) + "</CompositionPlaylist>", 1))


# ── fixtures ─────────────────────────────────────────────────────────────────
# each entry: (name, expected_codes, flags, baseline, notes, mutate_fn, reseal)
FIXTURES = []


def fixture(name, codes, flags, notes, reseal_after=True, reseal_cpl=True,
            copy_mxf=False, also=None, baseline="valid/dcp_ov",
            baseline_flags=None, src=BASE, requires=None, vendor_portable=True):
    def deco(fn):
        FIXTURES.append({
            "name": name, "codes": codes, "flags": flags, "notes": notes,
            "reseal": reseal_after, "reseal_cpl": reseal_cpl,
            "copy_mxf": copy_mxf, "fn": fn,
            "also": also or [], "baseline": baseline,
            "baseline_flags": baseline_flags, "src": src,
            "requires": requires or [], "vendor_portable": vendor_portable,
        })
        return fn
    return deco


@fixture("missing_assetmap", ["missing_assetmap"], [],
         "ASSETMAP removed", reseal_after=False)
def _(d):
    os.remove(am_path(d))


@fixture("xml_parse_error", ["xml_parse_error"], [],
         "ASSETMAP truncated mid-tag so XML parse fails", reseal_after=False)
def _(d):
    write(am_path(d), "<?xml version=\"1.0\"?><AssetMap><Id>urn:uuid:"
          "\n<broken")


@fixture("duplicate_asset_id", ["duplicate_asset_id"], [],
         "two ASSETMAP assets share one Id")
def _(d):
    s = read(am_path(d))
    # duplicate the sound asset block: its Id now appears twice, every CPL ref
    # still resolves so only DuplicateAssetId fires
    sound_id = cpl_asset_id(d, "MainSound")
    blocks = re.findall(r"[ \t]*<Asset>[\s\S]*?</Asset>\n", s)
    snd = next(b for b in blocks if sound_id in b)
    s = s.replace(snd, snd + snd, 1)
    write(am_path(d), s)


@fixture("asset_not_found", ["asset_not_found"], [],
         "ASSETMAP points the picture chunk at a nonexistent file",
         # the real picture file stays on disk with nothing referencing it
         also=["foreign_file_in_package"])
def _(d):
    picture = os.path.basename(picture_mxf(d))
    s = read(am_path(d))
    s = s.replace(f"<Path>{picture}</Path>", "<Path>missing_picture.mxf</Path>", 1)
    write(am_path(d), s)


@fixture("assetmap_invalid_name", ["assetmap_invalid_name"], [],
         "SMPTE asset map named ASSETMAP instead of ASSETMAP.xml")
def _(d):
    # reseal runs after this and am_path accepts either name, so the rename is
    # the only defect left
    os.rename(os.path.join(d, "ASSETMAP.xml"), os.path.join(d, "ASSETMAP"))


@fixture("assetmap_size_mismatch", ["assetmap_size_mismatch"], [],
         "ASSETMAP declares a chunk Length that is not the file's size",
         reseal_after=False)
def _(d):
    # ST 429-9 §7.4 lets Length be absent, and dcpwizard writes none, so the
    # violation has to be a declared Length that disagrees with the file
    def wrong_length(m):
        block = m.group(0)
        if "<Length>" in block:
            return re.sub(r"<Length>[^<]*</Length>", "<Length>1</Length>", block)
        return block.replace("</Chunk>", "  <Length>1</Length>\n        </Chunk>")

    write(am_path(d), re.sub(r"<Chunk>[\s\S]*?</Chunk>", wrong_length,
                             read(am_path(d)), count=1))


@fixture("missing_pkl", ["missing_pkl"], [],
         "PKL file and its ASSETMAP entry removed", reseal_after=False)
def _(d):
    os.remove(pkl_path(d))
    s = read(am_path(d))
    # drop the Asset block that carries PackingList=true
    s = re.sub(r"<Asset>(?:(?!</Asset>)[\s\S])*?<PackingList>true</PackingList>[\s\S]*?</Asset>\s*",
               "", s)
    write(am_path(d), s)


@fixture("pkl_hash_mismatch", ["pkl_hash_mismatch"], [],
         "PKL Hash for the picture MXF corrupted", reseal_after=False,
         # the CPL carries its own <Hash> for the same asset, so a corrupted PKL
         # hash disagrees with it as well as with the file
         also=["cpl_pkl_hash_mismatch"])
def _(d):
    s = read(pkl_path(d))
    # corrupt the first mxf asset hash (picture)
    def bad(m):
        return m.group(0).replace(re.search(r"<Hash>([^<]+)</Hash>", m.group(0)).group(1),
                                  "AAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    # target the picture asset (application/mxf, first one)
    blocks = re.split(r"(?<=</Asset>)", s)
    for i, b in enumerate(blocks):
        if "application/mxf" in b:
            blocks[i] = bad(re.match(r"[\s\S]*", b))
            break
    write(pkl_path(d), "".join(blocks))


@fixture("pkl_size_mismatch", ["pkl_size_mismatch"], [],
         "PKL Size for the picture MXF wrong (hash left correct, so only the "
         "size check fires)", reseal_after=False)
def _(d):
    s = read(pkl_path(d))
    blocks = re.split(r"(?<=</Asset>)", s)
    for i, b in enumerate(blocks):
        if "application/mxf" in b:
            blocks[i] = re.sub(r"<Size>\d+</Size>", "<Size>12345</Size>", b, count=1)
            break
    write(pkl_path(d), "".join(blocks))


@fixture("pkl_missing_asset_reference", ["pkl_missing_asset_reference"], [],
         "PKL references an Id absent from the ASSETMAP", reseal_after=False)
def _(d):
    s = read(pkl_path(d))
    extra = ("    <Asset>\n"
             "      <Id>urn:uuid:deadbeef-0000-0000-0000-000000000000</Id>\n"
             "      <Hash>AAAAAAAAAAAAAAAAAAAAAAAAAAA=</Hash>\n"
             "      <Size>1</Size>\n"
             "      <Type>application/mxf</Type>\n"
             "    </Asset>\n")
    s = s.replace("  </AssetList>", extra + "  </AssetList>", 1)
    write(pkl_path(d), s)


@fixture("missing_cpl", ["missing_cpl"], [],
         "CPL file plus its ASSETMAP and PKL entries removed")
def _(d):
    cid = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", read(cpl_path(d))).group(1)
    os.remove(cpl_path(d))
    for p in (am_path(d), pkl_path(d)):
        s = read(p)
        s = re.sub(r"<Asset>(?:(?!</Asset>)[\s\S])*?" + re.escape(cid) + r"[\s\S]*?</Asset>\s*",
                   "", s)
        write(p, s)


@fixture("cpl_missing_reel", ["cpl_missing_reel"], [],
         "CPL ReelList emptied",
         # 429-7 requires at least one Reel, so an emptied ReelList cannot
         # validate, and CompositionMetadataAsset lives inside a Reel
         also=["xml_schema_violation", "missing_required_element"])
def _(d):
    p = cpl_path(d)
    s = re.sub(r"<ReelList>[\s\S]*</ReelList>", "<ReelList></ReelList>", read(p))
    write(p, s)


@fixture("cpl_invalid_content_kind", ["cpl_invalid_content_kind"], ["--strict"],
         "ContentKind set to a non-standard value (strict)")
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<ContentKind>test</ContentKind>",
                             "<ContentKind>INVALID_KIND</ContentKind>"))


@fixture("cpl_invalid_duration", ["cpl_invalid_duration"], [],
         "MainPicture and MainSound Duration set to 0",
         # the duration the metadata asset and any marker Offset were written against
         also=["composition_metadata_asset_mismatch", "reel_too_short", "marker_invalid"])
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<Duration>48</Duration>", "<Duration>0</Duration>"))


@fixture("cpl_mismatched_durations", ["cpl_mismatched_durations"], [],
         "MainSound Duration differs from MainPicture Duration")
def _(d):
    p = cpl_path(d)
    s = read(p)
    # change only the sound reel's Duration (second occurrence)
    parts = s.split("<Duration>48</Duration>")
    s = parts[0] + "<Duration>48</Duration>" + "<Duration>24</Duration>".join(parts[1:])
    write(p, s)


@fixture("cpl_invalid_edit_rate", ["cpl_invalid_edit_rate"], ["--strict"],
         "MainPicture EditRate set to 13 1 (non-DCI, strict)",
         # the rate the metadata asset and any marker track were written against
         also=["composition_metadata_asset_mismatch", "reel_edit_rate_mismatch"])
def _(d):
    p = cpl_path(d)
    # DCP-o-matic writes a MainMarkers asset carrying its own EditRate ahead of
    # MainPicture, so the first EditRate in the reel is not the picture's
    s = re.sub(r"(<(?:[\w.-]+:)?MainPicture\b[^>]*>[\s\S]*?<(?:[\w.-]+:)?EditRate>)24 1",
               r"\g<1>13 1", read(p), count=1)
    write(p, s)


@fixture("cpl_pkl_hash_mismatch", ["cpl_pkl_hash_mismatch"], [],
         "CPL <Hash> for the picture corrupted while the PKL keeps the file's "
         "real digest, so the two disagree over an asset that is itself intact. "
         "The reseal skips the CPL's own hashes and still rewrites the PKL's "
         "record of the CPL, so no PKL code rides along.",
         reseal_cpl=False)
def _(d):
    set_picture_hash(d, WRONG_HASH)


@fixture("cpl_missing_hash", ["cpl_missing_hash"], [],
         "CPL <Hash> for the picture deleted. dcpwizard's stereoscopic CPL omits "
         "it too, which is why the code fires on the valid/dcp_3d baseline and "
         "has to be isolated on the 5.1 base instead.",
         reseal_cpl=False)
def _(d):
    set_picture_hash(d, None)


# wider than any picture the corpus wraps, and even, so the edge-parity half of
# the check stays quiet
ACTIVE_AREA_WIDTH_OVER_ESSENCE = 4096


@fixture("cpl_active_area_invalid", ["cpl_active_area_invalid"], [],
         "MainPictureActiveArea Width set to 4096 against the 2048-wide picture "
         "essence. The value is even, so only the size comparison fires, and the "
         "CompositionMetadataAsset check reads EditRate and IntrinsicDuration "
         "only, so it stays quiet.")
def _(d):
    def widen(body):
        edited, count = re.subn(r"<((?:[\w.-]+:)?)Width>[^<]*</(?:[\w.-]+:)?Width>",
                                rf"<\g<1>Width>{ACTIVE_AREA_WIDTH_OVER_ESSENCE}</\g<1>Width>",
                                body, count=1)
        assert count == 1, f"the MainPictureActiveArea in {d} carries no <Width>"
        return edited

    edit_cpl_element(d, "MainPictureActiveArea", widen)


# xs:language accepts a primary subtag of up to eight letters, so this passes the
# XSD pattern and only the RFC 5646 registry lookup rejects it
UNREGISTERED_LANGUAGE_TAG = "abcdefgh"


@fixture("cpl_invalid_language", ["cpl_invalid_language"], [],
         "MainSound carries an eight-letter primary subtag that the IANA registry "
         "does not list. ST 429-7 types Language as xs:language, whose pattern the "
         "tag satisfies, so the schema pass stays clean and the language check is "
         "the only thing that fires.")
def _(d):
    p = cpl_path(d)
    edited, count = re.subn(
        r"(\s*)</((?:[\w.-]+:)?)MainSound>",
        rf"\1  <\g<2>Language>{UNREGISTERED_LANGUAGE_TAG}</\g<2>Language>\1</\g<2>MainSound>",
        read(p), count=1)
    assert count == 1, f"no <MainSound> in the CPL of {d}"
    write(p, edited)


NON_ISDCF_TITLE = "BadNameNoFields"


@fixture("isdcf_naming_violation", ["isdcf_naming_violation"], [],
         "ContentTitleText replaced with a non-ISDCF name. The CPL and PKL "
         "AnnotationText are rewritten with it, so the name is the only defect "
         "and the two annotation-consistency checks stay quiet")
def _(d):
    for p in (cpl_path(d), pkl_path(d)):
        s = re.sub(r"<((?:[\w.-]+:)?)AnnotationText>[^<]*</(?:[\w.-]+:)?AnnotationText>",
                   rf"<\g<1>AnnotationText>{NON_ISDCF_TITLE}</\g<1>AnnotationText>", read(p))
        s = re.sub(r"<((?:[\w.-]+:)?)ContentTitleText>[^<]*</(?:[\w.-]+:)?ContentTitleText>",
                   rf"<\g<1>ContentTitleText>{NON_ISDCF_TITLE}</\g<1>ContentTitleText>", s)
        write(p, s)


PICTURE_KEY_ID = "urn:uuid:00000000-0000-0000-0000-0000000000bb"
SOUND_KEY_ID = "urn:uuid:00000000-0000-0000-0000-0000000000bc"


def add_key_id(d, element, key_id):
    p = cpl_path(d)
    write(p, read(p).replace(
        f"</{element}>",
        f"  <KeyId>{key_id}</KeyId>\n        </{element}>", 1))


@fixture("encrypted_no_kdm", ["encryption_detected", "kdm_required"], [],
         "KeyId added to both MainPicture and MainSound, no KDM present. Both "
         "tracks are keyed so the composition is wholly encrypted and only the "
         "missing KDM is left",
         # an encrypted package must be signed, and the injected KeyId is not schema-clean
         also=["dcp_not_signed", "xml_schema_violation"])
def _(d):
    add_key_id(d, "MainPicture", PICTURE_KEY_ID)
    add_key_id(d, "MainSound", SOUND_KEY_ID)


@fixture("partially_encrypted", ["partially_encrypted"], [],
         "KeyId added to MainPicture alone, so the composition mixes encrypted "
         "picture with clear sound",
         # everything an encrypted CPL brings with it, minus the KDM it has no
         # place to come from, and the injected KeyId is not schema-clean
         also=["encryption_detected", "kdm_required", "dcp_not_signed",
               "xml_schema_violation"])
def _(d):
    add_key_id(d, "MainPicture", PICTURE_KEY_ID)


@fixture("cpl_annotation_text_mismatch", ["cpl_annotation_text_mismatch"], [],
         "CPL AnnotationText no longer matches its own ContentTitleText. The PKL "
         "is left alone, whose check compares against ContentTitleText, so only "
         "the CPL-side check fires")
def _(d):
    p = cpl_path(d)
    write(p, re.sub(r"<((?:[\w.-]+:)?)AnnotationText>[^<]*</(?:[\w.-]+:)?AnnotationText>",
                    r"<\g<1>AnnotationText>SomethingElse</\g<1>AnnotationText>",
                    read(p), count=1))


@fixture("pkl_annotation_text_mismatch", ["pkl_annotation_text_mismatch"], [],
         "PKL AnnotationText no longer matches the CPL's ContentTitleText")
def _(d):
    p = pkl_path(d)
    write(p, re.sub(r"<((?:[\w.-]+:)?)AnnotationText>[^<]*</(?:[\w.-]+:)?AnnotationText>",
                    r"<\g<1>AnnotationText>SomethingElse</\g<1>AnnotationText>",
                    read(p), count=1))


@fixture("markers_bad", ["marker_missing", "marker_invalid"], ["--strict"],
         "The all-markers baseline with the FFMC and LFMC entries dropped and "
         "the FFOC Offset removed. Under --strict dcpdoctor reports every "
         "recommended marker a CPL leaves out, so the baseline has to carry the "
         "whole set for the missing pair to mean anything",
         src=ALL_MARKERS, baseline=ALL_MARKERS_BASELINE, vendor_portable=False,
         # the schema demands AnnotationText or Offset, so the missing Offset
         # this fixture is for cannot help violating it too
         also=["xml_schema_violation"])
def _(d):
    p = cpl_path(d)
    s = read(p)
    for label in ("FFMC", "LFMC"):
        s = re.sub(r"[ \t]*<Marker>\s*<Label>" + label + r"</Label>[\s\S]*?</Marker>\n",
                   "", s, count=1)
    s = re.sub(r"(<Label>FFOC</Label>)\s*<Offset>\d+</Offset>", r"\1", s, count=1)
    write(p, s)


@fixture("reel_edit_rate_mismatch", ["reel_edit_rate_mismatch"], [],
         "MainMarkers EditRate differs from the reel's picture EditRate")
def _(d):
    p = cpl_path(d)
    existing = re.search(
        r"<(?:[\w.-]+:)?MainMarkers[\s>][\s\S]*?</(?:[\w.-]+:)?MainMarkers>", read(p))
    if existing:
        s = read(p)
        block = re.sub(r"<EditRate>[^<]*</EditRate>", "<EditRate>13 1</EditRate>",
                       existing.group(0), count=1)
        write(p, s[:existing.start()] + block + s[existing.end():])
    else:
        # FFOC and LFOC are both present, so the marker checks stay quiet and the
        # rate is the only defect
        add_markers(d, [("FFOC", 1), ("LFOC", 47)], edit_rate="13 1")


@fixture("composition_metadata_asset_mismatch",
         ["composition_metadata_asset_mismatch"], [],
         "CompositionMetadataAsset IntrinsicDuration differs from the reel picture")
def _(d):
    p = cpl_path(d)

    def bump(m):
        return re.sub(
            r"<(?:[\w.-]+:)?IntrinsicDuration>(\d+)</(?:[\w.-]+:)?IntrinsicDuration>",
            lambda n: f"<IntrinsicDuration>{int(n.group(1)) + 1}</IntrinsicDuration>",
            m.group(0), count=1)

    write(p, re.sub(
        r"<(?:[\w.-]+:)?CompositionMetadataAsset[\s>][\s\S]*?"
        r"</(?:[\w.-]+:)?CompositionMetadataAsset>",
        bump, read(p), count=1))


@fixture("cross_ref_broken", ["cross_ref_broken"], ["--ov", "@."],
         "MainPicture Id points at an asset absent from ASSETMAP/PKL; --ov points "
         "at the fixture itself so the id resolves in neither package nor OV and "
         "cross_ref_broken fires (without --ov it downgrades to supplemental_ov_not_provided)")
def _(d):
    p = cpl_path(d)
    s = re.sub(r"(<MainPicture>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
               r"\1urn:uuid:deadbeef-0000-0000-0000-000000000000", read(p), count=1)
    write(p, s)


@fixture("supplemental_opl", ["supplemental_opl_missing"], [],
         "CPL carries an OPL marker (supplemental/version file)",
         # the injected supplemental block is not schema-clean
         also=["xml_schema_violation"])
def _(d):
    p = cpl_path(d)
    s = read(p).replace("</ReelList>",
                        "</ReelList>\n  <OPL>urn:uuid:00000000-0000-0000-0000-0000000000cc</OPL>", 1)
    write(p, s)


@fixture("supplemental_no_ov", ["supplemental_ov_not_provided"], [],
         "Supplemental CPL references an asset not in this package and no --ov given",
         # the injected supplemental block is not schema-clean
         also=["supplemental_opl_missing", "xml_schema_violation"])
def _(d):
    p = cpl_path(d)
    s = read(p)
    s = s.replace("</ReelList>",
                  "</ReelList>\n  <OPL>urn:uuid:00000000-0000-0000-0000-0000000000cc</OPL>", 1)
    s = re.sub(r"(<MainPicture>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
               r"\1urn:uuid:deadbeef-0000-0000-0000-000000000000", s, count=1)
    write(p, s)


@fixture("reel_discontinuity", ["reel_discontinuity"], [],
         "Second reel EntryPoint does not follow the first")
def _(d):
    duplicate_first_reel(d, lambda reel: reel.replace(
        "<EntryPoint>0</EntryPoint>", "<EntryPoint>100</EntryPoint>"))


@fixture("stereo_mismatch", ["stereo_mismatch"], [],
         "MainStereoscopicPicture has LeftEye but no RightEye",
         # the stereoscopic block swapped in is not schema-clean
         also=["xml_schema_violation"])
def _(d):
    p = cpl_path(d)
    pid = pic_id(d)
    block = ("        <MainStereoscopicPicture>\n"
             f"          <Id>{pid}</Id>\n"
             "          <EditRate>24 1</EditRate>\n"
             "          <IntrinsicDuration>48</IntrinsicDuration>\n"
             "          <Duration>48</Duration>\n"
             f"          <LeftEye><Id>{pid}</Id></LeftEye>\n"
             "        </MainStereoscopicPicture>\n")
    s = read(p).replace("        <MainSound>", block + "        <MainSound>", 1)
    write(p, s)


@fixture("signature_invalid", ["signature_invalid"], [],
         "CPL carries an enveloped ds:Signature with a bogus SignatureValue",
         # the SignedInfo carries placeholder digests, so it does not validate either
         also=["xml_schema_violation"])
def _(d):
    p = cpl_path(d)
    sig = ('<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
           '<ds:SignedInfo><ds:Reference URI="">'
           '<ds:DigestValue>AAAA</ds:DigestValue></ds:Reference></ds:SignedInfo>'
           '<ds:SignatureValue>bogus</ds:SignatureValue></ds:Signature>')
    s = read(p).replace("</CompositionPlaylist>", sig + "</CompositionPlaylist>", 1)
    write(p, s)


@fixture("certificate_chain_broken", ["certificate_chain_broken"], [],
         "ds:Signature embeds a certificate blob that is valid base64 but not DER",
         # the SignedInfo carries placeholder digests, so it does not validate either
         also=["signature_invalid", "xml_schema_violation"])
def _(d):
    p = cpl_path(d)
    badcert = base64.b64encode(b"this is not a certificate").decode()
    sig = ('<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
           '<ds:SignedInfo><ds:Reference URI="">'
           '<ds:DigestValue>AAAA</ds:DigestValue></ds:Reference></ds:SignedInfo>'
           '<ds:SignatureValue>bogus</ds:SignatureValue>'
           '<ds:KeyInfo><ds:X509Data><ds:X509Certificate>' + badcert +
           '</ds:X509Certificate></ds:X509Data></ds:KeyInfo></ds:Signature>')
    s = read(p).replace("</CompositionPlaylist>", sig + "</CompositionPlaylist>", 1)
    write(p, s)


CERTIFICATE_BASELINE = "valid/dcp_certificate_chain"
# the injected signature never verifies, so every cert fixture emits this too
CERTIFICATE_ALSO_EMITS = ["signature_invalid"]


@fixture("certificate_basic_constraints_invalid",
         ["certificate_basic_constraints_invalid"], [],
         "Signer leaf carries Basic Constraints cA=TRUE. Every other field of "
         "the chain is conformant", also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_basic_constraints_ca=True))


@fixture("certificate_key_usage_invalid", ["certificate_key_usage_invalid"], [],
         "Signer leaf Key Usage asserts keyEncipherment but not digitalSignature",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_key_usage={"key_encipherment": True}))


@fixture("certificate_key_size_invalid", ["certificate_key_size_invalid"], [],
         "Signer leaf holds a 3072-bit RSA key, off the 430-2 profile's 2048",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_key_size=3072))


@fixture("certificate_role_invalid", ["certificate_role_invalid"], [],
         "Signer leaf CommonName starts with '.', so its role token is empty and "
         "matches the CA roles instead of being distinct",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_common_name=".Signature.dci-ctp.corpus"))


@fixture("certificate_thumbprint_invalid", ["certificate_thumbprint_invalid"], [],
         "Signer leaf dnQualifier is a well-formed base64 value that is not the "
         "SHA-1 of its own public key", also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_dn_qualifier=WRONG_THUMBPRINT))


@fixture("certificate_organization_inconsistent",
         ["certificate_organization_inconsistent"], [],
         "Signer leaf Organization differs from the two CA certificates'",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_organization=".other-org.corpus"))


@fixture("sound_no_mca", ["sound_invalid_channel_count"], ["--check-mxf"],
         "Real mono DCP whose sound MXF carries no ST 429-12 MCA subdescriptors; "
         "the 5.1 base (dcp_ov) is labeled so the INFO is absent there. dcpdoctor "
         "reads the MXF subdescriptors, not the CPL, so this is non-vacuous.",
         src=MONO, copy_mxf=True,
         # 1 channel is neither 8 nor 16, and dcpwizard's mono build writes no
         # CompositionMetadataAsset at all
         also=["distributor_audio_channel_count", "missing_required_element"])
def _(d):
    pass  # the mono build already lacks MCA soundfield labels


@fixture("foreign_file_in_package", ["foreign_file_in_package"], [],
         "A file in the package directory that the ASSETMAP does not reference")
def _(d):
    write(os.path.join(d, "stray_notes.txt"), "not referenced by the ASSETMAP\n")


@fixture("empty_file_in_package", ["empty_file_in_package"], [],
         "A zero-byte file in the package directory")
def _(d):
    open(os.path.join(d, "empty.dat"), "wb").close()


@fixture("invalid_uuid", ["invalid_uuid"], [],
         "A urn:uuid: token in the CPL is malformed; compliance::check_uuids "
         "(wired into validate) flags it", reseal_after=False,
         # a malformed uuid also breaks the schema's urn pattern
         also=["xml_schema_violation"])
def _(d):
    # corrupt the reel Id into a malformed urn:uuid the uuid check scans
    p = cpl_path(d)
    s = re.sub(r"(<Reel>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
               r"\1urn:uuid:not-a-valid-uuid", read(p), count=1)
    write(p, s)


@fixture("xml_schema_violation", ["xml_schema_violation"], [],
         "CPL carries an element not allowed by the 429-16 CPL schema; schema "
         "validation is on by default (vendored schemas/), so no env var is needed",
         reseal_after=False)
def _(d):
    p = cpl_path(d)
    s = read(p).replace("</ContentTitleText>",
                        "</ContentTitleText><BogusElement>x</BogusElement>", 1)
    write(p, s)


@fixture("sound_invalid_sample_rate", ["sound_invalid_sample_rate"], ["--check-mxf"],
         "Sound MXF AudioSamplingRate KLV byte-patched 48000/1 -> 44100/1 so the "
         "prober reports a non-DCI rate", copy_mxf=True)
def _(d):
    # rational 48000/1 = 0000BB80 00000001 -> 44100/1 = 0000AC44 00000001
    patch_bytes(sound_mxf(d), "0000BB8000000001", "0000AC4400000001")


@fixture("sound_invalid_quantization", ["sound_invalid_quantization"],
         ["--check-mxf"],
         "Sound MXF QuantizationBits (local tag 3d01) patched to 16 bits, which "
         "DCI does not allow",
         copy_mxf=True,
         # block align is derived from the bit depth, so changing one contradicts the other
         also=["sound_invalid_block_align"])
def _(d):
    patch_local_tag(sound_mxf(d), "3d01", 4, 16)


@fixture("sound_invalid_block_align", ["sound_invalid_block_align"],
         ["--check-mxf"],
         "Sound MXF WaveAudioDescriptor BlockAlign (local tag 3d0a) patched one "
         "channel short of channels x bytes-per-sample",
         copy_mxf=True)
def _(d):
    mxf = sound_mxf(d)
    bytes_per_sample = local_tag_value(mxf, "3d01", 4) // 8
    channels = local_tag_value(mxf, "3d07", 4)
    patch_local_tag(mxf, "3d0a", 2, (channels - 1) * bytes_per_sample)


@fixture("stereo_framerate", ["stereo_mismatch"], ["--check-mxf"],
         "Real 3D DCP whose MainStereoscopicPicture FrameRate is not twice the "
         "EditRate (ST 429-10); part-1b relationship check", src=THREE_D,
         # the unmutated 3D package, so its own quirks (no picture <Hash> in the
         # CPL, F-3D in the ISDCF name) are not read as this fixture's doing
         baseline="valid/dcp_3d")
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<FrameRate>48 1</FrameRate>",
                             "<FrameRate>24 1</FrameRate>", 1))


@fixture("aux_data_atmos", ["aux_data_detected"], ["--check-mxf"],
         "Real Atmos DCP; the ST 429-18 AuxData track surfaces aux_data_detected "
         "(absent on the non-Atmos base)", src=ATMOS, reseal_after=False)
def _(d):
    pass  # the atmos build already carries the AuxData track


@fixture("subtitle_parse_error", ["subtitle_parse_error"], [],
         "MainSubtitle references a malformed DCST XML document",
         # a document that does not parse cannot validate against a schema either
         also=["xml_schema_violation"])
def _(d):
    add_subtitle(d, dcst(broken=True))


@fixture("subtitle_invalid_timing", ["subtitle_invalid_timing"], [],
         "Subtitle cue TimeIn is not before TimeOut",
         # a bad TimeOut makes the cue's duration wrong too
         also=["subtitle_duration"])
def _(d):
    add_subtitle(d, dcst(time_in="00:00:02:00", time_out="00:00:01:00"))


@fixture("subtitle_font_missing", ["subtitle_font_missing"], [],
         "Subtitle reel has no LoadFont element")
def _(d):
    add_subtitle(d, dcst(load_font=False))


@fixture("subtitle_missing_id", ["missing_required_element"], [],
         "Subtitle reel is missing its SubtitleID/Id identifier",
         # the DCST schema requires Id first, so dropping it also breaks the schema
         also=["xml_schema_violation"])
def _(d):
    add_subtitle(d, dcst(sub_id=False))


@fixture("subtitle_wrong_namespace", ["smpte_namespace_wrong"], [],
         "SMPTE DCP's subtitle document does not use the SMPTE DCST namespace")
def _(d):
    add_subtitle(d, dcst(ns=False))


@fixture("mxf_unreadable", ["mxf_unreadable"], ["--check-mxf"],
         "Picture MXF truncated to non-MXF bytes; PKL resealed to it",
         copy_mxf=True,
         # a truncated file is both unreadable and structurally invalid
         also=["mxf_invalid_structure"])
def _(d):
    mxf = picture_mxf(d)
    with open(mxf, "wb") as f:
        f.write(b"NOT AN MXF FILE" * 4)


@fixture("manifest_size_mismatch", ["mxf_hash_mismatch"], ["--manifest", "@refmanifest_bad.json"],
         "validate --manifest with a reference size that differs from the picture MXF",
         reseal_after=False, baseline="invalid/manifest_size_mismatch",
         baseline_flags=["--manifest", "@refmanifest_good.json"])
def _(d):
    mxf = os.path.basename(picture_mxf(d))
    real = os.path.getsize(os.path.join(d, mxf))
    write(os.path.join(d, "refmanifest_good.json"),
          json.dumps({"assets": [{"filename": mxf, "size": real}]}))
    write(os.path.join(d, "refmanifest_bad.json"),
          json.dumps({"assets": [{"filename": mxf, "size": real + 999}]}))


@fixture("bv21_pkl_no_xml_ext", ["smpte_naming_violation"], ["--bv21"],
         "PKL file renamed to drop .xml; BV2.1 flags the naming (--bv21 also adds "
         "advisory marker/element notes, not asserted)", reseal_after=False,
         also=["marker_missing", "missing_required_element", "sound_invalid_channel_count"])
def _(d):
    pkl = pkl_path(d)
    newname = os.path.basename(pkl)[:-4]  # strip .xml
    os.rename(pkl, os.path.join(d, newname))
    s = read(am_path(d))
    s = s.replace(os.path.basename(pkl), newname)
    write(am_path(d), s)


@fixture("mxf_invalid_structure", ["mxf_invalid_structure"], ["--check-mxf"],
         "Picture MXF footer partition pack key corrupted so the SMPTE 377-1 "
         "footer is absent; validate --check-mxf flags the structure. The header "
         "still parses, so read_mxf_info succeeds and no mxf_unreadable fires.",
         copy_mxf=True)
def _(d):
    break_mxf_footer(picture_mxf(d))


@fixture("interop_namespace_wrong", ["interop_namespace_wrong"], [],
         "Interop package whose subtitle document uses the SMPTE DCST namespace; "
         "validate_subtitle runs with Standard::Interop and flags the non-Interop "
         "namespace. The SMPTE equivalent is subtitle_wrong_namespace.",
         reseal_after=False, src=DOM_INTEROP, baseline="valid/dcp_dom_interop")
def _(d):
    # the source package is really Interop. dcpdoctor takes the standard from the
    # asset map's namespace, so renaming the file to ASSETMAP proves nothing
    add_subtitle(d, dcst(ns=True))  # SMPTE DCST ns, wrong for an Interop package
    reseal(d)


@fixture("unencrypted_dcp_not_signed", ["unencrypted_dcp_not_signed"], [],
         "The signed baseline with both signatures removed. Every other package "
         "here is unsigned already, so this is the only source whose baseline "
         "does not fire the code and make the fixture vacuous.",
         src=SIGNED_BASE, baseline="valid/dcp_signed", reseal_after=False)
def _(d):
    # both, then reseal: stripping only the CPL would leave the PKL's hash of it
    # stale, and resealing a signed PKL would leave that signature stale in turn
    for path in (cpl_path(d), pkl_path(d)):
        write(path, strip_signature(read(path)))
    reseal(d)


@fixture("dcp_not_signed", ["dcp_not_signed"], [],
         "The encrypted and signed baseline with both signatures removed: it "
         "keeps its KeyIds but loses the CPL/PKL ds:Signature, so "
         "check_dcp_signed fires.",
         src=ENCRYPTED_SIGNED_BASE, reseal_after=False,
         also=["encryption_detected", "kdm_required"],
         baseline=ENCRYPTED_SIGNED_BASELINE)
def _(d):
    # both, then reseal, for the reason unencrypted_dcp_not_signed gives
    for path in (cpl_path(d), pkl_path(d)):
        write(path, strip_signature(read(path)))
    reseal(d)


@fixture("picture_invalid_resolution",
         ["picture_invalid_resolution", "j2k_invalid_profile"],
         ["--check-mxf", "--strict"],
         "1920x1080 non-DCI J2K (grok, no cinema profile) wrapped by the vendored "
         "asdcp-wrap and swapped in for the picture MXF; the MXF descriptor "
         "resolution trips picture_invalid_resolution and the plain codestream "
         "trips j2k_invalid_profile", copy_mxf=True, requires=[NONDCI_MXF],
         # everything else the substituted essence brings with it: its own
         # AssetUUID, 1920 pixels against the CPL's 2048-wide active area, and a
         # plain grok codestream with 2 guard bits and no TLM marker
         also=["mxf_asset_id_mismatch", "cpl_active_area_invalid",
               "j2k_guard_bits", "j2k_missing_tlm"])
def _(d):
    replace_picture_mxf(d, NONDCI_MXF)


@fixture("j2k_invalid_component_count", ["j2k_invalid_component_count"],
         ["--check-mxf", "--deep-j2k"],
         "Picture MXF first-frame SIZ Csiz byte-patched 3 -> 4 so the codestream "
         "declares 4 components (asdcp-wrap refuses a 4-component essence, so the "
         "count is patched into a valid 3-component wrap)", copy_mxf=True,
         # only frame 0 is patched, so frame 1 onward disagrees with it
         also=["j2k_parameters_vary"])
def _(d):
    patch_j2k_component_count(picture_mxf(d))


@fixture("j2k_missing_tlm", ["j2k_missing_tlm"], ["--check-mxf", "--deep-j2k"],
         "Every frame's TLM marker code rewritten as a COM comment marker of the "
         "same length, so the codestream and the MXF's KLV lengths are unchanged "
         "and the essence simply carries no tile-part lengths. Doing it to every "
         "frame is what keeps j2k_parameters_vary quiet.", copy_mxf=True)
def _(d):
    patch_j2k_tlm_to_comment(picture_mxf(d))


@fixture("j2k_parameters_vary", ["j2k_parameters_vary"],
         ["--check-mxf", "--deep-j2k"],
         "First frame's COD turns the multiple-component transform off where "
         "every other frame has it on. It is the one parameter the per-frame "
         "comparison holds constant that no other check reads, so the frames "
         "disagreeing is the only defect. Codeblock size and decomposition "
         "levels both draw a j2k_invalid_profile of their own.", copy_mxf=True)
def _(d):
    patch_j2k_multiple_component_transform(picture_mxf(d))


@fixture("picture_invalid_frame_rate", ["picture_invalid_frame_rate"], [],
         "IMF IMP whose CPL EditRate (25 1) differs from the 24 fps picture "
         "essence; the IMF validate path (imf.rs) flags the pic/edit-rate "
         "mismatch", src=IMF_SRC, reseal_after=False, baseline="valid/imf_ov",
         requires=[IMF_SRC])
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<EditRate>24 1</EditRate>", "<EditRate>25 1</EditRate>"))


@fixture("non_ascii_filename", ["non_ascii_filename"], [],
         "A file whose name carries non-ASCII characters, which "
         "check_non_ascii_names "
         "scans every name in the package directory. The file is unreferenced, so "
         "the foreign-file note rides along.",
         also=["foreign_file_in_package"])
def _(d):
    write(os.path.join(d, "café_notes.txt"), "non-ascii filename\n")


@fixture("reel_too_short", ["reel_too_short"], [],
         "Reel picture/sound Duration cut to 12 frames at 24 fps (0.5s), under the "
         "ST 429-7 one-second minimum. Both tracks are cut together so the "
         "durations stay coherent and only the length check fires.",
         # the duration the metadata asset and any marker Offset were written against
         also=["composition_metadata_asset_mismatch", "marker_invalid"])
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<Duration>48</Duration>", "<Duration>12</Duration>"))


@fixture("main_sound_config_invalid", ["main_sound_config_invalid"], [],
         "CompositionMetadataAsset MainSoundConfiguration set to the literal "
         "'None' easyDCP emits, which has no soundfield/channels form")
def _(d):
    p = cpl_path(d)
    s = re.sub(r"(<meta:MainSoundConfiguration>)[^<]*(</meta:MainSoundConfiguration>)",
               r"\1None\2", read(p))
    write(p, s)


@fixture("subtitle_frame_rate_mismatch", ["subtitle_frame_rate_mismatch"], [],
         "Subtitle document declares TimeCodeRate 25 against the 24 fps "
         "composition edit rate (ST 428-7 §5.9)")
def _(d):
    add_subtitle(d, dcst(time_code_rate=25))


@fixture("subtitle_line_count", ["subtitle_line_count"], [],
         "One subtitle cue carries four <Text> lines, one over the Bv2.1 §7.2.7 "
         "limit of three")
def _(d):
    add_subtitle(d, dcst(lines=("one", "two", "three", "four")))


@fixture("subtitle_line_length", ["subtitle_line_length"], [],
         "One subtitle line is 85 characters, over the 79-character maximum")
def _(d):
    add_subtitle(d, dcst(lines=("x" * 85,)))


@fixture("subtitle_glyph_missing", ["subtitle_glyph_missing"], [],
         "Interop DCSubtitle whose LoadFont URI resolves to a minimal sfnt with a "
         "cmap covering only the ASCII the cue uses, so the star has no glyph. "
         "This exercises the URI form. The SMPTE ST 428-7 form (LoadFont carrying "
         "the font asset urn as element text) resolves through the ASSETMAP.",
         reseal_after=False, src=DOM_INTEROP, baseline="valid/dcp_dom_interop")
def _(d):
    # the source package is really Interop. dcpdoctor takes the standard from the
    # asset map's namespace, so renaming the file to ASSETMAP proves nothing
    with open(os.path.join(d, "font.ttf"), "wb") as f:
        f.write(make_font(["H", "i", " "]))
    add_assetmap_entry(d, FONT_ID, "font.ttf")
    add_timed_text(d, dcsubtitle(cues=[("00:00:01:000", "00:00:02:000", ("Hi ★",))]))
    reseal(d)


@fixture("closed_caption_line_count", ["closed_caption_line_count"], [],
         "One closed-caption cue carries four <Text> lines, one over the "
         "Bv2.1 §7.2.6 limit of "
         "three (an error for captions, a warning for subtitles)")
def _(d):
    add_closed_caption(d, dcst(lines=("one", "two", "three", "four")))


@fixture("closed_caption_line_length", ["closed_caption_line_length"], [],
         "One closed-caption line is 40 characters, over the 32-character limit")
def _(d):
    add_closed_caption(d, dcst(lines=("a" * 40,)))


@fixture("closed_caption_charset", ["closed_caption_charset"], [],
         "Closed-caption text uses a character outside the ISDCF Doc 9 set "
         "(ISO 8859-1 plus U+266A). The music note in the same line must not be "
         "flagged")
def _(d):
    add_closed_caption(d, dcst(lines=("♪ music ★",)))


@fixture("subtitle_entry_point", ["subtitle_entry_point"], [],
         "MainSubtitle reel asset carries no <EntryPoint>, which Bv2.1 §8.3.2 "
         "requires on a timed-text track")
def _(d):
    add_subtitle(d, dcst(), entry_point=None)


@fixture("closed_caption_entry_point", ["closed_caption_entry_point"], [],
         "ClosedCaption reel asset carries no <EntryPoint>")
def _(d):
    add_closed_caption(d, dcst(), entry_point=None)


@fixture("subtitle_invalid_issue_date", ["subtitle_invalid_issue_date"], [],
         "Subtitle document IssueDate carries a UTC offset, so it is a valid "
         "xs:dateTime but not the bare yyyy-mm-ddThh:mm:ss Deluxe QC demands")
def _(d):
    add_subtitle(d, dcst(issue_date="2026-01-01T00:00:00Z"))


@fixture("subtitle_empty_text", ["subtitle_empty_text"], [],
         "Subtitle cue carries a <Text> element with nothing in it")
def _(d):
    add_subtitle(d, dcst(lines=("",)))


@fixture("subtitle_namespace_count", ["subtitle_namespace_count"], [],
         "Subtitle root declares a second namespace beside the DCST one, which "
         "is how a document assembled from two schema versions reads")
def _(d):
    add_subtitle(d, dcst(extra_namespace="urn:example:second-namespace"))


# both lines bottom-aligned, where VPosition is measured upward from the bottom,
# so the second one is drawn above the first and the pair reads bottom-up
CCAP_BOTTOM_UP_LINES = (("bottom", 10), ("bottom", 20))


@fixture("closed_caption_layout", ["closed_caption_layout"], [],
         "One closed-caption cue lists its two lines out of the order they "
         "appear on screen: both are bottom-aligned and the second sits above "
         "the first. Two lines stay inside every count and length limit, so the "
         "layout is the only thing wrong with the cue")
def _(d):
    add_closed_caption(d, dcst(lines=("first", "second"),
                               placements=CCAP_BOTTOM_UP_LINES))


# over the 256 KiB Bv2.1 cap on closed-caption XML with room for the document
CCAP_PADDING_BYTES = 300 * 1024


@fixture("timed_text_size_exceeded", ["timed_text_size_exceeded"], [],
         "Closed-caption document padded past the Bv2.1 256 KiB cap with an XML "
         "comment. Padding with more cues instead would trip the cue and line "
         "rules long before the byte count")
def _(d):
    add_closed_caption(d, dcst(padding_bytes=CCAP_PADDING_BYTES))


@fixture("subtitle_missing_from_reel", ["subtitle_missing_from_reel"], [],
         "Two-reel composition carrying a MainSubtitle on the first reel only. "
         "The second reel is a copy of the first, so every per-reel check reads "
         "the same values twice and the missing subtitle is the only defect")
def _(d):
    duplicate_first_reel(d)
    add_subtitle(d, dcst())


@fixture("closed_caption_count_mismatch", ["closed_caption_count_mismatch"], [],
         "Two-reel composition carrying a ClosedCaption on the first reel only, "
         "so the reels disagree over how many captions they hold")
def _(d):
    duplicate_first_reel(d)
    add_closed_caption(d, dcst())


SECOND_SUB_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444488"


@fixture("subtitle_language_mismatch", ["subtitle_language_mismatch"], [],
         "Two-reel composition whose two MainSubtitle documents declare "
         "different <Language>. Both reels carry a subtitle, so the "
         "missing-from-reel check stays quiet")
def _(d):
    duplicate_first_reel(d)
    add_subtitle(d, dcst(language="en"))
    add_subtitle(d, dcst(language="fr", document_id=SECOND_SUB_ID),
                 asset_id=SECOND_SUB_ID, filename="sub2.xml", reel_index=1)


# over the 640 KiB a player handles reliably and far under the 10 MiB Bv2.1 caps
# the aggregate at, so the warning fires on its own
OVERSIZED_FONT_BYTES = 700 * 1024
TIMED_TEXT_MXF_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444499"


@fixture("subtitle_font_too_large", ["subtitle_font_too_large"], [],
         "MainSubtitle wrapped as an ST 429-5 MXF carrying a 700 KiB font, over "
         "the 640 KiB that plays back reliably. The rule only runs on wrapped "
         "essence, so a loose XML asset cannot reach it. The font is the sfnt "
         "the glyph fixture uses padded with a second table, so it still parses "
         "and still covers the cue",
         requires=[ASDCP_WRAP])
def _(d):
    wrap_timed_text(dcst(), make_font(["h", "i"], pad_to=OVERSIZED_FONT_BYTES),
                    os.path.join(d, "sub.mxf"), asset_id=TIMED_TEXT_MXF_ID)
    attach_timed_text(d, "sub.mxf", asset_id=TIMED_TEXT_MXF_ID)


@fixture("closed_caption_interop_overlap", ["closed_caption_interop_overlap"], [],
         "Interop package whose closed-caption document holds two cues that "
         "overlap in time. SMPTE allows the overlap, so the fixture can only sit "
         "on the Interop package and has no SMPTE twin. Both cues end inside the "
         "reel's two seconds, so the only thing wrong with them is the overlap.",
         reseal_after=False, src=DOM_INTEROP, baseline="valid/dcp_dom_interop",
         # cues that overlap are out of order for the document-level timing rule
         # as well as for the Interop caption rule
         also=["subtitle_invalid_timing"])
def _(d):
    add_closed_caption(d, dcsubtitle(cues=[
        ("00:00:00:012", "00:00:01:012", ("first",)),
        ("00:00:01:000", "00:00:02:000", ("second",)),
    ]))
    reseal(d)


@fixture("mxf_asset_id_mismatch", ["mxf_asset_id_mismatch"], [],
         "The picture asset id is rewritten to a fresh uuid in the CPL, PKL and "
         "ASSETMAP together, so the package resolves the asset but the MXF still "
         "declares its own AssetUUID. This is what dcpwizard emitted before "
         "c1d73a6")
def _(d):
    # read the id once, before the CPL rewrite changes what pic_id returns, and
    # match <Id> only so the ASSETMAP <Path> keeps naming the file on disk
    old = pic_id(d)[len("urn:uuid:"):]
    for p in (cpl_path(d), pkl_path(d), am_path(d)):
        write(p, re.sub(rf"(<Id>urn:uuid:){old}(</Id>)",
                        rf"\g<1>{REWRITTEN_PICTURE_ID}\g<2>", read(p)))


@fixture("j2k_bitrate_exceeded", ["j2k_bitrate_exceeded"], ["--check-mxf"],
         "Real 3D DCP encoded at the full 250 Mb/s 2K bandwidth. Both eyes share "
         "one edit unit, so the measured peak lands over the DCI limit. The "
         "baseline is the same package encoded at 100 Mb/s per eye",
         src=BITRATE_SRC, baseline="valid/dcp_3d", reseal_after=False)
def _(d):
    pass  # the essence is already over the limit


@fixture("j2k_guard_bits", ["j2k_guard_bits"], ["--deep-j2k"],
         "First frame's QCD guard-bit field zeroed. SMPTE RDD 52 requires 1 guard "
         "bit at 2K, so run_deep_j2k's per-frame scan reports frame 0",
         copy_mxf=True)
def _(d):
    patch_j2k_guard_bits(picture_mxf(d))


@fixture("j2k_legacy_ffff", ["j2k_legacy_ffff"], ["--check-mxf"],
         "0xFF 0xFF written into the first frame's entropy data at a byte position "
         "254 mod 256 from the codestream start, the Dolby Cat. 862 legacy-decoder "
         "condition (SMPTE Legacy Compatibility Note 1). The raw marker prefix "
         "also desyncs the marker walk, which reports stopping early",
         also=["check_skipped"], copy_mxf=True)
def _(d):
    patch_j2k_legacy_ffff(picture_mxf(d))


# the markers dcpwizard leaves out, with offsets inside the 48-frame base. Only
# FFOC and LFOC carry a rule about their value, and dcpwizard already writes both.
EXTRA_MARKERS = [("FFTC", 2), ("LFTC", 3), ("FFOI", 4), ("LFOI", 5),
                 ("FFEC", 6), ("LFEC", 7), ("FFMC", 8), ("LFMC", 9)]


def build_all_markers_baseline():
    """The markers_bad baseline: the base DCP with every marker dcpdoctor names
    under --strict, so a fixture that drops one is the only thing reporting
    marker_missing. Added inside the MainMarkers track dcpwizard already writes,
    which keeps the CPL schema-valid."""
    dest = clone(ALL_MARKERS)
    p = cpl_path(dest)
    entries = "".join(
        "            <Marker>\n"
        f"              <Label>{label}</Label>\n"
        f"              <Offset>{offset}</Offset>\n"
        "            </Marker>\n"
        for label, offset in EXTRA_MARKERS)
    write(p, read(p).replace("          <MarkerList>\n",
                             "          <MarkerList>\n" + entries, 1))
    reseal(dest)


def build_certificate_chain_baseline():
    """The baseline for the six certificate-rule fixtures: the base DCP signed
    with a chain that satisfies every rule in cert_rules.rs, so each fixture's
    code is asserted absent against a chain whose only difference is the defect."""
    dest = os.path.join(CORPUS, *CERTIFICATE_BASELINE.split("/"))
    clone(dest)
    sign_cpl_with_chain(dest, certificate_chain())
    reseal(dest)


def main():
    if not os.path.isdir(BASE):
        print(f"ERROR: base DCP missing at {BASE}; run build_corpus.sh first", file=sys.stderr)
        sys.exit(1)

    # the certificate-rule baseline (base DCP signed with a conformant chain)
    build_certificate_chain_baseline()

    # the markers_bad baseline (base DCP carrying every marker --strict names)
    build_all_markers_baseline()

    manifest = {"baselines": [], "fixtures": []}
    # real dcpwizard packages that must validate clean (0 errors). dcp_ov is the
    # 5.1 labeled base; dcp_3d / dcp_atmos are the new 429-10 / 429-18 types.
    manifest["baselines"].append({
        "dir": "valid/dcp_ov", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "real 5.1 DCP built by dcpwizard (MCA labeled); validates clean",
    })
    manifest["baselines"].append({
        "dir": "valid/dcp_3d", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "stereoscopic 3D (ST 429-10) DCP; FrameRate = 2x EditRate, "
                 "Jpeg2000Stereo essence; validates clean",
    })
    if not os.path.isdir(THREE_D_FOUR_K):
        sys.exit(f"ERROR: {THREE_D_FOUR_K} missing, which scripts/build_corpus.sh "
                 f"did not build")
    manifest["baselines"].append({
        "dir": "valid/dcp_3d_4k", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "4096x2160 stereoscopic 3D DCP, one second long; validates clean",
    })
    manifest["baselines"].append({
        "dir": "valid/dcp_atmos", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "Atmos AuxData (ST 429-18) DCP; validates clean (aux_data_detected "
                 "is INFO, not an error)",
    })
    manifest["baselines"].append({
        "dir": ALL_MARKERS_BASELINE, "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "the base DCP with every marker --strict names, so it is the one "
                 "baseline that reports no marker_missing at all",
    })
    manifest["baselines"].append({
        "dir": "valid/dcp_signed", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "CPL and PKL carry a real ds:Signature, so it is the one baseline "
                 "that does not emit unencrypted_dcp_not_signed",
    })

    # DCP-o-matic packages, present only when dcpomatic2_cli was available
    for path, name, standard in ((DOM_BASE, "valid/dcp_dom_ov", "SMPTE"),
                                 (DOM_INTEROP, "valid/dcp_dom_interop", "Interop")):
        if os.path.isdir(path):
            manifest["baselines"].append({
                "dir": name, "package_type": "dcp", "is_valid_baseline": True,
                "flags": ["--strict", "--check-mxf"], "expected_codes": [],
                "notes": f"real 5.1 {standard} DCP built by DCP-o-matic; validates clean",
            })

    inv = os.path.join(CORPUS, "invalid")
    if os.path.exists(inv):
        shutil.rmtree(inv)

    def build_fixture(f, name, src, baseline):
        missing = [r for r in f["requires"] if not os.path.exists(r)]
        if missing:
            sys.exit(f"ERROR: invalid/{name} needs {', '.join(missing)}, which "
                     f"scripts/build_corpus.sh did not build")
        d = clone(os.path.join(inv, name), copy_mxf=f["copy_mxf"], src=src)
        f["fn"](d)
        if f["reseal"]:
            reseal(d, reseal_cpl=f["reseal_cpl"])
        # read off the built package rather than declared per fixture: the
        # DCP-o-matic variants reuse these same mutation functions and differ in
        # what they leave behind, so a hand-written list would drift from them
        also = sorted(set(f["also"]) | (side_effects(d, src) - set(f["codes"])))
        manifest["fixtures"].append({
            "dir": f"invalid/{name}",
            "package_type": "dcp",
            "is_valid_baseline": False,
            "expected_codes": f["codes"],
            "also_emits": also,
            "flags": f["flags"],
            "baseline": baseline,
            "baseline_flags": f["baseline_flags"],
            "notes": f["notes"],
        })
        print(f"  built invalid/{name} -> {', '.join(f['codes'])}")

    for f in FIXTURES:
        if not os.path.isdir(f["src"]):
            sys.exit(f"ERROR: invalid/{f['name']} needs {f['src']}, which "
                     f"scripts/build_corpus.sh did not build")
        build_fixture(f, f["name"], f["src"], f["baseline"])

    # the same mutations on a DCP-o-matic package, so a code proves it fires on
    # two mastering tools' output. Fixtures checked against another baseline are
    # left out: it has no DoM twin, and asserting the code is absent from
    # dcp_dom_ov instead would pass on a package that could never emit it.
    if os.path.isdir(DOM_BASE):
        for f in FIXTURES:
            if f["vendor_portable"] and f["src"] == BASE and f["baseline"] == "valid/dcp_ov":
                build_fixture(f, f"dom_{f['name']}", DOM_BASE, "valid/dcp_dom_ov")

    # j2k_codestream_summary is the same shape: an INFO the deep-J2K scan reports
    # for any picture track it reads, so the fixture asserts the gate
    manifest["fixtures"].append({
        "dir": "valid/dcp_ov",
        "package_type": "dcp",
        "is_valid_baseline": False,
        "expected_codes": ["j2k_codestream_summary"],
        "also_emits": [],
        "flags": ["--deep-j2k"],
        "baseline": "valid/dcp_ov",
        "baseline_flags": [],
        "notes": "the base DCP's picture codestream summarised by the per-frame "
                 "deep-J2K scan; the code is INFO with no pass/fail, so the "
                 "assertion is that --deep-j2k turns the summary on and no flags "
                 "leaves it off",
    })

    # projector_4k_stereo_support reports a playability risk rather than a defect,
    # so the fixture is the whole 4K 3D build and its baseline is the 2K 3D one.
    # The two differ only in the stored width the check reads off the
    # stereoscopic picture MXF.
    manifest["fixtures"].append({
        "dir": "valid/dcp_3d_4k",
        "package_type": "dcp",
        "is_valid_baseline": False,
        "expected_codes": ["projector_4k_stereo_support"],
        "also_emits": [],
        "flags": ["--check-mxf"],
        "baseline": "valid/dcp_3d",
        "baseline_flags": ["--check-mxf"],
        "notes": "4K stereoscopic DCP; the check reads 4096 from the picture "
                 "descriptor of the MainStereoscopicPicture track file, where the "
                 "2K 3D baseline gives 2048",
    })

    # picture_bitrate_measured is INFO and needs no defect: any IMP picture track
    # read under the picture-details gate reports its measured peak. So the
    # fixture is the clean IMP with the gate on and its baseline is the same IMP
    # with the gate off, which asserts the gate rather than a mutation.
    if os.path.isdir(IMF_SRC):
        manifest["fixtures"].append({
            "dir": "valid/imf_ov",
            "package_type": "imf",
            "is_valid_baseline": False,
            "expected_codes": ["picture_bitrate_measured"],
            "also_emits": [],
            "flags": ["--check-mxf"],
            "baseline": "valid/imf_ov",
            "baseline_flags": [],
            "notes": "IMF IMP picture track measured through the AS-02 reader; the "
                     "code is INFO with no pass/fail, so the assertion is that "
                     "--check-mxf turns the measurement on and no flags leaves it off",
        })

    # schema_validation_skipped needs a schema directory that holds an XSD (or
    # dcpdoctor falls through to the one it ships) but not the one the document
    # needs. One placeholder XSD does both.
    partial = os.path.join(CORPUS, ".schemas_partial")
    os.makedirs(partial, exist_ok=True)
    write(os.path.join(partial, "placeholder.xsd"),
          '<?xml version="1.0"?>\n'
          '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>\n')
    manifest["fixtures"].append({
        "dir": "valid/dcp_ov",
        "package_type": "dcp",
        "is_valid_baseline": False,
        "expected_codes": ["schema_validation_skipped"],
        "also_emits": [],
        "flags": [],
        "env": {"DCPDOCTOR_SCHEMA_DIR": "@.schemas_partial"},
        "baseline": "valid/dcp_ov",
        "baseline_flags": [],
        "notes": "the base DCP validated against a schema directory missing every "
                 "XSD its documents need; the baseline is the same package with "
                 "dcpdoctor's own schemas/, which asserts the pass runs there",
    })

    # App 2E picture-descriptor fixtures. dcpdoctor's write_app2e_fixtures
    # example writes them (see their README); they are committed rather than
    # generated here because building an App 2E track file needs an AS-02 writer
    # this corpus has no other use for. Copied in so every fixture path the
    # manifest names is corpus-relative.
    app2e_source = os.path.join(REPO, "tests", "fixtures", "app2e")
    app2e_dest = os.path.join(CORPUS, "app2e")
    if not os.path.isdir(os.path.join(app2e_source, "clean")):
        sys.exit(f"ERROR: App 2E fixtures not at {app2e_source}. Regenerate them with "
                 f"dcpdoctor's write_app2e_fixtures example (see that directory's README)")
    if os.path.isdir(app2e_dest):
        shutil.rmtree(app2e_dest)
    shutil.copytree(app2e_source, app2e_dest)
    manifest["baselines"].append({
        "dir": "app2e/clean",
        "package_type": "imf",
        "is_valid_baseline": True,
        "expected_codes": [],
        "flags": APP2E_FLAGS,
        "standard": "SMPTE",
        "notes": "clean App 2E IMP, the baseline the four descriptor fixtures diverge from",
    })
    for name, code in APP2E_FIXTURES:
        manifest["fixtures"].append({
            "dir": f"app2e/{name}",
            "package_type": "imf",
            "is_valid_baseline": False,
            "expected_codes": [code],
            "also_emits": [],
            "flags": APP2E_FLAGS,
            "baseline": "app2e/clean",
            "baseline_flags": APP2E_FLAGS,
            "notes": f"App 2E IMP whose picture descriptor breaks the {code} rule",
        })

    write_kdm_digest_variants(os.path.join(CORPUS, "subcmd"))

    # fixtures reachable only through non-validate subcommands. Each runs
    # `dcpdoctor <subcommand> [args]` where an @name arg resolves to a file in
    # corpus/subcmd. auto-qc prints findings as text (not Codes), so those carry
    # a `match` map from code -> substring; kdm emits real Code notes.
    manifest["subcommand_fixtures"] = [
        {"name": "kdm_expired", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_expired.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_expired"],
         "notes": "KDM ContentKeysNotValidAfter in the past"},
        {"name": "kdm_not_yet_valid", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_future.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_not_yet_valid"],
         "notes": "KDM ContentKeysNotValidBefore in the future"},
        {"name": "kdm_thumbprint_invalid", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_thumbprint_short.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_thumbprint_invalid"],
         "notes": "DeviceList thumbprint that does not decode to 20 bytes"},
        {"name": "kdm_content_authenticator_invalid", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_content_authenticator_short.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_content_authenticator_invalid"],
         "notes": "ContentAuthenticator that does not decode to 20 bytes"},
        {"name": "kdm_assume_trust_conflict", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_named_and_assume_trust.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_assume_trust_conflict"],
         "notes": "DeviceList naming a device alongside the DCI assume-trust thumbprint"},
        {"name": "sound_clipping", "subcommand": "auto-qc", "dir": "subcmd",
         "args": ["--audio", "@clip.wav"], "baseline_args": ["--audio", "@normal.wav"],
         "expected_codes": ["sound_clipping"], "match": {"sound_clipping": "Audio clipping"},
         "notes": "full-scale audio; auto-qc reports clipping as a finding string"},
        {"name": "sound_silent", "subcommand": "auto-qc", "dir": "subcmd",
         "args": ["--audio", "@silent.wav"], "baseline_args": ["--audio", "@normal.wav"],
         "expected_codes": ["sound_silent"], "match": {"sound_silent": "Audio silence"},
         "notes": "near-silent audio; auto-qc reports silence as a finding string"},
    ]
    subcmd_dir = os.path.join(CORPUS, "subcmd")
    for sf in manifest["subcommand_fixtures"]:
        for a in sf["args"] + sf["baseline_args"]:
            if a.startswith("@") and not os.path.exists(os.path.join(subcmd_dir, a[1:])):
                sys.exit(f"ERROR: subcommand fixture {sf['name']} needs "
                         f"{os.path.join(subcmd_dir, a[1:])}, which "
                         f"scripts/build_corpus.sh did not build")

    # keep the reference_packages section scan_reference.py appended, or a
    # regen silently drops coverage from 73 to 65 until it is re-run
    path = os.path.join(CORPUS, "manifest.json")
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
        if "reference_packages" in old:
            manifest["reference_packages"] = old["reference_packages"]
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nmanifest: {os.path.join(CORPUS, 'manifest.json')}")
    print(f"fixtures: {len(manifest['fixtures'])} + "
          f"{len(manifest['subcommand_fixtures'])} subcommand")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write-kdm-chain":
        write_kdm_signing_chain(sys.argv[2])
    else:
        main()
